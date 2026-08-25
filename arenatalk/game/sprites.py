from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImageReader, QPixmap

FRAME_WIDTH = 192
FRAME_HEIGHT = 208
FRAME_COLUMNS = 8
SHEET_W = FRAME_WIDTH * FRAME_COLUMNS
SHEET_H = FRAME_HEIGHT * 11

_ANIM_ROWS = {
    "idle": (0, 6, 3.0),
    "running-right": (1, 8, 8.0),
    "running-left": (2, 8, 8.0),
    "waving": (3, 4, 6.0),
    "jumping": (4, 5, 7.0),
    "failed": (5, 8, 6.0),
    "waiting": (6, 6, 5.0),
    "running": (7, 6, 8.0),
    "review": (8, 6, 5.0),
    "reading": (9, 8, 5.0),
    "researching": (10, 8, 5.0),
}


@dataclass(frozen=True)
class SpriteAnim:
    name: str
    sheet: Path
    row: int
    frames: int
    fps: float


@dataclass(frozen=True)
class SpriteCharacter:
    character_id: str
    display_name: str
    root: Path
    sheet: Path
    animations: dict[str, SpriteAnim]


def load_sprite_character(root: Path) -> SpriteCharacter | None:
    manifest = root / "pet.json"
    sheet_path = root / "spritesheet.webp"
    if not manifest.is_file() or not sheet_path.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cid = str(data.get("id") or root.name)
    name = str(data.get("displayName") or cid)
    rel = str(data.get("spritesheetPath") or "spritesheet.webp")
    sheet = (root / rel).resolve()
    if not sheet.is_file():
        sheet = sheet_path
    reader = QImageReader(str(sheet))
    size = reader.size()
    if not size.isValid() or size.width() != SHEET_W or size.height() != SHEET_H:
        return None
    anims = {
        anim_name: SpriteAnim(anim_name, sheet, row, frames, fps)
        for anim_name, (row, frames, fps) in _ANIM_ROWS.items()
    }
    return SpriteCharacter(cid, name, root, sheet, anims)


@lru_cache(maxsize=48)
def _sheet_pixmap(path: str) -> QPixmap:
    return QPixmap(path)


def frame_pixmap(anim: SpriteAnim, index: int, scale: float = 0.45) -> QPixmap:
    sheet = _sheet_pixmap(str(anim.sheet))
    col = index % anim.frames
    x = col * FRAME_WIDTH
    y = anim.row * FRAME_HEIGHT
    cropped = sheet.copy(x, y, FRAME_WIDTH, FRAME_HEIGHT)
    if scale == 1.0:
        return cropped
    w = max(1, int(FRAME_WIDTH * scale))
    h = max(1, int(FRAME_HEIGHT * scale))
    return cropped.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
