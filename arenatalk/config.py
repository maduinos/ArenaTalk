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


_ATTACH_PARENT_PROCESS = -1


def _attach_parent_console() -> bool:
    """Borrow back the terminal that launched a windowed Windows build.

    The .exe is linked for the GUI subsystem so a double-click does not flash
    a console behind the window. That leaves the CLI subcommands with nowhere
    to print, so when the process *was* started from a terminal, attach to it.
    Returns False off Windows, and when there is no parent console to take.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        return bool(ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))
    except (AttributeError, OSError, ValueError):
        return False


def _open_console_stream():
    """A UTF-8 writer onto the attached console, or None if it cannot open."""
    try:
        return open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None


def setup_stdio() -> None:
    """Make stdout/stderr safe to write Korean to, whatever launched us.

    Three cases have to end with usable streams:

    - a normal console build, where the streams exist but default to the ANSI
      code page (cp1252, or cp949 on Korean Windows) and raise
      UnicodeEncodeError on so much as ``--help``;
    - a windowed build run from a terminal, where the streams are absent until
      we reattach to the caller's console;
    - a windowed build run by double-click, where there is no console at all
      and every write must go quietly nowhere rather than crash rich.

    Call this before the first write.
    """
    if sys.stdout is None or sys.stderr is None:
        if _attach_parent_console():
            for name in ("stdout", "stderr"):
                if getattr(sys, name, None) is None:
                    stream = _open_console_stream()
                    if stream is not None:
                        setattr(sys, name, stream)

    # Still missing means a double-click with no console anywhere. rich reads
    # attributes off the stream, so hand it a sink rather than None.
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
