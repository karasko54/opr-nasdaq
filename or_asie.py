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
                                 le SIGNAL (sonore), le point +2 min, puis TP / SL / sortie 05h00.
                                 Nuit sans trade : UNE ligne silencieuse (ou rien si le verdict du
                                 soir avait deja dit "pas de trade"). Le detail va dans journal_or_nuits.csv.
  python or_asie.py rejouer AAAA-MM-JJ -> rejoue une nuit passee sans rien envoyer (controle)
  python or_asie.py simuler AAAA-MM-JJ [HH:MM debut] [HH:MM arret] -> meme controle mais par le VRAI
                                 chemin de la nuit (horloge acceleree, memoire, journaux) ; rien ne part sur Telegram

Donnees : Dukascopy XAU/USD bid (temps reel, < 1 min de retard, meme source que le backtest).
Si les donnees manquent, le robot le DIT sur Telegram plutot que de se taire.
"""
import os
import sys
import csv
import json
import tempfile
import time
import random
import string
import traceback
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
JOURNAL_NUITS = "journal_or_nuits.csv"   # une ligne par nuit, trade ou pas (base d'etude)
PAUSE = 5            # s entre deux lectures du prix
POINT_MIN = 2        # min apres le signal : point de controle "SL/TP poses ?"
RAPIDE_MIN = 5       # TP/SL touche en moins de N min -> alerte sonore "n'entre plus"
MARGE_FLUX = 45      # s : au-dela, on n'attend plus la derniere bougie M1 (minute sans cotation)


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


def heure_sure(m1, now):
    """Dukascopy ne livre une bougie M1 que 2 a 15 s apres sa cloture. Tant que la derniere minute
    n'est pas arrivee, la bougie M5 n'est pas complete : on ne juge que ce qui est vraiment livre."""
    if not len(m1):
        return now
    return min(now, max(m1.index[-1] + pd.Timedelta(minutes=1), now - pd.Timedelta(seconds=MARGE_FLUX)))


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
           f"Garde ces SL / TP meme si ton prix d'entree differe de quelques centimes.")
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


COLS_NUIT = ["date", "motif", "regle_a", "atr_pct", "veille_atr", "atr_usd", "opr_haut", "opr_bas", "opr_usd",
             "opr_sur_atr", "nb_refus", "refus", "signal_heure", "sens", "entree", "sl", "tp", "stop_usd",
             "stop_sur_opr", "rsi", "retard_signal_s", "issue", "R", "duree_min", "heure_issue"]


def journal_nuit(e, jour, motif, ra=None, res=None, sig=None, issue=None, R=None, t=None):
    """Une ligne par nuit, qu'il y ait eu un trade ou non : ce qui permettra plus tard d'etudier
    ce qui distingue les bonnes nuits des mauvaises. motif = regle_a / opr_invalide /
    stop_trop_petit / aucune_cassure / trade / incomplet."""
    if e.get("journal_nuit"):
        return
    L = {"date": str(jour.date()), "motif": motif}
    if ra:
        L.update(regle_a="oui" if ra["ok"] else "non", atr_pct=f"{ra['atr_pct']:.3f}",
                 veille_atr=f"{ra['veille']:.3f}", atr_usd=f"{ra['atr']:.2f}")
    opr = (res or {}).get("opr")
    if opr:
        L.update(opr_haut=f"{opr['haut']:.2f}", opr_bas=f"{opr['bas']:.2f}", opr_usd=f"{opr['haut'] - opr['bas']:.2f}")
        if ra:
            L["opr_sur_atr"] = f"{(opr['haut'] - opr['bas']) / ra['atr']:.4f}"
    if res:
        L.update(nb_refus=len(res["refus"]),
                 refus=" ; ".join(f"{r['t']} {r['sens']} RSI {r['rsi']:.1f}" for r in res["refus"]))
    if sig:
        L.update(signal_heure=sig["t_ferm"][11:16], sens="achat" if sig["side"] == 1 else "vente",
                 entree=f"{sig['e']:.2f}", sl=f"{sig['sl']:.2f}", tp=f"{sig['tp']:.2f}",
                 stop_usd=f"{sig['risque']:.2f}", rsi=f"{sig['rsi']:.1f}",
                 retard_signal_s=e.get("retard_signal_s", ""))
        if opr and opr["haut"] > opr["bas"]:
            L["stop_sur_opr"] = f"{sig['risque'] / (opr['haut'] - opr['bas']):.3f}"
    if issue:
        fin_t = t + pd.Timedelta(minutes=1)
        L.update(issue=issue, R=f"{R:+.2f}", heure_issue=f"{fin_t:%H:%M}",
                 duree_min=f"{(fin_t - pd.Timestamp(sig['t_ferm'])).total_seconds() / 60:.0f}")
    neuf = not os.path.exists(JOURNAL_NUITS)
    with open(JOURNAL_NUITS, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS_NUIT, lineterminator="\n")
        if neuf:
            w.writeheader()
        w.writerow({k: L.get(k, "") for k in COLS_NUIT})
    e["journal_nuit"] = True


class Reel:
    """Horloge et flux de prix reels. Simule (plus bas) offre la meme interface avec une nuit passee :
    le controle passe ainsi par EXACTEMENT le meme code que la vraie nuit."""
    def __init__(self):
        self._m1 = None; self._t = 0.0

    def now(self):
        return pd.Timestamp.now(tz=TZ)

    def sleep(self, s):
        time.sleep(s)

    def h1(self):
        return barres("1HOUR", 320)

    def m1(self):
        if self._m1 is None or time.time() - self._t > 1800:
            self._m1 = barres("1MIN", 4); self._t = time.time()        # historique complet (RSI)
        else:                                                           # ensuite : les 20 dernieres minutes
            frais = barres("1MIN", 20 / 1440)
            if len(frais):
                m = pd.concat([self._m1, frais]); self._m1 = m[~m.index.duplicated(keep="last")].sort_index()
        return self._m1


class Simule:
    """Nuit passee rejouee en accelere : l'horloge avance a chaque pause, et chaque bougie M1
    n'est livree que `retard` secondes apres sa cloture, comme le vrai flux Dukascopy."""
    def __init__(self, jour, debut="02:15", retard=10, arret=None, donnees=None):
        self.t = jour + pd.Timedelta(debut + ":00")
        self.retard = pd.Timedelta(seconds=retard)
        self.arret = jour + pd.Timedelta(arret + ":00") if arret else None
        if donnees is None:
            n = (pd.Timestamp.now(tz=TZ) - jour).days
            donnees = (barres("1HOUR", n + 320), barres("1MIN", n + 4))
        self._h1, self._m1 = donnees

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += pd.Timedelta(seconds=s)
        if self.arret is not None and self.t >= self.arret:
            raise KeyboardInterrupt("arret simule")

    def h1(self):
        return self._h1[self._h1.index + pd.Timedelta(hours=1) <= self.t]

    def m1(self):
        return self._m1[self._m1.index + pd.Timedelta(minutes=1) + self.retard <= self.t]


