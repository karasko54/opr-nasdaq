#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Recap mensuel du journal -> Telegram.

Envoye le DERNIER DIMANCHE du mois (avec rattrapage le lundi si GitHub a
pris du retard), une seule fois par mois grace a un fichier d'etat.

Couvre tous les trades depuis le recap precedent : meme si un envoi saute,
rien n'est perdu, le suivant reprend la ou on s'etait arrete.

Config : TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import os
import sys
import csv
import datetime as dt

from notify import send_telegram

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

JOURNAL = os.environ.get("OPR_JOURNAL", "journal.csv")
STATE = os.environ.get("RECAP_STATE", ".recap_state")


# ───────────────────────── Date d'envoi ─────────────────────────
def is_recap_day(d):
    """Dernier dimanche du mois — ou le lundi suivant, en rattrapage.

    Les crons GitHub partent regulierement avec plusieurs heures de retard :
    sans ce rattrapage, un reveil repousse apres minuit ferait sauter le
    recap du mois entier.
    """
    if d.weekday() == 6:                                  # dimanche
        return (d + dt.timedelta(days=7)).month != d.month
    if d.weekday() == 0:                                  # lundi de rattrapage
        veille = d - dt.timedelta(days=1)
        return (veille + dt.timedelta(days=7)).month != veille.month
    return False


def recap_key(d):
    """Identifie le recap vise : annee-mois du dernier dimanche concerne.

    Le lundi de rattrapage porte la meme cle que le dimanche de la veille,
    sinon un recap parti dimanche serait renvoye une seconde fois le lundi.
    """
    ref = d if d.weekday() == 6 else d - dt.timedelta(days=1)
    return f"{ref:%Y-%m}"


def last_recap():
    try:
        with open(STATE) as f:
            return dt.date.fromisoformat(f.read().strip())
    except Exception:
        return None


def save_recap(d):
    with open(STATE, "w") as f:
        f.write(str(d))


# ───────────────────────── Lecture du journal ─────────────────────────
def num(row, key, default=None):
    """Lit une colonne numerique, tolerante aux cases vides (trades anciens)."""
    v = (row.get(key) or "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def jour(iso):
    """'2026-08-20' -> '20/08' (format lisible dans le message)."""
    return f"{iso[8:10]}/{iso[5:7]}" if len(iso) >= 10 else iso


def load_rows():
    if not os.path.exists(JOURNAL) or os.path.getsize(JOURNAL) == 0:
        return []
    with open(JOURNAL, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("date") or "").strip()]


# ───────────────────────── Construction du message ─────────────────────────
def bloc_actif(rows):
    lignes = []
    for actif in sorted({r["actif"] for r in rows}):
        d = [r for r in rows if r["actif"] == actif]
        Rs = [num(r, "resultat_R", 0.0) for r in d]
        wr = sum(1 for x in Rs if x > 0) / len(d) * 100
        lignes.append(f"{actif:<7} {len(d):>2} trades  {sum(Rs):+6.2f}R  (WR {wr:.0f}%)")
    return lignes


def bloc_sorties(rows):
    tp = sum(1 for r in rows if r["sortie"] == "TP")
    be = sum(1 for r in rows if r["sortie"] == "SL" and abs(num(r, "resultat_R", 0.0)) < 0.01)
    sl = sum(1 for r in rows if r["sortie"] == "SL" and num(r, "resultat_R", 0.0) < -0.01)
    cl = sum(1 for r in rows if r["sortie"] == "CLOSE")
    return f"🏁 TP {tp} · 🟢 BE {be} · 🛡️ SL {sl} · ⏰ CLOSE {cl}"


def bloc_cloture_21h(rows):
    """Verdict chiffre sur la cloture forcee de 21h.

    Ne porte que sur les trades soldes d'office a 21h ET pour lesquels la
    mesure post-cloture existe (colonnes remplies par trade_monitor).
    """
    m = [r for r in rows if r["sortie"] == "CLOSE" and num(r, "r_22h") is not None]
    if not m:
        return ["⏰ Clôture 21h : pas encore assez de données"]
    n_tp = n_sl = 0
    simule = reel = 0.0
    for r in m:
        r21 = num(r, "resultat_R", 0.0)
        r22 = num(r, "r_22h", r21)
        mfe = num(r, "mfe_apres_R", 0.0)
        mae = num(r, "mae_apres_R", 0.0)
        entree, sl_, tp_ = num(r, "entree"), num(r, "sl"), num(r, "tp")
        # objectif du trade en R, reconstruit depuis les niveaux enregistres
        tp_r = abs(tp_ - entree) / abs(entree - sl_) if entree and sl_ and tp_ else 3.5
        if mfe >= tp_r:
            n_tp += 1
            fin = tp_r
        elif mae <= -1.0:
            n_sl += 1
            fin = -1.0
        else:
            fin = r22
        simule += fin
        reel += r21
    delta = simule - reel
    verdict = ("elle ne change presque rien" if abs(delta) < 0.5
               else f"elle te {'coûte' if delta > 0 else 'fait gagner'} ~{abs(delta):.1f}R")
    return [
        f"⏰ Clôture 21h ({len(m)} trades mesurés)",
        f"   TP touché après : {n_tp}",
        f"   Retour au SL    : {n_sl}",
        f"   En tenant jusqu'à 22h : {delta:+.1f}R",
        f"   → {verdict}",
    ]


def build_message(rows, periode, tous):
    if not rows:
        return (f"📊 Récap — {periode}\n"
                "Aucun trade sur la période.")
    Rs = [num(r, "resultat_R", 0.0) for r in rows]
    tot_all = sum(num(r, "resultat_R", 0.0) for r in tous)
    best = max(rows, key=lambda r: num(r, "resultat_R", 0.0))
    worst = min(rows, key=lambda r: num(r, "resultat_R", 0.0))

    L = [f"📊 Récap — {periode}", ""]
    L += bloc_actif(rows)
    L += ["─" * 26,
          f"Période : {sum(Rs):+.2f}R   ({len(rows)} trades)",
          f"Cumul   : {tot_all:+.2f}R   ({len(tous)} trades)",
          "",
          bloc_sorties(rows),
          f"Meilleur : {best['actif']} {jour(best['date'])} {num(best,'resultat_R',0):+.2f}R",
          f"Pire     : {worst['actif']} {jour(worst['date'])} {num(worst,'resultat_R',0):+.2f}R",
          ""]
    L += bloc_cloture_21h(tous)          # verdict sur TOUT l'historique mesure
    return "\n".join(L)


# ───────────────────────── Main ─────────────────────────
def main():
    today = dt.date.today()
    force = os.environ.get("RECAP_FORCE") == "1"
    if not force and not is_recap_day(today):
        print(f"{today} n'est pas le dernier dimanche du mois — rien a faire.")
        return

    depuis = last_recap()
    if not force and depuis and recap_key(depuis) == recap_key(today):
        print(f"Recap {recap_key(today)} deja envoye le {depuis} — rien a faire.")
        return

    tous = load_rows()
    rows = [r for r in tous if not depuis or r["date"] > str(depuis)]
    debut = min((r["date"] for r in rows), default=str(today))
    periode = (f"{debut[8:10]}/{debut[5:7]} au {today:%d/%m}" if rows
               else f"{today:%d/%m}")

    msg = build_message(rows, periode, tous)
    print(msg)
    if send_telegram(msg):
        if not force:
            save_recap(today)
    else:
        print("!! recap non delivre — il sera renvoye au prochain reveil")


if __name__ == "__main__":
    main()
