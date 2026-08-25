from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum, auto

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from arenatalk.game.sprites import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    SpriteAnim,
    SpriteCharacter,
    frame_pixmap,
)

PHASE_LABELS = {
    "opening": "초기 입장",
    "final": "최종 투표",
    "round_1": "1라운드 반박",
    "round_2": "2라운드 수정",
    "round_3": "3라운드",
}

ROLE_LABELS = {
    "advocate": "주장",
    "critic": "반박",
    "evidence": "근거",
    "final_vote": "투표",
}

THINKING_HINTS = {
    "opening": "입장을 정리하는 중",
    "final": "최종 결론을 고르는 중",
    "advocate": "주장을 다듬는 중",
    "critic": "허점을 찾는 중",
    "evidence": "근거를 점검하는 중",
    "final_vote": "표를 던질 준비를 하는 중",
}


class ActorMode(Enum):
    HOME = auto()
    WALKING = auto()
    ARENA = auto()
    THINKING = auto()
    SPEAKING = auto()
    CELEBRATING = auto()


@dataclass
class Actor:
    sprite: SpriteCharacter
    home: QPointF
    position: QPointF
    target: QPointF
    mode: ActorMode = ActorMode.HOME
    anim_name: str = "idle"
    frame_index: int = 0
    frame_timer: float = 0.0
    speech: str = ""
    thinking_text: str = ""
    interest: float = 0.0
    selected: bool = False
    volunteered: bool = False  # user-clicked priority seat
    provider: str = ""
    win_odds: float = 0.0
    stance_label: str = ""
    bob: float = 0.0
    facing: int = 1  # 1 right, -1 left
    _anim: SpriteAnim | None = field(default=None, repr=False)

    @property
    def character_id(self) -> str:
        return self.sprite.character_id

    @property
    def display_name(self) -> str:
        return self.sprite.display_name



