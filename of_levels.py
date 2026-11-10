#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Niveaux d'options BTC : exposition gamma des teneurs de marche (GEX), par strike.

METHODE — et pourquoi pas la methode "classique".
La methode repandue (SpotGamma & co) prend l'open interest et SUPPOSE que les
teneurs de marche sont acheteurs de calls et vendeurs de puts. C'est une
convention heritee des actions US (clients qui vendent des calls couverts et
achetent des puts de protection). Elle est fausse sur le BTC, ou les clients
achetent massivement des calls ET vendent beaucoup de volatilite.

Ici on ne suppose rien : Deribit publie le SENS DE L'AGRESSEUR de chaque
transaction. Celui qui agresse est le client, celui qui est passif est (tres
majoritairement) le teneur de marche. On cumule donc, instrument par instrument :

    position_teneur = - somme( +quantite si le client achete, -quantite s'il vend )

puis  GEX(strike) = position_teneur x gamma(Black-Scholes) x spot^2 x 1 %
      = dollars que le teneur doit acheter/vendre pour se recouvrir si le prix
        bouge de 1 %.

  GEX > 0 : teneur LONG gamma  -> il vend les hausses, achete les baisses
            -> le prix COLLE au strike (aimant, mur). Logique : contrer.
  GEX < 0 : teneur SHORT gamma -> il achete les hausses, vend les baisses
            -> le prix ACCELERE au passage du strike. Logique : suivre.

LIMITES, a garder en tete :
  - une position ouverte AVANT le debut de l'historique est invisible (d'ou les
    6 mois de chauffe avant le debut du backtest) ;
  - "agresseur = client" est vrai en moyenne, pas trade par trade ;
  - les positions prises hors Deribit (OTC, CME, IBIT) ne sont pas vues.

ANTI-TRICHE : la photo du jour J est prise a 08:00 UTC (heure d'expiration
Deribit) avec les seules transactions anterieures. Les niveaux servent ensuite
de 08:00 J a 08:00 J+1. Aucune information du futur.

