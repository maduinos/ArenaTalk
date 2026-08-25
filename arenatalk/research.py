from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "ArenaTalk/0.0.1 (local debate research; +https://localhost)"
TIMEOUT = 8.0

DEFAULT_LOG_DIR = Path.home() / ".local/share/arenatalk/logs"


@dataclass(frozen=True)
class ResearchHit:
    title: str
    snippet: str
    url: str = ""
    side: str = ""  # 찬성 | 반대 | "" (neutral/local)


def research_topic(topic: str, *, max_hits: int = 10) -> str:
    """Collect web + local ammo and format a win-oriented debate brief."""
    topic = (topic or "").strip()
    if not topic:
        return ""

    hits: list[ResearchHit] = []
    errors: list[str] = []

    for query, side in _angled_queries(topic):
        for collector, label in (
            (_wiki_hits, "wikipedia"),
            (_ddg_hits, "duckduckgo"),
        ):
            try:
                for h in collector(query, limit=2):
                    hits.append(
                        ResearchHit(title=h.title, snippet=h.snippet, url=h.url, side=side or h.side)
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}/{query[:20]}: {exc}")
        if len(hits) >= max_hits * 2:
            break

    try:
        hits.extend(_local_hits(topic, limit=4))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"local: {exc}")

    # score: prefer sided evidence, then longer snippets
    def rank_key(h: ResearchHit) -> tuple:
        return (0 if h.side else 1, -len(h.snippet), h.title.lower())

    seen: set[str] = set()
    unique: list[ResearchHit] = []
    for h in sorted(hits, key=rank_key):
        key = h.title.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(h)
        if len(unique) >= max_hits:
            break

    support = [h for h in unique if h.side == "찬성"]
    oppose = [h for h in unique if h.side == "반대"]
    other = [h for h in unique if h.side not in {"찬성", "반대"}]

    lines = [
        "## 경쟁 토론용 자료 팩 (이기기 위한 근거)",
        f"- 질의: {topic}",
        "- 목표: 각 캐릭은 자신의 입장에 유리한 항목을 골라 인용하고, 상대 근거를 반박해 최종 투표에서 이겨라.",
        "- 유보는 자료가 정면으로 모순될 때만. 가능하면 찬성/반대로 승부를 내라.",
    ]
    if not unique:
        lines.append("- (웹·로컬 자료를 충분히 못 찾았습니다. 페르소나 논리로 공격적으로 논증하세요.)")
        if errors:
            lines.append(f"- 참고: {'; '.join(errors[:2])}")
    else:

        def emit(header: str, group: list[ResearchHit]) -> None:
            if not group:
                return
            lines.append(header)
            for i, h in enumerate(group, 1):
                snip = " ".join(h.snippet.split())
                if len(snip) > 240:
                    snip = snip[:237] + "…"
                bit = f"{i}. {h.title}: {snip}" if snip else f"{i}. {h.title}"
                if h.url:
                    bit += f" ({h.url})"
                lines.append(bit)

        emit("### 찬성 측 승리용 근거", support)
        emit("### 반대 측 승리용 근거", oppose)
        emit("### 공통·로컬 아카이브", other)

    from arenatalk.attachments import load_path_attachments

    attached, _status = load_path_attachments(topic)
    if attached:
        lines.append("")
        lines.append(attached)
    return "\n".join(lines)


def _angled_queries(topic: str) -> list[tuple[str, str]]:
    """(query, side) pairs — side-tagged searches for win ammo."""
    cores = _search_queries(topic)
    core = cores[min(1, len(cores) - 1)] if cores else topic
    angled: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(q: str, side: str = "") -> None:
        q = " ".join(q.split()).strip()
        if not q or q.lower() in seen:
            return
        seen.add(q.lower())
        angled.append((q, side))

    for c in cores[:3]:
        add(c, "")
    add(f"{core} 장점 효과 이점", "찬성")
    add(f"{core} 단점 위험 비용 문제", "반대")
    add(f"{core} benefits advantages success", "찬성")
    add(f"{core} risks costs problems failure", "반대")
    add(f"{core} 찬성 근거", "찬성")
    add(f"{core} 반대 근거", "반대")
    return angled


