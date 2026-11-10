#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest ORDER FLOW + NIVEAUX D'OPTIONS sur BTC (perpetuel BTCUSDT).

Donnees : python of_download.py flow|options ...  puis  python of_levels.py

LOGIQUE DE MARCHE TESTEE
  Un niveau d'options seul ne dit pas QUAND entrer, et un signal d'order flow
  seul ne dit pas OU il compte. On croise les deux : on n'agit que lorsque le
  prix arrive sur un strike a forte exposition gamma, et c'est le flux agressif
  qui decide du sens.

  Sur un strike, pendant les 5 dernieres minutes, deux lectures possibles :

  ABSORPTION  : le flux agresse fort VERS le niveau (delta z >= seuil), le prix
                touche le niveau, mais cloture du cote d'ou il vient. Les
                acheteurs (ou vendeurs) au marche ont ete absorbes par des ordres
                passifs.                                    -> on CONTRE (fade)
  INITIATIVE  : le flux agresse fort, et le prix cloture nettement AU-DELA du
                niveau. Personne n'a absorbe.               -> on SUIT (cassure)

  Et le signe du gamma des teneurs de marche dit laquelle des deux a un sens :
    GEX > 0 (teneur long gamma, il amortit)  -> ABSORPTION attendue -> fade
    GEX < 0 (teneur short gamma, il amplifie) -> INITIATIVE attendue -> cassure

  Le backtest mesure les 4 combinaisons (2 lectures x 2 signes), plus un
  TEMOIN : les memes regles sur les strikes ordinaires, sans grosse position.
  Si les "bonnes" cases ne battent ni les "mauvaises" ni le temoin, l'info
  options n'apporte rien, et il faut le savoir avant de risquer de l'argent.

EXECUTION (volontairement prudente)
  - entree au marche a la cloture de la bougie M1 du signal ;
  - stop et objectif en multiples de la volatilite horaire (pas en points) ;
  - si stop et objectif sont dans la meme bougie, on compte le STOP ;
  - frais : commission preneur + glissement, a l'entree ET a la sortie ;
  - sortie au temps si rien n'est touche.

Usage :
    python backtest_orderflow.py                 # rapport complet
    python backtest_orderflow.py --z 2 --sl 2    # autres reglages
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ───────────────────────── Reglages par defaut ─────────────────────────
WIN        = 5       # fenetre order flow (minutes)
Z_THR      = 1.5     # delta 5 min, en ecarts-types (sur 24 h glissantes)
BRK        = 0.25    # cassure validee si cloture a >= 0,25 x vol horaire du niveau
SL_MULT    = 1.5     # stop = 1,5 x volatilite horaire
TP_R       = 2.0     # objectif en R
MAX_HOURS  = 12      # sortie au temps
COOLDOWN   = 60      # minutes sans nouveau signal sur le meme niveau
FEE        = 0.0005  # commission preneur Binance futures, par cote (0,05 %)
SLIP       = 0.0001  # glissement par cote (0,01 %)
TOP_N      = 4       # niveaux d'options retenus par jour
SNAP_HOUR  = 8


