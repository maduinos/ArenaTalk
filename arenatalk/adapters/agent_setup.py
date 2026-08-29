"""Get a working agent CLI onto this machine, on Linux, macOS and Windows.

Two things have to be true before a debate can run: a CLI is *installed*, and
that CLI is *signed in*. This module owns the first half (detect, and install
the free tier when nothing usable is present) and defers the second half to
:mod:`arenatalk.adapters.agent_auth`, but it reports both so callers can tell a
missing CLI apart from a stranded one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from arenatalk.adapters.cli_agents import (
    FREE_PROVIDER_NAMES,
    PAID_PROVIDER_NAMES,
    ProviderInfo,
    agent_path_env,
    agent_search_path,
    attach_login_state,
    discover_providers,
    no_window_kwargs,
)

INSTALL_TIMEOUT = 900
SCRIPT_MAX_BYTES = 2 * 1024 * 1024

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

# Free agent CLIs, best-odds-first. qwen leads because its free OAuth tier still
# works, while Gemini CLI's individual Code Assist route now answers
# IneligibleTierError and needs a GEMINI_API_KEY instead. ollama is last: it is
# the most reliable (local, no account) but drags in a multi-gigabyte model.
FREE_INSTALL_ORDER: tuple[str, ...] = ("qwen", "gemini", "ollama")

# Both npm CLIs are ESM with top-level await; Ubuntu 22.04's distro node is
# v12 and dies with "SyntaxError: Unexpected reserved word" on first run.
MIN_NODE_MAJOR = 20

_NPM_PACKAGES: dict[str, str] = {
    "gemini": "@google/gemini-cli",
    "qwen": "@qwen-code/qwen-code@latest",
}

# Package ids for the OS package managers, used when there is no npm route.
_WINGET_IDS: dict[str, str] = {
    "node": "OpenJS.NodeJS.LTS",
    "ollama": "Ollama.Ollama",
}
_BREW_IDS: dict[str, str] = {
    "node": "node",
    "ollama": "ollama",
}

_OLLAMA_INSTALL_SH = "https://ollama.com/install.sh"
_NVM_INSTALL_SH = (
    "https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh"
)
_OLLAMA_MODEL = "qwen2.5-coder:7b"

Progress = Callable[[str], None]


@dataclass
class InstallPlan:
    """Exactly what a free-tier install would do, for the user to approve.

    Installing is never implicit: it puts global packages (and possibly a
    multi-gigabyte local model) on someone's machine, so the caller has to show
    this and get a yes first.
    """

    node_needed: bool
    candidates: list[str] = field(default_factory=list)
    heavy: bool = False  # a model download measured in gigabytes

    @property
    def empty(self) -> bool:
        return not self.candidates and not self.node_needed

    def describe(self) -> list[str]:
        lines: list[str] = []
        if self.node_needed:
            how = {
                True: "winget (OpenJS.NodeJS.LTS)",
                False: "Homebrew" if IS_MACOS else "nvm (~/.nvm, sudo 불필요)",
            }[IS_WINDOWS]
            lines.append(f"Node.js v{MIN_NODE_MAJOR}+ 설치 — {how}")
        for name in self.candidates:
            pkg = _NPM_PACKAGES.get(name)
            if pkg:
                lines.append(f"{name} — npm install -g {pkg}")
            elif name == "ollama":
                where = "winget" if IS_WINDOWS else ("brew" if IS_MACOS else "공식 install.sh")
                lines.append(
                    f"ollama — {where} 설치 + 모델 {_OLLAMA_MODEL} 내려받기 (수 GB)"
                )
        lines.append("먼저 성공하는 것 하나에서 멈춥니다.")
        return lines


ConfirmInstall = Callable[[InstallPlan], bool]


def plan_free_install() -> InstallPlan:
    """What ensure_agent_clis would install, without installing anything."""
    missing = [name for name in FREE_INSTALL_ORDER if not _is_installed(name)]
    return InstallPlan(
        node_needed=not node_is_usable(),
        candidates=missing,
        heavy="ollama" in missing,
    )


@dataclass
class EnsureResult:
    """What a debate will use (``providers``) and what exists (``all_seen``).

    The two differ on purpose. Selection is tier-preferring, so a signed-in
    Claude Code hides a signed-in Gemini CLI from the round-robin — but the
    settings screen must still list Gemini, or there is no way to reach a CLI
    the selection skipped.
    """

    tier: str  # paid | free | none
    providers: list[ProviderInfo]
    installed: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    all_seen: list[ProviderInfo] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.all_seen:
            self.all_seen = list(self.providers)

    @property
    def ready(self) -> list[ProviderInfo]:
        return [p for p in self.providers if p.ready]

    @property
    def needs_login(self) -> list[ProviderInfo]:
        return [p for p in self.all_seen if p.needs_login]

    @property
    def blocked(self) -> list[ProviderInfo]:
        """Installed but unusable: not signed in, or signed in without a plan."""
        return [p for p in self.all_seen if p.blocked]


def preferred_providers(
    *,
    probe_version: bool = True,
    probe_login: bool = True,
) -> list[ProviderInfo]:
    """Best usable CLIs: signed-in paid first, then signed-in free.

    Presence on PATH used to be enough to win here, which meant a ``claude``
    that had never been logged into shadowed a perfectly working Gemini CLI and
    failed every single turn. Login state is part of the choice now.
    """
    paid = discover_providers(
        names=PAID_PROVIDER_NAMES, probe_version=probe_version, probe_login=probe_login
    )
    free = discover_providers(
        names=FREE_PROVIDER_NAMES, probe_version=probe_version, probe_login=probe_login
    )
    paid_ready = [p for p in paid if p.ready]
    if paid_ready:
        return paid_ready
    free_ready = [p for p in free if p.ready]
    if free_ready:
        return free_ready
    # Nothing is confirmed usable; hand back whatever exists so the caller can
    # show "installed, needs login" rather than "nothing found".
    return paid + free


def ensure_agent_clis(
    *,
    auto_install: bool = True,
    probe_login: bool = True,
    on_progress: Progress | None = None,
    confirm: ConfirmInstall | None = None,
) -> EnsureResult:
    """Detect usable CLIs and, with consent, install the free tier.

    ``auto_install=True`` only makes installing *possible*. Nothing is installed
    unless ``confirm`` is given and returns True for the plan — a caller that
    forgets it gets detection, not a surprise download.
    """
    messages: list[str] = []
    installed: list[str] = []

    def say(line: str) -> None:
        messages.append(line)
        if on_progress:
            on_progress(line)

    # One scan of everything installed. Probes run in parallel, so covering
    # both tiers up front costs little and is what the settings screen shows.
    everything = discover_providers(probe_login=probe_login)
    paid = [p for p in everything if p.tier == "paid"]
    free = [p for p in everything if p.tier == "free"]

    stranded = [p for p in everything if p.blocked]
    if stranded:
        from arenatalk.adapters.cli_agents import AUTH_LABELS

        say(
            "설치됐지만 바로 쓸 수 없음: "
            + ", ".join(
                f"{p.display_name or p.name}({AUTH_LABELS.get(p.auth, p.auth)})"
                for p in stranded
            )
        )

    paid_ready = [p for p in paid if p.ready]
    if paid_ready:
        say(
            f"유료 에이전트 {len(paid_ready)}개 사용 가능: "
            + ", ".join(p.display_name or p.name for p in paid_ready)
        )
        return EnsureResult(
            tier="paid",
            providers=paid_ready,
            messages=messages,
            all_seen=everything,
        )

    free_ready = [p for p in free if p.ready]
    if free_ready:
        say(
            f"무료 에이전트 {len(free_ready)}개 사용 가능: "
            + ", ".join(p.display_name or p.name for p in free_ready)
        )
        return EnsureResult(
            tier="free",
            providers=free_ready,
            messages=messages,
            all_seen=everything,
        )

    if not auto_install:
        say("자동 설치를 하지 않았습니다 (감지 전용).")
        return EnsureResult(
            tier="free" if free else ("paid" if paid else "none"),
            providers=[],
            messages=messages,
            all_seen=everything,
        )

    plan = plan_free_install()
    if plan.empty:
        say("설치할 무료 CLI가 남아 있지 않습니다.")
        return EnsureResult(
            tier="none", providers=[], messages=messages, all_seen=everything
        )
    if confirm is None or not confirm(plan):
        say("설치는 진행하지 않았습니다 (사용자 승인 없음).")
        return EnsureResult(
            tier="none", providers=[], messages=messages, all_seen=everything
        )

    say("승인됨 — 무료 CLI를 설치합니다.")
    _prepare_node(say)

    for name in FREE_INSTALL_ORDER:
        if _is_installed(name):
            say(f"{name}: 이미 설치됨")
            continue
        ok, note = _try_install(name, say)
        say(note)
        if ok:
            installed.append(name)
        if ok and name in _NPM_PACKAGES:
            # gemini or qwen landed; skip the multi-GB ollama download.
            say("무료 CLI 하나가 준비돼 나머지 설치는 건너뜁니다.")
            break

    everything = discover_providers(probe_login=probe_login)
    free = [p for p in everything if p.tier == "free"]
    if free:
        ready = [p for p in free if p.ready]
        say(
            f"무료 에이전트 {len(free)}개 감지 (사용 가능 {len(ready)}개): "
            + ", ".join(p.display_name or p.name for p in free)
        )
        if not ready:
            say("설치는 됐지만 로그인이 필요합니다 — 「로그인」을 눌러 마무리하세요.")
        return EnsureResult(
            tier="free",
            providers=ready,
            installed=installed,
            messages=messages,
            all_seen=everything,
        )

    say("에이전트 CLI 없음 — mock 백엔드만 사용 가능")
    return EnsureResult(
        tier="none",
        providers=[],
        installed=installed,
        messages=messages,
        all_seen=everything,
    )


def _is_installed(name: str) -> bool:
    return bool(discover_providers(names=(name,), probe_version=False))


def _try_install(name: str, say: Progress) -> tuple[bool, str]:
    if name in _NPM_PACKAGES:
        return _install_npm(name, _NPM_PACKAGES[name])
    if name == "ollama":
        return _install_ollama(say)
    return False, f"{name}: 알 수 없는 설치 방법"


# --- process helpers ------------------------------------------------------


def _run_install(
    argv: list[str],
    timeout: int = INSTALL_TIMEOUT,
    *,
    env: dict[str, str] | None = None,
):
    """Run an installer with stdin closed and the augmented PATH."""
    return subprocess.run(
        argv,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env or agent_path_env(),
        **no_window_kwargs(),
    )


def _tail(proc) -> str:
    raw = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return lines[-1] if lines else f"exit {proc.returncode}"


def _which(name: str) -> str | None:
    return shutil.which(name, path=agent_search_path())


# --- Node.js / npm --------------------------------------------------------


def _find_npm() -> str | None:
    """npm is ``npm.cmd`` on Windows; ``shutil.which`` resolves that via PATHEXT."""
    return _which("npm")


def node_major() -> int | None:
    """Major version of the node these CLIs would actually run on, or None."""
    node = _which("node")
    if node is None:
        return None
    try:
        proc = _run_install([node, "--version"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or "").strip().lstrip("vV")
    head = text.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def node_is_usable() -> bool:
    """npm present *and* node new enough to load an ESM CLI."""
    if _find_npm() is None:
        return False
    major = node_major()
    # Unknown version: do not block on a check that could not answer.
    return major is None or major >= MIN_NODE_MAJOR


def _prepare_node(say: Progress) -> None:
    """Both free npm CLIs need a modern Node. Two ways that goes wrong.

    On a clean Windows box there is no Node at all — the missing step that made
    "no paid CLI" silently end in "no free CLI either". On Ubuntu 22.04 there is
    Node, but it is v12, so npm succeeds and the installed CLI dies on its first
    line. Both have to be caught here.
    """
    major = node_major()
    if _find_npm() and (major is None or major >= MIN_NODE_MAJOR):
        return
    if major is not None and major < MIN_NODE_MAJOR:
        say(
            f"Node.js v{major} 는 너무 낮습니다 (v{MIN_NODE_MAJOR} 이상 필요) "
            "→ 최신 Node.js 설치를 시도합니다."
        )
    else:
        say("npm 없음 → Node.js 설치를 시도합니다.")
    ok, note = _install_os_package("node", say)
    say(note)
    if not ok:
        say(
            f"Node.js v{MIN_NODE_MAJOR}+ 를 자동 설치하지 못했습니다. "
            "https://nodejs.org 에서 LTS를 설치한 뒤 다시 시도하세요 "
            "(Ubuntu 22.04의 apt nodejs는 v12라 쓸 수 없습니다)."
        )


def _install_npm(logical: str, package: str) -> tuple[bool, str]:
    npm = _find_npm()
    if npm is None:
        return False, f"{logical}: npm 없음 (Node.js 설치 필요 — https://nodejs.org)"
    major = node_major()
    if major is not None and major < MIN_NODE_MAJOR:
        return False, (
            f"{logical}: Node.js v{major} 로는 실행되지 않습니다 "
            f"(v{MIN_NODE_MAJOR} 이상 필요) — 설치를 건너뜁니다."
        )
    # A .cmd shim is safest to launch through cmd on Windows.
    argv = ["cmd", "/c", npm, "install", "-g", package] if IS_WINDOWS else [npm, "install", "-g", package]
    try:
        proc = _run_install(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{logical}: npm 설치 실패 — {exc}"
    if proc.returncode != 0:
        return False, f"{logical}: npm 설치 실패 — {_tail(proc)}"
    return True, f"{logical}: npm 설치 완료 ({package})"


# --- OS package managers --------------------------------------------------


def _install_os_package(logical: str, say: Progress) -> tuple[bool, str]:
    """Install via winget / Homebrew / apt, whichever this machine actually has."""
    if IS_WINDOWS:
        winget = _which("winget")
        pkg = _WINGET_IDS.get(logical)
        if not winget or not pkg:
            return False, f"{logical}: winget 없음 — 수동 설치가 필요합니다."
        say(f"{logical}: winget 설치 중… ({pkg})")
        try:
            proc = _run_install(
                [
                    winget, "install", "--id", pkg, "-e",
                    "--silent",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                    "--disable-interactivity",
                ]
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{logical}: winget 실패 — {exc}"
        # winget reports "already installed" as a non-zero code too.
        blob = ((proc.stdout or "") + (proc.stderr or "")).lower()
        if proc.returncode == 0 or "already installed" in blob:
            return True, f"{logical}: winget 설치 완료"
        return False, f"{logical}: winget 설치 실패 — {_tail(proc)}"

    if IS_MACOS:
        brew = _which("brew")
        pkg = _BREW_IDS.get(logical)
        if not brew or not pkg:
            return False, f"{logical}: Homebrew 없음 — 수동 설치가 필요합니다."
        say(f"{logical}: brew 설치 중… ({pkg})")
        try:
            proc = _run_install([brew, "install", pkg])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{logical}: brew 실패 — {exc}"
        if proc.returncode != 0:
            return False, f"{logical}: brew 설치 실패 — {_tail(proc)}"
        return True, f"{logical}: brew 설치 완료"

    if logical == "node":
        return _install_node_linux(say)
    return False, f"{logical}: 자동 설치 경로 없음"


def _install_node_linux(say: Progress) -> tuple[bool, str]:
    """Install a current Node into ~/.nvm, without sudo and without a shell edit.

    Ubuntu 22.04 ships Node v12, which cannot load these CLIs at all, and a
    .deb user has no reason to own a terminal — so "install nvm yourself" is a
    dead end. nvm installs entirely under $NVM_DIR, and PROFILE=/dev/null keeps
    it from appending to the user's shell rc; ArenaTalk finds the result because
    _path_extras already globs ~/.nvm/versions/node/*/bin.
    """
    bash = _which("bash")
    if bash is None:
        return False, "node: bash 없음"
    nvm_dir = Path(os.environ.get("NVM_DIR") or (Path.home() / ".nvm"))
    try:
        # The installer refuses outright when NVM_DIR is set but missing.
        nvm_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"node: {nvm_dir} 를 만들 수 없습니다 — {exc}"
    say(f"node: nvm 으로 최신 LTS 설치 중… ({nvm_dir})")
    try:
        with tempfile.TemporaryDirectory(prefix="arenatalk-nvm-") as tmp:
            script = Path(tmp) / "nvm-install.sh"
            _download_https(_NVM_INSTALL_SH, script, expect=b"nvm")
            env = agent_path_env()
            env["NVM_DIR"] = str(nvm_dir)
            # Do not touch the user's .bashrc/.zshrc — we resolve node ourselves.
            env["PROFILE"] = os.devnull
            # nvm refuses to run while npm has a global prefix pinned, which is
            # exactly the setup of anyone who configured npm global installs.
            for stray in ("npm_config_prefix", "NPM_CONFIG_PREFIX", "PREFIX"):
                env.pop(stray, None)
            proc = _run_install([bash, str(script)], env=env)
            if proc.returncode != 0:
                return False, f"node: nvm 설치 실패 — {_tail(proc)}"
            proc = _run_install(
                [
                    bash,
                    "-lc",
                    f'export NVM_DIR="{nvm_dir}"; . "$NVM_DIR/nvm.sh"; '
                    "nvm install --lts >&2 && nvm alias default 'lts/*' >&2",
                ],
                env=env,
            )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return False, f"node: nvm 설치 실패 — {exc}"
    if proc.returncode != 0:
        return False, f"node: Node.js 설치 실패 — {_tail(proc)}"
    major = node_major()
    if major is None or major < MIN_NODE_MAJOR:
        return False, "node: 설치했지만 새 버전을 찾지 못했습니다 (앱 재시작 필요)"
    return True, f"node: Node.js v{major} 준비 완료 (nvm)"


def _install_ollama(say: Progress) -> tuple[bool, str]:
    if IS_WINDOWS or IS_MACOS:
        ok, note = _install_os_package("ollama", say)
        if ok:
            _pull_ollama_model(say)
        return ok, note

    sh = _which("sh")
    if sh is None:
        return False, "ollama: sh 없음"
    say("ollama: 공식 설치 스크립트 실행 중…")
    try:
        with tempfile.TemporaryDirectory(prefix="arenatalk-install-") as tmp:
            script = Path(tmp) / "ollama-install.sh"
            _download_https(_OLLAMA_INSTALL_SH, script)
            proc = _run_install([sh, str(script)])
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return False, f"ollama: 설치 스크립트 실패 — {exc}"
    if proc.returncode != 0:
        tail = _tail(proc)
        if "sudo" in tail.lower() or "permission denied" in tail.lower():
            return False, (
                "ollama: 리눅스 설치에는 관리자 권한이 필요해 앱에서 진행할 수 없습니다. "
                "터미널에서 직접 실행하세요:\n"
                "  curl -fsSL https://ollama.com/install.sh | sh"
            )
        return False, f"ollama: 설치 실패 — {tail}"
    _pull_ollama_model(say)
    return True, "ollama: 공식 설치 스크립트 완료"


def _pull_ollama_model(say: Progress) -> None:
    ollama = _which("ollama")
    if ollama is None:
        return
    say(f"ollama: 모델 내려받는 중… ({_OLLAMA_MODEL}, 수 GB)")
    try:
        proc = _run_install([ollama, "pull", _OLLAMA_MODEL])
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f"ollama: 모델 다운로드 실패 — {exc}")
        return
    if proc.returncode != 0:
        say(f"ollama: 모델 다운로드 실패 — {_tail(proc)}")
    else:
        say(f"ollama: {_OLLAMA_MODEL} 준비 완료")


def _download_https(url: str, destination: Path, *, expect: bytes = b"ollama") -> None:
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
    data = b"".join(chunks)
    # Refuse obvious non-shell payloads (HTML error pages, empty bodies).
    head = data.lstrip()[:120]
    if not head.startswith(b"#!") and expect not in head.lower():
        raise ValueError("install script failed basic integrity check (missing shebang)")
    destination.write_bytes(data)
    destination.chmod(0o700)


def install_hint(name: str) -> str:
    """What to run to get this CLI, for a row the dialog shows as 미설치."""
    pkg = _NPM_PACKAGES.get(name)
    if pkg:
        return f"npm install -g {pkg}"
    if name == "ollama":
        where = "winget install Ollama.Ollama" if IS_WINDOWS else (
            "brew install ollama" if IS_MACOS else "https://ollama.com/install.sh"
        )
        return f"{where} + ollama pull {_OLLAMA_MODEL}"
    return "각 CLI 공식 설치 안내를 따르세요"


# --- login passthrough ----------------------------------------------------


def login_provider(provider: ProviderInfo) -> tuple[bool, str]:
    """Open this CLI's own sign-in flow in a terminal the user can see."""
    from arenatalk.adapters.agent_auth import launch_login

    return launch_login(provider.name, provider.binary)


def refresh_login_state(providers: list[ProviderInfo]) -> list[ProviderInfo]:
    return attach_login_state(providers)


__all__ = [
    "EnsureResult",
    "FREE_INSTALL_ORDER",
    "install_hint",
    "ensure_agent_clis",
    "login_provider",
    "preferred_providers",
    "refresh_login_state",
]
