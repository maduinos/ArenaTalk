from __future__ import annotations

import json
from pathlib import Path

import pytest

from arenatalk.adapters.base import MockBackend, parse_ballot
from arenatalk.characters import character_roots, load_characters
from arenatalk.config import env_get
from arenatalk.interest import interest_score, pick_top
from arenatalk.models import Character, MatchResult, Persona, StanceBallot
from arenatalk.ranking import RankingStore, aggregate_ballots, decide_winner


def _persona(**kwargs) -> Persona:
    base = dict(
        archetype="tester",
        summary="test character",
        traits=("curious",),
        speaking_style="plain",
        interests=("보안", "비용", "윤리"),
        biases={"risk": 0.8, "ethics": 0.5, "speed": 0.4, "empathy": 0.4, "novelty": 0.5},
        debate_role_affinity={},
    )
    base.update(kwargs)
    return Persona.from_dict(base)


def _character(cid: str, **persona_kw) -> Character:
    return Character(
        id=cid,
        display_name=cid.title(),
        description="",
        persona=_persona(**persona_kw),
        root=f"/tmp/{cid}",
    )


def test_character_roots_prefer_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_chars = tmp_path / "env-chars"
    env_chars.mkdir()
    monkeypatch.setenv("ARENATALK_CHARACTERS", str(env_chars))
    assert character_roots() == [env_chars]
    assert character_roots(tmp_path / "explicit") == [tmp_path / "explicit"]


