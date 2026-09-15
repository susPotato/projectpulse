"""Reusable widgets: a collapsible list section, a plan checklist, a thin
collapse strip, and a scroll-wheel guard for value widgets in scrollable
forms."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QDoubleSpinBox, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from ..core.flows import STEP_DONE, STEP_ERROR, STEP_PENDING, STEP_RUNNING
from ..theme import ACCENT
from .icons import DOT_BLUE, DOT_GREEN, DOT_GREY, DOT_RED, dot_icon, icon


class StatCard(QFrame):
    """A titled value card (e.g. token count + its cost as the subtitle) —
    shared by Dashboard and Monitoring's token/cost displays."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid rgba(140,146,152,0.35); border-radius: 16px; }")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)
        lay = QVBoxLayout(self)
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("hint")
        self.title_lbl.setStyleSheet("border: none;")
        self.value_lbl = QLabel("—")
        self.value_lbl.setStyleSheet("border: none; font-size: 20px; font-weight: 700;")
        self.sub_lbl = QLabel("")
        self.sub_lbl.setObjectName("hint")
        self.sub_lbl.setStyleSheet("border: none;")
        # Word-wrap the sub-label: a long note (e.g. the "estimated tokens"
        # sentence on the cost card) would otherwise report a single-line
        # sizeHint hundreds of px wide, forcing its WHOLE grid column open and
        # throwing every card in the row out of alignment.
        self.sub_lbl.setWordWrap(True)
        lay.addWidget(self.title_lbl)
        lay.addWidget(self.value_lbl)
        lay.addWidget(self.sub_lbl)

    def set(self, title: str, value: str, sub: str = "") -> None:
        self.title_lbl.setText(title)
        self.value_lbl.setText(value)
        self.sub_lbl.setText(sub)


