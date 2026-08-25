from __future__ import annotations

import re
from collections import Counter

from arenatalk.models import StanceBallot
from arenatalk.topic_frame import frame_topic

_LIST_ITEM_RE = re.compile(
    r"(?:^|\n)\s*(?:[-*•]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])\s*(.+?)(?=(?:\n\s*(?:[-*•]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])|\n\n|$))",
    re.MULTILINE | re.DOTALL,
)
_INLINE_NUM_RE = re.compile(
    r"(?:(?<=^)|(?<=[\s;；。]))(\d+)\s*[.)、:：]\s*([^0-9\n][^;\n]*?)(?=(?:\s+\d+\s*[.)、:：])|[.;；。]|$)"
)


def _clean_item(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = t.strip(" ·,，;；.")
    return t[:80]


def extract_list_items(speech: str, *, limit: int = 6) -> list[str]:
    """Pull numbered / bulleted concrete items from a speech."""
    text = (speech or "").strip()
    if not text:
        return []
    items: list[str] = []
    for m in _LIST_ITEM_RE.finditer(text):
        item = _clean_item(m.group(1))
        if len(item) >= 2:
            items.append(item)
    if len(items) < 2:
        for m in _INLINE_NUM_RE.finditer(text):
            item = _clean_item(m.group(2))
            if len(item) >= 2:
                items.append(item)
    # Also split "1) a 2) b 3) c" aggressively
    if len(items) < 2:
        chunks = re.split(r"(?=\d+\s*[.)、:：]\s*)", text)
        for ch in chunks:
            m = re.match(r"\d+\s*[.)、:：]\s*(.+)", ch.strip())
            if not m:
                continue
            item = _clean_item(m.group(1))
            item = re.split(r"[.。!?！？]", item, 1)[0].strip()
            if len(item) >= 2:
                items.append(item)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def build_conclusion(
    topic: str,
    transcript: list[dict[str, str]],
    ballots: list[StanceBallot],
    *,
    winner_id: str | None,
    recommendation_dist: dict[str, float],
    display_names: dict[str, str] | None = None,
) -> str:
    """Compose a human-readable final consensus summary for the result panel."""
    display_names = display_names or {}
    frame = frame_topic(topic)
    finals = [row for row in transcript if row.get("role") == "final_vote"]
    if not finals:
        finals = transcript[-max(1, len(ballots)) :] if transcript else []

    top_label = ""
    if recommendation_dist:
        top_label = max(recommendation_dist.items(), key=lambda x: x[1])[0]

    winner_speech = ""
    if winner_id:
        for row in reversed(finals):
            if row.get("character_id") == winner_id:
                winner_speech = (row.get("speech") or "").strip()
                break
    if not winner_speech and finals:
        winner_speech = (finals[-1].get("speech") or "").strip()

    # Prefer concrete list from winner, else majority of final speeches
    want_n = frame.ask_count or 3
    items = extract_list_items(winner_speech, limit=want_n + 2)
    if len(items) < max(2, want_n - 1):
        pool: list[str] = []
        for row in finals:
            pool.extend(extract_list_items(row.get("speech") or "", limit=want_n))
        if pool:
            # keep most common first tokens / full strings
            counts = Counter(pool)
            items = [x for x, _ in counts.most_common(want_n)]

    lines: list[str] = []
    if top_label:
        lines.append(f"합의 표결: {top_label}")
    if winner_id:
        wname = display_names.get(winner_id, winner_id)
        lines.append(f"대표 결론 발언: {wname}")

    if frame.kind == "recommend" and items:
        lines.append(f"합의 목록 ({len(items)}):")
        for i, it in enumerate(items[:want_n], 1):
            lines.append(f"  {i}) {it}")
    elif items:
        lines.append("핵심 합의 포인트:")
        for i, it in enumerate(items[:4], 1):
            lines.append(f"  {i}) {it}")

    # Short prose conclusion from winner speech (first 2 sentences)
    prose = _first_sentences(winner_speech, max_chars=220)
    if prose:
        lines.append("")
        lines.append(f"결론 요지: {prose}")

    # Ballot notes often hold slate summary
    note_bits = []
    for b in ballots:
        if b.notes and b.notes not in {"mock-win", "unparsed", "pending"}:
            name = display_names.get(b.character_id, b.character_id)
            note_bits.append(f"{name}: {b.notes.strip()[:60]}")
    if note_bits and frame.kind == "recommend" and len(items) < 2:
        lines.append("메모:")
        lines.extend(f"  · {n}" for n in note_bits[:3])

    text = "\n".join(lines).strip()
    return text or (prose or "합의 결론을 추출하지 못했습니다.")


def _first_sentences(text: str, *, max_chars: int = 220) -> str:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ""
    parts = re.split(r"(?<=[.!?。！？])\s+", raw)
    out = ""
    for p in parts:
        if not p:
            continue
        cand = (out + " " + p).strip() if out else p
        if len(cand) > max_chars and out:
            break
        out = cand
        if len(out) >= max_chars * 0.6:
            break
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    return out
