"""Waiting-room (lounge) opinion votes and gradual specialization overlays.

pet.json Persona stays frozen. Learning lives in LoungeProfile (SQLite), separate
from Elo which only applies to the debate cast of 3.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from arenatalk.config import env_get
from arenatalk.interest import interest_score
from arenatalk.models import Character, StanceBallot

_OFF_VALUES = {"0", "off", "false", "no", "n", "끄기", "꺼짐"}


def lounge_vote_env() -> bool | None:
    """``ARENATALK_LOUNGE`` as a decision, or None when it is not set.

    Kept apart from the default so the GUI can tell "the user never said" from
    "the user said yes": an env var set for this launch outranks the switch's
    remembered position, an unset one leaves it alone.
    """
    raw = env_get("ARENATALK_LOUNGE")
    if not raw:
        return None
    return raw.lower() not in _OFF_VALUES


def lounge_vote_default() -> bool:
    """Whether the waiting room polls unless a caller says otherwise.

    Off. The poll asks every character who is not on stage, so on a large
    roster it is the single biggest consumer of CLI tokens in a run — too
    expensive to charge anyone who never asked for it. ``ARENATALK_LOUNGE=1``
    turns it on for a whole machine; the GUI switch and ``--lounge`` turn it on
    for one debate.
    """
    env = lounge_vote_env()
    return False if env is None else env


LEARN_RATE = 0.08
MAX_SIGNATURE = 5
MAX_DOMAIN_KEYS = 24

_DOMAIN_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("보안", "해킹", "암호", "privacy", "보안성", "취약"), "보안"),
    (("비용", "예산", "가격", "세금", "경제", "투자", "주식"), "비용"),
    (("윤리", "정의", "권리", "공정", "차별", "도덕"), "윤리"),
    (("환경", "기후", "탄소", "에너지", "생태"), "환경"),
    (("ai", "인공지능", "모델", "llm", "자동화", "로봇"), "기술"),
    (("교육", "학교", "학습", "대학"), "교육"),
    (("건강", "의료", "병원", "백신", "질병"), "건강"),
    (("정치", "선거", "법률", "규제", "정부"), "정치"),
    (("속도", "긴급", "즉시", "빠르게", "의무화"), "속도"),
    (("감정", "공감", "돌봄", "관계", "협업"), "관계"),
)

_BIAS_FROM_STANCE = {
    "support": {"novelty": 0.08, "speed": 0.05},
    "oppose": {"risk": 0.08, "ethics": 0.05},
    "abstain": {"empathy": 0.04},
}


@dataclass
class LoungeProfile:
    """Accumulated audience memory — does not mutate Character.persona."""

    character_id: str
    domain_affinity: dict[str, float] = field(default_factory=dict)
    stance_prior: dict[str, float] = field(
        default_factory=lambda: {"support": 0.33, "oppose": 0.33, "abstain": 0.34}
    )
    bias_drift: dict[str, float] = field(default_factory=dict)
    judge_accuracy: float = 0.5
    votes: int = 0
    signature_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, character_id: str, data: dict[str, Any] | None) -> "LoungeProfile":
        data = data or {}
        prior = data.get("stance_prior") or {}
        return cls(
            character_id=character_id,
            domain_affinity={
                str(k): float(v) for k, v in (data.get("domain_affinity") or {}).items()
            },
            stance_prior={
                "support": float(prior.get("support", 0.33)),
                "oppose": float(prior.get("oppose", 0.33)),
                "abstain": float(prior.get("abstain", 0.34)),
            },
            bias_drift={
                str(k): float(v) for k, v in (data.get("bias_drift") or {}).items()
            },
            judge_accuracy=float(data.get("judge_accuracy", 0.5)),
            votes=int(data.get("votes") or 0),
            signature_notes=[str(x) for x in (data.get("signature_notes") or [])][
                :MAX_SIGNATURE
            ],
        )

    def affinity_label(self) -> str:
        """Short UI chip: domain lean + audience temperament."""
        if self.votes <= 0 and not self.domain_affinity:
            return ""
        top_domain = ""
        # Profiles written before tags were filtered still hold junk keys, so
        # filter on the way out too rather than making the user reset Elo.
        usable = {
            k: v
            for k, v in self.domain_affinity.items()
            if _is_useful_tag(_strip_particle(k))
        }
        if usable:
            top_domain = max(usable.items(), key=lambda x: x[1])[0]
        lean = max(self.stance_prior.items(), key=lambda x: x[1])[0]
        lean_kr = {"support": "찬성파", "oppose": "신중파", "abstain": "유보형"}.get(
            lean, "관전"
        )
        if top_domain:
            return f"{top_domain}·{lean_kr}"
        return lean_kr


# Junk that earlier versions wrote into stored profiles when they fell back to
# slicing the topic. Nothing produces these any more; the list exists so the
# chips of long-running installs stop reading 것이다·찬성파 without asking the
# user to reset their Elo history.
_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "것이다", "것인가", "것일까", "한다", "하다", "된다", "되다", "이다",
        "있다", "없다", "합니다", "입니다", "해야", "하는", "하지", "인가",
        "일까", "인지", "그리고", "그러나", "하지만", "그래서", "때문에",
        "위해", "대해", "관해", "통해", "계속", "정말", "매우", "가장",
        "이번", "다음", "지금", "여기", "저기", "우리", "너무", "많이",
        "어떻게", "무엇", "누가", "언제", "어디", "그것", "이것", "저것",
        "상승할", "하락할", "인상한다", "인하한다", "늘려야", "줄여야",
    }
)


# Particles that ride on the end of a Korean noun. Stripping them turns
# 채권금리는 into 채권금리, which is the thing the chip should actually name.
_PARTICLES: tuple[str, ...] = (
    "에서는", "으로는", "에게는", "에서", "으로", "까지", "부터", "보다",
    "에게", "한테", "라면", "다면", "이나", "이란", "이는", "은", "는",
    "이", "가", "을", "를", "의", "에", "도", "로", "와", "과", "만",
)


def _strip_particle(token: str) -> str:
    """Drop one trailing particle, but never down to a single syllable."""
    for particle in _PARTICLES:
        if token.endswith(particle) and len(token) - len(particle) >= 2:
            return token[: -len(particle)]
    return token


def _is_useful_tag(token: str) -> bool:
    if len(token) < 2 or len(token) > 8:
        return False
    if token in _STOP_TOKENS:
        return False
    # Verb/adjective endings that mean the token is a conjugation, not a noun.
    return not token.endswith(("한다", "했다", "된다", "이다", "하는", "할까", "인가"))


def extract_domain_tags(topic: str) -> list[str]:
    """Domain labels for a topic, in decreasing order of confidence."""
    from arenatalk.expertise import EXPERT_DOMAINS

    text = (topic or "").lower()
    tags: list[str] = []
    # The expert vocabulary is both wider and more specific than _DOMAIN_RULES,
    # which files a question about 주식 under "비용". Ask it first.
    for domain, vocab in EXPERT_DOMAINS.items():
        if any(word.lower() in text for word in vocab):
            tags.append(domain.split("·")[0])
    for keys, tag in _DOMAIN_RULES:
        if tag not in tags and any(k.lower() in text for k in keys):
            tags.append(tag)
    # No third guess. Slicing the topic into tokens used to fill the gap, but
    # Korean does not survive it: 것이다, 인상한, 적절한 are conjugations and
    # particles, not fields, and they ended up on the waiting room's chips and
    # in the stored profiles. A domain we cannot name is better left unnamed —
    # the chip falls back to temperament alone.
    return tags[:6]


def interest_with_profile(
    topic: str,
    character: Character,
    profile: LoungeProfile | None = None,
) -> float:
    base = interest_score(topic, character)
    if profile is None or (profile.votes <= 0 and not profile.domain_affinity):
        return base
    tags = extract_domain_tags(topic)
    if not tags:
        return base
    hits = [profile.domain_affinity.get(t, 0.0) for t in tags]
    boost = sum(hits) / max(1, len(hits))
    # Cap overlay so persona still dominates casting.
    return max(0.0, min(1.0, base + 0.18 * boost))


def pick_lounge_voters(
    topic: str,
    roster: list[Character],
    cast_ids: set[str],
    *,
    profiles: dict[str, LoungeProfile] | None = None,
) -> list[Character]:
    """Everyone not on stage, most interested first.

    The whole waiting room votes. Capping it at twelve produced a "여론" that
    silently excluded whoever happened to score low on the topic — and the cost
    that justified the cap is gone now that the poll runs during the debate
    instead of after it. Ordering still decides who is asked first.
    """
    lounge = [c for c in roster if c.id not in cast_ids]
    if not lounge:
        return []
    profiles = profiles or {}
    scored = [
        (interest_with_profile(topic, c, profiles.get(c.id)), c) for c in lounge
    ]
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return [c for _, c in scored]


def _ema(old: float, target: float, rate: float = LEARN_RATE) -> float:
    return (1.0 - rate) * old + rate * target


def _trim_domains(affinity: dict[str, float]) -> dict[str, float]:
    if len(affinity) <= MAX_DOMAIN_KEYS:
        return affinity
    keep = sorted(affinity.items(), key=lambda x: -x[1])[:MAX_DOMAIN_KEYS]
    return dict(keep)


def update_profile_from_vote(
    profile: LoungeProfile,
    *,
    topic: str,
    ballot: StanceBallot,
    cast_top_label: str | None,
) -> LoungeProfile:
    """Soft-update overlay from one audience ballot. Returns same object mutated."""
    alpha = LEARN_RATE
    for tag in extract_domain_tags(topic):
        old = profile.domain_affinity.get(tag, 0.0)
        profile.domain_affinity[tag] = _ema(old, 1.0, alpha)
    profile.domain_affinity = _trim_domains(profile.domain_affinity)

    stance = ballot.stance if ballot.stance in profile.stance_prior else "abstain"
    for key in ("support", "oppose", "abstain"):
        target = 1.0 if key == stance else 0.0
        profile.stance_prior[key] = _ema(profile.stance_prior.get(key, 0.33), target, alpha)

    for bias_key, delta in _BIAS_FROM_STANCE.get(stance, {}).items():
        old = profile.bias_drift.get(bias_key, 0.0)
        profile.bias_drift[bias_key] = max(-0.35, min(0.35, _ema(old, delta * 4, alpha)))

    matched = 0.0
    if cast_top_label:
        from arenatalk.research import normalize_recommendation

        matched = (
            1.0
            if normalize_recommendation(ballot.recommendation)
            == normalize_recommendation(cast_top_label)
            else 0.0
        )
    profile.judge_accuracy = _ema(profile.judge_accuracy, matched, alpha)
    profile.votes += 1

    note = (ballot.notes or "").strip()
    if note and note != "unparsed":
        # Keep distinctive short tokens / phrases
        chunks = [c.strip() for c in re.split(r"[,/·|]|(?:\s+)", note) if len(c.strip()) >= 2]
        for chunk in chunks[:3]:
            if chunk not in profile.signature_notes:
                profile.signature_notes.insert(0, chunk[:24])
        profile.signature_notes = profile.signature_notes[:MAX_SIGNATURE]
    return profile


def lounge_memory_line(profile: LoungeProfile | None) -> str:
    if profile is None or profile.votes <= 0:
        return "아직 누적된 여론 성향 없음(기본 페르소나만 사용)."
    label = profile.affinity_label() or "관전"
    top = sorted(profile.domain_affinity.items(), key=lambda x: -x[1])[:3]
    domains = ", ".join(f"{k}:{v:.2f}" for k, v in top) or "(없음)"
    notes = ", ".join(profile.signature_notes[:3]) or "(없음)"
    prior = profile.stance_prior
    return (
        f"여론 성향 라벨: {label}\n"
        f"도메인 친화도: {domains}\n"
        f"stance prior: {json.dumps(prior, ensure_ascii=False)}\n"
        f"판정 정확도(본선 합의 일치): {profile.judge_accuracy:.0%} · 투표 {profile.votes}회\n"
        f"자주 고른 키워드: {notes}"
    )


def build_lounge_system_prompt(
    character: Character,
    *,
    topic_brief: str = "",
    profile: LoungeProfile | None = None,
) -> str:
    p = character.persona
    brief_block = f"\n주제 해석:\n{topic_brief}\n" if topic_brief else "\n"
    memory = lounge_memory_line(profile)
    drift = json.dumps(profile.bias_drift if profile else {}, ensure_ascii=False)
    return f"""당신은 토론장 옆 **대기실 청중**이다. 발언권은 없고 **여론 투표**만 한다.
