"""Schedule Task tab — Kanban board for scheduled/automated tasks.

Columns: Backlog / Scheduled / Running / Waiting Input / Done / Failed /
Paused. Cards drag between columns (dropping = changing status), double-click
edits, right-click offers Run now / Edit / Duplicate / Pause / Delete / View
logs / Create-next-from-output. Header has search, a type filter, Add Task
and AI Create Task (preview first — nothing is created until confirmed).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import tasks as taskrepo
from ..core.projects import list_projects
from ..core.tasks import STATUSES, chain_error, duplicate_task, new_task
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .calendar_view import CalendarView
from .icons import icon
from .osutil import open_path

_VIEWS = ("kanban", "calendar")

# Priority shown as a plain text tag (no colored-emoji squares). Only the
# elevated priorities get a visible marker; low/medium stay unmarked as before.
_PRIORITY_ICONS = {"low": "", "medium": "", "high": "· high", "critical": "· critical"}


class _KanbanColumn(QListWidget):
    """One status lane. Accepts drops from sibling columns; a drop means
    'move this task to my status'."""

    task_dropped = Signal(str, str)   # task_id, new_status

    def __init__(self, status: str):
        super().__init__()
        self.status = status
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        # Shift/Ctrl-click several cards in the SAME column, then right-click
        # → "Delete N selected" to bulk-remove tasks instead of one at a time.
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setWordWrap(True)
        self.setMinimumWidth(190)

    def dropEvent(self, event):  # noqa: N802
        source = event.source()
        if isinstance(source, _KanbanColumn) and source is not self:
            item = source.currentItem()
            tid = item.data(Qt.UserRole) if item else None
            if tid:
                event.acceptProposedAction()
                self.task_dropped.emit(tid, self.status)
                return
        event.ignore()


class ScheduleTaskTab(QWidget):
    status_message = Signal(str)

    def __init__(self, ctx: AppContext, scheduler=None):
        super().__init__()
        self.ctx = ctx
        self.scheduler = scheduler          # TaskScheduler (may be None in tests)
        self._ai_worker: Optional[AgentWorker] = None
        self._tasks_dir: Optional[Path] = None   # None → default repo dir

        root = QVBoxLayout(self)

        # ---- header ----------------------------------------------------
        header = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("font-weight:700; font-size:15px;")
        self.counts_lbl = QLabel("")
        self.counts_lbl.setObjectName("hint")
        self.add_btn = QPushButton()
        self.add_btn.setIcon(icon("plus"))
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add_task)
        self.ai_btn = QPushButton()
        self.ai_btn.setIcon(icon("sparkle"))
        self.ai_btn.clicked.connect(self._ai_create)
        self.view_combo = QComboBox()
        for v in _VIEWS:
            self.view_combo.addItem("", v)
        self.view_combo.currentIndexChanged.connect(self._on_view_changed)
        header.addWidget(self._title)
        header.addWidget(self.counts_lbl, 1)
        header.addWidget(self.view_combo)
        header.addWidget(self.add_btn)
        header.addWidget(self.ai_btn)
        root.addLayout(header)

        # ---- board / calendar (two views of the SAME tasks) -----------------
        self._view_stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        board = QWidget()
        scroll.setWidget(board)
        cols = QHBoxLayout(board)
        cols.setSpacing(8)
        self.columns: Dict[str, _KanbanColumn] = {}
        self.column_headers: Dict[str, QLabel] = {}
        for status in STATUSES:
            box = QVBoxLayout()
            head = QLabel()
            head.setStyleSheet("font-weight:600;")
            col = _KanbanColumn(status)
            col.task_dropped.connect(self._on_task_dropped)
            col.itemDoubleClicked.connect(self._on_double_click)
            col.setContextMenuPolicy(Qt.CustomContextMenu)
            col.customContextMenuRequested.connect(
                lambda pos, c=col: self._context_menu(c, pos))
            box.addWidget(head)
            box.addWidget(col, 1)
            holder = QWidget()
            holder.setLayout(box)
            cols.addWidget(holder)
            self.columns[status] = col
            self.column_headers[status] = head
        self._view_stack.addWidget(scroll)
        self.calendar = CalendarView()
        self.calendar.edit_task.connect(self._edit_task)
        self.calendar.add_task_on_date.connect(self._add_task_on_date)
        self._view_stack.addWidget(self.calendar)
        root.addWidget(self._view_stack, 1)

        if self.scheduler is not None:
            self.scheduler.tasks_changed.connect(self.refresh)
            self.scheduler.task_started.connect(lambda _tid: self.refresh())
            self.scheduler.task_finished.connect(lambda _tid, _ok: self.refresh())

        # Belt-and-braces: also re-read the board every 10s so a card's lane
        # ALWAYS reflects reality (Scheduled → Running → Done) even if some
        # change slipped past the signals (e.g. task files edited externally).
        from PySide6.QtCore import QTimer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(10_000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()

        self.refresh()
        on_language_changed(self._retranslate)

    # ---- i18n ------------------------------------------------------------
    def _retranslate(self) -> None:
        self._title.setText(tr("schedtask.title"))
        self.add_btn.setText(tr("schedtask.add_btn"))
        self.add_btn.setToolTip(tr("schedtask.add_tooltip"))
        self.ai_btn.setText(tr("schedtask.ai_btn"))
        self.ai_btn.setToolTip(tr("schedtask.ai_tooltip"))
        for i, v in enumerate(_VIEWS):
            self.view_combo.setItemText(i, tr(f"schedtask.view.{v}"))
        for status, col in self.columns.items():
            col.setToolTip(tr(f"schedtask.col_tip.{status}"))
        self.refresh()

    # ---- Kanban / Calendar view switch --------------------------------
    def _on_view_changed(self) -> None:
        self._view_stack.setCurrentIndex(self.view_combo.currentIndex())

    def _add_task_on_date(self, date_str: str) -> None:
        """Create a task pre-filled with the clicked calendar date (default
        09:00) — same editor Add Task opens, nothing is saved until confirmed."""
        from .task_editor_dialog import TaskEditorDialog

        t = new_task("", schedule={"enabled": True, "run_at": f"{date_str} 09:00"})
        dlg = TaskEditorDialog(t, taskrepo.list_tasks(self._tasks_dir), self, ctx=self.ctx)
        if dlg.exec() and dlg.edited_task:
            self._save_and_refresh(dlg.edited_task)
            self.status_message.emit(tr("schedtask.msg_created"))

    # ---- board rendering ---------------------------------------------------
    def _card_text(self, t: dict) -> str:
        prio = _PRIORITY_ICONS.get(t.get("priority", "medium"), "")
        ai = "[AI] " if t.get("is_ai_generated") else ""
        sched = t.get("schedule", {})
        when = sched.get("run_at") if sched.get("enabled") else None
        when_line = when or tr("schedtask.no_schedule")
        chain = ""
        if t.get("dependency", {}).get("next_task_id") or t.get("dependency", {}).get("previous_task_id"):
            chain = " (linked)"
        last = t.get("logs", {}).get("last_status")
        last_line = {"success": tr("schedtask.last_success"),
                     "failed": tr("schedtask.last_failed")}.get(last, tr("schedtask.last_never"))
        # Card shows ONLY the task's own title (plus the [AI] marker and chain
        # note) — no "[Cowork]"/"[Code]" task-type tag cluttering it.
        return (f"{ai}{t.get('title', '')}{chain}\n"
                f"{when_line}   {prio}\n{last_line}")

    def refresh(self) -> None:
        all_tasks = taskrepo.list_tasks(self._tasks_dir)
        counts = {s: 0 for s in STATUSES}
        for col in self.columns.values():
            col.clear()
        for t in all_tasks:
            status = t.get("status", "backlog")
            if status not in self.columns:
                continue
            counts[status] += 1
            item = QListWidgetItem(self._card_text(t))
            item.setData(Qt.UserRole, t["task_id"])
            self.columns[status].addItem(item)
        for status, col in self.columns.items():
            self.column_headers[status].setText(
                f"{tr(f'schedtask.status.{status}')} ({counts[status]})")
            if col.count() == 0:
                empty = QListWidgetItem(tr("schedtask.no_tasks"))
                empty.setFlags(Qt.NoItemFlags)
                col.addItem(empty)
        self.counts_lbl.setText("   ".join(
            f"{tr(f'schedtask.status.{s}')}: {counts[s]}" for s in STATUSES if counts[s]))
        self.calendar.set_tasks(all_tasks)

    # ---- actions --------------------------------------------------------
    def _save_and_refresh(self, task: dict) -> None:
        taskrepo.save_task(task, self._tasks_dir)
        self.refresh()

    def _add_task(self) -> None:
        from .task_editor_dialog import TaskEditorDialog

        dlg = TaskEditorDialog(None, taskrepo.list_tasks(self._tasks_dir), self, ctx=self.ctx)
        if dlg.exec() and dlg.edited_task:
            self._save_and_refresh(dlg.edited_task)
            self.status_message.emit(tr("schedtask.msg_created"))

    def _edit_task(self, task_id: str) -> None:
        from .task_editor_dialog import TaskEditorDialog

        task = taskrepo.load_task(task_id, self._tasks_dir)
        if not task:
            return
        dlg = TaskEditorDialog(task, taskrepo.list_tasks(self._tasks_dir), self, ctx=self.ctx)
        if dlg.exec() and dlg.edited_task:
            self._save_and_refresh(dlg.edited_task)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.UserRole)
        if tid:
            self._edit_task(tid)

    def _on_task_dropped(self, task_id: str, new_status: str) -> None:
        """Dropping a card into a lane ACTS on the task, not just relabels it:
        → Running actually runs it now; → Done marks it completed; → Scheduled
        puts it on the calendar (opening the editor if no time is set yet)."""
        task = taskrepo.load_task(task_id, self._tasks_dir)
        if not task:
            return
        if task.get("status") == "running":
            self.refresh()   # can't drag a running task
            return
        if new_status == "running":
            # Dropping into Running = "run it now" (counts as manual approval).
            self.refresh()
            self._run_now(task)
            return
        if new_status == "done":
            task["status"] = "done"
            task["schedule"]["enabled"] = False   # done by hand → don't re-fire
            self._save_and_refresh(task)
            return
        task["status"] = new_status
        if new_status == "scheduled" and not task["schedule"].get("enabled"):
            if task["schedule"].get("run_at"):
                task["schedule"]["enabled"] = True
            else:
                # No time set yet — a silently-disabled "Scheduled" card would
                # never run and look broken. Open the editor so the user sets
                # the schedule right away.
                self._save_and_refresh(task)
                self.status_message.emit(tr("schedtask.msg_set_schedule"))
                self._edit_task(task_id)
                return
        self._save_and_refresh(task)

    @staticmethod
    def _is_multi_selection(item, selected) -> bool:
        """True when the right-clicked card is part of an existing multi-item
        selection — pure boolean, kept separate from _context_menu so it's
        testable without ever invoking Qt's (modal, event-loop-blocking) menu."""
        return len(selected) > 1 and item in selected

    def _context_menu(self, col: _KanbanColumn, pos) -> None:
        item = col.itemAt(pos)
        if item is None or not item.data(Qt.UserRole):
            return
        selected = [it for it in col.selectedItems() if it.data(Qt.UserRole)]
        if self._is_multi_selection(item, selected):
            self._bulk_delete_menu(col, pos, selected)
            return
        tid = item.data(Qt.UserRole)
        task = taskrepo.load_task(tid, self._tasks_dir)
        if not task:
            return
        menu = QMenu(col)
        run_act = menu.addAction(tr("schedtask.menu_run"))
        edit_act = menu.addAction(tr("schedtask.menu_edit"))
        dup_act = menu.addAction(tr("schedtask.menu_duplicate"))
        paused = task.get("status") == "paused"
        pause_act = menu.addAction(tr("schedtask.menu_resume" if paused else "schedtask.menu_pause"))
        logs_act = menu.addAction(tr("schedtask.menu_logs"))
        hist_act = menu.addAction(tr("schedtask.menu_history"))
        next_act = menu.addAction(tr("schedtask.menu_create_next"))
        menu.addSeparator()
        del_act = menu.addAction(tr("schedtask.menu_delete"))
        chosen = menu.exec(col.viewport().mapToGlobal(pos))
        if chosen == run_act:
            self._run_now(task)
        elif chosen == edit_act:
            self._edit_task(tid)
        elif chosen == dup_act:
            self._save_and_refresh(duplicate_task(task))
        elif chosen == pause_act:
            task["status"] = "backlog" if paused else "paused"
            self._save_and_refresh(task)
        elif chosen == logs_act:
            self._view_logs(task)
        elif chosen == hist_act:
            _RunHistoryDialog(task, self).exec()
        elif chosen == next_act:
            self._create_next_from_output(task)
        elif chosen == del_act:
            if QMessageBox.question(self, tr("schedtask.menu_delete"),
                                    tr("schedtask.delete_confirm", title=task.get("title", ""))
                                    ) == QMessageBox.Yes:
                taskrepo.delete_task(tid, self._tasks_dir)
                self.refresh()

    def _bulk_delete_menu(self, col: _KanbanColumn, pos, selected) -> None:
        """Right-click on a multi-selection within one column (Shift/Ctrl-click
        several cards first): one action deletes every selected task. The
        popup itself is a thin wrapper — see _confirm_and_delete_selected for
        the actual (independently testable) confirm+delete logic."""
        menu = QMenu(col)
        del_act = menu.addAction(tr("schedtask.menu_delete_selected", n=len(selected)))
        chosen = menu.exec(col.viewport().mapToGlobal(pos))
        if chosen == del_act:
            self._confirm_and_delete_selected(selected)

    def _confirm_and_delete_selected(self, selected) -> bool:
        """Confirm, then delete every task in ``selected``. Split out of
        _bulk_delete_menu so tests can drive it directly without having to
        fake a real (modal, event-loop-blocking) QMenu popup."""
        if QMessageBox.question(
                self, tr("schedtask.menu_delete"),
                tr("schedtask.delete_multi_confirm", n=len(selected))) != QMessageBox.Yes:
            return False
        for item in selected:
            tid = item.data(Qt.UserRole)
            if tid:
                taskrepo.delete_task(tid, self._tasks_dir)
        self.refresh()
        return True

    def _run_now(self, task: dict) -> None:
        if task.get("task_type") == "manual":
            self.status_message.emit(tr("schedtask.msg_manual_norun"))
            return
        if self.scheduler is None:
            self.status_message.emit(tr("schedtask.msg_no_scheduler"))
            return
        if self.scheduler.run_now(task["task_id"]):
            self.status_message.emit(tr("schedtask.msg_running", title=task.get("title", "")))
        self.refresh()

    def _view_logs(self, task: dict) -> None:
        run_id = task.get("logs", {}).get("last_run_id")
        if not run_id:
            QMessageBox.information(self, tr("schedtask.menu_logs"), tr("schedtask.no_runs_yet"))
            return
        folder = taskrepo.ARTIFACTS_DIR / task["task_id"] / run_id
        if folder.exists():
            open_path(str(folder))
        else:
            QMessageBox.information(self, tr("schedtask.menu_logs"), tr("schedtask.no_runs_yet"))

    def _create_next_from_output(self, task: dict) -> None:
        """Scaffold a follow-up task pre-wired to consume this task's output."""
        nxt = new_task(tr("schedtask.next_of", title=task.get("title", "")))
        nxt["task_type"] = "cowork"
        nxt["input"]["mode"] = "previous_task_output"
        nxt["input"]["previous_task_id"] = task["task_id"]
        nxt["dependency"]["previous_task_id"] = task["task_id"]
        err = chain_error(taskrepo.list_tasks(self._tasks_dir) + [nxt],
                          task["task_id"], nxt["task_id"])
        if err:
            QMessageBox.warning(self, tr("schedtask.g_dependency"), err)
            return
        taskrepo.save_task(nxt, self._tasks_dir)
        task["dependency"]["next_task_id"] = nxt["task_id"]
        task["dependency"]["pass_output_to_next"] = True
        if task["dependency"].get("run_next_mode", "none") == "none":
            task["dependency"]["run_next_mode"] = "run_after_success"
        taskrepo.save_task(task, self._tasks_dir)
        self.refresh()
        self._edit_task(nxt["task_id"])

    # ---- AI create ----------------------------------------------------------
    def _ai_create(self) -> None:
        dlg = _AiCreateDialog(self.ctx, self)
        if dlg.exec() and dlg.created_tasks:
            for t in dlg.created_tasks:
                taskrepo.save_task(t, self._tasks_dir)
            self.refresh()
            self.status_message.emit(tr("schedtask.msg_ai_created", n=len(dlg.created_tasks)))


