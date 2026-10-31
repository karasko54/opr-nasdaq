#!/usr/bin/env bash
# =============================================================================
# Installation de claude-reel-editor (skill Claude Code)
# Source : https://github.com/nicolapocock/claude-reel-editor
#
# A LANCER SUR TON MAC (pas dans une session cloud) :
#     bash reel-editor-setup/install.sh
#
# Ce que fait ce script :
#   1. Installe la skill "hyperframes" (graphismes + sous-titres) en GLOBAL
#   2. Installe la skill "reel-editor" en GLOBAL
#   3. Prepare le moteur d'animation (gsap + effets sonores + motions)
#   4. Lance le doctor qui verifie que tout est pret
#
# "Global" = ~/.claude/skills/ : la skill est dispo dans TOUS tes projets
# Claude Code, pas seulement dans ce repo. C'est le bon emplacement pour un
# outil video generaliste.
# =============================================================================
set -euo pipefail

echo "=============================================="
echo " Installation du Reel Editor pour Claude Code"
echo "=============================================="
echo

# --- Pre-requis minimaux -----------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { echo "MANQUANT: $1 - $2"; MISS=1; }; }
MISS=0
need node   "installe Node LTS depuis https://nodejs.org"
need npx    "fourni avec Node LTS (https://nodejs.org)"
need ffmpeg "macOS: brew install ffmpeg"
if [ "$MISS" = 1 ]; then
  echo
  echo "Installe les outils manquants ci-dessus, puis relance ce script."
  exit 1
fi

if [ "$(uname)" != "Darwin" ]; then
  echo "AVERTISSEMENT: macOS est la seule plateforme testee par l'auteur."
  echo "               Windows / Linux ne sont pas garantis."
  echo
fi

# --- 1 & 2 : installation des skills en global -------------------------------
# -g = global (~/.claude/skills) ; -y = non interactif
echo ">>> Installation de la skill hyperframes (dependance)..."
npx -y skills add heygen-com/hyperframes -g -y

echo
echo ">>> Installation de la skill reel-editor..."
npx -y skills add nicolapocock/claude-reel-editor -g -y

# --- 3 : localisation de la skill installee ----------------------------------
REEL_DIR=""
for d in "$HOME/.claude/skills/reel-editor" "$HOME/.agents/skills/reel-editor"; do
  [ -d "$d" ] && REEL_DIR="$d" && break
done
if [ -z "$REEL_DIR" ]; then
  echo "ERREUR: la skill reel-editor est introuvable apres installation."
  echo "        Verifie la sortie ci-dessus, ou lance: npx skills list"
  exit 1
fi
echo
echo ">>> Skill installee dans : $REEL_DIR"

# --- 4 : moteur d'animation + doctor -----------------------------------------
echo
echo ">>> Preparation du moteur d'animation (gsap + effets sonores + motions)..."
bash "$REEL_DIR/scripts/setup_engine.sh"

echo
echo ">>> Verification finale (doctor)..."
bash "$REEL_DIR/scripts/doctor.sh" || true

cat <<'EOF'

==============================================
 Termine.
==============================================

Optionnel - CapCut (export en brouillon editable) :
    claude mcp add --transport http --scope user capcut_creation https://www.capcut.com/api/external_mcp
  Puis dans Claude Code : tape /mcp, choisis capcut_creation, connecte-toi.
  (Indispo aux USA et en Inde ; OK Australie/UK/Europe/Asie.)

Pour t'en servir : ouvre Claude Code, donne-lui ta video et dis
"edit this into a reel". La 1ere fois il te posera ~5 questions
pour creer ton fichier de marque (couleurs, polices, style de
sous-titres, sons, call-to-action) dans ~/reels/.
EOF
