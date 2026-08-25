from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicFrame:
    """How the cast should treat this topic (proposition vs concrete task)."""

    kind: str  # proposition | recommend | decide
    ask_count: int | None
    brief: str
    opening_extra: str
    round_extra: str
    final_extra: str


_RECOMMEND_RE = re.compile(
    r"(추천|골라|선정|픽|pick|list|리스트|골라줘|뽑아|제시)",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(
    r"(\d+)\s*개|(세|네|다섯|여섯|열)\s*개|top\s*(\d+)|(\d+)\s*종목",
    re.IGNORECASE,
)
_KR_NUM = {"세": 3, "네": 4, "다섯": 5, "여섯": 6, "열": 10}


def _extract_count(topic: str) -> int | None:
    m = _COUNT_RE.search(topic)
    if not m:
        if re.search(r"세\s*개|세\s*종목|3\s*종목", topic):
            return 3
        return None
    if m.group(1):
        return int(m.group(1))
    if m.group(2):
        return _KR_NUM.get(m.group(2))
    if m.group(3):
        return int(m.group(3))
    if m.group(4):
        return int(m.group(4))
    return None


def frame_topic(topic: str) -> TopicFrame:
    text = (topic or "").strip()
    count = _extract_count(text)
    is_rec = bool(_RECOMMEND_RE.search(text)) or (
        count is not None and any(k in text for k in ("종목", "주식", "후보", "안", "방안"))
    )

    if is_rec:
        n = count or 3
        brief = (
            f"과제형 토론이다. 사용자는 **구체적 항목 {n}개**를 원한다. "
            f"추상적 찬반만 떠들지 말고, 먼저 {n}개를 제시한 뒤 그 세트의 적절성을 논하라."
        )
        opening = (
            f"【필수】요청된 개수({n}개)만큼 **고유명사/구체 항목**을 번호 목록으로 제시하라.\n"
            f"예: 1) … 2) … 3) …\n"
            "왜 그 항목인지 한 줄씩 이유를 붙여라. 동문서답·분위기 발언 금지.\n"
            "recommendation: 네 목록이 최선이면 찬성, 확신이 약하면 유보."
        )
        round_ = (
            f"【필수】이전 발언에 나온 목록을 인용하라. "
            f"상대 {n}개 중 부적절한 항목을 지적하고, 교체 후보를 제시하라.\n"
            f"가능하면 네가 지지하는 **통합 후보 {n}개**를 다시 적어라.\n"
            "recommendation: 지금 논의 중인 통합안이 적절하면 찬성, 교체 필요하면 반대."
        )
        final = (
            f"【필수】최종으로 합의할 **{n}개 목록**을 한 번 더 명시하라 "
            f"(1) 2) 3) …).\n"
            "그 세트가 사용자 질문에 적절한지 한 문장으로 결론 내고 "
            "recommendation을 찬성(적절) / 반대(부적절·재선정) / 유보 중 고르라.\n"
            "notes에는 최종 N개를 쉼표로 요약하라."
        )
        return TopicFrame("recommend", n, brief, opening, round_, final)

    brief = (
        "명제형 토론이다. 주제의 핵심 주장에 대해 찬성·반대를 분명히 하고, "
        "같은 쟁점을 공유한 채 반박하라. 주제와 무관한 발언 금지."
    )
    opening = (
        "주제에 대한 네 입장을 한 문장으로 요약한 뒤, 핵심 근거 1~2개를 대라.\n"
        "recommendation은 찬성|반대|유보."
    )
    round_ = (
        "상대 발언의 허점을 주제 범위 안에서만 공격하고, 네 입장을 보강하라.\n"
        "recommendation은 찬성|반대|유보."
    )
    final = (
        "최종 입장을 못 박아라. recommendation은 찬성|반대|유보."
    )
    return TopicFrame("proposition", None, brief, opening, round_, final)
