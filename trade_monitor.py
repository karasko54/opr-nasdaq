#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Surveillant du trade OPR en cours -> Telegram.

Tourne toutes les ~10 min pendant la seance (15h45-21h Paris) et suit le trade
enregistre par opr_live.py (trade_today.json). Envoie une notif pour chaque
etape franchie, une seule fois :
  🎯 entree declenchee   🟢 BE atteint   🏁 TP atteint   🛡️ SL touche
  ⏰ cloture 21h         ⚪ pas de cassure -> ordre annule
Meme bot Telegram que les autres messages.
"""
import os
import sys
import json
import datetime as dt

import pandas as pd

from backtest_opr import NY_TZ, OPEN_END, ENTRY_END, FORCE_CLOSE
from opr_live import send_telegram, load_live

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TRADE_FILE = os.environ.get("OPR_TRADE", "trade_today.json")


def r(x):
    return f"{x:,.1f}".replace(",", " ")


def main():
    if not os.path.exists(TRADE_FILE):
        print("Aucun trade enregistre — rien a surveiller.")
        return
    with open(TRADE_FILE) as f:
        T = json.load(f)
    if T.get("status") != "valid":
        print("Pas de setup valide aujourd'hui — rien a surveiller.")
        return
    if T["flags"]["closed"]:
        print("Trade deja termine — rien a surveiller.")
        return

    today = dt.date.fromisoformat(T["date"])
    now_ny = pd.Timestamp.now(tz=NY_TZ)
    if now_ny.date() != today:
        print("Ce n'est plus le jour du trade — arret.")
        T["flags"]["closed"] = True
        with open(TRADE_FILE, "w") as f:
            json.dump(T, f)
        return

    side = T["side"]; entry = T["entry"]; sl = T["sl"]; tp = T["tp"]
    be_trig = T["be_trig"]; flags = T["flags"]; notified = T["notified"]

    ts_dec = pd.Timestamp(f"{today} {OPEN_END}", tz=NY_TZ)
    ts_entry_end = pd.Timestamp(f"{today} {ENTRY_END}", tz=NY_TZ)
    ts_close = pd.Timestamp(f"{today} {FORCE_CLOSE}", tz=NY_TZ)

    m5all = load_live()
    day5 = m5all[m5all.index.normalize().date == today]
    bars = day5[(day5.index >= ts_dec) & (day5.index <= ts_close)]

    # rejoue le trade depuis le debut avec toutes les bougies connues
    entered = False; be = False; closed = False; reason = None; cur_sl = sl
    for t, b in bars.iterrows():
        if not entered:
            if t > ts_entry_end:
                break
            if (side == 1 and b["high"] >= entry) or (side == -1 and b["low"] <= entry):
                entered = True
        if entered and not closed:
            if be_trig and not be:
                if (side == 1 and b["high"] >= be_trig) or (side == -1 and b["low"] <= be_trig):
                    be = True; cur_sl = entry
            if side == 1:
                if b["low"] <= cur_sl:  closed = True; reason = "SL"; break
                if b["high"] >= tp:     closed = True; reason = "TP"; break
            else:
                if b["high"] >= cur_sl: closed = True; reason = "SL"; break
                if b["low"] <= tp:      closed = True; reason = "TP"; break

    if entered and not closed and now_ny >= ts_close:
        closed = True; reason = "CLOSE"

    events = []
    lbl = T["label"]

    # jamais entre et fenetre finie -> ordre annule
    if not entered and now_ny > ts_entry_end:
        if not notified["closed"]:
            events.append(f"⚪ {lbl} — pas de cassure avant 17h30, ordre annulé. Pas de trade aujourd'hui.")
            notified["closed"] = True
        flags["closed"] = True

    if entered and not notified["entered"]:
        events.append(f"🎯 Entrée déclenchée — {lbl} {T['sens']} à {r(entry)}\n🛡️ SL: {r(sl)}   🏁 TP: {r(tp)}")
        notified["entered"] = True

    if be and not notified["be"]:
        events.append(f"🟢 BE atteint (2R) — {lbl}\nDéplace ton SL à l'entrée ({r(entry)}). Trade sécurisé ✅")
        notified["be"] = True

    if closed and not notified["closed"]:
        if reason == "TP":
            events.append(f"🏁 TP ATTEINT — {lbl} ! +{T['tp_r']:g}R 🎉")
        elif reason == "SL":
            if be:
                events.append(f"🛡️ Sortie au break-even — {lbl}. Trade clôturé à l'entrée (0R).")
            else:
                events.append(f"🛡️ SL touché — {lbl}. Trade clôturé (−1R).")
        else:
            events.append(f"⏰ Clôture 21h — {lbl}. Pense à solder ta position.")
        notified["closed"] = True

    flags.update(entered=entered, be=be, closed=closed or flags["closed"], reason=reason)
    T["flags"] = flags; T["notified"] = notified
    with open(TRADE_FILE, "w") as f:
        json.dump(T, f)

    for msg in events:
        print(msg)
        send_telegram(msg)
    if not events:
        print("Aucune nouvelle etape.")


if __name__ == "__main__":
    main()
