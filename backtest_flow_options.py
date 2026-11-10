#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest ORDER FLOW + NIVEAUX D'OPTIONS sur le future Nasdaq (NQ / MNQ).

Idee de marche
--------------
Les niveaux d'options (call wall, put wall, zero gamma) disent OU les dealers
vont devoir couvrir leur gamma. L'order flow dit SI, a ce niveau, le marche
est en train de l'accepter ou de le rejeter. Aucun des deux ne suffit seul :
  - un niveau sans confirmation = on se fait ecraser les jours ou le flux 0DTE
    du jour (invisible dans l'OI) a deplace le positionnement ;
  - un desequilibre d'order flow sans niveau = du bruit, il y en a toutes les
    minutes.

Regime gamma (spot vs zero gamma, re-evalue a chaque bougie) :
  spot > ZG  -> gamma POSITIF : les dealers vendent la hausse / achetent la
                baisse -> le marche se comprime -> on JOUE LE RETOUR aux murs.
  spot < ZG  -> gamma NEGATIF : les dealers amplifient -> on JOUE LA CASSURE.

Setups (chacun activable separement, resultats ventiles par setup) :
  FADE_CW   gamma+ : la bougie touche le call wall, les acheteurs agressent
            (delta > 0, volume anormal) mais la cloture retombe sous le mur
            -> absorption par les vendeurs passifs -> VENTE.
  FADE_PW   gamma+ : symetrique au put wall -> ACHAT.
  BREAK_PW  gamma- : cloture sous le put wall avec agression vendeuse -> VENTE.
  ZG_DOWN   cloture sous le zero gamma avec agression vendeuse (bascule en
            gamma negatif, la vol s'ouvre) -> VENTE.
  ZG_UP     cloture au-dessus du zero gamma avec agression acheteuse (retour en
            gamma positif) -> ACHAT.
  Pas de « BREAK_CW » : en gamma negatif le call wall est quasi toujours loin
  au-dessus, et en gamma positif casser un call wall est l'evenement le moins
  probable de la carte. A ajouter si les donnees le justifient, pas avant.

« Volume anormal » = volume de la bougie / mediane du volume A LA MEME MINUTE
sur les 20 seances precedentes. Un z-score sur une moyenne glissante est faux
en intraday : la premiere demi-heure fait 3 a 5 fois le volume de midi, il
signalerait « anormal » chaque matin et jamais a 13h.

Execution : signal a la cloture de la bougie -> entree au marche a l'OUVERTURE
de la bougie suivante + slippage. Stop au-dela de l'extreme de la bougie
signal (+ tampon). SL teste avant TP si les deux sont touches dans la meme
bougie (conservateur). Cloture forcee a 15:55 NY.

Donnees d'entree
----------------
1) ORDER FLOW (NQ ou MNQ, contrat continu), au choix :
   a) ticks/trades : ts, price, size, side
        side = cote AGRESSEUR. Accepte : B/S, buy/sell, et le format Databento
        ou A = Ask = VENDEUR agresseur, B = Bid = ACHETEUR agresseur
        (contre-intuitif, verifie dans la doc Databento ; --side-format
        databento). Sans colonne side : regle du tick (moins fiable, averti).
   b) barres deja agregees : datetime, open, high, low, close, volume,
        buy_volume, sell_volume (export Sierra Chart / ATAS / NinjaTrader :
        AskVolume = achats agresseurs = buy_volume).
   PAS de donnees CFD Dukascopy ici : leur « volume » est un nombre de ticks
   de cotation, il n'y a ni volume reel ni cote agresseur. --approx-delta
   existe pour essayer quand meme sur des barres OHLCV, mais ce n'est plus de
   l'order flow.
2) NIVEAUX : options_levels.csv (collect_options_levels.py ou
   options_levels.py build). Niveaux en unites QQQ, convertis en points NQ par
   ratio = cloture NQ 16:00 de la veille / ref_spot. Si tes niveaux sont deja
   en points NQ (fournisseur type SpotGamma/MenthorQ) : --levels-native.

Usage
-----
    python backtest_flow_options.py FLOW.csv --levels options_levels.csv
        [--contract MNQ] [--tz UTC] [--rr 2] [--setups FADE_CW,FADE_PW,...]
        [--imb 0.25] [--vol-mult 1.5] [--tol-pct 0.05] [--demo]