class _RunHistoryDialog(QDialog):
    """Run history of one task as a table (newest first): time, status, error;
    double-click a row to open that run's artifact folder."""

    def __init__(self, task: dict, parent=None):
        super().__init__(parent)
        self._task = task
        self.setWindowTitle(f"{tr('schedtask.menu_history')} — {task.get('title', '')}")
        self.resize(620, 380)
        root = QVBoxLayout(self)
        hint = QLabel(tr("schedtask.hist_hint"))
        hint.setObjectName("hint")
        root.addWidget(hint)

        runs = list(reversed(task.get("runs", []) or []))
        self.table = QTableWidget(len(runs), 4)
        self.table.setHorizontalHeaderLabels([
            tr("schedtask.hist_col_time"), tr("schedtask.hist_col_status"),
            tr("schedtask.hist_col_run"), tr("schedtask.hist_col_error"),
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for row, run in enumerate(runs):
            ok = run.get("status") == "success"
            cells = (
                run.get("finished_at", ""),
                str(run.get("status", "")),
                run.get("run_id", ""),
                (run.get("error") or "")[:200],
            )
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 0:
                    item.setData(Qt.UserRole, run.get("run_id", ""))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(self._open_artifact)
        root.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _open_artifact(self, item: QTableWidgetItem) -> None:
        first = self.table.item(item.row(), 0)
        run_id = first.data(Qt.UserRole) if first else ""
        if not run_id:
            return
        folder = taskrepo.ARTIFACTS_DIR / self._task["task_id"] / run_id
        if folder.exists():
            open_path(str(folder))


class _DropZone(QLabel):
    """Drag-an-.xlsx-here area for the Import tab."""

    file_dropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(70)
        self.setStyleSheet(
            "QLabel { border: 2px dashed rgba(140,146,152,0.6); border-radius: 10px;"
            " color: #8c9298; padding: 10px; }")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(
                (".xlsx", ".xlsm", ".xls", ".csv", ".json")):
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())


class _AiCreateDialog(QDialog):
    """Create tasks two ways, one tab each (both preview first — nothing is
    saved until the user confirms): ✨ AI gen from a natural-language
    description, or 📥 Import from a filled Excel template (pick or drag)."""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTabWidget

        self.ctx = ctx
        self.created_tasks: List[dict] = []
        self._planned: List[dict] = []
        self._worker: Optional[AgentWorker] = None
        self.setWindowTitle(tr("schedtask.ai_btn"))
        self.resize(600, 520)

        root = QVBoxLayout(self)
        ws_row = QHBoxLayout()
        ws_row.addWidget(QLabel(tr("schedtask.f_workspace")))
        self.workspace_combo = QComboBox()
        self.workspace_combo.addItem(tr("schedtask.no_workspace"), "")
        for p in list_projects():
            self.workspace_combo.addItem(p.name, p.project_id)
        self.workspace_combo.setToolTip(tr("schedtask.hint_workspace"))
        ws_row.addWidget(self.workspace_combo, 1)
        root.addLayout(ws_row)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # ---- tab 1: AI gen ------------------------------------------------
        ai_page = QWidget()
        al = QVBoxLayout(ai_page)
        al.addWidget(QLabel(tr("schedtask.ai_desc_label")))
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setPlaceholderText(tr("schedtask.ai_desc_ph"))
        self.desc_edit.setMaximumHeight(110)
        al.addWidget(self.desc_edit)
        # Attachments (files + links) — merged into every task this generates,
        # AND into the planning prompt so the AI knows they exist.
        attach_row = QHBoxLayout()
        self.ai_files_edit = QLineEdit()
        self.ai_files_edit.setPlaceholderText(tr("schedtask.files_placeholder"))
        ai_pick_btn = QPushButton(tr("schedtask.pick_files"))
        ai_pick_btn.setIcon(icon("folder"))
        ai_pick_btn.clicked.connect(self._ai_pick_files)
        attach_row.addWidget(self.ai_files_edit, 1)
        attach_row.addWidget(ai_pick_btn)
        al.addWidget(QLabel(tr("schedtask.f_files")))
        al.addLayout(attach_row)
        self.ai_links_edit = QLineEdit()
        self.ai_links_edit.setPlaceholderText(tr("schedtask.links_placeholder"))
        al.addWidget(QLabel(tr("schedtask.f_links")))
        al.addWidget(self.ai_links_edit)
        self.gen_btn = QPushButton(tr("schedtask.ai_generate"))
        self.gen_btn.setIcon(icon("sparkle"))
        self.gen_btn.setObjectName("primary")
        self.gen_btn.clicked.connect(self._generate)
        al.addWidget(self.gen_btn)
        al.addWidget(QLabel(tr("schedtask.ai_preview_label")))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        al.addWidget(self.preview, 1)
        self.tabs.addTab(ai_page, tr("schedtask.tab_ai"))

        # ---- tab 2: Import from Excel --------------------------------------
        imp_page = QWidget()
        il = QVBoxLayout(imp_page)
        tpl_btn = QPushButton(tr("schedtask.export_template_btn"))
        tpl_btn.setIcon(icon("upload"))
        tpl_btn.clicked.connect(self._export_template)
        il.addWidget(tpl_btn)
        pick_row = QHBoxLayout()
        pick_btn = QPushButton(tr("schedtask.import_pick_btn"))
        pick_btn.setIcon(icon("folder"))
        pick_btn.clicked.connect(self._pick_import_file)
        pick_row.addWidget(pick_btn)
        pick_row.addStretch(1)
        il.addLayout(pick_row)
        self.drop_zone = _DropZone()
        self.drop_zone.setText(tr("schedtask.drop_hint"))
        self.drop_zone.file_dropped.connect(self._load_import_file)
        il.addWidget(self.drop_zone)
        il.addWidget(QLabel(tr("schedtask.ai_preview_label")))
        self.import_preview = QPlainTextEdit()
        self.import_preview.setReadOnly(True)
        il.addWidget(self.import_preview, 1)
        self.tabs.addTab(imp_page, tr("schedtask.tab_import"))

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText(tr("schedtask.ai_confirm"))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.buttons.accepted.connect(self._confirm)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

    # ---- Import tab ------------------------------------------------------
    def _export_template(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..core.task_excel import export_template

        path, _ = QFileDialog.getSaveFileName(
            self, tr("schedtask.export_template_btn"),
            "cowork_tasks_template.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_template(path)
            open_path(str(Path(path).parent))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("schedtask.tab_import"), str(exc))

    def _pick_import_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..core.task_import import IMPORT_FILTER

        path, _ = QFileDialog.getOpenFileName(
            self, tr("schedtask.import_pick_btn"), "", IMPORT_FILTER)
        if path:
            self._load_import_file(path)

    def _load_import_file(self, path: str) -> None:
        from ..core.task_import import import_tasks

        try:
            self._planned = import_tasks(path)
        except ValueError as exc:
            self.import_preview.setPlainText(str(exc))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        by_id = {t["task_id"]: t["title"] for t in self._planned}
        lines = []
        for i, t in enumerate(self._planned, 1):
            sched = t.get("schedule", {})
            when = sched.get("run_at") if sched.get("enabled") else tr("schedtask.no_schedule")
            deps = t.get("dependency", {}).get("depends_on") or []
            dep_note = ("  ← depends: " + ", ".join(by_id.get(d, "?") for d in deps)) if deps else ""
            lines.append(f"{i}. [{t.get('task_type')}] {t.get('title')}\n"
                         f"    {when}   repeat={sched.get('repeat_type', 'none')}{dep_note}")
        self.import_preview.setPlainText("\n\n".join(lines))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self._planned))

    def _ai_pick_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        files, _ = QFileDialog.getOpenFileNames(self, tr("schedtask.pick_files"))
        if files:
            existing = [f for f in self.ai_files_edit.text().split(";") if f.strip()]
            self.ai_files_edit.setText("; ".join(existing + files))

    def _attached_files(self) -> List[str]:
        return [p.strip() for p in self.ai_files_edit.text().split(";") if p.strip()]

    def _attached_links(self) -> List[str]:
        return [u.strip() for u in self.ai_links_edit.text().split(";") if u.strip()]

    def _generate(self) -> None:
        description = self.desc_edit.toPlainText().strip()
        if not description or self._worker is not None:
            return
        files, links = self._attached_files(), self._attached_links()
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText(tr("schedtask.ai_generating"))

        def job(worker: AgentWorker):
            from ..core.ai_task_planner import plan_tasks

            provider = self.ctx.build_active_provider()
            full_desc = description
            if files or links:
                attach_note = "; ".join(files + links)
                full_desc += f"\n\n(Attached references available: {attach_note})"
            planned = plan_tasks(provider, full_desc, cancel=worker.is_cancelled)
            # Attachments apply to every generated task so they're available
            # at RUN time too, not just visible to the planner.
            for t in planned:
                t["input"]["file_paths"] = list(files)
                t["input"]["links"] = list(links)
            return {"tasks": planned}

        w = AgentWorker(job)
        w.finished_ok.connect(self._on_planned)
        w.failed.connect(self._on_failed)
        self._worker = w
        w.start()

    def _on_planned(self, result: dict) -> None:
        self._worker = None
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText(tr("schedtask.ai_generate"))
        self._planned = result.get("tasks") or []
        lines = []
        for i, t in enumerate(self._planned, 1):
            sched = t.get("schedule", {})
            when = sched.get("run_at") if sched.get("enabled") else tr("schedtask.no_schedule")
            dep = t.get("dependency", {})
            chain = f"  ← {dep.get('previous_task_id', '')[:8]}" if dep.get("previous_task_id") else ""
            lines.append(f"{i}. [{t.get('task_type')}] {t.get('title')}\n"
                         f"    {when}   repeat={sched.get('repeat_type', 'none')}{chain}\n"
                         f"    {t.get('description', '')[:150]}")
        self.preview.setPlainText("\n\n".join(lines))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self._planned))

    def _on_failed(self, err: str) -> None:
        self._worker = None
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText(tr("schedtask.ai_generate"))
        self.preview.setPlainText(str(err))

    def _confirm(self) -> None:
        project_id = self.workspace_combo.currentData() or ""
        for t in self._planned:
            t["project_id"] = project_id
        self.created_tasks = self._planned
        self.accept()