def envoyer(e, cle, texte, silencieux=False, test=False):
    """Envoie une fois par cle et par nuit ; ne marque 'envoye' que si Telegram a confirme."""
    if test:
        print(f"--- [{cle}{' silencieux' if silencieux else ''}]\n{texte}\n"); return
    if e.get(cle):
        return
    if send_telegram(texte, silent=silencieux):
        e[cle] = True
    else:       # un destinataire en panne ne doit pas faire re-sonner les autres a chaque tour
        n = e.setdefault("_echecs", {}); n[cle] = n.get(cle, 0) + 1
        if n[cle] >= (3 if silencieux else 8):      # on insiste plus longtemps pour une alerte sonore
            print(f"!! {cle} : {n[cle]} envois incomplets, on n'insiste plus"); e[cle] = True
    ecrire_etat(e)


def diag():
    """Controle sans aucun envoi : acces aux prix + regle A. Sort en erreur si les donnees manquent."""
    now = pd.Timestamp.now(tz=TZ)
    h1 = barres("1HOUR", 320); m1 = barres("1MIN", 4)
    print(f"DIAG {now:%d/%m %H:%M} Paris : H1 {len(h1)} barres, M1 {len(m1)} barres, "
          f"derniere M1 {m1.index[-1]:%d/%m %H:%M} (il y a {(now - m1.index[-1]).total_seconds() / 60:.0f} min)")
    print("regle A (provisoire) :", regle_a(h1, (now + pd.Timedelta(hours=6)).normalize()))
    if len(h1) < 200 or len(m1) < 1000:
        raise SystemExit("DIAG : donnees Dukascopy insuffisantes")


# ── Modes ───────────────────────────────────────────────────────────────────
def verdict():
    now = pd.Timestamp.now(tz=TZ)
    if now.hour != 23:          # avant 23h la seance du jour n'est pas finie : la regle A serait fausse
        print(f"verdict hors horaire ({now:%H:%M}) : rien n'est envoye, simple controle des donnees")
        diag(); return
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
             f"Signal possible entre 02h35 et 04h10 (alerte sonore), sortie 05h00.\n"
             f"Sans signal : une seule ligne silencieuse en fin de nuit.")
    else:
        t = f"😴 OR ASIE — nuit du {jour:%d/%m} : PAS DE TRADE (regle A)\n{ra['pourquoi']}"
    print(t)
    if send_telegram(t):
        e[cle] = "oui" if ra["ok"] else "non"; ecrire_etat(e)


