# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows ArenaTalk build.

One FILE rather than the one-folder layout the .deb uses: Windows has no
package manager to unpack a 300-file directory into place, so a single
downloadable .exe is the shape that actually reaches users. It costs startup
time — the bundle unpacks to a temp dir on every launch — which is why Linux
keeps the one-folder spec.

Build (on Windows):
  pyinstaller packaging/arenatalk-windows.spec
"""

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent
ICON_ICO = PROJECT_ROOT / "arenatalk" / "assets" / "icons" / "arenatalk.ico"
ICON_PNG = PROJECT_ROOT / "arenatalk" / "assets" / "icons" / "arenatalk.png"

analysis = Analysis(
    [str(PROJECT_ROOT / "packaging" / "arenatalk_entry.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (
            str(PROJECT_ROOT / "arenatalk" / "assets"),
            "arenatalk/assets",
        ),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    # One-file: the binaries and datas ride inside the executable itself
    # instead of being COLLECT()ed into a sibling folder.
    analysis.binaries,
    analysis.datas,
    [],
    name="arenatalk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # True so CLI subcommands (list, debate, …) print to the terminal.
    # The cost is a console window behind the GUI on a double-click launch.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO if ICON_ICO.is_file() else ICON_PNG),
)
