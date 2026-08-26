"""Small env helpers for ArenaTalk (no separate character-library settings)."""

from __future__ import annotations

import os
from typing import Mapping


def env_get(*names: str, default: str = "") -> str:
    """Return the first set environment variable among ``names``."""
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def env_mapping() -> Mapping[str, str]:
    return os.environ
