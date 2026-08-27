"""Agent CLI discovery, login state, error reporting and cancellation."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import pytest

from arenatalk.adapters import agent_setup, cli_agents
from arenatalk.adapters.agent_auth import looks_like_login_failure
from arenatalk.adapters.cli_agents import (
    BackendCancelled,
    ProviderInfo,
    _cli_error,
    _run,
    agent_path_env,
    cancel_active_backends,
    reset_cancel_state,
)


def _info(name: str, tier: str, auth: str) -> ProviderInfo:
    return ProviderInfo(
        name=name, binary=f"/usr/bin/{name}", display_name=name, tier=tier, auth=auth
    )


def test_installed_but_stranded_cli_is_not_ready() -> None:
    assert _info("claude", "paid", "needs_login").ready is False
    assert _info("claude", "paid", "needs_login").needs_login is True
    assert _info("gemini", "free", "ready").ready is True
    # A probe that could not answer must not disqualify a working CLI.
    assert _info("qwen", "free", "unknown").ready is True


def test_preferred_providers_skips_paid_cli_that_needs_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The friend's bug: a never-logged-in `claude` shadowed a working Gemini."""
    paid = [_info("claude", "paid", "needs_login")]
    free = [_info("gemini", "free", "ready")]

    def fake_discover(*, names=None, probe_version=True, probe_login=False):
        wanted = set(names or ())
        if "claude" in wanted:
            return paid
        return free

    monkeypatch.setattr(agent_setup, "discover_providers", fake_discover)
    assert [p.name for p in agent_setup.preferred_providers()] == ["gemini"]


def test_preferred_providers_falls_back_to_stranded_for_reporting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stranded = [_info("claude", "paid", "needs_login")]

    def fake_discover(*, names=None, probe_version=True, probe_login=False):
        return stranded if "claude" in set(names or ()) else []

    monkeypatch.setattr(agent_setup, "discover_providers", fake_discover)
    got = agent_setup.preferred_providers()
    assert [p.name for p in got] == ["claude"]
    assert got[0].needs_login


def test_cli_error_reads_stdout_when_stderr_is_empty() -> None:
    """Claude Code prints "Not logged in" on stdout and leaves stderr empty."""
    proc = subprocess.CompletedProcess(
        ["claude"], returncode=1, stdout="Not logged in · Please run /login", stderr=""
    )
    message = str(_cli_error("Claude Code", proc))
    assert "Not logged in" in message
    assert "claude failed: 1" not in message
    assert "로그인" in message


def test_cli_error_falls_back_to_exit_code() -> None:
    proc = subprocess.CompletedProcess(["gemini"], returncode=3, stdout="", stderr="")
    assert "exit 3" in str(_cli_error("Gemini CLI", proc))


@pytest.mark.parametrize(
    "text",
    ["Not logged in · Please run /login", "Invalid API key", "authentication_error"],
)
def test_login_failure_detection(text: str) -> None:
    assert looks_like_login_failure(text)
    assert not looks_like_login_failure("model returned malformed JSON")


def test_agent_path_env_puts_install_dirs_first() -> None:
    env = agent_path_env({"PATH": "/only/this"})
    assert env["PATH"].endswith("/only/this")
    assert len(env["PATH"]) > len("/only/this")


def test_cancel_raises_backend_cancelled_not_a_failure() -> None:
    """Pressing stop used to surface as a red "CLI failed" dialog."""
    reset_cancel_state()
    box: dict[str, object] = {}

    def call() -> None:
        try:
            box["result"] = _run(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=60
            )
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            box["error"] = exc

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.time() + 10
    while not cli_agents._ACTIVE_PROCS and time.time() < deadline:
        time.sleep(0.02)
    assert cli_agents._ACTIVE_PROCS, "child never registered"
    assert cancel_active_backends() == 1
    thread.join(timeout=20)

    assert not thread.is_alive()
    assert isinstance(box.get("error"), BackendCancelled)
    assert "result" not in box


def test_reset_cancel_state_clears_stale_pids() -> None:
    cli_agents._CANCELLED_PIDS.add(999999)
    reset_cancel_state()
    assert not cli_agents._CANCELLED_PIDS


