#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPR ASIE OR (XAUUSD) + REGLE A -> Telegram.   (ajoute le 12/11/2026)

Regles = celles du backtest (opr_asie_marches.py + regle A), heure de Paris :
  OPR      : bougies M5 02h25 et 02h30 (haut / bas)
  Entree   : 1re cloture M5 hors de l'OPR entre 02h35 et 04h10
  RSI(7) M5: achat refuse si RSI > 95, vente refusee si RSI < 35 ;
             apres un refus il faut une cloture de retour dans l'OPR pour re-armer
  SL       : extremite de la bougie de cassure ; TP 2R ; sortie forcee 05h00
  Tailles  : OPR <= 0,25 ATR ; OPR >= 10 couts et stop >= 6,67 couts (cout = 0,0075 % du prix)
  REGLE A  : ATR% de la veille >= 2,1 -> trade ; 1,7-2,1 -> trade seulement si le range
             de la derniere seance complete >= 1 ATR ; < 1,7 -> pas de trade

Modes :
  python or_asie.py verdict   -> le soir (~23h10) : "cette nuit trade possible / pas de trade"
  python or_asie.py nuit      -> lance vers 02h15 : surveille jusqu'a 05h02 et envoie
                                 l'OPR (silencieux), le SIGNAL (sonore), puis TP / SL / sortie 05h00
  python or_asie.py rejouer AAAA-MM-JJ -> rejoue une nuit passee sans rien envoyer (controle)

