"""Login state for the agent CLIs, plus a semi-automatic login launcher.

Being on ``PATH`` is not the same as being usable: a ``claude`` that has never
been signed in still resolves, still reports a version, and then fails every
debate turn with ``exit 1``. ArenaTalk therefore probes login state separately
from discovery, and can hand the user straight into the CLI's own login flow.

Every probe here must be cheap and offline-ish — no model call, no token spend.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from arenatalk.adapters.cli_agents import agent_path_env, no_window_kwargs

READY = "ready"
NEEDS_LOGIN = "needs_login"
NEEDS_PLAN = "needs_plan"
UNKNOWN = "unknown"

# Claude Code and Codex have no free tier: signing in with an unpaid account
# gets you a working `auth status` and a failure on every actual turn. Only
# Claude Code reports the plan, so only it can be caught before the first call —
# the others are classified from the error text when a turn fails.
_UNPAID_CLAUDE_PLANS = frozenset({"free", "none"})

PROBE_TIMEOUT = 20

# API keys that make a CLI usable without an interactive login.
_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
    "codex": ("OPENAI_API_KEY", "CODEX_ACCESS_TOKEN"),
    "cursor": ("CURSOR_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "qwen": ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"),
    "ollama": (),
}

# Credential files written by each CLI's own OAuth flow.
_CRED_FILES: dict[str, tuple[str, ...]] = {
    "gemini": (".gemini/oauth_creds.json",),
    "qwen": (".qwen/oauth_creds.json",),
}

# Text a CLI prints when the real problem is "you are not signed in".
_LOGIN_MARKERS: tuple[str, ...] = (
    "not logged in",
    "please run /login",
    "please log in",
    "run `claude login`",
    "invalid api key",
    "authentication_error",
    "unauthorized",
    "not authenticated",
    "no credentials",
    "sign in to",
    "login required",
    # Qwen Code's wording when it has no credentials in non-interactive mode.
    "no auth type is selected",
    "configure an auth type",
    "로그인",
)


@dataclass(frozen=True)
class AuthStatus:
    """Result of one login probe."""

    provider: str
    state: str  # ready | needs_login | unknown
    detail: str = ""
    account: str = ""

    @property
    def ready(self) -> bool:
        return self.state == READY


# Text a CLI prints when the account is fine but unpaid or out of credit.
# Deliberately excludes plain rate limits, which are transient and retryable.
_PLAN_MARKERS: tuple[str, ...] = (
    "insufficient credit",
    "insufficient_quota",
    "out of credits",
    "no credit balance",
    "credit balance is too low",
    "upgrade your plan",
    "requires a paid",
    "subscription required",
    "no active subscription",
    "payment required",
    "billing",
    "402",
    "결제",
    "크레딧",
)

# Free tiers have daily/minute caps. Hitting one is temporary — telling the user
# to go buy a plan would be wrong advice, so this is kept separate.
_QUOTA_MARKERS: tuple[str, ...] = (
    "resource has been exhausted",
    "resource_exhausted",
    "check quota",
    "quota exceeded",
    "rate limit",
    "too many requests",
    "429",
    "한도",
)


def looks_like_login_failure(text: str) -> bool:
    """True when CLI output blames authentication rather than the prompt."""
    low = (text or "").lower()
    return any(marker in low for marker in _LOGIN_MARKERS)


# A route the vendor retired. Neither logging in again nor waiting helps; the
# user has to switch to a different auth route or a different CLI.
_RETIRED_MARKERS: tuple[str, ...] = (
    "ineligibletiererror",
    "no longer supported",
    "has been deprecated",
    "please migrate to",
    "is no longer available",
)


def looks_like_retired_route(text: str) -> bool:
    """True when the vendor discontinued the auth route this CLI is using."""
    low = (text or "").lower()
    return any(marker in low for marker in _RETIRED_MARKERS)


def looks_like_quota_failure(text: str) -> bool:
    """True when the account is fine but its rate/daily limit is used up."""
    low = (text or "").lower()
    return any(marker in low for marker in _QUOTA_MARKERS)


def looks_like_plan_failure(text: str) -> bool:
    """True when the account exists but has no paid plan or credit left.

    Codex and Cursor report no plan in their status output, so this is the only
    place the difference between "signed in" and "can actually run" shows up.
    """
    low = (text or "").lower()
    return any(marker in low for marker in _PLAN_MARKERS)


def _run_probe(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an auth-status command with stdin closed so it can never block."""
    try:
        return subprocess.run(
            cmd,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT,
            env=agent_path_env(),
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(exc))


def _env_key_present(provider: str) -> str:
    for key in _ENV_KEYS.get(provider, ()):
        if os.environ.get(key, "").strip():
            return key
    return ""


