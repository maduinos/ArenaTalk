#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Build the Ubuntu amd64 ArenaTalk .deb with PyInstaller.

Usage:
  tools/build_ubuntu_deb.sh [--output-dir DIRECTORY]

Supported build hosts:
  Ubuntu 22.04 or 24.04 on x86_64. Build release artifacts on Ubuntu 22.04
  so the resulting binary can run on both supported Ubuntu releases.

Environment:
  ARENATALK_PYINSTALLER  Explicit path to the pyinstaller executable.
EOF
}

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
OUTPUT_DIR="$PROJECT_ROOT/dist/ubuntu"

while (($#)); do
    case "$1" in
        --output-dir)
            if (($# < 2)); then
                printf '%s\n' "--output-dir requires a directory" >&2
                exit 2
            fi
            OUTPUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ "$(uname -m)" != "x86_64" ]]; then
    printf '%s\n' "ArenaTalk packages currently support x86_64 (amd64) only." >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then
    printf '%s\n' "Cannot identify the build host: /etc/os-release is missing." >&2
    exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
    printf 'Unsupported build host: %s %s. Use Ubuntu 22.04 or 24.04.\n' \
        "${ID:-unknown}" "${VERSION_ID:-unknown}" >&2
    exit 1
fi
if [[ "$VERSION_ID" == "24.04" ]]; then
    printf '%s\n' \
        "warning: this build may require Ubuntu 24.04; use Ubuntu 22.04 for release artifacts" >&2
fi

if [[ -n "${ARENATALK_PYINSTALLER:-}" ]]; then
    PYINSTALLER_BIN="$ARENATALK_PYINSTALLER"
elif [[ -x "$PROJECT_ROOT/.venv/bin/pyinstaller" ]]; then
    PYINSTALLER_BIN="$PROJECT_ROOT/.venv/bin/pyinstaller"
elif command -v pyinstaller >/dev/null 2>&1; then
    PYINSTALLER_BIN="$(command -v pyinstaller)"
else
    printf '%s\n' \
        'PyInstaller is missing. Install the packaging extra: python3 -m pip install -e ".[gui,packaging]"' >&2
    exit 1
fi
if [[ ! -x "$PYINSTALLER_BIN" ]]; then
    printf 'PyInstaller is not executable: %s\n' "$PYINSTALLER_BIN" >&2
    exit 1
fi

ICON_PNG="$PROJECT_ROOT/arenatalk/assets/icons/arenatalk.png"
ICON_PIXMAP="$PROJECT_ROOT/arenatalk/assets/icons/arenatalk-256.png"
DESKTOP_FILE="$PROJECT_ROOT/packaging/arenatalk.desktop"
for required in "$ICON_PNG" "$ICON_PIXMAP" "$DESKTOP_FILE" \
    "$PROJECT_ROOT/packaging/arenatalk.spec" \
    "$PROJECT_ROOT/packaging/arenatalk_entry.py"; do
    if [[ ! -r "$required" ]]; then
        printf 'Required packaging input is missing: %s\n' "$required" >&2
        exit 1
    fi
done

VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"$/\1/p' "$PROJECT_ROOT/arenatalk/__init__.py")"
if [[ ! "$VERSION" =~ ^[0-9]+([.][0-9]+)*([+~.-][A-Za-z0-9.+~:-]+)?$ ]]; then
    printf 'Invalid ArenaTalk version: %s\n' "$VERSION" >&2
    exit 1
fi

BUILD_ROOT="$(mktemp -d -p "${TMPDIR:-/tmp}" arenatalk-deb.XXXXXXXX)"
cleanup() {
    local temp_parent
    temp_parent="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
    if [[ -n "${BUILD_ROOT:-}" && "$BUILD_ROOT" == "$temp_parent"/arenatalk-deb.* ]]; then
        rm -rf -- "$BUILD_ROOT"
    fi
}
trap cleanup EXIT

PYINSTALLER_DIST="$BUILD_ROOT/pyinstaller-dist"
PYINSTALLER_WORK="$BUILD_ROOT/pyinstaller-work"
STAGE_ROOT="$BUILD_ROOT/package"
BUNDLE_ROOT="$PYINSTALLER_DIST/arenatalk"

printf 'Building ArenaTalk %s with %s...\n' "$VERSION" "$PYINSTALLER_BIN"
"$PYINSTALLER_BIN" \
    --noconfirm \
    --clean \
    --distpath "$PYINSTALLER_DIST" \
    --workpath "$PYINSTALLER_WORK" \
    "$PROJECT_ROOT/packaging/arenatalk.spec"

if [[ ! -x "$BUNDLE_ROOT/arenatalk" ]]; then
    printf '%s\n' "PyInstaller did not create the expected one-folder bundle." >&2
    exit 1
fi

printf '%s\n' "Running frozen-bundle smoke tests..."
"$BUNDLE_ROOT/arenatalk" --help >/dev/null
"$BUNDLE_ROOT/arenatalk" backends >/dev/null
if [[ ! -r "$BUNDLE_ROOT/_internal/arenatalk/assets/icons/arenatalk.png" ]] \
    && [[ ! -r "$BUNDLE_ROOT/_internal/arenatalk/assets/icons/arenatalk-256.png" ]]; then
    # PyInstaller may nest package data under different layouts; accept either.
    icon_hit="$(find "$BUNDLE_ROOT" -type f \( -name 'arenatalk.png' -o -name 'arenatalk-256.png' \) | head -n 1 || true)"
    if [[ -z "$icon_hit" ]]; then
        printf '%s\n' "The frozen bundle is missing ArenaTalk icon assets." >&2
        exit 1
    fi
fi

install -d -m 0755 \
    "$STAGE_ROOT/DEBIAN" \
    "$STAGE_ROOT/usr/bin" \
    "$STAGE_ROOT/usr/lib/arenatalk" \
    "$STAGE_ROOT/usr/share/applications" \
    "$STAGE_ROOT/usr/share/doc/arenatalk" \
    "$STAGE_ROOT/usr/share/pixmaps"
cp -a "$BUNDLE_ROOT/." "$STAGE_ROOT/usr/lib/arenatalk/"
ln -s ../lib/arenatalk/arenatalk "$STAGE_ROOT/usr/bin/arenatalk"

install -m 0644 \
    "$DESKTOP_FILE" \
    "$STAGE_ROOT/usr/share/applications/arenatalk.desktop"
install -m 0644 "$ICON_PIXMAP" "$STAGE_ROOT/usr/share/pixmaps/arenatalk.png"
install -m 0644 "$PROJECT_ROOT/README.md" "$STAGE_ROOT/usr/share/doc/arenatalk/README.md"
if [[ -r "$PROJECT_ROOT/docs/UBUNTU_PACKAGING.md" ]]; then
    install -d -m 0755 "$STAGE_ROOT/usr/share/doc/arenatalk/docs"
    install -m 0644 \
        "$PROJECT_ROOT/docs/UBUNTU_PACKAGING.md" \
        "$STAGE_ROOT/usr/share/doc/arenatalk/docs/UBUNTU_PACKAGING.md"
fi
if [[ -r "$PROJECT_ROOT/CHANGELOG.md" ]]; then
    gzip -9n -c "$PROJECT_ROOT/CHANGELOG.md" \
        >"$STAGE_ROOT/usr/share/doc/arenatalk/changelog.gz"
else
    printf 'ArenaTalk %s\n' "$VERSION" \
        | gzip -9n >"$STAGE_ROOT/usr/share/doc/arenatalk/changelog.gz"
fi

# PyInstaller preserves the developer's cooperative umask on directories and
# source documents. Installed package content must never remain group-writable.
find "$STAGE_ROOT" -type d -exec chmod 0755 {} +
find "$STAGE_ROOT" -type f -exec chmod go-w {} +

# PyInstaller links the bundled runtime against the build host's glibc, so the
# required libc6 version is a property of the produced binaries, not a constant.
if ! command -v objdump >/dev/null 2>&1; then
    printf '%s\n' \
        "objdump is missing. Install binutils so the libc6 dependency can be derived." >&2
    exit 1
fi
LIBC_MINIMUM="$(
    set +o pipefail
    find "$STAGE_ROOT/usr/lib/arenatalk" -type f -exec objdump -p {} + 2>/dev/null \
        | grep -oE 'GLIBC_[0-9]+\.[0-9]+(\.[0-9]+)?' \
        | sort -u -V \
        | tail -n 1
)"
LIBC_MINIMUM="${LIBC_MINIMUM#GLIBC_}"
if [[ ! "$LIBC_MINIMUM" =~ ^[0-9]+\.[0-9]+(\.[0-9]+)?$ ]]; then
    printf 'Could not derive the required libc6 version from the staged bundle: %s\n' \
        "${LIBC_MINIMUM:-<empty>}" >&2
    exit 1
