from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path


def icon_dir() -> Path:
    """On-disk icons directory (works for editable install and frozen bundles)."""
    return Path(__file__).resolve().parent / "assets" / "icons"


def icon_path(name: str = "arenatalk.png") -> Path | None:
    path = icon_dir() / name
    return path if path.is_file() else None


@lru_cache(maxsize=1)
def window_icon_path() -> Path | None:
    """Best icon for QWindow / taskbar (prefer 256, then master PNG, then ICO)."""
    for name in ("arenatalk-256.png", "arenatalk.png", "arenatalk.ico"):
        found = icon_path(name)
        if found is not None:
            return found
    return None


# UI font family, resolved per platform.
#
# "Sans" is a fontconfig alias and only resolves on Linux; on Windows and macOS
# Qt falls back to an arbitrary default, which also drops Korean glyphs on some
# systems. Name a real Korean-capable family per platform instead.
if sys.platform.startswith("win"):
    UI_FONT_FAMILY = "Malgun Gothic"
elif sys.platform == "darwin":
    UI_FONT_FAMILY = "Apple SD Gothic Neo"
else:
    UI_FONT_FAMILY = "Sans"

# CSS stack for Qt stylesheets — every platform's Korean face, then generics.
UI_FONT_STACK = (
    '"Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", '
    '"Apple SD Gothic Neo", "Noto Sans", Sans-Serif'
)
