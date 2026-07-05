#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Brief macro-economique -> Telegram.

Envoie chaque matin (lun-ven) les grosses news US (USD, impact High) du jour
susceptibles de faire bouger le NASDAQ : NFP, CPI, FOMC, discours Fed, ISM,
PIB, ventes au detail, inscriptions chomage, etc.
Les news qui tombent dans la fenetre OPR (14h-17h Paris) sont marquees ⭐.

Source : calendrier ForexFactory (gratuit, JSON).
Config via variables d'environnement :
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import sys
import time
import datetime as dt
from zoneinfo import ZoneInfo

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PARIS = ZoneInfo("Europe/Paris")
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OPR_START, OPR_END = dt.time(14, 0), dt.time(17, 0)   # fenetre sensible (Paris)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chats = [c.strip() for c in (os.environ.get("TELEGRAM_CHAT_ID") or "").split(",") if c.strip()]
    if not token or not chats:
        print("!! TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants - message non envoye :")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok_all = True
    for chat in chats:  # ex: "12345,-100987" -> perso + canal
        r = requests.post(url, data={"chat_id": chat, "text": text}, timeout=30)
        ok = r.ok and r.json().get("ok")
        dest = chat[:4] + "..." if len(chat) > 6 else chat
        print(f"Telegram {dest}:", "OK" if ok else f"ERREUR {r.status_code} {r.text[:200]}")
        ok_all = ok_all and ok
    return ok_all


def fetch_events():
    """Recupere le calendrier avec tentatives espacees (gere le 429)."""
    last = None
    delays = [0, 15, 45, 90]   # backoff progressif
    for d in delays:
        if d:
            time.sleep(d)
        try:
            r = requests.get(FEED, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
    raise RuntimeError(f"Calendrier indisponible apres plusieurs essais: {last}")


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def build_message(events, today):
    rows = []
    for e in events:
        if e.get("impact") != "High" or e.get("country") != "USD":
            continue
        try:
            t = dt.datetime.fromisoformat(e["date"]).astimezone(PARIS)
        except Exception:
            continue
        if t.date() == today:
            rows.append((t, e.get("title", "?")))
    rows.sort(key=lambda r: r[0])

    entete = f"📅 Macro USD — {JOURS[today.weekday()]} {today:%d/%m}"
    if not rows:
        return f"{entete}\n✅ Aucune grosse news US aujourd'hui."

    lines = [f"{entete} (news qui bougent le NASDAQ)"]
    has_star = False
    for t, title in rows:
        star = ""
        if OPR_START <= t.time() <= OPR_END:
            star = " ⭐"
            has_star = True
        lines.append(f"🔴 {t:%H:%M} — {title}{star}")
    if has_star:
        lines.append("⚠️ News dans la fenêtre OPR (14h–17h) → prudence sur le trade.")
    return "\n".join(lines)


def main():
    now_paris = dt.datetime.now(PARIS)
    if now_paris.weekday() >= 5:
        print("Week-end — pas de brief.")
        return
    events = fetch_events()
    msg = build_message(events, now_paris.date())
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
