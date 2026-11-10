#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telechargement des donnees pour le backtest ORDER FLOW + NIVEAUX D'OPTIONS (BTC).

Deux sources publiques et gratuites, sans cle :

  1. ORDER FLOW  — Binance USD-M futures, aggTrades BTCUSDT (data.binance.vision)
     Chaque transaction porte le cote AGRESSEUR (is_buyer_maker). C'est du vrai
     order flow, pas un "volume de ticks" de courtier CFD. On l'agrege en barres
     d'une minute : OHLC, volume achete/vendu au marche, gros ordres.

  2. OPTIONS     — Deribit (history.deribit.com), toutes les transactions
     d'options BTC, avec sens de l'agresseur, volatilite implicite et prix de
     l'indice. Sert a reconstruire le positionnement des teneurs de marche.

Usage :
    python of_download.py flow    --start 2023-07-01 --end 2026-11-08
    python of_download.py options --start 2023-01-01 --end 2026-11-08

Sortie : data/flow/AAAA-MM-JJ.parquet et data/options/AAAA-MM.parquet
(le dossier data/ n'est PAS versionne : ~200 Mo, regenerable).
"""
import argparse
import datetime as dt
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BINANCE = ("https://data.binance.vision/data/futures/um/daily/aggTrades/"
           "{sym}/{sym}-aggTrades-{day}.zip")
DERIBIT = "https://history.deribit.com/api/v2/public/get_last_trades_by_currency_and_time"

# Un "gros ordre" = une execution agressive d'au moins ce notionnel (USD).
# Exprime en dollars et non en BTC : 1 BTC valait 30 k$ en 2023 et >100 k$ en
# 2025, un seuil en BTC ne designerait pas les memes acteurs d'une annee a l'autre.
BIG_USD = 100_000.0


def _get(url, params=None, tries=6, timeout=60):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = repr(e)
        time.sleep(min(2 ** i, 30))
    raise RuntimeError(f"echec {url} {params}: {err}")


# ───────────────────────── Order flow (Binance) ─────────────────────────
def flow_day(day, sym="BTCUSDT"):
    out = os.path.join(DATA, "flow", f"{day}.parquet")
    if os.path.exists(out):
        return day, "deja la"
    r = _get(BINANCE.format(sym=sym, day=day))
    if r is None:
        return day, "ABSENT (404)"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0])
    # les anciens fichiers n'ont pas d'en-tete, les recents si
    has_header = raw[:20].lstrip()[:1].isalpha()
    df = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None,
                     usecols=[1, 2, 5, 6], names=None if has_header else list(range(7)))
    df.columns = ["price", "qty", "ts", "buyer_maker"]
    if df["buyer_maker"].dtype != bool:
        df["buyer_maker"] = df["buyer_maker"].astype(str).str.lower().eq("true")
    ts = df["ts"].to_numpy("int64")
    if ts[0] > 10 ** 14:          # microsecondes (format spot recent)
        ts = ts // 1000
    minute = ts // 60_000
    p = df["price"].to_numpy(float)
    q = df["qty"].to_numpy(float)
    # buyer_maker = True  -> l'acheteur etait passif -> l'AGRESSEUR est vendeur
    buy = np.where(df["buyer_maker"].to_numpy(), 0.0, q)
    sell = q - buy
    big = (p * q) >= BIG_USD
    g = pd.DataFrame({"m": minute, "p": p, "q": q, "pq": p * q, "buy": buy, "sell": sell,
                      "big_buy": buy * big, "big_sell": sell * big}).groupby("m", sort=True)
    bars = pd.DataFrame({
        "open": g["p"].first(), "high": g["p"].max(), "low": g["p"].min(),
        "close": g["p"].last(), "vol": g["q"].sum(), "notional": g["pq"].sum(),
        "buy": g["buy"].sum(), "sell": g["sell"].sum(),
        "big_buy": g["big_buy"].sum(), "big_sell": g["big_sell"].sum(),
        "n": g["q"].size(),
    })
    bars.index = pd.to_datetime(bars.index * 60_000, unit="ms", utc=True)
    bars.index.name = "time"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bars.to_parquet(out + ".tmp")
    os.replace(out + ".tmp", out)
    return day, f"{len(df):,} trades -> {len(bars)} barres"


# ───────────────────────── Options (Deribit) ─────────────────────────
KEEP = ["timestamp", "trade_id", "instrument_name", "price", "mark_price", "iv",
        "index_price", "direction", "amount"]


def options_month(month_start, month_end):
    tag = month_start.strftime("%Y-%m")
    out = os.path.join(DATA, "options", f"{tag}.parquet")
    if os.path.exists(out):
        return tag, "deja la"
    start = int(month_start.timestamp() * 1000)
    end = int(month_end.timestamp() * 1000) - 1
    rows, seen = [], set()
    while start <= end:
        r = _get(DERIBIT, params={"currency": "BTC", "kind": "option", "count": 10000,
                                  "start_timestamp": start, "end_timestamp": end,
                                  "sorting": "asc"})
        res = r.json()["result"]
        tr = res["trades"]
        if not tr:
            break
        for t in tr:
            if t["trade_id"] in seen:
                continue
            seen.add(t["trade_id"])
            row = [t.get(k) for k in KEEP]
            row.append(t.get("block_trade_id") is not None)
            rows.append(row)
        last = tr[-1]["timestamp"]
        if not res.get("has_more"):
            break
        # on repart du dernier horodatage (inclus) : plusieurs trades peuvent
        # partager la meme milliseconde, le dedoublonnage par trade_id fait le tri
        start = last if last > start else last + 1
    df = pd.DataFrame(rows, columns=KEEP + ["block"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out + ".tmp")
    os.replace(out + ".tmp", out)
    return tag, f"{len(df):,} trades"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["flow", "options"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    d0 = dt.date.fromisoformat(a.start)
    d1 = dt.date.fromisoformat(a.end)
    if a.what == "flow":
        jobs = [(flow_day, (str(d0 + dt.timedelta(days=i)),)) for i in range((d1 - d0).days + 1)]
    else:
        jobs, m = [], d0.replace(day=1)
        while m <= d1:
            nxt = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
            utc = dt.timezone.utc
            ms = dt.datetime(m.year, m.month, m.day, tzinfo=utc)
            me = min(dt.datetime(nxt.year, nxt.month, 1, tzinfo=utc),
                     dt.datetime(d1.year, d1.month, d1.day, tzinfo=utc) + dt.timedelta(days=1))
            jobs.append((options_month, (ms, me)))
            m = nxt
    done = 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(f, *args) for f, args in jobs]
        for fu in as_completed(futs):
            done += 1
            try:
                tag, msg = fu.result()
            except Exception as e:      # on continue, le script est relancable
                tag, msg = "?", f"ERREUR {e}"
            if "deja" not in msg:
                print(f"[{done}/{len(jobs)}] {tag}: {msg}", flush=True)
    print("termine.")


if __name__ == "__main__":
    sys.exit(main())
