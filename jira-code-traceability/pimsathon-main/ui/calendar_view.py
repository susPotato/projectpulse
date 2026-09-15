"""Calendar view for Schedule Task — an alternative to the Kanban board:
Week / Month / Year granularity, each task placed on its scheduled date
(``schedule.run_at``). Click a task to edit it (same editor the Kanban
board's double-click opens); click a day's "+" to create a task pre-filled
with that date. All grid/date math lives in ``core/calendar_grid.py`` (no Qt,
directly unit-testable) — this module is just the Qt rendering of it.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core.calendar_grid import (
    GRANULARITIES, group_tasks_by_date, month_grid, month_task_counts, shift_period, week_days,
)
from ..i18n import on_language_changed, tr
from .icons import icon

_WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class _DayCell(QFrame):
    add_requested = Signal(str)   # "YYYY-MM-DD"
    task_clicked = Signal(str)    # task_id

    def __init__(self):
        super().__init__()
        self.setObjectName("dayCell")
        self.setFrameShape(QFrame.StyledPanel)
        self._date_str = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        head = QHBoxLayout()
        self.date_lbl = QLabel()
        self.add_btn = QPushButton("+")
        self.add_btn.setFixedSize(20, 20)
        self.add_btn.clicked.connect(lambda: self.add_requested.emit(self._date_str))
        head.addWidget(self.date_lbl, 1)
        head.addWidget(self.add_btn)
        lay.addLayout(head)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        # Transparent so the cell's today/weekend tint shows through the task area.
        self.list.setStyleSheet("background: transparent;")
        self.list.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.list, 1)

    def set_day(self, d: date, tasks: List[dict], dim: bool,
                today: bool = False, weekend: bool = False) -> None:
        self._date_str = d.isoformat()
        self.date_lbl.setText(str(d.day))
        num_color = "#0096C7" if today else ("#888" if dim else "")
        self.date_lbl.setStyleSheet(f"font-weight:700; color:{num_color};")
        # Today = accent border + stronger tint; weekend (Sat/Sun) = a subtle
        # darker-blue tint than the base cell. rgba overlays read correctly on
        # both light and dark themes.
        base_border = "1px solid rgba(128,128,128,0.35)"
        if today:
            css = ("#dayCell { background: rgba(0,150,199,0.22); "
                   "border: 2px solid #0096C7; border-radius: 6px; }")
        elif weekend:
            css = ("#dayCell { background: rgba(0,120,182,0.13); "
                   f"border: {base_border}; border-radius: 6px; }}")
        else:
            css = f"#dayCell {{ border: {base_border}; border-radius: 6px; }}"
        self.setStyleSheet(css)
        self.list.clear()
        for t in tasks:
            item = QListWidgetItem(t.get("title") or tr("schedtask.no_title"))
            item.setData(Qt.UserRole, t.get("task_id"))
            self.list.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.UserRole)
        if tid:
            self.task_clicked.emit(tid)


class CalendarView(QWidget):
    add_task_on_date = Signal(str)   # "YYYY-MM-DD"
    edit_task = Signal(str)          # task_id

    def __init__(self):
        super().__init__()
        self.granularity = "month"
        self.anchor = date.today()
        self._tasks: List[dict] = []

        root = QVBoxLayout(self)
        head = QHBoxLayout()
        self.prev_btn = QPushButton()
        self.prev_btn.setIcon(icon("chevron-left"))
        self.prev_btn.clicked.connect(lambda: self._shift(-1))
        self.today_btn = QPushButton()
        self.today_btn.clicked.connect(self._go_today)
        self.next_btn = QPushButton()
        self.next_btn.setIcon(icon("chevron-right"))
        self.next_btn.clicked.connect(lambda: self._shift(1))
        self.period_lbl = QLabel()
        self.period_lbl.setStyleSheet("font-weight:700;")
        self.granularity_combo = QComboBox()
        for g in GRANULARITIES:
            self.granularity_combo.addItem("", g)
        self.granularity_combo.currentIndexChanged.connect(self._on_granularity_changed)
        head.addWidget(self.prev_btn)
        head.addWidget(self.today_btn)
        head.addWidget(self.next_btn)
        head.addWidget(self.period_lbl, 1)
        head.addWidget(self.granularity_combo)
        root.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(4)
        scroll.setWidget(self._grid_host)
        root.addWidget(scroll, 1)

        on_language_changed(self._retranslate)
        self._retranslate()

    def _retranslate(self) -> None:
        self.today_btn.setText(tr("schedtask.cal_today"))
        self.prev_btn.setToolTip(tr("schedtask.cal_prev"))
        self.next_btn.setToolTip(tr("schedtask.cal_next"))
        for i, g in enumerate(GRANULARITIES):
            self.granularity_combo.setItemText(i, tr(f"schedtask.cal_gran.{g}"))
        self._render()

    # ---- public ------------------------------------------------------
    def set_tasks(self, tasks: List[dict]) -> None:
        self._tasks = tasks
        self._render()

    def show_month(self, year: int, month: int) -> None:
        """Switch to Month view centered on (year, month) — used when the
        user drills down from a Year-view row."""
        self.anchor = date(year, month, 1)
        self.granularity = "month"
        idx = self.granularity_combo.findData("month")
        if idx >= 0:
            self.granularity_combo.blockSignals(True)
            self.granularity_combo.setCurrentIndex(idx)
            self.granularity_combo.blockSignals(False)
        self._render()

    # ---- navigation ---------------------------------------------------
    def _shift(self, direction: int) -> None:
        self.anchor = shift_period(self.anchor, self.granularity, direction)
        self._render()

    def _go_today(self) -> None:
        self.anchor = date.today()
        self._render()

    def _on_granularity_changed(self) -> None:
        data = self.granularity_combo.currentData()
        if data:
            self.granularity = data
        self._render()

    # ---- rendering ------------------------------------------------------
    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render(self) -> None:
        self._update_period_label()
        self._clear_grid()
        by_date = group_tasks_by_date(self._tasks)
        if self.granularity == "week":
            self._render_days(week_days(self.anchor), by_date)
        elif self.granularity == "year":
            self._render_year(by_date)
        else:
            self._render_days(sum(month_grid(self.anchor), []), by_date, mark_month=self.anchor.month)

    def _render_days(self, days: List[date], by_date: Dict[str, List[dict]],
                     mark_month: Optional[int] = None) -> None:
        for col, key in enumerate(_WEEKDAY_KEYS):
            lbl = QLabel(tr(f"schedtask.cal_weekday.{key}"))
            lbl.setStyleSheet("font-weight:600;")
            lbl.setAlignment(Qt.AlignCenter)
            self._grid.addWidget(lbl, 0, col)
        today = date.today()
        rows = [days[i:i + 7] for i in range(0, len(days), 7)]
        for r, week in enumerate(rows, start=1):
            for c, d in enumerate(week):
                cell = _DayCell()
                dim = mark_month is not None and d.month != mark_month
                # _WEEKDAY_KEYS is Mon..Sun → columns 5 (Sat) and 6 (Sun) are the weekend.
                cell.set_day(d, by_date.get(d.isoformat(), []), dim,
                             today=(d == today), weekend=(c in (5, 6)))
                cell.add_requested.connect(self.add_task_on_date.emit)
                cell.task_clicked.connect(self.edit_task.emit)
                self._grid.addWidget(cell, r, c)

    def _render_year(self, by_date: Dict[str, List[dict]]) -> None:
        counts = month_task_counts(by_date, self.anchor.year)
        lst = QListWidget()
        for m in range(1, 13):
            label = date(self.anchor.year, m, 1).strftime("%B")
            n = counts[m]
            text = tr("schedtask.cal_month_count", month=label, n=n) if n else label
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, m)
            lst.addItem(item)
        lst.itemClicked.connect(lambda item: self.show_month(self.anchor.year, item.data(Qt.UserRole)))
        self._grid.addWidget(lst, 0, 0)

    def _update_period_label(self) -> None:
        if self.granularity == "week":
            days = week_days(self.anchor)
            self.period_lbl.setText(f"{days[0].isoformat()} - {days[-1].isoformat()}")
        elif self.granularity == "year":
            self.period_lbl.setText(str(self.anchor.year))
        else:
            self.period_lbl.setText(self.anchor.strftime("%Y-%m"))
