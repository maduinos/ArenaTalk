from __future__ import annotations

import random
import re
from collections.abc import Iterable

from debatesim.models import Character

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def interest_score(topic: str, character: Character) -> float:
    """Tag/bias overlap in [0, 1]. No LLM calls."""
    persona = character.persona
    topic_tokens = tokenize(topic)
    hay = " ".join(
        [
            *persona.interests,
            *persona.traits,
            persona.summary,
            persona.archetype.replace("_", " "),
        ]
    )
    interest_tokens = tokenize(hay)

    if not topic_tokens:
        return 0.0

    overlap = topic_tokens & interest_tokens
    # substring hits for Korean compounds (비용 in 비용과, etc.)
    sub_hits = 0
    lowered_topic = topic.lower()
    for interest in persona.interests:
        if interest.lower() in lowered_topic:
            sub_hits += 1
    tag_score = (len(overlap) + 0.8 * sub_hits) / max(3, min(8, len(topic_tokens)))
    tag_score = max(0.0, min(1.0, tag_score))

    bias = persona.biases
    boost = 0.0
    checks = (
        (("위험", "리스크", "실패", "보안", "재난", "비용", "risk", "cost"), "risk"),
        (("윤리", "정의", "권리", "공정", "ethics", "moral"), "ethics"),
        (("빨리", "속도", "즉시", "지금", "긴급", "의무화", "speed"), "speed"),
        (("감정", "공감", "돌봄", "사람", "관계", "협업", "empathy"), "empathy"),
        (("새로운", "혁신", "실험", "개척", "novel", "new"), "novelty"),
    )
    for keys, bias_key in checks:
        if any(k in lowered_topic for k in keys):
            boost += 0.14 * bias.get(bias_key, 0.5)

    prior = 0.04
    return max(0.0, min(1.0, 0.55 * tag_score + 0.45 * min(1.0, boost + prior)))


def _scores_all_equal(scores: list[float], *, eps: float = 1e-9) -> bool:
    if len(scores) <= 1:
        return True
    first = scores[0]
    return all(abs(s - first) <= eps for s in scores[1:])


def pick_top(
    topic: str,
    characters: Iterable[Character],
    k: int = 3,
    recent_ids: set[str] | None = None,
    priority_ids: Iterable[str] | None = None,
) -> list[tuple[Character, float]]:
    """Pick cast by interest, with optional forced priority seats.

    ``priority_ids`` (click order) always fill seats first, ignoring interest.
    Remaining seats use interest ranking. If every candidate's topic interest is
    identical, fill remaining seats by random sample from that pool.
    """
    recent_ids = recent_ids or set()
    roster = list(characters)
    by_id = {c.id: c for c in roster}

    forced: list[Character] = []
    seen: set[str] = set()
    for pid in priority_ids or ():
        if pid in seen or pid not in by_id:
            continue
        seen.add(pid)
        forced.append(by_id[pid])
        if len(forced) >= k:
            break

    raw_scores = {c.id: interest_score(topic, c) for c in roster}

    def score_of(c: Character) -> float:
        score = raw_scores[c.id]
        if c.id in recent_ids:
            score = max(0.0, score - 0.12)
        return score

    picked: list[tuple[Character, float]] = [(c, score_of(c)) for c in forced]
    if len(picked) >= k:
        return picked[:k]

    remaining = [c for c in roster if c.id not in seen]
    need = k - len(picked)
    if not remaining or need <= 0:
        return picked

    # Topic interest identical across the remaining pool → random cast.
    if _scores_all_equal([raw_scores[c.id] for c in remaining]):
        pool = list(remaining)
        random.shuffle(pool)
        picked.extend((c, score_of(c)) for c in pool[:need])
        return picked

    scored: list[tuple[Character, float]] = [(c, score_of(c)) for c in remaining]
    scored.sort(key=lambda x: (-x[1], x[0].id in recent_ids, x[0].id))
    picked.extend(scored[:need])
    return picked
