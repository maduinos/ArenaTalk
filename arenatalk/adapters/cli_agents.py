from __future__ import annotations

import glob
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

# Live CLI children so debate cancel can kill hung agent processes.
_ACTIVE_PROCS: set[subprocess.Popen[str]] = set()
# pids we killed on purpose, so their non-zero exit reads as "stopped", not "failed"
_CANCELLED_PIDS: set[int] = set()
_PROCS_LOCK = threading.Lock()

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


class BackendCancelled(RuntimeError):
    """The CLI call was killed by the user pressing stop, not by a failure."""


def no_window_kwargs() -> dict[str, int]:
    """Keep helper CLIs from flashing a console on the windowed Windows build."""
    if sys.platform.startswith("win"):
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _group_spawn_kwargs() -> dict[str, object]:
    """Start each agent CLI as its own process group.

    Agent CLIs are launched through shims (npm ``.cmd``/``sh`` wrappers around
    node), so killing just the direct child leaves the real worker running and
    holding the pipe. Owning the whole group makes stop actually stop.
    """
    if sys.platform.startswith("win"):
        return {"creationflags": _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the CLI and everything it spawned."""
    if sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            try:
                proc.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def reset_cancel_state() -> None:
    """Forget cancellations from an earlier debate. Call before starting one."""
    with _PROCS_LOCK:
        _CANCELLED_PIDS.clear()


def _path_extras() -> list[str]:
    """Install locations the agent CLIs use that a GUI session often misses.

    A double-clicked .exe or a .desktop launcher inherits the login PATH, not
    the shell PATH, so npm's global shim directory and per-user installs are
    routinely invisible even though the CLI is installed.
    """
    home = Path.home()
    extras: list[str] = [str(home / ".local" / "bin"), str(home / "bin")]
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA", "")
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        if appdata:
            extras.append(str(Path(appdata) / "npm"))
        if local:
            extras.append(str(Path(local) / "Ollama"))
            extras.append(str(Path(local) / "Programs" / "Ollama"))
            extras.append(str(Path(local) / "Programs" / "cursor-agent"))
            extras.append(str(Path(local) / "Microsoft" / "WinGet" / "Links"))
        extras.append(str(Path(program_files) / "nodejs"))
        extras.append(str(Path(program_files) / "Ollama"))
    else:
        extras.append("/usr/local/bin")
        extras.append("/opt/homebrew/bin")
        for pattern in (
            str(home / ".nvm/versions/node/*/bin"),
            str(home / ".npm-global/bin"),
            str(home / ".volta/bin"),
            str(home / ".bun/bin"),
        ):
            extras.extend(sorted(glob.glob(pattern)))
    return [e for e in extras if e]


def agent_path_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment with the agent CLI install dirs on PATH."""
    merged = dict(env or os.environ)
    extras = _path_extras()
    current = merged.get("PATH", "")
    prefix = os.pathsep.join(extras)
    merged["PATH"] = f"{prefix}{os.pathsep}{current}" if prefix else current
    return merged


def agent_search_path() -> str:
    return agent_path_env().get("PATH", "")


@dataclass(frozen=True)
class ProviderInfo:
    """A discovered local agent CLI usable as a debate backend."""

    name: str
    binary: str
    display_name: str = ""
    version: str = ""
    kind: str = "cli"  # cli
    notes: str = ""
    tier: str = ""  # paid | free
    auth: str = "unknown"  # ready | needs_login | needs_plan | unknown
    auth_detail: str = ""
    account: str = ""

    @property
    def ready(self) -> bool:
        """Installed, signed in, and paid for — what a debate can actually use.

        ``unknown`` counts as ready on purpose: a probe that could not answer
        must not disqualify a working CLI.
        """
        return self.auth in {"ready", "unknown"}

    @property
    def needs_login(self) -> bool:
        return self.auth == "needs_login"

    @property
    def needs_plan(self) -> bool:
        """Signed in, but on an account that cannot actually run a turn."""
        return self.auth == "needs_plan"

    @property
    def blocked(self) -> bool:
        return self.needs_login or self.needs_plan

    @property
    def label(self) -> str:
        base = self.display_name or self.name
        if self.version:
            return f"{base} ({self.version})"
        return base


# Known agent CLIs: (logical name, binary candidates, human label, tier)
# tier: paid = subscription CLI, free = no subscription required (may need API key)
_AGENT_CATALOG: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("claude", ("claude",), "Claude Code", "paid"),
    ("codex", ("codex",), "OpenAI Codex", "paid"),
    ("cursor", ("agent", "cursor-agent"), "Cursor Agent", "paid"),
    ("gemini", ("gemini",), "Gemini CLI", "free"),
    ("qwen", ("qwen",), "Qwen Code", "free"),
    ("ollama", ("ollama",), "Ollama", "free"),
)

