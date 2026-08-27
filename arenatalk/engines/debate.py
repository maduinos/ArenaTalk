from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable

from arenatalk.adapters.base import AgentBackend, build_system_prompt, parse_ballot
from arenatalk.adapters.cli_agents import (
    BackendCancelled,
    EnsembleBackend,
    reset_cancel_state,
)
from arenatalk.conclusion import build_conclusion
from arenatalk.lounge import (
    audience_weight,
    build_lounge_system_prompt,
    interest_with_profile,
    pick_lounge_voters,
)
from arenatalk.models import Character, MatchResult, StanceBallot
from arenatalk.ranking import (
    RankingStore,
    aggregate_audience_ballots,
    aggregate_ballots,
    decide_winner,
)
from arenatalk.research import research_topic
from arenatalk.topic_frame import frame_topic

ROLE_CYCLE = ("advocate", "critic", "evidence")

# Concurrent lounge CLI calls during the main debate. Enough to finish inside
# the remaining rounds; low enough not to race the cast for the rate limit.
LOUNGE_PREFETCH_WORKERS = 3

OnCast = Callable[[list[tuple[Character, float]], dict[str, str]], None]
OnTurn = Callable[[str, str, str, str, str, float], None]  # id, role, speech, phase, rec, conf
OnThinking = Callable[[str, str, str], None]  # character_id, role, phase
OnProgress = Callable[[int, int, str], None]  # current, total, label


class DebateCancelled(Exception):
    """Raised when the user aborts a live debate."""


