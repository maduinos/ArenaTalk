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
