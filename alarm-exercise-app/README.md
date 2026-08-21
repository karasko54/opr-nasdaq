# Réveil Sportif 🏋️‍♂️⏰

Un réveil qui **ne s'éteint qu'une fois ton objectif d'exercice atteint**. La
sonnerie tourne en boucle jusqu'à ce que tu aies validé le nombre de pompes,
squats (ou autre) demandé.

Application web autonome — **un seul fichier** (`index.html`), sans dépendance,
sans serveur, sans compte. Fonctionne sur téléphone et sur ordinateur.

## Utilisation

Ouvre `index.html` dans un navigateur (double-clic, ou envoie le fichier sur ton
téléphone). Idéalement, ajoute la page à l'écran d'accueil de ton téléphone.

1. **Règle l'heure** du réveil.
2. **Choisis l'exercice** : pompes, squats, jumping jacks, fentes, abdos, burpees.
3. **Fixe l'objectif** (nombre de répétitions).
4. **Choisis la vérification** :
   - **Manuel** — tu tapes le grand bouton `+1` à chaque répétition (fiable partout).
   - **Capteur** — l'accéléromètre du téléphone compte tes reps automatiquement
     (mobile uniquement, nécessite une autorisation).
   - **Caméra** — la caméra vérifie visuellement que le mouvement est réellement
     exécuté (voir plus bas).
5. **Active l'alarme.** À l'heure prévue, l'écran passe en mode sonnerie.
6. **La sonnerie tourne en boucle** (bip + vibration) et **ne s'arrête que**
   lorsque l'objectif est atteint. 💪

Le bouton **« Tester la sonnerie maintenant »** déclenche l'alarme immédiatement
pour essayer sans attendre.

## Vérification par la caméra 📷

Tu peux utiliser la **caméra avant ou arrière** (bouton « ↺ Autre caméra ») pour
faire valider les répétitions par l'image.

**Comment ça marche.** Aucun modèle d'IA n'est téléchargé — tout est calculé sur
place, en JavaScript :

1. **Calibration (~2,5 s)** — l'image est réduite en 96×72 niveaux de gris et une
   image de référence de la pièce est construite par **médiane temporelle** sur
   9 clichés. La médiane permet d'obtenir le décor réel même si tu bouges déjà
   devant l'objectif.
2. **Extraction du corps** — chaque image est comparée à cette référence, ce qui
   isole la silhouette en mouvement. Les variations globales de luminosité
   (auto-exposition) sont compensées pour ne pas être prises pour du mouvement.
3. **Trois signaux** sont suivis en parallèle : la **surface** occupée par le
   corps, sa **position verticale**, et la **structure** de l'image (ce dernier
   fonctionne même quand tu remplis tout le cadre, téléphone au sol). Celui qui
   oscille le plus fort pilote la détection.
4. **Comptage** — une répétition est validée par un **déclencheur de Schmitt** :
   il faut une descente *puis* une remontée complètes, avec une amplitude
   suffisante **et** en respectant la cadence minimale de l'exercice.

Résultat : impossible de valider en agitant vaguement la main ou en tapant un
bouton — il faut un mouvement ample, complet et rythmé.

**Honnêteté sur les limites** — le système vérifie l'**amplitude et le rythme**
réels de ton corps, pas la qualité de ta posture : il ne dira pas si tu as le dos
creux ou les coudes trop écartés. Il faut aussi **un minimum de lumière** (l'app
te prévient si la pièce est trop sombre) et que le téléphone reste immobile (s'il
est bougé, la calibration se relance automatiquement).

**Vie privée** — l'image est analysée **uniquement sur ton appareil**. Rien n'est
enregistré, rien n'est envoyé sur le réseau. Le flux est coupé et libéré dès que
l'alarme s'arrête. Un bouton « Compter à la main » permet de basculer en manuel à
tout moment si la caméra ne convient pas.

> ℹ️ Les navigateurs n'autorisent la caméra que sur une origine sûre : `https://`
> ou un fichier local `file://`. Servi en `http://` depuis un autre poste, l'accès
> caméra sera refusé.

## Fonctionnement / détails

- **Sonnerie** générée en direct via l'API Web Audio (aucun fichier son externe).
- **Anti-triche** : une cadence minimale réaliste est imposée entre deux
  répétitions (≈1,1 s pour les pompes, 2,4 s pour les burpees, etc.). Marteler le
  bouton ne compte pas — il faut vraiment faire le mouvement. En mode caméra,
  l'amplitude du mouvement est vérifiée en plus de la cadence.
- **Vibration** et **maintien de l'écran allumé** (Wake Lock) quand c'est
  supporté par l'appareil.
- **Thème clair / sombre** automatique (suit le système) avec bascule manuelle.
- Les réglages sont **mémorisés** localement (localStorage).

## Limite importante

Comme toute page web, l'alarme n'est active **que si la page reste ouverte**
(onglet au premier plan, écran allumé). Ce n'est pas une app installée en
arrière-plan : garde l'appareil branché, écran allumé, page ouverte pendant la
nuit. Pour une vraie alarme système en arrière-plan, il faudrait une application
native (Android/iOS).

## Structure

```
alarm-exercise-app/
└── index.html   # toute l'application (HTML + CSS + JS)
```
