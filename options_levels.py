#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Niveaux d'options (GEX) a partir d'une chaine d'options : call wall, put wall,
zero gamma, GEX net.

Utilise par :
  - collect_options_levels.py  (collecte quotidienne QQQ via yfinance)
  - backtest_flow_options.py   (lit le CSV de niveaux produit ici)

Usage en ligne de commande (reconstruire les niveaux depuis des chaines brutes,
par ex. achetees chez un fournisseur) :
    python options_levels.py build DOSSIER_OU_FICHIER [--out options_levels.csv]

Format NORMALISE d'une chaine brute (un fichier par jour ou un seul gros
fichier, .csv ou .csv.gz) :
    date, underlying, spot, expiry, strike, type, oi, iv
      date   = SEANCE pour laquelle le niveau est utilisable (voir plus bas)
      spot   = prix du sous-jacent au moment de la photo (QQQ en general)
      type   = C ou P
      oi     = open interest (contrats)
      iv     = volatilite implicite en fraction (0.22 = 22 %)

POINT DE MARCHE CRITIQUE — l'anti-regard-vers-le-futur.
L'open interest publie par l'OCC le matin du jour J reflete les positions a la
CLOTURE de J-1. Il est donc utilisable des l'ouverture de J. Le champ `date`
doit etre la seance J ou le niveau sert, PAS la date de la cloture qui l'a
produit. Les fournisseurs historiques datent souvent l'OI a J-1 : dans ce cas
il faut decaler d'un jour (option --shift-days 1 de `build`), sinon le
backtest triche.

CONVENTION GEX (« GEX naif ») : on suppose que les dealers sont ACHETEURS des
calls et VENDEURS des puts que le public leur met en face, donc
    GEX = +gamma_call x OI  -  gamma_put x OI.
