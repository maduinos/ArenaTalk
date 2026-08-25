# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for DebateSim GUI.

Build:
  cd DebateSim
  pyinstaller packaging/debatesim.spec
"""

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent
ICON_PNG = PROJECT_ROOT / "debatesim" / "assets" / "icons" / "debatesim.png"
ICON_ICO = PROJECT_ROOT / "debatesim" / "assets" / "icons" / "debatesim.ico"

analysis = Analysis(
    [str(PROJECT_ROOT / "packaging" / "debatesim_entry.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (
            str(PROJECT_ROOT / "debatesim" / "assets"),
            "debatesim/assets",
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
    [],
    exclude_binaries=True,
    name="debatesim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO if ICON_ICO.is_file() else ICON_PNG),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="debatesim",
)