def run_debate(
    topic: str,
    roster: list[Character],
    backend: AgentBackend | EnsembleBackend,
    store: RankingStore,
    *,
    rounds: int = 2,
    cast_n: int = 3,
    cast: list[Character] | None = None,
    parallel: bool = True,
    speech_hold: bool = False,
    speech_hold_scale: float = 1.0,
    recent_ids: set[str] | None = None,
    materials: str = "",
    research: bool = True,
    apply_ranking: bool = True,
    lounge_vote: bool = True,
    lounge_prefetch: bool = True,
    should_cancel: Callable[[], bool] | None = None,
    live_materials: Callable[[], str] | None = None,
    on_event: Callable[[str], None] | None = None,
    on_cast: OnCast | None = None,
    on_turn: OnTurn | None = None,
    on_thinking: OnThinking | None = None,
    on_progress: OnProgress | None = None,
) -> MatchResult:
    # A previous debate's cancellation must not poison this one's first turn.
    reset_cancel_state()

    def log(msg: str) -> None:
        if on_event:
            on_event(msg)

    def check_cancel() -> None:
        if should_cancel and should_cancel():
            raise DebateCancelled("토론이 중지되었습니다.")

    def turn(
        character_id: str,
        role: str,
        speech: str,
        phase: str,
        recommendation: str = "",
        confidence: float = 0.0,
    ) -> None:
        if on_turn:
            on_turn(character_id, role, speech, phase, recommendation, confidence)

    def thinking(character_id: str, role: str, phase: str) -> None:
        if on_thinking:
            on_thinking(character_id, role, phase)

    frame = frame_topic(topic)
    log(f"주제 유형: {frame.kind}" + (f" · 요청 {frame.ask_count}개" if frame.ask_count else ""))

    profiles = store.get_lounge_profiles([c.id for c in roster])

    def score_fn(c: Character) -> float:
        return interest_with_profile(topic, c, profiles.get(c.id))

    from arenatalk.interest import pick_top

    picked = (
        pick_top(topic, roster, k=cast_n, recent_ids=recent_ids, score_fn=score_fn)
        if cast is None
        else [(c, score_fn(c)) for c in cast]
    )
    cast = [c for c, _ in picked]
    interest_scores = {c.id: score for c, score in picked}
    if len(cast) < 2:
        raise RuntimeError("need at least 2 characters with personas")

    lounge_voters: list[Character] = []
    if lounge_vote:
        lounge_voters = pick_lounge_voters(
            topic,
            roster,
            {c.id for c in cast},
            profiles=profiles,
        )

    assignment: dict[str, str] = {}
    if isinstance(backend, EnsembleBackend):
        # So a provider being retired mid-debate shows up in the live log.
        backend.on_event = log
        # Cast first so their indices — and therefore their providers — do not
        # shift when the lounge is appended. A prefetch needs the mapping to
        # exist before the main debate starts.
        assignment = backend.assign(
            [c.id for c in cast] + [c.id for c in lounge_voters]
        )
        log(
            "providers: "
            + ", ".join(f"{cid}→{assignment[cid]}" for cid in (c.id for c in cast))
        )

    if on_cast:
        on_cast(picked, assignment)

    check_cancel()
    research_brief = ""
    if research:
        log("웹·로컬에서 근거를 수집하는 중…")
        research_brief = research_topic(topic)
        log("자료 팩 준비 완료" if "1." in research_brief else "자료 제한적 — 페르소나·구체안으로 진행")
        from arenatalk.attachments import extract_local_paths, load_path_attachments

        # Ensure topic paths are attached even if research returned early empty-ish.
        if "사용자 지정 로컬 파일" not in research_brief:
            attached, status = load_path_attachments(topic)
            if attached:
                research_brief = (
                    (research_brief + "\n\n" if research_brief else "") + attached
                )
            for line in status:
                log(f"로컬 첨부: {line}")
        else:
            for path in extract_local_paths(topic):
                log(f"로컬 첨부 후보: {path}")
    if materials.strip():
        from arenatalk.attachments import expand_text_with_attachments

        expanded, status = expand_text_with_attachments(materials)
        research_brief = (
            (research_brief + "\n\n" if research_brief else "")
            + "## 사용자 제공 자료\n"
            + expanded
        )
        for line in status:
            log(f"로컬 첨부: {line}")

    def context_block() -> str:
        parts: list[str] = []
        if research_brief:
            parts.append(research_brief.rstrip())
        if live_materials:
            live = (live_materials() or "").strip()
            if live:
                parts.append(live)
        if not parts:
            return ""
        return "\n\n".join(parts) + "\n\n"

    def sys_for(character: Character, role: str) -> str:
        return build_system_prompt(character, role, topic_brief=frame.brief)

    n_cast = len(cast)
    total_turns = n_cast * (1 + rounds + 1) + len(lounge_voters)
    turn_i = 0

    def bump(label: str) -> None:
        nonlocal turn_i
        turn_i += 1
        if on_progress:
            on_progress(turn_i, total_turns, label)

    transcript: list[dict[str, str]] = []

    check_cancel()
    opening_jobs = []
    for i, character in enumerate(cast):
        role = ROLE_CYCLE[i % len(ROLE_CYCLE)]
        user = (
            f"{context_block()}"
            f"사용자 주제:\n{topic}\n\n"
            f"단계: 초기 입장\n"
            "이전 발언은 없다.\n"
            f"{frame.opening_extra}\n"
            "관전자 추가 의견·증거가 있으면 반드시 인용·반영하라.\n"
        )
        opening_jobs.append((character, role, sys_for(character, role), user, "opening"))

    opening = _run_jobs(
        opening_jobs,
        backend,
        parallel=parallel,
        log=log,
        on_turn=turn,
        on_thinking=thinking,
        on_progress_bump=bump,
        speech_hold=speech_hold,
        speech_hold_scale=speech_hold_scale,
        should_cancel=should_cancel,
    )
    for character, role, speech, ballot in opening:
        transcript.append(
            {
                "character_id": character.id,
                "role": role,
                "speech": speech,
                "recommendation": ballot.recommendation,
                "confidence": f"{ballot.confidence:.3f}",
            }
        )

    # --- lounge votes, started now and collected at the end -------------
    #
    # The audience used to vote strictly after the closing statements, one
    # member at a time, each with a speech pause — up to twelve serial CLI
    # calls bolted onto a debate that had already finished. Nothing about
    # those calls needs the closing statements: an audience forms its view
    # while it watches. So they run against the opening statements, in the
    # background, and by the last round the answers are already in hand.
    lounge_future = None
    lounge_pool: ThreadPoolExecutor | None = None

    def start_lounge_prefetch() -> None:
        """Kick the audience off against the debate as it stands right now."""
        nonlocal lounge_future, lounge_pool
        if not (lounge_voters and lounge_prefetch) or lounge_future is not None:
            return
        jobs = _build_lounge_jobs(
            lounge_voters,
            topic=topic,
            frame=frame,
            profiles=profiles,
            context=context_block(),
            debate_so_far=_format_transcript(transcript),
            cast_summary="(본선 진행 중 — 최종 투표는 아직 나오지 않았다)",
            in_progress=True,
        )
        log(f"대기실 {len(lounge_voters)}명 여론 조사 시작 (본선과 동시 진행)")
        lounge_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lounge")
        lounge_future = lounge_pool.submit(
            _run_jobs,
            jobs,
            backend,
            parallel=True,
            # A handful at a time: enough to finish inside the main debate
            # without twelve CLI processes racing the cast for rate limit.
            max_workers=LOUNGE_PREFETCH_WORKERS,
            log=lambda _m: None,  # quiet — the main debate owns the log here
            speech_hold=False,
            should_cancel=should_cancel,
        )

    if rounds < 1:
        start_lounge_prefetch()

    for r in range(1, rounds + 1):
        check_cancel()
        snapshot = _format_transcript(transcript)
        jobs = []
        for i, character in enumerate(cast):
            role = ROLE_CYCLE[(i + r) % len(ROLE_CYCLE)]
            user = (
                f"{context_block()}"
                f"사용자 주제:\n{topic}\n\n"
                f"단계: 라운드 {r} — 제시안 검토·수정\n"
                f"이전 발언:\n{snapshot}\n"
                f"{frame.round_extra}\n"
                "관전자 추가 의견·증거가 있으면 반드시 인용·반영하라.\n"
            )
            jobs.append(
                (character, role, sys_for(character, role), user, f"round_{r}")
            )
        results = _run_jobs(
            jobs,
            backend,
            parallel=parallel,
            log=log,
            on_turn=turn,
            on_thinking=thinking,
            on_progress_bump=bump,
            speech_hold=speech_hold,
            speech_hold_scale=speech_hold_scale,
            should_cancel=should_cancel,
        )
        for character, role, speech, ballot in results:
            transcript.append(
                {
                    "character_id": character.id,
                    "role": role,
                    "speech": speech,
                    "round": str(r),
                    "recommendation": ballot.recommendation,
                    "confidence": f"{ballot.confidence:.3f}",
                }
            )
        if r == 1:
            # After the first rebuttal, not after the openings: the audience
            # gets the clash it is actually reacting to, and everything still
            # to come (later rounds plus the closing) covers the poll's cost.
            start_lounge_prefetch()

    check_cancel()
    final_snap = _format_transcript(transcript)
    final_jobs = []
    for character in cast:
        user = (
            f"{context_block()}"
            f"사용자 주제:\n{topic}\n\n"
            f"전체 토론:\n{final_snap}\n"
            f"{frame.final_extra}\n"
            "관전자 추가 의견·증거가 있으면 최종안에 반영하라.\n"
        )
        final_jobs.append(
            (character, "final_vote", sys_for(character, "final_vote"), user, "final")
        )
    finals = _run_jobs(
        final_jobs,
        backend,
        parallel=parallel,
        log=log,
        on_turn=turn,
        on_thinking=thinking,
        on_progress_bump=bump,
        speech_hold=speech_hold,
        speech_hold_scale=speech_hold_scale,
        should_cancel=should_cancel,
    )

    ballots: list[StanceBallot] = []
    for character, role, speech, ballot in finals:
        transcript.append(
            {
                "character_id": character.id,
                "role": role,
                "speech": speech,
                "recommendation": ballot.recommendation,
                "confidence": f"{ballot.confidence:.3f}",
            }
        )
        ballots.append(ballot)

    dist, consensus_p, mean_conf = aggregate_ballots(ballots)
    winner = decide_winner(ballots, dist)

    audience_ballots: list[StanceBallot] = []
    audience_dist: dict[str, float] = {}
    audience_consensus_p = 0.0
    if lounge_voters:
        check_cancel()
        lounge_results: list[tuple[Character, str, str, StanceBallot]] = []
        if lounge_future is not None:
            if not lounge_future.done():
                log("대기실 표 집계를 기다리는 중…")
            try:
                lounge_results = _await_lounge(lounge_future, should_cancel)
            except _LoungeFailed as exc:
                # The audience is a garnish; a finished debate is not thrown
                # away because one waiting-room CLI fell over.
                log(f"대기실 여론 수집 실패 — 본선 결과만 사용합니다: {exc}")
                lounge_results = []
            finally:
                if lounge_pool is not None:
                    lounge_pool.shutdown(wait=False)
        else:
            log(f"대기실 여론 투표 · {len(lounge_voters)}명")
            lounge_results = _run_jobs(
                _build_lounge_jobs(
                    lounge_voters,
                    topic=topic,
                    frame=frame,
                    profiles=profiles,
                    context=context_block(),
                    debate_so_far=final_snap,
                    cast_summary=_format_cast_ballots(ballots, cast),
                    in_progress=False,
                ),
                backend,
                parallel=bool(parallel),
                log=log,
                on_turn=turn,
                on_thinking=thinking,
                on_progress_bump=bump,
                speech_hold=speech_hold,
                speech_hold_scale=max(0.45, speech_hold_scale * 0.55),
                should_cancel=should_cancel,
            )

        for character, role, speech, ballot in lounge_results:
            transcript.append(
                {
                    "character_id": character.id,
                    "role": role,
                    "speech": speech,
                    "recommendation": ballot.recommendation,
                    "confidence": f"{ballot.confidence:.3f}",
                }
            )
            audience_ballots.append(ballot)
            if lounge_future is not None:
                # Already-fetched votes reveal at once: the point of prefetching
                # is not to replay twelve speeches the user is waiting through.
                turn(
                    character.id,
                    role,
                    speech,
                    "audience",
                    ballot.recommendation,
                    float(ballot.confidence),
                )
                bump("audience")
        if audience_ballots:
            log("대기실 여론: " + _tally(audience_ballots))
        # Keep the progress bar honest when some voters dropped out.
        for _ in range(len(lounge_voters) - len(lounge_results)):
            bump("audience")

        def _w(b: StanceBallot) -> float:
            return audience_weight(b, profiles.get(b.character_id))

        audience_dist, audience_consensus_p, _ = aggregate_audience_ballots(
            audience_ballots, weight_fn=_w
        )

    conclusion = build_conclusion(
        topic,
        transcript,
        ballots,
        winner_id=winner,
        recommendation_dist=dist,
        display_names={c.id: c.display_name for c in cast},
        audience_dist=audience_dist,
        audience_consensus_p=audience_consensus_p,
    )
    result = MatchResult(
        topic=topic,
        participants=[c.id for c in cast],
        interest_scores=interest_scores,
        ballots=ballots,
        recommendation_dist=dist,
        consensus_p=consensus_p,
        mean_confidence=mean_conf,
        winner_id=winner,
        transcript=transcript,
        providers={cid: assignment[cid] for cid in (c.id for c in cast) if cid in assignment},
        research_brief=research_brief,
        conclusion=conclusion,
        audience_ballots=audience_ballots,
        audience_dist=audience_dist,
        audience_consensus_p=audience_consensus_p,
    )
    if apply_ranking:
        return store.apply_match(result)
    return result