def nuit(jour=None, test=False, maintenant=None, env=None):
    env = env or Reel()
    reel = maintenant is None
    now = maintenant or env.now()
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
        ra = regle_a(env.h1(), jour)
    except Exception as ex:
        envoyer(e, "err_h1", f"⚠️ OR ASIE : donnees indisponibles ({ex}). Surveille TradingView toi-meme cette nuit.", test=test)
        return
    print("regle A :", ra)
    if not ra["ok"]:
        if e.get("verdict_" + str(jour.date())) == "non":
            print("pas de trade (regle A) : deja annonce par le verdict du soir, aucun message")
        else:
            envoyer(e, "pas_de_trade", f"😴 OR ASIE {jour:%d/%m} : pas de trade (regle A). {ra['pourquoi']}", silencieux=True, test=test)
        if not test:
            journal_nuit(e, jour, "regle_a", ra)
        e["fini"] = True; ecrire_etat(e) if not test else None; return
    erreurs = 0; res = None
    while True:
        now = env.now() if reel else maintenant
        try:
            m1 = env.m1()
            if not reel:
                m1 = m1[m1.index < now]
            erreurs = 0
        except Exception as ex:
            erreurs += 1
            if erreurs == 3:
                envoyer(e, "err_m1", f"⚠️ OR ASIE : flux de prix coupe ({ex}). Regarde TradingView !", test=test)
            env.sleep(30)
            if reel and env.now() >= fin:       # flux coupe jusqu'au bout : on le note et on sort
                if not test:
                    journal_nuit(e, jour, "incomplet", ra, res, e.get("sig")); ecrire_etat(e)
                return
            continue
        if len(m1) and (now - m1.index[-1]) > pd.Timedelta(minutes=15) and now >= jour + pd.Timedelta(hours=2, minutes=40):
            envoyer(e, "err_retard", f"⚠️ OR ASIE : prix Dukascopy figes depuis {m1.index[-1]:%H:%M}. Regarde TradingView !", test=test)
        res = chercher_signal(m5_de(m1), jour, ra["atr"], heure_sure(m1, now) if reel else now)
        if res["opr"] and not e.get("opr"):
            o = res["opr"]
            if o.get("invalide"):
                envoyer(e, "opr", f"OR ASIE {jour:%d/%m} : OPR {o['bas']:.2f} - {o['haut']:.2f} — pas de trade ({o['invalide']}).", silencieux=True, test=test)
                if not test:
                    journal_nuit(e, jour, "opr_invalide", ra, res)
                e["fini"] = True; ecrire_etat(e) if not test else None; return
        sig = res["signal"]
        if sig and sig["trop_petit"]:
            # le backtest prend la 1re cassure valide puis ecarte la journee si le stop est < 6,67 couts
            envoyer(e, "trop_petit", f"OR ASIE {jour:%d/%m} : cassure {'achat' if sig['side'] == 1 else 'vente'} a "
                                     f"{sig['t_ferm'][11:16]} mais stop {sig['risque']:.2f} $ trop petit (frais trop lourds). "
                                     f"Pas de trade cette nuit.", silencieux=True, test=test)
            if not test:
                journal_nuit(e, jour, "stop_trop_petit", ra, res, sig)
            e["fini"] = True; ecrire_etat(e) if not test else None; return
        if sig:
            # ATTENTION : "sig" = les niveaux ; "signal" = drapeau "message envoye" pose par envoyer().
            # (les deux partageaient la cle "signal" : le message SIGNAL n'etait alors jamais envoye)
            e["sig"] = sig
            e.setdefault("retard_signal_s", round((now - pd.Timestamp(sig["t_ferm"])).total_seconds()))
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
                rapide = issue != "05h00" and duree <= RAPIDE_MIN
                if rapide:
                    txt += (f"\nTouche {duree:.0f} min apres le signal. Pas encore entre ? N'ENTRE PLUS. "
                            f"Entre en retard ? Ta position n'est plus celle du robot : verifie ton SL et ton TP.")
                envoyer(e, "issue", txt, silencieux=(issue != "05h00" and not rapide), test=test)
                if not test and not e.get("journal"):
                    journaliser(jour, sig, issue, R); e["journal"] = True
                if not test:
                    journal_nuit(e, jour, "trade", ra, res, sig, issue, R, t)
                e["fini"] = True; ecrire_etat(e) if not test else None; return
            # point de controle peu apres le signal : position encore ouverte -> SL et TP poses ?
            t5 = pd.Timestamp(sig["t_ferm"]) + pd.Timedelta(minutes=POINT_MIN)
            if t5 <= now < t5 + pd.Timedelta(minutes=10) and len(m1):
                px = float(m1["close"].iloc[-1])
                r = sig["side"] * (px - sig["e"]) / sig["risque"]
                envoyer(e, "point_5min",
                        f"🔔 OR ASIE — point +{POINT_MIN} min : position toujours ouverte\n"
                        f"Prix {px:.2f}  ({r:+.2f} R)\n"
                        f"VERIFIE dans MT5 que les deux ordres sont poses :\n"
                        f"SL {sig['sl']:.2f}   |   TP {sig['tp']:.2f}\n"
                        f"Ensuite tu peux te rendormir : je te previens au TP, au SL, ou a 04h55.", test=test)
            if now >= jour + pd.Timedelta(hours=4, minutes=55) and not e.get("rappel_0500"):
                envoyer(e, "rappel_0500", "⏰ OR ASIE : sortie forcee a 05h00 — prepare-toi a fermer la position.", test=test)
        elif res["fin_fenetre"]:
            envoyer(e, "rien", f"OR ASIE {jour:%d/%m} : aucune cassure valide avant 04h10"
                               + (f" ({len(res['refus'])} refusee(s) par le RSI)" if res["refus"] else "") + ". Pas de trade.", silencieux=True, test=test)
            if not test:
                journal_nuit(e, jour, "aucune_cassure", ra, res)
            e["fini"] = True; ecrire_etat(e) if not test else None; return
        if not reel:
            return
        if now >= fin:              # 05h02 sans conclusion (donnees manquantes) : on le note quand meme
            if not test:
                journal_nuit(e, jour, "incomplet", ra, res, e.get("sig")); ecrire_etat(e)
            return
        if not test:
            ecrire_etat(e)
        env.sleep(PAUSE)


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


