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
import csv
import json
import datetime as dt

import pandas as pd

from backtest_opr import NY_TZ, OPEN_END, ENTRY_END, FORCE_CLOSE
from notify import send_telegram
from opr_live import load_live, DataUnavailable

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TRADE_FILE   = os.environ.get("OPR_TRADE", "trade_today.json")
JOURNAL_FILE = os.environ.get("OPR_JOURNAL", "journal.csv")


def r(x):
    return f"{x:,.1f}".replace(",", " ")


# Colonnes du journal. mfe_R / mae_R = excursions maximales atteintes pendant
# le trade (en R) :
#   mfe_R = plus loin alle EN NOTRE FAVEUR  -> dit si le TP est trop ambitieux
#           (ex: beaucoup de trades a 3.0R alors que le TP est a 3.5R)
#   mae_R = pire creux traverse (negatif)   -> dit si le SL est trop serre
#           (ex: des gagnants qui frolent -0.9R avant de partir)
#
# r_22h / mfe_apres_R / mae_apres_R = ce qui s'est passe APRES la cloture
# forcee de 21h, jusqu'a la cloture cash (22h Paris). Renseignes uniquement
# pour les trades soldes d'office (sortie CLOSE) : ils disent si cette
# cloture a coute des TP ou protege de retours au SL.
FIELDS = ["date", "actif", "sens", "entree", "sl", "tp", "range",
          "resultat_R", "mfe_R", "mae_R",
          "r_22h", "mfe_apres_R", "mae_apres_R",
          "sortie", "contexte", "plan_respecte"]

# Cloture du cash US = 16:00 NY (~22h Paris) : derniere cotation de l'indice.
CASH_CLOSE = "16:00"