Usage : python of_levels.py            -> data/levels.parquet + data/levels_daily.parquet
"""
import glob
import os

import numpy as np
import pandas as pd

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SNAP_HOUR = 8            # 08:00 UTC = expiration Deribit
BAND = 0.06              # on ne garde que les strikes a +/- 6 % du spot
TOP_N = 4                # nombre de strikes retenus par jour (les plus gros |GEX|)
MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def load_trades():
    files = sorted(glob.glob(os.path.join(DATA, "options", "*.parquet")))
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.drop_duplicates("trade_id").sort_values("timestamp", kind="stable")
    # BTC-27JUN25-100000-C  -> expiration, strike, type
    inst = pd.Series(df["instrument_name"].unique())
    parts = inst.str.split("-", expand=True)
    d = parts[1].str.extract(r"^(\d{1,2})([A-Z]{3})(\d{2})$")
    exp = pd.to_datetime(dict(year=2000 + d[2].astype(int), month=d[1].map(MONTHS),
                              day=d[0].astype(int)), utc=True) + pd.Timedelta(hours=SNAP_HOUR)
    meta = pd.DataFrame({"expiry": exp.values, "strike": parts[2].astype(float).values,
                         "cp": parts[3].values}, index=inst.values)
    meta["expiry"] = pd.to_datetime(meta["expiry"], utc=True)
    df["signed"] = np.where(df["direction"] == "buy", df["amount"], -df["amount"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df, meta


def bs_gamma(S, K, T, sigma):
    """Gamma Black-Scholes (taux nul). S peut etre un vecteur (grille de spots)."""
    sigma = np.clip(sigma, 0.05, 5.0)
    T = np.maximum(T, 1.0 / (365 * 24))          # plancher : 1 heure
    v = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * v * v) / v
    return np.exp(-0.5 * d1 * d1) / (np.sqrt(2 * np.pi) * S * v)


def build():
    df, meta = load_trades()
    # jour de photo : la 1re photo (08:00) strictement posterieure a la transaction
    snap = (df["time"] - pd.Timedelta(hours=SNAP_HOUR)).dt.floor("D") \
        + pd.Timedelta(days=1, hours=SNAP_HOUR)
    df["snap"] = snap
    days = pd.date_range(df["snap"].min(), df["snap"].max(), freq="D")

    pos = pd.Series(dtype=float)          # position teneur par instrument
    iv = pd.Series(dtype=float)           # derniere vol implicite connue
    lv_rows, day_rows = [], []
    by_snap = dict(tuple(df.groupby("snap", sort=True)))
    grid_rel = np.arange(0.85, 1.1501, 0.0025)

    for day in days:
        g = by_snap.get(day)
        if g is not None:
            pos = pos.add(-g.groupby("instrument_name")["signed"].sum(), fill_value=0.0)
            last = g.groupby("instrument_name")[["iv"]].last()["iv"]
            iv = last.combine_first(iv)
            spot = float(g["index_price"].iloc[-1])
        m = meta.loc[pos.index]
        alive = (m["expiry"] > day).to_numpy()
        pos, m = pos[alive], m[alive]
        iv = iv.reindex(pos.index)
        if len(pos) == 0:
            continue
        K = m["strike"].to_numpy()
        T = ((m["expiry"] - day).dt.total_seconds() / (365 * 86400)).to_numpy()
        sig = np.nan_to_num(iv.to_numpy() / 100.0, nan=0.6)
        p = pos.to_numpy()

        gex = p * bs_gamma(spot, K, T, sig) * spot * spot * 0.01      # USD par 1 %
        per_strike = pd.Series(gex).groupby(K).sum()
        total = float(gex.sum())

        # point de bascule gamma : spot hypothetique ou le GEX total change de signe
        grid = spot * grid_rel
        tot_grid = (p[None, :] * bs_gamma(grid[:, None], K[None, :], T[None, :], sig[None, :])
                    * grid[:, None] ** 2 * 0.01).sum(axis=1)
        sgn = np.sign(tot_grid)
        cross = np.where(sgn[1:] * sgn[:-1] < 0)[0]
        flip = np.nan
        if len(cross):
            mids = np.array([grid[i] - tot_grid[i] * (grid[i + 1] - grid[i])
                             / (tot_grid[i + 1] - tot_grid[i]) for i in cross])
            flip = float(mids[np.argmin(np.abs(mids - spot))])

        near = per_strike[(per_strike.index > spot * (1 - BAND))
                          & (per_strike.index < spot * (1 + BAND))]
        # on garde TOUS les strikes proches, classes par |GEX| : les TOP_N premiers
        # sont les vrais niveaux, les suivants servent de TEMOINS (strikes ronds
        # ordinaires, sans grosse position) pour mesurer ce que l'info options
        # apporte reellement par rapport a un simple "chiffre rond".
        top = near.reindex(near.abs().sort_values(ascending=False).index)
        for rank, (k, v) in enumerate(top.items(), 1):
            lv_rows.append((day, float(k), float(v), rank))
        day_rows.append((day, spot, total, flip, float(np.abs(gex).sum()), len(pos)))

    levels = pd.DataFrame(lv_rows, columns=["snap", "level", "gex", "rank"])
    daily = pd.DataFrame(day_rows, columns=["snap", "spot", "gex_total", "flip",
                                            "gex_abs", "n_instr"]).set_index("snap")
    levels.to_parquet(os.path.join(DATA, "levels.parquet"))
    daily.to_parquet(os.path.join(DATA, "levels_daily.parquet"))
    return levels, daily


if __name__ == "__main__":
    lv, dy = build()
    print(f"{len(dy)} jours, {len(lv)} niveaux  ({dy.index.min().date()} -> {dy.index.max().date()})")
    print(f"Jours GEX total > 0 (teneurs long gamma) : {(dy.gex_total > 0).mean() * 100:.0f} %")
    lv = lv[lv["rank"] <= TOP_N]
    print(f"Niveaux retenus (top {TOP_N}) a GEX > 0 : {(lv.gex > 0).mean() * 100:.0f} %")
    print(dy.tail(5).round(0).to_string())
    print(lv.tail(8).round(0).to_string(index=False))
