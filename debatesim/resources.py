from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def icon_dir() -> Path:
    """On-disk icons directory (works for editable install and frozen bundles)."""
    return Path(__file__).resolve().parent / "assets" / "icons"


def icon_path(name: str = "debatesim.png") -> Path | None:
    path = icon_dir() / name
    return path if path.is_file() else None


@lru_cache(maxsize=1)
def window_icon_path() -> Path | None:
    """Best icon for QWindow / taskbar (prefer 256, then master PNG, then ICO)."""
    for name in ("debatesim-256.png", "debatesim.png", "debatesim.ico"):
        found = icon_path(name)
        if found is not None:
            return found
    return None
