#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest de la logique OPR / US Breakout (Benjamin Deleuze).
Lit un CSV de bougies M1, reconstruit M5/M15/H1, applique la strategie,
et sort les metriques (trades, win rate, profit factor, R total, drawdown).

Usage:
    python backtest_opr.py CHEMIN_DU_CSV [--tz UTC] [--tp 4] [--be 1] [--asset gold]
                           [--spread PTS] [--slippage PTS] [--commission R] [--no-costs]
                           [--buffer 0.40]

Le CSV doit contenir: datetime, open, high, low, close [,volume]
(formats de date courants gérés automatiquement).

ATTENTION — SOURCE DES DONNEES. Ce backtest lit le CSV que tu lui donnes, alors
que opr_live.py lit ^NDX via yfinance. Verification faite sur 19 journees
communes : les ranges d'ouverture Dukascopy (NAS100 CFD) sont ~5 % plus larges
et les prix decales de ~127 points (0,4 %). Consequence : les signaux ne sont
pas rigoureusement les memes, et un seuil de filtre calibre ici correspond a un
seuil legerement plus BAS sur les donnees du robot. Les conclusions
structurelles tiennent, les valeurs exactes non.

FRAIS : par defaut, chaque trade est net de spread + slippage + commission
(table COSTS ci-dessous, a ajuster selon TON broker). --no-costs pour un
backtest theorique sans frais (ancien comportement).
"""
import sys, argparse
import numpy as np
import pandas as pd

# ───────────────────────── Reglages strategie ─────────────────────────
NY_TZ          = "America/New_York"
OPEN_START     = "09:30"   # ouverture US (= 15h30 Paris)
OPEN_END       = "09:45"   # fin bougie d'ouverture M15
ENTRY_END      = "11:30"   # fin fenetre d'entree (= 17h30 Paris)
FORCE_CLOSE    = "15:00"   # cloture forcee (= 21h00 Paris)
ST_ATR, ST_FACT = 10, 3.0  # Supertrend H1 (defaut TradingView)
EMA_FAST, EMA_SLOW = 20, 50
RISK_PCT       = 0.25      # % equity par trade
START_EQUITY   = 10000.0

# table par actif: (TP en R, BE en R ou None, mois exclus)
ASSETS = {
    "gold":   (4.0, 1.0, {3, 10}),   # XAUUSD : TP4, BE1R, exclu mars+octobre
    # NAS100 : TP3.5, BE2R. AVRIL N'EST PLUS EXCLU — mesure le 28/09/2026 sur
    # 11 ans : avril donne 66 trades a +0,232 R/trade (WR 40,9 %), soit un des
    # MEILLEURS mois, contre ~0,09 R en moyenne generale. L'exclure coutait
    # 15,3 R et faisait tomber le t de 2,08 a 1,79. La regle venait du backtest
    # 22 ans de la video, invérifiable ici ; rien dans nos donnees ne la soutient.
    "nas":    (3.5, 2.0, set()),
    "us30":   (2.0, None, set()),
    "usdcad": (3.0, None, set()),
    "btc":    (3.5, None, set()),
    "wti":    (3.5, 1.0, set()),
}

# ───────────────────────── Frais / spread par actif ─────────────────────────
# Cout REEL preleve par le broker, en POINTS de prix (unite de l'actif), a
# ajuster selon TON broker/compte :
#   spread     = ecart bid/ask, paye a l'aller-retour
#   slippage   = derapage sur l'entree STOP (la cassure part toujours contre toi)
#   commission = frais fixes eventuels, exprimes directement en R par trade
# Total deduit par trade = (spread + slippage) / risque_en_points + commission_R
COSTS = {
    "gold":   {"spread": 0.30,   "slippage": 0.10,   "commission_R": 0.0},
    "nas":    {"spread": 2.0,    "slippage": 1.0,    "commission_R": 0.0},
    "us30":   {"spread": 3.0,    "slippage": 1.5,    "commission_R": 0.0},
    "usdcad": {"spread": 0.0002, "slippage": 0.0001, "commission_R": 0.0},
    "btc":    {"spread": 30.0,   "slippage": 15.0,   "commission_R": 0.0},
    "wti":    {"spread": 0.03,   "slippage": 0.02,   "commission_R": 0.0},
}
DEFAULT_COST = {"spread": 0.0, "slippage": 0.0, "commission_R": 0.0}

# ── Tampon anti-fausse-cassure, en fraction du range ────────────────────────
# L'ordre STOP est place a BUFFER x range AU-DELA du bord, au lieu du bord lui-
# meme. Valeurs reprises des workflows GitHub Actions (OPR_BUFFER), pour que le
# backtest simule ce que le robot fait reellement. 0 = regle de la video.
BUFFERS = {"nas": 0.40, "btc": 0.50}
DEFAULT_BUFFER = 0.0

# ───────────────────────── Chargement donnees ─────────────────────────
def load_csv(path, tz):
    # lecture souple: detecte header et separateur
    # PERFORMANCE : le moteur "python" (sep=None) parse ligne a ligne et met
    # plusieurs dizaines de minutes sur un fichier de 245 Mo. On tente d'abord le
    # moteur C, qui fait la meme chose en quelques secondes ; repli sur l'ancien
    # comportement si le separateur n'est pas une virgule.
    try:
        df = pd.read_csv(path, header=0)
        if df.shape[1] < 4:
            raise ValueError("separateur non standard")
    except Exception:
        df = pd.read_csv(path, sep=None, engine="python", header=0)
    cols = [c.strip().lower() for c in df.columns]
    df.columns = cols
    # si pas de colonne datetime explicite, tente date+time ou 1ere colonne
    if "datetime" not in cols:
        if "date" in cols and "time" in cols:
            df["datetime"] = df["date"].astype(str) + " " + df["time"].astype(str)
        else:
            df = df.rename(columns={cols[0]: "datetime"})
    # renomme ohlc si prefixes (gmt time, etc.)
    ren = {}
    for c in df.columns:
        if c.startswith("open"):  ren[c] = "open"
        if c.startswith("high"):  ren[c] = "high"
        if c.startswith("low"):   ren[c] = "low"
        if c.startswith("close"): ren[c] = "close"
        if c.startswith("vol"):   ren[c] = "volume"
    df = df.rename(columns=ren)
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce", dayfirst=False)
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.set_index("datetime")
    # localise puis convertit en heure de New York
    if df.index.tz is None:
        df.index = df.index.tz_localize(tz)
    df.index = df.index.tz_convert(NY_TZ)
    return df[["open", "high", "low", "close"]]

def resample(df, rule):
    o = df["open"].resample(rule).first()
    h = df["high"].resample(rule).max()
    l = df["low"].resample(rule).min()
    c = df["close"].resample(rule).last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def supertrend(df, atr_n, fact):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/atr_n, adjust=False).mean()   # lissage Wilder
    hl2 = (h + l) / 2
    upper = hl2 + fact * atr
    lower = hl2 - fact * atr
    fu = upper.copy(); fl = lower.copy()
    dir_ = pd.Series(index=df.index, dtype=float)
    for i in range(len(df)):
        if i == 0:
            dir_.iloc[i] = 1; continue
        fu.iloc[i] = upper.iloc[i] if (upper.iloc[i] < fu.iloc[i-1] or c.iloc[i-1] > fu.iloc[i-1]) else fu.iloc[i-1]
        fl.iloc[i] = lower.iloc[i] if (lower.iloc[i] > fl.iloc[i-1] or c.iloc[i-1] < fl.iloc[i-1]) else fl.iloc[i-1]
        if c.iloc[i] > fu.iloc[i-1]:
            dir_.iloc[i] = 1
        elif c.iloc[i] < fl.iloc[i-1]:
            dir_.iloc[i] = -1
        else:
            dir_.iloc[i] = dir_.iloc[i-1]
    return dir_   # +1 = haussier (vert), -1 = baissier (rouge)

# ───────────────────────── Backtest ─────────────────────────
def run(df, tp_r, be_r, skip_months, cost_pts=0.0, commission_R=0.0, buffer=0.0, range_min=0.0, range_min_pct=0.0):
    m1  = df
    m5  = resample(df, "5min")
    h1  = resample(df, "60min")
    m5["ema_f"] = ema(m5["close"], EMA_FAST)
    m5["ema_s"] = ema(m5["close"], EMA_SLOW)
    h1["st"]    = supertrend(h1, ST_ATR, ST_FACT)

    # PERFORMANCE : on precalcule en UNE passe la derniere valeur des filtres a
    # 09:45 pour chaque journee. Avant, chaque journee refiltrait l'integralite
    # des index M5 et H1 (m5.index.normalize().date == day), soit des milliards
    # de comparaisons sur 11 ans : le backtest mettait plus de 20 minutes.
    # Resultat strictement identique, temps divise par ~200.
    import datetime as _dt
    _lim = _dt.time(9, 45)
    _m5s = m5[m5.index.time <= _lim].copy(); _m5s["_d"] = _m5s.index.date
    _h1s = h1[h1.index.time <= _lim].copy(); _h1s["_d"] = _h1s.index.date
    SNAP_M5 = _m5s.groupby("_d")[["ema_f", "ema_s"]].last().to_dict("index")
    SNAP_H1 = _h1s.groupby("_d")["st"].last().to_dict()
    del _m5s, _h1s

    equity = START_EQUITY
    trades = []
    for day, g1 in m1.groupby(m1.index.date):
        month = day.month
        if month in skip_months:
            continue
        # bougie d'ouverture 09:30-09:45 (exclut 09:45)
        op = g1.between_time(OPEN_START, OPEN_END, inclusive="left")
        if len(op) == 0:
            continue
        orH, orL = op["high"].max(), op["low"].min()
        orMid = (orH + orL) / 2
        if not np.isfinite(orH) or orH <= orL:
            continue
        # filtres figes a 09:45 (lus dans les snapshots precalcules)
        _sm, _sh = SNAP_M5.get(day), SNAP_H1.get(day)
        if _sm is None or _sh is None:
            continue
        ef, es, st = _sm["ema_f"], _sm["ema_s"], _sh
        if not (np.isfinite(ef) and np.isfinite(es)):
            continue
        long_ok  = (ef > es) and (st > 0)
        short_ok = (ef < es) and (st < 0)
        if not (long_ok or short_ok):
            continue
        side = 1 if long_ok else -1
        # filtre de range : sous ce seuil le stop est trop petit pour absorber
        # les frais. 0 = inactif.
        if range_min > 0 and (orH - orL) < range_min:
            continue
        if range_min_pct > 0 and (orH - orL) / ((orH + orL) / 2) * 100 < range_min_pct:
            continue
        # CORRECTIF : tampon anti-fausse-cassure, identique a opr_live.py.
        # buffer=0 reproduit la regle de la video (ordre au bord du range).
        rng = orH - orL
        if side == 1:
            entry, sl = orH + buffer * rng, orMid
            tp = entry + (entry - sl) * tp_r
        else:
            entry, sl = orL - buffer * rng, orMid
            tp = entry - (sl - entry) * tp_r
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        # fenetre d'execution
        # CORRECTIF : on suit le trade jusqu'a la CLOTURE FORCEE (15:00 NY = 21h
        # Paris), pas jusqu'a la fin de la fenetre d'ENTREE (11:30 NY = 17h30).
        # Avant, toute position encore ouverte a 17h30 etait soldee la, soit 3h30
        # trop tot : FORCE_CLOSE etait declare mais n'avait aucun effet, et le
        # backtest ne simulait pas ce que fait opr_live.py. Le garde-fou qui
        # interdit d'ENTRER apres ENTRY_END est quelques lignes plus bas, inchange.
        win = g1.between_time(OPEN_END, FORCE_CLOSE)
        bars = win[win.index <= pd.Timestamp(f"{day} {FORCE_CLOSE}", tz=NY_TZ)]
        in_pos = False; be_done = False; result_R = None; exit_reason = None
        for t, b in bars.iterrows():
            if not in_pos:
                # declenchement de l'ordre stop a la cassure
                if side == 1 and b["high"] >= entry: in_pos = True
                elif side == -1 and b["low"] <= entry: in_pos = True
                if in_pos and t.strftime("%H:%M") > ENTRY_END:
                    in_pos = False; break
            if in_pos:
                # break-even
                if be_r and not be_done:
                    if side == 1 and b["high"] >= entry + risk*be_r:
                        sl = entry; be_done = True
                    elif side == -1 and b["low"] <= entry - risk*be_r:
                        sl = entry; be_done = True
                # sorties (conservateur: SL teste avant TP si les deux dans la bougie)
                if side == 1:
                    if b["low"] <= sl:  result_R = (sl-entry)/risk; exit_reason="SL"; break
                    if b["high"] >= tp: result_R = (tp-entry)/risk; exit_reason="TP"; break
                else:
                    if b["high"] >= sl: result_R = (entry-sl)/risk; exit_reason="SL"; break
                    if b["low"]  <= tp: result_R = (entry-tp)/risk; exit_reason="TP"; break
        if in_pos and result_R is None:   # cloture forcee 15:00
            last = bars["close"].iloc[-1]
            result_R = ((last-entry) if side==1 else (entry-last))/risk
            exit_reason = "CLOSE"
        if result_R is None:
            continue   # ordre jamais declenche
        # frais reels : spread+slippage rapportes au risque, + commission fixe
        cost_R = (cost_pts / risk if risk > 0 else 0.0) + commission_R
        gross_R = result_R
        net_R = gross_R - cost_R
        pnl = equity * (RISK_PCT/100) * net_R
        equity += pnl
        trades.append({"date": str(day), "side": "L" if side==1 else "S",
                       "R": round(net_R,2), "R_brut": round(gross_R,2),
                       "frais_R": round(cost_R,3), "reason": exit_reason,
                       "equity": round(equity,2)})
    return pd.DataFrame(trades)

def metrics(tr):
    if len(tr) == 0:
        return "Aucun trade genere."
    wins = tr[tr["R"] > 0]; losses = tr[tr["R"] <= 0]
    gp = wins["R"].sum(); gl = abs(losses["R"].sum())
    pf = gp/gl if gl > 0 else float("inf")
    eq = tr["equity"].values
    peak = np.maximum.accumulate(eq); dd = (peak-eq)/peak
    out = []
    out.append(f"Trades              : {len(tr)}")
    out.append(f"Win rate            : {len(wins)/len(tr)*100:.2f} %")
    out.append(f"R total (NET frais) : {tr['R'].sum():.1f} R")
    if "frais_R" in tr.columns:
        out.append(f"  - R brut (theo.)  : {tr['R_brut'].sum():.1f} R")
        out.append(f"  - frais deduits   : -{tr['frais_R'].sum():.1f} R "
                   f"({tr['frais_R'].mean():.3f} R/trade)")
    out.append(f"Profit factor       : {pf:.3f}")
    out.append(f"Gain moyen/trade    : {tr['R'].mean():.3f} R")
    out.append(f"Max drawdown        : {dd.max()*100:.2f} %")
    out.append(f"Equity finale       : {eq[-1]:.2f} (depart {START_EQUITY:.0f})")
    out.append(f"Sorties: TP={ (tr['reason']=='TP').sum() }  SL={ (tr['reason']=='SL').sum() }  "
               f"BE/CLOSE={ tr['reason'].isin(['CLOSE']).sum() }")
    return "\n".join(out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--tz", default="UTC", help="fuseau des donnees (defaut UTC = Dukascopy)")
    ap.add_argument("--asset", default="gold", choices=list(ASSETS))
    ap.add_argument("--tp", type=float, default=None)
    ap.add_argument("--be", type=float, default=None)
    ap.add_argument("--spread", type=float, default=None, help="spread en points (override)")
    ap.add_argument("--slippage", type=float, default=None, help="slippage entree en points (override)")
    ap.add_argument("--commission", type=float, default=None, help="commission en R/trade (override)")
    ap.add_argument("--no-costs", action="store_true", help="backtest theorique sans frais")
    ap.add_argument("--buffer", type=float, default=None,
                    help="tampon en fraction du range (defaut : valeur du robot ; 0 = regle video)")
    a = ap.parse_args()
    tp_r, be_r, skip = ASSETS[a.asset]
    buffer = a.buffer if a.buffer is not None else BUFFERS.get(a.asset, DEFAULT_BUFFER)
    if a.tp is not None: tp_r = a.tp
    if a.be is not None: be_r = (a.be if a.be > 0 else None)

    # frais : table par actif, override possible en ligne de commande
    cost = dict(COSTS.get(a.asset, DEFAULT_COST))
    spread   = a.spread     if a.spread     is not None else cost["spread"]
    slippage = a.slippage   if a.slippage   is not None else cost["slippage"]
    commission_R = a.commission if a.commission is not None else cost["commission_R"]
    if a.no_costs:
        spread = slippage = commission_R = 0.0
    cost_pts = spread + slippage

    print(f"Chargement {a.csv} (tz={a.tz})...")
    df = load_csv(a.csv, a.tz)
    print(f"  {len(df):,} bougies M1 | du {df.index.min()} au {df.index.max()}")
    print(f"Actif={a.asset}  TP={tp_r}R  BE={be_r}  mois_exclus={sorted(skip) or 'aucun'}")
    print(f"Tampon={buffer:g} x range  |  entree {'au bord (regle video)' if buffer == 0 else 'decalee'}  "
          f"|  suivi jusqu'a {FORCE_CLOSE} NY")
    if a.no_costs:
        print("Frais : AUCUN (--no-costs, backtest theorique)\n")
    else:
        print(f"Frais : spread={spread:g} + slippage={slippage:g} pts  "
              f"commission={commission_R:g} R/trade\n")
    tr = run(df, tp_r, be_r, skip, cost_pts, commission_R, buffer)
    print(metrics(tr))
    tr.to_csv("resultats_trades.csv", index=False)
    print("\nDetail des trades -> resultats_trades.csv")
