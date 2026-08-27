from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from arenatalk.adapters.agent_setup import ensure_agent_clis, preferred_providers
from arenatalk.adapters.cli_agents import AUTH_LABELS
from arenatalk.adapters.factory import build_backend
from arenatalk.characters import active_character_root, load_characters
from arenatalk.config import setup_stdio
from arenatalk.engines.debate import run_debate
from arenatalk.logs import DebateLogStore
from arenatalk.ranking import RankingStore

console = Console()
DEFAULT_DB = Path.home() / ".local/share/arenatalk/rankings.db"
_CMDS = frozenset(
    {
        "list",
        "ranks",
        "backends",
        "debate",
        "play",
        "setup",
        "install",
        "characters",
        "login",
        "experts",
    }
)


def _confirm_install(plan) -> bool:
    """Show exactly what will be installed and wait for a yes.

    Installing puts global packages — and possibly a multi-gigabyte model — on
    the user's machine, so it never happens as a side effect of running setup.
    """
    console.print("[bold]다음을 설치합니다:[/bold]")
    for line in plan.describe():
        console.print(f"  • {line}")
    if plan.heavy:
        console.print("[yellow]  ※ ollama까지 가면 모델 다운로드가 수 GB입니다.[/yellow]")
    if not sys.stdin.isatty():
        console.print(
            "[yellow]대화형 터미널이 아니어서 물어볼 수 없습니다 — "
            "설치하려면 [bold]--yes[/bold] 를 붙이세요.[/yellow]"
        )
        return False
    try:
        answer = input("설치할까요? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    return answer in {"y", "yes"}


def _cmd_login(provider: str | None, *, status_only: bool = False) -> int:
    """Show login state and open the CLIs' own sign-in flows.

    Sign-in always ends in a browser round-trip, so the most a tool can do is
    put the user in front of the right command — this opens a terminal already
    running it, then tells them to re-check.
    """
    from arenatalk.adapters.agent_setup import login_provider
    from arenatalk.adapters.cli_agents import discover_providers

    names = frozenset({provider}) if provider else None
    providers = discover_providers(names=names, probe_login=True)
    if not providers:
        target = provider or "에이전트"
        console.print(f"[red]{target} CLI가 설치돼 있지 않습니다.[/red]")
        console.print("[dim]arenatalk setup 으로 무료 CLI를 자동 설치할 수 있습니다.[/dim]")
        return 1

    for p in providers:
        state = AUTH_LABELS.get(p.auth, p.auth)
        colour = "green" if p.auth == "ready" else ("red" if p.blocked else "yellow")
        extra = p.account or p.auth_detail
        console.print(
            f"  • {p.display_name or p.name}: [{colour}]{state}[/{colour}]"
            + (f" [dim]({extra})[/dim]" if extra else "")
        )
    if status_only:
        return 0

    targets = [p for p in providers if p.needs_login] if provider is None else providers
    if not targets:
        console.print("[green]모두 로그인되어 있습니다.[/green]")
        return 0

    failed = 0
    for p in targets:
        ok, message = login_provider(p)
        console.print(
            f"[bold]{p.display_name or p.name}[/bold]: {message}"
            if ok
            else f"[red]{p.display_name or p.name}: {message}[/red]"
        )
        if not ok:
            failed += 1
    console.print(
        "[dim]로그인을 마친 뒤 arenatalk login --status 로 확인하세요.[/dim]"
    )
    return 1 if failed else 0


def _cmd_install(
    *,
    with_agents: bool = True,
    with_desktop: bool = True,
    assume_yes: bool = False,
) -> int:
    """Reinstall ArenaTalk package, optional desktop entry, then agent CLI setup."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    console.print("[bold]1/3[/bold] pip install -e '.[gui]'")
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".[gui]"],
        cwd=str(root),
        check=False,
    )
    if pip.returncode != 0:
        console.print("[red]pip install 실패[/red]")
        return pip.returncode

    if with_desktop:
        desktop = root / "packaging" / "install-desktop.sh"
        console.print("[bold]2/3[/bold] desktop launcher")
        if desktop.is_file():
            subprocess.run(["bash", str(desktop)], check=False)
        else:
            console.print("[yellow]install-desktop.sh 없음 — 건너뜀[/yellow]")
    else:
        console.print("[dim]2/3 desktop — skipped[/dim]")

    if with_agents:
        console.print(
            "[bold]3/3[/bold] 에이전트: 유료 검사 → 없으면 무료 Top3 자동 설치"
        )
        result = ensure_agent_clis(
            auto_install=True,
            confirm=(lambda _plan: True) if assume_yes else _confirm_install,
        )
        for line in result.messages:
            console.print(f"[dim]{line}[/dim]")
        if result.installed:
            console.print(f"[green]설치됨:[/green] {', '.join(result.installed)}")
        if result.providers:
            tier = "유료" if result.tier == "paid" else "무료"
            console.print(f"[green]{tier} 에이전트 {len(result.providers)}개 준비[/green]")
            return 0
        console.print("[yellow]에이전트 CLI 없음 — mock 으로 실행 가능[/yellow]")
        return 0

    console.print("[dim]3/3 agents — skipped[/dim]")
    console.print("[green]ArenaTalk 설치 완료[/green]")
    return 0


def _cmd_characters(args: argparse.Namespace) -> int:
    if args.characters_command == "root":
        from arenatalk.characters import sync_character_library

        if args.set is not None:
            try:
                path = sync_character_library(Path(args.set))
            except (OSError, NotADirectoryError, RuntimeError, ImportError) as exc:
                console.print(f"[red]캐릭터 폴더 설정 실패:[/red] {exc}")
                return 1
            console.print(f"[green]캐릭터 폴더 저장 (AgentPet characters.path):[/green] {path}")
            return 0
        if args.clear:
            try:
                path = sync_character_library(None)
            except ImportError as exc:
                console.print(f"[red]AgentPet 필요:[/red] {exc}")
                return 1
            console.print("[yellow]저장된 캐릭터 폴더를 지웠습니다.[/yellow]")
            console.print(f"기본값: {path}")
            return 0
        root = active_character_root()
        console.print(str(root))
        console.print("[dim]CharacterPet 호환 · AgentPet characters.path[/dim]")
        return 0

    root = active_character_root(getattr(args, "characters", None))
    chars = load_characters(getattr(args, "characters", None))
    console.print(f"[dim]root: {root}[/dim]")
    table = Table(title=f"Characters ({len(chars)})")
    table.add_column("id")
    table.add_column("name")
    table.add_column("archetype")
    table.add_column("interests")
    for c in chars:
        table.add_row(
            c.id,
            c.display_name,
            c.persona.archetype,
            ", ".join(c.persona.interests[:4]),
        )
    console.print(table)
    return 0


def _launch_gui(characters: Path | None = None) -> int:
    try:
        from arenatalk.game.window import run_game
    except ImportError:
        console.print(
            "[red]GUI 의존성 없음[/red]: pip install -e '.[gui]'  (PySide6 필요)"
        )
        return 1
    return run_game(characters)


def main(argv: list[str] | None = None) -> int:
    # Before argparse or rich can write: the help text is Korean and the
    # Windows console defaults to a codec that cannot encode it.
    setup_stdio()

    raw = list(sys.argv[1:] if argv is None else argv)

    # Bare launch → GUI
    if not raw:
        return _launch_gui()

    # GUI flags only (no subcommand): arenatalk --characters /path
    if raw[0] not in _CMDS and raw[0] not in {"-h", "--help"}:
        if raw[0].startswith("-"):
            gparser = argparse.ArgumentParser(prog="arenatalk")
            gparser.add_argument("--characters", type=Path, default=None)
            gargs = gparser.parse_args(raw)
            return _launch_gui(gargs.characters)
        console.print(f"[red]알 수 없는 명령:[/red] {raw[0]}")
        console.print(
            "GUI: arenatalk   CLI: arenatalk list|ranks|debate|backends|play|characters"
        )
        return 2

    parser = argparse.ArgumentParser(
        prog="arenatalk",
        description="ArenaTalk — 인자 없이 실행하면 GUI. CLI는 하위 명령 사용.",
        epilog="예: arenatalk | ./play | arenatalk debate '주제' | arenatalk characters root",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_list = sub.add_parser("list", help="로드된 캐릭터와 페르소나 요약")
    p_list.add_argument("--characters", type=Path, default=None)

    p_chars = sub.add_parser(
        "characters",
        help="캐릭터 목록 / AgentPet 방식 캐릭터 폴더 설정 (root)",
    )
    p_chars.add_argument("--characters", type=Path, default=None)
    char_sub = p_chars.add_subparsers(dest="characters_command")
    p_root = char_sub.add_parser(
        "root",
        help="캐릭터 폴더 경로 출력 (AgentPet 기본과 동일)",
    )
    p_root.add_argument(
        "--set",
        type=Path,
        default=None,
        help="캐릭터 폴더를 AgentPet characters.path에 저장",
    )
    p_root.add_argument(
        "--clear",
        action="store_true",
        help="저장된 캐릭터 폴더를 지우고 AgentPet 기본 경로로 복귀",
    )

    p_rank = sub.add_parser("ranks", help="Elo 리더보드")
    p_rank.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_rank.add_argument(
        "--reset",
        action="store_true",
        help="Elo · 승패 · 매치 기록을 모두 초기화",
    )

    sub.add_parser("backends", help="설치된 에이전트 CLI 목록 + 로그인/할당 상태")

    p_setup = sub.add_parser(
        "setup",
        help="유료 CLI 검사 → 없으면 무료 Top3(gemini/qwen/ollama) 자동 설치",
    )
    p_setup.add_argument(
        "--no-install",
        action="store_true",
        help="설치 없이 감지만",
    )
    p_setup.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="설치 확인 질문 건너뛰기 (비대화형/스크립트용)",
    )

    p_login = sub.add_parser(
        "login",
        help="에이전트 CLI 로그인 창 열기 (인자 없으면 로그인 안 된 것 전부)",
    )
    p_login.add_argument(
        "provider",
        nargs="?",
        choices=("claude", "codex", "cursor", "gemini", "qwen", "ollama"),
        default=None,
    )
    p_login.add_argument(
        "--status",
        action="store_true",
        help="로그인 상태만 출력하고 창은 열지 않음",
    )

    p_install = sub.add_parser(
        "install",
        help="ArenaTalk 로컬 재설치 + 에이전트 자동 설정 (유료 검사 → 없으면 무료 설치)",
    )
    p_install.add_argument(
        "--no-agents",
        action="store_true",
        help="에이전트 CLI 자동 설치 건너뛰기",
    )
    p_install.add_argument(
        "--no-desktop",
        action="store_true",
        help="데스크톱 아이콘/런처 설치 건너뛰기",
    )
    p_install.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="에이전트 설치 확인 질문 건너뛰기",
    )

    p_debate = sub.add_parser("debate", help="주제 토론 실행")
    p_debate.add_argument("topic", help="논의 주제")
    p_debate.add_argument("--rounds", type=int, default=2)
    p_debate.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_debate.add_argument(
        "--backend",
        choices=("all", "mock", "claude", "codex", "cursor", "gemini", "qwen", "ollama"),
        default="all",
        help="all=설치된 에이전트 자동 탐지·할당(기본)",
    )
    p_debate.add_argument(
        "--no-parallel",
        action="store_true",
        help="캐릭 발언을 한 명씩 직렬 실행",
    )
    p_debate.add_argument("--characters", type=Path, default=None)
    p_debate.add_argument(
        "--domain",
        default=None,
        help="이 분야 전문가만 출전 (예: '주식·투자·경제'). "
        "목록은 arenatalk experts",
    )
    p_debate.add_argument(
        "--experts",
        type=int,
        default=3,
        help="--domain 사용 시 출전 인원 (기본 3)",
    )

    p_experts = sub.add_parser(
        "experts",
        help="분야별 전문가 순위 (GUI 「전문가」 탭과 같은 점수)",
    )
    p_experts.add_argument("domain", nargs="?", default=None, help="비우면 분야 목록")
    p_experts.add_argument("--characters", type=Path, default=None)

    p_play = sub.add_parser("play", help="게임형 GUI (기본과 동일)")
    p_play.add_argument("--characters", type=Path, default=None)

    args = parser.parse_args(raw)

    if args.cmd is None or args.cmd == "play":
        return _launch_gui(getattr(args, "characters", None))

    if args.cmd == "characters":
        return _cmd_characters(args)

    if args.cmd == "backends":
        result = ensure_agent_clis(auto_install=False)
        # Everything installed, with a marker for what the round-robin picks.
        providers = result.all_seen or preferred_providers()
        chosen = {p.name for p in result.providers}
        if not providers:
            console.print("[red]연결된 CLI 없음[/red]")
            console.print(
                "[dim]arenatalk setup 으로 유료 검사 후 없으면 무료 CLI 자동 설치[/dim]"
            )
            return 1
        tier_label = {"paid": "유료", "free": "무료", "none": "없음"}.get(
            result.tier, result.tier
        )
        table = Table(title=f"Agent CLIs · {tier_label} ({len(providers)})")
        table.add_column("#")
        table.add_column("name")
        table.add_column("display")
        table.add_column("version")
        table.add_column("login")
        table.add_column("자동 할당")
        table.add_column("binary")
        for i, p in enumerate(providers, 1):
            state = AUTH_LABELS.get(p.auth, p.auth)
            if p.blocked:
                state = f"[red]{state}[/red]"
            elif p.auth == "ready":
                state = f"[green]{state}[/green]"
            table.add_row(
                str(i),
                p.name,
                p.display_name or p.name,
                p.version or "-",
                state,
                "○" if p.name in chosen else "-",
                p.binary,
            )
        console.print(table)
        stranded = [p for p in providers if p.blocked]
        if stranded:
            for p in stranded:
                fix = (
                    "arenatalk setup (무료 CLI 설치)"
                    if p.needs_plan
                    else f"arenatalk login {p.name}"
                )
                console.print(
                    f"[yellow]{p.name}: {AUTH_LABELS.get(p.auth, p.auth)}[/yellow]"
                    f" — [bold]{fix}[/bold]"
                )
        console.print(
            "[dim]--backend all 은 「자동 할당 ○」만 사용합니다 (유료 우선). "
            "나머지는 --backend <name> 으로 직접 지정하세요.[/dim]"
        )
        return 0

    if args.cmd == "setup":
        result = ensure_agent_clis(
            auto_install=not args.no_install,
            confirm=(lambda _plan: True) if args.yes else _confirm_install,
        )
        for line in result.messages:
            console.print(f"[dim]{line}[/dim]")
        if result.installed:
            console.print(f"[green]설치됨:[/green] {', '.join(result.installed)}")
        usable = result.ready
        if usable:
            tier = "유료" if result.tier == "paid" else "무료"
            console.print(f"[green]{tier} 에이전트 {len(usable)}개 준비[/green]")
            for p in usable:
                ver = f" · {p.version}" if p.version else ""
                who = f" · {p.account}" if p.account else ""
                console.print(f"  • {p.display_name or p.name}{ver}{who}")
            return 0
        stranded = result.blocked
        if stranded:
            for p in stranded:
                console.print(
                    f"[yellow]{p.display_name or p.name}: "
                    f"{AUTH_LABELS.get(p.auth, p.auth)}[/yellow]"
                    + (f" — {p.auth_detail}" if p.auth_detail else "")
                )
            if all(p.needs_plan for p in stranded):
                console.print(
                    "  요금제가 없으면 로그인해도 돌지 않습니다 — "
                    "[bold]arenatalk setup[/bold] 으로 무료 CLI를 설치하세요."
                )
            else:
                console.print(
                    "  [bold]arenatalk login[/bold] 을 실행하면 로그인 창이 열립니다."
                )
            return 1
        console.print("[yellow]사용 가능한 CLI 없음 — mock 백엔드만 가능[/yellow]")
        return 1

    if args.cmd == "login":
        return _cmd_login(args.provider, status_only=args.status)

    if args.cmd == "experts":
        from arenatalk.expertise import domain_names, rank_experts

        if not args.domain:
            console.print("[bold]분야[/bold]")
            for name in domain_names():
                console.print(f"  • {name}")
            console.print("\n[dim]arenatalk experts '주식·투자·경제'[/dim]")
            return 0
        chars = load_characters(args.characters)
        ranked = rank_experts(chars, args.domain)
        if not any(m.score for m in ranked):
            console.print(f"[yellow]'{args.domain}' 분야를 찾지 못했습니다.[/yellow]")
            console.print("[dim]arenatalk experts 로 분야 목록을 보세요.[/dim]")
            return 1
        table = Table(title=f"{args.domain} ({sum(m.is_expert for m in ranked)}명)")
        table.add_column("#")
        table.add_column("character")
        table.add_column("전문 태그")
        table.add_column("점수", justify="right")
        for i, m in enumerate(ranked, 1):
            if not m.score:
                continue
            name = m.character.display_name or m.character.id
            table.add_row(
                str(i),
                f"[green]{name}[/green]" if m.is_expert else name,
                m.tags or "-",
                f"{m.score:.2f}",
            )
        console.print(table)
        return 0

    if args.cmd == "install":
        return _cmd_install(
            with_agents=not args.no_agents,
            with_desktop=not args.no_desktop,
            assume_yes=args.yes,
        )

    if args.cmd == "list":
        chars = load_characters(args.characters)
        root = active_character_root(args.characters)
        console.print(f"[dim]root: {root}[/dim]")
        table = Table(title=f"Characters ({len(chars)})")
        table.add_column("id")
        table.add_column("name")
        table.add_column("archetype")
        table.add_column("interests")
        for c in chars:
            table.add_row(
                c.id,
                c.display_name,
                c.persona.archetype,
                ", ".join(c.persona.interests[:4]),
            )
        console.print(table)
        return 0

    if args.cmd == "ranks":
        store = RankingStore(args.db)
        if args.reset:
            store.reset()
            console.print("[yellow]Elo 랭킹을 초기화했습니다.[/yellow]")
        rows = store.leaderboard()
        table = Table(title="ArenaTalk Elo")
        table.add_column("#", justify="right")
        table.add_column("character")
        table.add_column("elo", justify="right")
        table.add_column("W", justify="right")
        table.add_column("L", justify="right")
        table.add_column("D", justify="right")
        table.add_column("WR", justify="right")
        for i, r in enumerate(rows, 1):
            table.add_row(
                str(i),
                r.character_id,
                f"{r.elo:.1f}",
                str(r.wins),
                str(r.losses),
                str(r.draws),
                f"{r.win_rate:.0%}",
            )
        console.print(table)
        return 0

    if args.cmd == "debate":
        chars = load_characters(args.characters)
        if len(chars) < 2:
            console.print("[red]페르소나 있는 캐릭이 2명 이상 필요합니다.[/red]")
            console.print(
                f"[dim]캐릭터 폴더: {active_character_root(args.characters)}[/dim]"
            )
            console.print(
                "[dim]설정: arenatalk characters root --set /path/to/characters[/dim]"
            )
            return 1
        store = RankingStore(args.db)
        try:
            backend = build_backend(args.backend)
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]백엔드 오류:[/red] {exc}")
            return 1
        expert_cast = None
        if args.domain:
            from arenatalk.expertise import EXPERT_DOMAINS, suggest_experts

            if args.domain not in EXPERT_DOMAINS:
                console.print(f"[red]모르는 분야:[/red] {args.domain}")
                console.print("[dim]arenatalk experts 로 목록을 보세요.[/dim]")
                return 1
            matches = suggest_experts(chars, args.domain, limit=max(2, args.experts))
            expert_cast = [m.character for m in matches]
            console.print(
                f"[green]{args.domain} 전문가 {len(expert_cast)}명 출전:[/green] "
                + ", ".join(
                    f"{m.character.display_name or m.character.id}({m.score:.2f})"
                    for m in matches
                )
            )
            if not any(m.is_expert for m in matches):
                console.print(
                    "[yellow]이 분야 전문가가 없어 점수가 가장 가까운 캐릭으로 진행합니다.[/yellow]"
                )

        result = run_debate(
            args.topic,
            chars,
            backend,  # type: ignore[arg-type]
            store,
            cast=expert_cast,
            rounds=args.rounds,
            parallel=not args.no_parallel,
            research=True,
            on_event=lambda m: console.print(f"[dim]{m}[/dim]"),
        )

        console.rule("Cast")
        for cid, score in result.interest_scores.items():
            via = result.providers.get(cid, args.backend)
            console.print(f"  {cid}: interest={score:.2f}  via={via}")

        console.rule("Transcript")
        for row in result.transcript:
            console.print(f"[bold]{row['character_id']}[/] ({row.get('role')})")
            console.print(f"  {row['speech']}")

        console.rule("Result probabilities")
        for label, p in sorted(result.recommendation_dist.items(), key=lambda x: -x[1]):
            console.print(f"  {label}: {p:.0%}")
        console.print(f"  consensus_p: {result.consensus_p:.0%}")
        console.print(f"  mean_confidence: {result.mean_confidence:.0%}")
        console.print(f"  winner: {result.winner_id or 'draw'}")
        if result.audience_dist:
            console.rule("Lounge opinion")
            for label, p in sorted(result.audience_dist.items(), key=lambda x: -x[1]):
                console.print(f"  {label}: {p:.0%}")
            console.print(f"  audience_consensus_p: {result.audience_consensus_p:.0%}")
            for b in result.audience_ballots:
                console.print(
                    f"  · {b.character_id}: {b.recommendation} ({b.confidence:.0%})"
                )
        console.print(f"  elo_delta: {result.rankings_delta}")
        names = {c.id: c.display_name for c in chars}
        log_path = DebateLogStore().save(result, display_names=names)
        console.print(f"  log: {log_path}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