Donnees : Dukascopy XAU/USD bid (temps reel, < 1 min de retard, meme source que le backtest).
Si les donnees manquent, le robot le DIT sur Telegram plutot que de se taire.
"""
import os
import sys
import json
import time
import random
import string
import datetime as dt

import numpy as np
import pandas as pd
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from notify import send_telegram

TZ = "Europe/Paris"
RSI_LEN, RSI_BAS, RSI_HAUT = 7, 35.0, 95.0
COUT = 0.000075
OPR_MAX_ATR, OPR_MIN_COUT, STOP_MIN_COUT = 0.25, 10.0, 6.67
ATR_MIN, ATR_FORT, VEILLE_MIN = 1.7, 2.1, 1.0
CAPITAL = float(os.environ.get("OR_CAPITAL", "10000"))      # compte FTMO 10K
RISQUES = (1.0, 0.5)                                          # % : normal / garde-fou
ETAT = ".or_asie_state"
JOURNAL = "journal_or.csv"


# ── Donnees Dukascopy ───────────────────────────────────────────────────────
def _dk(interval, depuis_ms):
    jsonp = "_callbacks____" + "".join(random.choices(string.ascii_letters + string.digits, k=9))
    p = {"path": "chart/json3", "splits": "true", "stocks": "true", "time_direction": "N",
         "jsonp": jsonp, "last_update": str(int(depuis_ms)), "offer_side": "B",
         "instrument": "XAU/USD", "interval": interval}
    h = {"User-Agent": "Mozilla/5.0", "Referer": "https://freeserv.dukascopy.com/2.0/"}
    r = requests.get("https://freeserv.dukascopy.com/2.0/index.php", params=p, headers=h, timeout=30)
    r.raise_for_status()
    return json.loads(r.text.removeprefix(jsonp + "(").removesuffix(");"))


def barres(interval, jours):
    """OHLC indexees en heure de Paris. Plusieurs pages si besoin."""
    cur = int((time.time() - jours * 86400) * 1000)
    rows = []
    for _ in range(20):
        r = None
        for essai in range(4):
            try:
                r = _dk(interval, cur); break
            except Exception as e:
                print(f"Dukascopy {interval} echec {essai + 1}/4 : {e!r}"); time.sleep(5 * (essai + 1))
        if r is None:
            raise RuntimeError("Dukascopy injoignable")
        new = [x for x in r if not rows or x[0] > rows[-1][0]]
        if not new:
            break
        rows += new
        if len(r) < 1000:
            break
        cur = rows[-1][0]
    df = pd.DataFrame([x[:5] for x in rows], columns=["t", "open", "high", "low", "close"])
    df = df.set_index(pd.DatetimeIndex(pd.to_datetime(df.pop("t"), unit="ms", utc=True)).tz_convert(TZ))
    return df[~df.index.duplicated()].sort_index().astype(float)


# ── Calculs (identiques au backtest) ────────────────────────────────────────
def rsi_wilder(s, n):
    d = s.diff(); a = 1.0 / n
    mu = d.clip(lower=0).ewm(alpha=a, adjust=False).mean()
    md = (-d).clip(lower=0).ewm(alpha=a, adjust=False).mean()
    return 100 - 100 / (1 + mu / md.replace(0, np.nan))


def regle_a(h1, jour):
    """Etat de la regle A pour la nuit du `jour` (minuit Paris) a partir de barres H1."""
    h1 = h1[h1.index < jour]
    g = h1.groupby(h1.index.normalize())
    d = pd.DataFrame({"high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                      "n": g["close"].size()}).dropna()
    pc = d["close"].shift()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    atr_pct = float(atr.iloc[-1] / d["close"].iloc[-1] * 100)       # journalier().shift(1) du backtest
    pleine = d[d["n"] >= 5]                                          # seance >= 5 h (exclut le dimanche soir)
    veille = float((pleine["high"].iloc[-1] - pleine["low"].iloc[-1]) / atr.iloc[-1])
    if atr_pct >= ATR_FORT:
        ok, pourquoi = True, f"ATR {atr_pct:.2f} % >= {ATR_FORT}"
    elif atr_pct >= ATR_MIN:
        ok = veille >= VEILLE_MIN
        pourquoi = f"ATR {atr_pct:.2f} % (zone grise) et veille {veille:.2f} ATR " + (">= 1" if ok else "< 1")
    else:
        ok, pourquoi = False, f"ATR {atr_pct:.2f} % < {ATR_MIN}"
    return {"ok": ok, "atr": float(atr.iloc[-1]), "atr_pct": atr_pct, "veille": veille,
            "pourquoi": pourquoi, "seance": str(pleine.index[-1].date())}


def m5_de(m1):
    m5 = m1.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    m5["rsi"] = rsi_wilder(m5["close"], RSI_LEN)
    return m5


def chercher_signal(m5, jour, atr, maintenant):
    """Rejoue la nuit sur les bougies M5 COMPLETES (cloturees avant `maintenant`)."""
    m5 = m5[m5.index + pd.Timedelta(minutes=5) <= maintenant]
    nuit = m5[(m5.index >= jour) & (m5.index < jour + pd.Timedelta(days=1))]
    opr = nuit.between_time("02:25", "02:35", inclusive="left")
    res = {"opr": None, "refus": [], "signal": None, "fin_fenetre": False}
    if len(opr) < 2:
        return res
    hi, lo = float(opr["high"].max()), float(opr["low"].min())
    cout = COUT * float(opr["close"].iloc[-1])
    res["opr"] = {"haut": hi, "bas": lo}
    if hi - lo > OPR_MAX_ATR * atr:
        res["opr"]["invalide"] = f"OPR {hi - lo:.2f} $ > 0,25 ATR ({OPR_MAX_ATR * atr:.2f} $)"; return res
    if hi - lo < OPR_MIN_COUT * cout:
        res["opr"]["invalide"] = f"OPR {hi - lo:.2f} $ trop petite (< {OPR_MIN_COUT * cout:.2f} $)"; return res
    win = nuit.between_time("02:35", "04:10", inclusive="left")
    armee = True
    for t, b in win.iterrows():
        c = float(b["close"])
        if not armee:
            if lo <= c <= hi:
                armee = True
            continue
        haut, bas = c > hi, c < lo
        if not (haut or bas):
            continue
        r = float(b["rsi"])
        if (not np.isfinite(r)) or (haut and r > RSI_HAUT) or (bas and r < RSI_BAS):
            res["refus"].append({"t": t.strftime("%H:%M"), "sens": "achat" if haut else "vente", "rsi": r})
            armee = False; continue
        sl = float(b["low"]) if haut else float(b["high"])
        risque = abs(c - sl)
        if risque <= 0:
            armee = False; continue
        side = 1 if haut else -1
        res["signal"] = {"t": t.strftime("%H:%M"), "t_ferm": (t + pd.Timedelta(minutes=5)).isoformat(),
                         "side": side, "e": c, "sl": sl, "tp": c + side * 2 * risque, "risque": risque,
                         "rsi": r, "trop_petit": risque < STOP_MIN_COUT * cout}
        return res
    res["fin_fenetre"] = maintenant >= jour + pd.Timedelta(hours=4, minutes=10)
    return res


def suivre(m1, sig, maintenant, jour):
    """TP / SL / sortie 05h00 (meme ordre de priorite que le backtest : SL d'abord)."""
    deb = pd.Timestamp(sig["t_ferm"])
    b = m1[(m1.index >= deb) & (m1.index <= jour + pd.Timedelta(hours=5)) & (m1.index + pd.Timedelta(minutes=1) <= maintenant)]
    s = sig["side"]
    for t, x in b.iterrows():
        if (s == 1 and x["low"] <= sig["sl"]) or (s == -1 and x["high"] >= sig["sl"]):
            return "SL", -1.0, t
        if (s == 1 and x["high"] >= sig["tp"]) or (s == -1 and x["low"] <= sig["tp"]):
            return "TP", 2.0, t
    if maintenant >= jour + pd.Timedelta(hours=5, minutes=1) and len(b):
        return "05h00", s * (float(b["close"].iloc[-1]) - sig["e"]) / sig["risque"], b.index[-1]
    return None, None, None


# ── Messages ────────────────────────────────────────────────────────────────
def lots(risque_px, pct):
    return max(np.floor(CAPITAL * pct / 100 / (risque_px * 100) * 100) / 100, 0.0)


def msg_signal(sig, ra):
    sens = "🟢 ACHAT" if sig["side"] == 1 else "🔴 VENTE"
    l1, l05 = lots(sig["risque"], RISQUES[0]), lots(sig["risque"], RISQUES[1])
    txt = (f"🚨 OR ASIE — {sens} XAUUSD\n"
           f"Cassure cloturee a {sig['t_ferm'][11:16]}\n\n"
           f"Entree : {sig['e']:.2f} (au marche)\n"
           f"SL : {sig['sl']:.2f}  ({sig['risque']:.2f} $)\n"
           f"TP 2R : {sig['tp']:.2f}\n"
           f"Sortie forcee : 05h00\n\n"
           f"Lots (compte {CAPITAL:,.0f} $) : {l1:.2f} a 1 %  |  {l05:.2f} a 0,5 % (garde-fou)\n"
           f"{ra['pourquoi']} — RSI {sig['rsi']:.1f}\n"
           f"Garde-fou : 0,5 % si compte a -5 % de son plus haut ; stop total a -7 %.\n"
           f"Prix Dukascopy : ecart possible de quelques centimes avec ton courtier — garde les niveaux SL/TP.")
    if l1 < 0.01:
        txt += "\n⚠️ Stop trop large : meme 0,01 lot depasse le risque prevu."
    return txt


# ── Etat (anti-doublon entre runs) ──────────────────────────────────────────
def lire_etat():
    try:
        with open(ETAT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ecrire_etat(e):
    with open(ETAT, "w", encoding="utf-8") as f:
        json.dump(e, f, ensure_ascii=False, indent=1)


def journaliser(jour, sig, issue, R):
    neuf = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", encoding="utf-8") as f:
        if neuf:
            f.write("date,heure,sens,entree,sl,tp,risque,issue,R\n")
        f.write(f"{jour.date()},{sig['t_ferm'][11:16]},{'achat' if sig['side'] == 1 else 'vente'},"
                f"{sig['e']:.2f},{sig['sl']:.2f},{sig['tp']:.2f},{sig['risque']:.2f},{issue},{R:+.2f}\n")


def envoyer(e, cle, texte, silencieux=False, test=False):
    """Envoie une fois par cle et par nuit ; ne marque 'envoye' que si Telegram a confirme."""
    if test:
        print(f"--- [{cle}{' silencieux' if silencieux else ''}]\n{texte}\n"); return
    if e.get(cle):
        return
    if send_telegram(texte, silent=silencieux):
        e[cle] = True; ecrire_etat(e)


# ── Modes ───────────────────────────────────────────────────────────────────
def verdict():
    now = pd.Timestamp.now(tz=TZ)
    if not 19 <= now.hour <= 23:
        print(f"verdict hors horaire ({now:%H:%M}) : ignore"); return
    e = lire_etat()
    cle = "verdict_" + str((now + pd.Timedelta(hours=6)).date())
    if e.get(cle):
        print("verdict deja envoye"); return
    jour = (now + pd.Timedelta(hours=6)).normalize()        # 23h10 -> la nuit qui suit
    if jour.dayofweek >= 5:
        print("week-end : pas de nuit de trading"); return
    try:
        ra = regle_a(barres("1HOUR", 320), jour)
    except Exception as ex:
        send_telegram(f"⚠️ OR ASIE : impossible de calculer la regle A ce soir ({ex}). Verifie l'indicateur TradingView."); return
    if ra["ok"]:
        t = (f"🌙 OR ASIE — nuit du {jour:%d/%m} : TRADE POSSIBLE\n{ra['pourquoi']}\n"
             f"OPR 02h25-02h35, entrees jusqu'a 04h10, sortie 05h00.\n"
             f"Le robot surveille et t'envoie le signal (alerte sonore). Mets Telegram en exception de « Ne pas deranger ».\n"
             f"Si a 02h40 tu n'as pas recu « OPR formee », le robot est en panne : regarde TradingView.")
    else:
        t = f"😴 OR ASIE — nuit du {jour:%d/%m} : PAS DE TRADE (regle A)\n{ra['pourquoi']}\nDors tranquille."
    print(t)
    if send_telegram(t):
        e[cle] = True; ecrire_etat(e)


def nuit(jour=None, test=False, maintenant=None):
    reel = maintenant is None
    now = maintenant or pd.Timestamp.now(tz=TZ)
    jour = jour or now.normalize()
    fin = jour + pd.Timedelta(hours=5, minutes=2)
    if reel and (now < jour + pd.Timedelta(hours=1) or now >= fin):
        print(f"hors fenetre ({now:%H:%M}) : rien a faire"); return
    if jour.dayofweek >= 5:
        print("week-end"); return
    e = lire_etat()
    if e.get("jour") != str(jour.date()):
        e = {"jour": str(jour.date()), **{k: v for k, v in e.items() if k == "verdict_" + str(jour.date())}}
    if e.get("fini") and not test:
        print("nuit deja traitee"); return
    try:
        ra = regle_a(barres("1HOUR", 320), jour)
    except Exception as ex:
        envoyer(e, "err_h1", f"⚠️ OR ASIE : donnees indisponibles ({ex}). Surveille TradingView toi-meme cette nuit.", test=test)
        return
    print("regle A :", ra)
    if not ra["ok"]:
        envoyer(e, "pas_de_trade", f"😴 OR ASIE {jour:%d/%m} : pas de trade (regle A). {ra['pourquoi']}", silencieux=True, test=test)
        e["fini"] = True; ecrire_etat(e) if not test else None; return
    erreurs = 0
    while True:
        now = pd.Timestamp.now(tz=TZ) if reel else maintenant
        try:
            m1 = barres("1MIN", 4)
            if not reel:
                m1 = m1[m1.index < now]
            erreurs = 0
        except Exception as ex:
            erreurs += 1
            if erreurs == 3:
                envoyer(e, "err_m1", f"⚠️ OR ASIE : flux de prix coupe ({ex}). Regarde TradingView !", test=test)
            time.sleep(30); continue
        if len(m1) and (now - m1.index[-1]) > pd.Timedelta(minutes=15) and now >= jour + pd.Timedelta(hours=2, minutes=40):
            envoyer(e, "err_retard", f"⚠️ OR ASIE : prix Dukascopy figes depuis {m1.index[-1]:%H:%M}. Regarde TradingView !", test=test)
        res = chercher_signal(m5_de(m1), jour, ra["atr"], now)
        if res["opr"] and not e.get("opr"):
            o = res["opr"]
            if o.get("invalide"):
                envoyer(e, "opr", f"OR ASIE {jour:%d/%m} : OPR {o['bas']:.2f} - {o['haut']:.2f} — pas de trade ({o['invalide']}).", silencieux=True, test=test)
                e["fini"] = True; ecrire_etat(e) if not test else None; return
            envoyer(e, "opr", f"👀 OR ASIE {jour:%d/%m} : OPR formee  haut {o['haut']:.2f} / bas {o['bas']:.2f}\n"
                              f"Robot en surveillance jusqu'a 04h10. {ra['pourquoi']}", silencieux=True, test=test)
        for rf in res["refus"]:
            envoyer(e, f"refus_{rf['t']}", f"OR ASIE : cassure {rf['sens']} a {rf['t']} refusee (RSI {rf['rsi']:.1f}). "
                                          f"Re-armement si retour dans l'OPR.", silencieux=True, test=test)
        sig = res["signal"]
        if sig and sig["trop_petit"]:
            # le backtest prend la 1re cassure valide puis ecarte la journee si le stop est < 6,67 couts
            envoyer(e, "trop_petit", f"OR ASIE {jour:%d/%m} : cassure {'achat' if sig['side'] == 1 else 'vente'} a "
                                     f"{sig['t_ferm'][11:16]} mais stop {sig['risque']:.2f} $ trop petit (frais trop lourds). "
                                     f"Pas de trade cette nuit.", silencieux=True, test=test)
            e["fini"] = True; ecrire_etat(e) if not test else None; return
        if sig:
            e["signal"] = sig
            txt = msg_signal(sig, ra)
            age = (now - pd.Timestamp(sig["t_ferm"])).total_seconds() / 60
            if age > 10:
                txt = (f"⚠️ SIGNAL EN RETARD ({age:.0f} min) : le robot a demarre tard. Verifie le prix "
                       f"avant d'entrer ; si le TP ou le SL est deja touche, n'entre pas.\n\n") + txt
            envoyer(e, "signal", txt, test=test)
            issue, R, t = suivre(m1, sig, now, jour)
            if issue:
                emo = {"TP": "✅", "SL": "❌", "05h00": "⏰"}[issue]
                txt = f"{emo} OR ASIE {jour:%d/%m} : {issue} a {t:%H:%M}  ->  {R:+.2f} R (avant frais)"
                if issue == "05h00":
                    txt += "\nFERME LA POSITION si elle est encore ouverte."
                # trade plie en moins de 5 min : tu es peut-etre en train d'entrer -> alerte sonore
                duree = (t + pd.Timedelta(minutes=1) - pd.Timestamp(sig["t_ferm"])).total_seconds() / 60
                rapide = issue != "05h00" and duree <= 5
                if rapide:
                    txt += (f"\nTouche {duree:.0f} min apres le signal. Pas encore entre ? N'ENTRE PLUS. "
                            f"Entre en retard ? Ta position n'est plus celle du robot : verifie ton SL et ton TP.")
                envoyer(e, "issue", txt, silencieux=(issue != "05h00" and not rapide), test=test)
                if not test and not e.get("journal"):
                    journaliser(jour, sig, issue, R); e["journal"] = True
                e["fini"] = True; ecrire_etat(e) if not test else None; return
            # point de controle 5 min apres le signal : position encore ouverte -> SL et TP poses ?
            t5 = pd.Timestamp(sig["t_ferm"]) + pd.Timedelta(minutes=5)
            if t5 <= now < t5 + pd.Timedelta(minutes=10) and len(m1):
                px = float(m1["close"].iloc[-1])
                r = sig["side"] * (px - sig["e"]) / sig["risque"]
                envoyer(e, "point_5min",
                        f"🔔 OR ASIE — point +5 min : position toujours ouverte\n"
                        f"Prix {px:.2f}  ({r:+.2f} R)\n"
                        f"VERIFIE dans MT5 que les deux ordres sont poses :\n"
                        f"SL {sig['sl']:.2f}   |   TP {sig['tp']:.2f}\n"
                        f"Ensuite tu peux te rendormir : je te previens au TP, au SL, ou a 04h55.", test=test)
            if now >= jour + pd.Timedelta(hours=4, minutes=55) and not e.get("rappel_0500"):
                envoyer(e, "rappel_0500", "⏰ OR ASIE : sortie forcee a 05h00 — prepare-toi a fermer la position.", test=test)
                e["rappel_0500"] = True
        elif res["fin_fenetre"]:
            envoyer(e, "rien", f"OR ASIE {jour:%d/%m} : aucune cassure valide avant 04h10. Pas de trade.", silencieux=True, test=test)
            e["fini"] = True; ecrire_etat(e) if not test else None; return
        if not reel or now >= fin:
            return
        if not test:
            ecrire_etat(e)
        time.sleep(20)


def rejouer(date):
    """Controle : rejoue une nuit passee (rien n'est envoye)."""
    jour = pd.Timestamp(date, tz=TZ)
    ra = regle_a(barres("1HOUR", (pd.Timestamp.now(tz=TZ) - jour).days + 320), jour)
    print("regle A :", ra)
    m1 = barres("1MIN", (pd.Timestamp.now(tz=TZ) - jour).days + 4)
    m1 = m1[m1.index < jour + pd.Timedelta(hours=6)]
    res = chercher_signal(m5_de(m1), jour, ra["atr"], jour + pd.Timedelta(hours=6))
    print("OPR :", res["opr"], "refus :", res["refus"])
    if res["signal"]:
        s = res["signal"]
        print("signal :", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()})
        print("issue :", suivre(m1, s, jour + pd.Timedelta(hours=6), jour))
        print(msg_signal(s, ra))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:                       # sans argument : le mode depend de l'heure de Paris
        mode = "verdict" if pd.Timestamp.now(tz=TZ).hour >= 19 else "nuit"
    if mode == "verdict":
        verdict()
    elif mode == "nuit":
        nuit()
    elif mode == "rejouer":
        rejouer(sys.argv[2])
    else:
        print(__doc__)
