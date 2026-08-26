"""Frozen / PyInstaller entry for ArenaTalk (GUI + CLI)."""

from arenatalk.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