PAID_PROVIDER_NAMES: frozenset[str] = frozenset(
    name for name, _, _, tier in _AGENT_CATALOG if tier == "paid"
)
FREE_PROVIDER_NAMES: frozenset[str] = frozenset(
    name for name, _, _, tier in _AGENT_CATALOG if tier == "free"
)

DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"

# Strong profile: rolling aliases only — never pin dated model IDs (they go stale).
# None = omit --model / -m so the CLI auto-picks its current recommended, same
# tracking behavior as the default profile. Claude Code exposes tier aliases
# (opus / fable / best) that follow the latest of that tier; other CLIs mostly
# only float via their own default / Auto routing.
# Override with ARENATALK_MODEL_<NAME>.
STRONG_MODELS: dict[str, str | None] = {
    "claude": "opus",
    "codex": None,
    "cursor": None,
    "gemini": None,
    "qwen": None,
    "ollama": None,
}


def resolve_model(provider: str, profile: str = "default") -> str | None:
    """Return model id to pass to a CLI, or None to use the CLI's own default."""
    from arenatalk.config import env_get

    raw = env_get(f"ARENATALK_MODEL_{provider.upper()}")
    if raw:
        return raw or None
    profile = (profile or "default").strip().lower()
    if profile in {"default", "auto", "cli"}:
        return None
    if profile in {"strong", "high", "pro"}:
        # Explicit None is intentional (CLI auto); missing key → also auto.
        return STRONG_MODELS.get(provider)
    return STRONG_MODELS.get(provider)

class ClaudePrintBackend:
    name = "claude"

    def __init__(
        self,
        binary: str | None = None,
        cwd: Path | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.binary = binary or shutil.which("claude") or "claude"
        self.cwd = str(cwd) if cwd else None
        self.model = model

    def complete(self, system: str, user: str) -> str:
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "도구/쉘/파일 수정은 하지 말고 JSON만 출력하라."
        )
        cmd = [self.binary, "-p", prompt, "--tools", ""]
        if self.model:
            cmd.extend(["--model", self.model])
        proc = _run(cmd, cwd=self.cwd)
        if proc.returncode != 0 and not _is_auth_failure(proc):
            # Retry without the tool switch in case this build rejects it — but
            # never for a login failure, which would just fail twice as slowly.
            cmd2 = [self.binary, "-p", prompt]
            if self.model:
                cmd2.extend(["--model", self.model])
            proc = _run(cmd2, cwd=self.cwd)
        if proc.returncode != 0:
            raise _cli_error("Claude Code", proc)
        return proc.stdout.strip()


class CodexExecBackend:
    name = "codex"

    def __init__(
        self,
        binary: str | None = None,
        cwd: Path | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.binary = binary or shutil.which("codex") or "codex"
        self.cwd = str(cwd) if cwd else None
        self.model = model

    def complete(self, system: str, user: str) -> str:
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "Do not edit files or run shell tools. Reply with JSON only."
        )
        cmd = [self.binary, "exec", "--skip-git-repo-check"]
        if self.model:
            cmd.extend(["-m", self.model])
        cmd.append(prompt)
        proc = _run(cmd, cwd=self.cwd)
        if proc.returncode != 0 and _is_auth_failure(proc):
            raise _cli_error("OpenAI Codex", proc)
        if proc.returncode != 0 and "unknown" in (proc.stderr or "").lower():
            cmd2 = [self.binary, "exec"]
            if self.model:
                cmd2.extend(["-m", self.model])
            cmd2.append(prompt)
            proc = _run(cmd2, cwd=self.cwd)
        if proc.returncode != 0:
            raise _cli_error("OpenAI Codex", proc)
        return proc.stdout.strip()


