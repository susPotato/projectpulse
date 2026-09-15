"""Off/Auto/Manual routing toggle + Auto-run toggle + Manual-mode confirm dialog.

Dropped into every chat surface's composer (Cowork / Co4E / AI-Edit). By
default a :class:`RoutingToggle` reads/writes the **per-workspace** mode via
``AppContext.project_routing_mode`` / ``set_project_routing_mode`` (so each
workspace keeps its own mode), but the storage is fully injectable through
``get_mode``/``set_mode`` callables — all the real decision logic lives in
``core/routing``. Call :meth:`refresh` when the active workspace changes so the
control shows that workspace's mode.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QWidget,
)

from ..i18n import tr


class RoutingToggle(QWidget):
    """A small ``Routing: [Off ▾]`` control bound to one chat surface.

    Storage is injectable so the same widget can back a per-workspace mode, a
    global mode, or anything else:

    * ``get_mode()`` returns the current mode string to display.
    * ``set_mode(mode)`` persists a newly-chosen mode.

    When omitted, both default to the ACTIVE workspace's per-surface mode
    (``AppContext.project_routing_mode`` / ``set_project_routing_mode``).
    Emits :attr:`mode_changed`; call :meth:`refresh` after the workspace switches.
    """

    mode_changed = Signal(str)  # "off" | "auto" | "manual"

    def __init__(
        self,
        ctx: Any,
        surface: str,
        parent: Optional[QWidget] = None,
        *,
        get_mode: Optional[Callable[[], str]] = None,
        set_mode: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.surface = surface
        # Default to per-workspace storage (each workspace keeps its own mode).
        self._get_mode = get_mode or (lambda: ctx.project_routing_mode(surface))
        self._set_mode = set_mode or (lambda m: ctx.set_project_routing_mode(surface, m))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._label = QLabel(tr("routing.toggle_label"))
        self._label.setObjectName("hint")
        self._combo = QComboBox()
        self._combo.setToolTip(tr("routing.toggle_tooltip"))
        # (data value, i18n key) — data is the persisted mode string.
        self._modes = [
            ("off", "routing.mode_off"),
            ("auto", "routing.mode_auto"),
            ("manual", "routing.mode_manual"),
        ]
        for value, key in self._modes:
            self._combo.addItem(tr(key), value)

        self.refresh()  # reflect the current (per-workspace) mode

        self._combo.currentIndexChanged.connect(self._on_changed)
        lay.addWidget(self._label)
        lay.addWidget(self._combo)

    def current_mode(self) -> str:
        return self._combo.currentData() or "off"

    def refresh(self) -> None:
        """Re-read the backing mode (e.g. after switching workspace) and show it
        without emitting a spurious change."""
        try:
            mode = self._get_mode() or "off"
        except Exception:  # noqa: BLE001
            mode = "off"
        idx = self._combo.findData(mode)
        if idx < 0:
            idx = 0
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)

    def retranslate(self) -> None:
        """Re-apply labels after a language change."""
        self._label.setText(tr("routing.toggle_label"))
        self._combo.setToolTip(tr("routing.toggle_tooltip"))
        for i, (value, key) in enumerate(self._modes):
            self._combo.setItemText(i, tr(key))

    def _on_changed(self, _idx: int) -> None:
        mode = self.current_mode()
        try:
            self._set_mode(mode)
        except Exception:  # noqa: BLE001 — never let a toggle change crash the UI
            pass
        self.mode_changed.emit(mode)


class AutoRunToggle(QWidget):
    """A checkbox that auto-approves commands for the ACTIVE workspace.

    Checked → commands run without a confirm dialog (auto-approve) in this
    workspace; unchecked → the Approve/Reject dialog is shown. Backed by the
    per-workspace ``auto_run`` override (``AppContext.set_project_auto_run``),
    falling back to the global ``cowork_confirm_commands`` when unset.
    """

    toggled_auto = Signal(bool)

    def __init__(self, ctx: Any, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._chk = QCheckBox(tr("routing.autorun_label"))
        self._chk.setToolTip(tr("routing.autorun_tooltip"))
        self.refresh()
        self._chk.toggled.connect(self._on_toggled)
        lay.addWidget(self._chk)

    def refresh(self) -> None:
        try:
            auto = bool(self.ctx.project_auto_run())
        except Exception:  # noqa: BLE001
            auto = False
        self._chk.blockSignals(True)
        self._chk.setChecked(auto)
        self._chk.blockSignals(False)

    def retranslate(self) -> None:
        self._chk.setText(tr("routing.autorun_label"))
        self._chk.setToolTip(tr("routing.autorun_tooltip"))

    def _on_toggled(self, checked: bool) -> None:
        try:
            self.ctx.set_project_auto_run(bool(checked))
        except Exception:  # noqa: BLE001
            pass
        self.toggled_auto.emit(bool(checked))


def confirm_switch(parent: QWidget, decision: Any, timeout_sec: float) -> bool:
    """Modal Manual-mode confirm: ask before switching, auto-keep on timeout.

    Returns True if the user approved the switch; False if they declined or the
    ``timeout_sec`` window elapsed (→ keep the current model, per spec). The
    "Keep current" button shows a live countdown so the timeout is visible.
    """
    from ..core.routing.models import split_key

    from_id = split_key(decision.from_model)[1] if decision.from_model else "—"
    to_id = split_key(decision.to_model)[1] if decision.to_model else "—"

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(tr("routing.confirm_title"))
    box.setText(tr(
        "routing.confirm_body",
        task=decision.task_type or "?",
        from_model=from_id,
        to_model=to_id,
        gain=f"{decision.score_gain:.2f}",
        reason=decision.reason,
    ))
    yes_btn = box.addButton(tr("routing.confirm_yes"), QMessageBox.AcceptRole)
    no_btn = box.addButton(tr("routing.confirm_no"), QMessageBox.RejectRole)
    box.setDefaultButton(no_btn)

    # Countdown that auto-declines (keep current) when the window elapses.
    remaining = {"secs": int(max(1, round(timeout_sec)))}
    timer = QTimer(box)
    timer.setInterval(1000)

    def _tick() -> None:
        remaining["secs"] -= 1
        if remaining["secs"] <= 0:
            timer.stop()
            box.done(QMessageBox.RejectRole)  # timeout → keep current
        else:
            no_btn.setText(tr("routing.confirm_countdown", secs=remaining["secs"]))

    no_btn.setText(tr("routing.confirm_countdown", secs=remaining["secs"]))
    timer.timeout.connect(_tick)
    if timeout_sec > 0:
        timer.start()

    box.exec()
    timer.stop()
    return box.clickedButton() is yes_btn


__all__ = ["RoutingToggle", "AutoRunToggle", "confirm_switch"]