def _format_cast_ballots(ballots: list[StanceBallot], cast: list[Character]) -> str:
    names = {c.id: c.display_name for c in cast}
    if not ballots:
        return "(없음)"
    lines = []
    for b in ballots:
        name = names.get(b.character_id, b.character_id)
        lines.append(
            f"- {name}: {b.recommendation} ({b.confidence:.0%}) — {b.notes or ''}"
        )
    return "\n".join(lines)


def _build_lounge_jobs(
    voters: list[Character],
    *,
    topic: str,
    frame,
    profiles: dict,
    context: str,
    debate_so_far: str,
    cast_summary: str,
    in_progress: bool,
) -> list[tuple[Character, str, str, str, str]]:
    """Lounge ballot prompts, for either the prefetch or the old serial path."""
    stage = (
        "단계: 대기실 여론 투표 (본선 진행 중)\n"
        "지금까지 나온 발언만 보고, 네 성향대로 표를 던져라.\n"
        if in_progress
        else "단계: 대기실 여론 투표\n"
        "위 본선 결과를 참고하되, 네 성향대로 표를 던져라.\n"
    )
    heading = "본선 진행 상황" if in_progress else "본선 토론 요약"
    jobs: list[tuple[Character, str, str, str, str]] = []
    for character in voters:
        system = build_lounge_system_prompt(
            character, topic_brief=frame.brief, profile=profiles.get(character.id)
        )
        user = (
            f"{context}"
            f"사용자 주제:\n{topic}\n\n"
            f"{heading}:\n{debate_so_far}\n\n"
            f"본선 투표:\n{cast_summary}\n\n"
            f"{stage}"
        )
        jobs.append((character, "lounge_vote", system, user, "audience"))
    return jobs


