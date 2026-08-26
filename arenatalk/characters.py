from __future__ import annotations

import json
import os
from pathlib import Path

from arenatalk.config import env_get
from arenatalk.models import Character, Persona


def _agentpet_state_path() -> Path:
    xdg = (os.environ.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        root = Path(xdg).expanduser()
        if root.is_absolute():
            return root / "agentpet" / "state.json"
    return Path.home() / ".config" / "agentpet" / "state.json"


def _fallback_character_root() -> Path:
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        root = Path(xdg).expanduser()
        if root.is_absolute():
            return root / "agentpet" / "characters"
    return Path.home() / ".local" / "share" / "agentpet" / "characters"


def _read_agentpet_characters_path() -> Path | None:
    """Read AgentPet ``characters.path`` without importing the agentpet package."""
    path = _agentpet_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    block = payload.get("characters")
    if not isinstance(block, dict):
        return None
    raw = block.get("path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return Path(raw).expanduser()


def sync_character_library(path: Path | None) -> Path:
    """Persist the CharacterPet-compatible library in AgentPet settings."""
    try:
        from agentpet.character_packages import set_character_library

        if path is None:
            return set_character_library(None)
        resolved = path.expanduser().resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(f"캐릭터 폴더가 없습니다: {resolved}")
        return set_character_library(resolved)
    except ImportError:
        return _sync_agentpet_state_file(path)


def _sync_agentpet_state_file(path: Path | None) -> Path:
    """Best-effort write when the agentpet package is not importable."""
    state_path = _agentpet_state_path()
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {"schema_version": 1}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        payload = {"schema_version": 1}

    if path is None:
        payload["characters"] = {"path": None}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return _fallback_character_root()

    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise NotADirectoryError(f"캐릭터 폴더가 없습니다: {resolved}")
    payload["characters"] = {"path": str(resolved)}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return resolved


def character_roots(root: Path | None = None) -> list[Path]:
    """Return CharacterPet-compatible character search roots.

    Priority:
    1. Explicit ``root`` (CLI ``--characters`` / GUI one-shot)
    2. ``ARENATALK_CHARACTERS`` env
    3. ``AGENTPET_CHARACTERS`` env
    4. AgentPet ``characters.path``
    5. XDG default ``~/.local/share/agentpet/characters``
    """
    if root is not None:
        return [root.expanduser()]

    env = env_get("ARENATALK_CHARACTERS")
    if env:
        return [Path(env).expanduser()]

    agentpet_env = env_get("AGENTPET_CHARACTERS")
    if agentpet_env:
        return [Path(agentpet_env).expanduser()]

    shared = _read_agentpet_characters_path()
    if shared is not None:
        return [shared]

    try:
        from agentpet.character_packages import resolve_character_library

        return [resolve_character_library()]
    except ImportError:
        return [_fallback_character_root()]


def active_character_root(root: Path | None = None) -> Path:
    return character_roots(root)[0]


def load_characters(root: Path | None = None) -> list[Character]:
    roots = character_roots(root)
    found: dict[str, Character] = {}
    for base in roots:
        if not base or not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            manifest = child / "pet.json"
            if not child.is_dir() or not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            cid = str(data.get("id") or child.name)
            persona_raw = data.get("persona")
            if not isinstance(persona_raw, dict):
                continue
            found[cid] = Character(
                id=cid,
                display_name=str(data.get("displayName") or cid),
                description=str(data.get("description") or ""),
                persona=Persona.from_dict(persona_raw),
                root=str(child),
            )
    return list(found.values())
