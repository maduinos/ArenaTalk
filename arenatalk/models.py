from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Persona:
    archetype: str
    summary: str
    traits: tuple[str, ...]
    speaking_style: str
    interests: tuple[str, ...]
    biases: dict[str, float]
    debate_role_affinity: dict[str, float]
    catchphrases: tuple[str, ...] = ()
    debate_tactics: tuple[str, ...] = ()
    signature_moves: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Persona":
        return cls(
            archetype=str(data.get("archetype", "unknown")),
            summary=str(data.get("summary", "")),
            traits=tuple(data.get("traits") or ()),
            speaking_style=str(data.get("speaking_style", "")),
            interests=tuple(data.get("interests") or ()),
            biases={k: float(v) for k, v in (data.get("biases") or {}).items()},
            debate_role_affinity={
                k: float(v) for k, v in (data.get("debate_role_affinity") or {}).items()
            },
            catchphrases=tuple(str(x) for x in (data.get("catchphrases") or ())),
            debate_tactics=tuple(str(x) for x in (data.get("debate_tactics") or ())),
            signature_moves=tuple(str(x) for x in (data.get("signature_moves") or ())),
        )


@dataclass(frozen=True)
class Character:
    id: str
    display_name: str
    description: str
    persona: Persona
    root: str


@dataclass
class StanceBallot:
    character_id: str
    recommendation: str
    stance: str  # support | oppose | abstain | alt:<label>
    confidence: float
    notes: str = ""


@dataclass
class MatchResult:
    topic: str
    participants: list[str]
    interest_scores: dict[str, float]
    ballots: list[StanceBallot]
    recommendation_dist: dict[str, float]
    consensus_p: float
    mean_confidence: float
    winner_id: str | None
    rankings_delta: dict[str, float] = field(default_factory=dict)
    transcript: list[dict[str, str]] = field(default_factory=list)
    providers: dict[str, str] = field(default_factory=dict)
    research_brief: str = ""
    conclusion: str = ""
    # Waiting-room opinion (separate from cast Elo / winner)
    audience_ballots: list[StanceBallot] = field(default_factory=list)
    audience_dist: dict[str, float] = field(default_factory=dict)
    audience_consensus_p: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RankRow:
    character_id: str
    elo: float
    wins: int
    losses: int
    draws: int
    matches: int

    @property
    def win_rate(self) -> float:
        if self.matches <= 0:
            return 0.0
        return self.wins / self.matches
