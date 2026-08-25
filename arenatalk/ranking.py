from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from arenatalk.models import MatchResult, RankRow, StanceBallot

DEFAULT_ELO = 1000.0
K_FACTOR = 32.0


class RankingStore:
    """SQLite Elo store safe for GUI main thread + debate worker thread."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        # New connection per call so QThread workers don't hit
        # "SQLite objects created in a thread can only be used in that same thread".
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS ranks (
                      character_id TEXT PRIMARY KEY,
                      elo REAL NOT NULL,
                      wins INTEGER NOT NULL DEFAULT 0,
                      losses INTEGER NOT NULL DEFAULT 0,
                      draws INTEGER NOT NULL DEFAULT 0,
                      matches INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS matches (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      topic TEXT NOT NULL,
                      payload TEXT NOT NULL,
                      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                conn.commit()

    def ensure(self, character_id: str) -> RankRow:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM ranks WHERE character_id = ?", (character_id,)
                ).fetchone()
                if row:
                    return _row_from_sql(row)
                conn.execute(
                    "INSERT INTO ranks(character_id, elo) VALUES (?, ?)",
                    (character_id, DEFAULT_ELO),
                )
                conn.commit()
                return RankRow(character_id, DEFAULT_ELO, 0, 0, 0, 0)

    def leaderboard(self, limit: int = 50) -> list[RankRow]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM ranks ORDER BY elo DESC, wins DESC, character_id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [_row_from_sql(r) for r in rows]

    def reset(self, *, clear_match_log: bool = True) -> None:
        """Wipe Elo / W-L-D (and optional match history rows)."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM ranks")
                if clear_match_log:
                    conn.execute("DELETE FROM matches")
                conn.commit()

    def apply_match(self, result: MatchResult) -> MatchResult:
        """Update Elo from winner vs others. Draws if no clear winner."""
        participants = list(result.participants)
        winner = result.winner_id
        deltas: dict[str, float] = {cid: 0.0 for cid in participants}

        with self._lock:
            with self._connect() as conn:
                for cid in participants:
                    _ensure_conn(conn, cid)

                if winner is None or winner not in participants:
                    for cid in participants:
                        conn.execute(
                            """
                            UPDATE ranks
                            SET draws = draws + 1, matches = matches + 1
                            WHERE character_id = ?
                            """,
                            (cid,),
                        )
                else:
                    others = [c for c in participants if c != winner]
                    winner_row = _ensure_conn(conn, winner)
                    for opp in others:
                        opp_row = _ensure_conn(conn, opp)
                        exp_w = _expected(winner_row.elo, opp_row.elo)
                        exp_o = _expected(opp_row.elo, winner_row.elo)
                        d_w = K_FACTOR * (1.0 - exp_w)
                        d_o = K_FACTOR * (0.0 - exp_o)
                        deltas[winner] += d_w
                        deltas[opp] += d_o
                        winner_row = RankRow(
                            winner_row.character_id,
                            winner_row.elo + d_w,
                            winner_row.wins,
                            winner_row.losses,
                            winner_row.draws,
                            winner_row.matches,
                        )
                        conn.execute(
                            "UPDATE ranks SET elo = elo + ? WHERE character_id = ?",
                            (d_w, winner),
                        )
                        conn.execute(
                            "UPDATE ranks SET elo = elo + ? WHERE character_id = ?",
                            (d_o, opp),
                        )

                    conn.execute(
                        """
                        UPDATE ranks
                        SET wins = wins + 1, matches = matches + 1
                        WHERE character_id = ?
                        """,
                        (winner,),
                    )
                    for opp in others:
                        conn.execute(
                            """
                            UPDATE ranks
                            SET losses = losses + 1, matches = matches + 1
                            WHERE character_id = ?
                            """,
                            (opp,),
                        )

                result.rankings_delta = deltas
                conn.execute(
                    "INSERT INTO matches(topic, payload) VALUES (?, ?)",
                    (result.topic, json.dumps(result.to_dict(), ensure_ascii=False)),
                )
                conn.commit()
        return result


