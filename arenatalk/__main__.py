from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from arenatalk.adapters.agent_setup import ensure_agent_clis, preferred_providers
from arenatalk.adapters.factory import build_backend
from arenatalk.characters import active_character_root, load_characters
from arenatalk.config import setup_stdio
from arenatalk.engines.debate import run_debate
from arenatalk.logs import DebateLogStore
from arenatalk.ranking import RankingStore

console = Console()
DEFAULT_DB = Path.home() / ".local/share/arenatalk/rankings.db"
_CMDS = frozenset(
    {"list", "ranks", "backends", "debate", "play", "setup", "install", "characters"}
)


def _cmd_install(*, with_agents: bool = True, with_desktop: bool = True) -> int:
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
        result = ensure_agent_clis(auto_install=True)
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

    sub.add_parser("backends", help="연결된 에이전트 CLI 목록 (유료 우선)")

    p_setup = sub.add_parser(
        "setup",
        help="유료 CLI 검사 → 없으면 무료 Top3(gemini/qwen/ollama) 자동 설치",
    )
    p_setup.add_argument(
        "--no-install",
        action="store_true",
        help="설치 없이 감지만",
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

    p_play = sub.add_parser("play", help="게임형 GUI (기본과 동일)")
    p_play.add_argument("--characters", type=Path, default=None)

    args = parser.parse_args(raw)

    if args.cmd is None or args.cmd == "play":
        return _launch_gui(getattr(args, "characters", None))

    if args.cmd == "characters":
        return _cmd_characters(args)

    if args.cmd == "backends":
        result = ensure_agent_clis(auto_install=False)
        providers = result.providers or preferred_providers()
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
        table.add_column("binary")
        for i, p in enumerate(providers, 1):
            table.add_row(
                str(i), p.name, p.display_name or p.name, p.version or "-", p.binary
            )
        console.print(table)
        console.print("[dim]debate --backend all 시 위 목록을 라운드로빈 자동 할당[/dim]")
        return 0

    if args.cmd == "setup":
        result = ensure_agent_clis(auto_install=not args.no_install)
        for line in result.messages:
            console.print(f"[dim]{line}[/dim]")
        if result.installed:
            console.print(f"[green]설치됨:[/green] {', '.join(result.installed)}")
        providers = result.providers
        if providers:
            tier = "유료" if result.tier == "paid" else "무료"
            console.print(f"[green]{tier} 에이전트 {len(providers)}개 준비[/green]")
            for p in providers:
                ver = f" · {p.version}" if p.version else ""
                console.print(f"  • {p.display_name or p.name}{ver}")
            return 0
        console.print("[yellow]사용 가능한 CLI 없음 — mock 백엔드만 가능[/yellow]")
        return 1

    if args.cmd == "install":
        return _cmd_install(
            with_agents=not args.no_agents,
            with_desktop=not args.no_desktop,
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
        result = run_debate(
            args.topic,
            chars,
            backend,  # type: ignore[arg-type]
            store,
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
