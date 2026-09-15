"""Permission gate for the Code agent.

In ``auto`` mode every action is approved immediately. In ``confirm`` mode the
agent thread blocks on a threading event while the UI shows a preview dialog and
the user approves or rejects. Cancelling the task releases any pending wait.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

RequestFn = Callable[[Dict[str, Any]], None]


class PermissionGate:
    def __init__(self, mode: str = "confirm", on_request: Optional[RequestFn] = None,
                agent_role: str = ""):
        self.mode = mode
        self.on_request = on_request
        self.agent_role = agent_role
        self._event = threading.Event()
        self._approved = False

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def request(self, action: Dict[str, Any]) -> bool:
        """Block (in confirm mode) until the action is approved or rejected."""
        from . import audit_log

        if self.mode == "auto":
            audit_log.record("permission", str(action.get("name", "")), True,
                             "auto mode", agent_role=self.agent_role)
            return True
        self._approved = False
        self._event.clear()
        if self.on_request:
            self.on_request(action)
        self._event.wait()
        audit_log.record("permission", str(action.get("name", "")), self._approved,
                         "user approved" if self._approved else "user rejected",
                         agent_role=self.agent_role)
        return self._approved

    def resolve(self, approved: bool) -> None:
        self._approved = approved
        self._event.set()

    def cancel(self) -> None:
        """Unblock any pending request, treating it as rejected."""
        self._approved = False
        self._event.set()
