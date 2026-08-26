"""Shared agent backend construction for CLI and GUI."""

from __future__ import annotations

from pathlib import Path

from arenatalk.adapters.agent_setup import ensure_agent_clis, preferred_providers
from arenatalk.adapters.base import MockBackend
from arenatalk.adapters.cli_agents import (
    ClaudePrintBackend,
    CodexExecBackend,
    CursorAgentBackend,
    EnsembleBackend,
    GeminiPrintBackend,
    OllamaRunBackend,
    ProviderInfo,
    QwenPrintBackend,
    resolve_model,
)


def build_backend(
    key: str,
    *,
    providers: list[ProviderInfo] | None = None,
    model_profile: str = "default",
    mock_think_seconds: float = 0.9,
) -> object:
    """Build a debate backend from a CLI/GUI backend key.

    ``key`` is one of: all | mock | claude | codex | cursor | gemini | qwen | ollama
    """
    profile = (model_profile or "default").strip().lower()
    if key == "mock":
        return MockBackend(think_seconds=mock_think_seconds)
    if key == "all":
        resolved = providers
        if resolved is None:
            setup = ensure_agent_clis(auto_install=False)
            resolved = setup.providers or preferred_providers()
        return EnsembleBackend(providers=list(resolved) if resolved else None, model_profile=profile)

    model = resolve_model(str(key), profile)
    if key == "claude":
        return ClaudePrintBackend(model=model)
    if key == "codex":
        return CodexExecBackend(model=model)
    if key == "gemini":
        return GeminiPrintBackend(model=model)
    if key == "qwen":
        return QwenPrintBackend(model=model)
    if key == "ollama":
        return OllamaRunBackend(model=model)
    if key == "cursor":
        return CursorAgentBackend(model=model)
    raise ValueError(f"unknown backend: {key}")
