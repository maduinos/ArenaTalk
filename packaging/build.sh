#!/usr/bin/env bash
# Build a windowed ArenaTalk binary with app icon (PyInstaller).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! python3 -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller 필요: pip install -e '.[packaging]'  또는  pip install pyinstaller"
  exit 1
fi

python3 -m PyInstaller --noconfirm --clean packaging/arenatalk.spec
echo
echo "Built: $ROOT/dist/arenatalk/arenatalk"
echo "Desktop install (optional): bash packaging/install-desktop.sh"
