# Correctifs du robot OPR — 27 septembre 2026

Quatre fichiers à téléverser sur `github.com/karasko54/opr-nasdaq` (branche `master`).
**Aucune règle de trading n'est modifiée** : ni la bougie d'ouverture, ni les EMA,
ni le Supertrend, ni le SL, ni le TP, ni le break-even, ni les horaires.

---

## 1. `opr_live.py` — LE correctif important

**Ajoute un filtre de range**, exprimé en pourcentage du prix.

| Actif | Seuil | Mesure sur le backtest |
|---|---|---|
| NASDAQ | 0,25 % | +25,5 R → **+85,6 R** sur 11 ans |
| BTC | 0,45 % | **−97,5 R → +41,1 R** sur 5,5 ans |

Le BTC perd de l'argent de façon statistiquement établie sans ce filtre
(t = −2,08 sur 651 trades). C'est le seul problème mesuré qui ne soit pas ambigu.

**Pourquoi ça marche** : les trades écartés ont le même avantage brut que les
autres (+0,21 R contre +0,22 R), mais ils paient 3 à 4,5 fois plus de frais.
Le filtre ne choisit pas de meilleurs trades, il écarte ceux dont le gain part
entièrement chez le courtier.

**Pourquoi en % et pas en points** : un seuil en points fixes finit par ne
sélectionner que les années où l'actif cote cher. Vérifié trois fois — sur l'or
(200 pips), sur le BTC (500 points) et sur le NASDAQ (50 points).

Désactivation : `OPR_RANGE_PCT=0`.

## 2. `garde_silence.py` + `.github/workflows/garde.yml` — le filet

Alerte Telegram si aucune session n'a été envoyée un jour ouvré.

Aujourd'hui le robot repose sur **un seul** déclenchement fiable (cron-job.org
à 13:48 UTC). Les 7 crons GitHub arrivent à 18:04 UTC en médiane — **84 %
tombent après la fermeture de la fenêtre d'entrée** et ne servent à rien.
Si cron-job.org tombe, le silence est total et rien ne prévient.

Le garde-fou s'appuie volontairement sur le planificateur GitHub : il est donc
indépendant du déclencheur qu'il surveille. Testé sur 4 cas.

## 3. `opr.yml` — commentaire corrigé

L'ancien annonçait que les crons rattrapaient « un éventuel petit retard ».
Le retard mesuré est de 4 à 6 heures. Le commentaire dit maintenant la vérité.
Les crons sont conservés (ils tombent juste 16 % du temps, et ne coûtent rien).

## 4. `backtest_opr.py` — deux bugs

- **`FORCE_CLOSE` n'avait aucun effet** : les positions étaient soldées à 17h30
  au lieu de 21h. Le backtest ne simulait donc pas ce que fait le robot.
- **Le tampon n'existait pas** dans le backtest alors que le robot l'utilise.
- Bonus : 37 secondes au lieu de 20 minutes, résultat identique.

---

## Ce que je n'ai pas pu faire

**Le second déclencheur cron-job.org.** Un clone est créé mais **inactif**
(`console.cron-job.org/jobs/8524968`). Il reste à mettre les minutes à 52 et à
l'activer. Service externe, ton compte, ton token.

**Recalibrer sur ^NDX.** Le backtest tourne sur du CFD Dukascopy, le robot lit
^NDX : ranges ~5 % plus larges, prix décalés de 0,4 %. Les seuils retenus sont
volontairement sous l'optimum du backtest pour cette raison.

---

## Réserve honnête

Le t de Student reste entre 1,1 et 2,0 selon les configurations : rien n'est
significatif au sens strict. Ce qui l'est, c'est que **le BTC perd de l'argent
aujourd'hui** (t = −2,08). Le filtre corrige un problème réel pour une raison
mécanique vérifiée — il ne transforme pas la stratégie en machine à gagner.