def simuler(date, debut="02:15", arret=None, dossier=None, donnees=None, envoi=None):
    """Controle de bout en bout : rejoue une nuit passee par le VRAI chemin de nuit() (memes envois,
    meme memoire, memes journaux), mais rien ne part sur Telegram et tout s'ecrit dans un dossier temporaire.
    `envoi(texte, silent)` -> True/False permet de simuler une panne Telegram."""
    global send_telegram, ETAT, JOURNAL, JOURNAL_NUITS
    d = dossier or tempfile.mkdtemp(prefix="or_sim_")
    ETAT, JOURNAL, JOURNAL_NUITS = (os.path.join(d, x) for x in ("etat.json", "journal_or.csv", "journal_or_nuits.csv"))
    jour = pd.Timestamp(date, tz=TZ)
    env = Simule(jour, debut=debut, arret=arret, donnees=donnees)
    envois = []

    def faux(texte, silent=False, **_):
        ok = envoi(texte, silent) if envoi else True
        envois.append((env.t, silent, texte, ok))
        print(f"[{env.t:%H:%M:%S}] {'silencieux' if silent else 'SONORE'}{'' if ok else ' (ECHEC simule)'} | "
              + texte.replace("\n", "\n      ") + "\n")
        return ok
    send_telegram = faux
    try:
        nuit(jour=jour, env=env)
    except KeyboardInterrupt:
        print(f"[{env.t:%H:%M:%S}] --- arret simule ---")
    print(f"fin de simulation a {env.t:%H:%M:%S} : {len(envois)} message(s)")
    for f in (JOURNAL, JOURNAL_NUITS):
        if os.path.exists(f):
            print(os.path.basename(f), ":"); print(open(f, encoding="utf-8").read())
    return d, envois


if __name__ == "__main__":
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:                       # sans argument : le mode depend de l'heure de Paris
        mode = "verdict" if pd.Timestamp.now(tz=TZ).hour >= 19 else "nuit"
    if mode == "verdict":
        verdict()
    elif mode == "nuit":
        for essai in range(1, 6):       # une erreur imprevue ne doit pas arreter la surveillance
            try:
                nuit(); break
            except Exception:
                traceback.print_exc()
                print(f"!! nuit() a plante (essai {essai}/5) : reprise dans 15 s"); time.sleep(15)
        else:
            raise SystemExit("nuit() : 5 plantages, abandon")
    elif mode == "diag":
        diag()
    elif mode == "rejouer":
        rejouer(sys.argv[2])
    elif mode == "simuler":
        simuler(sys.argv[2], *sys.argv[3:5])
    else:
        print(__doc__)