fi
printf 'Bundled binaries require libc6 >= %s.\n' "$LIBC_MINIMUM"

installed_size="$(du -sk "$STAGE_ROOT/usr" | awk '{print $1}')"
cat >"$STAGE_ROOT/DEBIAN/control" <<EOF
Package: arenatalk
Version: $VERSION
Section: games
Priority: optional
Architecture: amd64
Maintainer: ArenaTalk contributors <noreply@arenatalk.invalid>
Installed-Size: $installed_size
Depends: bash, ca-certificates, libc6 (>= $LIBC_MINIMUM), libegl1, libgl1,
 libstartup-notification0, libx11-6, libxcb1
Recommends: agentpet
Description: CharacterPet debate arena with Elo ranking
 ArenaTalk runs multi-character debates using CharacterPet-compatible
 sprites and personas, with Elo rankings. The package bundles its private
 Python and Qt runtime. Character library path is shared with AgentPet
 (~/.config/agentpet/state.json characters.path) when available.
EOF
chmod 0644 "$STAGE_ROOT/DEBIAN/control"

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"
PACKAGE_PATH="$OUTPUT_DIR/arenatalk_${VERSION}_amd64.deb"
TEMP_PACKAGE="$BUILD_ROOT/arenatalk_${VERSION}_amd64.deb"
dpkg-deb --root-owner-group --build "$STAGE_ROOT" "$TEMP_PACKAGE"
mv -f "$TEMP_PACKAGE" "$PACKAGE_PATH"

printf '\nPackage: %s\n' "$PACKAGE_PATH"
sha256sum "$PACKAGE_PATH"
