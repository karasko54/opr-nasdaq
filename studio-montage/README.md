# Studio Montage

Un petit **studio de montage local**, en français, branché sur le dossier `~/reels` de
l'[éditeur de reels](../reel-editor-setup/). Une page web claire qui tourne sur ton Mac,
sans rien envoyer sur internet (serveur local `127.0.0.1` uniquement).

![onglets](docs-not-shipped) <!-- captures d'écran envoyées dans la conversation -->

## Ce qu'il montre

1. **En cours** — la vidéo en cours de montage et son avancement : coupes, sous-titres, version finale.
2. **Mes vidéos** — toutes tes vidéos avec leurs versions (montage, aperçu, finale) + un bouton
   **« Demander une modif »** qui copie un message prêt à m'envoyer dans Claude Code.
3. **Ma marque** — tes couleurs et tes typos, modifiables, avec un **aperçu en direct** (téléphone 9:16).
4. **Inspiration** — glisse une vidéo, ou colle un lien Instagram / TikTok / YouTube.
5. **Mes idées** — un tableau par étape : Idée → Tournée → En montage → Postée (glisser-déposer).

## Installation

Pré-requis commun : **Node.js** (https://nodejs.org, bouton LTS). **ffmpeg** est optionnel
(seulement pour les vignettes des vidéos).

### Windows (Surface, PC…)

1. Récupère le dépôt à jour : `git pull`
2. Ouvre le dossier `studio-montage` dans l'Explorateur et **double-clique `install-windows.bat`**.

L'installeur copie le studio dans `%USERPROFILE%\StudioMontage`, pose un raccourci
**« Studio Montage »** sur le **Bureau**, et ouvre le studio. Ensuite, un double-clic sur
le raccourci le rouvre.

> Si Windows affiche un écran bleu « Windows a protégé votre ordinateur », clique
> **Informations complémentaires → Exécuter quand même** (le fichier vient de ton dépôt, il est sûr).

### macOS

1. `git pull`
2. Dans le Finder, ouvre `studio-montage` et **double-clique `install.command`**
   *(ou en Terminal : `bash studio-montage/install.command`)*.

L'installeur copie le studio dans `~/StudioMontage`, pose une icône **« Studio Montage »**
sur le **Bureau**, et ouvre le studio.

> Si macOS bloque le double-clic (fichier « non identifié »), fais **clic droit → Ouvrir**.

## Où sont mes données ?

Tout vit dans `~/reels/` :

| Donnée | Emplacement |
|---|---|
| Couleurs / typos | `~/reels/brand.json` (lu et écrit par le studio) |
| Idées | `~/reels/studio/ideas.json` |
| Inspiration (liens + vidéos) | `~/reels/studio/inspiration/` + `inspiration.json` |
| Vignettes | `~/reels/studio/thumbs/` |
| Vidéos et versions | les sous-dossiers de reels créés par l'éditeur |

Le studio **lit** l'avancement de tes reels (il ne les modifie pas) et **n'écrit** que la marque,
les idées et l'inspiration. Pour changer un montage, utilise le bouton **« Demander une modif »** :
il prépare le message, tu me l'envoies dans Claude Code, et c'est moi qui fais le montage.

## Comment ça marche (technique)

- `app/server.js` : serveur HTTP **sans aucune dépendance** (Node pur), lié à `127.0.0.1`.
  Il scanne `~/reels`, déduit l'étape de chaque reel d'après les fichiers présents
  (`raw.mov` → `transcript.json` → `fillers.json`/`edl.json` → `proposal.json` → `master.mp4` → `final.mp4`),
  sert les vidéos (avec `Range`), génère les vignettes via ffmpeg, et lit/écrit marque, idées, inspiration.
- `app/public/` : l'interface (HTML/CSS/JS sans framework).
- `install-windows.bat` (+ `windows/StudioMontage.vbs`) : installe dans `%USERPROFILE%\StudioMontage`
  et crée un raccourci sur le Bureau (lance le serveur sans fenêtre, puis ouvre le navigateur).
- `install.command` : équivalent macOS (installe dans `~/StudioMontage`, crée l'app `.app` sur le Bureau).
- `seeds/ideas.json` : idées de départ, copiées dans `~/reels/studio/` au premier lancement
  seulement si tu n'en as pas encore.

Pour l'arrêter : ferme l'onglet. Le serveur local peut rester actif en fond (sans danger) et
repart au prochain double-clic. Pour le stopper vraiment :
- **Windows** : Gestionnaire des tâches → termine le processus `Node.js`.
- **macOS** : `pkill -f StudioMontage/app/server.js`.
