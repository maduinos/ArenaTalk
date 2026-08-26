from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from arenatalk.adapters.base import AgentBackend, build_system_prompt, parse_ballot
from arenatalk.adapters.cli_agents import EnsembleBackend
from arenatalk.conclusion import build_conclusion
from arenatalk.interest import interest_score, pick_top
from arenatalk.models import Character, MatchResult, StanceBallot
from arenatalk.ranking import RankingStore, aggregate_ballots, decide_winner
from arenatalk.research import research_topic
from arenatalk.topic_frame import frame_topic

ROLE_CYCLE = ("advocate", "critic", "evidence")

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
    should_cancel: Callable[[], bool] | None = None,
    live_materials: Callable[[], str] | None = None,
    on_event: Callable[[str], None] | None = None,
    on_cast: OnCast | None = None,
    on_turn: OnTurn | None = None,
    on_thinking: OnThinking | None = None,
    on_progress: OnProgress | None = None,
) -> MatchResult:
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

    picked = (
        pick_top(topic, roster, k=cast_n, recent_ids=recent_ids)
        if cast is None
        else [(c, interest_score(topic, c)) for c in cast]
    )
    cast = [c for c, _ in picked]
    interest_scores = {c.id: score for c, score in picked}
    if len(cast) < 2:
        raise RuntimeError("need at least 2 characters with personas")

    assignment: dict[str, str] = {}
    if isinstance(backend, EnsembleBackend):
        assignment = backend.assign([c.id for c in cast])
        log("providers: " + ", ".join(f"{cid}→{prov}" for cid, prov in assignment.items()))

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
    total_turns = n_cast * (1 + rounds + 1)
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
    conclusion = build_conclusion(
        topic,
        transcript,
        ballots,
        winner_id=winner,
        recommendation_dist=dist,
        display_names={c.id: c.display_name for c in cast},
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
        providers=assignment,
        research_brief=research_brief,
        conclusion=conclusion,
    )
    if apply_ranking:
        return store.apply_match(result)
    return result


def _speech_hold_seconds(speech: str, scale: float = 1.0) -> float:
    n = len((speech or "").strip())
    base = min(14.0, max(4.5, 3.2 + n * 0.07))
    return max(1.2, base * max(0.35, float(scale)))


def _run_jobs(
    jobs: list[tuple[Character, str, str, str, str]],
    backend: AgentBackend | EnsembleBackend,
    *,
    parallel: bool,
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
        if isinstance(backend, EnsembleBackend):
            provider = backend.provider_for(character.id)
            log(f"→ {character.id} via {provider} ({role})")
            raw = backend.complete_for(character.id, system, user)
        else:
            log(f"→ {character.id} ({role})")
            raw = backend.complete(system, user)
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
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
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
