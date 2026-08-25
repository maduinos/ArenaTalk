from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderInfo:
    """A discovered local agent CLI usable as a debate backend."""

    name: str
    binary: str
    display_name: str = ""
    version: str = ""
    kind: str = "cli"  # cli
    notes: str = ""

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
# Override any with env AREATALK_MODEL_<NAME> (e.g. AREATALK_MODEL_CLAUDE=fable).
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
    env_key = f"AREATALK_MODEL_{provider.upper()}"
    if os.environ.get(env_key):
        return os.environ[env_key].strip() or None
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
        if proc.returncode != 0:
            cmd2 = [self.binary, "-p", prompt]
            if self.model:
                cmd2.extend(["--model", self.model])
            proc = _run(cmd2, cwd=self.cwd)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"claude failed: {proc.returncode}")
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
        if proc.returncode != 0 and "unknown" in (proc.stderr or "").lower():
            cmd2 = [self.binary, "exec"]
            if self.model:
                cmd2.extend(["-m", self.model])
            cmd2.append(prompt)
            proc = _run(cmd2, cwd=self.cwd)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"codex failed: {proc.returncode}")
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
            raise RuntimeError(
                proc.stderr.strip() or f"cursor agent failed: {proc.returncode}"
            )
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
            "--approval-mode",
            "plan",
        ]
        if self.model:
            cmd.extend(["-m", self.model])
        proc = _run(cmd, cwd=self.cwd)
        if proc.returncode != 0:
            cmd2 = [self.binary, "-p", prompt]
            if self.model:
                cmd2.extend(["-m", self.model])
            proc = _run(cmd2, cwd=self.cwd)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"gemini failed: {proc.returncode}")
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
            raise RuntimeError(proc.stderr.strip() or f"qwen failed: {proc.returncode}")
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
        proc = _run(cmd, cwd=self.cwd, timeout=900)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"ollama failed: {proc.returncode}")
        return proc.stdout.strip()


BackendType = (
    ClaudePrintBackend
    | CodexExecBackend
    | CursorAgentBackend
    | GeminiPrintBackend
    | QwenPrintBackend
    | OllamaRunBackend
)


def _run(cmd: list[str], cwd: str | None = None, *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


def _probe_version(binary: str) -> str:
    for args in (["--version"], ["-v"], ["version"]):
        try:
            proc = subprocess.run(
                [binary, *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        if out:
            return out[0].strip()[:80]
    return ""


def _resolve_binary(candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        path = shutil.which(name)
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
) -> list[ProviderInfo]:
    """Scan PATH for installed agent CLIs and return a de-duplicated list."""
    found: list[ProviderInfo] = []
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
        version = _probe_version(binary) if probe_version else ""
        which_name = Path(binary).name
        found.append(
            ProviderInfo(
                name=logical,
                binary=binary,
                display_name=display,
                version=version,
                notes=f"{tier} · via {which_name}",
            )
        )
    return found


def format_provider_list(providers: list[ProviderInfo] | None = None) -> str:
    providers = providers if providers is not None else discover_providers()
    if not providers:
        return "설치된 에이전트 CLI 없음 (claude / codex / agent / gemini)"
    lines = [f"감지된 에이전트 {len(providers)}개:"]
    for i, p in enumerate(providers, 1):
        ver = f" · {p.version}" if p.version else ""
        lines.append(f"  {i}. {p.display_name or p.name}{ver}")
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
                "에이전트 CLI를 찾지 못했습니다.\n"
                "arenatalk setup 으로 유료/무료 CLI 자동 설치 후 다시 시도하세요."
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