def _row_from_sql(row: sqlite3.Row) -> RankRow:
    return RankRow(
        character_id=row["character_id"],
        elo=float(row["elo"]),
        wins=int(row["wins"]),
        losses=int(row["losses"]),
        draws=int(row["draws"]),
        matches=int(row["matches"]),
    )


def _ensure_conn(conn: sqlite3.Connection, character_id: str) -> RankRow:
    row = conn.execute(
        "SELECT * FROM ranks WHERE character_id = ?", (character_id,)
    ).fetchone()
    if row:
        return _row_from_sql(row)
    conn.execute(
        "INSERT INTO ranks(character_id, elo) VALUES (?, ?)",
        (character_id, DEFAULT_ELO),
    )
    return RankRow(character_id, DEFAULT_ELO, 0, 0, 0, 0)


def _expected(a: float, b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((b - a) / 400.0))


def decide_winner(ballots: list[StanceBallot], recommendation_dist: dict[str, float]) -> str | None:
    """Winner = ballot whose recommendation matches top option, highest confidence.

    Draw only when the field is genuinely split (near-tie) or nobody backs the top label.
    """
    if not ballots or not recommendation_dist:
        return None
    ranked = sorted(recommendation_dist.items(), key=lambda x: (-x[1], x[0]))
    top_label, top_p = ranked[0]
    second_p = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = top_p - second_p
    # Softened vs old 0.08: clear plurality still yields a winner.
    if margin < 0.04 and top_p < 0.45:
        return None
    if margin < 0.02:
        return None
    aligned = [b for b in ballots if b.recommendation == top_label]
    if not aligned:
        return None
    aligned.sort(key=lambda b: (-b.confidence, b.character_id))
    return aligned[0].character_id


def aggregate_ballots(ballots: list[StanceBallot]) -> tuple[dict[str, float], float, float]:
    if not ballots:
        return {}, 0.0, 0.0
    from arenatalk.research import normalize_recommendation

    weights: dict[str, float] = {}
    for b in ballots:
        label = normalize_recommendation(b.recommendation)
        b.recommendation = label
        w = max(0.05, min(1.0, b.confidence))
        weights[label] = weights.get(label, 0.0) + w
    total = sum(weights.values()) or 1.0
    dist = {k: v / total for k, v in weights.items()}
    mean_conf = sum(b.confidence for b in ballots) / len(ballots)
    consensus = max(dist.values()) if dist else 0.0
    return dist, consensus, mean_conf


def live_win_probs(
    ballots_by_id: dict[str, StanceBallot],
    cast_ids: list[str],
) -> dict[str, float]:
    """Estimate P(character wins the match) from the latest per-cast ballots.

    Uses camp strength × confidence so odds move after every speech.
    Characters who have not spoken yet get a soft 유보 prior.
    """
    if not cast_ids:
        return {}
    ballots: list[StanceBallot] = []
    for cid in cast_ids:
        b = ballots_by_id.get(cid)
        if b is None:
            ballots.append(
                StanceBallot(
                    character_id=cid,
                    recommendation="유보",
                    stance="abstain",
                    confidence=0.35,
                    notes="pending",
                )
            )
        else:
            ballots.append(b)
    dist, _, _ = aggregate_ballots(ballots)
    if not dist:
        even = 1.0 / len(cast_ids)
        return {cid: even for cid in cast_ids}
    top_label = max(dist.items(), key=lambda x: x[1])[0]
    scores: dict[str, float] = {}
    for b in ballots:
        camp = dist.get(b.recommendation, 0.0)
        conf = max(0.05, min(1.0, b.confidence))
        if b.recommendation == top_label:
            scores[b.character_id] = conf * (0.55 + 0.45 * camp)
        else:
            scores[b.character_id] = conf * (0.16 + 0.40 * camp)
    total = sum(scores.values()) or 1.0
    return {cid: scores.get(cid, 0.0) / total for cid in cast_ids}