class SpeechBubbleOverlay(QFrame):
    """Real widget bubble so long debate text is fully readable (scroll if needed)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpeechBubbleOverlay")
        self._kind = "speech"
        self._body = QTextEdit(self)
        self._body.setReadOnly(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self._body)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.hide()
        self._apply_style("speech")

    def _apply_style(self, kind: str) -> None:
        self._kind = kind
        if kind == "thinking":
            self.setStyleSheet(
                "#SpeechBubbleOverlay {"
                " background:#fffbeb; border:2px solid #fbbf24; border-radius:16px; }"
                "QTextEdit { background:transparent; color:#92400e; font-size:12px; border:none; }"
                "QScrollBar:vertical { width:7px; background:transparent; }"
                "QScrollBar::handle:vertical { background:#f59e0b; border-radius:3px; }"
            )
        else:
            self.setStyleSheet(
                "#SpeechBubbleOverlay {"
                " background:#ffffff; border:2px solid #64748b; border-radius:14px; }"
                "QTextEdit { background:transparent; color:#0f172a; font-size:13px; border:none; }"
                "QScrollBar:vertical { width:7px; background:transparent; }"
                "QScrollBar::handle:vertical { background:#94a3b8; border-radius:3px; }"
            )

    def show_text(
        self,
        text: str,
        *,
        kind: str = "speech",
        max_width: int = 480,
        max_height: int = 280,
    ) -> None:
        self._apply_style(kind)
        raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        cleaned_lines = [" ".join(line.split()) for line in raw.split("\n")]
        clean = "\n".join(cleaned_lines).strip()
        while "\n\n\n" in clean:
            clean = clean.replace("\n\n\n", "\n\n")
        self._body.setPlainText(clean)

        w = max(220, min(max_width, 560))
        self._body.setFixedWidth(w - 24)
        doc = self._body.document()
        doc.setTextWidth(w - 24)
        content_h = int(doc.size().height()) + 8
        h = min(max_height, max(52, content_h + 20))
        self.setFixedSize(w, h)
        self._body.setFixedHeight(h - 20)
        self.show()
        self.raise_()
        self._body.moveCursor(QTextCursor.MoveOperation.Start)

    def clear(self) -> None:
        self._body.clear()
        self.hide()


class ArenaScene(QWidget):
    """Top-down world + debate arena with animated CharacterPet sprites."""

    def __init__(self, sprites: list[SpriteCharacter], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(1100, 720)
        self.setMouseTracking(True)
        self._sprites = list(sprites)
        self._actors: list[Actor] = []
        self._topic = ""
        self._status = "대기 중 — 아래에 논의 주제를 입력하세요."
        self._phase = ""
        self._result_lines: list[str] = []
        self._result_dist: dict[str, float] = {}
        self._result_consensus: float = 0.0
        self._result_winner_label: str = ""
        self._result_conclusion: str = ""
        self._speech_anchor: QPointF | None = None
        self._speech_speaker_name: str = ""
        self._clock = 0.0
        self._arena = QRectF(560, 70, 640, 430)
        self._lounge = QRectF(20, 450, 500, 220)
        self._podiums = (
            QPointF(700, 330),
            QPointF(880, 300),
            QPointF(1060, 330),
        )
        self._hovered_id: str | None = None
        self._volunteer_order: list[str] = []
        self._lobby_pick_enabled = True
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(33)
        self._lounge_scale = 0.34
        self._bubble = SpeechBubbleOverlay(self)
        self._layout_zones()
        self._layout_home(self._sprites)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._layout_zones()
        self._relayout_home_preserving_state()
        # Keep arena cast on updated podiums if they already arrived.
        selected = [a for a in self._actors if a.selected and a.mode in {
            ActorMode.ARENA, ActorMode.THINKING, ActorMode.SPEAKING, ActorMode.CELEBRATING
        }]
        for i, actor in enumerate(selected[:3]):
            slot = self._podiums[i]
            actor.target = QPointF(slot)
            if actor.mode != ActorMode.WALKING:
                actor.position = QPointF(slot)
        speaker = next(
            (a for a in self._actors if a.mode in {ActorMode.SPEAKING, ActorMode.THINKING}),
            None,
        )
        if speaker is not None and self._bubble.isVisible():
            max_w = int(min(560, max(280, self._arena.width() - 36)))
            max_h = int(min(320, max(120, self._arena.height() * 0.55)))
            kind = "thinking" if speaker.mode == ActorMode.THINKING else "speech"
            content = speaker.thinking_text + "…" if kind == "thinking" else speaker.speech
            if content:
                self._bubble.show_text(content, kind=kind, max_width=max_w, max_height=max_h)
            self._place_bubble_for(speaker)

    def _layout_zones(self) -> None:
        w = max(self.width(), 1000)
        h = max(self.height(), 640)
        header = 58
        footer = 40
        gap = 14
        usable_top = header + 8
        usable_h = max(280, h - usable_top - footer - 10)
        # Left lounge / right arena — efficient split, full height
        lounge_w = min(440, max(340, int(w * 0.32)))
        self._lounge = QRectF(12, usable_top, lounge_w, usable_h)
        arena_x = self._lounge.right() + gap
        self._arena = QRectF(arena_x, usable_top, max(380, w - arena_x - 12), usable_h)

        base_y = self._arena.top() + self._arena.height() * 0.68
        xs = [
            self._arena.left() + self._arena.width() * 0.22,
            self._arena.left() + self._arena.width() * 0.50,
            self._arena.left() + self._arena.width() * 0.78,
        ]
        self._podiums = (
            QPointF(xs[0], base_y),
            QPointF(xs[1], base_y - 18),
            QPointF(xs[2], base_y),
        )
        self._lounge_scale = getattr(self, "_lounge_scale", 0.34)


    def _relayout_home_preserving_state(self) -> None:
        """Refit lounge grid when the window resizes; keep arena/walking actors put."""
        if not self._actors:
            return
        homes = self._compute_home_positions(len(self._actors))
        for actor, home in zip(self._actors, homes):
            actor.home = QPointF(home)
            if actor.mode == ActorMode.HOME:
                actor.position = QPointF(home)
                actor.target = QPointF(home)
        self.update()

    def _compute_home_positions(self, count: int) -> list[QPointF]:
        """Non-overlapping grid packed into the left lounge."""
        lounge = self._lounge
        if count <= 0:
            return []
        inner = lounge.adjusted(8, 44, -8, -8)
        # Fit an integer grid; prefer more columns so rows stay short
        cols = max(3, min(6, int(math.ceil(math.sqrt(count * (inner.width() / max(inner.height(), 1)))))))
        cols = min(cols, count)
        rows = (count + cols - 1) // cols
        cell_w = inner.width() / cols
        cell_h = inner.height() / rows
        # Floor so sprites + name plate never collide
        cell_w = max(56.0, cell_w)
        cell_h = max(68.0, cell_h)
        # If still overflowing height, bump columns
        while rows * cell_h > inner.height() + 0.5 and cols < count:
            cols += 1
            rows = (count + cols - 1) // cols
            cell_w = max(52.0, inner.width() / cols)
            cell_h = max(64.0, inner.height() / rows)
        # Sprite scale from cell; leave room for name (~16) + interest (~14)
        self._lounge_scale = max(0.22, min(0.36, (cell_h - 34) / 208))
        grid_w = cols * cell_w
        grid_h = rows * cell_h
        origin_x = inner.x() + max(0.0, (inner.width() - grid_w) / 2)
        origin_y = inner.y() + max(0.0, (inner.height() - grid_h) / 2)
        positions: list[QPointF] = []
        for i in range(count):
            col = i % cols
            row = i // cols
            # Anchor at feet near bottom of cell so plates don't overlap next row
            x = origin_x + col * cell_w + cell_w / 2
            y = origin_y + row * cell_h + cell_h * 0.62
            positions.append(QPointF(x, y))
        return positions


    def _layout_home(self, sprites: list[SpriteCharacter]) -> None:
        homes = self._compute_home_positions(len(sprites))
        self._actors = []
        for sp, home in zip(sprites, homes):
            self._actors.append(
                Actor(sprite=sp, home=home, position=QPointF(home), target=QPointF(home))
            )

    def actor(self, character_id: str) -> Actor | None:
        for a in self._actors:
            if a.character_id == character_id:
                return a
        return None

    def set_lobby_pick_enabled(self, enabled: bool) -> None:
        self._lobby_pick_enabled = bool(enabled)
        if not enabled:
            self._hovered_id = None
            self.unsetCursor()

    def volunteered_ids(self) -> list[str]:
        """Click order of priority seats (max useful for cast size)."""
        return list(self._volunteer_order)

    def _set_hovered(self, character_id: str | None) -> None:
        if self._hovered_id == character_id:
            return
        self._hovered_id = character_id
        if character_id:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def _actor_hit_rect(self, actor: Actor) -> QRectF:
        if actor.mode == ActorMode.HOME:
            scale = getattr(self, "_lounge_scale", 0.34)
        elif actor.mode == ActorMode.WALKING:
            scale = 0.46
        else:
            scale = 0.52
        w = FRAME_WIDTH * scale
        h = FRAME_HEIGHT * scale
        x = actor.position.x() - w / 2
        y = actor.position.y() - h + actor.bob
        # Include name plate under feet
        return QRectF(x, y, w, h + 28)

    def _hit_lobby_actor(self, pos: QPointF) -> Actor | None:
        # Top-most (front) first: higher y draws later
        candidates = [a for a in self._actors if a.mode == ActorMode.HOME]
        candidates.sort(key=lambda a: a.position.y(), reverse=True)
        for actor in candidates:
            if self._actor_hit_rect(actor).contains(pos):
                return actor
        return None

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        actor = self._hit_lobby_actor(event.position())
        self._set_hovered(actor.character_id if actor else None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hovered(None)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._lobby_pick_enabled
        ):
            actor = self._hit_lobby_actor(event.position())
            if actor is not None:
                self._toggle_volunteer(actor)
                event.accept()
                return
        super().mousePressEvent(event)

    def _toggle_volunteer(self, actor: Actor) -> None:
        if actor.volunteered:
            actor.volunteered = False
            self._volunteer_order = [x for x in self._volunteer_order if x != actor.character_id]
            n = len(self._volunteer_order)
            self.set_status(
                f"{actor.display_name} 우선 출전 해제"
                + (f" · 남은 지정 {n}명" if n else " · 관심도로 자동 선정")
            )
        else:
            actor.volunteered = True
            if actor.character_id not in self._volunteer_order:
                self._volunteer_order.append(actor.character_id)
            # Cap visual seats at 3 — extra clicks still queue but only first 3 used
            seat = self._volunteer_order.index(actor.character_id) + 1
            self.set_status(
                f"{actor.display_name} 우선 출전 지정 (#{seat}) · 다시 클릭하면 해제"
            )
        self.update()

    def set_status(self, text: str) -> None:
        self._status = text
        self.update()

    def set_phase(self, phase: str) -> None:
        self._phase = phase
        self.update()

    def set_topic(self, topic: str) -> None:
        self._topic = topic
        self.update()

    def set_interest(self, scores: dict[str, float]) -> None:
        for a in self._actors:
            a.interest = scores.get(a.character_id, 0.0)
        self.update()

    def select_cast(self, cast_ids: list[str], providers: dict[str, str]) -> None:
        cast = set(cast_ids)
        for a in self._actors:
            a.selected = a.character_id in cast
            a.provider = providers.get(a.character_id, "")
            if not a.selected:
                a.win_odds = 0.0
                a.stance_label = ""
        self.update()

    def set_win_odds(
        self,
        odds: dict[str, float],
        *,
        stances: dict[str, str] | None = None,
    ) -> None:
        stances = stances or {}
        for a in self._actors:
            if a.selected:
                a.win_odds = float(odds.get(a.character_id, 0.0))
                if a.character_id in stances:
                    a.stance_label = stances[a.character_id]
            else:
                a.win_odds = 0.0
                a.stance_label = ""
        self.update()

    def clear_win_odds(self) -> None:
        for a in self._actors:
            a.win_odds = 0.0
            a.stance_label = ""
        self.update()

    def move_cast_to_arena(self, cast_ids: list[str]) -> None:
        slots = self._podiums[: len(cast_ids)]
        for cid, slot in zip(cast_ids, slots):
            actor = self.actor(cid)
            if actor is None:
                continue
            actor.target = QPointF(slot)
            actor.mode = ActorMode.WALKING
            actor.speech = ""
            actor.thinking_text = ""
            actor.bob = 0.0
        self.set_status("토론장으로 이동 중…")
        self.update()

    def return_cast_home(self, cast_ids: list[str]) -> None:
        # Keep final result panel visible through walk-home and idle until next match.
        for cid in cast_ids:
            actor = self.actor(cid)
            if actor is None:
                continue
            actor.target = QPointF(actor.home)
            actor.mode = ActorMode.WALKING
            actor.speech = ""
            actor.thinking_text = ""
            actor.bob = 0.0
            actor.selected = False
        self._phase = "returning"
        self._bubble.clear()
        self._speech_anchor = None
        self._speech_speaker_name = ""
        self.set_status("토론 종료 — 각자 자리로 돌아가는 중…")
        self.update()

    def show_thinking(self, character_id: str, role: str, phase: str) -> None:
        actor = self.actor(character_id)
        if actor is None:
            return
        # Keep the previous speaker's bubble until the next show_speech().
        for a in self._actors:
            if a.character_id == character_id:
                continue
            if a.mode == ActorMode.THINKING and a.selected:
                a.mode = ActorMode.ARENA
                a.anim_name = "waiting"
                a.thinking_text = ""
        hint = THINKING_HINTS.get(phase) or THINKING_HINTS.get(role) or "생각하는 중"
        role_l = ROLE_LABELS.get(role, role)
        actor.mode = ActorMode.THINKING
        actor.thinking_text = f"{hint} · {role_l}"
        actor.speech = ""
        actor.anim_name = "researching"
        self._phase = phase
        self.update()


    def show_speech(self, character_id: str, speech: str) -> None:
        actor = self.actor(character_id)
        if actor is None:
            return
        # Only one speech bubble at a time — sequential turns avoid overlap.
        for a in self._actors:
            if a.character_id == character_id:
                continue
            if a.mode in {ActorMode.SPEAKING, ActorMode.THINKING, ActorMode.ARENA} and a.selected:
                a.mode = ActorMode.ARENA
                a.anim_name = "waiting"
                a.thinking_text = ""
                a.speech = ""
        actor.mode = ActorMode.SPEAKING
        actor.speech = speech
        actor.thinking_text = ""
        actor.anim_name = "waving"
        # Fixed anchor in the upper arena (does not follow sprite bob / micro-moves).
        ax = actor.target.x() if actor.selected else actor.position.x()
        self._speech_anchor = QPointF(ax, self._arena.top() + 52)
        self._speech_speaker_name = actor.display_name
        self._bubble.clear()
        self.update()


    def _place_bubble_for(self, actor: Actor) -> None:
        if not self._bubble.isVisible():
            return
        bw = self._bubble.width()
        bh = self._bubble.height()
        bounds = self._arena if actor.selected or actor.mode != ActorMode.HOME else QRectF(self.rect())
        margin = 10
        # Anchor near speaker head, but keep the whole bubble inside arena
        cx = actor.position.x()
        x = int(cx - bw / 2)
        y = int(actor.position.y() - 100 - bh)
        x = max(int(bounds.left()) + margin, min(x, int(bounds.right()) - bw - margin))
        y = max(int(bounds.top()) + margin + 36, min(y, int(bounds.bottom()) - bh - margin))
        # Prefer upper arena strip for long speeches so characters stay visible
        if bh > 140:
            y = int(bounds.top()) + margin + 36
            x = max(int(bounds.left()) + margin, min(int(cx - bw / 2), int(bounds.right()) - bw - margin))
        self._bubble.move(x, y)
        self._bubble.raise_()


    def show_result(
        self,
        lines: list[str],
        winner_id: str | None,
        *,
        dist: dict[str, float] | None = None,
        consensus_p: float = 0.0,
        winner_label: str = "",
        conclusion: str = "",
    ) -> None:
        self._result_lines = list(lines)
        self._result_dist = dict(dist or {})
        self._result_consensus = float(consensus_p)
        self._result_winner_label = winner_label or (
            next((a.display_name for a in self._actors if a.character_id == winner_id), winner_id or "무승부")
        )
        self._result_conclusion = (conclusion or "").strip()
        self._speech_anchor = None
        self._speech_speaker_name = ""
        for a in self._actors:
            if not a.selected:
                continue
            a.thinking_text = ""
            a.speech = ""
            if winner_id and a.character_id == winner_id:
                a.mode = ActorMode.CELEBRATING
                a.anim_name = "jumping"
            else:
                a.mode = ActorMode.ARENA
                a.anim_name = "waiting"
        self._phase = "result"
        self._bubble.clear()
        self.update()


    def clear_result(self) -> None:
        self._result_lines = []
        self._result_dist = {}
        self._result_consensus = 0.0
        self._result_winner_label = ""
        self._result_conclusion = ""
        self._speech_anchor = None
        self._speech_speaker_name = ""
        if self._phase in {"result", "returning"}:
            self._phase = ""
        for a in self._actors:
            if a.mode == ActorMode.CELEBRATING:
                a.mode = ActorMode.ARENA if a.selected else ActorMode.HOME
                a.anim_name = "idle"
                a.bob = 0.0
        self.update()

    def all_walking_done(self) -> bool:
        return all(
            a.mode != ActorMode.WALKING and _dist(a.position, a.target) < 2.5
            for a in self._actors
        )

    def _on_tick(self) -> None:
        dt = 0.033
        self._clock += dt
        for actor in self._actors:
            dist = _dist(actor.position, actor.target)
            # Never yank THINKING/SPEAKING actors into a walk just because of float drift.
            moving = actor.mode == ActorMode.WALKING or (
                dist >= 2.5
                and actor.mode
                not in {
                    ActorMode.THINKING,
                    ActorMode.SPEAKING,
                    ActorMode.CELEBRATING,
                }
            )
            if moving:
                if dist >= 1.0:
                    dx = actor.target.x() - actor.position.x()
                    dy = actor.target.y() - actor.position.y()
                    dist = max(dist, 0.001)
                    speed = 165.0
                    step = min(dist, speed * dt)
                    actor.position = QPointF(
                        actor.position.x() + dx / dist * step,
                        actor.position.y() + dy / dist * step,
                    )
                    actor.facing = 1 if dx >= 0 else -1
                    actor.mode = ActorMode.WALKING
                    actor.anim_name = "running-right" if actor.facing >= 0 else "running-left"
                    actor.bob = 0.0
                    actor.speech = ""
                    actor.thinking_text = ""
                else:
                    actor.position = QPointF(actor.target)
                    actor.mode = ActorMode.ARENA if actor.selected else ActorMode.HOME
                    actor.anim_name = "idle"
                    actor.bob = 0.0
            else:
                # Arrived — settle mode and reaction anim
                if actor.mode == ActorMode.WALKING:
                    actor.mode = ActorMode.ARENA if actor.selected else ActorMode.HOME
                    actor.anim_name = "idle"
                    actor.bob = 0.0
                elif actor.mode == ActorMode.THINKING:
                    actor.anim_name = "researching"
                    actor.bob = math.sin(self._clock * 5.5) * 3.5
                elif actor.mode == ActorMode.SPEAKING:
                    actor.anim_name = "waving"
                    actor.bob = -abs(math.sin(self._clock * 7.0)) * 5.0
                elif actor.mode == ActorMode.CELEBRATING:
                    actor.anim_name = "jumping"
                    actor.bob = -abs(math.sin(self._clock * 9.0)) * 10.0
                elif actor.mode == ActorMode.ARENA:
                    # Watch the active speaker/thinker
                    active = any(
                        o.mode in {ActorMode.THINKING, ActorMode.SPEAKING, ActorMode.CELEBRATING}
                        for o in self._actors
                        if o.character_id != actor.character_id
                    )
                    actor.anim_name = "waiting" if active else "idle"
                    actor.bob = math.sin(self._clock * 2.0 + hash(actor.character_id) % 7) * 1.2
                else:
                    # Lounge: hover or click-volunteer → wave (AgentPet hover uses waving)
                    hand_up = (
                        actor.volunteered
                        or actor.character_id == self._hovered_id
                    )
                    actor.anim_name = "waving" if hand_up else "idle"
                    actor.bob = (
                        math.sin(self._clock * 3.2 + hash(actor.character_id) % 5) * 1.5
                        if hand_up
                        else 0.0
                    )

            anim = actor.sprite.animations.get(actor.anim_name) or actor.sprite.animations["idle"]
            # Restart frame cycle when animation changes
            if actor._anim is not anim:
                actor.frame_index = 0
                actor.frame_timer = 0.0
            actor._anim = anim
            if actor.mode in {ActorMode.SPEAKING, ActorMode.THINKING}:
                self._place_bubble_for(actor)
            actor.frame_timer += dt
            frame_dur = 1.0 / max(anim.fps, 1.0)
            if actor.frame_timer >= frame_dur:
                actor.frame_timer = 0.0
                actor.frame_index = (actor.frame_index + 1) % anim.frames
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_background(p)
        self._draw_arena(p)
        self._draw_header(p)

        layered = sorted(self._actors, key=lambda a: a.position.y())
        foreground_modes = {ActorMode.THINKING, ActorMode.SPEAKING, ActorMode.WALKING, ActorMode.CELEBRATING}
        foreground = [a for a in layered if a.mode in foreground_modes]
        background = [a for a in layered if a.mode not in foreground_modes]
        for actor in background:
            self._draw_actor(p, actor)
        for actor in foreground:
            self._draw_actor(p, actor)

        # Speech last so it never sits under another sprite / chip.
        self._draw_active_speech(p)
        self._draw_result_panel(p)
        self._draw_footer(p)
        p.end()

    def _draw_background(self, p: QPainter) -> None:
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor("#0b1220"))
        grad.setColorAt(0.45, QColor("#111827"))
        grad.setColorAt(1.0, QColor("#1e293b"))
        p.fillRect(self.rect(), grad)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 35))
        p.drawRect(0, 0, self.width(), 8)
        p.drawRect(0, self.height() - 8, self.width(), 8)

        lounge = self._lounge
        p.setPen(QPen(QColor("#334155"), 1))
        p.setBrush(QColor(15, 23, 42, 150))
        p.drawRoundedRect(lounge, 16, 16)
        p.setPen(QPen(QColor(148, 163, 184, 40), 1))
        p.drawRoundedRect(lounge.adjusted(1, 1, -1, -1), 15, 15)
        p.setPen(QColor("#94a3b8"))
        p.setFont(QFont("Sans", 10, QFont.Weight.DemiBold))
        n_vol = len(self._volunteer_order)
        lounge_title = f"대기실  ·  {len(self._actors)}명"
        if n_vol:
            used = min(n_vol, 3)
            lounge_title += (
                f"  ·  우선 {used}명"
                if n_vol <= 3
                else f"  ·  우선 {used}/{n_vol} (앞 3명)"
            )
        p.drawText(
            int(lounge.x() + 16),
            int(lounge.y() + 22),
            lounge_title,
        )
        p.setPen(QColor("#64748b"))
        p.setFont(QFont("Sans", 8))
        p.drawText(
            int(lounge.x() + 16),
            int(lounge.y() + 38),
            "호버=손흔들기  ·  클릭=우선 출전",
        )

    def _draw_arena(self, p: QPainter) -> None:
        r = self._arena
        shadow = QRectF(r.x() + 4, r.y() + 6, r.width(), r.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))
        p.drawRoundedRect(shadow, 20, 20)

        arena_grad = QLinearGradient(r.topLeft(), r.bottomRight())
        arena_grad.setColorAt(0.0, QColor("#172554"))
        arena_grad.setColorAt(0.55, QColor("#0f2744"))
        arena_grad.setColorAt(1.0, QColor("#0b1c33"))
        p.setBrush(arena_grad)
        p.setPen(QPen(QColor("#f59e0b"), 2.5))
        p.drawRoundedRect(r, 20, 20)

        p.setPen(QColor("#fbbf24"))
        p.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        p.drawText(int(r.x() + 22), int(r.y() + 32), "토론장")
        p.setPen(QColor("#64748b"))
        p.setFont(QFont("Sans", 9))
        p.drawText(int(r.x() + 92), int(r.y() + 30), "DEBATE ARENA")

        for i, pt in enumerate(self._podiums):
            # Keep podium below the name/odds/provider stack (~50px under feet).
            podium = QRectF(pt.x() - 40, pt.y() + 54, 80, 12)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(71, 85, 105, 160))
            p.drawRoundedRect(podium, 6, 6)
            p.setPen(QColor("#94a3b8"))
            p.setFont(QFont("Sans", 7, QFont.Weight.Bold))
            p.drawText(podium, Qt.AlignmentFlag.AlignCenter, str(i + 1))

    def _draw_header(self, p: QPainter) -> None:
        from arenatalk import __version__

        bar = QRectF(14, 10, self.width() - 28, 46)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(15, 23, 42, 180))
        p.drawRoundedRect(bar, 12, 12)

        p.setPen(QColor("#f8fafc"))
        p.setFont(QFont("Sans", 14, QFont.Weight.Bold))
        p.drawText(28, 38, "ArenaTalk")
        title_advance = p.fontMetrics().horizontalAdvance("ArenaTalk")
        p.setPen(QColor("#94a3b8"))
        p.setFont(QFont("Sans", 10, QFont.Weight.DemiBold))
        version_text = f"v{__version__}"
        p.drawText(28 + title_advance + 10, 37, version_text)
        ver_w = p.fontMetrics().horizontalAdvance(version_text)
        p.setPen(QColor("#64748b"))
        p.setFont(QFont("Sans", 10))
        p.drawText(28 + title_advance + 10 + ver_w + 12, 37, "캐릭터 토론 시뮬레이터")

        if self._topic:
            chip_x = 28 + title_advance + 10 + ver_w + 190
            chip_w = max(160, min(self.width() - chip_x - 180, 420))
            if chip_w > 140:
                chip = QRectF(chip_x, 18, chip_w, 28)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#1e293b"))
                p.drawRoundedRect(chip, 8, 8)
                p.setPen(QColor("#93c5fd"))
                p.setFont(QFont("Sans", 10))
                topic = self._topic if len(self._topic) <= 48 else self._topic[:45] + "…"
                p.drawText(int(chip.x() + 12), int(chip.y() + 18), f"주제  {topic}")

        if self._phase and self._phase not in {"result", "returning"}:
            label = PHASE_LABELS.get(self._phase, self._phase)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#f59e0b"))
            badge = QRectF(self.width() - 162, 18, 136, 28)
            p.drawRoundedRect(badge, 8, 8)
            p.setPen(QColor("#0f172a"))
            p.setFont(QFont("Sans", 10, QFont.Weight.Bold))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        elif self._phase == "result" or (self._phase == "returning" and self._result_lines):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#22c55e"))
            badge = QRectF(self.width() - 162, 18, 136, 28)
            p.drawRoundedRect(badge, 8, 8)
            p.setPen(QColor("#0f172a"))
            p.setFont(QFont("Sans", 10, QFont.Weight.Bold))
            label = "자리 복귀" if self._phase == "returning" else "최종 결과"
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        elif self._phase == "returning":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#38bdf8"))
            badge = QRectF(self.width() - 162, 18, 136, 28)
            p.drawRoundedRect(badge, 8, 8)
            p.setPen(QColor("#0f172a"))
            p.setFont(QFont("Sans", 10, QFont.Weight.Bold))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, "자리 복귀")

    def _draw_footer(self, p: QPainter) -> None:
        bar = QRectF(12, self.height() - 38, self.width() - 24, 28)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(15, 23, 42, 200))
        p.drawRoundedRect(bar, 10, 10)
        status = self._status
        p.setFont(QFont("Sans", 10))
        fm = p.fontMetrics()
        max_w = max(80, int(bar.width()) - 24)
        if fm.horizontalAdvance(status) > max_w:
            while status and fm.horizontalAdvance(status + "…") > max_w:
                status = status[:-1]
            status = status + "…"
        p.setPen(QColor("#e2e8f0"))
        p.drawText(int(bar.x() + 14), int(bar.y() + 18), status)


    def _draw_result_panel(self, p: QPainter) -> None:
        if not self._result_lines and not self._result_dist and not self._result_conclusion:
            return
        arena = self._arena
        pad_x, pad_y = 18, 16
        max_w = int(min(560, max(340, arena.width() * 0.68)))
        dist_items = sorted(self._result_dist.items(), key=lambda x: -x[1])
        bar_rows = max(1, len(dist_items)) if dist_items else 0
        bar_h = 16
        bar_gap = 8
        title_h = 28
        meta_h = 44
        graph_h = bar_rows * (bar_h + bar_gap) + (8 if bar_rows else 0)

        conclusion = (self._result_conclusion or "").strip()
        concl_lines = conclusion.splitlines() if conclusion else []
        # Estimate conclusion block height
        concl_font = QFont("Sans", 10)
        p.setFont(concl_font)
        fm = p.fontMetrics()
        text_w = max_w - pad_x * 2
        wrapped: list[str] = []
        for raw in concl_lines:
            line = raw.rstrip()
            if not line:
                wrapped.append("")
                continue
            # simple wrap
            while line:
                if fm.horizontalAdvance(line) <= text_w:
                    wrapped.append(line)
                    break
                # binary-ish cut
                lo, hi = 1, len(line)
                cut = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if fm.horizontalAdvance(line[:mid]) <= text_w:
                        cut = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                # prefer break at space
                space = line.rfind(" ", 0, cut)
                if space >= cut // 2:
                    cut = space + 1
                wrapped.append(line[:cut].rstrip())
                line = line[cut:].lstrip()
            if len(wrapped) >= 12:
                if wrapped[-1] and not wrapped[-1].endswith("…"):
                    wrapped[-1] = wrapped[-1][: max(1, len(wrapped[-1]) - 1)] + "…"
                break
        concl_h = (fm.height() + 3) * len(wrapped) + (18 if wrapped else 0)

        bw = max_w
        bh = pad_y * 2 + title_h + graph_h + meta_h + concl_h
        max_h = max(220, int(arena.height() * 0.72))
        bh = min(bh, max_h)

        bx = arena.left() + (arena.width() - bw) / 2
        by = arena.top() + 36
        by = min(by, arena.bottom() - bh - 14)
        by = max(arena.top() + 32, by)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 80))
        p.drawRoundedRect(QRectF(bx + 4, by + 6, bw, bh), 16, 16)
        card = QLinearGradient(bx, by, bx, by + bh)
        card.setColorAt(0.0, QColor("#0f172a"))
        card.setColorAt(1.0, QColor("#1e293b"))
        p.setBrush(card)
        p.setPen(QPen(QColor("#34d399"), 2))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 16, 16)

        p.setPen(QColor("#6ee7b7"))
        p.setFont(QFont("Sans", 13, QFont.Weight.Bold))
        p.drawText(int(bx + pad_x), int(by + pad_y + 16), "최종 결과")

        colors = [
            QColor("#38bdf8"),
            QColor("#f59e0b"),
            QColor("#a78bfa"),
            QColor("#f472b6"),
            QColor("#34d399"),
        ]
        label_font = QFont("Sans", 10)
        y = by + pad_y + title_h + 4
        bar_left = bx + pad_x + 72
        bar_right = bx + bw - pad_x - 52
        bar_span = max(40.0, bar_right - bar_left)
        for i, (label, frac) in enumerate(dist_items):
            p.setFont(label_font)
            p.setPen(QColor("#e2e8f0"))
            p.drawText(int(bx + pad_x), int(y + 12), label[:8])
            track = QRectF(bar_left, y + 1, bar_span, bar_h)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(51, 65, 85, 180))
            p.drawRoundedRect(track, 8, 8)
            fill_w = bar_span * max(0.0, min(1.0, frac))
            if fill_w > 2:
                fill = QRectF(bar_left, y + 1, fill_w, bar_h)
                grad = QLinearGradient(fill.topLeft(), fill.topRight())
                c = colors[i % len(colors)]
                grad.setColorAt(0.0, c)
                grad.setColorAt(1.0, c.lighter(120))
                p.setBrush(grad)
                p.drawRoundedRect(fill, 8, 8)
            p.setPen(QColor("#f8fafc"))
            p.setFont(QFont("Sans", 10, QFont.Weight.DemiBold))
            p.drawText(int(bar_right + 8), int(y + 12), f"{frac:.0%}")
            y += bar_h + bar_gap

        # Conclusion body
        if wrapped:
            y += 4
            p.setPen(QColor("#94a3b8"))
            p.setFont(QFont("Sans", 9, QFont.Weight.DemiBold))
            p.drawText(int(bx + pad_x), int(y + 10), "합의 결론")
            y += 16
            p.setFont(QFont("Sans", 10))
            line_h = p.fontMetrics().height() + 3
            bottom_limit = by + bh - pad_y - meta_h
            for line in wrapped:
                if y + line_h > bottom_limit:
                    break
                p.setPen(QColor("#e2e8f0"))
                p.drawText(int(bx + pad_x), int(y + p.fontMetrics().ascent()), line)
                y += line_h

        # Meta footer
        p.setPen(QColor("#94a3b8"))
        p.setFont(QFont("Sans", 10))
        meta_y = by + bh - pad_y - 26
        p.drawText(int(bx + pad_x), int(meta_y), f"합의 확률  {self._result_consensus:.0%}")
        p.setPen(QColor("#fbbf24"))
        p.setFont(QFont("Sans", 11, QFont.Weight.Bold))
        winner = self._result_winner_label or "무승부"
        p.drawText(int(bx + pad_x), int(meta_y + 18), f"승자  {winner}")

    def _draw_actor(self, p: QPainter, actor: Actor) -> None:
        anim = actor._anim or actor.sprite.animations["idle"]
        if actor.mode == ActorMode.HOME:
            scale = getattr(self, "_lounge_scale", 0.34)
        elif actor.mode == ActorMode.WALKING:
            scale = 0.46
        else:
            scale = 0.52
        pix = frame_pixmap(anim, actor.frame_index, scale=scale)
        if pix.isNull():
            return
        w, h = pix.width(), pix.height()
        x = actor.position.x() - w / 2
        y = actor.position.y() - h + actor.bob

        # Soft shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 55))
        p.drawEllipse(QPointF(actor.position.x(), actor.position.y() + 4), w * 0.28, 7)
        p.drawPixmap(int(x), int(y), pix)

        # Motion streak while walking
        if actor.mode == ActorMode.WALKING:
            p.setPen(QPen(QColor(148, 163, 184, 55), 1.5))
            streak_dir = -actor.facing
            for i in range(2):
                sx = actor.position.x() + streak_dir * (12 + i * 9)
                sy = actor.position.y() - h * 0.5 + i * 7
                p.drawLine(int(sx), int(sy), int(sx + streak_dir * 7), int(sy))

        if actor.selected and actor.mode != ActorMode.WALKING:
            ring = QColor("#fbbf24") if actor.mode == ActorMode.THINKING else QColor("#38bdf8")
            p.setPen(QPen(ring, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(actor.position.x(), y - 10), 8, 8)
        elif actor.volunteered and actor.mode == ActorMode.HOME:
            p.setPen(QPen(QColor("#fbbf24"), 2))
            p.setBrush(QColor(251, 191, 36, 60))
            p.drawEllipse(QPointF(actor.position.x(), y - 10), 8, 8)

        # Foot labels: name, then one meta chip (승률 · provider). Podium sits below.
        gap = 5
        stack_y = actor.position.y() + 6
        name = actor.display_name[:14]
        p.setFont(QFont("Sans", 8, QFont.Weight.Bold))
        fm = p.fontMetrics()
        nw = max(fm.horizontalAdvance(name) + 12, 48)
        name_h = 16
        plate = QRectF(actor.position.x() - nw / 2, stack_y, nw, name_h)
        p.setPen(Qt.PenStyle.NoPen)
        if actor.volunteered and actor.mode == ActorMode.HOME and not actor.selected:
            p.setBrush(QColor(120, 53, 15, 230))
        else:
            p.setBrush(QColor(15, 23, 42, 220))
        p.drawRoundedRect(plate, 5, 5)
        p.setPen(QColor("#fde68a") if actor.volunteered and actor.mode == ActorMode.HOME and not actor.selected else QColor("#f8fafc"))
        p.drawText(plate, Qt.AlignmentFlag.AlignCenter, name)
        stack_y += name_h + gap

        if actor.volunteered and actor.mode == ActorMode.HOME and not actor.selected:
            try:
                seat = self._volunteer_order.index(actor.character_id) + 1
            except ValueError:
                seat = 0
            tag = f"우선 #{seat}" if seat else "우선"
            p.setFont(QFont("Sans", 8, QFont.Weight.Bold))
            tfm = p.fontMetrics()
            tw = tfm.horizontalAdvance(tag) + 12
            th = 15
            tplate = QRectF(actor.position.x() - tw / 2, stack_y, tw, th)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(245, 158, 11, 230))
            p.drawRoundedRect(tplate, 5, 5)
            p.setPen(QColor("#0f172a"))
            p.drawText(tplate, Qt.AlignmentFlag.AlignCenter, tag)
            stack_y += th + gap

        if actor.selected and actor.mode != ActorMode.WALKING:
            bits: list[str] = []
            if actor.win_odds > 0.0:
                if actor.stance_label:
                    bits.append(f"{actor.stance_label[:2]} {actor.win_odds:.0%}")
                else:
                    bits.append(f"승 {actor.win_odds:.0%}")
            if actor.provider:
                bits.append(actor.provider[:10])
            if bits:
                meta = " · ".join(bits)
                leading = (
                    actor.win_odds > 0.0
                    and actor.win_odds
                    >= max((o.win_odds for o in self._actors if o.selected), default=0.0)
                    - 1e-9
                )
                p.setFont(QFont("Sans", 8, QFont.Weight.Bold))
                mfm = p.fontMetrics()
                mw = mfm.horizontalAdvance(meta) + 14
                mh = 16
                mplate = QRectF(actor.position.x() - mw / 2, stack_y, mw, mh)
                if actor.win_odds > 0.0 and leading:
                    bg, fg = QColor(22, 101, 52, 235), QColor("#bbf7d0")
                elif actor.win_odds > 0.0:
                    bg, fg = QColor(30, 41, 59, 235), QColor("#cbd5e1")
                else:
                    bg, fg = QColor(30, 41, 59, 220), QColor("#94a3b8")
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(mplate, 5, 5)
                p.setPen(fg)
                p.drawText(mplate, Qt.AlignmentFlag.AlignCenter, meta)

        if actor.interest > 0.01 and not actor.selected and actor.mode == ActorMode.HOME:
            p.setPen(QColor("#64748b"))
            p.setFont(QFont("Sans", 8))
            p.drawText(
                int(actor.position.x() - 16),
                int(actor.position.y() + 30),
                f"{actor.interest:.0%}",
            )

        if actor.mode == ActorMode.WALKING:
            label = "자리로…" if _dist(actor.target, actor.home) < 2 else "토론장으로…"
            self._draw_move_chip(p, actor.position.x(), y - 8, label)
        elif actor.mode == ActorMode.THINKING:
            # Compact chip above the head — avoids mid-body side popups
            self._draw_status_chip(p, actor.position.x(), y - 22, "생각 중")
        # Speech is drawn once in _draw_active_speech (avoids overlap / z-order issues).

    def _draw_active_speech(self, p: QPainter) -> None:
        if self._result_lines or self._result_dist or self._result_conclusion:
            return
        speaker = next(
            (a for a in self._actors if a.mode == ActorMode.SPEAKING and a.speech),
            None,
        )
        if speaker is None:
            return
        # Fixed anchor: characters may bob/walk micro-adjust, bubble stays put.
        anchor = self._speech_anchor or QPointF(speaker.target.x(), self._arena.top() + 52)
        self._draw_bubble(
            p,
            anchor.x(),
            float(anchor.y()),
            speaker.speech,
            speaker_name=self._speech_speaker_name or speaker.display_name,
        )


    def _draw_move_chip(self, p: QPainter, cx: float, top: float, text: str) -> None:
        font = QFont("Sans", 8, QFont.Weight.Bold)
        p.setFont(font)
        fm = p.fontMetrics()
        pad_x, pad_y = 8, 4
        bw = fm.horizontalAdvance(text) + pad_x * 2
        bh = fm.height() + pad_y * 2
        bx = max(8, min(cx - bw / 2, self.width() - bw - 8))
        by = max(8, top - bh - 6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(14, 165, 233, 230))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 10, 10)
        p.setPen(QColor("#0f172a"))
        p.drawText(int(bx + pad_x), int(by + pad_y + fm.ascent()), text)

    def _draw_status_chip(self, p: QPainter, cx: float, top: float, text: str) -> None:
        """Small centered chip above the head (thinking / status)."""
        dots = "." * (1 + int(self._clock * 2.2) % 3)
        display = f"{text}{dots}"
        font = QFont("Sans", 8, QFont.Weight.DemiBold)
        p.setFont(font)
        fm = p.fontMetrics()
        pad_x, pad_y = 8, 3
        bw = fm.horizontalAdvance(display) + pad_x * 2
        bh = fm.height() + pad_y * 2
        bx = max(8, min(cx - bw / 2, self.width() - bw - 8))
        by = max(8, top - bh - 4)
        p.setPen(QPen(QColor("#f59e0b"), 1.5))
        p.setBrush(QColor(255, 251, 235, 240))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 8, 8)
        p.setPen(QColor("#92400e"))
        p.drawText(QRectF(bx, by, bw, bh), Qt.AlignmentFlag.AlignCenter, display)

    def _draw_thinking_bubble(self, p: QPainter, cx: float, top: float, text: str) -> None:
        # Kept for compatibility; arena now uses _draw_status_chip.
        self._draw_status_chip(p, cx, top, "생각 중")

    def _draw_bubble(
        self,
        p: QPainter,
        cx: float,
        top: float,
        text: str,
        *,
        speaker_name: str = "",
    ) -> None:
        """Wide, fixed speech card in the upper arena — does not track sprite bob."""
        display = " ".join(text.split())
        if not display:
            return

        arena = self._arena
        font = QFont("Sans", 11)
        name_font = QFont("Sans", 9, QFont.Weight.DemiBold)
        p.setFont(font)
        fm = p.fontMetrics()
        pad_x, pad_y = 18, 14
        name_h = 22 if speaker_name else 0
        # Wide horizontal card — only one bubble at a time, so overlap is avoided by sequencing.
        max_w = int(max(320, min(560, arena.width() * 0.70)))
        ceiling = arena.top() + 40
        # Leave room above the cast
        floor = min(arena.top() + arena.height() * 0.48, arena.bottom() - 160)
        max_h = int(max(100, floor - ceiling))

        text_max_w = max_w - pad_x * 2
        lines = _wrap_text(display, text_max_w, fm)
        line_h = max(fm.height(), fm.lineSpacing())
        content_w = max((fm.horizontalAdvance(line) for line in lines), default=120)
        bw = min(max_w, max(280, content_w + pad_x * 2))
        bh = pad_y * 2 + name_h + line_h * len(lines)

        if bh > max_h:
            fit = max(1, (max_h - pad_y * 2 - name_h) // line_h)
            lines = lines[:fit]
            bh = pad_y * 2 + name_h + line_h * len(lines)

        bx = cx - bw / 2
        bx = max(arena.left() + 16, min(bx, arena.right() - bw - 16))
        by = float(top) if top >= ceiling else ceiling
        by = max(ceiling, min(by, floor - bh))

        # Soft drop shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawRoundedRect(QRectF(bx + 3, by + 5, bw, bh), 16, 16)

        # Card fill
        grad = QLinearGradient(bx, by, bx, by + bh)
        grad.setColorAt(0.0, QColor("#fffdf8"))
        grad.setColorAt(1.0, QColor("#f1f5f9"))
        p.setBrush(grad)
        p.setPen(QPen(QColor("#cbd5e1"), 1.5))
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 16, 16)

        # Accent bar
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#38bdf8"))
        p.drawRoundedRect(QRectF(bx + 1, by + 1, 6, bh - 2), 3, 3)

        if speaker_name:
            p.setFont(name_font)
            p.setPen(QColor("#0284c7"))
            p.drawText(int(bx + pad_x + 4), int(by + pad_y + 12), speaker_name)

        p.setFont(font)
        p.setPen(QColor("#0f172a"))
        y0 = by + pad_y + name_h + fm.ascent()
        for i, line in enumerate(lines):
            p.drawText(int(bx + pad_x + 4), int(y0 + i * line_h), line)

        # Small pointer toward speaker column (short, not tracking bob)
        tip_x = min(max(cx, bx + 28), bx + bw - 28)
        tip_y = by + bh
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f1f5f9"))
        p.drawPolygon(
            QPolygonF(
                [
                    QPointF(tip_x - 10, tip_y - 1),
                    QPointF(tip_x + 10, tip_y - 1),
                    QPointF(tip_x, tip_y + 12),
                ]
            )
        )


    def _draw_full_speech_panel(self, p: QPainter, text: str) -> None:
        """Long replies: use the empty upper arena as a tall reading panel."""
        arena = self._arena
        panel = QRectF(
            arena.left() + 18,
            arena.top() + 42,
            min(arena.width() - 36, max(280, arena.width() * 0.55)),
            min(arena.height() * 0.55, arena.height() - 160),
        )
        p.setPen(QPen(QColor("#64748b"), 2))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(panel, 12, 12)
        p.setPen(QColor("#0f172a"))
        font = QFont("Sans", 10)
        p.setFont(font)
        text_rect = QRectF(panel.x() + 14, panel.y() + 14, panel.width() - 28, panel.height() - 28)
        p.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            text,
        )


def _dist(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def _wrap_text(text: str, max_w: int, fm) -> list[str]:
    if not text:
        return [""]
    pieces: list[tuple[str, bool]] = []
    for word in text.split(" "):
        if not word:
            continue
        if fm.horizontalAdvance(word) <= max_w:
            pieces.append((word, False))
            continue
        chunk = ""
        first = True
        for ch in word:
            trial = chunk + ch
            if chunk and fm.horizontalAdvance(trial) > max_w:
                pieces.append((chunk, not first))
                first = False
                chunk = ch
            else:
                chunk = trial
        if chunk:
            pieces.append((chunk, not first))

    lines: list[str] = []
    cur = ""
    for token, sticky in pieces:
        if not cur:
            cur = token
            continue
        sep = "" if sticky else " "
        trial = cur + sep + token
        if fm.horizontalAdvance(trial) > max_w:
            lines.append(cur)
            cur = token
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines if lines else [""]
