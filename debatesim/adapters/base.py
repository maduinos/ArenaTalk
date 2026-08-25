from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from debatesim.models import Character, StanceBallot
from debatesim.research import normalize_recommendation, preferred_side


class AgentBackend(Protocol):
    def complete(self, system: str, user: str) -> str: ...


@dataclass
class MockBackend:
    """Deterministic offline backend for dry-runs (no CLI cost)."""

    think_seconds: float = 0.9

    def complete(self, system: str, user: str) -> str:
        import time

        time.sleep(max(0.0, self.think_seconds))
        m = re.search(r"character_id=([a-z0-9\-]+)", system)
        cid = m.group(1) if m else "unknown"
        text = (system + "\n" + user).lower()
        # Prefer explicit fight-for side from prompt
        if "이번 승리 목표 진영: 반대" in system + user or "승리 목표 진영: 반대" in user:
            rec, stance, conf = "반대", "oppose", 0.82
        elif "이번 승리 목표 진영: 찬성" in system + user or "승리 목표 진영: 찬성" in user:
            rec, stance, conf = "찬성", "support", 0.82
        elif any(k in text for k in ("반대", "위험", "비용", "비판", "리스크", "실패")):
            rec, stance, conf = "반대", "oppose", 0.74
        elif any(k in text for k in ("찬성", "추진", "도입", "하자", "효과", "이점")):
            rec, stance, conf = "찬성", "support", 0.74
        else:
            rec, stance, conf = "유보", "abstain", 0.55
        if "critic" in text:
            rec, stance, conf = "반대", "oppose", max(conf, 0.8)
        if "advocate" in text:
            rec, stance, conf = "찬성", "support", max(conf, 0.8)
        cite = "자료 팩의 유리한 근거를 앞세워 "
        if "경쟁 토론용 자료 팩" in user or "실시간 웹" in user:
            cite = "자료 팩을 인용해 "
        payload = {
            "speech": (
                f"[{cid}] {cite}{rec}으로 밀어야 한다. "
                f"상대 약점을 찌르고, 내 근거가 더 설득력 있음을 분명히 한다."
            ),
            "recommendation": rec,
            "stance": stance,
            "confidence": conf,
            "notes": "mock-win",
        }
        return json.dumps(payload, ensure_ascii=False)


def build_system_prompt(
    character: Character,
    role_hint: str,
    *,
    topic_brief: str = "",
) -> str:
    p = character.persona
    side = preferred_side(p.biases, role_hint)
    catch = " / ".join(p.catchphrases) if p.catchphrases else "(없음)"
    tactics = " · ".join(p.debate_tactics) if p.debate_tactics else "(페르소나 편향에 맞게)"
    moves = " · ".join(p.signature_moves) if p.signature_moves else "(없음)"
    brief_block = f"\n주제 해석:\n{topic_brief}\n" if topic_brief else "\n"
    return f"""당신은 토론 시뮬레이터의 캐릭터다. 목표는 **사용자 주제에 대한 구체적·설득력 있는 답**으로 이기는 것이다.
character_id={character.id}
이름: {character.display_name}
아키타입: {p.archetype}
성격 요약: {p.summary}
특성: {", ".join(p.traits)}
말투: {p.speaking_style}
시그니처 말버릇: {catch}
토론 전술: {tactics}
시그니처 무브: {moves}
관심사: {", ".join(p.interests)}
편향(0~1): {json.dumps(p.biases, ensure_ascii=False)}
이번 역할 힌트: {role_hint}
기본 성향 진영(참고): {side}
{brief_block}
규칙:
- **주제 밖 동문서답 금지.** 사용자가 물은 것(개수·대상·조건)을 먼저 충족하라.
- 과제형(추천 N개 등)이면 반드시 고유명사/구체 항목 목록을 포함하라. 분위기·추상론만으로 채우지 마라.
- 이전 발언에 나온 구체 항목을 무시하지 말고, 인용·반박·교체하라.
- 자료 팩이 있으면 유리한 근거를 인용하되, 자료가 없어도 페르소나로 구체안을 내라.
- 말투·전술은 유지하되, 이기려면 상대보다 **더 구체적이고 검증 가능한 답**을 내라.
- recommendation은 찬성|반대|유보.
  · 과제형: 지금 논의 중인 최종 목록/안이 적절하면 찬성, 교체·재선정이면 반대.
  · 명제형: 주제 주장에 대한 찬성/반대.
- speech는 3~6문장. 구체 항목·근거·상대 반박을 포함하라.

반드시 JSON만 출력하세요:
{{
  "speech": "말풍선용 3~6문장 발언",
  "recommendation": "찬성|반대|유보",
  "stance": "support|oppose|abstain",
  "confidence": 0.0~1.0,
  "notes": "한 줄 메모(제시한 핵심 항목 요약)"
}}
"""


def parse_ballot(character_id: str, raw: str) -> tuple[str, StanceBallot]:
    speech = raw.strip()
    data = None
    try:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            data = json.loads(m.group(0))
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return speech, StanceBallot(
            character_id=character_id,
            recommendation="유보",
            stance="abstain",
            confidence=0.4,
            notes="unparsed",
        )
    speech = str(data.get("speech") or speech)
    conf = float(data.get("confidence") or 0.5)
    conf = max(0.05, min(0.99, conf))
    rec = normalize_recommendation(str(data.get("recommendation") or "유보"))
    stance = str(data.get("stance") or "abstain")
    if rec == "찬성":
        stance = "support"
    elif rec == "반대":
        stance = "oppose"
    else:
        stance = "abstain"
    return speech, StanceBallot(
        character_id=character_id,
        recommendation=rec,
        stance=stance,
        confidence=conf,
        notes=str(data.get("notes") or ""),
    )