def _search_queries(topic: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = " ".join(q.split()).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            ordered.append(q)

    add(topic)
    base = re.sub(r"[?？].*$", "", topic).strip()
    add(base)
    base2 = re.sub(r"(을|를|에 대해|할까|인가|일까|해보자)+$", "", base).strip()
    add(base2)
    toks = re.findall(r"[0-9A-Za-z가-힣]{2,}", base2 or base or topic)
    if len(toks) >= 2:
        add(" ".join(toks[:3]))
    if toks:
        add(toks[0])
    return ordered


def _local_roots() -> list[Path]:
    roots: list[Path] = [DEFAULT_LOG_DIR]
    env = os.environ.get("AREATALK_LOCAL_DIRS", "")
    for part in env.split(":"):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser())
    # CharacterPet personas nearby (read-only context)
    cp = Path("/home/whjeong/00_Github/maduinos/CharacterPet/characters")
    if cp.is_dir():
        roots.append(cp)
    return roots


def _local_hits(topic: str, *, limit: int = 4) -> list[ResearchHit]:
    tokens = [t.lower() for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", topic)]
    tokens = tokens[:8]
    if not tokens:
        return []

    hits: list[ResearchHit] = []
    for root in _local_roots():
        if not root.is_dir():
            continue
        patterns = ("*.md", "*.txt")
        files: list[Path] = []
        for pat in patterns:
            files.extend(sorted(root.rglob(pat), key=lambda p: -p.stat().st_mtime)[:60])
        for path in files:
            if len(hits) >= limit:
                return hits
            try:
                if path.stat().st_size > 400_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            low = text.lower()
            score = sum(1 for t in tokens if t in low)
            if score < max(1, min(2, len(tokens) // 2)):
                continue
            snippet = _best_local_snippet(text, tokens)
            if not snippet:
                continue
            side = ""
            if any(k in snippet for k in ("장점", "효과", "이점", "찬성", "성공")):
                side = "찬성"
            if any(k in snippet for k in ("단점", "위험", "비용", "반대", "실패")):
                side = "반대" if side != "찬성" else ""
            hits.append(
                ResearchHit(
                    title=f"local:{path.name}",
                    snippet=snippet,
                    url=str(path),
                    side=side,
                )
            )
    return hits


def _best_local_snippet(text: str, tokens: list[str], *, width: int = 220) -> str:
    # Prefer paragraph containing the most topic tokens.
    parts = re.split(r"\n{2,}", text)
    best = ""
    best_score = 0
    for part in parts:
        clean = " ".join(part.split())
        if len(clean) < 40:
            continue
        low = clean.lower()
        score = sum(1 for t in tokens if t in low)
        if score > best_score:
            best_score = score
            best = clean
    if not best:
        return ""
    if len(best) > width:
        # center on first token hit
        low = best.lower()
        idx = min((low.find(t) for t in tokens if t in low), default=0)
        start = max(0, idx - 40)
        best = best[start : start + width] + "…"
    return best


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _wiki_hits(topic: str, *, limit: int = 3) -> list[ResearchHit]:
    hits: list[ResearchHit] = []
    for lang in ("ko", "en"):
        api = f"https://{lang}.wikipedia.org/w/api.php"
        q = urllib.parse.urlencode(
            {
                "action": "opensearch",
                "search": topic,
                "limit": str(limit),
                "namespace": "0",
                "format": "json",
            }
        )
        data = _get_json(f"{api}?{q}")
        if not isinstance(data, list) or len(data) < 4:
            continue
        titles, descs, urls = data[1], data[2], data[3]
        for title, desc, url in zip(titles, descs, urls):
            snippet = str(desc or "")
            if not snippet and title:
                snippet = _wiki_summary(lang, str(title))
            hits.append(ResearchHit(title=str(title), snippet=snippet, url=str(url)))
            if len(hits) >= limit:
                return hits
    return hits


def _wiki_summary(lang: str, title: str) -> str:
    enc = urllib.parse.quote(title.replace(" ", "_"))
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{enc}"
    try:
        data = _get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return ""
    if isinstance(data, dict):
        return str(data.get("extract") or "")[:400]
    return ""


def _ddg_hits(topic: str, *, limit: int = 3) -> list[ResearchHit]:
    q = urllib.parse.urlencode({"q": topic, "format": "json", "no_redirect": "1", "no_html": "1"})
    data = _get_json(f"https://api.duckduckgo.com/?{q}")
    if not isinstance(data, dict):
        return []
    hits: list[ResearchHit] = []
    abstract = str(data.get("AbstractText") or "").strip()
    heading = str(data.get("Heading") or topic).strip()
    abs_url = str(data.get("AbstractURL") or "").strip()
    if abstract:
        hits.append(ResearchHit(title=heading or "요약", snippet=abstract, url=abs_url))

    for item in data.get("RelatedTopics") or []:
        if len(hits) >= limit:
            break
        if not isinstance(item, dict):
            continue
        if "Topics" in item:
            for sub in item.get("Topics") or []:
                if len(hits) >= limit:
                    break
                hit = _ddg_related(sub)
                if hit:
                    hits.append(hit)
            continue
        hit = _ddg_related(item)
        if hit:
            hits.append(hit)
    return hits[:limit]


def _ddg_related(item: object) -> ResearchHit | None:
    if not isinstance(item, dict):
        return None
    text = str(item.get("Text") or "").strip()
    url = str(item.get("FirstURL") or "").strip()
    if not text:
        return None
    title = text.split(" - ", 1)[0][:80]
    return ResearchHit(title=title, snippet=text, url=url)


_CANON_SUPPORT = re.compile(
    r"(찬성|지지|도입|추진|의무화|해야\s*한|찬\b|support|agree|yes)",
    re.I,
)
_CANON_OPPOSE = re.compile(
    r"(반대|기각|불가|철회|금지|하지\s*말|안\s*됨|oppose|against|no\b)",
    re.I,
)
_CANON_ABSTAIN = re.compile(
    r"(유보|보류|중립|조건부|판단\s*유보|더\s*필요|abstain|conditional|unclear)",
    re.I,
)


def normalize_recommendation(raw: str) -> str:
    """Collapse free-form labels into 찬성|반대|유보 to avoid vote fragmentation."""
    text = (raw or "").strip()
    if not text:
        return "유보"
    if text in {"찬성", "반대", "유보"}:
        return text
    oppose = bool(_CANON_OPPOSE.search(text))
    support = bool(_CANON_SUPPORT.search(text))
    abstain = bool(_CANON_ABSTAIN.search(text))
    if abstain and not (oppose ^ support):
        return "유보"
    if oppose and not support:
        return "반대"
    if support and not oppose:
        return "찬성"
    if oppose and support:
        return "유보"
    return "유보"


def preferred_side(character_biases: dict[str, float], role_hint: str) -> str:
    """Role + persona bias → which side this cast member should fight for."""
    role = (role_hint or "").lower()
    if role == "advocate":
        return "찬성"
    if role == "critic":
        return "반대"
    risk = float(character_biases.get("risk", 0.5))
    novelty = float(character_biases.get("novelty", 0.5))
    speed = float(character_biases.get("speed", 0.5))
    ethics = float(character_biases.get("ethics", 0.5))
    # High risk/ethics sensitivity → oppose bold mandates; novelty/speed → support.
    oppose_score = risk * 0.45 + ethics * 0.25
    support_score = novelty * 0.4 + speed * 0.35
    if support_score - oppose_score > 0.08:
        return "찬성"
    if oppose_score - support_score > 0.08:
        return "반대"
    return "찬성" if role == "evidence" else "반대"
