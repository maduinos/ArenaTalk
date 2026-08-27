from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from arenatalk.adapters.agent_setup import ensure_agent_clis, preferred_providers
from arenatalk.adapters.cli_agents import EnsembleBackend
from arenatalk.adapters.factory import build_backend
from arenatalk.characters import active_character_root, load_characters
from arenatalk.game.scene import PHASE_LABELS, ROLE_LABELS, ArenaScene
from arenatalk.game.sprites import load_sprite_character
from arenatalk.game.worker import start_debate_thread
from arenatalk.interest import pick_top
from arenatalk.logs import DebateLogStore, match_result_from_dict, topic_title
from arenatalk.lounge import interest_with_profile
from arenatalk.models import MatchResult, StanceBallot
from arenatalk import __version__
from arenatalk.config import setup_stdio
from arenatalk.ranking import RankingStore, live_win_probs
from arenatalk.resources import UI_FONT_FAMILY, UI_FONT_STACK
from arenatalk.topic_frame import frame_topic

DEFAULT_DB = Path.home() / ".local/share/arenatalk/rankings.db"

STYLE = """
QMainWindow, QWidget#Root {
  background: #0b1220;
  color: #e2e8f0;
  font-family: __UI_FONT_STACK__;
}
QFrame#SidePanel {
  background: #0f172a;
  border-left: 1px solid #1e293b;
}
QLabel#SideTitle {
  color: #f8fafc;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.3px;
  padding: 2px 2px 6px 2px;
}
QLabel#SideHint {
  color: #94a3b8;
  font-size: 11px;
  line-height: 1.35;
  padding-bottom: 8px;
}
QTableWidget {
  background: #111827;
  alternate-background-color: #0f172a;
  gridline-color: transparent;
  border: 1px solid #1e293b;
  border-radius: 10px;
  color: #e2e8f0;
  outline: none;
}
QTableWidget::item { padding: 4px 6px; }
QHeaderView::section {
  background: #1e293b;
  color: #94a3b8;
  border: none;
  border-bottom: 1px solid #334155;
  padding: 8px 6px;
  font-weight: 600;
  font-size: 11px;
}
QTextEdit#Transcript {
  background: #111827;
  border: 1px solid #1e293b;
  border-radius: 10px;
  color: #e2e8f0;
  padding: 10px;
  font-size: 12px;
  selection-background-color: #334155;
}
QScrollBar:vertical {
  background: transparent;
  width: 8px;
  margin: 4px 2px;
}
QScrollBar::handle:vertical {
  background: #334155;
  border-radius: 4px;
  min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLineEdit#TopicInput {
  background: #111827;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 11px 14px;
  color: #f8fafc;
  font-size: 13px;
  selection-background-color: #2563eb;
}
QLineEdit#TopicInput:focus { border: 1px solid #38bdf8; }
QComboBox {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 10px;
  padding: 9px 12px;
  color: #e2e8f0;
  min-width: 150px;
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
  background: #1e293b;
  color: #e2e8f0;
  border: 1px solid #334155;
  selection-background-color: #334155;
}
QPushButton#StartBtn {
  background: #f59e0b;
  color: #0f172a;
  border: none;
  border-radius: 12px;
  padding: 11px 20px;
  font-weight: 700;
  min-width: 96px;
}
QPushButton#StartBtn:hover { background: #fbbf24; }
QPushButton#StartBtn:disabled { background: #475569; color: #94a3b8; }
QPushButton#GhostBtn {
  background: transparent;
  color: #94a3b8;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 11px;
}
QPushButton#GhostBtn:hover {
  background: #1e293b;
  color: #e2e8f0;
}
QSplitter::handle:horizontal {
  background: #1e293b;
  width: 1px;
}
QLabel#CreditLabel {
  color: #64748b;
  font-size: 11px;
  padding: 0 4px 0 12px;
  min-width: 128px;
}
QLabel#ProgressLabel {
  color: #fbbf24;
  font-size: 12px;
  font-weight: 600;
  padding: 4px 2px 8px 2px;
}
QComboBox#SpeedBox, QComboBox#HistoryBox {
  min-width: 120px;
  padding: 7px 10px;
  font-size: 12px;
}
"""

STYLE = STYLE.replace("__UI_FONT_STACK__", UI_FONT_STACK)


