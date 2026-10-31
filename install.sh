#!/bin/bash
# Installs Bunny Deck's user-local launcher and icon. Touches only:
#   ~/.local/share/applications/org.bunnydeck.BunnyDeck.desktop
#   ~/.local/share/icons/hicolor/scalable/apps/bunny-deck.svg
# and refreshes the user icon/desktop caches. No root required.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor"

chmod +x "$HERE/bunny-deck" "$HERE/bunnydeck/askpass_helper.py" "$HERE/build-native.sh"
# The QTermWidget Python binding is built locally (distributions ship none).
if ! (cd "$HERE" && python3 -c "import bunnydeck.native") 2>/dev/null; then
  echo "Building the QTermWidget binding..."
  "$HERE/build-native.sh"
fi
mkdir -p "$APPS" "$ICONS/scalable/apps"
install -m 644 "$HERE/data/bunny-deck.svg" "$ICONS/scalable/apps/bunny-deck.svg"
sed "s|@EXEC@|$HERE/bunny-deck|" "$HERE/data/org.bunnydeck.BunnyDeck.desktop.in" \
  > "$APPS/org.bunnydeck.BunnyDeck.desktop"
chmod 644 "$APPS/org.bunnydeck.BunnyDeck.desktop"

command -v desktop-file-validate >/dev/null && desktop-file-validate "$APPS/org.bunnydeck.BunnyDeck.desktop"
if [ -f "$ICONS/index.theme" ] || [ -f "$ICONS/icon-theme.cache" ]; then
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t -q "$ICONS" || true
fi
command -v update-desktop-database >/dev/null && update-desktop-database -q "$APPS" || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
echo "Installed: $APPS/org.bunnydeck.BunnyDeck.desktop"
echo "Installed: $ICONS/scalable/apps/bunny-deck.svg"