def ensure_columns(path, fields):
    """Migre un journal existant vers le format courant (colonnes manquantes).

    Evite d'ecrire des lignes a 13 colonnes sous un entete qui n'en declare
    que 11, ce qui corromprait le CSV. Les trades anterieurs gardent une
    valeur vide : leur MFE/MAE n'a jamais ete mesure.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows or set(fields) <= set(rows[0].keys()):
        return                                   # deja au bon format
    extras = [c for c in rows[0].keys() if c not in fields]   # rien ne se perd
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + extras)
        w.writeheader()
        for old in rows:
            w.writerow({k: old.get(k, "") for k in fields + extras})
    print(f"Journal: migre vers le nouveau format ({len(rows)} lignes conservees)")


def journal_update(date, actif, **valeurs):
    """Complete une ligne deja ecrite (identifiee par date + actif)."""
    if not os.path.exists(JOURNAL_FILE) or os.path.getsize(JOURNAL_FILE) == 0:
        return False
    ensure_columns(JOURNAL_FILE, FIELDS)
    with open(JOURNAL_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return False
    touche = False
    for row in rows:
        if row.get("date") == date and row.get("actif") == actif:
            row.update({k: v for k, v in valeurs.items() if k in row})
            touche = True
    if not touche:
        return False
    with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return True


def journal_append(T, result_R, reason, mfe=None, mae=None):
    """Ajoute une ligne au journal des trades (cree l'entete si besoin)."""
    orH, orL = T.get("orH"), T.get("orL")
    rng = round(orH - orL, 1) if (orH is not None and orL is not None) else ""
    row = {
        "date": T["date"], "actif": T["label"], "sens": T["sens"],
        "entree": round(T["entry"], 1), "sl": round(T["sl"], 1), "tp": round(T["tp"], 1),
        "range": rng, "resultat_R": round(result_R, 2),
        "mfe_R": "" if mfe is None else round(mfe, 2),
        "mae_R": "" if mae is None else round(mae, 2),
        "sortie": reason,
        "contexte": T.get("ctx", ""), "plan_respecte": "oui",
    }
    ensure_columns(JOURNAL_FILE, FIELDS)
    new_file = not os.path.exists(JOURNAL_FILE) or os.path.getsize(JOURNAL_FILE) == 0
    with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    extra = "" if mfe is None else f"  MFE {mfe:+.2f}R / MAE {mae:+.2f}R"
    print(f"Journal: ligne ajoutee ({T['date']} {reason} {result_R:+.2f}R{extra})")


def capture_post_close(T):
    """Mesure ce qu'aurait donne le trade s'il avait couru jusqu'a 22h.

    Ne concerne que les trades soldes d'office a 21h (sortie CLOSE) : entre
    la cloture forcee (15:00 NY) et la cloture cash (16:00 NY) on releve le
    resultat final et les extremes atteints. C'est la donnee qui permet de
    dire, chiffres en main, si la cloture 21h coute des TP ou protege.
    """
    flags = T["flags"]
    if flags.get("post_close"):
        return
    if flags.get("reason") != "CLOSE":
        flags["post_close"] = True          # sorti au TP/SL : rien a mesurer
        return

    today = dt.date.fromisoformat(T["date"])
    ts_close = pd.Timestamp(f"{today} {FORCE_CLOSE}", tz=NY_TZ)
    ts_cash = pd.Timestamp(f"{today} {CASH_CLOSE}", tz=NY_TZ)
    if pd.Timestamp.now(tz=NY_TZ) < ts_cash:
        return                               # trop tot, on repassera

    try:
        m5all = load_live()
    except DataUnavailable as e:
        print(f"Donnees indisponibles — mesure post-cloture reportee ({e})")
        return

    w = m5all[(m5all.index > ts_close) & (m5all.index <= ts_cash)]
    if len(w) == 0:
        print("Aucune bougie entre 21h et 22h — mesure post-cloture abandonnee")
        flags["post_close"] = True
        return

    side, entry, risk = T["side"], T["entry"], T["risk"]
    r22 = (float(w["close"].iloc[-1]) - entry) / risk * side
    if side == 1:
        mfe = (float(w["high"].max()) - entry) / risk
        mae = (float(w["low"].min()) - entry) / risk
    else:
        mfe = (entry - float(w["low"].min())) / risk
        mae = (entry - float(w["high"].max())) / risk

    journal_update(T["date"], T["label"], r_22h=round(r22, 2),
                   mfe_apres_R=round(mfe, 2), mae_apres_R=round(mae, 2))
    flags["post_close"] = True
    print(f"Post-cloture 22h : R={r22:+.2f}  MFE={mfe:+.2f}  MAE={mae:+.2f}")


def main():
    if not os.path.exists(TRADE_FILE):
        print("Aucun trade enregistre — rien a surveiller.")
        return
    with open(TRADE_FILE) as f:
        T = json.load(f)
    if T.get("status") != "valid":
        print("Pas de setup valide aujourd'hui — rien a surveiller.")
        return
    # On ne s'arrete que si le trade est termine ET que la notif de cloture est
    # bien partie : sinon on repasse pour rejouer l'envoi rate.
    if T["flags"]["closed"] and T["notified"].get("closed"):
        if T["flags"].get("post_close"):
            print("Trade termine, notifie et mesure — rien a surveiller.")
            return
        capture_post_close(T)       # il reste la mesure 21h -> 22h a relever
        with open(TRADE_FILE, "w") as f:
            json.dump(T, f)
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
    risk = T["risk"]
    be_trig = T["be_trig"]; flags = T["flags"]; notified = T["notified"]

    ts_dec = pd.Timestamp(f"{today} {OPEN_END}", tz=NY_TZ)
    ts_entry_end = pd.Timestamp(f"{today} {ENTRY_END}", tz=NY_TZ)
    ts_close = pd.Timestamp(f"{today} {FORCE_CLOSE}", tz=NY_TZ)

    try:
        m5all = load_live()
    except DataUnavailable as e:
        # Panne externe passagere (Yahoo en rate-limit) : on saute ce cycle
        # SANS crasher, donc sans declencher de fausse alerte Telegram.
        # Le monitor rejoue tout le trade depuis le debut au prochain passage
        # (~10 min) : aucune etape n'est perdue.
        print(f"Donnees indisponibles — cycle ignore ({e})")
        return
    day5 = m5all[m5all.index.normalize().date == today]
    bars = day5[(day5.index >= ts_dec) & (day5.index <= ts_close)]

    # rejoue le trade depuis le debut avec toutes les bougies connues
    entered = False; be = False; closed = False; reason = None; cur_sl = sl
    mfe = mae = 0.0          # excursions max, en R, depuis l'entree
    for t, b in bars.iterrows():
        if not entered:
            if t > ts_entry_end:
                break
            if (side == 1 and b["high"] >= entry) or (side == -1 and b["low"] <= entry):
                entered = True
        if entered and not closed:
            # excursions : mesurees sur les extremes M5, bougie de sortie incluse
            if side == 1:
                mfe = max(mfe, (b["high"] - entry) / risk)
                mae = min(mae, (b["low"] - entry) / risk)
            else:
                mfe = max(mfe, (entry - b["low"]) / risk)
                mae = min(mae, (entry - b["high"]) / risk)
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

    # (cle de notification, message) — la cle n'est cochee qu'apres envoi reussi
    events = []
    lbl = T["label"]

    # jamais entre et fenetre finie -> ordre annule
    if not entered and now_ny > ts_entry_end:
        if not notified["closed"]:
            events.append(("closed", f"⚪ {lbl} — pas de cassure avant 17h30, ordre annulé. Pas de trade aujourd'hui."))
        flags["closed"] = True

    if entered and not notified["entered"]:
        events.append(("entered", f"🎯 Entrée déclenchée — {lbl} {T['sens']} à {r(entry)}\n🛡️ SL: {r(sl)}   🏁 TP: {r(tp)}"))

    if be and not notified["be"]:
        events.append(("be", f"🟢 BE atteint (2R) — {lbl}\nDéplace ton SL à l'entrée ({r(entry)}). Trade sécurisé ✅"))

    if closed and not notified["closed"]:
        if reason == "TP":
            msg = f"🏁 TP ATTEINT — {lbl} ! +{T['tp_r']:g}R 🎉"
        elif reason == "SL":
            if be:
                msg = f"🛡️ Sortie au break-even — {lbl}. Trade clôturé à l'entrée (0R)."
            else:
                msg = f"🛡️ SL touché — {lbl}. Trade clôturé (−1R)."
        else:
            msg = f"⏰ Clôture 21h — {lbl}. Pense à solder ta position."
        events.append(("closed", msg))

    # journal automatique : une ligne des qu'un vrai trade (entree declenchee) se termine
    if closed and entered and not flags.get("journaled", False):
        if reason == "TP":
            result_R = float(T["tp_r"])
        elif reason == "SL":
            result_R = 0.0 if be else -1.0
        else:  # CLOSE 21h
            last = float(bars["close"].iloc[-1]) if len(bars) else entry
            result_R = ((last - entry) if side == 1 else (entry - last)) / T["risk"]
        journal_append(T, result_R, reason, mfe, mae)
        flags["journaled"] = True

    # ENVOI D'ABORD, marquage ENSUITE : une notif n'est consideree delivree que
    # si Telegram l'a acceptee. Sinon elle reste "a envoyer" et sera rejouee au
    # prochain cycle (~10 min) — une alerte ne peut plus se perdre en silence.
    try:
        for key, msg in events:
            print(msg)
            if send_telegram(msg):
                notified[key] = True
            else:
                print(f"!! notif '{key}' NON delivree — nouvel essai au prochain cycle")
        if not events:
            print("Aucune nouvelle etape.")
    finally:
        # l'etat est sauvegarde quoi qu'il arrive (evite de rejouer le journal)
        flags.update(entered=entered, be=be, closed=closed or flags["closed"],
                     reason=reason, journaled=flags.get("journaled", False))
        T["flags"] = flags; T["notified"] = notified
        with open(TRADE_FILE, "w") as f:
            json.dump(T, f)


if __name__ == "__main__":
    main()
