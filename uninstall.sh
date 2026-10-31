#!/bin/bash
# Removes only the files install.sh created. Leaves ~/.config/bunny-deck alone.
set -euo pipefail
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -f "$DATA/applications/org.bunnydeck.BunnyDeck.desktop" \
      "$DATA/icons/hicolor/scalable/apps/bunny-deck.svg"
if [ -f "$DATA/icons/hicolor/icon-theme.cache" ]; then
  gtk-update-icon-cache -f -t -q "$DATA/icons/hicolor" || true
fi
update-desktop-database -q "$DATA/applications" 2>/dev/null || true
kbuildsycoca6 >/dev/null 2>&1 || true
echo "Bunny Deck launcher and icon removed."
