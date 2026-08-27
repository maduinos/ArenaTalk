"""Casting by field of expertise rather than by interest in the sentence."""

from __future__ import annotations

import pytest

from arenatalk.expertise import (
    EXPERT_DOMAINS,
    EXPERT_THRESHOLD,
    domain_names,
    expert_score,
    rank_experts,
    suggest_experts,
)
from arenatalk.models import Character, Persona


def _char(cid: str, interests: tuple[str, ...], *, summary: str = "", archetype: str = "x") -> Character:
    return Character(
        id=cid,
        display_name=cid,
        description="",
        root="/tmp",
        persona=Persona.from_dict(
            {
                "archetype": archetype,
                "summary": summary,
                "traits": (),
                "speaking_style": "",
                "interests": list(interests),
                "biases": {},
                "debate_role_affinity": {},
            }
        ),
    )


ANALYST = _char("analyst", ("투자", "시장", "경제", "리스크"))
CARER = _char("carer", ("돌봄", "음식", "가족"))


def test_a_specialist_outscores_someone_unrelated() -> None:
    field = "주식·투자·경제"
    assert expert_score(ANALYST, field).score > expert_score(CARER, field).score
    assert expert_score(ANALYST, field).is_expert
    assert not expert_score(CARER, field).is_expert


def test_score_reports_which_words_earned_it() -> None:
    match = expert_score(ANALYST, "주식·투자·경제")
    assert set(match.matched) >= {"투자", "시장", "경제"}
    assert "투자" in match.tags


def test_one_stray_word_is_not_expertise() -> None:
    """A caretaker who mentions 비용 once must not be cast as a stock expert."""
    dabbler = _char("dabbler", ("돌봄", "비용", "가족"))
    assert expert_score(dabbler, "주식·투자·경제").score < EXPERT_THRESHOLD


def test_ranking_does_not_flatten_the_specialists() -> None:
    """A hard cap tied every real expert at 1.00 and destroyed the ordering."""
    deep = _char("deep", ("투자", "시장", "경제", "금융", "자산", "펀드"))
    shallow = _char("shallow", ("투자", "시장"))
    a = expert_score(deep, "주식·투자·경제").score
    b = expert_score(shallow, "주식·투자·경제").score
    assert a > b
    assert a < 1.0, "score must stay open-ended, not saturate to a tie"


def test_rank_experts_is_sorted_and_total() -> None:
    roster = [CARER, ANALYST]
    ranked = rank_experts(roster, "주식·투자·경제")
    assert [m.character.id for m in ranked] == ["analyst", "carer"]
    assert len(ranked) == len(roster)


def test_suggestion_never_returns_an_unusable_cast() -> None:
    """A debate needs two; a domain with one specialist still has to start."""
    roster = [ANALYST, CARER, _char("other", ("놀이",))]
    picked = suggest_experts(roster, "주식·투자·경제")
    assert len(picked) >= 2
    assert picked[0].character.id == "analyst"


def test_suggestion_prefers_real_experts_over_filler() -> None:
    roster = [CARER, ANALYST, _char("banker", ("금융", "자산", "환율"))]
    picked = suggest_experts(roster, "주식·투자·경제", limit=2)
    assert {m.character.id for m in picked} == {"analyst", "banker"}


def test_empty_domain_scores_zero() -> None:
    assert expert_score(ANALYST, "존재하지 않는 분야").score == 0.0


@pytest.mark.parametrize("domain", list(EXPERT_DOMAINS))
def test_every_domain_has_vocabulary(domain: str) -> None:
    assert len(EXPERT_DOMAINS[domain]) >= 5
    assert domain in domain_names()


def test_real_roster_finds_the_finance_people() -> None:
    """Regression for the reported bug: a stock topic cast a caretaker."""
    from arenatalk.characters import load_characters

    roster = load_characters(None)
    if len(roster) < 5:
        pytest.skip("character library not available")
    picked = {m.character.id for m in suggest_experts(roster, "주식·투자·경제")}
    # These three are the roster's finance-literate personas.
    assert picked & {"pioneer", "yawa", "tanya-von-degurechaff"}, picked
    assert "tangsuni" not in picked, "the caretaker is not a stock expert"
