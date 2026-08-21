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
4. **Choisis le comptage** :
   - **Manuel** — tu tapes le grand bouton `+1` à chaque répétition (fiable partout).
   - **Mouvement** — le capteur du téléphone (accéléromètre) compte tes reps
     automatiquement (expérimental, mobile uniquement, nécessite une autorisation).
5. **Active l'alarme.** À l'heure prévue, l'écran passe en mode sonnerie.
6. **La sonnerie tourne en boucle** (bip + vibration) et **ne s'arrête que**
   lorsque l'objectif est atteint. 💪

Le bouton **« Tester la sonnerie maintenant »** déclenche l'alarme immédiatement
pour essayer sans attendre.

## Fonctionnement / détails

- **Sonnerie** générée en direct via l'API Web Audio (aucun fichier son externe).
- **Anti-triche** : une cadence minimale réaliste est imposée entre deux
  répétitions (≈1,1 s pour les pompes, 2,4 s pour les burpees, etc.). Marteler le
  bouton ne compte pas — il faut vraiment faire le mouvement.
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
