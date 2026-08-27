"""Group characters by what they are actually expert in.

Casting normally goes by interest in *this topic*, which works when the topic
uses the same words the personas do. Ask about 주식 and it collapses: one
character mentions 투자, everyone else ties at the floor score, and the cast is
effectively random. A stock question then gets debated by a caretaker and a
knight.

So expertise is matched against a field, not a sentence. Each domain carries the
vocabulary its specialists actually use, and a character scores by how much of
that vocabulary their persona already contains — no LLM call, instant, and the
result is a ranked list a human can override.
"""

from __future__ import annotations

from dataclasses import dataclass

from arenatalk.models import Character

# Domain → the words a specialist in it tends to have in their persona.
# Order is the order shown in the picker.
EXPERT_DOMAINS: dict[str, tuple[str, ...]] = {
    "주식·투자·경제": (
        "투자", "주식", "주가", "시장", "증시", "경제", "금융", "자산", "펀드",
        "환율", "물가", "금리", "채권", "배당", "종목", "상장", "코스피",
        "나스닥", "매수", "매도", "수익률", "인플레이션", "실적", "밸류에이션",
        "비용", "예산", "가격", "수익", "리스크", "경영", "스타트업", "인센티브",
        "이해충돌", "권력",
    ),
    "정책·행정": (
        "정책", "행정", "복지", "선거", "여론", "정부", "규제", "개혁", "공공",
        "제도", "예산", "국정",
    ),
    "법·규정·감사": (
        "법", "법정", "규정", "거버넌스", "감사", "표준", "준법", "계약", "책임",
        "증거", "절차", "정의",
    ),
    "기술·AI": (
        "기술", "ai", "인공지능", "모델", "자동화", "로봇", "데이터", "소프트웨어",
        "엔지니어링", "개발", "시스템", "알고리즘", "반도체", "클라우드",
    ),
    "보안·정보": (
        "보안", "해킹", "암호", "정보", "작전", "은밀", "스파이", "감시", "취약",
        "방어", "장비",
    ),
    "의료·건강": (
        "의료", "건강", "병원", "백신", "질병", "돌봄", "위생", "재난", "응급",
        "수면", "영양", "운동", "정신건강",
    ),
    "교육·언어": (
        "교육", "학습", "언어", "학교", "대학", "문해", "접근성", "커리큘럼", "문화",
    ),
    "환경·에너지": (
        "환경", "기후", "탄소", "에너지", "생태", "자원", "지속가능",
    ),
    "마케팅·콘텐츠": (
        "마케팅", "바이럴", "밈", "트렌드", "콘텐츠", "커뮤니티", "미디어", "브랜드",
        "홍보", "선전", "예능",
    ),
    "조직·인사": (
        "조직", "직장", "인간관계", "팀워크", "번아웃", "심리", "자존감", "협동",
        "리더십", "인사",
    ),
    "안보·군사": (
        "안보", "전쟁", "전투", "전략", "군사", "작전", "생존", "위험", "미션",
        "방위",
    ),
    "창작·디자인": (
        "창작", "디자인", "스토리", "예술", "아이디어", "게임", "미학", "연출",
    ),
}

# How much a hit is worth, by where in the persona it was found. Interests are
# the field an author fills in deliberately, so they count for the most.
_INTEREST_HIT = 1.0
_INTEREST_PARTIAL = 0.6
_SUPPORT_HIT = 0.35

# Roughly two solid interest hits. Below this a character merely brushes the
# domain — one stray word — and should not be cast as its expert.
EXPERT_THRESHOLD = 0.45

# Saturation constant. A hard cap at "three hits = 1.0" tied every real
# specialist at the top and threw away the ordering the picker exists to show.
_SATURATION = 2.0
DEFAULT_EXPERT_CAST = 3


@dataclass(frozen=True)
class ExpertMatch:
    character: Character
    score: float
    matched: tuple[str, ...]

    @property
    def is_expert(self) -> bool:
        return self.score >= EXPERT_THRESHOLD

    @property
    def tags(self) -> str:
        """The persona words that earned the score, for the picker's second column."""
        return "·".join(self.matched[:3])


def domain_names() -> list[str]:
    return list(EXPERT_DOMAINS)


def expert_score(character: Character, domain: str) -> ExpertMatch:
    """Score one character against one domain's vocabulary."""
    vocab = EXPERT_DOMAINS.get(domain, ())
    persona = character.persona
    interests = [i.lower() for i in persona.interests]
    support = " ".join(
        [
            persona.archetype.replace("_", " "),
            persona.summary,
            *persona.traits,
            *persona.debate_tactics,
            *persona.signature_moves,
        ]
    ).lower()

    total = 0.0
    matched: list[str] = []
    for word in vocab:
        w = word.lower()
        if w in interests:
            total += _INTEREST_HIT
            matched.append(word)
            continue
        if any(w in i or i in w for i in interests):
            total += _INTEREST_PARTIAL
            matched.append(word)
            continue
        if w in support:
            total += _SUPPORT_HIT
            matched.append(word)

    # Saturating, not capped: dividing by len(vocab) would push every real
    # specialist near zero, and a hard ceiling ties them all at 1.00. This keeps
    # the ordering readable all the way up.
    score = total / (total + _SATURATION) if total else 0.0
    return ExpertMatch(character=character, score=score, matched=tuple(matched))


def rank_experts(roster: list[Character], domain: str) -> list[ExpertMatch]:
    """Every character scored for a domain, best first."""
    matches = [expert_score(c, domain) for c in roster]
    matches.sort(key=lambda m: (-m.score, m.character.id))
    return matches


def suggest_experts(
    roster: list[Character],
    domain: str,
    *,
    limit: int = DEFAULT_EXPERT_CAST,
    minimum: int = 2,
) -> list[ExpertMatch]:
    """The picker's default ticks: real specialists, or the closest thing to them.

    A debate needs at least two participants, so when a domain has only one
    convincing specialist the runners-up are included rather than refusing.
    """
    ranked = rank_experts(roster, domain)
    experts = [m for m in ranked if m.is_expert][:limit]
    if len(experts) >= minimum:
        return experts
    return ranked[: max(minimum, min(limit, len(ranked)))]