def test_windows_spawn_kwargs_use_a_new_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows kills by taskkill /T, which needs the child in its own group."""
    monkeypatch.setattr(cli_agents.sys, "platform", "win32")
    kwargs = cli_agents._group_spawn_kwargs()
    assert "start_new_session" not in kwargs
    flags = kwargs["creationflags"]
    assert flags & cli_agents._CREATE_NEW_PROCESS_GROUP
    assert flags & cli_agents._CREATE_NO_WINDOW


def test_posix_spawn_kwargs_use_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_agents.sys, "platform", "linux")
    assert cli_agents._group_spawn_kwargs() == {"start_new_session": True}


def test_windows_path_extras_cover_npm_and_winget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GUI launched from Explorer does not see %APPDATA%\\npm without this."""
    monkeypatch.setattr(cli_agents.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\\Users\\u\\AppData\\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\\Users\\u\\AppData\\Local")
    extras = cli_agents._path_extras()
    assert any("npm" in e for e in extras)
    assert any("WinGet" in e for e in extras)


def test_free_install_skips_ollama_once_an_npm_cli_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows users got no free CLI at all; they must not get a 5GB one now."""
    attempted: list[str] = []

    monkeypatch.setattr(agent_setup, "_is_installed", lambda name: False)
    monkeypatch.setattr(agent_setup, "_prepare_node", lambda say: None)

    def fake_install(name, say):
        attempted.append(name)
        return name == "qwen", f"{name}: ok"

    monkeypatch.setattr(agent_setup, "_try_install", fake_install)

    calls = {"n": 0}

    def fake_discover(*, names=None, probe_version=True, probe_login=False):
        # Nothing at all on the first scan; qwen appears after the install.
        calls["n"] += 1
        return [_info("qwen", "free", "ready")] if calls["n"] > 1 else []

    monkeypatch.setattr(agent_setup, "discover_providers", fake_discover)

    result = agent_setup.ensure_agent_clis(auto_install=True, confirm=lambda _p: True)
    assert attempted == ["qwen"], attempted
    assert result.tier == "free"
    assert [p.name for p in result.ready] == ["qwen"]


def test_gui_startup_does_not_install_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detection is free; installing is always an explicit click."""
    monkeypatch.setattr(
        agent_setup,
        "_try_install",
        lambda name, say: pytest.fail("auto_install=False must not install"),
    )
    monkeypatch.setattr(
        agent_setup,
        "discover_providers",
        lambda **kw: [_info("claude", "paid", "needs_login")],
    )
    result = agent_setup.ensure_agent_clis(auto_install=False)
    assert result.ready == []
    assert [p.name for p in result.needs_login] == ["claude"]


def test_settings_inventory_keeps_a_cli_the_tier_preference_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed-in Gemini must stay reachable for login behind a signed-in Claude."""
    everything = [_info("claude", "paid", "ready"), _info("gemini", "free", "ready")]
    monkeypatch.setattr(agent_setup, "discover_providers", lambda **kw: everything)

    result = agent_setup.ensure_agent_clis(auto_install=False)
    assert [p.name for p in result.providers] == ["claude"]
    assert [p.name for p in result.all_seen] == ["claude", "gemini"]


def test_needs_login_is_reported_from_the_full_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    everything = [_info("claude", "paid", "ready"), _info("gemini", "free", "needs_login")]
    monkeypatch.setattr(agent_setup, "discover_providers", lambda **kw: everything)

    result = agent_setup.ensure_agent_clis(auto_install=False)
    # Selection is happy, but the settings screen still has something to offer.
    assert [p.name for p in result.ready] == ["claude"]
    assert [p.name for p in result.needs_login] == ["gemini"]


def test_all_seen_defaults_to_providers() -> None:
    result = agent_setup.EnsureResult(tier="free", providers=[_info("qwen", "free", "ready")])
    assert [p.name for p in result.all_seen] == ["qwen"]


def test_unpaid_claude_account_is_not_usable() -> None:
    """Signed in is not enough: Claude Code has no free tier."""
    info = _info("claude", "paid", "needs_plan")
    assert info.ready is False
    assert info.blocked is True
    assert info.needs_login is False


def test_unpaid_paid_cli_falls_back_to_free(monkeypatch: pytest.MonkeyPatch) -> None:
    everything = [_info("claude", "paid", "needs_plan"), _info("gemini", "free", "ready")]
    monkeypatch.setattr(agent_setup, "discover_providers", lambda **kw: everything)

    result = agent_setup.ensure_agent_clis(auto_install=False)
    assert [p.name for p in result.providers] == ["gemini"]
    assert [p.name for p in result.blocked] == ["claude"]


def test_claude_probe_reads_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    from arenatalk.adapters import agent_auth

    def fake(payload: dict, rc: int = 0):
        return subprocess.CompletedProcess(
            ["claude"], returncode=rc, stdout=json.dumps(payload), stderr=""
        )

    cases = [
        ({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}, "ready"),
        ({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}, "ready"),
        ({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "free"}, "needs_plan"),
        # Console / API-key users pay per token and report no plan.
        ({"loggedIn": True, "authMethod": "console"}, "ready"),
        # Plan field absent entirely: cannot tell, so do not disqualify.
        ({"loggedIn": True, "authMethod": "claude.ai"}, "ready"),
        ({"loggedIn": False, "authMethod": "none"}, "needs_login"),
    ]
    for payload, expected in cases:
        monkeypatch.setattr(agent_auth, "_run_probe", lambda cmd, p=payload: fake(p))
        assert agent_auth._probe_claude("claude").state == expected, payload


@pytest.mark.parametrize(
    "text",
    [
        "Your credit balance is too low to access the API",
        "insufficient_quota",
        "This feature requires a paid plan",
        "HTTP 402 Payment Required",
    ],
)
def test_plan_failure_detection(text: str) -> None:
    from arenatalk.adapters.agent_auth import looks_like_plan_failure

    assert looks_like_plan_failure(text)


@pytest.mark.parametrize(
    "text",
    ["rate limit exceeded, retry in 30s", "model returned malformed JSON"],
)
def test_plan_failure_ignores_transient_errors(text: str) -> None:
    from arenatalk.adapters.agent_auth import looks_like_plan_failure

    assert not looks_like_plan_failure(text)


def test_cli_error_points_unpaid_users_at_the_free_tier() -> None:
    proc = subprocess.CompletedProcess(
        ["codex"],
        returncode=1,
        stdout="",
        stderr="Your credit balance is too low to access the API",
    )
    message = str(_cli_error("OpenAI Codex", proc))
    assert "요금제" in message
    assert "무료 CLI" in message


def test_summary_prefers_the_error_over_the_stack_tail() -> None:
    """Node CLIs bury the message under a trace; the tail is the useless part."""
    raw = "\n".join([
        "(node:1) [DEP0040] DeprecationWarning: punycode is deprecated.",
        "Loaded cached credentials.",
        "Error authenticating: GaxiosError: Resource has been exhausted (e.g. check quota).",
        "    at Gaxios._request (/x/gaxios.js:142:23)",
        "    at async main (/x/gemini.js:245:17) {",
    ])
    summary = cli_agents._summarize_cli_output(raw)
    assert "Resource has been exhausted" in summary
    assert "at async main" not in summary
    assert "DeprecationWarning" not in summary


def test_quota_and_plan_advice_do_not_get_mixed_up() -> None:
    from arenatalk.adapters.agent_auth import (
        looks_like_plan_failure,
        looks_like_quota_failure,
    )

    quota = "Resource has been exhausted (e.g. check quota)."
    assert looks_like_quota_failure(quota)
    assert not looks_like_plan_failure(quota)     # telling them to pay would be wrong

    billing = "Your credit balance is too low to access the API"
    assert looks_like_plan_failure(billing)
    assert not looks_like_quota_failure(billing)


def test_retired_route_is_not_reported_as_a_login_problem() -> None:
    from arenatalk.adapters.agent_auth import looks_like_retired_route

    text = (
        "IneligibleTierError: This client is no longer supported for Gemini "
        "Code Assist for individuals. Please migrate to the Antigravity suite."
    )
    assert looks_like_retired_route(text)
    # Re-logging-in cannot fix a discontinued route, so never advise it.
    message = str(
        _cli_error("Gemini CLI", subprocess.CompletedProcess(["gemini"], 1, "", text))
    )
    advice = message.split("→")[1]
    assert "중단" in advice
    # It may mention login, but only to say re-logging-in will not help.
    assert "다시 로그인해도 해결되지 않습니다" in advice
    assert "「로그인」" not in advice


def test_account_problems_are_never_retried() -> None:
    """Each of these fails identically on a second run, only slower."""
    from arenatalk.adapters.cli_agents import _is_auth_failure

    for text in (
        "Not logged in · Please run /login",
        "Your credit balance is too low",
        "Resource has been exhausted (e.g. check quota).",
        "IneligibleTierError: no longer supported",
    ):
        proc = subprocess.CompletedProcess(["x"], returncode=1, stdout="", stderr=text)
        assert _is_auth_failure(proc), text
    transient = subprocess.CompletedProcess(
        ["x"], returncode=1, stdout="", stderr="unknown flag --tools"
    )
    assert not _is_auth_failure(transient)


def test_gemini_does_not_use_the_gated_plan_approval_mode() -> None:
    """`--approval-mode plan` needs experimental.plan and aborts every turn."""
    from arenatalk.adapters.cli_agents import GeminiPrintBackend

    captured: dict[str, list[str]] = {}

    def fake_run(cmd, cwd=None, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="{}", stderr="")

    backend = GeminiPrintBackend(binary="gemini")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli_agents, "_run", fake_run)
        backend.complete("sys", "user")
    assert "plan" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--approval-mode") + 1] == "default"


def test_install_needs_explicit_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto_install only makes installing possible; consent makes it happen."""
    monkeypatch.setattr(agent_setup, "discover_providers", lambda **kw: [])
    monkeypatch.setattr(agent_setup, "_is_installed", lambda name: False)
    monkeypatch.setattr(agent_setup, "_prepare_node", lambda say: None)
    monkeypatch.setattr(
        agent_setup,
        "_try_install",
        lambda name, say: pytest.fail("installed without consent"),
    )

    # No confirm callback at all.
    result = agent_setup.ensure_agent_clis(auto_install=True)
    assert result.installed == []
    assert any("승인" in m for m in result.messages)

    # Callback that says no.
    result = agent_setup.ensure_agent_clis(auto_install=True, confirm=lambda _p: False)
    assert result.installed == []


def test_consent_callback_sees_what_will_be_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_setup, "discover_providers", lambda **kw: [])
    monkeypatch.setattr(agent_setup, "_is_installed", lambda name: False)
    monkeypatch.setattr(agent_setup, "_prepare_node", lambda say: None)
    monkeypatch.setattr(agent_setup, "_try_install", lambda name, say: (False, "no"))

    seen: list = []
    agent_setup.ensure_agent_clis(
        auto_install=True, confirm=lambda plan: seen.append(plan) or False
    )
    assert len(seen) == 1
    plan = seen[0]
    assert plan.candidates == list(agent_setup.FREE_INSTALL_ORDER)
    assert plan.heavy is True                     # ollama is in the plan
    text = " ".join(plan.describe())
    assert "npm install -g" in text
    assert "수 GB" in text


