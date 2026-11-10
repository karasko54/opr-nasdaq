# Backtest order flow + niveaux d'options (NQ / MNQ)

## Les 3 fichiers

| Fichier | Rôle |
|---|---|
| `backtest_flow_options.py` | le moteur de backtest |
| `options_levels.py` | calcule call wall / put wall / zero gamma / GEX net depuis une chaîne d'options |
| `collect_options_levels.py` + `.github/workflows/options.yml` | photo quotidienne de la chaîne QQQ → `options_levels.csv` |

Test immédiat (données synthétiques, résultats **sans valeur**) :
```
python backtest_flow_options.py --demo
```

## Le vrai problème : les données

Aucune des deux données nécessaires n'est dans ce dépôt, ni gratuite en historique.

**1. Order flow** — il faut le **côté agresseur** de chaque transaction, sur le **future** (NQ/MNQ).
Les CFD Dukascopy utilisés par `backtest_opr.py` ne conviennent pas : leur « volume »
est un compte de cotations, sans volume réel ni agresseur. Sources :
- **Databento** (`GLBX.MDP3`, schéma `trades`, symbole `NQ.c.0` ou `MNQ.c.0`) : paiement à l'usage,
  crédit offert à l'inscription — le plus simple. Format lu directement (`ts_event, price, size, side`).
- Export **footprint** Sierra Chart / ATAS / NinjaTrader (barres avec `AskVolume`/`BidVolume`).

**2. Niveaux d'options** — il faut l'open interest **par strike et par jour**. yfinance ne donne
que la chaîne du jour. Deux options :
- **Gratuit mais lent** : le workflow `options.yml` enregistre la chaîne QQQ chaque matin.
  Il faut ~6 mois minimum avant un premier backtest lisible, 1 an+ pour conclure.
- **Payant et immédiat** : ORATS, CBOE DataShop, ThetaData, Databento OPRA. Convertir au format
  normalisé (voir en-tête de `options_levels.py`) puis `python options_levels.py build DOSSIER --shift-days 1`
  si le fournisseur date l'OI à la clôture de la veille.

## Corrections de logique de marché intégrées au code

- **Anti-triche OI** : l'OI publié le matin de J reflète la clôture de J-1. Les niveaux sont
  datés de la séance où ils servent ; `--shift-days` gère les fournisseurs qui datent autrement.
- **QQQ → NQ** : les niveaux sont convertis avec le ratio NQ/QQQ de la **veille à 16:00**, recalculé
  chaque jour (absorbe roll, dividendes, base du future).
- **Zero gamma** : recalculé en réévaluant toute la chaîne sur une grille de prix (vraie définition),
  pas avec le raccourci « changement de signe strike par strike », qui est faux.
- **Volume anormal** : comparé à la médiane de la **même minute** sur 20 séances. Un z-score glissant
  déclencherait chaque matin à l'ouverture et jamais à midi.
- **Pas d'entrée avant 09:45** : le flux d'ouverture (ordres MOO, couvertures de nuit) n'est pas
  de l'absorption.
- **Le régime gamma décide du sens** : au-dessus du zero gamma on joue le **rejet** des murs,
  en dessous on joue la **cassure**. Faire l'inverse, c'est trader contre la couverture des dealers.
- **Limite assumée** : l'OI ne voit pas le 0DTE ouvert et fermé dans la journée (>50 % du volume
  d'options). Les niveaux sont la carte de la veille ; c'est pour ça que chaque entrée exige une
  confirmation order flow.

## Lancer un vrai backtest

```
python backtest_flow_options.py nq_trades.csv.gz --levels options_levels.csv --contract MNQ
python backtest_flow_options.py nq_trades.csv.gz --setups FADE_CW,FADE_PW --rr 1.5
python backtest_flow_options.py nq_trades.csv.gz --to 2025-06-30     # calibrage
python backtest_flow_options.py nq_trades.csv.gz --from 2025-07-01   # vérification hors échantillon
```

Résultats ventilés par setup, par régime gamma et par année, avec le t de Student.
Règle : calibrer `--imb`, `--vol-mult`, `--rr` sur la première moitié seulement, puis vérifier une seule fois sur la seconde.