def test_load_characters_reads_pet_json(tmp_path: Path) -> None:
    root = tmp_path / "characters"
    pet = root / "alpha"
    pet.mkdir(parents=True)
    (pet / "pet.json").write_text(
        json.dumps(
            {
                "id": "alpha",
                "displayName": "Alpha",
                "persona": {
                    "archetype": "advocate",
                    "summary": "bold",
                    "traits": ["brave"],
                    "speaking_style": "sharp",
                    "interests": ["보안"],
                    "biases": {"risk": 0.7},
                    "debate_role_affinity": {},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = load_characters(root)
    assert len(loaded) == 1
    assert loaded[0].id == "alpha"
    assert loaded[0].display_name == "Alpha"


def test_env_get_first_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARENATALK_CHARACTERS", raising=False)
    monkeypatch.setenv("ARENATALK_CHARACTERS", "/new")
    assert env_get("ARENATALK_CHARACTERS") == "/new"


def test_parse_ballot_nested_and_noise() -> None:
    raw = 'prefix {"speech": "ok", "recommendation": "찬성", "confidence": 0.9, "notes": "a"} trailer'
    speech, ballot = parse_ballot("a", raw)
    assert speech == "ok"
    assert ballot.recommendation == "찬성"
    assert ballot.confidence == 0.9

    nested = '{"speech": "x", "recommendation": "반대", "confidence": 0.5, "notes": "{\\"k\\":1}"}'
    speech2, ballot2 = parse_ballot("b", nested)
    assert ballot2.recommendation == "반대"
    assert "x" in speech2


def test_parse_ballot_unparsed_fallback() -> None:
    speech, ballot = parse_ballot("c", "not json at all")
    assert ballot.recommendation == "유보"
    assert ballot.notes == "unparsed"
    assert "not json" in speech


def test_interest_and_pick_top() -> None:
    a = _character("a", interests=("보안", "리스크"))
    b = _character("b", interests=("요리", "여행"))
    c = _character("c", interests=("비용", "예산"))
    score_a = interest_score("보안 비용 분석", a)
    score_b = interest_score("보안 비용 분석", b)
    assert score_a > score_b
    picked = pick_top("보안 비용 분석", [a, b, c], k=2)
    assert len(picked) == 2
    assert picked[0][0].id in {"a", "c"}


def test_decide_winner_and_aggregate() -> None:
    ballots = [
        StanceBallot("a", "찬성", "support", 0.9),
        StanceBallot("b", "찬성", "support", 0.7),
        StanceBallot("c", "반대", "oppose", 0.6),
    ]
    dist, consensus, mean = aggregate_ballots(ballots)
    assert dist["찬성"] > dist["반대"]
    assert consensus > 0.5
    assert 0.7 < mean < 0.8
    assert decide_winner(ballots, dist) == "a"


def test_elo_multiplayer_uses_starting_snapshot(tmp_path: Path) -> None:
    store = RankingStore(tmp_path / "ranks.db")
    for cid in ("a", "b", "c"):
        store.ensure(cid)
    result = MatchResult(
        topic="t",
        participants=["a", "b", "c"],
        interest_scores={},
        ballots=[],
        recommendation_dist={"찬성": 1.0},
        consensus_p=1.0,
        mean_confidence=0.9,
        winner_id="a",
    )
    updated = store.apply_match(result)
    assert abs(updated.rankings_delta["a"] - 32.0) < 1e-6
    assert abs(updated.rankings_delta["b"] + 16.0) < 1e-6
    assert abs(updated.rankings_delta["c"] + 16.0) < 1e-6
    board = {r.character_id: r for r in store.leaderboard()}
    assert abs(board["a"].elo - 1032.0) < 1e-6
    assert board["a"].wins == 1
    assert board["b"].losses == 1


def test_lounge_profile_learns_without_touching_elo(tmp_path: Path) -> None:
    from arenatalk.lounge import extract_domain_tags

    store = RankingStore(tmp_path / "ranks.db")
    store.ensure("cast_a")
    store.ensure("cast_b")
    store.ensure("lounge_x")
    before = store.ensure("lounge_x").elo
    ballot = StanceBallot("lounge_x", "반대", "oppose", 0.8, notes="보안,비용")
    result = MatchResult(
        topic="AI 보안 규제 강화",
        participants=["cast_a", "cast_b"],
        interest_scores={},
        ballots=[
            StanceBallot("cast_a", "찬성", "support", 0.9),
            StanceBallot("cast_b", "찬성", "support", 0.7),
        ],
        recommendation_dist={"찬성": 1.0},
        consensus_p=1.0,
        mean_confidence=0.8,
        winner_id="cast_a",
        audience_ballots=[ballot],
        audience_dist={"반대": 1.0},
        audience_consensus_p=1.0,
    )
    store.apply_match(result)
    assert abs(store.ensure("lounge_x").elo - before) < 1e-9
    profile = store.get_lounge_profile("lounge_x")
    assert profile.votes == 1
    assert "보안" in extract_domain_tags(result.topic)
    assert profile.domain_affinity.get("보안", 0) > 0
    assert profile.stance_prior["oppose"] > profile.stance_prior["support"]
    assert profile.affinity_label()


def test_interest_with_profile_boosts_domain() -> None:
    from arenatalk.lounge import LoungeProfile, interest_with_profile

    c = _character("sec", interests=("요리",))
    base = interest_with_profile("보안 규제", c, None)
    profile = LoungeProfile(
        character_id="sec",
        domain_affinity={"보안": 0.9},
        votes=5,
    )
    boosted = interest_with_profile("보안 규제", c, profile)
    assert boosted > base


def test_run_debate_lounge_vote_mock(tmp_path: Path) -> None:
    from arenatalk.engines.debate import run_debate

    roster = [
        _character("a", interests=("보안", "비용")),
        _character("b", interests=("윤리", "공정")),
        _character("c", interests=("속도", "혁신")),
        _character("d", interests=("요리", "여행")),
        _character("e", interests=("건강", "돌봄")),
    ]
    store = RankingStore(tmp_path / "ranks.db")
    backend = MockBackend(think_seconds=0.0)
    result = run_debate(
        "보안 비용 규제",
        roster,
        backend,
        store,
        rounds=1,
        cast_n=3,
        research=False,
        parallel=True,
        speech_hold=False,
        lounge_vote=True,
    )
    assert len(result.participants) == 3
    assert result.audience_ballots
    assert result.audience_dist
    # Elo only on cast
    board = {r.character_id: r for r in store.leaderboard()}
    for cid in result.participants:
        assert board[cid].matches >= 1
    lounge_ids = {b.character_id for b in result.audience_ballots}
    for cid in lounge_ids:
        assert store.get_lounge_profile(cid).votes >= 1
        assert board.get(cid, store.ensure(cid)).matches == 0 or cid in result.participants


def test_mock_backend_returns_json() -> None:
    backend = MockBackend(think_seconds=0.0)
    raw = backend.complete("character_id=alpha critic", "주제: 반대해야 할까")
    data = json.loads(raw)
    assert "speech" in data
    assert data["recommendation"] in {"찬성", "반대", "유보"}


def _lounge_roster(n: int) -> list[Character]:
    return [_character(f"c{i}") for i in range(n)]


def test_lounge_prefetch_matches_the_serial_result(tmp_path: Path) -> None:
    """Prefetching changes when the audience votes, not whether it does."""
    from arenatalk.engines.debate import run_debate

    roster = _lounge_roster(8)
    out = {}
    for prefetch in (False, True):
        store = RankingStore(tmp_path / f"{prefetch}.db")
        result = run_debate(
            "주제",
            roster,
            MockBackend(think_seconds=0.0),
            store,
            rounds=1,
            parallel=False,
            research=False,
            lounge_prefetch=prefetch,
            lounge_vote=True,
        )
        out[prefetch] = result
    assert len(out[True].audience_ballots) == len(out[False].audience_ballots)
    assert out[True].audience_ballots, "no audience votes at all"
    assert set(out[True].audience_dist) == set(out[False].audience_dist)


def test_lounge_prefetch_still_reports_every_vote(tmp_path: Path) -> None:
    """The votes have to reach the UI even though nobody watched them happen."""
    from arenatalk.engines.debate import run_debate

    turns: list[tuple[str, str]] = []
    events: list[str] = []
    result = run_debate(
        "주제",
        _lounge_roster(8),
        MockBackend(think_seconds=0.0),
        RankingStore(tmp_path / "t.db"),
        rounds=1,
        parallel=False,
        research=False,
        lounge_prefetch=True,
        on_turn=lambda cid, role, speech, phase, rec, conf: turns.append((cid, phase)),
        on_event=events.append,
        lounge_vote=True,
    )
    audience_turns = [cid for cid, phase in turns if phase == "audience"]
    assert len(audience_turns) == len(result.audience_ballots)
    assert any("대기실 여론:" in e for e in events), events


def test_lounge_prefetch_completes_the_progress_bar(tmp_path: Path) -> None:
    from arenatalk.engines.debate import run_debate

    seen: list[tuple[int, int]] = []
    run_debate(
        "주제",
        _lounge_roster(8),
        MockBackend(think_seconds=0.0),
        RankingStore(tmp_path / "t.db"),
        rounds=1,
        parallel=False,
        research=False,
        lounge_prefetch=True,
        on_progress=lambda cur, total, label: seen.append((cur, total)),
        lounge_vote=True,
    )
    assert seen
    current, total = seen[-1]
    assert current == total, seen[-5:]


def test_lounge_vote_off_makes_no_audience_calls(tmp_path: Path) -> None:
    """The switch exists to save tokens, so it has to save the calls too."""
    from arenatalk.engines.debate import run_debate

    turns: list[str] = []
    events: list[str] = []
    result = run_debate(
        "주제",
        _lounge_roster(8),
        MockBackend(think_seconds=0.0),
        RankingStore(tmp_path / "off.db"),
        rounds=1,
        parallel=False,
        research=False,
        lounge_vote=False,
        on_turn=lambda cid, role, speech, phase, rec, conf: turns.append(phase),
        on_event=events.append,
    )
    assert "audience" not in turns
    assert not result.audience_ballots
    assert not result.audience_dist
    assert result.winner_id or result.recommendation_dist, "debate itself must stand"
    assert any("여론 조사 꺼짐" in e for e in events), events


def test_lounge_vote_default_follows_the_env(monkeypatch) -> None:
    from arenatalk.lounge import lounge_vote_default

    monkeypatch.delenv("ARENATALK_LOUNGE", raising=False)
    assert lounge_vote_default() is False, "the poll costs too much to be opt-out"
    monkeypatch.setenv("ARENATALK_LOUNGE", "0")
    assert lounge_vote_default() is False
    monkeypatch.setenv("ARENATALK_LOUNGE", "off")
    assert lounge_vote_default() is False
    monkeypatch.setenv("ARENATALK_LOUNGE", "1")
    assert lounge_vote_default() is True


def test_lounge_vote_unset_defers_to_the_env(tmp_path: Path, monkeypatch) -> None:
    """GUI and CLI pass None when the user has not said; the env decides then."""
    from arenatalk.engines.debate import run_debate

    def _run(db: str) -> object:
        return run_debate(
            "주제",
            _lounge_roster(8),
            MockBackend(think_seconds=0.0),
            RankingStore(tmp_path / db),
            rounds=1,
            parallel=False,
            research=False,
        )

    monkeypatch.delenv("ARENATALK_LOUNGE", raising=False)
    assert not _run("unset.db").audience_ballots, "silence means no poll"

    monkeypatch.setenv("ARENATALK_LOUNGE", "1")
    assert _run("on.db").audience_ballots, "the env is what turns it on"

    monkeypatch.setenv("ARENATALK_LOUNGE", "0")
    assert not _run("off.db").audience_ballots


def test_cancel_during_lounge_prefetch_is_a_cancel(tmp_path: Path) -> None:
    """A stop must not surface as "the audience failed" and finish the debate."""
    from arenatalk.engines.debate import DebateCancelled, run_debate

    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        # Let the openings and the first round through, then stop.
        return calls["n"] > 40

    with pytest.raises(DebateCancelled):
        run_debate(
            "주제",
            _lounge_roster(8),
            MockBackend(think_seconds=0.0),
            RankingStore(tmp_path / "t.db"),
            rounds=2,
            parallel=False,
            research=False,
            lounge_prefetch=True,
            should_cancel=should_cancel,
            lounge_vote=True,
        )


def test_a_broken_audience_does_not_lose_the_debate(tmp_path: Path) -> None:
    """The audience is a garnish; its failure must not discard a finished debate."""
    from arenatalk.engines import debate as debate_mod

    real = debate_mod._run_jobs

    def flaky(jobs, backend, **kw):
        if jobs and jobs[0][1] == "lounge_vote":
            raise RuntimeError("waiting-room CLI fell over")
        return real(jobs, backend, **kw)

    events: list[str] = []
    original = debate_mod._run_jobs
    debate_mod._run_jobs = flaky
    try:
        result = debate_mod.run_debate(
            "주제",
            _lounge_roster(8),
            MockBackend(think_seconds=0.0),
            RankingStore(tmp_path / "t.db"),
            rounds=1,
            parallel=False,
            research=False,
            lounge_prefetch=True,
            on_event=events.append,
            lounge_vote=True,
        )
    finally:
        debate_mod._run_jobs = original

    assert result.ballots, "main debate should still have produced a result"
    assert result.audience_ballots == []
    assert any("대기실 여론 수집 실패" in e for e in events), events


def test_the_whole_waiting_room_votes() -> None:
    """Polling only the top 12 quietly dropped part of the audience."""
    from arenatalk.lounge import pick_lounge_voters

    roster = _lounge_roster(20)
    cast = {roster[0].id, roster[1].id, roster[2].id}
    voters = pick_lounge_voters("주제", roster, cast)
    assert len(voters) == len(roster) - len(cast)
    assert cast.isdisjoint({c.id for c in voters})


def test_lounge_voters_are_ordered_by_interest() -> None:
    """Ordering decides who is polled first, so it still has to be sorted."""
    from arenatalk.lounge import interest_with_profile, pick_lounge_voters

    roster = _lounge_roster(10)
    voters = pick_lounge_voters("주제", roster, {roster[0].id})
    scores = [interest_with_profile("주제", c, None) for c in voters]
    assert scores == sorted(scores, reverse=True)


def test_every_lounge_member_gets_a_ballot(tmp_path: Path) -> None:
    from arenatalk.engines.debate import run_debate

    roster = _lounge_roster(9)
    result = run_debate(
        "주제",
        roster,
        MockBackend(think_seconds=0.0),
        RankingStore(tmp_path / "t.db"),
        rounds=1,
        parallel=False,
        research=False,
        lounge_prefetch=True,
        lounge_vote=True,
    )
    assert len(result.audience_ballots) == len(roster) - len(result.participants)


@pytest.mark.parametrize(
    "topic",
    [
        "넷마블 주가는 계속 상승할 것이다",
        "금리를 인상한다면 채권금리는 어떻게 될 것인가",
        "실내 온도는 24도가 적절한가",
        "점심 뭐 먹지",
    ],
)
def test_domain_tags_are_never_grammar(topic: str) -> None:
    """Slicing Korean topics into tokens put 것이다·인상한 on the lounge chips."""
    from arenatalk.lounge import extract_domain_tags

    junk = {"것이다", "것인", "인상한", "적절한", "상승할", "계속", "하는가", "먹지"}
    tags = extract_domain_tags(topic)
    assert not (set(tags) & junk), tags


def test_a_finance_topic_still_gets_a_finance_domain() -> None:
    """Dropping the token fallback must not mean dropping real domains."""
    from arenatalk.lounge import extract_domain_tags

    assert "주식" in extract_domain_tags("넷마블 주가는 계속 상승할 것이다")
    assert "주식" in extract_domain_tags("금리 인상과 채권 수익률")


def test_an_unclassifiable_topic_gets_no_domain() -> None:
    from arenatalk.lounge import extract_domain_tags

    assert extract_domain_tags("점심 뭐 먹지") == []


def test_legacy_junk_domains_stay_off_the_chip() -> None:
    """Profiles written before the fix still hold 것이다; do not show it."""
    from arenatalk.lounge import LoungeProfile

    profile = LoungeProfile(
        character_id="x",
        domain_affinity={"것이다": 0.9, "주식": 0.4},
        votes=3,
    )
    label = profile.affinity_label()
    assert "것이다" not in label
    assert label.startswith("주식")


def test_a_profile_with_only_junk_falls_back_to_temperament() -> None:
    from arenatalk.lounge import LoungeProfile

    profile = LoungeProfile(
        character_id="x", domain_affinity={"것이다": 0.9, "상승할": 0.8}, votes=3
    )
    label = profile.affinity_label()
    assert "것이다" not in label and "상승할" not in label
    assert label  # temperament alone is still a useful chip



def test_lounge_switch_is_remembered_across_restarts(tmp_path: Path, monkeypatch) -> None:
    """A token budget is not per-run: turning the poll off has to stick."""
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings

    from arenatalk.game import window as win

    monkeypatch.delenv("ARENATALK_LOUNGE", raising=False)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path)
    )
    settings = win.app_settings()
    settings.clear()
    settings.sync()

    assert win.stored_lounge_vote() is False, "first run stays quiet"

    settings.setValue(win.LOUNGE_VOTE_KEY, False)
    settings.sync()
    assert win.stored_lounge_vote() is False, "a saved 'off' survives the restart"

    # The ini backend hands booleans back as strings; "false" must not read true.
    assert win._stored_bool(win.app_settings(), win.LOUNGE_VOTE_KEY) is False

    monkeypatch.setenv("ARENATALK_LOUNGE", "1")
    assert win.stored_lounge_vote() is True, "this launch's env outranks the file"


def test_lounge_votes_outlive_the_debate_that_produced_them() -> None:
    """The audience result is worth reading after everyone sits back down."""
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from arenatalk.game.scene import ArenaScene

    app = QApplication.instance() or QApplication([])
    assert app is not None
    scene = ArenaScene([])

    class _Actor:
        def __init__(self, cid: str, selected: bool) -> None:
            self.character_id = cid
            self.selected = selected
            self.win_odds = 0.7
            self.stance_label = "찬성"

    scene._actors = [_Actor("on_stage", True), _Actor("in_lounge", False)]

    scene.clear_win_odds()
    assert scene._actors[0].stance_label == "", "the arena's live chip should go"
    assert scene._actors[1].stance_label == "찬성", "the audience vote should stay"
    assert scene._actors[1].win_odds == 0.0

    scene.clear_win_odds(keep_lounge_votes=False)
    assert scene._actors[1].stance_label == "", "a new debate wipes the board"