class GameWindow(QMainWindow):
    def __init__(self, characters_root: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"ArenaTalk v{__version__} — CharacterPet Arena")
        self.resize(1580, 920)
        self.setStyleSheet(STYLE)

        self._characters_override = characters_root
        self._roster = load_characters(characters_root)
        self._names = {c.id: c.display_name for c in self._roster}
        self._store = RankingStore(DEFAULT_DB)
        self._logs = DebateLogStore()
        self._thread = None
        self._worker = None
        self._pending_cast: list[str] = []
        self._pending_cast_chars: list = []
        self._pending_backend = None
        self._pending_topic = ""
        self._busy = False
        self._session = 0
        self._recent_cast: list[str] = []
        self._replay_timer: QTimer | None = None
        self._replay_queue: list[dict] = []
        self._replay_wait_ticks = 0
        self._last_result: MatchResult | None = None
        self._hold_scale = 1.0
        self._live_ballots: dict[str, StanceBallot] = {}
        self._live_cast_ids: list[str] = []
        self._replaying = False

        sprites = []
        for c in self._roster:
            sp = load_sprite_character(Path(c.root))
            if sp:
                sprites.append(sp)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.scene = ArenaScene(sprites)
        splitter.addWidget(self.scene)
        self._apply_lounge_labels()

        side = QFrame()
        side.setObjectName("SidePanel")
        side.setMinimumWidth(380)
        side.setMaximumWidth(520)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(8)

        cast_title = QLabel("이번 출전")
        cast_title.setObjectName("SideTitle")
        side_layout.addWidget(cast_title)
        self.cast_label = QLabel("토론을 시작하면 Top3가 표시됩니다.")
        self.cast_label.setObjectName("SideHint")
        self.cast_label.setWordWrap(True)
        side_layout.addWidget(self.cast_label)

        self.progress_label = QLabel("대기 중 — 주제를 입력하면 관전이 시작됩니다.")
        self.progress_label.setObjectName("ProgressLabel")
        self.progress_label.setWordWrap(True)
        side_layout.addWidget(self.progress_label)

        agents_title = QLabel("설치된 에이전트")
        agents_title.setObjectName("SideTitle")
        side_layout.addWidget(agents_title)
        self.agents_label = QLabel("검색 중…")
        self.agents_label.setObjectName("SideHint")
        self.agents_label.setWordWrap(True)
        self.agents_label.setMaximumHeight(72)
        side_layout.addWidget(self.agents_label)

        chars_row = QHBoxLayout()
        chars_title = QLabel("캐릭터 폴더")
        chars_title.setObjectName("SideTitle")
        chars_row.addWidget(chars_title)
        chars_row.addStretch(1)
        self.chars_folder_btn = QPushButton("폴더 선택")
        self.chars_folder_btn.setObjectName("GhostBtn")
        self.chars_folder_btn.setToolTip(
            "CharacterPet 호환 캐릭터 폴더를 고릅니다\n"
            "(characters/<id>/pet.json · AgentPet과 공유)"
        )
        self.chars_folder_btn.clicked.connect(self._pick_character_root)
        chars_row.addWidget(self.chars_folder_btn)
        side_layout.addLayout(chars_row)
        self.chars_root_label = QLabel(str(active_character_root(characters_root)))
        self.chars_root_label.setObjectName("SideHint")
        self.chars_root_label.setWordWrap(True)
        self.chars_root_label.setToolTip(self.chars_root_label.text())
        side_layout.addWidget(self.chars_root_label)

        hist_row = QHBoxLayout()
        hist_title = QLabel("지난 토론")
        hist_title.setObjectName("SideTitle")
        hist_row.addWidget(hist_title)
        hist_row.addStretch(1)
        self.stop_replay_btn = QPushButton("중지")
        self.stop_replay_btn.setObjectName("GhostBtn")
        self.stop_replay_btn.setEnabled(False)
        self.stop_replay_btn.setToolTip("다시보기 재생을 즉시 멈춥니다")
        self.stop_replay_btn.clicked.connect(self._cancel_replay)
        hist_row.addWidget(self.stop_replay_btn)
        self.replay_btn = QPushButton("다시보기")
        self.replay_btn.setObjectName("GhostBtn")
        self.replay_btn.clicked.connect(self._replay_selected)
        hist_row.addWidget(self.replay_btn)
        side_layout.addLayout(hist_row)
        self.history_box = QComboBox()
        self.history_box.setObjectName("HistoryBox")
        self.history_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        side_layout.addWidget(self.history_box)

        log_row = QHBoxLayout()
        log_title = QLabel("토론 로그")
        log_title.setObjectName("SideTitle")
        log_row.addWidget(log_title)
        log_row.addStretch(1)
        self.open_logs_btn = QPushButton("로그 폴더")
        self.open_logs_btn.setObjectName("GhostBtn")
        self.open_logs_btn.clicked.connect(self._open_log_dir)
        log_row.addWidget(self.open_logs_btn)
        side_layout.addLayout(log_row)
        self.transcript = QTextEdit()
        self.transcript.setObjectName("Transcript")
        self.transcript.setReadOnly(True)
        self.transcript.setMinimumHeight(280)
        side_layout.addWidget(self.transcript, stretch=5)

        rank_row = QHBoxLayout()
        rank_title = QLabel("Elo 랭킹")
        rank_title.setObjectName("SideTitle")
        rank_row.addWidget(rank_title)
        rank_row.addStretch(1)
        self.reset_elo_btn = QPushButton("리셋")
        self.reset_elo_btn.setObjectName("GhostBtn")
        self.reset_elo_btn.setToolTip("Elo · 승패 기록을 초기화합니다")
        self.reset_elo_btn.clicked.connect(self._reset_elo)
        rank_row.addWidget(self.reset_elo_btn)
        side_layout.addLayout(rank_row)
        self.rank_table = QTableWidget(0, 4)
        self.rank_table.setHorizontalHeaderLabels(["캐릭", "Elo", "W-L-D", "승률"])
        self.rank_table.setAlternatingRowColors(True)
        self.rank_table.verticalHeader().setVisible(False)
        self.rank_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rank_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.rank_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rank_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self.rank_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.rank_table.setColumnWidth(0, 96)
        self.rank_table.setColumnWidth(1, 64)
        self.rank_table.setColumnWidth(2, 78)
        self.rank_table.setColumnWidth(3, 58)
        self.rank_table.setMinimumHeight(120)
        self.rank_table.setMaximumHeight(200)
        self.rank_table.setMaximumWidth(330)
        side_layout.addWidget(self.rank_table, stretch=1)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)

        bar_wrap = QWidget()
        bar_wrap.setStyleSheet("background: #0b1220; border-top: 1px solid #1f2937;")
        bar = QHBoxLayout(bar_wrap)
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(10)

        self.topic_input = QLineEdit()
        self.topic_input.setObjectName("TopicInput")
        self.topic_input.setPlaceholderText("논의 주제를 입력하고 Enter…")
        self.topic_input.setToolTip(
            "주제·의견에 /절대/경로 또는 ~/경로 를 넣으면 텍스트 파일 내용을 읽어 토론에 첨부합니다"
        )
        self.topic_input.returnPressed.connect(self._on_bar_enter)
        bar.addWidget(self.topic_input, stretch=1)

        self.inject_btn = QPushButton("의견 투입")
        self.inject_btn.setObjectName("GhostBtn")
        self.inject_btn.setEnabled(False)
        self.inject_btn.setToolTip(
            "추가 의견·증거. 로컬 경로를 포함하면 파일 본문을 읽어 다음 발언부터 반영"
        )
        self.inject_btn.clicked.connect(self._inject_note)
        bar.addWidget(self.inject_btn)

        self.speed_box = QComboBox()
        self.speed_box.setObjectName("SpeedBox")
        self.speed_box.addItem("관전 · 느리게", 1.35)
        self.speed_box.addItem("관전 · 보통", 1.0)
        self.speed_box.addItem("관전 · 빠르게", 0.55)
        self.speed_box.setCurrentIndex(1)
        self.speed_box.setToolTip("말풍선 유지 시간 (관전용)")
        bar.addWidget(self.speed_box)

        self.model_box = QComboBox()
        self.model_box.setObjectName("SpeedBox")
        self.model_box.addItem("모델 · CLI기본", "default")
        self.model_box.addItem("모델 · 고성능", "strong")
        self.model_box.setCurrentIndex(0)
        self.model_box.setToolTip(
            "CLI기본=각 도구 기본 모델(자동 최신)\n"
            "고성능=롤링 별칭만 사용(Claude: opus) / 나머지 CLI 자동 최신"
        )
        bar.addWidget(self.model_box)

        self.backend_box = QComboBox()
        self._populate_backend_box()
        bar.addWidget(self.backend_box)

        self.start_btn = QPushButton("토론 시작")
        self.start_btn.setObjectName("StartBtn")
        self.start_btn.clicked.connect(self._start_match)
        bar.addWidget(self.start_btn)

        self.stop_debate_btn = QPushButton("토론 중지")
        self.stop_debate_btn.setObjectName("GhostBtn")
        self.stop_debate_btn.setEnabled(False)
        self.stop_debate_btn.setToolTip("진행 중인 토론을 즉시 중지합니다")
        self.stop_debate_btn.clicked.connect(self._cancel_debate)
        bar.addWidget(self.stop_debate_btn)

        credit = QLabel("Created by whjeong")
        credit.setObjectName("CreditLabel")
        credit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(credit)

        layout.addWidget(bar_wrap)
        self._refresh_agents()
        self._refresh_ranks()
        self._refresh_history()

    def _populate_backend_box(self) -> None:
        self.backend_box.clear()
        setup = getattr(self, "_agent_setup", None)
        if setup is None:
            # Detect only — never auto-install from GUI startup.
            setup = ensure_agent_clis(auto_install=False)
            self._agent_setup = setup
        providers = setup.providers or preferred_providers()
        self._discovered = providers
        n = len(providers)
        tier = setup.tier
        if n:
            names = ", ".join(p.display_name or p.name for p in providers)
            tier_label = "유료" if tier == "paid" else "무료"
            self.backend_box.addItem(f"자동 할당 · {tier_label} ({n}개)", "all")
            self.backend_box.setItemData(0, names, Qt.ItemDataRole.ToolTipRole)
        else:
            self.backend_box.addItem("자동 할당 (감지된 CLI 없음)", "all")
        self.backend_box.addItem("mock (무료)", "mock")
        for p in providers:
            label = p.display_name or p.name
            self.backend_box.addItem(label, p.name)

    def _pick_character_root(self) -> None:
        if self._busy:
            QMessageBox.information(
                self, "ArenaTalk", "토론/다시보기 중에는 캐릭터 폴더를 바꿀 수 없습니다."
            )
            return
        current = active_character_root(self._characters_override)
        chosen = QFileDialog.getExistingDirectory(
            self,
            "캐릭터 폴더 선택 (CharacterPet 호환)",
            str(current if current.is_dir() else Path.home()),
        )
        if not chosen:
            return
        path = Path(chosen)
        from arenatalk.characters import sync_character_library

        try:
            resolved = sync_character_library(path)
        except (OSError, NotADirectoryError, RuntimeError, ImportError) as exc:
            QMessageBox.critical(self, "ArenaTalk", f"폴더 저장 실패: {exc}")
            return
        self._characters_override = None
        self._apply_character_library(resolved)

    def _apply_character_library(self, root: Path) -> None:
        """Reload roster + sprites from a CharacterPet-compatible folder."""
        self._roster = load_characters(root)
        self._names = {c.id: c.display_name for c in self._roster}
        sprites = []
        for c in self._roster:
            sp = load_sprite_character(Path(c.root))
            if sp:
                sprites.append(sp)
        self.scene.reload_sprites(sprites)
        self._apply_lounge_labels()
        self.chars_root_label.setText(str(root))
        self.chars_root_label.setToolTip(str(root))
        self._refresh_ranks()
        n = len(self._roster)
        if n < 2:
            QMessageBox.warning(
                self,
                "ArenaTalk",
                f"폴더를 적용했습니다.\n{root}\n\n"
                f"persona가 있는 캐릭터가 {n}명입니다. "
                "CharacterPet 형식(characters/<id>/pet.json + persona)인지 확인하세요.",
            )
        else:
            QMessageBox.information(
                self,
                "ArenaTalk",
                f"캐릭터 폴더 적용: {root}\n{n}명 로드 (AgentPet과 공유)",
            )

    def _refresh_agents(self) -> None:
        setup = getattr(self, "_agent_setup", None)
        providers = (
            setup.providers
            if setup and setup.providers
            else preferred_providers()
        )
        if not providers:
            self.agents_label.setText(
                "감지된 에이전트 없음.\n"
                "터미널에서 arenatalk setup 실행"
            )
            return
        tier = setup.tier if setup else ("paid" if any("paid" in (p.notes or "") for p in providers) else "free")
        tier_label = "유료" if tier == "paid" else "무료"
        bits = []
        for p in providers:
            ver = f" · {p.version}" if p.version else ""
            bits.append(f"• {p.display_name or p.name}{ver}")
        self.agents_label.setText(
            f"{tier_label} {len(providers)}개 → 출전 캐릭에 라운드로빈 할당\n"
            + "\n".join(bits)
        )

    def _open_log_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        self._logs.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._logs.root)))

    def _build_backend(self):
        key = self.backend_box.currentData()
        profile = str(self.model_box.currentData() or "default")
        discovered = getattr(self, "_discovered", None)
        return build_backend(
            str(key),
            providers=list(discovered) if discovered else None,
            model_profile=profile,
            mock_think_seconds=1.6,
        )

    def _append_log(self, html: str) -> None:
        self.transcript.append(html)
        self.transcript.moveCursor(QTextCursor.MoveOperation.End)

    def _start_match(self) -> None:
        if self._busy:
            return
        topic = self.topic_input.text().strip()
        if not topic:
            return
        if len(self._roster) < 2:
            QMessageBox.warning(self, "ArenaTalk", "페르소나가 있는 캐릭이 2명 이상 필요합니다.")
            return

        self._busy = True
        self.start_btn.setEnabled(False)
        self.replay_btn.setEnabled(False)
        self.stop_replay_btn.setEnabled(False)
        self.stop_debate_btn.setEnabled(False)
        self.reset_elo_btn.setEnabled(False)
        self.scene.set_lobby_pick_enabled(False)
        self._stop_replay()
        self.topic_input.clear()
        self.scene.clear_result()
        self.scene.clear_win_odds()
        title = topic_title(topic)
        self.scene.set_topic(title)
        self._live_ballots = {}
        self._live_cast_ids = []
        self._hold_scale = float(self.speed_box.currentData() or 1.0)
        frame = frame_topic(topic)
        if frame.kind == "recommend":
            self.progress_label.setText(
                f"과제형 · {frame.ask_count or '?'}개 제시 후 적절성 논의"
            )
        else:
            self.progress_label.setText("출전 선정 · 토론장 이동 중…")
        self._session += 1
        topic_note = ""
        if len(topic) > len(title) + 8 or "\n" in topic:
            topic_note = (
                f"<div style='color:#64748b;font-size:11px;margin-top:2px'>"
                f"(긴 주제 {len(topic)}자 — 제목만 표시, 전체는 저장 로그에 포함)</div>"
            )
        frame_html = (
            f"<div style='color:#fbbf24;margin-top:4px'>{self._esc(frame.brief)}</div>"
        )
        self._append_log(
            "<br><hr>"
            f"<div style='color:#93c5fd;font-weight:700;margin-top:8px'>"
            f"—— 세션 #{self._session} ——</div>"
            f"<b style='color:#93c5fd'>주제</b> {self._esc(title)}"
            f"{topic_note}{frame_html}"
        )

        scores = {
            c.id: interest_with_profile(
                topic, c, self._store.get_lounge_profile(c.id)
            )
            for c in self._roster
        }
        self.scene.set_interest(scores)
        self._apply_lounge_labels()
        recent = set(self._recent_cast[-6:])
        priority = self.scene.volunteered_ids()
        profiles = {c.id: self._store.get_lounge_profile(c.id) for c in self._roster}
        picked = pick_top(
            topic,
            self._roster,
            k=3,
            recent_ids=recent,
            priority_ids=priority,
            score_fn=lambda c: interest_with_profile(topic, c, profiles.get(c.id)),
        )
        cast_chars = [c for c, _ in picked]
        cast_ids = [c.id for c in cast_chars]
        self._pending_cast = cast_ids
        priority_set = set(priority[:3])
        score_vals = [scores[cid] for cid in scores]
        random_pick = (
            not priority_set
            and len(score_vals) >= 2
            and all(abs(s - score_vals[0]) <= 1e-9 for s in score_vals[1:])
        )

        try:
            backend = self._build_backend()
        except RuntimeError as exc:
            self._fail(str(exc))
            return

        providers: dict[str, str] = {}
        profile = str(self.model_box.currentData() or "default")
        profile_label = "고성능" if profile == "strong" else "CLI기본"
        if isinstance(backend, EnsembleBackend):
            providers = backend.assign(cast_ids)
            self._append_log(
                f"<span style='color:#94a3b8'>모델</span> {profile_label}"
                f" · {self._esc(backend.model_summary())}<br>"
                "<span style='color:#94a3b8'>에이전트 자동 할당</span><br>"
                + "<br>".join(
                    f"· <b>{self._names.get(cid, cid)}</b> → {pname}"
                    for cid, pname in providers.items()
                )
            )
        else:
            self._append_log(
                f"<span style='color:#94a3b8'>모델</span> {profile_label}"
            )

        cast_bits = []
        for c, score in picked:
            via = providers.get(c.id, self.backend_box.currentData())
            force = c.id in priority_set
            if force:
                tag = "우선지정"
            elif random_pick:
                tag = f"랜덤 · 관심도 {score:.0%}"
            else:
                tag = f"관심도 {score:.0%}"
            cast_bits.append(f"• {c.display_name} ({tag}) · {via}")
            self._append_log(
                f"<span style='color:#38bdf8'>출전</span> "
                f"<b>{c.display_name}</b> {tag} · {via}"
            )
        self.cast_label.setText("\n".join(cast_bits))

        self._live_cast_ids = list(cast_ids)
        self._live_ballots = {}
        even = {cid: 1.0 / max(1, len(cast_ids)) for cid in cast_ids}
        self.scene.set_lobby_pick_enabled(False)
        self.scene.select_cast(cast_ids, providers)
        self.scene.set_win_odds(even)
        self.scene.move_cast_to_arena(cast_ids)
        if priority_set:
            self.scene.set_status("우선 지정 + 관심도 — 토론장으로 이동 중…")
        elif random_pick:
            self.scene.set_status("관심도 동일 — 전체에서 랜덤 출전, 토론장으로 이동 중…")
        else:
            self.scene.set_status("관심도 Top3 — 토론장으로 이동 중…")

        self._pending_backend = backend
        self._pending_cast_chars = cast_chars
        self._pending_topic = topic
        QTimer.singleShot(100, self._wait_for_arrival)

    def _wait_for_arrival(self) -> None:
        if not self.scene.all_walking_done():
            QTimer.singleShot(80, self._wait_for_arrival)
            return
        self.scene.set_status("토론 시작!")
        self.progress_label.setText("토론 진행 중…")
        self._append_log("<span style='color:#fbbf24'>토론장이 열렸습니다.</span>")
        self._thread, self._worker = start_debate_thread(
            self._pending_topic,
            self._roster,
            self._pending_cast_chars,
            self._pending_backend,
            self._store,
            rounds=2,
            recent_ids=set(self._recent_cast[-6:]),
            speech_hold_scale=self._hold_scale,
        )
        self._worker.thinking.connect(self._on_thinking)
        self._worker.turn.connect(self._on_turn)
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(lambda m: self.scene.set_status(m))
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._fail)
        self._worker.cancelled.connect(self._on_debate_cancelled)
        self.stop_debate_btn.setEnabled(True)
        self.inject_btn.setEnabled(True)
        self.topic_input.setEnabled(True)
        self.topic_input.setPlaceholderText("추가 의견·증거 입력 후 Enter / 의견 투입…")
        self.topic_input.clear()
        self.topic_input.setFocus()
        self._thread.start()

    def _on_bar_enter(self) -> None:
        if self._busy and not self._replaying and self._worker is not None:
            self._inject_note()
        elif not self._busy:
            self._start_match()

    def _inject_note(self) -> None:
        if self._replaying or not self._busy:
            return
        worker = self._worker
        if worker is None:
            return
        note = self.topic_input.text().strip()
        if not note:
            return
        if not worker.inject(note):
            return
        self.topic_input.clear()
        from arenatalk.attachments import extract_local_paths, load_path_attachments

        _attached, status = load_path_attachments(note)
        attach_html = ""
        if status:
            attach_html = (
                "<div style='color:#86efac;font-size:11px;margin-top:2px'>"
                + " · ".join(self._esc(s) for s in status)
                + "</div>"
            )
        elif extract_local_paths(note):
            attach_html = (
                "<div style='color:#f87171;font-size:11px;margin-top:2px'>"
                "경로를 인식했지만 첨부할 텍스트 파일이 없습니다"
                "</div>"
            )
        self._append_log(
            f"<div style='margin:6px 0;padding:6px 10px;background:#1e293b;"
            f"border-left:3px solid #fbbf24;border-radius:6px'>"
            f"<span style='color:#fbbf24;font-weight:700'>📌 관전자 투입</span>"
            f"<div style='color:#e2e8f0;margin-top:4px'>{self._esc(note)}</div>"
            f"{attach_html}"
            f"<div style='color:#64748b;font-size:11px;margin-top:2px'>"
            f"다음 발언부터 반영됩니다</div></div>"
        )
        self.scene.set_status("추가 의견·증거 투입됨 — 다음 발언부터 반영")
        self.progress_label.setText("의견 투입됨 · 다음 발언부터 반영")

    def _on_thinking(self, character_id: str, role: str, phase: str) -> None:
        self.scene.show_thinking(character_id, role, phase)
        name = character_id
        actor = self.scene.actor(character_id)
        if actor:
            name = actor.display_name
        phase_l = PHASE_LABELS.get(phase, phase)
        role_l = ROLE_LABELS.get(role, role)
        self.scene.set_status(f"생각 중 · {name} ({role_l}) · {phase_l}")
        self._append_log(
            f"<span style='color:#fbbf24'>💭 생각</span> "
            f"<b>{name}</b> · {role_l} · {phase_l}"
        )

    def _on_progress(self, current: int, total: int, phase: str) -> None:
        phase_l = PHASE_LABELS.get(phase, phase)
        pct = int(100 * current / total) if total else 0
        self.progress_label.setText(
            f"관전 {current}/{total} · {phase_l} · {pct}%"
        )

    def _on_turn(
        self,
        character_id: str,
        role: str,
        speech: str,
        phase: str,
        recommendation: str = "",
        confidence: float = 0.0,
    ) -> None:
        self.scene.show_speech(character_id, speech)
        if recommendation:
            is_lounge = phase == "audience" or role == "lounge_vote"
            ballot = StanceBallot(
                character_id=character_id,
                recommendation=recommendation,
                stance="support"
                if recommendation == "찬성"
                else ("oppose" if recommendation == "반대" else "abstain"),
                confidence=float(confidence) if confidence else 0.55,
            )
            if is_lounge:
                self.scene.set_lounge_votes({character_id: recommendation})
            else:
                self._live_ballots[character_id] = ballot
                cast = self._live_cast_ids or list(self._pending_cast)
                if cast:
                    odds = live_win_probs(self._live_ballots, cast)
                    stances = {
                        cid: b.recommendation for cid, b in self._live_ballots.items()
                    }
                    self.scene.set_win_odds(odds, stances=stances)
        name = character_id
        actor = self.scene.actor(character_id)
        if actor:
            name = actor.display_name
        phase_l = PHASE_LABELS.get(phase, phase)
        role_l = ROLE_LABELS.get(role, role)
        odds_note = ""
        actor2 = self.scene.actor(character_id)
        if actor2 and actor2.win_odds > 0:
            odds_note = f" · 승률 {actor2.win_odds:.0%}"
        self.scene.set_status(f"[{phase_l}] {name} ({role_l}){odds_note}")
        body = speech.strip().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body = body.replace("\n", "<br>")
        rec_html = ""
        if recommendation:
            rec_html = (
                f" <span style='color:#fbbf24;font-weight:500'>"
                f"· {self._esc(recommendation)} {float(confidence):.0%}</span>"
            )
        self._append_log(
            f"<div style='margin:8px 0;padding:8px 10px;background:#1e293b;"
            f"border-left:3px solid #86efac;border-radius:6px'>"
            f"<div style='color:#86efac;font-weight:700'>💬 {name}"
            f" <span style='color:#94a3b8;font-weight:500'>· {role_l}"
            f" · {phase_l}</span>{rec_html}</div>"
            f"<div style='color:#e2e8f0;margin-top:4px'>{body}</div>"
            f"</div>"
        )

    def _on_finished(self, result) -> None:
        self._last_result = result
        lines = []
        for label, p in sorted(result.recommendation_dist.items(), key=lambda x: -x[1]):
            lines.append(f"본선 {label}: {p:.0%}")
        lines.append(f"본선 합의: {result.consensus_p:.0%}")
        aud = getattr(result, "audience_dist", None) or {}
        if aud:
            for label, p in sorted(aud.items(), key=lambda x: -x[1]):
                lines.append(f"여론 {label}: {p:.0%}")
            lines.append(f"여론 합의: {getattr(result, 'audience_consensus_p', 0):.0%}")
        winner = (
            self._names.get(result.winner_id, result.winner_id)
            if result.winner_id
            else "무승부"
        )
        lines.append(f"승자: {winner}")
        conclusion = getattr(result, "conclusion", "") or ""
        if not conclusion and result.transcript:
            from arenatalk.conclusion import build_conclusion

            conclusion = build_conclusion(
                result.topic,
                result.transcript,
                result.ballots,
                winner_id=result.winner_id,
                recommendation_dist=result.recommendation_dist,
                display_names=self._names,
            )
            result.conclusion = conclusion
        self.scene.show_result(
            lines,
            result.winner_id,
            dist=result.recommendation_dist,
            consensus_p=result.consensus_p,
            winner_label=winner,
            conclusion=conclusion,
        )
        self.progress_label.setText(f"종료 · 승자 {winner}")
        self.scene.set_status("토론 종료 — 결과 확인 중…")
        if conclusion:
            body = self._esc(conclusion).replace("\n", "<br>")
            self._append_log(
                f"<div style='margin:10px 0;padding:10px;background:#0c1a2e;"
                f"border:1px solid #1e3a5f;border-radius:8px'>"
                f"<div style='color:#6ee7b7;font-weight:700'>합의 결론</div>"
                f"<div style='color:#e2e8f0;margin-top:6px'>{body}</div></div>"
            )
        self._append_log(
            "<div style='color:#f8fafc;font-weight:700;margin-top:10px'>—— 전체 대화 기록 ——</div>"
        )
        for i, row in enumerate(result.transcript, 1):
            cid = row.get("character_id", "?")
            name = self._names.get(cid, cid)
            role = ROLE_LABELS.get(row.get("role", ""), row.get("role", "?"))
            speech = (row.get("speech") or "").strip()
            speech = (
                speech.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
            )
            self._append_log(
                f"<div style='margin:6px 0'><span style='color:#64748b'>{i}.</span> "
                f"<b>{name}</b> "
                f"<span style='color:#94a3b8'>({role})</span><br>"
                f"<span style='color:#cbd5e1'>{speech or '(빈 발언)'}</span></div>"
            )
        self._append_log("<hr>")
        for line in lines:
            self._append_log(f"<span style='color:#86efac'>결과</span> {line}")
        if getattr(result, "audience_ballots", None):
            self._append_log(
                "<div style='color:#93c5fd;font-weight:700;margin-top:8px'>"
                "—— 대기실 여론 ——</div>"
            )
            for b in result.audience_ballots:
                name = self._names.get(b.character_id, b.character_id)
                self._append_log(
                    f"<span style='color:#93c5fd'>여론</span> "
                    f"<b>{name}</b> · {self._esc(b.recommendation)} "
                    f"{b.confidence:.0%}"
                    + (
                        f" · {self._esc(b.notes)}"
                        if b.notes and b.notes not in {"mock-win", "unparsed"}
                        else ""
                    )
                )
        try:
            path = self._logs.save(result, display_names=self._names)
            self._append_log(
                f"<span style='color:#38bdf8'>파일 저장</span> <code>{self._esc(str(path))}</code>"
            )
            self._refresh_history(select_latest=True)
        except OSError as exc:
            self._append_log(f"<span style='color:#f87171'>로그 저장 실패</span> {exc}")
        self._refresh_ranks()
        self._apply_lounge_labels()
        self.scene.reset_lounge_modes()
        self._cleanup_thread()
        # Remember cast so the next match rotates faces.
        for cid in result.participants:
            self._recent_cast.append(cid)
        self._recent_cast = self._recent_cast[-12:]
        if getattr(result, "research_brief", ""):
            brief = result.research_brief.strip().splitlines()
            preview = "<br>".join(
                line.replace("&", "&amp;").replace("<", "&lt;") for line in brief[:8]
            )
            self._append_log(
                f"<div style='color:#94a3b8;margin:8px 0'><b style='color:#38bdf8'>웹 참고</b><br>{preview}</div>"
            )
        QTimer.singleShot(5000, self._begin_return_home)

    def _begin_return_home(self) -> None:
        cast = list(self._pending_cast)
        if not cast:
            self._set_idle()
            self.progress_label.setText("대기 중 — 지난 토론은 「다시보기」로 볼 수 있습니다.")
            return
        self._append_log("<span style='color:#38bdf8'>캐릭터들이 자리로 돌아갑니다.</span>")
        self.scene.return_cast_home(cast)
        QTimer.singleShot(80, self._wait_for_home)

    def _wait_for_home(self) -> None:
        if not self.scene.all_walking_done():
            QTimer.singleShot(80, self._wait_for_home)
            return
        self.scene.set_status("대기 중 — 아래에 논의 주제를 입력하세요.")
        self.scene.set_phase("")
        self.scene.clear_win_odds()
        self.cast_label.setText("토론을 시작하면 Top3가 표시됩니다.")
        self.progress_label.setText("대기 중 — 지난 토론은 「다시보기」로 볼 수 있습니다.")
        self._pending_cast = []
        self._live_cast_ids = []
        self._live_ballots = {}
        self._set_idle()
        self._append_log("<span style='color:#94a3b8'>전원 자리 복귀 완료.</span>")

    def _return_home(self) -> None:
        self._begin_return_home()

    def _fail(self, message: str) -> None:
        self.scene.set_status(f"오류: {message}")
        self.progress_label.setText(f"오류 — {message[:80]}")
        self._append_log(f"<span style='color:#f87171'>오류</span> {message}")
        QMessageBox.critical(self, "ArenaTalk", message)
        self._cleanup_thread()
        self._stop_replay()
        self._set_idle()

    @staticmethod
    def _esc(text: str) -> str:
        return (
            (text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _refresh_history(self, *, select_latest: bool = False) -> None:
        current = None if select_latest else self.history_box.currentData()
        self.history_box.blockSignals(True)
        self.history_box.clear()
        rows = self._logs.list_recent(40)
        if not rows:
            self.history_box.addItem("(저장된 토론 없음)", None)
            self.history_box.blockSignals(False)
            return
        for row in rows:
            short = row.get("topic_short") or topic_title(str(row.get("topic") or "?"))
            # Combo items must be single-line or Qt hides / clips them.
            short = re.sub(r"[\r\n\t]+", " ", str(short)).strip() or "토론"
            if len(short) > 40:
                short = short[:39] + "…"
            stamp = str(row.get("saved_at") or "")
            if "T" in stamp:
                stamp = stamp.split("T", 1)[1][:8]
            else:
                stamp = stamp[-8:]
            winner = row.get("winner")
            wlabel = self._names.get(winner, winner) if winner else "무승부"
            label = f"{stamp} · {short} · {wlabel}"
            self.history_box.addItem(label, row.get("json"))
        if select_latest:
            self.history_box.setCurrentIndex(0)
        elif current:
            idx = self.history_box.findData(current)
            if idx >= 0:
                self.history_box.setCurrentIndex(idx)
        self.history_box.blockSignals(False)
        self.history_box.setToolTip(self.history_box.currentText())

    def _stop_replay(self) -> None:
        if self._replay_timer is not None:
            self._replay_timer.stop()
            self._replay_timer.deleteLater()
            self._replay_timer = None
        self._replay_queue = []
        self._replay_wait_ticks = 0

    def _set_idle(self) -> None:
        self._busy = False
        self._replaying = False
        self.start_btn.setEnabled(True)
        self.replay_btn.setEnabled(True)
        self.stop_replay_btn.setEnabled(False)
        self.stop_debate_btn.setEnabled(False)
        self.inject_btn.setEnabled(False)
        self.reset_elo_btn.setEnabled(True)
        self.topic_input.setEnabled(True)
        self.topic_input.setPlaceholderText("논의 주제를 입력하고 Enter…")
        self.scene.set_lobby_pick_enabled(True)

    def _cancel_debate(self) -> None:
        if self._replaying:
            self._cancel_replay()
            return
        worker = self._worker
        if not self._busy or worker is None:
            return
        self.stop_debate_btn.setEnabled(False)
        self.progress_label.setText("토론 중지 요청…")
        self.scene.set_status("토론 중지 중…")
        self._append_log("<span style='color:#f87171'>토론 중지를 요청했습니다.</span>")
        try:
            worker.request_cancel()
        except Exception:  # noqa: BLE001
            pass

    def _on_debate_cancelled(self) -> None:
        self._append_log("<span style='color:#f87171'>토론이 중지되었습니다. (Elo 미반영)</span>")
        self.progress_label.setText("토론 중지됨")
        self.scene.set_status("토론 중지 — 자리로 복귀")
        self._cleanup_thread()
        if self._pending_cast:
            QTimer.singleShot(200, self._begin_return_home)
        else:
            self.scene.clear_win_odds()
            self._set_idle()

    def _reset_elo(self) -> None:
        if self._busy:
            QMessageBox.information(self, "ArenaTalk", "토론/다시보기 중에는 리셋할 수 없습니다.")
            return
        reply = QMessageBox.question(
            self,
            "Elo 리셋",
            "Elo · 승패 · 내부 매치 기록을 모두 초기화할까요?\n(토론 로그 파일은 그대로 둡니다)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._store.reset()
        except OSError as exc:
            QMessageBox.critical(self, "ArenaTalk", f"리셋 실패: {exc}")
            return
        self._refresh_ranks()
        self._append_log("<span style='color:#fbbf24'>Elo 랭킹을 초기화했습니다.</span>")
        self.progress_label.setText("Elo 랭킹 초기화 완료")

    def _cancel_replay(self) -> None:
        if not self._replaying:
            return
        self._replaying = False
        self.stop_replay_btn.setEnabled(False)
        self._stop_replay()
        self._append_log("<span style='color:#f87171'>다시보기를 중지했습니다.</span>")
        self.progress_label.setText("다시보기 중지")
        self.scene.set_status("다시보기 중지 — 자리로 복귀")
        if self._pending_cast:
            QTimer.singleShot(200, self._begin_return_home)
        else:
            self.scene.clear_win_odds()
            self._set_idle()

    def _replay_selected(self) -> None:
        if self._busy:
            QMessageBox.information(self, "ArenaTalk", "진행 중인 토론/다시보기가 끝낸 뒤 시도하세요.")
            return
        name = self.history_box.currentData()
        if not name:
            QMessageBox.information(self, "ArenaTalk", "다시볼 토론이 없습니다.")
            return
        data = self._logs.load_json(str(name))
        if not data:
            QMessageBox.warning(self, "ArenaTalk", "로그 파일을 읽을 수 없습니다.")
            return
        try:
            result = match_result_from_dict(data)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "ArenaTalk", f"로그 파싱 실패: {exc}")
            return

        self._busy = True
        self._replaying = True
        self.start_btn.setEnabled(False)
        self.replay_btn.setEnabled(False)
        self.stop_replay_btn.setEnabled(True)
        self.reset_elo_btn.setEnabled(False)
        self.scene.set_lobby_pick_enabled(False)
        self._stop_replay()
        self.stop_replay_btn.setEnabled(True)
        self.transcript.clear()
        self.scene.clear_result()
        self.scene.clear_win_odds()
        self.scene.set_topic(topic_title(result.topic))
        cast_ids = list(result.participants)
        self._pending_cast = cast_ids
        self._live_cast_ids = list(cast_ids)
        self._live_ballots = {}
        providers = dict(result.providers or {})
        self.cast_label.setText(
            "\n".join(
                f"• {self._names.get(cid, cid)}"
                + (f" · {providers[cid]}" if cid in providers else "")
                for cid in cast_ids
            )
            or "(출전 정보 없음)"
        )
        self.progress_label.setText("다시보기 준비…")
        self._append_log(
            "<br><hr>"
            "<div style='color:#c4b5fd;font-weight:700'>—— 다시보기 ——</div>"
            f"<b style='color:#c4b5fd'>주제</b> {self._esc(topic_title(result.topic))}"
        )
        self._last_result = result
        try:
            if cast_ids:
                even = {cid: 1.0 / len(cast_ids) for cid in cast_ids}
                self.scene.select_cast(cast_ids, providers)
                self.scene.set_win_odds(even)
                self.scene.move_cast_to_arena(cast_ids)
                self.scene.set_status("다시보기 — 토론장으로…")
                self._replay_wait_ticks = 0
                QTimer.singleShot(100, lambda: self._wait_replay_arrival(result))
            else:
                self._start_replay_speeches(result)
        except Exception as exc:  # noqa: BLE001
            self._stop_replay()
            self._set_idle()
            QMessageBox.critical(self, "ArenaTalk", f"다시보기 시작 실패: {exc}")

    def _wait_replay_arrival(self, result: MatchResult) -> None:
        if not self._replaying:
            return
        self._replay_wait_ticks = getattr(self, "_replay_wait_ticks", 0) + 1
        # ~8s max wait; don't hang forever if a sprite is stuck mid-walk
        if not self.scene.all_walking_done() and self._replay_wait_ticks < 100:
            QTimer.singleShot(80, lambda: self._wait_replay_arrival(result))
            return
        self._start_replay_speeches(result)

    def _start_replay_speeches(self, result: MatchResult) -> None:
        if not self._replaying:
            return
        self.scene.set_status("다시보기 재생 중…")
        self._hold_scale = float(self.speed_box.currentData() or 1.0)
        self._replay_queue = list(result.transcript or [])
        total = max(1, len(self._replay_queue))
        self.progress_label.setText(f"다시보기 0/{total}")
        if self._replay_timer is not None:
            self._replay_timer.stop()
            self._replay_timer.deleteLater()
        self._replay_timer = QTimer(self)
        self._replay_timer.setSingleShot(True)
        self._replay_timer.timeout.connect(lambda: self._replay_tick(result))
        self._replay_tick(result)

    def _replay_tick(self, result: MatchResult) -> None:
        if not self._replaying:
            return
        try:
            if not self._replay_queue:
                winner = (
                    self._names.get(result.winner_id, result.winner_id)
                    if result.winner_id
                    else "무승부"
                )
                lines = [
                    f"{label}: {p:.0%}"
                    for label, p in sorted(
                        result.recommendation_dist.items(), key=lambda x: -x[1]
                    )
                ]
                lines.append(f"합의 확률: {result.consensus_p:.0%}")
                lines.append(f"승자: {winner}")
                conclusion = getattr(result, "conclusion", "") or ""
                if not conclusion and result.transcript:
                    from arenatalk.conclusion import build_conclusion

                    conclusion = build_conclusion(
                        result.topic,
                        result.transcript,
                        result.ballots,
                        winner_id=result.winner_id,
                        recommendation_dist=result.recommendation_dist,
                        display_names=self._names,
                    )
                self.scene.show_result(
                    lines,
                    result.winner_id,
                    dist=result.recommendation_dist,
                    consensus_p=result.consensus_p,
                    winner_label=winner,
                    conclusion=conclusion,
                )
                if conclusion:
                    body = self._esc(conclusion).replace("\n", "<br>")
                    self._append_log(
                        f"<div style='margin:8px 0;padding:8px;background:#0c1a2e;"
                        f"border-radius:8px'><b style='color:#6ee7b7'>합의 결론</b><br>"
                        f"<span style='color:#e2e8f0'>{body}</span></div>"
                    )
                self.progress_label.setText(f"다시보기 완료 · 승자 {winner}")
                self.scene.set_status("다시보기 종료 — 아레나 결과를 확인하세요")
                self._append_log("<span style='color:#c4b5fd'>다시보기 종료.</span>")
                QTimer.singleShot(3500, self._begin_return_home)
                return

            total = len(result.transcript or [])
            done = total - len(self._replay_queue) + 1
            row = self._replay_queue.pop(0)
            cid = row.get("character_id", "?")
            role = row.get("role", "?")
            phase = row.get("round") or row.get("phase") or ""
            if phase and str(phase).isdigit():
                phase = f"round_{phase}"
            elif not phase:
                phase = (
                    "opening"
                    if done <= 3
                    else ("final" if done > total - 3 else "round_1")
                )
            speech = row.get("speech") or ""
            recommendation = row.get("recommendation") or ""
            try:
                confidence = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            self._on_turn(cid, role, speech, str(phase), recommendation, confidence)
            self.progress_label.setText(f"다시보기 {done}/{total}")
            n = len(speech.strip())
            hold_ms = int(
                1000
                * max(1.2, min(14.0, max(4.5, 3.2 + n * 0.07)) * self._hold_scale)
            )
            if self._replay_timer is None:
                self._replay_timer = QTimer(self)
                self._replay_timer.setSingleShot(True)
                self._replay_timer.timeout.connect(lambda: self._replay_tick(result))
            self._replay_timer.start(hold_ms)
        except Exception as exc:  # noqa: BLE001
            self._stop_replay()
            self._set_idle()
            self.progress_label.setText(f"다시보기 오류 — {exc}")
            QMessageBox.critical(self, "ArenaTalk", f"다시보기 오류: {exc}")

    def _cleanup_thread(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is None:
            return
        if thread.isRunning():
            thread.quit()
            if not thread.wait(5000):
                thread.terminate()
                thread.wait(2000)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_replay()
        self._cleanup_thread()
        super().closeEvent(event)

    def _refresh_ranks(self) -> None:
        rows = self._store.leaderboard(30)
        self.rank_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = self._names.get(r.character_id, r.character_id)
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(r.character_id)
            self.rank_table.setItem(i, 0, name_item)
            elo_item = QTableWidgetItem(f"{r.elo:.0f}")
            elo_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            self.rank_table.setItem(i, 1, elo_item)
            wld_item = QTableWidgetItem(f"{r.wins}-{r.losses}-{r.draws}")
            wld_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            self.rank_table.setItem(i, 2, wld_item)
            wr_item = QTableWidgetItem(f"{r.win_rate:.0%}")
            wr_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            self.rank_table.setItem(i, 3, wr_item)
        # Keep compact fixed columns; names may elide.
        self.rank_table.setColumnWidth(0, 96)
        self.rank_table.setColumnWidth(1, 64)
        self.rank_table.setColumnWidth(2, 78)
        self.rank_table.setColumnWidth(3, 58)

    def _apply_lounge_labels(self) -> None:
        if not self._roster:
            return
        labels = {}
        for c in self._roster:
            profile = self._store.get_lounge_profile(c.id)
            labels[c.id] = profile.affinity_label()
        self.scene.set_affinity_labels(labels)


def run_game(characters_root: Path | None = None) -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    import sys

    from arenatalk.resources import window_icon_path

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("ArenaTalk")
    app.setOrganizationName("maduinos")
    app.setDesktopFileName("arenatalk")
    icon_file = window_icon_path()
    if icon_file is not None:
        icon = QIcon(str(icon_file))
        app.setWindowIcon(icon)
    app.setFont(QFont(UI_FONT_FAMILY, 10))
    window = GameWindow(characters_root)
    if icon_file is not None:
        window.setWindowIcon(QIcon(str(icon_file)))
    window.show()
    return app.exec()


def run_game_entry() -> None:
    setup_stdio()
    raise SystemExit(run_game())
