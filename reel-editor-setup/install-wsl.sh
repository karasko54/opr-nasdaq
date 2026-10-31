#!/usr/bin/env bash
# =============================================================================
# Editeur de reels - installation DANS WSL / Ubuntu (Windows)
#
# A LANCER DANS LE TERMINAL UBUNTU (pas dans PowerShell), en une fois :
#     bash install-wsl.sh
# ou, sans avoir le depot :
#     curl -fsSL https://raw.githubusercontent.com/karasko54/opr-nasdaq/claude/install-reel-editor-4tpmvh/reel-editor-setup/install-wsl.sh | bash
#
# Installe : ffmpeg, Node.js LTS, la skill reel-editor + sa dependance
# hyperframes, le moteur d'animation, puis lance le doctor.
# =============================================================================
set -e

echo "============================================"
echo " Editeur de reels - installation (WSL/Ubuntu)"
echo "============================================"

echo ">>> 1/5  Outils systeme (ffmpeg, python3, git...)"
sudo apt-get update -y
sudo apt-get install -y ffmpeg python3 python3-pip curl git perl

echo ">>> 2/5  Node.js LTS"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
echo "    node $(node -v)  /  npm $(npm -v)"

echo ">>> 3/5  Skills (hyperframes + reel-editor)"
npx -y skills add heygen-com/hyperframes -g -y
npx -y skills add nicolapocock/claude-reel-editor -g -y

echo ">>> 4/5  Moteur d'animation"
REEL=""
for d in "$HOME/.claude/skills/reel-editor" "$HOME/.agents/skills/reel-editor"; do
  [ -d "$d" ] && REEL="$d" && break
done
if [ -n "$REEL" ]; then
  bash "$REEL/scripts/setup_engine.sh"
else
  echo "    (!) skill reel-editor introuvable apres installation - verifie la sortie ci-dessus"
fi

echo ">>> 5/5  Verification (doctor)"
[ -n "$REEL" ] && bash "$REEL/scripts/doctor.sh" || true

cat <<'EOF'

============================================
 Outils + skill installes.
============================================
Il reste a installer Claude Code DANS WSL pour t'en servir
(abonnement Claude payant requis : Pro, Max... ; le plan gratuit ne suffit pas) :

    curl -fsSL https://claude.ai/install.sh | bash
    # ouvre un NOUVEAU terminal, puis :
    claude            # premiere fois : connexion dans le navigateur

Ensuite, depuis le terminal Ubuntu, lance "claude", donne ta video et dis
"edit this into a reel". Tes montages seront dans ~/reels (cote Linux).

Astuce : copie tes videos depuis Windows dans WSL avec, par exemple :
    cp /mnt/c/Users/TON_NOM/Videos/ma_video.mov ~/
EOF
