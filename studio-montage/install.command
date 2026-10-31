#!/bin/bash
# =============================================================================
# Studio Montage - installation (macOS)
# Double-clique ce fichier dans le Finder, OU lance :  bash studio-montage/install.command
#
# Ce script :
#   1. copie le studio dans ~/StudioMontage
#   2. cree une icone "Studio Montage" sur ton Bureau (double-clic -> navigateur)
#   3. ouvre le studio tout de suite
# Rien n'est envoye sur internet : le studio tourne en local (127.0.0.1).
# =============================================================================
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/StudioMontage"
DESKTOP="$HOME/Desktop"
APP="$DESKTOP/Studio Montage.app"

echo "============================================"
echo " Installation du Studio Montage"
echo "============================================"

# --- trouver node ------------------------------------------------------------
find_node() {
  command -v node 2>/dev/null && return 0
  for p in /opt/homebrew/bin/node /usr/local/bin/node "$HOME"/.nvm/versions/node/*/bin/node /usr/bin/node; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}
if ! NODE="$(find_node | head -1)" || [ -z "$NODE" ]; then
  echo "Node.js est introuvable. Installe-le depuis https://nodejs.org puis relance ce script."
  osascript -e 'display dialog "Node.js est introuvable. Installe-le depuis nodejs.org puis relance l'\''installation." buttons {"OK"} default button 1 with icon stop' >/dev/null 2>&1 || true
  exit 1
fi
echo "Node trouve : $NODE"

# --- 1. copier l'app ---------------------------------------------------------
echo ">>> Copie du studio dans $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/app" "$DEST/seeds"
cp -R "$SRC/app" "$DEST/app"
cp -R "$SRC/seeds" "$DEST/seeds"

# --- 2. creer l'icone sur le Bureau -----------------------------------------
echo ">>> Creation de l'icone sur le Bureau"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Studio Montage</string>
  <key>CFBundleDisplayName</key><string>Studio Montage</string>
  <key>CFBundleIdentifier</key><string>com.studiomontage.launcher</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>StudioMontage</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
</dict></plist>
PLIST

cat > "$APP/Contents/MacOS/StudioMontage" <<'LAUNCH'
#!/bin/bash
DIR="$HOME/StudioMontage"
PORT_FILE="$DIR/.port"
find_node(){
  command -v node 2>/dev/null && return 0
  for p in /opt/homebrew/bin/node /usr/local/bin/node "$HOME"/.nvm/versions/node/*/bin/node /usr/bin/node; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  return 1
}
open_studio(){ P="$(cat "$PORT_FILE" 2>/dev/null || echo 4178)"; open "http://127.0.0.1:$P"; }
# deja demarre ?
if [ -f "$PORT_FILE" ] && curl -s "http://127.0.0.1:$(cat "$PORT_FILE")/api/ping" >/dev/null 2>&1; then open_studio; exit 0; fi
NODE="$(find_node | head -1)"
if [ -z "$NODE" ]; then
  osascript -e 'display dialog "Node.js est introuvable. Installe-le depuis nodejs.org." buttons {"OK"} default button 1 with icon stop' >/dev/null 2>&1
  exit 1
fi
cd "$DIR" || exit 1
rm -f "$PORT_FILE"
nohup "$NODE" app/server.js >> "$DIR/studio.log" 2>&1 &
for i in $(seq 1 60); do
  [ -f "$PORT_FILE" ] && curl -s "http://127.0.0.1:$(cat "$PORT_FILE")/api/ping" >/dev/null 2>&1 && break
  sleep 0.25
done
open_studio
LAUNCH

chmod +x "$APP/Contents/MacOS/StudioMontage"
# rafraichir l'icone dans le Finder
touch "$APP"

# --- 3. premier lancement ----------------------------------------------------
echo ">>> Demarrage du studio"
rm -f "$DEST/.port"
cd "$DEST"
nohup "$NODE" app/server.js >> "$DEST/studio.log" 2>&1 &
for i in $(seq 1 60); do [ -f "$DEST/.port" ] && break; sleep 0.25; done
P="$(cat "$DEST/.port" 2>/dev/null || echo 4178)"
open "http://127.0.0.1:$P" 2>/dev/null || true

cat <<EOF

============================================
 Termine.
============================================
Le studio est ouvert dans ton navigateur : http://127.0.0.1:$P
Une icone "Studio Montage" est sur ton Bureau : double-clique dessus quand tu veux.

Pour l'arreter : ferme l'onglet (le petit serveur local peut rester actif en
fond, c'est sans danger ; il repart tout seul au prochain double-clic).
EOF
