#!/usr/bin/env bash
# Install / reinstall ArenaTalk locally.
# After pip + desktop: check paid agent CLIs; if none, auto-install free top-3.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> ArenaTalk install (pip + desktop + agents)"
exec python3 -m arenatalk install "$@"
