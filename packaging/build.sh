#!/usr/bin/env bash
# Build a windowed ArenaTalk binary with app icon (PyInstaller).
# For the Ubuntu .deb (recommended release path), use tools/build_ubuntu_deb.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! python3 -c "import PyInstaller" 2>/dev/null; then
  echo "PyInstaller 필요: pip install -e '.[gui,packaging]'  또는  pip install pyinstaller"
  exit 1
fi

python3 -m PyInstaller --noconfirm --clean packaging/arenatalk.spec
echo
echo "Built: $ROOT/dist/arenatalk/arenatalk"
echo "Ubuntu .deb: tools/build_ubuntu_deb.sh"
echo "Desktop install (optional): bash packaging/install-desktop.sh"
