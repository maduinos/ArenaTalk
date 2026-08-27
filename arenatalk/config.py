"""Small env helpers for ArenaTalk (no separate character-library settings)."""

from __future__ import annotations

import os
import sys
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


def force_utf8_stdio() -> None:
    """Write UTF-8 to stdout/stderr regardless of the console's code page.

    Every user-facing string in this app is Korean. On Windows the standard
    streams default to the ANSI code page (cp1252, or cp949 on Korean
    installs), so printing so much as ``--help`` raises UnicodeEncodeError.
    Call this before the first write.

    The streams are absent in a windowed frozen build and are not always
    reconfigurable, so failure here is not worth crashing over.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
