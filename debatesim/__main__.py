from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from debatesim.adapters.base import MockBackend
from debatesim.adapters.cli_agents import (
    ClaudePrintBackend,
    CodexExecBackend,
    CursorAgentBackend,
    EnsembleBackend,
    GeminiPrintBackend,
    discover_providers,
)
from debatesim.characters import load_characters
from debatesim.engines.debate import run_debate
from debatesim.logs import DebateLogStore
from debatesim.ranking import RankingStore

console = Console()
DEFAULT_DB = Path.home() / ".local/share/debatesim/rankings.db"
_CMDS = frozenset({"list", "ranks", "backends", "debate", "play"})


def _launch_gui(characters: Path | None = None) -> int:
    try:
        from debatesim.game.window import run_game
    except ImportError:
        console.print(
            "[red]GUI 의존성 없음[/red]: pip install -e '.[gui]'  (PySide6 필요)"
        )
        return 1
    return run_game(characters)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    # Bare launch → GUI
    if not raw:
        return _launch_gui()

    # GUI flags only (no subcommand): debatesim --characters /path
    if raw[0] not in _CMDS and raw[0] not in {"-h", "--help"}:
        if raw[0].startswith("-"):
            gparser = argparse.ArgumentParser(prog="debatesim")
            gparser.add_argument("--characters", type=Path, default=None)
            gargs = gparser.parse_args(raw)
            return _launch_gui(gargs.characters)
        console.print(f"[red]알 수 없는 명령:[/red] {raw[0]}")
        console.print("GUI: debatesim   CLI: debatesim list|ranks|debate|backends|play")
        return 2

    parser = argparse.ArgumentParser(
        prog="debatesim",
        description="DebateSim — 인자 없이 실행하면 GUI. CLI는 하위 명령 사용.",
        epilog="예: debatesim | ./play | debatesim debate '주제'",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_list = sub.add_parser("list", help="로드된 캐릭터와 페르소나 요약")
    p_list.add_argument("--characters", type=Path, default=None)

    p_rank = sub.add_parser("ranks", help="Elo 리더보드")
    p_rank.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_rank.add_argument(
        "--reset",
        action="store_true",
        help="Elo · 승패 · 매치 기록을 모두 초기화",
    )

    sub.add_parser("backends", help="연결된 정액제 CLI 목록")

    p_debate = sub.add_parser("debate", help="주제 토론 실행")
    p_debate.add_argument("topic", help="논의 주제")
    p_debate.add_argument("--rounds", type=int, default=2)
    p_debate.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_debate.add_argument(
        "--backend",
        choices=("all", "mock", "claude", "codex", "cursor", "gemini"),
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

    if args.cmd == "backends":
        providers = discover_providers()
        if not providers:
            console.print("[red]연결된 CLI 없음[/red] (claude / codex / agent / gemini)")
            return 1
        table = Table(title=f"Detected agent CLIs ({len(providers)})")
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

    if args.cmd == "list":
        chars = load_characters(args.characters)
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
        table = Table(title="DebateSim Elo")
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
            return 1
        store = RankingStore(args.db)
        if args.backend == "mock":
            backend: object = MockBackend()
        elif args.backend == "all":
            backend = EnsembleBackend()
        elif args.backend == "claude":
            backend = ClaudePrintBackend()
        elif args.backend == "codex":
            backend = CodexExecBackend()
        elif args.backend == "gemini":
            backend = GeminiPrintBackend()
        else:
            backend = CursorAgentBackend()
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
        console.print(f"  elo_delta: {result.rankings_delta}")
        names = {c.id: c.display_name for c in chars}
        log_path = DebateLogStore().save(result, display_names=names)
        console.print(f"  log: {log_path}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
