#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Garde-fou : alerte Telegram si aucune session OPR n'a ete envoyee aujourd'hui.

POURQUOI CE FICHIER EXISTE
--------------------------
Le robot repose en pratique sur les seuls declenchements fiables : DEUX jobs
cron-job.org, a 09:48 et 09:52 New York (15h48 et 15h52 Paris ; le second est
un rattrapage ajoute le 28/09/2026, l'anti-doublon .opr_state l'empeche de
renvoyer un signal deja parti). Les 7 crons GitHub declares dans opr.yml sont
censes servir de filet, mais la mesure sur 350 executions (20/07 -> 25/09/2026)
donne :

    horaire programme : 13:45 -> 16:15 UTC
    horaire reel      : mediane 18:04 UTC
    84 % arrivent APRES 15:30 UTC, soit apres la fermeture de la fenetre
    d'entree (11:30 New York) -> opr_live.py les rejette, a juste titre.

Autrement dit : si le service cron-job.org tombe, les deux jobs tombent avec
lui, il n'y a plus de signal du tout, et RIEN ne previent. Ce script comble ce
trou.

CE QU'IL FAIT
-------------
Il tourne apres la fermeture de la fenetre d'entree et verifie que chaque actif
a bien inscrit la date du jour dans son fichier d'etat. Si ce n'est pas le cas,
il envoie une alerte Telegram. Il ne corrige rien : il rend la panne visible.

Volontairement tolerant : en cas de doute il se tait plutot que de crier au loup
(week-end, fichier absent au premier lancement).

Config par variables d'environnement :
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   (comme les autres scripts)
    OPR_GARDE  = "fichier:libelle,fichier:libelle"
                 defaut ".opr_state:NASDAQ,.opr_state_btc:BTC"
"""
import os
import sys
import datetime as dt
from zoneinfo import ZoneInfo

from notify import send_telegram

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

NY = ZoneInfo("America/New_York")
DEFAUT = ".opr_state:NASDAQ,.opr_state_btc:BTC"


def a_surveiller():
    brut = os.environ.get("OPR_GARDE", DEFAUT)
    out = []
    for bloc in brut.split(","):
        bloc = bloc.strip()
        if not bloc:
            continue
        fichier, _, libelle = bloc.partition(":")
        out.append((fichier.strip(), (libelle or fichier).strip()))
    return out


def date_etat(fichier):
    """Date inscrite dans le fichier d'etat, ou None s'il est absent/illisible."""
    try:
        with open(fichier) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"!! lecture {fichier} impossible : {e}")
        return None


def main():
    maintenant = dt.datetime.now(NY)
    session = maintenant.date()

    # Week-end : la bourse US est fermee, aucune session n'est attendue.
    if session.weekday() >= 5:
        print(f"{session} : week-end, rien a verifier.")
        return

    # Avant la fermeture de la fenetre d'entree (11:30 NY), il est trop tot
    # pour conclure quoi que ce soit.
    if maintenant.time() < dt.time(11, 30):
        print(f"{maintenant:%H:%M} New York : fenetre d'entree encore ouverte, "
              f"verification prematuree.")
        return

    muets = []
    for fichier, libelle in a_surveiller():
        vu = date_etat(fichier)
        etat = "OK" if vu == str(session) else f"DERNIERE SESSION : {vu or 'aucune'}"
        print(f"  {libelle:8} ({fichier}) -> {etat}")
        if vu != str(session):
            muets.append((libelle, vu))

    if not muets:
        print(f"{session} : toutes les sessions ont ete envoyees.")
        return

    lignes = [f"🔕 *Silence du robot OPR* — {session:%d/%m/%Y}", ""]
    for libelle, vu in muets:
        lignes.append(f"• *{libelle}* : aucune session aujourd'hui "
                      f"(derniere : {vu or 'jamais'})")
    lignes += [
        "",
        "La fenêtre d'entrée (11h30 New York) est fermée et rien n'a été envoyé.",
        "",
        "À vérifier dans cet ordre :",
        "1. cron-job.org — les jobs de 15h48 et 15h52 (Paris) ont-ils tiré ?",
        "2. GitHub Actions — l'exécution est-elle en échec ?",
        "3. yfinance — données indisponibles au moment du run ?",
        "",
        "_Rappel : les crons GitHub arrivent 4 à 6 h en retard et ne servent "
        "pas de filet._",
    ]
    msg = "\n".join(lignes)
    print(msg)
    if not send_telegram(msg):
        print("!! l'alerte Telegram n'est pas partie")
        sys.exit(1)


if __name__ == "__main__":
    main()