C'est la convention de place (SqueezeMetrics, SpotGamma...), mais c'est une
HYPOTHESE : elle est fausse les jours ou le public vend massivement des calls
(overwriting) ou achete des puts en couverture. Et l'OI ne voit pas le 0DTE
ouvert et ferme dans la journee, qui represente aujourd'hui > 50 % du volume
d'options SPX/QQQ. Les niveaux sont donc une carte de la veille, pas du flux du
jour — c'est justement pour ca qu'on exige une confirmation par l'order flow.
"""
import argparse
import glob
import math
import os

import numpy as np
import pandas as pd

CONTRACT_MULT = 100          # 1 option action/ETF = 100 titres
MIN_T_YEARS   = 1.0 / (365 * 24)   # plancher 1h : evite gamma infini a l'expiration
MAX_DTE       = 45           # au-dela, le gamma est negligeable et l'IV yfinance douteuse
STRIKE_BAND   = 0.15         # on ne garde que les strikes a +/-15 % du spot
IV_MIN, IV_MAX = 0.03, 3.0   # IV hors de ces bornes = donnee corrompue (frequent sur yfinance)


def _norm_pdf(x):
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(S, K, T, iv, r=0.0):
    """Gamma Black-Scholes (vectorise). S peut etre un scalaire ou un tableau."""
    sqrtT = np.sqrt(T)
    d1 = (np.log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrtT)
    return _norm_pdf(d1) / (S * iv * sqrtT)


def clean_chain(ch):
    """Normalise et filtre une chaine. Retourne un DataFrame propre."""
    ch = ch.copy()
    ch.columns = [c.strip().lower() for c in ch.columns]
    ch["type"] = ch["type"].astype(str).str.upper().str[0]
    for c in ("strike", "oi", "iv", "spot"):
        ch[c] = pd.to_numeric(ch[c], errors="coerce")
    ch = ch.dropna(subset=["strike", "oi", "iv", "spot"])
    ch = ch[(ch["oi"] > 0) & ch["type"].isin(["C", "P"])]
    ch = ch[(ch["iv"] >= IV_MIN) & (ch["iv"] <= IV_MAX)]
    spot = float(ch["spot"].iloc[0]) if len(ch) else np.nan
    ch = ch[(ch["strike"] >= spot * (1 - STRIKE_BAND)) & (ch["strike"] <= spot * (1 + STRIKE_BAND))]
    return ch


def _t_years(ch, asof):
    """Temps jusqu'a l'expiration (16:00 New York le jour d'expiration)."""
    exp = pd.to_datetime(ch["expiry"]).dt.tz_localize(None) + pd.Timedelta(hours=16)
    asof = pd.Timestamp(asof).tz_localize(None) if pd.Timestamp(asof).tzinfo else pd.Timestamp(asof)
    t = (exp - asof).dt.total_seconds() / (365.0 * 24 * 3600)
    return t.clip(lower=MIN_T_YEARS)


def compute_levels(ch, asof=None):
    """
    ch    : chaine NETTOYEE (clean_chain) d'UNE date.
    asof  : instant de la photo (defaut : date 09:30 NY, i.e. avant l'ouverture).
    Retourne un dict de niveaux, en unites du sous-jacent de la chaine (QQQ).
    """
    if len(ch) == 0:
        return None
    spot = float(ch["spot"].iloc[0])
    if asof is None:
        asof = pd.Timestamp(str(ch["date"].iloc[0])[:10]) + pd.Timedelta(hours=9, minutes=30)
    ch = ch.copy()
    ch["T"] = _t_years(ch, asof)
    ch = ch[ch["T"] <= MAX_DTE / 365.0]
    if len(ch) == 0:
        return None
    sign = np.where(ch["type"].values == "C", 1.0, -1.0)
    K, T, iv, oi = ch["strike"].values, ch["T"].values, ch["iv"].values, ch["oi"].values

    # GEX en $ par mouvement de 1 % du sous-jacent
    def gex_at(S):
        g = bs_gamma(S, K, T, iv)
        return sign * g * oi * CONTRACT_MULT * S * S * 0.01

    g_spot = gex_at(spot)
    ch["gex"] = g_spot
    by_k = ch.groupby(["strike", "type"])["gex"].sum().unstack(fill_value=0.0)
    calls = by_k.get("C", pd.Series(0.0, index=by_k.index))
    puts = by_k.get("P", pd.Series(0.0, index=by_k.index))
    net_k = calls + puts

    # Call wall : strike >= spot au plus fort GEX call. Put wall : strike <= spot
    # au plus fort GEX put (en valeur absolue). Restreindre au bon cote du spot
    # est la definition de place : un « mur » au-dessus du prix est une
    # resistance, en dessous un support. Repli sur toute la chaine si vide.
    c_up = calls[calls.index >= spot]
    p_dn = puts[puts.index <= spot]
    call_wall = float((c_up if len(c_up) else calls).idxmax())
    put_wall = float((p_dn if len(p_dn) else puts).idxmin())

    # Zero gamma : prix hypothetique ou le GEX TOTAL change de signe. On
    # re-evalue toute la chaine sur une grille (la vraie definition), au lieu
    # de chercher le changement de signe strike par strike (approximation
    # repandue mais fausse : elle ignore que le gamma de chaque option bouge
    # avec le spot).
    grid = np.linspace(spot * 0.85, spot * 1.15, 301)
    tot = np.array([gex_at(s).sum() for s in grid])
    flips = np.where(np.diff(np.sign(tot)) != 0)[0]
    if len(flips):
        # le changement de signe le plus proche du spot, interpole lineairement
        i = flips[np.argmin(np.abs(grid[flips] - spot))]
        x0, x1, y0, y1 = grid[i], grid[i + 1], tot[i], tot[i + 1]
        zero_gamma = float(x0 - y0 * (x1 - x0) / (y1 - y0))
    else:
        zero_gamma = float("nan")   # GEX de meme signe partout : pas de flip

    return {
        "date": str(ch["date"].iloc[0])[:10],
        "underlying": str(ch["underlying"].iloc[0]) if "underlying" in ch else "",
        "ref_spot": round(spot, 4),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "zero_gamma": round(zero_gamma, 4) if np.isfinite(zero_gamma) else np.nan,
        "net_gex_musd": round(float(g_spot.sum()) / 1e6, 2),
        "max_oi_call": float(ch[ch["type"] == "C"].groupby("strike")["oi"].sum().idxmax()) if (ch["type"] == "C").any() else np.nan,
        "max_oi_put": float(ch[ch["type"] == "P"].groupby("strike")["oi"].sum().idxmax()) if (ch["type"] == "P").any() else np.nan,
        "n_contracts": int(len(ch)),
    }


def _read_any(path):
    return pd.read_csv(path, compression="infer")


def build(paths, shift_days=0):
    frames = [_read_any(p) for p in paths]
    raw = pd.concat(frames, ignore_index=True)
    raw.columns = [c.strip().lower() for c in raw.columns]
    if shift_days:
        # OI date a la cloture J-1 chez le fournisseur -> utilisable a la seance suivante
        d = pd.to_datetime(raw["date"]) + pd.offsets.BDay(shift_days)
        raw["date"] = d.dt.strftime("%Y-%m-%d")
    rows = []
    for d, ch in raw.groupby("date"):
        lv = compute_levels(clean_chain(ch))
        if lv:
            rows.append(lv)
    return pd.DataFrame(rows).sort_values("date")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="chaines brutes -> CSV de niveaux")
    b.add_argument("src", help="fichier ou dossier de chaines (.csv / .csv.gz)")
    b.add_argument("--out", default="options_levels.csv")
    b.add_argument("--shift-days", type=int, default=0,
                   help="decalage en jours ouvres si le fournisseur date l'OI a la cloture J-1")
    a = ap.parse_args()
    if os.path.isdir(a.src):
        paths = sorted(glob.glob(os.path.join(a.src, "*.csv")) + glob.glob(os.path.join(a.src, "*.csv.gz")))
    else:
        paths = [a.src]
    out = build(paths, a.shift_days)
    out.to_csv(a.out, index=False)
    print(f"{len(out)} journees de niveaux -> {a.out}")
