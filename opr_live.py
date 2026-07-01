#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Robot OPR live -> Telegram (NASDAQ / US Tech 100).

A lancer une fois par jour de bourse, juste apres la cloture de la bougie
d'ouverture US M15 (09:30-09:45 New York = 15h30-15h45 Paris).

Il reproduit EXACTEMENT la logique de backtest_opr.py :
  - range = bougie M15 09:30-09:45 NY (haut/bas), SL = milieu du range
  - filtres figes a 09:45 : EMA20 vs EMA50 (M5) + Supertrend H1 (ATR10, x3)
  - entree STOP au bord du range si les 2 filtres sont dans le meme sens
  - NASDAQ : TP 3.5R, BE 2R, mois exclu = avril
Puis il envoie UNE notification Telegram :
  - soit "Setup valide" avec entree / SL / TP / BE,
  - soit "Pas de trade aujourd'hui" (filtres opposes ou mois exclu).

Config via variables d'environnement (secrets GitHub Actions) :
  TELEGRAM_TOKEN   = token du bot @BotFather
  TELEGRAM_CHAT_ID = ton chat id
  OPR_SYMBOL       = symbole yfinance (defaut ^NDX)
  OPR_TEST         = "1" pour forcer un message de test et sortir
"""
import os
import sys
import datetime as dt

import numpy as np
import pandas as pd
import requests

# console Windows: force l'UTF-8 pour afficher les emojis sans planter
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# reutilise la logique validee du backtest
from backtest_opr import (
    ema, supertrend, resample,
    NY_TZ, OPEN_START, OPEN_END, ST_ATR, ST_FACT,
    EMA_FAST, EMA_SLOW, RISK_PCT, ASSETS,
)

SYMBOL   = os.environ.get("OPR_SYMBOL", "^NDX")
ASSET    = os.environ.get("OPR_ASSET", "nas")
TP_R, BE_R, SKIP_MONTHS = ASSETS[ASSET]
LABEL    = "NAS100"


# ───────────────────────── Telegram ─────────────────────────
def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("!! TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants — message non envoye :\n")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat, "text": text}, timeout=30)
    ok = r.ok and r.json().get("ok")
    print("Telegram:", "OK" if ok else f"ERREUR {r.status_code} {r.text[:200]}")
    return ok


def fmt_setup(sens, entry, sl, tp):
    r = lambda x: f"{x:,.1f}".replace(",", " ")
    be_txt = f"{BE_R:g}R" if BE_R else "Non"
    return (
        f"🚨 OPR {sens} — {LABEL}\n"
        f"🎯 Entree STOP: {r(entry)}\n"
        f"🛡️ SL: {r(sl)}\n"
        f"🏁 TP: {r(tp)} ({TP_R:g}R)\n"
        f"⚖️ BE: {be_txt}\n"
        f"💰 Risque 0.25%"
    )


# ───────────────────────── Donnees ─────────────────────────
def load_live():
    """Recupere le 5m des derniers jours via yfinance et le passe en heure NY."""
    import yfinance as yf
    d = yf.download(SYMBOL, period="7d", interval="5m",
                    progress=False, auto_adjust=False)
    if d is None or len(d) == 0:
        raise RuntimeError(f"Aucune donnee pour {SYMBOL}")
    # aplati les colonnes multi-index eventuelles
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    d = d.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    if d.index.tz is None:
        d.index = d.index.tz_localize("UTC")
    d.index = d.index.tz_convert(NY_TZ)
    return d


def analyse(m5all, today):
    """Applique la strategie pour la date NY 'today'. Renvoie un dict."""
    if today.month in SKIP_MONTHS:
        return {"trade": False, "reason": f"mois exclu ({today:%B})"}

    ts_decision = pd.Timestamp(f"{today} {OPEN_END}", tz=NY_TZ)

    # bougie d'ouverture 09:30-09:45 (exclut 09:45)
    day5 = m5all[m5all.index.normalize().date == today]
    op = day5.between_time(OPEN_START, OPEN_END, inclusive="left")
    if len(op) == 0:
        return {"trade": None, "reason": "range d'ouverture pas encore dispo"}
    # completude : il faut au moins une bougie a/apres 09:45 pour confirmer
    # que la bougie d'ouverture est cloturee (protege des donnees en retard)
    post = day5[day5.index.time >= dt.time(9, 45)]
    if len(post) == 0:
        return {"trade": None,
                "reason": "bougie d'ouverture pas encore cloturee (donnees en retard)"}
    orH, orL = float(op["high"].max()), float(op["low"].min())
    orMid = (orH + orL) / 2
    if not np.isfinite(orH) or orH <= orL:
        return {"trade": None, "reason": "range invalide"}

    # filtres figes a 09:45
    m5 = m5all.copy()
    m5["ema_f"] = ema(m5["close"], EMA_FAST)
    m5["ema_s"] = ema(m5["close"], EMA_SLOW)
    h1 = resample(m5all, "60min")
    h1["st"] = supertrend(h1, ST_ATR, ST_FACT)

    m5d = m5[(m5.index.normalize().date == today) & (m5.index <= ts_decision)]
    h1d = h1[h1.index <= ts_decision]
    if len(m5d) == 0 or len(h1d) == 0:
        return {"trade": None, "reason": "indicateurs pas encore calculables"}

    ef, es = float(m5d["ema_f"].iloc[-1]), float(m5d["ema_s"].iloc[-1])
    st = float(h1d["st"].iloc[-1])

    long_ok  = (ef > es) and (st > 0)
    short_ok = (ef < es) and (st < 0)

    ctx = (f"EMA20 {'>' if ef > es else '<'} EMA50 (M5) | "
           f"Supertrend H1 {'haussier' if st > 0 else 'baissier'}")

    if not (long_ok or short_ok):
        return {"trade": False, "reason": "filtres opposes", "ctx": ctx,
                "orH": orH, "orL": orL}

    if long_ok:
        entry, sl = orH, orMid
        tp = entry + (entry - sl) * TP_R
        sens = "ACHAT"
    else:
        entry, sl = orL, orMid
        tp = entry - (sl - entry) * TP_R
        sens = "VENTE"

    return {"trade": True, "sens": sens, "entry": entry, "sl": sl, "tp": tp,
            "orH": orH, "orL": orL, "ctx": ctx}


# ───────────────────────── Main ─────────────────────────
STATE_FILE = os.environ.get("OPR_STATE", ".opr_state")
TRADE_FILE = os.environ.get("OPR_TRADE", "trade_today.json")


def write_trade(session, res):
    """Enregistre le trade du jour pour le surveillant (BE/TP/SL)."""
    import json
    if res.get("trade") is True:
        entry, sl, tp = res["entry"], res["sl"], res["tp"]
        side = 1 if res["sens"] == "ACHAT" else -1
        risk = abs(entry - sl)
        be_trig = (entry + risk * BE_R * side) if BE_R else None
        data = {
            "date": str(session), "status": "valid", "label": LABEL,
            "sens": res["sens"], "side": side,
            "entry": entry, "sl": sl, "tp": tp, "risk": risk,
            "be_trig": be_trig, "be_r": BE_R, "tp_r": TP_R,
            "flags": {"entered": False, "be": False, "closed": False, "reason": None},
            "notified": {"entered": False, "be": False, "closed": False},
        }
    else:
        data = {"date": str(session), "status": "none"}
    with open(TRADE_FILE, "w") as f:
        json.dump(data, f)


def already_sent(session_date):
    try:
        with open(STATE_FILE) as f:
            return f.read().strip() == str(session_date)
    except FileNotFoundError:
        return False


def mark_sent(session_date):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(str(session_date))
    except Exception as e:
        print("!! ecriture etat impossible:", e)


def main():
    if os.environ.get("OPR_TEST") == "1":
        msg = ("✅ TEST robot OPR — le canal Telegram fonctionne.\n\n"
               + fmt_setup("ACHAT", 30510.0, 30381.0, 30960.0)
               + "\n\n(exemple — donnees fictives)")
        send_telegram(msg)
        return

    # garde-fou horaire : on n'agit qu'entre 09:45 et 11:30 heure de New York
    # (gere automatiquement l'heure d'ete/hiver, quel que soit le cron UTC)
    now_ny = pd.Timestamp.now(tz=NY_TZ)
    if not (dt.time(9, 45) <= now_ny.time() <= dt.time(11, 30)):
        print(f"Hors fenetre NY ({now_ny:%H:%M} NY) — rien a faire.")
        return
    if now_ny.weekday() >= 5:
        print("Week-end — rien a faire.")
        return

    session = now_ny.date()
    if already_sent(session):
        print(f"Deja envoye pour la session {session} — rien a faire.")
        return

    m5all = load_live()

    # --- Diagnostic temporaire : mesure le retard des donnees Yahoo ---
    if os.environ.get("OPR_DIAG") == "1":
        last = m5all.index[-1].tz_convert("UTC")
        nowu = pd.Timestamp.now(tz="UTC")
        lag = (nowu - last).total_seconds() / 60
        send_telegram(
            "🔧 Diagnostic données ^NDX (cloud, PC éteint)\n"
            f"Dernière bougie : {last:%H:%M} UTC\n"
            f"Maintenant : {nowu:%H:%M} UTC\n"
            f"➡️ Retard ≈ {lag:.0f} min"
        )

    today = session
    res = analyse(m5all, today)

    if res["trade"] is None:
        # donnees pas pretes : on n'envoie rien (un run de secours reessaiera)
        print("Donnees insuffisantes:", res["reason"])
        return

    if res["trade"] is True:
        msg = fmt_setup(res["sens"], res["entry"], res["sl"], res["tp"])
        msg += f"\n\n📊 {res['ctx']}\n⏰ Valable jusqu'a 17h30 — clôture avant 21h"
        print(msg)
        sent = send_telegram(msg)
    else:
        # Option A : aucun message les jours sans setup (silence total)
        print(f"Pas de setup ({res['reason']}) — silence, aucun message envoye.")
        sent = True

    if sent:
        mark_sent(session)
        write_trade(session, res)


if __name__ == "__main__":
    main()