def _cred_file_present(provider: str) -> str:
    home = Path.home()
    for rel in _CRED_FILES.get(provider, ()):
        path = home / rel
        try:
            if path.is_file() and path.stat().st_size > 0:
                return str(path)
        except OSError:
            continue
    return ""


def _probe_claude(binary: str) -> AuthStatus:
    proc = _run_probe([binary, "auth", "status", "--json"])
    blob = (proc.stdout or "").strip()
    try:
        data = json.loads(blob)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        if not data.get("loggedIn"):
            return AuthStatus("claude", NEEDS_LOGIN, "claude auth login 필요")
        email = str(data.get("email") or "")
        method = str(data.get("authMethod") or "").strip().lower()
        plan = str(data.get("subscriptionType") or "").strip().lower()
        # console / Bedrock / Vertex users pay per token and report no plan.
        if method and method != "claude.ai":
            return AuthStatus("claude", READY, method, email)
        if plan in _UNPAID_CLAUDE_PLANS:
            return AuthStatus(
                "claude",
                NEEDS_PLAN,
                "무료 계정 — Claude Pro/Max 구독이나 API 크레딧이 필요합니다",
                email,
            )
        # A missing plan field means the CLI did not say; do not disqualify it.
        return AuthStatus("claude", READY, plan or method, email)
    if proc.returncode == 0:
        return AuthStatus("claude", READY)
    return AuthStatus("claude", UNKNOWN, (proc.stderr or blob).strip()[:120])


def _probe_codex(binary: str) -> AuthStatus:
    proc = _run_probe([binary, "login", "status"])
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and text and not looks_like_login_failure(text):
        return AuthStatus("codex", READY, text.splitlines()[0][:120])
    if proc.returncode == -1:
        return AuthStatus("codex", UNKNOWN, text[:120])
    return AuthStatus("codex", NEEDS_LOGIN, "codex login 필요")


def _probe_cursor(binary: str) -> AuthStatus:
    proc = _run_probe([binary, "status"])
    text = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and not looks_like_login_failure(text):
        first = text.splitlines()[0][:120] if text else ""
        return AuthStatus("cursor", READY, first)
    if proc.returncode == -1:
        return AuthStatus("cursor", UNKNOWN, text[:120])
    return AuthStatus("cursor", NEEDS_LOGIN, "cursor-agent login 필요")


def _probe_ollama(binary: str) -> AuthStatus:
    # No account anywhere; "ready" means the daemon answers and a model exists.
    proc = _run_probe([binary, "list"])
    if proc.returncode != 0:
        return AuthStatus("ollama", NEEDS_LOGIN, "ollama 서버 미실행 (ollama serve)")
    rows = [r for r in (proc.stdout or "").splitlines()[1:] if r.strip()]
    if not rows:
        return AuthStatus("ollama", NEEDS_LOGIN, "받아둔 모델 없음 (ollama pull)")
    return AuthStatus("ollama", READY, f"{len(rows)}개 모델")


def _probe_file_based(provider: str) -> AuthStatus:
    """gemini / qwen: no status subcommand, so read what their OAuth flow wrote."""
    key = _env_key_present(provider)
    if key:
        return AuthStatus(provider, READY, f"{key} 사용")
    cred = _cred_file_present(provider)
    if cred:
        return AuthStatus(provider, READY, "OAuth 자격증명 있음")
    return AuthStatus(provider, NEEDS_LOGIN, f"{provider} 실행 후 /auth 로 로그인")


_PROBES = {
    "claude": _probe_claude,
    "codex": _probe_codex,
    "cursor": _probe_cursor,
    "ollama": _probe_ollama,
}


def probe_auth(provider: str, binary: str) -> AuthStatus:
    """Return the login state of one installed CLI. Never raises."""
    if _env_key_present(provider) and provider in {"claude", "codex", "cursor"}:
        return AuthStatus(provider, READY, f"{_env_key_present(provider)} 사용")
    probe = _PROBES.get(provider)
    if probe is not None:
        try:
            return probe(binary)
        except Exception as exc:  # noqa: BLE001 - a probe must never break startup
            return AuthStatus(provider, UNKNOWN, str(exc)[:120])
    return _probe_file_based(provider)


# --- semi-automatic login -------------------------------------------------

# The command that starts each CLI's own sign-in flow. ``None`` means the CLI
# has no dedicated subcommand and the user finishes inside the interactive UI.
_LOGIN_ARGV: dict[str, tuple[str, ...] | None] = {
    "claude": ("auth", "login"),
    "codex": ("login",),
    "cursor": ("login",),
    "gemini": None,
    "qwen": None,
    "ollama": None,
}

