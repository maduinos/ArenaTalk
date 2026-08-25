from __future__ import annotations

import re
from pathlib import Path

# Readable text / source attachments. Images and binaries are skipped with a note.
_TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".toml",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
    ".conf",
    ".log",
    ".rst",
    ".html",
    ".htm",
    ".xml",
    ".py",
    ".pyi",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cc",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".css",
    ".scss",
    ".r",
    ".R",
}

_MAX_FILES = 8
_MAX_BYTES = 400_000
_MAX_CHARS = 14_000

# Absolute / home / relative unix paths, optional Windows drive paths.
_PATH_RE = re.compile(
    r"(?P<q>[\"'])(?P<p1>(?:~|/|\./|\.\./|[A-Za-z]:[\\/])[^\"']+)(?P=q)"
    r"|(?P<p2>(?:~/|/|\./|\.\./)[^\s\"'<>|]+|[A-Za-z]:\\[^\s\"'<>|]+)"
)


def extract_local_paths(text: str) -> list[Path]:
    """Pull candidate filesystem paths from free-form topic / inject text."""
    raw = text or ""
    found: list[Path] = []
    seen: set[str] = set()
    for m in _PATH_RE.finditer(raw):
        token = (m.group("p1") or m.group("p2") or "").strip().rstrip(".,;:)]}>")
        if not token:
            continue
        try:
            path = Path(token).expanduser()
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            else:
                path = path.resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        found.append(path)
    return found


def load_path_attachments(text: str) -> tuple[str, list[str]]:
    """Read files referenced in ``text`` and format a citeable materials block.

    Returns ``(markdown, status_lines)``. Empty markdown when nothing usable.
    """
    paths = extract_local_paths(text)
    if not paths:
        return "", []

    blocks: list[str] = [
        "## 사용자 지정 로컬 파일 (경로로 첨부 — 반드시 인용·반영하라)",
    ]
    status: list[str] = []
    used = 0

    for path in paths:
        if used >= _MAX_FILES:
            status.append(f"한도 초과로 생략: {path}")
            continue
        if not path.exists():
            status.append(f"없음: {path}")
            blocks.append(f"- (없음) `{path}`")
            continue
        if path.is_dir():
            listing = _list_dir(path)
            blocks.append(f"### 디렉터리 `{path}`\n{listing}")
            status.append(f"디렉터리 목록: {path}")
            used += 1
            continue
        if not path.is_file():
            status.append(f"파일 아님: {path}")
            continue

        suffix = path.suffix.lower()
        if suffix and suffix not in _TEXT_SUFFIXES:
            status.append(f"텍스트 아님(스킵): {path.name}")
            blocks.append(
                f"- (미지원 형식) `{path}` — 이미지·바이너리는 아직 본문 첨부 불가"
            )
            used += 1
            continue

        try:
            size = path.stat().st_size
        except OSError as exc:
            status.append(f"읽기 실패: {path} ({exc})")
            continue
        if size > _MAX_BYTES:
            status.append(f"너무 큼(스킵): {path.name} ({size}B)")
            blocks.append(f"- (용량 초과) `{path}` — {_MAX_BYTES}B 이하만 첨부")
            used += 1
            continue

        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            status.append(f"읽기 실패: {path} ({exc})")
            continue

        body = raw.strip()
        if not body:
            status.append(f"빈 파일: {path}")
            blocks.append(f"- (빈 파일) `{path}`")
            used += 1
            continue
        truncated = False
        if len(body) > _MAX_CHARS:
            body = body[:_MAX_CHARS].rstrip() + "\n…(이하 생략)"
            truncated = True
        fence = "```"
        # Avoid breaking the fence if file contains triple backticks
        if "```" in body:
            fence = "````"
        blocks.append(
            f"### 파일 `{path}`"
            + (" (일부)" if truncated else "")
            + f"\n{fence}\n{body}\n{fence}"
        )
        status.append(f"첨부: {path.name}" + (" (일부)" if truncated else ""))
        used += 1

    if used == 0 and len(blocks) == 1:
        return "", status
    return "\n".join(blocks), status


def expand_text_with_attachments(text: str) -> tuple[str, list[str]]:
    """Keep original note and append loaded file bodies when paths are present."""
    note = (text or "").strip()
    if not note:
        return "", []
    attached, status = load_path_attachments(note)
    if not attached:
        return note, status
    return f"{note}\n\n{attached}", status


def _list_dir(path: Path, *, limit: int = 40) -> str:
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        return f"(목록 실패: {exc})"
    lines: list[str] = []
    for i, entry in enumerate(entries):
        if i >= limit:
            lines.append(f"- … 외 {len(entries) - limit}개")
            break
        kind = "dir" if entry.is_dir() else "file"
        lines.append(f"- [{kind}] {entry.name}")
    return "\n".join(lines) if lines else "(비어 있음)"
