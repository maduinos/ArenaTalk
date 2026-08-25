from __future__ import annotations

import json
import os
from pathlib import Path

from arenatalk.models import Character, Persona

_DEFAULT_ROOTS = (
    Path("/home/whjeong/00_Github/maduinos/CharacterPet/characters"),
    Path.home() / ".local/share/agentpet/characters",
)


def character_roots() -> list[Path]:
    env = os.environ.get("AREATALK_CHARACTERS")
    if env:
        return [Path(env).expanduser()]
    return [p for p in _DEFAULT_ROOTS if p.is_dir()]


def load_characters(root: Path | None = None) -> list[Character]:
    roots = [root] if root else character_roots()
    found: dict[str, Character] = {}
    for base in roots:
        if not base or not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            manifest = child / "pet.json"
            if not child.is_dir() or not manifest.is_file():
                continue
            data = json.loads(manifest.read_text(encoding="utf-8"))
            cid = str(data.get("id") or child.name)
            persona_raw = data.get("persona")
            if not isinstance(persona_raw, dict):
                # installed AgentPet copy may lag CharacterPet; skip until synced
                continue
            found[cid] = Character(
                id=cid,
                display_name=str(data.get("displayName") or cid),
                description=str(data.get("description") or ""),
                persona=Persona.from_dict(persona_raw),
                root=str(child),
            )
    return list(found.values())