class CursorAgentBackend:
    name = "cursor"

    def __init__(
        self,
        binary: str | None = None,
        cwd: Path | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.binary = (
            binary
            or shutil.which("agent")
            or shutil.which("cursor-agent")
            or "agent"
        )
        self.cwd = str(cwd) if cwd else None
        self.model = model

    def complete(self, system: str, user: str) -> str:
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "Do not edit files or run tools. Reply with JSON only."
        )
        cmd = [
            self.binary,
            "-p",
            "--mode",
            "ask",
            "--output-format",
            "text",
            "--trust",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.append(prompt)
        proc = _run(cmd, cwd=self.cwd)
        if proc.returncode != 0:
            raise _cli_error("Cursor Agent", proc)
        return proc.stdout.strip()


class GeminiPrintBackend:
    name = "gemini"

    def __init__(
        self,
        binary: str | None = None,
        cwd: Path | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.binary = binary or shutil.which("gemini") or "gemini"
        self.cwd = str(cwd) if cwd else None
        self.model = model

    def complete(self, system: str, user: str) -> str:
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "Do not use tools. Reply with JSON only."
        )
        cmd = [
            self.binary,
            "-p",
            prompt,
            "-o",
            "text",
            # "plan" is advertised in --help but rejected at runtime unless
            # experimental.plan is on, which killed every Gemini turn outright.
            # "default" never auto-approves a tool, which is all we need.
            "--approval-mode",
            "default",
        ]
        if self.model:
            cmd.extend(["-m", self.model])
        proc = _run(cmd, cwd=self.cwd)
        if proc.returncode != 0 and not _is_auth_failure(proc):
            cmd2 = [self.binary, "-p", prompt]
            if self.model:
                cmd2.extend(["-m", self.model])
            proc = _run(cmd2, cwd=self.cwd)
        if proc.returncode != 0:
            raise _cli_error("Gemini CLI", proc)
        return proc.stdout.strip()


class QwenPrintBackend:
    name = "qwen"

    def __init__(
        self,
        binary: str | None = None,
        cwd: Path | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.binary = binary or shutil.which("qwen") or "qwen"
        self.cwd = str(cwd) if cwd else None
        self.model = model

    def complete(self, system: str, user: str) -> str:
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "Do not use tools. Reply with JSON only."
        )
        cmd = [self.binary, "-p", prompt]
        if self.model:
            cmd.extend(["-m", self.model])
        proc = _run(cmd, cwd=self.cwd)
        if proc.returncode != 0:
            raise _cli_error("Qwen Code", proc)
        return proc.stdout.strip()


class OllamaRunBackend:
    name = "ollama"

    def __init__(
        self,
        binary: str | None = None,
        cwd: Path | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.binary = binary or shutil.which("ollama") or "ollama"
        self.cwd = str(cwd) if cwd else None
        self.model = model or DEFAULT_OLLAMA_MODEL

    def complete(self, system: str, user: str) -> str:
        prompt = (
            f"{system}\n\n---\n\n{user}\n\n"
            "Do not run shell commands. Reply with JSON only."
        )
        cmd = [self.binary, "run", self.model, prompt]
        proc = _run(cmd, cwd=self.cwd, timeout=max(cli_timeout(), 900))
        if proc.returncode != 0:
            raise _cli_error("Ollama", proc)
        return proc.stdout.strip()


BackendType = (
    ClaudePrintBackend
    | CodexExecBackend
    | CursorAgentBackend
    | GeminiPrintBackend
    | QwenPrintBackend
    | OllamaRunBackend
)


def cancel_active_backends() -> int:
    """Kill in-flight agent CLI processes started by ArenaTalk. Returns kill count."""
    killed = 0
    with _PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    for proc in procs:
        try:
            if proc.poll() is None:
                # Record first: _run reads this to tell "stopped" from "crashed".
                with _PROCS_LOCK:
                    _CANCELLED_PIDS.add(proc.pid)
                _kill_tree(proc)
                killed += 1
        except OSError:
            pass
    return killed


DEFAULT_CLI_TIMEOUT = 600


def cli_timeout(default: int = DEFAULT_CLI_TIMEOUT) -> int:
    """Seconds to wait on one CLI turn. Override with ARENATALK_CLI_TIMEOUT."""
    from arenatalk.config import env_get

    raw = env_get("ARENATALK_CLI_TIMEOUT")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _run(
    cmd: list[str], cwd: str | None = None, *, timeout: int | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a CLI and track the Popen so cancel can interrupt it.

    Raises :class:`BackendCancelled` when the user stopped the debate, so a
    deliberate stop never reaches the caller looking like a CLI failure.
    """
    timeout = cli_timeout() if timeout is None else timeout
    try:
        proc = subprocess.Popen(
            cmd,
            # Closed stdin, always. Inheriting the GUI's stdin makes a CLI that
            # reads piped input sit and wait for an EOF that never arrives, and
            # the debate freezes until the timeout instead of answering.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Agent CLIs emit UTF-8; without this Windows decodes with the
            # locale codec (cp949 on Korean installs) and mangles every reply.
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            env=agent_path_env(),
            **_group_spawn_kwargs(),
        )
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr=str(exc))

    with _PROCS_LOCK:
        _ACTIVE_PROCS.add(proc)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            if _was_cancelled(proc):
                raise BackendCancelled("사용자가 토론을 중지했습니다.") from None
            return subprocess.CompletedProcess(
                cmd,
                returncode=-1,
                stdout=stdout or "",
                stderr=stderr or f"{timeout}초 안에 응답이 없어 중단했습니다.",
            )
        if _was_cancelled(proc):
            # Stop was pressed: a killed process exits non-zero, and without
            # this every cancel surfaced as a red "CLI failed" dialog — and the
            # retry path even started a *new* CLI after the user said stop.
            raise BackendCancelled("사용자가 토론을 중지했습니다.")
        return subprocess.CompletedProcess(
            cmd, returncode=proc.returncode or 0, stdout=stdout or "", stderr=stderr or ""
        )
    finally:
        with _PROCS_LOCK:
            _ACTIVE_PROCS.discard(proc)


def _was_cancelled(proc: subprocess.Popen[str]) -> bool:
    """True when this exact process was killed by cancel_active_backends()."""
    with _PROCS_LOCK:
        if proc.pid in _CANCELLED_PIDS:
            _CANCELLED_PIDS.discard(proc.pid)
            return True
    return False


# Node CLIs bury the real message under a stack trace and deprecation notices.
_NOISE_PREFIXES: tuple[str, ...] = ("at ", "(node:", "(Use `node", "{", "}", "...")
_ERROR_HINTS: tuple[str, ...] = (
    "error",
    "failed",
    "cannot",
    "not logged",
    "invalid",
    "exhaust",
    "denied",
    "unauthorized",
    "quota",
    "credit",
)


def _summarize_cli_output(raw: str, limit: int = 3) -> str:
    """The most informative lines of a CLI's output, not merely the last ones.

    Taking the tail of a Node stack trace yields ``at async main (...)`` and
    hides the one line that names the problem, so prefer lines that read like
    an error and drop stack frames and deprecation chatter.
    """
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    meaningful = [
        line
        for line in lines
        if not line.startswith(_NOISE_PREFIXES)
        and "DeprecationWarning" not in line
    ]
    flagged = [
        line for line in meaningful if any(h in line.lower() for h in _ERROR_HINTS)
    ]
    picked = flagged[:limit] or meaningful[:limit] or lines[-limit:]
    seen: list[str] = []
    for line in picked:
        if line not in seen:
            seen.append(line)
    return " / ".join(seen)[:400]


def _is_auth_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    """Retrying an account problem only doubles the wait before the same error."""
    from arenatalk.adapters.agent_auth import (
        looks_like_login_failure,
        looks_like_plan_failure,
        looks_like_quota_failure,
        looks_like_retired_route,
    )

    blob = (proc.stderr or "") + (proc.stdout or "")
    return (
        looks_like_login_failure(blob)
        or looks_like_plan_failure(blob)
        or looks_like_quota_failure(blob)
        or looks_like_retired_route(blob)
    )


def _cli_error(label: str, proc: subprocess.CompletedProcess[str]) -> RuntimeError:
    """Turn a failed CLI run into an error a user can act on.

    Agent CLIs are inconsistent about which stream carries the reason: Claude
    Code prints "Not logged in \u00b7 Please run /login" on *stdout* and leaves
    stderr empty, so reading stderr alone reduced every auth failure to a bare
    "claude failed: 1".
    """
    from arenatalk.adapters.agent_auth import (
        looks_like_login_failure,
        looks_like_plan_failure,
        looks_like_quota_failure,
        looks_like_retired_route,
    )

    raw = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    detail = _summarize_cli_output(raw) or f"exit {proc.returncode}"
    if looks_like_retired_route(raw):
        detail += (
            "\n\u2192 \uc774 CLI\uac00 \uc4f0\ub358 \ub85c\uadf8\uc778 \ubc29\uc2dd\uc744 \uacf5\uae09\uc0ac\uac00 \uc911\ub2e8\ud588\uc2b5\ub2c8\ub2e4. "
            "\ub2e4\uc2dc \ub85c\uadf8\uc778\ud574\ub3c4 \ud574\uacb0\ub418\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4 \u2014 API \ud0a4\ub97c \ub123\uac70\ub098 "
            "\ub2e4\ub978 CLI\ub97c \uc4f0\uc138\uc694 (Ollama\ub294 \ub85c\uceec \uc2e4\ud589\uc774\ub77c \uacc4\uc815\uc774 \ud544\uc694 \uc5c6\uc2b5\ub2c8\ub2e4)."
        )
    elif looks_like_quota_failure(raw):
        detail += (
            "\n\u2192 \uc774 CLI\uc758 \uc0ac\uc6a9 \ud55c\ub3c4\ub97c \ub2e4 \uc37c\uc2b5\ub2c8\ub2e4 (\uacc4\uc815 \ubb38\uc81c \uc544\ub2d8). "
            "\uc7a0\uc2dc \ub4a4\uc5d0 \ub2e4\uc2dc \ud558\uac70\ub098 \ub2e4\ub978 CLI\ub97c \uace8\ub77c\ubcf4\uc138\uc694 "
            "(Ollama\ub294 \ub85c\uceec \uc2e4\ud589\uc774\ub77c \ud55c\ub3c4\uac00 \uc5c6\uc2b5\ub2c8\ub2e4)."
        )
    elif looks_like_plan_failure(raw):
        detail += (
            "\n\u2192 \ub85c\uadf8\uc778\uc740 \ub410\uc9c0\ub9cc \uc694\uae08\uc81c/\ud06c\ub808\ub527\uc774 \ubd80\uc871\ud569\ub2c8\ub2e4. "
            "\ubb34\ub8cc CLI(Gemini / Qwen / Ollama)\ub85c \ubc14\uafb8\ub824\uba74 "
            "\u300c\uc5d0\uc774\uc804\ud2b8 \uc124\uc815\u300d \u2192 \u300c\ubb34\ub8cc CLI \uc790\ub3d9 \uc124\uce58\u300d."
        )
    elif looks_like_login_failure(raw):
        detail += "\n\u2192 \uc774 CLI\ub294 \ub85c\uadf8\uc778\uc774 \ud544\uc694\ud569\ub2c8\ub2e4. ArenaTalk \u300c\uc5d0\uc774\uc804\ud2b8 \uc124\uc815\u300d \u2192 \u300c\ub85c\uadf8\uc778\u300d, \ub610\ub294 arenatalk login \uc744 \uc2e4\ud589\ud558\uc138\uc694."
    return RuntimeError(f"{label}: {detail}")


def _probe_version(binary: str) -> str:
    for args in (["--version"], ["-v"], ["version"]):
        try:
            proc = subprocess.run(
                [binary, *args],
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                env=agent_path_env(),
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        if out:
            return out[0].strip()[:80]
    return ""


def _resolve_binary(candidates: tuple[str, ...]) -> str | None:
    search = agent_search_path()
    for name in candidates:
        path = shutil.which(name, path=search)
        if path:
            try:
                return str(Path(path).resolve())
            except OSError:
                return path
    return None


def discover_providers(
    *,
    probe_version: bool = True,
    names: frozenset[str] | set[str] | None = None,
    probe_login: bool = False,
) -> list[ProviderInfo]:
    """Scan PATH for installed agent CLIs and return a de-duplicated list.

    ``probe_login`` additionally asks each CLI whether it is signed in. It costs
    one cheap subprocess per provider and no model tokens, but it is the only
    way to tell an installed CLI from a *usable* one.
    """
    hits: list[tuple[str, str, str, str]] = []
    seen_paths: set[str] = set()
    allowed = set(names) if names is not None else None

    for logical, candidates, display, tier in _AGENT_CATALOG:
        if allowed is not None and logical not in allowed:
            continue
        binary = _resolve_binary(candidates)
        if not binary:
            continue
        if binary in seen_paths:
            continue
        seen_paths.add(binary)
        hits.append((logical, binary, display, tier))

    # Probing is subprocess-bound, and doing six of them in series is a visible
    # stall on GUI startup. Fan out instead.
    versions = _map_parallel(
        (_probe_version if probe_version else lambda _b: ""),
        [binary for _, binary, _, _ in hits],
    )
    found = [
        ProviderInfo(
            name=logical,
            binary=binary,
            display_name=display,
            version=version,
            tier=tier,
            notes=f"{tier} · via {Path(binary).name}",
        )
        for (logical, binary, display, tier), version in zip(hits, versions)
    ]
    if probe_login:
        found = attach_login_state(found)
    return found


def _map_parallel(func, items: list):
    """Run ``func`` over ``items`` concurrently, preserving order."""
    if not items:
        return []
    if len(items) == 1:
        return [func(items[0])]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(func, items))


def attach_login_state(providers: list[ProviderInfo]) -> list[ProviderInfo]:
    """Fill in the ``auth`` fields for already-discovered providers."""
    from arenatalk.adapters.agent_auth import probe_auth

    statuses = _map_parallel(
        lambda info: probe_auth(info.name, info.binary), list(providers)
    )
    return [
        replace(
            info,
            auth=status.state,
            auth_detail=status.detail,
            account=status.account,
        )
        for info, status in zip(providers, statuses)
    ]


def ready_providers(providers: list[ProviderInfo]) -> list[ProviderInfo]:
    """Only the CLIs a debate can actually call (installed, signed in, paid)."""
    return [p for p in providers if p.ready]


AUTH_LABELS: dict[str, str] = {
    "ready": "사용 가능",
    "needs_login": "로그인 필요",
    "needs_plan": "요금제 필요",
    "unknown": "확인 불가",
}


def format_provider_list(providers: list[ProviderInfo] | None = None) -> str:
    providers = providers if providers is not None else discover_providers()
    if not providers:
        return "설치된 에이전트 CLI 없음 (claude / codex / agent / gemini)"
    lines = [f"감지된 에이전트 {len(providers)}개:"]
    for i, p in enumerate(providers, 1):
        ver = f" · {p.version}" if p.version else ""
        state = f" · {AUTH_LABELS.get(p.auth, p.auth)}" if p.auth != "unknown" else ""
        lines.append(f"  {i}. {p.display_name or p.name}{ver}{state}")
        lines.append(f"     {p.binary}")
    return "\n".join(lines)


def build_backend(
    name: str,
    binary: str,
    cwd: Path,
    *,
    model: str | None = None,
) -> BackendType:
    if name == "claude":
        return ClaudePrintBackend(binary=binary, cwd=cwd, model=model)
    if name == "codex":
        return CodexExecBackend(binary=binary, cwd=cwd, model=model)
    if name == "cursor":
        return CursorAgentBackend(binary=binary, cwd=cwd, model=model)
    if name == "gemini":
        return GeminiPrintBackend(binary=binary, cwd=cwd, model=model)
    if name == "qwen":
        return QwenPrintBackend(binary=binary, cwd=cwd, model=model)
    if name == "ollama":
        return OllamaRunBackend(binary=binary, cwd=cwd, model=model)
    raise ValueError(f"unknown provider: {name}")


class EnsembleBackend:
    """Auto-discover local agent CLIs and assign them round-robin to cast members."""

    def __init__(
        self,
        providers: list[ProviderInfo] | None = None,
        *,
        work_root: Path | None = None,
        auto_discover: bool = True,
        model_profile: str = "default",
        prefer_paid: bool = True,
    ) -> None:
        if providers is not None:
            self.providers = list(providers)
        elif auto_discover:
            if prefer_paid:
                from arenatalk.adapters.agent_setup import preferred_providers

                self.providers = preferred_providers()
            else:
                self.providers = discover_providers()
        else:
            self.providers = []
        if not self.providers:
            raise RuntimeError(
                "쓸 수 있는 에이전트 CLI가 없습니다.\n"
                "「에이전트 설정」에서 자동 설치·로그인을 하거나, "
                "터미널에서 arenatalk setup 을 실행하세요."
            )
        stranded = [p for p in self.providers if p.blocked]
        if stranded and not any(p.ready for p in self.providers):
            names = ", ".join(
                f"{p.display_name or p.name}({AUTH_LABELS.get(p.auth, p.auth)})"
                for p in stranded
            )
            raise RuntimeError(
                f"바로 쓸 수 있는 CLI가 없습니다: {names}\n"
                "「에이전트 설정」에서 로그인하거나, 무료 CLI를 자동 설치하세요."
            )
        self.model_profile = model_profile
        self.work_root = work_root or Path(tempfile.mkdtemp(prefix="arenatalk-"))
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._backends: dict[str, BackendType] = {}
        self._models: dict[str, str | None] = {}
        for p in self.providers:
            model = resolve_model(p.name, model_profile)
            self._models[p.name] = model
            self._backends[p.name] = build_backend(
                p.name, p.binary, self.work_root, model=model
            )
        self.assignment: dict[str, str] = {}
        self.discovery_summary = format_provider_list(self.providers)

    def model_summary(self) -> str:
        bits = []
        for p in self.providers:
            m = self._models.get(p.name)
            bits.append(f"{p.name}→{m or 'CLI자동'}")
        return ", ".join(bits)

    def assign(self, character_ids: list[str]) -> dict[str, str]:
        names = [p.name for p in self.providers]
        mapping = {cid: names[i % len(names)] for i, cid in enumerate(character_ids)}
        self.assignment = mapping
        return mapping

    def assignment_detail(self) -> list[tuple[str, str, str]]:
        by_name = {p.name: p for p in self.providers}
        rows: list[tuple[str, str, str]] = []
        for cid, pname in self.assignment.items():
            p = by_name.get(pname)
            rows.append((cid, pname, p.binary if p else ""))
        return rows

    def provider_for(self, character_id: str) -> str:
        if character_id not in self.assignment:
            raise KeyError(f"unassigned character: {character_id}")
        return self.assignment[character_id]

    def complete_for(self, character_id: str, system: str, user: str) -> str:
        name = self.provider_for(character_id)
        return self._backends[name].complete(system, user)

    def complete(self, system: str, user: str) -> str:
        name = self.providers[0].name
        return self._backends[name].complete(system, user)
