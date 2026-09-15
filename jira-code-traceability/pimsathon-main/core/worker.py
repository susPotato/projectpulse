"""Background worker (QThread) that runs an agent job off the UI thread.

Each chat tab owns its own worker, so the Cowork and Code tabs (and any number
of tabs) run concurrently — true multitasking. All UI updates happen via Qt
signals, which are delivered to the main thread as queued connections.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QThread, Signal

from .permissions import PermissionGate

# A job receives the worker and returns a result dict (or None).
Job = Callable[["AgentWorker"], Optional[Dict[str, Any]]]


class AgentWorker(QThread):
    event = Signal(dict)                 # streaming/agent events
    permission_requested = Signal(dict)  # confirm-mode tool action awaiting approval
    finished_ok = Signal(dict)           # job completed
    failed = Signal(str)                 # job raised

    def __init__(self, job: Job, parent=None):
        super().__init__(parent)
        self._job = job
        self.stop_event = threading.Event()  # public for provider Event.wait() — immediate Stop
        self.gate: Optional[PermissionGate] = None

    # -- helpers used from inside the job (worker thread) --------------
    def is_cancelled(self) -> bool:
        return self.stop_event.is_set()

    def emit_event(self, ev: Dict[str, Any]) -> None:
        self.event.emit(ev)

    def new_gate(self, mode: str, agent_role: str = "") -> PermissionGate:
        self.gate = PermissionGate(
            mode, on_request=lambda action: self.permission_requested.emit(action),
            agent_role=agent_role,
        )
        return self.gate

    # -- control from the UI thread -----------------------------------
    def request_stop(self) -> None:
        self.stop_event.set()
        if self.gate:
            self.gate.cancel()

    def resolve_permission(self, approved: bool) -> None:
        if self.gate:
            self.gate.resolve(approved)

    # -- thread body ---------------------------------------------------
    def run(self) -> None:  # noqa: D401
        try:
            result = self._job(self)
            self.finished_ok.emit(result or {})
        except Exception as exc:  # surface any failure to the UI
            self.failed.emit(str(exc))
