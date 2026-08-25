from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from debatesim.models import MatchResult, StanceBallot

DEFAULT_LOG_DIR = Path.home() / ".local/share/debatesim/logs"


def topic_title(topic: str, limit: int = 48) -> str:
    """One-line title for combo boxes / headers (paste-safe)."""
    text = (topic or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "토론"
    for line in text.split("\n"):
        line = line.strip().lstrip("#").strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    collapsed = re.sub(r"\s+", " ", text)
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def match_result_from_dict(data: dict) -> MatchResult:
    """Rebuild MatchResult from a saved debate JSON (replay / review)."""
    ballots = []
    for raw in data.get("ballots") or []:
        if not isinstance(raw, dict):
            continue
        ballots.append(
            StanceBallot(
                character_id=str(raw.get("character_id", "")),
                recommendation=str(raw.get("recommendation", "유보")),
                stance=str(raw.get("stance", "abstain")),
                confidence=float(raw.get("confidence") or 0.5),
                notes=str(raw.get("notes") or ""),
            )
        )
    transcript = []
    for row in data.get("transcript") or []:
        if isinstance(row, dict):
            transcript.append({str(k): str(v) for k, v in row.items()})
    return MatchResult(
        topic=str(data.get("topic") or ""),
        participants=[str(x) for x in (data.get("participants") or [])],
        interest_scores={
            str(k): float(v) for k, v in (data.get("interest_scores") or {}).items()
        },
        ballots=ballots,
        recommendation_dist={
            str(k): float(v) for k, v in (data.get("recommendation_dist") or {}).items()
        },
        consensus_p=float(data.get("consensus_p") or 0.0),
        mean_confidence=float(data.get("mean_confidence") or 0.0),
        winner_id=data.get("winner_id"),
        rankings_delta={
            str(k): float(v) for k, v in (data.get("rankings_delta") or {}).items()
        },
        transcript=transcript,
        providers={str(k): str(v) for k, v in (data.get("providers") or {}).items()},
        research_brief=str(data.get("research_brief") or ""),
        conclusion=str(data.get("conclusion") or ""),
    )


def _slug(topic: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", "-", topic.strip())
    text = re.sub(r"[^\w\-가-힣]+", "", text, flags=re.UNICODE)
    return (text or "debate")[:limit]


class DebateLogStore:
    """Persist full debate transcripts as markdown + json."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or DEFAULT_LOG_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, result: MatchResult, *, display_names: dict[str, str] | None = None) -> Path:
        display_names = display_names or {}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}_{_slug(result.topic)}"
        md_path = self.root / f"{base}.md"
        json_path = self.root / f"{base}.json"

        payload = result.to_dict()
        payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        lines: list[str] = []
        lines.append(f"# DebateSim 토론 로그")
        lines.append("")
        lines.append(f"- 시각: {payload['saved_at']}")
        lines.append(f"- 주제: {result.topic}")
        lines.append(f"- 출전: {', '.join(result.participants)}")
        if result.providers:
            prov = ", ".join(f"{k}→{v}" for k, v in result.providers.items())
            lines.append(f"- Provider: {prov}")
        lines.append(f"- 승자: {result.winner_id or '무승부'}")
        lines.append(f"- 합의 확률: {result.consensus_p:.0%}")
        lines.append(f"- 평균 확신도: {result.mean_confidence:.0%}")
        lines.append("")
        if getattr(result, "conclusion", ""):
            lines.append("## 합의 결론")
            lines.append("")
            lines.append(result.conclusion)
            lines.append("")
        if result.research_brief:
            lines.append("## 웹 참고 자료")
            lines.append("")
            lines.append(result.research_brief)
            lines.append("")
        lines.append("## 권고 분포")
        lines.append("")
        for label, p in sorted(result.recommendation_dist.items(), key=lambda x: -x[1]):
            lines.append(f"- {label}: {p:.0%}")
        lines.append("")
        lines.append("## 최종 투표")
        lines.append("")
        for b in result.ballots:
            name = display_names.get(b.character_id, b.character_id)
            lines.append(
                f"- **{name}** (`{b.character_id}`) · {b.recommendation} "
                f"({b.stance}, conf={b.confidence:.2f})"
            )
            if b.notes:
                lines.append(f"  - notes: {b.notes}")
        lines.append("")
        lines.append("## 대화 기록")
        lines.append("")
        if not result.transcript:
            lines.append("(발언 없음)")
        for i, row in enumerate(result.transcript, 1):
            cid = row.get("character_id", "?")
            name = display_names.get(cid, cid)
            role = row.get("role", "?")
            phase = row.get("round") or row.get("phase") or ""
            speech = (row.get("speech") or "").strip()
            meta = f"{name} · {role}"
            if phase:
                meta += f" · round {phase}"
            lines.append(f"### {i}. {meta}")
            lines.append("")
            lines.append(speech or "(빈 발언)")
            lines.append("")

        md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        # Keep a rolling index of recent debates
        index = self.root / "index.jsonl"
        with index.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "saved_at": payload["saved_at"],
                        "topic": result.topic,
                        "topic_short": topic_title(result.topic),
                        "winner": result.winner_id,
                        "participants": result.participants,
                        "markdown": md_path.name,
                        "json": json_path.name,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return md_path

    def list_recent(self, limit: int = 40) -> list[dict]:
        """Newest-first index rows that still have a JSON file on disk."""
        index = self.root / "index.jsonl"
        if not index.is_file():
            return []
        rows: list[dict] = []
        for line in index.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            name = row.get("json")
            if not name or not (self.root / str(name)).is_file():
                continue
            rows.append(row)
        rows.reverse()
        return rows[: max(0, limit)]

    def load_json(self, name: str) -> dict | None:
        path = self.root / name
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