class BudgetCard(QFrame):
    """Remaining/Budget box — same card chrome as :class:`StatCard`, plus a
    direct budget-entry field. The card is a dumb display: the owning tab
    (Dashboard/Monitoring, both share the same ``usage.budget_*`` config) wires
    ``apply_btn.clicked`` to persist a new budget and refresh, and calls
    :meth:`set` with pre-formatted text + whether to render in the ⚠ warn color
    (the app turns the remaining balance red past 85% budget used)."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { border: 1px solid rgba(140,146,152,0.35); border-radius: 16px; }")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(shadow)
        lay = QVBoxLayout(self)
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("hint")
        self.title_lbl.setStyleSheet("border: none;")
        self.value_lbl = QLabel("—")
        self._value_style = "border: none; font-size: 20px; font-weight: 700;"
        self.value_lbl.setStyleSheet(self._value_style)
        self.sub_lbl = QLabel("")
        self.sub_lbl.setObjectName("hint")
        self.sub_lbl.setStyleSheet("border: none;")
        # Word-wrap the sub-label: a long note (e.g. the "estimated tokens"
        # sentence on the cost card) would otherwise report a single-line
        # sizeHint hundreds of px wide, forcing its WHOLE grid column open and
        # throwing every card in the row out of alignment.
        self.sub_lbl.setWordWrap(True)
        lay.addWidget(self.title_lbl)
        lay.addWidget(self.value_lbl)
        lay.addWidget(self.sub_lbl)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.budget_spin = QDoubleSpinBox()
        self.budget_spin.setRange(0, 100_000_000)
        self.budget_spin.setDecimals(2)
        self.budget_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.apply_btn = QPushButton()
        self.apply_btn.setFixedWidth(30)
        row.addWidget(self.budget_spin, 1)
        row.addWidget(self.apply_btn)
        lay.addLayout(row)

    def set(self, title: str, value: str, sub: str, warn: bool = False) -> None:
        self.title_lbl.setText(title)
        self.value_lbl.setText(value)
        self.value_lbl.setStyleSheet(
            self._value_style + (" color: #E5484D;" if warn else ""))
        self.sub_lbl.setText(sub)


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1e6:.2f}M"
    if n >= 1_000:
        return f"{n / 1e3:.1f}K"
    return str(n)


class _WheelGuard(QObject):
    """Swallows wheel events on a value widget unless the user has clicked
    into it first (i.e. it has keyboard focus). Without this, scrolling a
    Settings/task-editor form accidentally spins whatever combo box or
    spin box the cursor happens to pass over, silently changing values."""

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            event.ignore()
            return True   # eat it → the scroll area scrolls instead
        return False


_wheel_guard = _WheelGuard()


def guard_wheel(root: QWidget) -> None:
    """Protect every QComboBox / spin box / date-time edit under ``root``:
    the mouse wheel only changes their value after an explicit click into
    the widget (StrongFocus excludes wheel-acquired focus), otherwise the
    wheel scrolls the surrounding form like the user expects."""
    targets = root.findChildren(QComboBox) + root.findChildren(QAbstractSpinBox)
    for w in targets:
        w.setFocusPolicy(Qt.StrongFocus)
        w.installEventFilter(_wheel_guard)


class CollapseStrip(QWidget):
    """The slim bar shown in place of a collapsed side panel.

    It draws a clear chevron (▸ / ◂) near the top — the expand affordance — over
    a thin handle line, and the whole strip is clickable to expand the panel."""

    clicked = Signal()
    WIDTH = 18  # click target width; wide enough to show the expand arrow

    def __init__(self, tooltip: str = "Click to expand", expand_dir: str = "right"):
        super().__init__()
        self._hover = False
        self._dir = "left" if expand_dir == "left" else "right"
        self.setFixedWidth(self.WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)

    def enterEvent(self, e) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        accent = QColor(ACCENT) if self._hover else QColor("#8b8d98")

        # A small rounded "button" at the top carries the expand arrow so the
        # collapsed panel always shows a clear, clickable affordance.
        bw = min(w - 2.0, 16.0)
        btn = QRectF((w - bw) / 2.0, 6.0, bw, 18.0)
        p.setPen(QPen(QColor(139, 144, 150, 130), 1.0))
        p.setBrush(QColor(155, 160, 166, 70) if self._hover else QColor(155, 160, 166, 32))
        p.drawRoundedRect(btn, 4.0, 4.0)

        cx = w / 2.0
        cy = btn.center().y()
        s = 4.0
        pen = QPen(accent, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if self._dir == "right":   # '›' — expands content to the right
            tip_x, base_x = cx + s / 2.0, cx - s / 2.0
        else:                       # '‹' — expands content to the left
            tip_x, base_x = cx - s / 2.0, cx + s / 2.0
        p.drawLine(QPointF(base_x, cy - s), QPointF(tip_x, cy))
        p.drawLine(QPointF(tip_x, cy), QPointF(base_x, cy + s))

        # thin handle line below the button
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(155, 160, 166, 90))
        line_w = 2.0
        x = (w - line_w) / 2.0
        ltop = btn.bottom() + 6.0
        lbottom = max(ltop, self.height() - 10.0)
        p.drawRoundedRect(QRectF(x, ltop, line_w, lbottom - ltop), 1.0, 1.0)
        p.end()


class PlanSection(QWidget):
    """A collapsible checklist of the current message's plan steps with live
    line-icon status markers (pending dot · running play · done check · error
    close). Hidden until it has steps; updated in place as the agent calls
    ``update_plan``."""

    _COLORS = {STEP_RUNNING: ACCENT, STEP_DONE: "#6fe3a4", STEP_ERROR: "#ef6368"}

    @staticmethod
    def _step_icon(status: str):
        if status == STEP_RUNNING:
            return icon("play", color=DOT_BLUE)
        if status == STEP_DONE:
            return icon("check", color=DOT_GREEN)
        if status == STEP_ERROR:
            return icon("close", color=DOT_RED)
        return dot_icon(DOT_GREY)   # pending

    def __init__(self, title: str = "Plan", max_height: int = 150):
        super().__init__()
        self._title = title
        self._count = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setStyleSheet("text-align:left; font-weight:600;")
        self.header.toggled.connect(self._toggle)
        lay.addWidget(self.header)

        self.list = QListWidget()
        self.list.setMaximumHeight(max_height)   # scrolls when longer
        lay.addWidget(self.list)

        self.setVisible(False)
        self._update_header()

    def set_steps(self, steps) -> None:
        """Replace the checklist with ``[{title, status}]`` (the agent sends the FULL
        list each update, so we rebuild in place)."""
        self.list.clear()
        self._count = 0
        for s in steps or []:
            title = str((s or {}).get("title", "")).strip()
            if not title:
                continue
            status = str((s or {}).get("status", STEP_PENDING)).strip().lower()
            item = QListWidgetItem(self._step_icon(status), f"  {title}")
            color = self._COLORS.get(status)
            if color:
                item.setForeground(QColor(color))
            self.list.addItem(item)
            self._count += 1
        self.setVisible(self._count > 0)
        if self._count and not self.header.isChecked():
            self.header.setChecked(True)
        self.list.setVisible(self.header.isChecked())
        self._update_header()

    def clear(self) -> None:
        self.list.clear()
        self._count = 0
        self.setVisible(False)
        self._update_header()

    def set_title(self, title: str) -> None:
        """Update the header label (for live language switching)."""
        self._title = title
        self._update_header()

    def _toggle(self, on: bool) -> None:
        self.list.setVisible(on)
        self._update_header()

    def _update_header(self) -> None:
        arrow = "▾" if self.header.isChecked() else "▸"
        self.header.setText(f"{arrow} {self._title} ({self._count})")


class CollapsibleSection(QWidget):
    """A pull-down header + a scrollable list. Hidden until it has items."""

    activated = Signal(str)  # emits the path of a clicked item

    def __init__(self, title: str, max_height: int = 130):
        super().__init__()
        self._title = title
        self._paths: list[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.header = QPushButton()
        self.header.setCheckable(True)
        self.header.setChecked(False)
        self.header.setStyleSheet("text-align:left; font-weight:600;")
        self.header.toggled.connect(self._toggle)
        lay.addWidget(self.header)

        self.list = QListWidget()
        self.list.setMaximumHeight(max_height)   # scrolls when longer
        self.list.setVisible(False)
        self.list.itemActivated.connect(self._emit)
        self.list.itemClicked.connect(self._emit)
        lay.addWidget(self.list)

        self.setVisible(False)
        self._update_header()

    def add(self, path: str) -> None:
        if not path or path in self._paths:
            return
        self._paths.append(path)
        item = QListWidgetItem(Path(path).name)
        item.setData(Qt.UserRole, path)
        item.setToolTip(path)
        self.list.addItem(item)
        self.setVisible(True)
        # Auto-open so added / restored files are visible without a click.
        if not self.header.isChecked():
            self.header.setChecked(True)
        self._update_header()

    def remove(self, path: str) -> None:
        if path not in self._paths:
            return
        i = self._paths.index(path)
        self._paths.pop(i)
        item = self.list.takeItem(i)
        del item
        self.setVisible(bool(self._paths))
        self._update_header()

    def paths(self) -> list[str]:
        return list(self._paths)

    def clear(self) -> None:
        self._paths.clear()
        self.list.clear()
        self.setVisible(False)
        self._update_header()

    def set_title(self, title: str) -> None:
        """Update the header label (for live language switching)."""
        self._title = title
        self._update_header()

    def _toggle(self, on: bool) -> None:
        self.list.setVisible(on)
        self._update_header()

    def _update_header(self) -> None:
        arrow = "▾" if self.header.isChecked() else "▸"
        self.header.setText(f"{arrow} {self._title} ({len(self._paths)})")

    def _emit(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.activated.emit(path)
