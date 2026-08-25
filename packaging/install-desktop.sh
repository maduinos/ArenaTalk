#!/usr/bin/env bash
# Install DebateSim desktop launcher + icons for the current user.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICON_SRC="$ROOT/debatesim/assets/icons"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"

mkdir -p "$APP_DIR"
for size in 16 24 32 48 64 128 256 512; do
  dest="$ICON_BASE/${size}x${size}/apps"
  mkdir -p "$dest"
  if [[ -f "$ICON_SRC/debatesim-${size}.png" ]]; then
    cp -f "$ICON_SRC/debatesim-${size}.png" "$dest/debatesim.png"
  fi
done
# scalable fallback
mkdir -p "$ICON_BASE/512x512/apps"
cp -f "$ICON_SRC/debatesim.png" "$ICON_BASE/512x512/apps/debatesim.png"

# Point Exec at the installed debatesim on PATH when possible.
EXEC_BIN="$(command -v debatesim || true)"
if [[ -z "$EXEC_BIN" ]]; then
  EXEC_BIN="debatesim"
fi
sed "s|^Exec=.*|Exec=${EXEC_BIN}|" "$ROOT/packaging/debatesim.desktop" \
  > "$APP_DIR/debatesim.desktop"
chmod 644 "$APP_DIR/debatesim.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICON_BASE" >/dev/null 2>&1 || true
fi

echo "Installed desktop entry: $APP_DIR/debatesim.desktop"
echo "Icons under: $ICON_BASE/*/apps/debatesim.png"
echo "Exec: $EXEC_BIN"