# ───────────────────────── Donnees ─────────────────────────
def load_flow():
    files = sorted(glob.glob(os.path.join(DATA, "flow", "*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated()]
    full = pd.date_range(df.index[0], df.index[-1], freq="1min")
    df = df.reindex(full)
    df["trou"] = df["close"].isna()          # minute sans donnee (jour absent chez Binance)
    df["close"] = df["close"].ffill()
    for c in ("open", "high", "low"):
        df[c] = df[c].fillna(df["close"])
    num = df.columns.drop("trou")
    df[num] = df[num].fillna(0.0)
    return df


def features(df, win=WIN):
    f = pd.DataFrame(index=df.index)
    delta = df["buy"] - df["sell"]
    d = delta.rolling(win).sum()
    # normalisation sur les 24 h PRECEDENTES uniquement (shift) : pas de futur
    f["z"] = d / d.rolling(1440).std().shift(1)
    r = np.log(df["close"]).diff()
    f["vp"] = r.rolling(1440).std().shift(1) * np.sqrt(60) * df["close"]   # vol horaire, en prix
    # CORRECTIF : apres un trou de donnees, le prix est fige, la volatilite
    # mesuree tombe vers zero et le risque du trade avec -> R absurdes. On
    # neutralise les signaux tant que la fenetre de 24 h contient un trou.
    bad = df["trou"].rolling(1440 + win).sum().fillna(1) > 0
    f.loc[bad, ["z", "vp"]] = np.nan
    f["hi"] = df["high"].rolling(win).max()
    f["lo"] = df["low"].rolling(win).min()
    f["c"] = df["close"]
    f["c_prev"] = df["close"].shift(win)
    return f


# ───────────────────────── Signaux ─────────────────────────
def find_signals(f, levels, z_thr=Z_THR, brk=BRK, cooldown=COOLDOWN):
    """Retourne un DataFrame de signaux (un par evenement, dedoublonne)."""
    idx = f.index
    t0 = idx[0]
    z, vp, hi, lo, c, cp = (f[k].to_numpy() for k in ("z", "vp", "hi", "lo", "c", "c_prev"))
    out = []
    for snap, level, gex, rank in levels.itertuples(index=False):
        a = int((snap - t0).total_seconds() // 60) + WIN
        b = int((snap - t0).total_seconds() // 60) + 1440
        if a < 1500 or b > len(idx):
            continue
        s = slice(a, b)
        zz, v, h, l, cc, pp = z[s], vp[s], hi[s], lo[s], c[s], cp[s]
        L = level
        conds = (
            ("fade", -1, (pp < L) & (h >= L) & (cc < L) & (zz >= z_thr)),
            ("fade", +1, (pp > L) & (l <= L) & (cc > L) & (zz <= -z_thr)),
            ("break", +1, (pp < L) & (cc > L + brk * v) & (zz >= z_thr)),
            ("break", -1, (pp > L) & (cc < L - brk * v) & (zz <= -z_thr)),
        )
        for kind, side, m in conds:
            last = -10 ** 9
            for i in np.flatnonzero(m):
                if i - last < cooldown:
                    continue
                last = i
                out.append((a + i, L, gex, rank, kind, side))
    sig = pd.DataFrame(out, columns=["i", "level", "gex", "rank", "kind", "side"])
    return sig.sort_values("i", kind="stable").reset_index(drop=True)


# ───────────────────────── Simulation d'un trade ─────────────────────────
def simulate(df, f, sig, sl_mult=SL_MULT, tp_r=TP_R, max_hours=MAX_HOURS,
             fee=FEE, slip=SLIP):
    high, low, close = (df[k].to_numpy() for k in ("high", "low", "close"))
    vp = f["vp"].to_numpy()
    n = len(close)
    hold = max_hours * 60
    res = np.full(len(sig), np.nan)
    cost = np.full(len(sig), np.nan)
    iexit = np.zeros(len(sig), dtype=np.int64)
    reason = np.empty(len(sig), dtype=object)
    for k, (i, side) in enumerate(zip(sig["i"].to_numpy(), sig["side"].to_numpy())):
        entry = close[i]
        risk = sl_mult * vp[i]
        if not np.isfinite(risk) or risk <= 0 or i + 1 >= n:
            continue
        j1 = min(i + hold, n - 1)
        h, l = high[i + 1:j1 + 1], low[i + 1:j1 + 1]
        if side == 1:
            hit_sl = np.flatnonzero(l <= entry - risk)
            hit_tp = np.flatnonzero(h >= entry + tp_r * risk)
        else:
            hit_sl = np.flatnonzero(h >= entry + risk)
            hit_tp = np.flatnonzero(l <= entry - tp_r * risk)
        a = hit_sl[0] if len(hit_sl) else 10 ** 9
        b = hit_tp[0] if len(hit_tp) else 10 ** 9
        if a <= b and a < 10 ** 9:            # egalite -> stop (prudent)
            res[k], iexit[k], reason[k] = -1.0, i + 1 + a, "SL"
        elif b < 10 ** 9:
            res[k], iexit[k], reason[k] = tp_r, i + 1 + b, "TP"
        else:
            res[k] = side * (close[j1] - entry) / risk
            iexit[k], reason[k] = j1, "TEMPS"
        cost[k] = 2 * (fee + slip) * entry / risk
    out = sig.copy()
    out["time"] = df.index[out["i"].to_numpy()]
    out["R_brut"] = res
    out["frais_R"] = cost
    out["R"] = res - cost
    out["i_exit"] = iexit
    out["sortie"] = reason
    return out.dropna(subset=["R"])


# ───────────────────────── Statistiques ─────────────────────────
def stat(x):
    n = len(x)
    if n < 2:
        return dict(n=n, wr=np.nan, brut=np.nan, net=np.nan, t=np.nan, total=np.nan)
    r = x["R"]
    return dict(n=n, wr=(r > 0).mean() * 100, brut=x["R_brut"].mean(), net=r.mean(),
                t=r.mean() / (r.std(ddof=1) / np.sqrt(n)), total=r.sum())


def line(label, s):
    if s["n"] < 2:
        return f"  {label:<34} n={s['n']:>5}"
    return (f"  {label:<34} n={s['n']:>5}  WR={s['wr']:5.1f}%  brut={s['brut']:+.3f}R  "
            f"net={s['net']:+.3f}R  t={s['t']:+5.2f}  total={s['total']:+8.1f}R")


def one_at_a_time(tr):
    """Portefeuille reel : une seule position a la fois, dans l'ordre du temps."""
    keep, free = [], -1
    for k, (i, j) in enumerate(zip(tr["i"].to_numpy(), tr["i_exit"].to_numpy())):
        if i >= free:
            keep.append(k)
            free = j
    return tr.iloc[keep]


def drawdown(r):
    eq = np.cumsum(r)
    return float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()) if len(r) else 0.0


def pick(tr):
    """La strategie = les deux cases que la theorie designe."""
    return tr[((tr.kind == "fade") & (tr.gex > 0)) | ((tr.kind == "break") & (tr.gex < 0))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z", type=float, default=Z_THR)
    ap.add_argument("--sl", type=float, default=SL_MULT)
    ap.add_argument("--tp", type=float, default=TP_R)
    ap.add_argument("--hours", type=int, default=MAX_HOURS)
    ap.add_argument("--fee", type=float, default=FEE)
    ap.add_argument("--slip", type=float, default=SLIP)
    ap.add_argument("--start", default="2023-07-01", help="debut du backtest (apres chauffe)")
    ap.add_argument("--no-grid", action="store_true")
    a = ap.parse_args()

    df = load_flow()
    f = features(df)
    lv = pd.read_parquet(os.path.join(DATA, "levels.parquet"))
    lv = lv[lv["snap"] >= pd.Timestamp(a.start, tz="UTC")]
    real, ctrl = lv[lv["rank"] <= TOP_N], lv[lv["rank"] > TOP_N]
    print(f"Order flow : {len(df):,} barres M1  {df.index[0]} -> {df.index[-1]}")
    print(f"Niveaux    : {len(real)} strikes majeurs + {len(ctrl)} strikes temoins, "
          f"{lv['snap'].nunique()} jours")
    print(f"Reglages   : z>={a.z}  stop={a.sl} x vol horaire  TP={a.tp}R  "
          f"sortie au temps {a.hours} h  frais={2 * (a.fee + a.slip) * 100:.2f} % aller-retour\n")

    kw = dict(sl_mult=a.sl, tp_r=a.tp, max_hours=a.hours, fee=a.fee, slip=a.slip)
    tr = simulate(df, f, find_signals(f, real, a.z), **kw)
    tc = simulate(df, f, find_signals(f, ctrl, a.z), **kw)

    print("0) ETUDE D'EVENEMENT — mouvement moyen APRES le signal, dans le sens du trade,")
    print("   en volatilite horaire, SANS stop ni objectif ni frais (t entre parentheses).")
    print("   C'est le test le plus pur : y a-t-il une information, oui ou non ?")
    c = df["close"].to_numpy()
    vpa = f["vp"].to_numpy()

    def fwd(x, h):
        i = x["i"].to_numpy()
        i = i[i + h < len(c)]
        r = x["side"].to_numpy()[:len(i)] * (c[i + h] - c[i]) / vpa[i]
        r = r[np.isfinite(r)]
        return f"{r.mean():+.3f}({r.mean() / (r.std(ddof=1) / np.sqrt(len(r))):+.1f})" if len(r) > 2 else "   n/a"

    groups = [("fade  GEX>0 (theorie)", tr[(tr.kind == "fade") & (tr.gex > 0)]),
              ("fade  GEX<0", tr[(tr.kind == "fade") & (tr.gex < 0)]),
              ("fade  temoin", tc[tc.kind == "fade"]),
              ("break GEX<0 (theorie)", tr[(tr.kind == "break") & (tr.gex < 0)]),
              ("break GEX>0", tr[(tr.kind == "break") & (tr.gex > 0)]),
              ("break temoin", tc[tc.kind == "break"])]
    print(f"   {'':<24}{'n':>6}   {'15 min':>12} {'1 h':>12} {'4 h':>12} {'12 h':>12}")
    for lab, x in groups:
        print(f"   {lab:<24}{len(x):>6}   " + " ".join(f"{fwd(x, h):>12}" for h in (15, 60, 240, 720)))
    print()

    print("1) LES 4 CASES — chaque signal evalue independamment (strikes majeurs)")
    for kind, lab in (("fade", "ABSORPTION -> contrer"), ("break", "INITIATIVE -> suivre")):
        for pos, gl in ((True, "GEX>0"), (False, "GEX<0")):
            x = tr[(tr.kind == kind) & ((tr.gex > 0) == pos)]
            ok = (kind == "fade") == pos
            print(line(f"{lab} | {gl} {'<- theorie' if ok else ''}", stat(x)))
    print("\n2) TEMOIN — memes regles sur les strikes ordinaires (sans grosse position)")
    for kind, lab in (("fade", "ABSORPTION -> contrer"), ("break", "INITIATIVE -> suivre")):
        print(line(lab, stat(tc[tc.kind == kind])))

    st = one_at_a_time(pick(tr))
    print("\n3) STRATEGIE (fade si GEX>0 + cassure si GEX<0), une position a la fois")
    print(line("ensemble", stat(st)))
    for kind in ("fade", "break"):
        print(line(f"  dont {kind}", stat(st[st.kind == kind])))
    for side, lab in ((1, "achats"), (-1, "ventes")):
        print(line(f"  dont {lab}", stat(st[st.side == side])))
    print(f"  Drawdown max : {drawdown(st['R'].to_numpy()):.1f} R   "
          f"frais moyens : {st['frais_R'].mean():.3f} R/trade   "
          f"sorties : {st['sortie'].value_counts().to_dict()}")
    print("\n   Par annee :")
    for y, g in st.groupby(st["time"].dt.year):
        print(line(str(y), stat(g)))

    if not a.no_grid:
        print("\n4) ROBUSTESSE — R net moyen par trade de la strategie (t entre parentheses)")
        print("   Une vraie strategie tient sur TOUTE la grille, pas sur une case.")
        for z in (1.0, 1.5, 2.0, 2.5):
            sg = find_signals(f, real, z)
            row = []
            for sl in (1.0, 1.5, 2.5):
                for tp in (1.0, 2.0, 3.0):
                    s = stat(one_at_a_time(pick(simulate(
                        df, f, sg, sl_mult=sl, tp_r=tp, max_hours=a.hours,
                        fee=a.fee, slip=a.slip))))
                    row.append(f"{s['net']:+.2f}({s['t']:+.1f})")
            print(f"   z>={z:<3} | SL1.0: " + " ".join(row[0:3]) + " | SL1.5: "
                  + " ".join(row[3:6]) + " | SL2.5: " + " ".join(row[6:9])
                  + f" | n={s['n']}")
        print("   (colonnes : TP = 1R, 2R, 3R)")

    cols = ["time", "kind", "side", "level", "gex", "rank", "R", "R_brut", "frais_R", "sortie"]
    st[cols].round({"R": 4, "R_brut": 4, "frais_R": 4, "gex": 0}).to_csv("resultats_orderflow.csv", index=False)
    print("\nDetail des trades de la strategie -> resultats_orderflow.csv")


if __name__ == "__main__":
    main()
