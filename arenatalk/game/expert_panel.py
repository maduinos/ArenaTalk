"""Pick a debate cast by field of expertise instead of by topic interest.

Interest scoring asks "who cares about this sentence", which is the right
question for a general debate and the wrong one for a specialist question: ask
about 주식 and every persona that never says the word ties at the floor, so the
cast comes out random. This panel asks the other question — who already has the
vocabulary — and then lets the user overrule it, because a ranked list is a
suggestion, not a verdict.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arenatalk.expertise import (
    ExpertMatch,
    domain_names,
    rank_experts,
    suggest_experts,
)
from arenatalk.models import Character

MIN_CAST = 2
MAX_CAST = 5


class ExpertPanel(QWidget):
    """Domain picker + checkable roster + "debate with these" button."""

    start_requested = Signal(list)  # character ids

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._roster: list[Character] = []
        self._matches: list[ExpertMatch] = []
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("분야"))
        self.domain_box = QComboBox()
        self.domain_box.addItems(domain_names())
        self.domain_box.currentTextChanged.connect(lambda _t: self.refresh())
        row.addWidget(self.domain_box, stretch=1)
        self.suggest_btn = QPushButton("자동 추천")
        self.suggest_btn.setObjectName("GhostBtn")
        self.suggest_btn.setToolTip("이 분야 전문가를 점수 순으로 다시 체크합니다.")
        self.suggest_btn.clicked.connect(self._apply_suggestion)
        row.addWidget(self.suggest_btn)
        layout.addLayout(row)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["캐릭터", "전문 태그", "점수"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, stretch=1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.start_btn = QPushButton("이 인원으로 토론 시작")
        self.start_btn.setObjectName("StartBtn")
        self.start_btn.clicked.connect(self._emit_start)
        layout.addWidget(self.start_btn)

    # --- data ------------------------------------------------------------

    def set_roster(self, roster: list[Character]) -> None:
        self._roster = list(roster)
        self.refresh()

    @property
    def domain(self) -> str:
        return self.domain_box.currentText()

    def refresh(self) -> None:
        """Re-score for the current domain and pre-tick the suggestion."""
        if not self._roster:
            self._matches = []
            self.table.setRowCount(0)
            self.status.setText("캐릭터 폴더를 먼저 지정하세요.")
            self.start_btn.setEnabled(False)
            return
        self._matches = rank_experts(self._roster, self.domain)
        suggested = {m.character.id for m in suggest_experts(self._roster, self.domain)}
        self._render(suggested)

    def _render(self, ticked: set[str]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._matches))
        for row, match in enumerate(self._matches):
            name = QTableWidgetItem(match.character.display_name or match.character.id)
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(
                Qt.CheckState.Checked
                if match.character.id in ticked
                else Qt.CheckState.Unchecked
            )
            name.setData(Qt.ItemDataRole.UserRole, match.character.id)
            self.table.setItem(row, 0, name)

            tags = QTableWidgetItem(match.tags or "—")
            if not match.is_expert:
                tags.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(row, 1, tags)

            score = QTableWidgetItem(f"{match.score:.2f}")
            score.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.table.setItem(row, 2, score)
        self.table.blockSignals(False)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(2)
        self._update_status()

    def _apply_suggestion(self) -> None:
        if not self._roster:
            return
        suggested = {m.character.id for m in suggest_experts(self._roster, self.domain)}
        self._render(suggested)

    # --- selection -------------------------------------------------------

    def checked_ids(self) -> list[str]:
        ids: list[str] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                ids.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        self._update_status()

    def _update_status(self) -> None:
        experts = [m for m in self._matches if m.is_expert]
        chosen = self.checked_ids()
        bits = [f"이 분야 전문가 {len(experts)}명 · 선택 {len(chosen)}명"]
        if not experts:
            bits.append(
                "이 캐릭터 목록에는 해당 분야 전문가가 없습니다 — "
                "점수가 가장 가까운 캐릭터를 대신 제안했습니다."
            )
        elif len(experts) < MIN_CAST:
            bits.append("전문가가 1명뿐이라 다음 순위를 함께 제안했습니다.")
        if len(chosen) > MAX_CAST:
            bits.append(f"최대 {MAX_CAST}명까지 출전합니다.")
        self.status.setText("\n".join(bits))
        self.start_btn.setEnabled(
            not self._busy and MIN_CAST <= len(chosen) <= MAX_CAST
        )

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.domain_box.setEnabled(not busy)
        self.suggest_btn.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self._update_status()

    def _emit_start(self) -> None:
        chosen = self.checked_ids()
        if MIN_CAST <= len(chosen) <= MAX_CAST:
            self.start_requested.emit(chosen)
