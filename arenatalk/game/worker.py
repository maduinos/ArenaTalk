from __future__ import annotations

import threading
from datetime import datetime

from PySide6.QtCore import QObject, QThread, Signal

from arenatalk.adapters.base import AgentBackend
from arenatalk.adapters.cli_agents import BackendCancelled, EnsembleBackend
from arenatalk.engines.debate import DebateCancelled, run_debate
from arenatalk.models import Character
from arenatalk.ranking import RankingStore


class DebateWorker(QObject):
    thinking = Signal(str, str, str)  # char_id, role, phase
    turn = Signal(str, str, str, str, str, float)  # id, role, speech, phase, rec, conf
    progress = Signal(int, int, str)  # current, total, phase
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        topic: str,
        roster: list[Character],
        cast: list[Character],
        backend: AgentBackend | EnsembleBackend,
        store: RankingStore,
        *,
        rounds: int = 2,
        recent_ids: set[str] | None = None,
        speech_hold_scale: float = 1.0,
    ) -> None:
        super().__init__()
        self.topic = topic
        self.roster = roster
        self.cast = cast
        self.backend = backend
        self.store = store
        self.rounds = rounds
        self.recent_ids = recent_ids or set()
        self.speech_hold_scale = speech_hold_scale
        self._cancel = False
        self._inject_lock = threading.Lock()
        self._injections: list[str] = []

    def request_cancel(self) -> None:
        self._cancel = True
        from arenatalk.adapters.cli_agents import cancel_active_backends

        cancel_active_backends()

    def inject(self, text: str) -> bool:
        """Queue spectator opinion / evidence for the next speaker prompts."""
        note = (text or "").strip()
        if not note:
            return False
        from arenatalk.attachments import expand_text_with_attachments

        expanded, status = expand_text_with_attachments(note)
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._inject_lock:
            self._injections.append(f"- ({stamp}) {expanded}")
        for line in status:
            self.log.emit(f"첨부: {line}")
        return True

    def _live_materials(self) -> str:
        with self._inject_lock:
            if not self._injections:
                return ""
            body = "\n".join(self._injections)
        return (
            "## 관전자 추가 의견·증거 (토론 중 투입 — 반드시 반영·인용하라)\n"
            f"{body}"
        )

    def run(self) -> None:
        try:
            result = run_debate(
                self.topic,
                self.roster,
                self.backend,
                self.store,
                cast=self.cast,
                rounds=self.rounds,
                parallel=False,
                speech_hold=True,
                speech_hold_scale=self.speech_hold_scale,
                recent_ids=self.recent_ids,
                research=True,
                should_cancel=lambda: self._cancel,
                live_materials=self._live_materials,
                on_thinking=self._on_thinking,
                on_turn=self._on_turn,
                on_progress=self._on_progress,
                on_event=lambda m: self.log.emit(m),
            )
            self.finished.emit(result)
        except (DebateCancelled, BackendCancelled):
            # Both mean "the user pressed stop" — never show a failure dialog.
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))

    def _on_thinking(self, character_id: str, role: str, phase: str) -> None:
        self.thinking.emit(character_id, role, phase)

    def _on_turn(
        self,
        character_id: str,
        role: str,
        speech: str,
        phase: str,
        recommendation: str = "",
        confidence: float = 0.0,
    ) -> None:
        self.turn.emit(
            character_id, role, speech, phase, recommendation, float(confidence)
        )

    def _on_progress(self, current: int, total: int, phase: str) -> None:
        self.progress.emit(current, total, phase)


def start_debate_thread(
    topic: str,
    roster: list[Character],
    cast: list[Character],
    backend: AgentBackend | EnsembleBackend,
    store: RankingStore,
    *,
    rounds: int = 2,
    recent_ids: set[str] | None = None,
    speech_hold_scale: float = 1.0,
) -> tuple[QThread, DebateWorker]:
    thread = QThread()
    worker = DebateWorker(
        topic,
        roster,
        cast,
        backend,
        store,
        rounds=rounds,
        recent_ids=recent_ids,
        speech_hold_scale=speech_hold_scale,
    )
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    worker.cancelled.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.failed.connect(worker.deleteLater)
    worker.cancelled.connect(worker.deleteLater)
    # Deliberately NOT thread.finished.connect(thread.deleteLater): the caller
    # still holds this QThread and inspects it when the debate ends. Letting Qt
    # delete it behind their back crashed the app on any failure — the modal
    # error box pumps the event loop, the deferred delete lands, and the very
    # next line touches a dead C++ object. The window deletes it explicitly.
    return thread, worker
