#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Envoi Telegram — source unique pour tous les scripts du bot.

Volontairement sans dependance lourde (juste `requests`) pour pouvoir etre
importe aussi bien par opr_live.py / trade_monitor.py (pandas, yfinance) que
par macro_news.py (qui n'installe que requests).

Regle d'or : une alerte ne doit JAMAIS se perdre en silence.
  - chaque envoi est reessaye plusieurs fois (reseau, 5xx, rate-limit Telegram)
  - la valeur de retour dit clairement si le message est bien parti, pour que
    l'appelant ne marque "notifie" que ce qui a reellement ete delivre.
"""
import os
import time

import requests

TIMEOUT = 30


def _chats():
    raw = os.environ.get("TELEGRAM_CHAT_ID") or ""
    return [c.strip() for c in raw.split(",") if c.strip()]


def _mask(chat):
    return chat[:4] + "..." if len(chat) > 6 else chat


def _send_one(url, chat, text, retries, base_delay):
    """Envoie a UN destinataire, avec reessais. Renvoie True si delivre."""
    dest = _mask(chat)
    err = "?"
    for attempt in range(1, retries + 1):
        wait = None
        try:
            r = requests.post(url, data={"chat_id": chat, "text": text},
                              timeout=TIMEOUT)
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            if r.ok and body.get("ok"):
                print(f"Telegram {dest}: OK" + (f" (tentative {attempt})" if attempt > 1 else ""))
                return True
            err = f"HTTP {r.status_code} {r.text[:150]}"
            # 429 = trop de messages : Telegram indique combien de temps patienter
            if r.status_code == 429:
                wait = body.get("parameters", {}).get("retry_after")
        except Exception as e:
            err = repr(e)
        if attempt < retries:
            delay = wait if wait else base_delay * attempt
            print(f"Telegram {dest}: echec ({err}) — nouvelle tentative dans {delay}s "
                  f"({attempt}/{retries - 1})...")
            time.sleep(delay)
    print(f"Telegram {dest}: ERREUR DEFINITIVE apres {retries} tentatives ({err})")
    return False


def send_telegram(text, retries=4, base_delay=3):
    """Envoie `text` a tous les destinataires configures.

    Renvoie True seulement si TOUS les envois ont abouti : l'appelant peut
    donc s'y fier pour decider de rejouer la notification plus tard.
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    chats = _chats()
    if not token or not chats:
        print("!! TELEGRAM_TOKEN / TELEGRAM_CHAT_ID manquants - message non envoye :")
        print(text)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok_all = True
    for chat in chats:  # ex: "12345,-100987" -> perso + canal
        ok_all = _send_one(url, chat, text, retries, base_delay) and ok_all
    return ok_all