def test_plan_reports_a_missing_node(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_setup, "_is_installed", lambda name: False)
    monkeypatch.setattr(agent_setup, "_find_npm", lambda: None)
    plan = agent_setup.plan_free_install()
    assert plan.node_needed
    assert "Node.js" in " ".join(plan.describe())


def test_node_too_old_is_caught_before_installing_a_broken_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ubuntu 22.04 ships node v12; npm succeeds and the CLI dies on line one."""
    monkeypatch.setattr(agent_setup, "_find_npm", lambda: "/usr/bin/npm")
    monkeypatch.setattr(agent_setup, "node_major", lambda: 12)
    assert agent_setup.node_is_usable() is False
    assert agent_setup.plan_free_install().node_needed is True

    ok, note = agent_setup._install_npm("qwen", "@qwen-code/qwen-code@latest")
    assert ok is False
    assert "v12" in note


def test_node_version_unknown_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_setup, "_find_npm", lambda: "/usr/bin/npm")
    monkeypatch.setattr(agent_setup, "node_major", lambda: None)
    assert agent_setup.node_is_usable() is True


def test_qwen_missing_credentials_reads_as_a_login_problem() -> None:
    from arenatalk.adapters.agent_auth import looks_like_login_failure

    assert looks_like_login_failure(
        "No auth type is selected. Please configure an auth type "
        "(e.g. via settings or `--auth-type`) before running in non-interactive mode."
    )


def _ensemble(names: list[str]):
    from arenatalk.adapters.cli_agents import EnsembleBackend

    return EnsembleBackend(
        providers=[_info(n, "paid", "ready") for n in names], auto_discover=False
    )


class _FakeCLI:
    def __init__(self, name: str, error: str | None = None) -> None:
        self.name, self.error, self.calls = name, error, 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return self.name


RATE_LIMIT = "Codex: 429 Too Many Requests / rate limit exceeded"


def test_a_rate_limited_provider_does_not_end_the_debate() -> None:
    """One CLI hitting its limit used to fail every turn assigned to it."""
    eb = _ensemble(["claude", "codex", "cursor"])
    events: list[str] = []
    eb.on_event = events.append
    eb._backends = {
        "claude": _FakeCLI("claude"),
        "codex": _FakeCLI("codex", RATE_LIMIT),
        "cursor": _FakeCLI("cursor"),
    }
    eb.assign(["a", "b", "c", "d"])
    answers = [eb.complete_for(cid, "s", "u") for cid in ("a", "b", "c", "d")]

    assert "codex" not in answers, answers
    assert len(answers) == 4
    assert eb.healthy_names() == ["claude", "cursor"]
    assert any("한도" in e for e in events), events


def test_a_dead_provider_is_not_tried_a_second_time() -> None:
    eb = _ensemble(["claude", "codex"])
    codex = _FakeCLI("codex", RATE_LIMIT)
    eb._backends = {"claude": _FakeCLI("claude"), "codex": codex}
    eb.assign(["a", "b", "c", "d"])
    for cid in ("a", "b", "c", "d"):
        eb.complete_for(cid, "s", "u")
    assert codex.calls == 1, "the exhausted CLI should be asked exactly once"


def test_an_ordinary_error_does_not_retire_a_provider() -> None:
    """Only account/quota failures repeat for every turn; others may be flukes."""
    eb = _ensemble(["claude", "codex"])
    eb._backends = {
        "claude": _FakeCLI("claude"),
        "codex": _FakeCLI("codex", "model returned malformed JSON"),
    }
    eb.assign(["a", "b"])
    eb.complete_for("a", "s", "u")
    with pytest.raises(RuntimeError, match="malformed"):
        eb.complete_for("b", "s", "u")
    assert eb.healthy_names() == ["claude", "codex"]


def test_when_every_provider_is_exhausted_the_error_says_so() -> None:
    eb = _ensemble(["claude", "codex"])
    eb._backends = {
        "claude": _FakeCLI("claude", RATE_LIMIT),
        "codex": _FakeCLI("codex", RATE_LIMIT),
    }
    eb.assign(["a", "b"])
    with pytest.raises(RuntimeError) as caught:
        for cid in ("a", "b"):
            eb.complete_for(cid, "s", "u")
    assert "모두 소진" in str(caught.value)


def test_cancel_is_never_mistaken_for_an_exhausted_provider() -> None:
    from arenatalk.adapters.cli_agents import BackendCancelled

    eb = _ensemble(["claude", "codex"])

    class _Cancelled:
        def complete(self, system: str, user: str) -> str:
            raise BackendCancelled("사용자가 토론을 중지했습니다.")

    eb._backends = {"claude": _Cancelled(), "codex": _FakeCLI("codex")}
    eb.assign(["a"])
    with pytest.raises(BackendCancelled):
        eb.complete_for("a", "s", "u")
    assert eb.healthy_names() == ["claude", "codex"]


def test_failover_walks_the_whole_bench_before_giving_up() -> None:
    """A retry that also hits a limit must retire that CLI too, not just raise."""
    eb = _ensemble(["claude", "codex", "cursor"])
    events: list[str] = []
    eb.on_event = events.append
    eb._backends = {
        "claude": _FakeCLI("claude", RATE_LIMIT),
        "codex": _FakeCLI("codex", RATE_LIMIT),
        "cursor": _FakeCLI("cursor"),
    }
    eb.assign(["a"])
    assert eb.complete_for("a", "s", "u") == "cursor"
    assert eb.healthy_names() == ["cursor"]
    assert sum("제외" in e for e in events) == 2, events


def test_gui_survives_a_worker_failure_during_the_error_dialog() -> None:
    """The reported crash: the modal pumps the loop, the QThread dies mid-cleanup."""
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    thread = QThread()
    thread.start()

    class _Window:
        _thread = thread
        _worker = None

    from arenatalk.game.window import GameWindow

    cleanup = GameWindow._cleanup_thread
    cleanup(_Window)                       # first pass stops and schedules delete
    assert _Window._thread is None
    for _ in range(20):
        app.processEvents()                # what the modal dialog does

    _Window._thread = thread               # a stale reference to a dead object
    cleanup(_Window)                       # must not raise RuntimeError
    assert _Window._thread is None