LOGIN_HINTS: dict[str, str] = {
    "claude": "브라우저가 열리면 Claude 계정으로 승인한 뒤 터미널로 돌아오세요.",
    "codex": "브라우저에서 ChatGPT 계정으로 승인하세요.",
    "cursor": "브라우저에서 Cursor 계정으로 승인하세요.",
    "gemini": "창이 열리면 /auth 를 입력해 'Login with Google'을 고르세요.",
    "qwen": "창이 열리면 /auth 를 입력해 로그인 방식을 고르세요.",
    "ollama": "Ollama는 로그인이 없습니다. 서버 실행과 모델 다운로드만 필요합니다.",
}


def login_command(provider: str, binary: str) -> list[str]:
    """The argv that puts the user in front of this CLI's login flow."""
    argv = _LOGIN_ARGV.get(provider, None)
    if argv is None:
        return [binary]
    return [binary, *argv]


def launch_login(provider: str, binary: str) -> tuple[bool, str]:
    """Open a real terminal running this CLI's login flow.

    Sign-in is interactive by nature (browser round-trip, device codes), so the
    best ArenaTalk can do is hand the user a terminal that is already sitting in
    the right command — hence "semi-automatic". Returns (launched, message).
    """
    if provider == "ollama":
        return _start_ollama_server(binary)

    cmd = login_command(provider, binary)
    try:
        if sys.platform.startswith("win"):
            _launch_windows_console(cmd)
        elif sys.platform == "darwin":
            _launch_macos_terminal(cmd)
        else:
            _launch_linux_terminal(cmd)
    except OSError as exc:
        return False, f"터미널을 열지 못했습니다: {exc}\n직접 실행: {' '.join(cmd)}"
    hint = LOGIN_HINTS.get(provider, "")
    return True, f"로그인 창을 열었습니다: {' '.join(cmd)}\n{hint}".strip()


def _quote(cmd: list[str]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in cmd)


def _launch_windows_console(cmd: list[str]) -> None:
    """New console window that stays open after the flow finishes."""
    create_new_console = 0x00000010
    quoted = " ".join(f'"{part}"' if " " in part else part for part in cmd)
    subprocess.Popen(
        ["cmd", "/c", "start", "ArenaTalk 로그인", "cmd", "/k", quoted],
        env=agent_path_env(),
        creationflags=create_new_console,
        close_fds=True,
    )


def _launch_macos_terminal(cmd: list[str]) -> None:
    script = f'tell application "Terminal" to do script "{_quote(cmd)}"\n'
    script += 'tell application "Terminal" to activate\n'
    subprocess.Popen(
        ["osascript", "-e", script],
        env=agent_path_env(),
        stdin=subprocess.DEVNULL,
        close_fds=True,
    )


_LINUX_TERMINALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x-terminal-emulator", ("-e",)),
    ("gnome-terminal", ("--",)),
    ("konsole", ("-e",)),
    ("xfce4-terminal", ("-e",)),
    ("kitty", ()),
    ("alacritty", ("-e",)),
    ("xterm", ("-e",)),
)


def _launch_linux_terminal(cmd: list[str]) -> None:
    """Run the login command in whichever terminal emulator this desktop has."""
    # Keep the window alive after the flow so the user can read the result.
    inner = f"{_quote(cmd)}; echo; echo '[ArenaTalk] 창을 닫아도 됩니다.'; exec $SHELL"
    env = agent_path_env()
    for name, flag in _LINUX_TERMINALS:
        exe = shutil.which(name, path=env.get("PATH", ""))
        if not exe:
            continue
        argv = [exe, *flag, "bash", "-lc", inner]
        try:
            subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, close_fds=True)
            return
        except OSError:
            continue
    # No emulator (bare WM, container). Fall back to a detached run so an
    # already-authorised browser flow can still complete.
    script = Path(tempfile.gettempdir()) / "arenatalk-login.sh"
    script.write_text(f"#!/bin/sh\n{_quote(cmd)}\n", encoding="utf-8")
    script.chmod(0o700)
    raise OSError(f"터미널 에뮬레이터를 찾지 못했습니다. 직접 실행: {script}")


def _start_ollama_server(binary: str) -> tuple[bool, str]:
    """Ollama has no login — just make sure the local server is up."""
    probe = _probe_ollama(binary)
    if probe.ready:
        return True, "Ollama 준비됨."
    try:
        subprocess.Popen(
            [binary, "serve"],
            env=agent_path_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **no_window_kwargs(),
        )
    except OSError as exc:
        return False, f"ollama serve 실패: {exc}"
    return True, "ollama serve 를 시작했습니다. 모델이 없으면 ollama pull 이 필요합니다."
