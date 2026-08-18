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

from notify import send_telegram

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PARIS = ZoneInfo("Europe/Paris")
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OPR_START, OPR_END = dt.time(14, 0), dt.time(17, 0)   # fenetre sensible (Paris)

# Etat ecrit par opr_live.py a chaque session traitee : sert de preuve de vie.
STATE_FILES = [(".opr_state", "NASDAQ"), (".opr_state_btc", "BTC")]
STALE_DAYS = 2          # au-dela : le robot n'a plus tourne, on alerte


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


def business_days_between(start, end):
    """Nombre de jours ouvres (lun-ven) strictement apres `start` jusqu'a `end`."""
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def health_lines(today):
    """Preuve de vie du robot OPR (detecte un arret silencieux).

    Le brief macro part chaque matin : c'est le seul message garanti quotidien.
    On y greffe l'etat du robot pour que l'ABSENCE de signal ne soit jamais
    ambigue ("pas de setup" vs "le robot est mort depuis 3 semaines").
    """
    lines = []
    for path, label in STATE_FILES:
        try:
            with open(path) as f:
                last = dt.date.fromisoformat(f.read().strip())
        except Exception as e:
            lines.append(f"⚠️ Robot {label} : état illisible ({path}) — {e}")
            continue
        gap = business_days_between(last, today)
        if gap > STALE_DAYS:
            lines.append(f"🔴 Robot {label} : plus aucune session depuis le "
                         f"{last:%d/%m} ({gap} jours ouvrés) — vérifie GitHub Actions !")
        else:
            lines.append(f"🤖 Robot {label} : actif (dernière session {last:%d/%m})")
    return lines


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
        lines = [entete, "✅ Aucune grosse news US aujourd'hui."]
    else:
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

    # preuve de vie quotidienne du robot (voir health_lines)
    health = health_lines(today)
    if health:
        lines.append("")
        lines.extend(health)
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
