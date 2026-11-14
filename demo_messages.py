#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Demonstration : envoie sur Telegram les messages d'une journee type, dans l'ordre ou ils
arrivent, pour voir comment ils s'affichent. AUCUN de ces messages n'est un vrai signal.

  - envoye UNIQUEMENT en prive (les identifiants de canal / groupe, qui commencent par "-",
    sont ecartes) : les abonnes du canal ne recoivent rien ;
  - chaque message commence par une ligne "TEST n/N" avec l'heure a laquelle il arriverait ;
  - les textes sortent des vraies fonctions du robot quand elles existent (msg_signal,
    fmt_setup, build_message) ; les autres sont recopies de trade_monitor.py / or_asie.py ;
  - les sonneries sont celles du vrai robot (sonore ou silencieux selon le message).

Lancement : workflow "Demo messages" (bouton manuel, ou modification de .demo_declencheur).
Sans TELEGRAM_TOKEN, les messages sont seulement affiches.
"""
import datetime as dt
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# prive uniquement : jamais le canal
os.environ["TELEGRAM_CHAT_ID"] = ",".join(
    c.strip() for c in (os.environ.get("TELEGRAM_CHAT_ID") or "").split(",")
    if c.strip() and not c.strip().startswith("-"))

import macro_news
import opr_live
import or_asie
from backtest_opr import ASSETS
from notify import send_telegram


def r(x):
    return f"{x:,.1f}".replace(",", " ")


def setup(actif, label, sens, entree, sl, tp, ctx):
    opr_live.LABEL = label
    opr_live.TP_R, opr_live.BE_R = ASSETS[actif][0], ASSETS[actif][1]
    return (opr_live.fmt_setup(sens, entree, sl, tp)
            + f"\n\n📊 {ctx}\n⏰ Valable jusqu'a 17h30 — clôture avant 21h")


def macro():
    jour = dt.date(2026, 11, 13)
    ev = [{"impact": "High", "country": "USD", "date": "2026-11-13T14:30:00+01:00", "title": "PPI m/m"}]
    try:
        macro_news.health_lines = lambda today: []          # la demo ne parle pas de l'etat du robot
        return macro_news.build_message(ev, jour)
    except Exception as ex:
        print("build_message indisponible :", ex)
        return "📅 Macro USD — vendredi 13/11 (news qui bougent le NASDAQ)\n🔴 14:30 — PPI m/m ⭐"


# Nuit reelle du 13/11/2026 (niveaux du robot)
SIG = {"t": "03:10", "t_ferm": "2026-11-13T03:15:00+01:00", "side": -1, "e": 3907.509, "sl": 3911.255,
       "tp": 3900.017, "risque": 3.746, "rsi": 55.98, "trop_petit": False}
RA = {"ok": True, "pourquoi": "ATR 2.35 % >= 2.1"}

# (heure d'arrivee, silencieux, texte)
MESSAGES = [
    ("09h00", False, macro()),
    ("15h46", False, setup("nas", "NAS100", "ACHAT", 28385.3, 28236.4, 28906.6,
                           "EMA20 > EMA50 (M5) | Supertrend H1 haussier")),
    ("15h46", False, setup("btc", "BTC", "VENTE", 71181.9, 71839.7, 68879.9,
                           "EMA20 < EMA50 (M5) | Supertrend H1 baissier")),
    ("vers 16h20", False, "🛡️ SL touché — BTC. Trade clôturé (−1R)."),
    ("vers 16h40", False, f"🟢 BE atteint (2R) — NAS100\nDéplace ton SL à l'entrée ({r(28385.3)}). Trade sécurisé ✅"),
    ("vers 18h10", False, "🏁 TP ATTEINT — NAS100 ! +3.5R 🎉"),
    ("23h10", False, ("🌙 OR ASIE — nuit du 13/11 : TRADE POSSIBLE\nATR 2.35 % >= 2.1\n"
                      "Signal possible entre 02h35 et 04h10 (alerte sonore), sortie 05h00.\n"
                      "Sans signal : une seule ligne silencieuse en fin de nuit.")),
    ("03h15", False, or_asie.msg_signal(SIG, RA)),
    ("03h33", True, "❌ OR ASIE 13/11 : SL a 03:32  ->  -1.00 R (avant frais)"),
    ("selon le jour", True, ("Les autres messages possibles, une ligne chacun :\n\n"
                             "✅ OR ASIE 11/11 : TP a 03:19  ->  +2.00 R (avant frais)\n\n"
                             "⏰ OR ASIE : sortie forcee a 05h00 — prepare-toi a fermer la position.\n\n"
                             "😴 OR ASIE — nuit du 14/11 : PAS DE TRADE (regle A)\nATR 1.52 % < 1.7\n\n"
                             "OR ASIE 13/11 : aucune cassure valide avant 04h10 (1 refusee(s) par le RSI). Pas de trade.\n\n"
                             "⚪ NAS100 — pas de cassure avant 17h30, ordre annulé. Pas de trade aujourd'hui.\n\n"
                             "⏰ Clôture 21h — BTC. Pense à solder ta position.")),
]


def main():
    reel = bool(os.environ.get("TELEGRAM_TOKEN"))
    if reel and not os.environ["TELEGRAM_CHAT_ID"]:
        raise SystemExit("Aucun destinataire prive dans TELEGRAM_CHAT_ID : rien n'est envoye.")
    n = len(MESSAGES); rates = 0
    for i, (heure, silencieux, texte) in enumerate(MESSAGES, 1):
        entete = f"🧪 TEST {i}/{n} · {heure} · exemple, PAS un vrai signal"
        if not send_telegram(entete + "\n\n" + texte, silent=silencieux) and reel:
            rates += 1
        if reel:
            time.sleep(2)
    if rates:
        raise SystemExit(f"{rates} message(s) non delivre(s)")


if __name__ == "__main__":
    main()
