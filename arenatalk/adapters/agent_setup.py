from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from arenatalk.adapters.cli_agents import (
    FREE_PROVIDER_NAMES,
    PAID_PROVIDER_NAMES,
    ProviderInfo,
    discover_providers,
)

INSTALL_TIMEOUT = 600
SCRIPT_MAX_BYTES = 2 * 1024 * 1024

# Top-3 popular free agent CLIs (auto-install when no paid CLI is on PATH).
FREE_INSTALL_ORDER: tuple[str, ...] = ("gemini", "qwen", "ollama")

_INSTALL_URLS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "ollama": ("https://ollama.com/install.sh", "sh", ()),
}

_NPM_PACKAGES: dict[str, str] = {
    "gemini": "@google/gemini-cli",
    "qwen": "@qwen-code/qwen-code@latest",
}

_OLLAMA_MODEL = "qwen2.5-coder:7b"


@dataclass
class EnsureResult:
    tier: str  # paid | free | none
    providers: list[ProviderInfo]
    installed: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def preferred_providers(*, probe_version: bool = True) -> list[ProviderInfo]:
    """Return paid CLIs when present, otherwise discovered free CLIs."""
    paid = discover_providers(
        names=PAID_PROVIDER_NAMES, probe_version=probe_version
    )
    if paid:
        return paid
    return discover_providers(names=FREE_PROVIDER_NAMES, probe_version=probe_version)


def ensure_agent_clis(*, auto_install: bool = True) -> EnsureResult:
    """Check paid CLIs; if none, optionally auto-install free top-3 (gemini/qwen/ollama)."""
    messages: list[str] = []
    installed: list[str] = []

    paid = discover_providers(names=PAID_PROVIDER_NAMES)
    if paid:
        messages.append(
            f"유료 에이전트 {len(paid)}개 감지 → 그대로 사용: "
            + ", ".join(p.display_name or p.name for p in paid)
        )
        return EnsureResult(tier="paid", providers=paid, messages=messages)

    messages.append(
        "유료 에이전트 없음 (claude / codex / cursor) → 무료 Top3 준비"
    )

    if auto_install:
        for name in FREE_INSTALL_ORDER:
            if _is_installed(name):
                messages.append(f"{name}: 이미 설치됨")
                continue
            ok, note = _try_install(name)
            messages.append(note)
            if ok:
                installed.append(name)

    free = discover_providers(names=FREE_PROVIDER_NAMES)
    if free:
        messages.append(
            f"무료 에이전트 {len(free)}개 사용: "
            + ", ".join(p.display_name or p.name for p in free)
        )
        return EnsureResult(
            tier="free",
            providers=free,
            installed=installed,
            messages=messages,
        )

    messages.append("에이전트 CLI 없음 — mock 백엔드만 사용 가능")
    return EnsureResult(
        tier="none", providers=[], installed=installed, messages=messages
    )


def _is_installed(name: str) -> bool:
    return bool(discover_providers(names=(name,), probe_version=False))


def _try_install(name: str) -> tuple[bool, str]:
    if name in _NPM_PACKAGES:
        return _install_npm(name, _NPM_PACKAGES[name])
    if name in _INSTALL_URLS:
        return _install_script(name)
    return False, f"{name}: 알 수 없는 설치 방법"


def _install_npm(logical: str, package: str) -> tuple[bool, str]:
    npm = _find_npm()
    if npm is None:
        return False, f"{logical}: npm 없음 (Node.js 설치 필요)"
    env = _augment_path(os.environ.copy())
    proc = subprocess.run(
        [npm, "install", "-g", package],
        check=False,
        capture_output=True,
        text=True,
        timeout=INSTALL_TIMEOUT,
        env=env,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-1] if err else f"exit {proc.returncode}"
        return False, f"{logical}: npm 설치 실패 — {tail}"
    return True, f"{logical}: npm 설치 완료 ({package})"


def _install_script(logical: str) -> tuple[bool, str]:
    url, interpreter, extra_args = _INSTALL_URLS[logical]
    which = shutil.which(interpreter)
    if which is None:
        return False, f"{logical}: {interpreter} 없음"
    try:
        with tempfile.TemporaryDirectory(prefix="arenatalk-install-") as tmp:
            script = Path(tmp) / f"{logical}-install.sh"
            _download_https(url, script)
            env = _augment_path(os.environ.copy())
            if logical == "codex":
                env["CODEX_NON_INTERACTIVE"] = "1"
            argv = [which, str(script), *extra_args]
            proc = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=INSTALL_TIMEOUT,
                env=env,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{logical}: 설치 스크립트 실패 — {exc}"

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-1] if err else f"exit {proc.returncode}"
        return False, f"{logical}: 설치 실패 — {tail}"

    if logical == "ollama":
        _pull_ollama_model()
    return True, f"{logical}: 공식 설치 스크립트 완료"


def _pull_ollama_model() -> None:
    ollama = _resolve_binary(("ollama",))
    if ollama is None:
        return
    subprocess.run(
        [ollama, "pull", _OLLAMA_MODEL],
        check=False,
        capture_output=True,
        text=True,
        timeout=INSTALL_TIMEOUT,
    )


def _find_npm() -> str | None:
    return _resolve_binary(("npm",))


def _resolve_binary(candidates: tuple[str, ...]) -> str | None:
    env = _augment_path(os.environ.copy())
    path = env.get("PATH", "")
    for name in candidates:
        found = shutil.which(name, path=path)
        if found:
            return found
    return None


def _augment_path(env: dict[str, str]) -> dict[str, str]:
    home = Path.home()
    extras: list[str] = [
        str(home / ".local" / "bin"),
        str(home / "bin"),
    ]
    for pattern in (
        str(home / ".nvm/versions/node/*/bin"),
        str(home / ".npm-global/bin"),
    ):
        extras.extend(glob.glob(pattern))
    current = env.get("PATH", "")
    merged = os.pathsep.join(p for p in extras if p)
    env["PATH"] = f"{merged}{os.pathsep}{current}" if merged else current
    return env


def _download_https(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ArenaTalk agent installer"},
        method="GET",
    )
    chunks: list[bytes] = []
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response:
        while True:
            block = response.read(65536)
            if not block:
                break
            total += len(block)
            if total > SCRIPT_MAX_BYTES:
                raise ValueError("install script too large")
            chunks.append(block)
    destination.write_bytes(b"".join(chunks))
    destination.chmod(0o700)