--demo genere des donnees synthetiques pour verifier que la mecanique tourne
(les resultats du mode demo ne veulent RIEN dire sur la strategie).
"""
import argparse
import math
import sys

import numpy as np
import pandas as pd

NY_TZ = "America/New_York"
RTH_OPEN, RTH_CLOSE = "09:30", "16:00"
ENTRY_START = "09:45"   # 15 premieres minutes ignorees : le flux d'ouverture
                        # (ordres MOO, couvertures de nuit) n'est pas de l'absorption
ENTRY_END   = "15:30"
FORCE_CLOSE = "15:55"
START_EQUITY = 10000.0
RISK_PCT = 0.25

CONTRACTS = {
    # tick, $ par point, commission aller-retour $ (ordre de grandeur courtier discount)
    "NQ":  {"tick": 0.25, "pv": 20.0, "comm_rt": 4.5},
    "MNQ": {"tick": 0.25, "pv": 2.0,  "comm_rt": 1.5},
}
ALL_SETUPS = ["FADE_CW", "FADE_PW", "BREAK_PW", "ZG_DOWN", "ZG_UP"]


# ───────────────────────── Chargement order flow ─────────────────────────
def _norm_cols(df):
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def load_flow(path, tz, side_format="auto", approx_delta=False):
    """Retourne des barres 1 min : open high low close volume buy sell delta."""
    df = _norm_cols(pd.read_csv(path, compression="infer"))
    tcol = next((c for c in ("ts_event", "ts", "timestamp", "datetime", "time", "date") if c in df.columns), df.columns[0])
    df[tcol] = pd.to_datetime(df[tcol], errors="coerce", utc=False)
    df = df.dropna(subset=[tcol]).set_index(tcol).sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize(tz)
    df.index = df.index.tz_convert(NY_TZ)

    # renomme les exports de plateformes
    ren = {"askvolume": "buy_volume", "ask_volume": "buy_volume", "bidvolume": "sell_volume",
           "bid_volume": "sell_volume", "buyvolume": "buy_volume", "sellvolume": "sell_volume",
           "last": "close", "vol": "volume", "qty": "size", "quantity": "size"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})

    if "price" in df.columns:                       # ── ticks
        if "size" not in df.columns:
            df["size"] = 1.0
        px, sz = df["price"].astype(float), df["size"].astype(float)
        if "side" in df.columns:
            s = df["side"].astype(str).str.strip().str.upper()
            fmt = side_format
            if fmt == "auto":
                fmt = "databento" if s.isin(["A", "B", "N"]).mean() > 0.95 else "bs"
            if fmt == "databento":
                # Doc Databento, champ side : « the side that initiates the
                # event » -> A (Ask) = VENDEUR agresseur, B (Bid) = ACHETEUR
                # agresseur, N = inconnu (compte dans le volume, pas le delta).
                sign = np.select([s == "B", s == "A"], [1.0, -1.0], 0.0)
            else:
                sign = np.select([s.str.startswith("B"), s.str.startswith("S")], [1.0, -1.0], 0.0)
        else:
            print("ATTENTION : pas de colonne side -> regle du tick (precision ~70-85 %).")
            d = px.diff()
            sign = np.sign(d).replace(0, np.nan).ffill().fillna(0).values
        df["buy"] = np.where(sign > 0, sz, 0.0)
        df["sell"] = np.where(sign < 0, sz, 0.0)
        g = df.resample("1min")
        bars = pd.DataFrame({"open": g["price"].first(), "high": g["price"].max(),
                             "low": g["price"].min(), "close": g["price"].last(),
                             "volume": g["size"].sum(), "buy": g["buy"].sum(), "sell": g["sell"].sum()})
    else:                                           # ── barres
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        if "buy_volume" in df.columns and "sell_volume" in df.columns:
            df["buy"], df["sell"] = df["buy_volume"].astype(float), df["sell_volume"].astype(float)
            if "volume" not in df.columns:
                df["volume"] = df["buy"] + df["sell"]
        elif approx_delta:
            print("ATTENTION : --approx-delta -> delta ESTIME par la position de la cloture "
                  "dans la bougie. Ce n'est PAS de l'order flow ; resultats indicatifs seulement.")
            rng = (df["high"] - df["low"]).replace(0, np.nan)
            w = ((df["close"] - df["low"]) / rng).fillna(0.5)
            df["buy"], df["sell"] = df["volume"] * w, df["volume"] * (1 - w)
        else:
            sys.exit("ERREUR : barres sans buy_volume/sell_volume. Il faut des ticks avec cote "
                     "agresseur ou des barres footprint. (--approx-delta pour forcer une estimation)")
        g = df.resample("1min")
        bars = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                             "close": g["close"].last(), "volume": g["volume"].sum(),
                             "buy": g["buy"].sum(), "sell": g["sell"].sum()})
    bars = bars.dropna(subset=["open", "close"])
    bars["delta"] = bars["buy"] - bars["sell"]
    return bars


def add_flow_features(bars, lookback_days=20):
    """vol_rel = volume / mediane du volume a la meme minute sur les N seances PRECEDENTES."""
    b = bars.copy()
    b["day"] = b.index.date
    b["tod"] = b.index.strftime("%H:%M")
    piv = b.pivot_table(index="day", columns="tod", values="volume", aggfunc="sum")
    # shift(1) : la seance du jour n'entre JAMAIS dans sa propre reference
    ref = piv.shift(1).rolling(lookback_days, min_periods=5).median()
    ref_long = ref.stack().rename("vol_ref").reset_index()
    ref_long.columns = ["day", "tod", "vol_ref"]
    b = b.reset_index().merge(ref_long, on=["day", "tod"], how="left").set_index(b.index.name or "index")
    b.index.name = "ts"
    b["vol_rel"] = b["volume"] / b["vol_ref"]
    b["imb"] = (b["delta"] / b["volume"].replace(0, np.nan)).fillna(0.0)   # -1..+1
    return b


# ───────────────────────── Niveaux ─────────────────────────
def load_levels(path, bars, native=False):
    """Retourne {date: {CW, PW, ZG}} en points du future."""
    lv = pd.read_csv(path)
    lv["date"] = pd.to_datetime(lv["date"]).dt.date
    out = {}
    if native:
        for _, r in lv.iterrows():
            out[r["date"]] = {"CW": r["call_wall"], "PW": r["put_wall"], "ZG": r["zero_gamma"], "ratio": 1.0}
        return out
    # cloture 16:00 NY de chaque seance (derniere barre <= 15:59)
    rth = bars.between_time(RTH_OPEN, "15:59")
    closes = rth.groupby(rth.index.date)["close"].last()
    days = list(closes.index)
    for _, r in lv.iterrows():
        d = r["date"]
        prev = [x for x in days if x < d]
        if not prev or not np.isfinite(r["ref_spot"]) or r["ref_spot"] <= 0:
            continue
        ratio = closes[prev[-1]] / r["ref_spot"]
        out[d] = {"CW": r["call_wall"] * ratio, "PW": r["put_wall"] * ratio,
                  "ZG": r["zero_gamma"] * ratio if np.isfinite(r["zero_gamma"]) else np.nan,
                  "ratio": ratio}
    return out


# ───────────────────────── Signaux ─────────────────────────
def detect(b, prev_close, L, p):
    """Liste des (setup, side, stop) declenches sur la bougie b."""
    sig = []
    tol = p["tol_pct"] / 100 * b["close"]
    loud = b["vol_rel"] >= p["vol_mult"] if np.isfinite(b["vol_rel"]) else False
    mid = (b["high"] + b["low"]) / 2
    zg = L["ZG"]
    pos_gamma = np.isfinite(zg) and b["close"] > zg
    neg_gamma = np.isfinite(zg) and b["close"] < zg
    buf = p["stop_buf"]
    cw, pw = L["CW"], L["PW"]

    if "FADE_CW" in p["setups"] and pos_gamma and loud and np.isfinite(cw):
        if b["high"] >= cw - tol and b["imb"] >= p["imb"] and b["close"] < cw and b["close"] <= mid:
            sig.append(("FADE_CW", -1, b["high"] + buf))
    if "FADE_PW" in p["setups"] and pos_gamma and loud and np.isfinite(pw):
        if b["low"] <= pw + tol and b["imb"] <= -p["imb"] and b["close"] > pw and b["close"] >= mid:
            sig.append(("FADE_PW", 1, b["low"] - buf))
    if "BREAK_PW" in p["setups"] and neg_gamma and loud and np.isfinite(pw) and np.isfinite(prev_close):
        if prev_close >= pw and b["close"] < pw - tol and b["imb"] <= -p["imb"]:
            sig.append(("BREAK_PW", -1, max(b["high"], pw) + buf))
    if np.isfinite(zg) and np.isfinite(prev_close) and loud:
        if "ZG_DOWN" in p["setups"] and prev_close >= zg and b["close"] < zg - tol and b["imb"] <= -p["imb"]:
            sig.append(("ZG_DOWN", -1, max(b["high"], zg) + buf))
        if "ZG_UP" in p["setups"] and prev_close <= zg and b["close"] > zg + tol and b["imb"] >= p["imb"]:
            sig.append(("ZG_UP", 1, min(b["low"], zg) - buf))
    return sig


# ───────────────────────── Backtest ─────────────────────────
def run(bars, levels, p):
    c = CONTRACTS[p["contract"]]
    tick = c["tick"]
    slip = p["slip_ticks"] * tick
    comm_pts = c["comm_rt"] / c["pv"]
    equity = START_EQUITY
    trades = []
    rth = bars.between_time(RTH_OPEN, "15:59")
    for day, g in rth.groupby(rth.index.date):
        L = levels.get(day)
        if L is None:
            continue
        idx = g.index
        rows = g.to_dict("records")
        n_today = 0
        i = 0
        prev_close = np.nan
        while i < len(rows) - 1:
            b = rows[i]
            t = idx[i].strftime("%H:%M")
            if t < ENTRY_START or t >= ENTRY_END or n_today >= p["max_trades"]:
                prev_close = b["close"]; i += 1; continue
            sig = detect(b, prev_close, L, p)
            prev_close = b["close"]
            if not sig:
                i += 1; continue
            setup, side, stop = sig[0]          # 1 signal max par bougie : priorite = ordre ALL_SETUPS
            nb = rows[i + 1]
            entry = nb["open"] + side * slip    # au marche, slippage contre soi
            risk = (entry - stop) * side
            if risk < p["min_stop_ticks"] * tick:
                stop = entry - side * p["min_stop_ticks"] * tick
                risk = p["min_stop_ticks"] * tick
            if risk > p["max_stop_pts"]:
                i += 1; continue                 # stop trop large : on ne force pas le trade
            tp = entry + side * risk * p["rr"]
            exit_px, reason, j = None, None, i + 1
            for j in range(i + 1, len(rows)):
                x = rows[j]
                if idx[j].strftime("%H:%M") >= FORCE_CLOSE:
                    exit_px, reason = x["open"], "CLOSE"; break
                if side == 1:
                    if x["low"] <= stop:  exit_px, reason = stop - slip, "SL"; break
                    if x["high"] >= tp:   exit_px, reason = tp, "TP"; break
                else:
                    if x["high"] >= stop: exit_px, reason = stop + slip, "SL"; break
                    if x["low"] <= tp:    exit_px, reason = tp, "TP"; break
            if exit_px is None:
                exit_px, reason = rows[-1]["close"], "CLOSE"
            gross_R = (exit_px - entry) * side / risk
            cost_R = comm_pts / risk
            net_R = gross_R - cost_R
            equity += equity * RISK_PCT / 100 * net_R
            trades.append({
                "date": str(day), "time": idx[i + 1].strftime("%H:%M"), "setup": setup,
                "side": "L" if side == 1 else "S", "regime": ("n/a" if not np.isfinite(L["ZG"]) else "gamma+" if b["close"] > L["ZG"] else "gamma-"),
                "entry": round(entry, 2), "stop": round(stop, 2), "tp": round(tp, 2),
                "exit": round(exit_px, 2), "risk_pts": round(risk, 2), "reason": reason,
                "R_brut": round(gross_R, 3), "frais_R": round(cost_R, 3), "R": round(net_R, 3),
                "vol_rel": round(b["vol_rel"], 2), "imb": round(b["imb"], 2), "equity": round(equity, 2),
            })
            n_today += 1
            i = j + 1                            # une position a la fois
            prev_close = rows[j]["close"] if j < len(rows) else prev_close
    return pd.DataFrame(trades)


def _stats(r):
    n = len(r)
    if n == 0:
        return "0 trade"
    w = r[r > 0].sum(); l = -r[r <= 0].sum()
    pf = w / l if l > 0 else float("inf")
    t = r.mean() / (r.std(ddof=1) / math.sqrt(n)) if n > 1 and r.std(ddof=1) > 0 else float("nan")
    return f"{n:4d} trades  WR {(r > 0).mean()*100:5.1f}%  moy {r.mean():+.3f}R  total {r.sum():+7.1f}R  PF {pf:5.2f}  t={t:+.2f}"


def report(tr):
    if len(tr) == 0:
        return "Aucun trade genere."
    r = tr["R"]
    cum = r.cumsum()
    dd_R = (cum.cummax() - cum).max()
    out = ["GLOBAL (net de frais)", "  " + _stats(r),
           f"  brut {tr['R_brut'].sum():+.1f}R  frais -{tr['frais_R'].sum():.1f}R  "
           f"max drawdown {dd_R:.1f}R  equity finale {tr['equity'].iloc[-1]:.0f}",
           f"  sorties : TP={(tr['reason']=='TP').sum()}  SL={(tr['reason']=='SL').sum()}  CLOSE={(tr['reason']=='CLOSE').sum()}",
           "", "PAR SETUP"]
    for s, g in tr.groupby("setup"):
        out.append(f"  {s:9s}" + _stats(g["R"]))
    out += ["", "PAR REGIME"]
    for s, g in tr.groupby("regime"):
        out.append(f"  {s:9s}" + _stats(g["R"]))
    out += ["", "PAR ANNEE"]
    for y, g in tr.groupby(tr["date"].str[:4]):
        out.append(f"  {y:9s}" + _stats(g["R"]))
    out += ["", "LECTURE : |t| < 2 = pas distinguable du hasard. Moins de ~100 trades par setup = "
            "ne rien conclure. Optimiser --imb/--vol-mult/--rr sur tout l'historique puis lire le "
            "resultat = sur-apprentissage : calibre sur la 1re moitie, verifie sur la 2nde (--from/--to)."]
    return "\n".join(out)


# ───────────────────────── Donnees de demonstration ─────────────────────────
def make_demo(days=120, seed=7):
    """Ticks NQ synthetiques + niveaux QQQ synthetiques. Pour tester la MECANIQUE seulement."""
    rng = np.random.default_rng(seed)
    bdays = pd.bdate_range("2026-01-05", periods=days)
    bars, lv = [], []
    px = 21000.0
    for d in bdays:
        idx = pd.date_range(f"{d.date()} 09:30", f"{d.date()} 15:59", freq="1min", tz=NY_TZ)
        n = len(idx)
        tod = np.arange(n)
        vol_shape = 1 + 2.5 * np.exp(-tod / 25) + 0.8 * np.exp(-(n - tod) / 20)
        ret = rng.standard_t(4, n) * 0.0004
        close = px * np.exp(np.cumsum(ret))
        openp = np.r_[px, close[:-1]]
        hi = np.maximum(openp, close) * (1 + np.abs(rng.normal(0, 0.0002, n)))
        lo = np.minimum(openp, close) * (1 - np.abs(rng.normal(0, 0.0002, n)))
        # volume = profil intraday x choc lognormal (les vrais volumes ont des pics)
        vol = rng.poisson(400 * vol_shape * rng.lognormal(0, 0.5, n)).astype(float)
        bias = np.clip(0.5 + np.sign(close - openp) * 0.15 + rng.normal(0, 0.1, n), 0.05, 0.95)
        buy = np.round(vol * bias)
        bars.append(pd.DataFrame({"datetime": idx, "open": openp, "high": hi, "low": lo, "close": close,
                                  "volume": vol, "buy_volume": buy, "sell_volume": vol - buy}))
        qqq = px / 41.0
        lv.append({"date": d.date().isoformat(), "ref_spot": round(qqq, 2),
                   "call_wall": round(qqq * (1 + rng.uniform(0.004, 0.015))),
                   "put_wall": round(qqq * (1 - rng.uniform(0.004, 0.015))),
                   "zero_gamma": round(qqq * (1 + rng.normal(0, 0.006)), 2), "net_gex_musd": 0})
        px = close[-1]
    flow = pd.concat(bars)
    flow["datetime"] = flow["datetime"].dt.tz_convert("UTC").dt.tz_localize(None)
    return flow, pd.DataFrame(lv)


# ───────────────────────── Main ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("flow", nargs="?", help="ticks ou barres footprint NQ/MNQ (.csv/.csv.gz)")
    ap.add_argument("--levels", default="options_levels.csv")
    ap.add_argument("--levels-native", action="store_true", help="niveaux deja en points NQ")
    ap.add_argument("--tz", default="UTC", help="fuseau des horodatages du fichier flow")
    ap.add_argument("--side-format", default="auto", choices=["auto", "databento", "bs"])
    ap.add_argument("--approx-delta", action="store_true")
    ap.add_argument("--contract", default="MNQ", choices=list(CONTRACTS))
    ap.add_argument("--setups", default=",".join(ALL_SETUPS))
    ap.add_argument("--rr", type=float, default=2.0, help="objectif en R")
    ap.add_argument("--imb", type=float, default=0.25, help="seuil |delta|/volume de la bougie signal")
    ap.add_argument("--vol-mult", type=float, default=1.5, help="volume >= X fois la normale de la minute")
    ap.add_argument("--tol-pct", type=float, default=0.05, help="tolerance autour d'un niveau, %% du prix")
    ap.add_argument("--stop-buf", type=float, default=2.0, help="tampon du stop en points")
    ap.add_argument("--slip-ticks", type=int, default=1)
    ap.add_argument("--min-stop-ticks", type=int, default=16, help="stop minimum (16 ticks = 4 pts)")
    ap.add_argument("--max-stop-pts", type=float, default=60.0)
    ap.add_argument("--max-trades", type=int, default=2, help="trades max par seance")
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--out", default="resultats_flow_options.csv")
    ap.add_argument("--demo", action="store_true", help="donnees synthetiques (test de la mecanique)")
    a = ap.parse_args()

    p = {"contract": a.contract, "setups": set(a.setups.split(",")), "rr": a.rr, "imb": a.imb,
         "vol_mult": a.vol_mult, "tol_pct": a.tol_pct, "stop_buf": a.stop_buf,
         "slip_ticks": a.slip_ticks, "min_stop_ticks": a.min_stop_ticks,
         "max_stop_pts": a.max_stop_pts, "max_trades": a.max_trades}
    bad = p["setups"] - set(ALL_SETUPS)
    if bad:
        sys.exit(f"setups inconnus : {bad}")

    if a.demo:
        import tempfile, os
        flow, lv = make_demo()
        tmp = tempfile.mkdtemp()
        fpath, lpath = os.path.join(tmp, "flow.csv"), os.path.join(tmp, "levels.csv")
        flow.to_csv(fpath, index=False); lv.to_csv(lpath, index=False)
        a.flow, a.levels, a.tz = fpath, lpath, "UTC"
        print("MODE DEMO : donnees synthetiques, resultats SANS valeur.\n")
    if not a.flow:
        ap.error("fichier flow requis (ou --demo)")

    bars = load_flow(a.flow, a.tz, a.side_format, a.approx_delta)
    if a.dfrom: bars = bars[bars.index >= pd.Timestamp(a.dfrom, tz=NY_TZ)]
    if a.dto:   bars = bars[bars.index <  pd.Timestamp(a.dto, tz=NY_TZ) + pd.Timedelta(days=1)]
    bars = add_flow_features(bars)
    levels = load_levels(a.levels, bars, a.levels_native)
    print(f"{len(bars):,} barres 1 min  |  {bars.index.min()} -> {bars.index.max()}")
    print(f"{len(levels)} seances avec niveaux  |  contrat {a.contract}  |  setups {sorted(p['setups'])}")
    print(f"RR {a.rr}  imb>={a.imb}  vol>={a.vol_mult}x  tol {a.tol_pct}%  "
          f"entree {ENTRY_START}-{ENTRY_END} NY  cloture {FORCE_CLOSE}\n")
    tr = run(bars, levels, p)
    print(report(tr))
    tr.to_csv(a.out, index=False)
    print(f"\nDetail -> {a.out}")


if __name__ == "__main__":
    main()
