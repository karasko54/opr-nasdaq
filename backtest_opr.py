#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest de la logique OPR / US Breakout (Benjamin Deleuze).
Lit un CSV de bougies M1, reconstruit M5/M15/H1, applique la strategie,
et sort les metriques (trades, win rate, profit factor, R total, drawdown).

Usage:
    python backtest_opr.py CHEMIN_DU_CSV [--tz UTC] [--tp 4] [--be 1] [--asset gold]

Le CSV doit contenir: datetime, open, high, low, close [,volume]
(formats de date courants gérés automatiquement).
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
    "nas":    (3.5, 2.0, {4}),       # NAS100 : TP3.5, BE2R, exclu avril
    "us30":   (2.0, None, set()),
    "usdcad": (3.0, None, set()),
    "btc":    (3.5, None, set()),
    "wti":    (3.5, 1.0, set()),
}

# ───────────────────────── Chargement donnees ─────────────────────────
def load_csv(path, tz):
    # lecture souple: detecte header et separateur
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
def run(df, tp_r, be_r, skip_months):
    m1  = df
    m5  = resample(df, "5min")
    h1  = resample(df, "60min")
    m5["ema_f"] = ema(m5["close"], EMA_FAST)
    m5["ema_s"] = ema(m5["close"], EMA_SLOW)
    h1["st"]    = supertrend(h1, ST_ATR, ST_FACT)

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
        ts_decision = pd.Timestamp(f"{day} {OPEN_END}", tz=NY_TZ)
        # filtres figes a 09:45
        m5d = m5[(m5.index.normalize().date == day) & (m5.index <= ts_decision)]
        h1d = h1[(h1.index.normalize().date == day) & (h1.index <= ts_decision)]
        if len(m5d) == 0 or len(h1d) == 0:
            continue
        ef, es = m5d["ema_f"].iloc[-1], m5d["ema_s"].iloc[-1]
        st = h1d["st"].iloc[-1]
        if not (np.isfinite(ef) and np.isfinite(es)):
            continue
        long_ok  = (ef > es) and (st > 0)
        short_ok = (ef < es) and (st < 0)
        if not (long_ok or short_ok):
            continue
        side = 1 if long_ok else -1
        if side == 1:
            entry, sl = orH, orMid
            tp = entry + (entry - sl) * tp_r
        else:
            entry, sl = orL, orMid
            tp = entry - (sl - entry) * tp_r
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        # fenetre d'execution
        win = g1.between_time(OPEN_END, ENTRY_END)
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
        pnl = equity * (RISK_PCT/100) * result_R
        equity += pnl
        trades.append({"date": str(day), "side": "L" if side==1 else "S",
                       "R": round(result_R,2), "reason": exit_reason,
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
    out.append(f"R total (somme)     : {tr['R'].sum():.1f} R")
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
    a = ap.parse_args()
    tp_r, be_r, skip = ASSETS[a.asset]
    if a.tp is not None: tp_r = a.tp
    if a.be is not None: be_r = (a.be if a.be > 0 else None)
    print(f"Chargement {a.csv} (tz={a.tz})...")
    df = load_csv(a.csv, a.tz)
    print(f"  {len(df):,} bougies M1 | du {df.index.min()} au {df.index.max()}")
    print(f"Actif={a.asset}  TP={tp_r}R  BE={be_r}  mois_exclus={sorted(skip) or 'aucun'}\n")
    tr = run(df, tp_r, be_r, skip)
    print(metrics(tr))
    tr.to_csv("resultats_trades.csv", index=False)
    print("\nDetail des trades -> resultats_trades.csv")
