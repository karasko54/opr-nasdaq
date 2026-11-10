#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collecte quotidienne de la chaine d'options QQQ (yfinance) AVANT l'ouverture US,
sauvegarde la chaine brute et ajoute une ligne de niveaux dans options_levels.csv.

Pourquoi ce script existe : yfinance ne donne QUE la chaine du jour, jamais
l'historique. L'historique d'open interest par strike est payant (ORATS, CBOE
DataShop, ThetaData, Databento OPRA...). Sans achat, la seule facon d'avoir un
backtest honnete est de commencer a enregistrer MAINTENANT, chaque matin.

Pourquoi QQQ et pas ^NDX : les options NDX sont bien moins liquides et moins
bien renseignees sur Yahoo ; QQQ porte l'essentiel du positionnement
options sur le Nasdaq-100. Le backtest convertit les niveaux QQQ en points NQ
avec le ratio NQ/QQQ de la veille.

A lancer idealement entre ~07:00 et 09:25 New York (OI de l'OCC publie, marche
pas ouvert). Tolere jusqu'a 16:00 NY car les crons GitHub arrivent souvent avec
plusieurs heures de retard : l'OI ne change pas en seance (publie une fois par
jour) et le spot utilise est la cloture de la veille. Seule l'IV est alors
celle du jour — impact faible sur la position des murs, consigne dans
`snap_time` pour pouvoir filtrer.
Sorties :
    data/options/AAAA-MM-JJ_QQQ.csv.gz   chaine brute normalisee (recalculable)
    options_levels.csv                   1 ligne par seance
"""
import datetime as dt
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd

from options_levels import clean_chain, compute_levels, MAX_DTE

SYMBOL = os.getenv("OPT_SYMBOL", "QQQ")
OUT_DIR = os.path.join("data", "options")
LEVELS_CSV = "options_levels.csv"
NY = ZoneInfo("America/New_York")


def fetch_chain(symbol):
    import yfinance as yf
    tk = yf.Ticker(symbol)
    today = dt.datetime.now(NY).date()
    hist = tk.history(period="10d", interval="1d")
    # spot = cloture de la VEILLE, toujours. Si le script tourne en seance, la
    # barre du jour existe deja et contient un prix intraday : la prendre
    # ferait entrer de l'information du jour dans des niveaux censes etre
    # connus a l'ouverture.
    hist = hist[[d.date() < today for d in hist.index]]
    if hist.empty:
        raise RuntimeError("pas de prix pour " + symbol)
    spot = float(hist["Close"].iloc[-1])
    rows = []
    for exp in tk.options:
        dte = (dt.date.fromisoformat(exp) - today).days
        if dte < 0 or dte > MAX_DTE:
            continue
        for attempt in range(3):
            try:
                oc = tk.option_chain(exp)
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        else:
            print(f"  echeance {exp} ignoree (3 echecs)")
            continue
        for typ, df in (("C", oc.calls), ("P", oc.puts)):
            if df is None or df.empty:
                continue
            rows.append(pd.DataFrame({
                "expiry": exp, "strike": df["strike"], "type": typ,
                "oi": df["openInterest"], "iv": df["impliedVolatility"],
            }))
    if not rows:
        raise RuntimeError("chaine vide")
    ch = pd.concat(rows, ignore_index=True)
    ch.insert(0, "date", today.isoformat())
    ch.insert(1, "underlying", symbol)
    ch.insert(2, "spot", spot)
    return ch


def main():
    now = dt.datetime.now(NY)
    if now.weekday() >= 5:
        print("week-end : rien a collecter"); return 0
    if now.time() >= dt.time(16, 0) and os.getenv("OPT_FORCE") != "1":
        # apres la cloture, Yahoo peut basculer sur l'OI du lendemain pendant la
        # nuit : la photo ne correspondrait plus a la seance datee. OPT_FORCE=1 pour forcer.
        print("marche ferme : photo refusee (OPT_FORCE=1 pour forcer)"); return 0
    ch = fetch_chain(SYMBOL)
    day = ch["date"].iloc[0]
    os.makedirs(OUT_DIR, exist_ok=True)
    raw_path = os.path.join(OUT_DIR, f"{day}_{SYMBOL}.csv.gz")
    ch.to_csv(raw_path, index=False, compression="gzip")
    lv = compute_levels(clean_chain(ch))
    if lv is not None:
        lv["snap_time"] = now.strftime("%H:%M")
    if lv is None:
        raise RuntimeError("niveaux non calculables")
    old = pd.read_csv(LEVELS_CSV) if os.path.exists(LEVELS_CSV) else pd.DataFrame()
    if len(old):
        old = old[old["date"].astype(str) != day]
    out = pd.concat([old, pd.DataFrame([lv])], ignore_index=True).sort_values("date")
    out.to_csv(LEVELS_CSV, index=False)
    print(f"{day} {SYMBOL} spot={lv['ref_spot']} CW={lv['call_wall']} PW={lv['put_wall']} "
          f"ZG={lv['zero_gamma']} GEX={lv['net_gex_musd']} M$  ({len(ch)} lignes brutes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