def _await_lounge(future, should_cancel: Callable[[], bool] | None):
    """Wait on the prefetch without swallowing a stop request."""
    while True:
        if should_cancel and should_cancel():
            future.cancel()
            raise DebateCancelled("토론이 중지되었습니다.")
        try:
            return future.result(timeout=0.25)
        except FuturesTimeout:
            continue
        except (DebateCancelled, BackendCancelled):
            # A stop reached the prefetch first; it is still a stop.
            raise DebateCancelled("토론이 중지되었습니다.") from None
        except Exception as exc:  # noqa: BLE001
            # One dead audience member must not throw away a finished debate.
            raise _LoungeFailed(str(exc)) from exc


class _LoungeFailed(Exception):
    """The audience poll failed; the main debate result still stands."""


def _tally(ballots: list[StanceBallot]) -> str:
    counts: dict[str, int] = {}
    for b in ballots:
        counts[b.recommendation or "중립"] = counts.get(b.recommendation or "중립", 0) + 1
    order = ["찬성", "반대", "중립"]
    bits = [f"{k} {counts[k]}" for k in order if counts.get(k)]
    bits += [f"{k} {v}" for k, v in counts.items() if k not in order]
    return " · ".join(bits) or "(표 없음)"


def _speech_hold_seconds(speech: str, scale: float = 1.0) -> float:
    n = len((speech or "").strip())
    base = min(14.0, max(4.5, 3.2 + n * 0.07))
    return max(1.2, base * max(0.35, float(scale)))


