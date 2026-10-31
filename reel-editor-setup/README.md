# Reel Editor — installation

Installeur pour la skill Claude Code **[claude-reel-editor](https://github.com/nicolapocock/claude-reel-editor)** :
elle transforme une vidéo brute « face caméra » (clip téléphone, selfie, webcam) en
reel vertical court et prêt à publier — nettoyage audio, coupe des mots de remplissage,
sous-titres et graphismes, le tout dans **ton** style de marque.

> C'est une **skill Claude Code**, pas un plugin ni un MCP autonome. Les graphismes et
> sous-titres s'appuient sur la skill **HyperFrames** (installée automatiquement comme
> dépendance). CapCut est une sortie optionnelle via un connecteur MCP séparé.

## Pré-requis

- **macOS** (seule plateforme testée par l'auteur ; Windows/Linux non garantis)
- **Claude Code** sur un abonnement Claude payant
- **Node LTS** (fournit `node` et `npx`) — https://nodejs.org
- **ffmpeg** — `brew install ffmpeg`
- **~10 Go** d'espace disque libre (35+ Go pour des exports 4K)
- Optionnel : un compte **CapCut** gratuit

## Installation (sur ton Mac)

Depuis la racine du dépôt :

```bash
bash reel-editor-setup/install.sh
```

Le script :

1. installe la skill `hyperframes` en **global** (`~/.claude/skills/`) ;
2. installe la skill `reel-editor` en **global** ;
3. prépare le moteur d'animation (gsap + effets sonores synthétisés par ffmpeg + motions HyperFrames) ;
4. lance le `doctor` qui confirme que tout est prêt (`READY` / `READY WITH LIMITS` / `MISSING`).

### Équivalent manuel

```bash
npx skills add heygen-com/hyperframes
npx skills add nicolapocock/claude-reel-editor
bash ~/.claude/skills/reel-editor/scripts/doctor.sh
```

### Optionnel — CapCut (brouillon éditable)

```bash
claude mcp add --transport http --scope user capcut_creation https://www.capcut.com/api/external_mcp
```

Puis dans Claude Code : `/mcp` → `capcut_creation` → connexion dans le navigateur.
Dispo en Australie / UK / Europe / Asie ; **indisponible aux USA et en Inde**.

## Utilisation

1. Ouvre Claude Code, donne-lui ta vidéo et dis : **« edit this into a reel »**.
2. La **première fois**, il lance un petit entretien (~5 min) pour créer ton
   **fichier de marque** (couleurs, polices, style de sous-titres, sons, call-to-action),
   stocké dans `~/reels/` (`brand.md` + `brand.json`).
3. Tu valides les coupes / graphismes / sous-titres sur une page de revue privée.
4. Dis **« build preview »**, puis **« final »** pour le rendu final.

## Pourquoi en global et pas dans ce dépôt ?

Le reel editor est un **outil vidéo généraliste** : son installateur officiel le place dans
`~/.claude/skills/` pour qu'il soit disponible dans **tous** tes projets Claude Code, et son
`doctor.sh` cherche justement HyperFrames dans `~/.claude/skills/hyperframes`. L'installer
uniquement dans ce dépôt de trading le limiterait à ce seul projet — ce n'est pas l'objectif.
Ce dossier ne contient donc que l'**installateur**, pas une copie de la skill.

## Notes

- Projet jeune (early-stage) selon l'auteur. Les polices et animations de texte côté CapCut
  demandent encore un réglage manuel.
- Licence du projet source : MIT.
