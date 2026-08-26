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


def test_mock_backend_returns_json() -> None:
    backend = MockBackend(think_seconds=0.0)
    raw = backend.complete("character_id=alpha critic", "주제: 반대해야 할까")
    data = json.loads(raw)
    assert "speech" in data
    assert data["recommendation"] in {"찬성", "반대", "유보"}