def _run_jobs(
    jobs: list[tuple[Character, str, str, str, str]],
    backend: AgentBackend | EnsembleBackend,
    *,
    parallel: bool,
    max_workers: int | None = None,
    log: Callable[[str], None],
    on_turn: OnTurn | None = None,
    on_thinking: OnThinking | None = None,
    on_progress_bump: Callable[[str], None] | None = None,
    speech_hold: bool = False,
    speech_hold_scale: float = 1.0,
    should_cancel: Callable[[], bool] | None = None,
) -> list[tuple[Character, str, str, StanceBallot]]:
    def one(
        job: tuple[Character, str, str, str, str],
    ) -> tuple[Character, str, str, StanceBallot]:
        if should_cancel and should_cancel():
            raise DebateCancelled("토론이 중지되었습니다.")
        character, role, system, user, phase = job
        if on_thinking:
            on_thinking(character.id, role, phase)
        try:
            if isinstance(backend, EnsembleBackend):
                provider = backend.provider_for(character.id)
                log(f"→ {character.id} via {provider} ({role})")
                raw = backend.complete_for(character.id, system, user)
            else:
                log(f"→ {character.id} ({role})")
                raw = backend.complete(system, user)
        except BackendCancelled as exc:
            # Stop killed the CLI mid-answer. That is a cancel, not a failure.
            raise DebateCancelled(str(exc)) from exc
        if should_cancel and should_cancel():
            raise DebateCancelled("토론이 중지되었습니다.")
        speech, ballot = parse_ballot(character.id, raw)
        log(f"✓ {character.id}: {speech[:120].replace(chr(10), ' ')}")
        if on_turn:
            on_turn(
                character.id,
                role,
                speech,
                phase,
                ballot.recommendation,
                float(ballot.confidence),
            )
            if speech_hold and not parallel:
                # Interruptible hold
                remain = _speech_hold_seconds(speech, speech_hold_scale)
                while remain > 0:
                    if should_cancel and should_cancel():
                        raise DebateCancelled("토론이 중지되었습니다.")
                    step = min(0.25, remain)
                    time.sleep(step)
                    remain -= step
        if on_progress_bump:
            on_progress_bump(phase)
        return character, role, speech, ballot

    if not parallel or len(jobs) == 1:
        return [one(job) for job in jobs]

    ordered: list[tuple[Character, str, str, StanceBallot] | None] = [None] * len(jobs)
    workers = max(1, min(max_workers or len(jobs), len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one, job): idx for idx, job in enumerate(jobs)}
        try:
            for fut in as_completed(futures):
                idx = futures[fut]
                ordered[idx] = fut.result()
        except DebateCancelled:
            from arenatalk.adapters.cli_agents import cancel_active_backends

            cancel_active_backends()
            for fut in futures:
                fut.cancel()
            raise
    return [item for item in ordered if item is not None]


def _format_transcript(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "(아직 없음)"
    lines = []
    for row in transcript[-24:]:
        lines.append(f"- {row['character_id']} ({row.get('role', '?')}): {row['speech']}")
    return "\n".join(lines)
