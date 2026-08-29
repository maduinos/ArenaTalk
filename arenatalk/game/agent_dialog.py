"""GUI for getting an agent CLI installed and signed in.

The CLI has had ``arenatalk setup`` since the beginning, but the people who
install the .deb or the Windows .exe never open a terminal — so for them the app
simply reported "감지된 에이전트 없음" and stopped. This dialog puts the same
three steps (detect → install free tier → log in) behind buttons.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from arenatalk.adapters.agent_auth import login_guide
from arenatalk.adapters.agent_setup import (
    EnsureResult,
    ensure_agent_clis,
    install_hint,
    login_provider,
    plan_free_install,
    refresh_login_state,
)
from arenatalk.adapters.cli_agents import AUTH_LABELS, ProviderInfo, known_agents

_TIER_LABELS = {"paid": "유료", "free": "무료", "": "-"}


def has_usable_agent(setup: EnsureResult | None) -> bool:
    return bool(setup and setup.ready)


class _SetupWorker(QObject):
    """Runs detection/installation off the UI thread."""

    progress = Signal(str)
    done = Signal(object)

    def __init__(self, *, auto_install: bool) -> None:
        super().__init__()
        self._auto_install = auto_install

    def run(self) -> None:
        try:
            result = ensure_agent_clis(
                auto_install=self._auto_install,
                on_progress=self.progress.emit,
                # Approval was collected on the UI thread before this started.
                confirm=(lambda _plan: True) if self._auto_install else None,
            )
        except Exception as exc:  # noqa: BLE001 - surface, never crash the GUI
            self.progress.emit(f"실패: {exc}")
            result = EnsureResult(tier="none", providers=[], messages=[str(exc)])
        self.done.emit(result)


class AgentSetupDialog(QDialog):
    """Detect, install and sign in to the agent CLIs ArenaTalk drives."""

    def __init__(self, parent=None, *, setup: EnsureResult | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("에이전트 설정")
        self.resize(760, 520)
        self._setup = setup
        # Everything installed, not just what the round-robin picked — a CLI the
        # tier preference skipped still has to be reachable for login.
        self._providers: list[ProviderInfo] = list(setup.all_seen) if setup else []
        self._thread: QThread | None = None
        self._worker: _SetupWorker | None = None
        # Filled by _render: the known CLIs shown as 미설치 rows, in row order.
        self._missing_names: list[str] = []

        layout = QVBoxLayout(self)

        self.headline = QLabel()
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["에이전트", "등급", "버전", "상태", "토론 사용", "계정 / 안내"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.detect_btn = QPushButton("다시 검사")
        self.detect_btn.clicked.connect(lambda: self._start(auto_install=False))
        buttons.addWidget(self.detect_btn)

        self.install_btn = QPushButton("무료 CLI 자동 설치")
        self.install_btn.setToolTip(
            "유료 CLI가 없거나 로그인되지 않았을 때\n"
            "Gemini CLI / Qwen Code / Ollama 중 하나를 설치합니다."
        )
        self.install_btn.clicked.connect(self._install_with_consent)
        buttons.addWidget(self.install_btn)

        self.login_btn = QPushButton("선택 항목 로그인")
        self.login_btn.setToolTip(
            "선택한 CLI의 로그인 화면을 새 터미널 창으로 엽니다.\n"
            "브라우저 승인이 끝나면 「다시 검사」를 누르세요."
        )
        self.login_btn.clicked.connect(self._login_selected)
        buttons.addWidget(self.login_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("설치·검사 로그가 여기에 표시됩니다.")
        self.log.setMaximumHeight(170)
        layout.addWidget(self.log)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.button_box.rejected.connect(self.reject)
        self.button_box.accepted.connect(self.accept)
        layout.addWidget(self.button_box)

        if setup is None:
            self._start(auto_install=False)
        else:
            for line in setup.messages:
                self._append(line)
            self._render()

    # --- results ---------------------------------------------------------

    @property
    def result_setup(self) -> EnsureResult | None:
        return self._setup

    def _append(self, line: str) -> None:
        self.log.append(line)
        self.log.ensureCursorVisible()

    def _render(self) -> None:
        providers = self._providers
        setup = self._setup
        chosen = {p.name for p in setup.providers} if setup else set()
        # Discovery only sees what is on PATH, so a CLI the user does not have
        # never appeared at all — not even as something they could install.
        seen = {p.name for p in providers}
        missing = [
            (name, label, tier)
            for name, label, tier in known_agents()
            if name not in seen
        ]
        self._missing_names = [name for name, _, _ in missing]
        self.table.setRowCount(len(providers) + len(missing))
        for row, info in enumerate(providers):
            state = AUTH_LABELS.get(info.auth, info.auth)
            if info.name in chosen:
                used = "○ 자동 할당"
            elif info.ready:
                used = "직접 선택 시"
            else:
                used = "-"
            # "로그인 필요" on its own does not say what to type, and this is
            # the most common reason a debate will not start.
            if info.needs_login:
                guidance = login_guide(info.name, info.binary)
            else:
                guidance = info.account or info.auth_detail or ""
            cells = [
                info.display_name or info.name,
                _TIER_LABELS.get(info.tier, info.tier or "-"),
                info.version or "-",
                state,
                used,
                guidance,
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 3 and info.blocked:
                    item.setForeground(Qt.GlobalColor.red)
                self.table.setItem(row, col, item)
        for offset, (name, label, tier) in enumerate(missing):
            cells = [
                label,
                _TIER_LABELS.get(tier, tier or "-"),
                "-",
                "미설치",
                "-",
                install_hint(name),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setForeground(Qt.GlobalColor.gray)
                self.table.setItem(len(providers) + offset, col, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        if providers:
            self.table.selectRow(0)

        ready = [p for p in providers if p.ready]
        stranded = [p for p in providers if p.blocked]
        if chosen:
            names = ", ".join(sorted(chosen))
            tier = _TIER_LABELS.get(setup.tier, setup.tier) if setup else ""
            extra = len(ready) - len(chosen)
            tail = (
                f" (나머지 {extra}개는 백엔드 목록에서 직접 고를 때 사용)"
                if extra > 0
                else ""
            )
            self.headline.setText(
                f"자동 할당에 쓰이는 {tier} CLI: {names}{tail}\n"
                "토론을 시작할 수 있습니다."
            )
        elif stranded:
            names = ", ".join(
                f"{p.display_name or p.name}({AUTH_LABELS.get(p.auth, p.auth)})"
                for p in stranded
            )
            unpaid = [p for p in stranded if p.needs_plan]
            tail = (
                "\n요금제가 없는 CLI는 로그인해도 토론이 돌지 않습니다 — "
                "「무료 CLI 자동 설치」를 쓰세요."
                if unpaid
                else "\n해당 줄을 선택하고 「선택 항목 로그인」을 누르세요."
            )
            self.headline.setText(f"{names} 이(가) 바로 쓸 수 없습니다.{tail}")
        else:
            self.headline.setText(
                "감지된 에이전트 CLI가 없습니다.\n"
                "「무료 CLI 자동 설치」를 누르면 Node.js와 무료 CLI를 설치합니다."
            )

    # --- background detection / install ----------------------------------

    def _set_busy(self, busy: bool) -> None:
        for btn in (self.detect_btn, self.install_btn, self.login_btn):
            btn.setEnabled(not busy)
        self.button_box.setEnabled(not busy)

    def reject(self) -> None:  # noqa: D102 - Qt override
        # The worker thread is parented to this dialog; letting it be destroyed
        # mid-install takes the app down with it. Installs are bounded by
        # INSTALL_TIMEOUT, so waiting is safe.
        if self._thread is not None:
            self._append("설치가 끝나면 닫을 수 있습니다…")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: D102 - Qt override
        if self._thread is not None:
            self._append("설치가 끝나면 닫을 수 있습니다…")
            event.ignore()
            return
        super().closeEvent(event)

    def _install_with_consent(self) -> None:
        """Never install without showing what lands on the machine first."""
        plan = plan_free_install()
        if plan.empty:
            QMessageBox.information(
                self, "에이전트 설정", "설치할 무료 CLI가 남아 있지 않습니다."
            )
            return
        body = "\n".join(f"  • {line}" for line in plan.describe())
        warn = (
            "\n\n※ ollama 단계까지 가면 모델 다운로드가 수 GB입니다."
            if plan.heavy
            else ""
        )
        answer = QMessageBox.question(
            self,
            "무료 CLI 설치",
            f"아래 항목을 이 PC에 설치합니다.\n\n{body}{warn}\n\n계속할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._append("설치를 취소했습니다.")
            return
        self._start(auto_install=True)

    def _start(self, *, auto_install: bool) -> None:
        if self._thread is not None:
            return
        self._set_busy(True)
        self._append("검사 중…" if not auto_install else "설치를 시작합니다…")
        thread = QThread(self)
        worker = _SetupWorker(auto_install=auto_install)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._append)
        worker.done.connect(self._on_done)
        worker.done.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.finished.connect(self._on_thread_finished)
        thread.start()

    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.deleteLater()
        self._set_busy(False)

    def _on_done(self, result: object) -> None:
        if isinstance(result, EnsureResult):
            self._setup = result
            self._providers = list(result.all_seen)
        self._render()

    # --- login -----------------------------------------------------------

    def _selected_provider(self) -> ProviderInfo | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._providers):
            return self._providers[row]
        return None

    def _login_selected(self) -> None:
        info = self._selected_provider()
        if info is None:
            row = self.table.currentRow() - len(self._providers)
            if 0 <= row < len(self._missing_names):
                name = self._missing_names[row]
                QMessageBox.information(
                    self,
                    "에이전트 설정",
                    f"{name} 은(는) 아직 설치되지 않아 로그인할 수 없습니다.\n\n"
                    f"설치: {install_hint(name)}\n"
                    "또는 「무료 CLI 자동 설치」를 누르세요.\n\n"
                    f"설치한 뒤 로그인: {login_guide(name)}",
                )
                return
            QMessageBox.information(self, "에이전트 설정", "먼저 목록에서 CLI를 고르세요.")
            return
        ok, message = login_provider(info)
        self._append(message)
        if not ok:
            QMessageBox.warning(self, "에이전트 설정", message)
            return
        QMessageBox.information(
            self,
            "에이전트 설정",
            f"{info.display_name or info.name} 로그인 창을 열었습니다.\n\n"
            f"{message}\n\n"
            f"직접 하려면: {login_guide(info.name, info.binary)}\n\n"
            "로그인을 마친 뒤 「다시 검사」를 누르면 상태가 갱신됩니다.",
        )

    # --- cheap refresh used by callers after a login round-trip ----------

    def refresh_auth_only(self) -> None:
        self._providers = refresh_login_state(self._providers)
        self._render()