character_id={character.id}
이름: {character.display_name}
아키타입: {p.archetype}
성격 요약: {p.summary}
특성: {", ".join(p.traits)}
말투: {p.speaking_style}
관심사: {", ".join(p.interests)}
기본 편향(0~1): {json.dumps(p.biases, ensure_ascii=False)}
학습된 편향 드리프트(페르소나에 소량 가산): {drift}
누적 청중 기억:
{memory}
{brief_block}
규칙:
- 본선 출전자의 최종안·합의 후보를 듣고, 페르소나+누적 성향으로 표를 던져라.
- 길게 토론하지 마라. speech는 **1~2문장** 반응만.
- recommendation은 찬성|반대|유보 (본선과 같은 척도).
- notes에는 표의 이유를 짧게(키워드 위주).
- Elo/승패를 노리지 말고, 네 성향대로 솔직히 투표하라.

반드시 JSON만 출력하세요:
{{
  "speech": "대기실 반응 1~2문장",
  "recommendation": "찬성|반대|유보",
  "stance": "support|oppose|abstain",
  "confidence": 0.0~1.0,
  "notes": "짧은 이유 키워드"
}}
"""


def audience_weight(ballot: StanceBallot, profile: LoungeProfile | None) -> float:
    """Slightly up-weight accurate judges; never dominates cast Elo."""
    conf = max(0.05, min(1.0, ballot.confidence))
    if profile is None or profile.votes < 3:
        return conf
    # 0.85 .. 1.15 multiplier from judge_accuracy
    mult = 0.85 + 0.30 * max(0.0, min(1.0, profile.judge_accuracy))
    return conf * mult
