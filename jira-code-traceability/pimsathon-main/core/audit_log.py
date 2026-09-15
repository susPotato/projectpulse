"""Centralized audit log — the single source of truth behind the Monitoring
Dashboard's "Security Events", "MCP Call History", and "Action Logs" panels
(each is just a filtered VIEW of this one log by ``kind``, not 3 separate
storage systems).

One JSON line per event, one file per day under ``~/.cowork_local/audit/`` —
same on-disk shape as ``usage_tracker.py`` (day-sharded ``.jsonl``, append-only,
``record()`` never raises so audit logging can never break a chat turn).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import CONFIG_DIR

AUDIT_DIR = CONFIG_DIR / "audit"

# One of: "tool_call" (a built-in file/command tool ran), "permission" (a
# PermissionGate decision), "security_block" (Agent Security refused an
# action), "mcp_call" (a call to an external MCP server's tool).
Kind = str

# Process-global identity — who's logged in, their role, and this machine's
# name — set once right after login (app.py::run()), mirroring
# usage_tracker.py's identical pattern. NOT thread-local: fixed per process.
_identity_account = ""
_identity_role = ""
_identity_machine = ""
_identity_shared_dir = ""


def set_identity(account: str, machine: str, role: str = "", shared_dir: str = "") -> None:
    """Called once after login succeeds. ``shared_dir``, when reachable,
    makes every subsequent :func:`record` ALSO best-effort-append to the
    shared cross-machine telemetry store (see :mod:`telemetry_shared`)."""
    global _identity_account, _identity_role, _identity_machine, _identity_shared_dir
    _identity_account = account or ""
    _identity_role = role or ""
    _identity_machine = machine or ""
    _identity_shared_dir = shared_dir or ""


def record(kind: Kind, name: str, ok: bool, detail: str = "",
          agent_role: str = "") -> None:
    """Append one audit event. Never raises — audit logging must never break
    a chat turn, a permission decision, or a tool call."""
    try:
        now = datetime.now()
        event = {
            "ts": now.isoformat(timespec="seconds"),
            "kind": kind,
            "agent_role": agent_role or "",
            "name": name or "",
            "ok": bool(ok),
            "detail": (detail or "")[:2000],   # bounded — never let a huge blob bloat the log
            "account": _identity_account,
            "role": _identity_role,
            "machine": _identity_machine,
        }
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        path = AUDIT_DIR / f"{now.strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        _write_shared(event, now)
    except Exception:  # noqa: BLE001
        pass


def _write_shared(event: Dict[str, Any], now: datetime) -> None:
    """Best-effort mirror of ``event`` into the shared cross-machine store —
    one file PER MACHINE per day, so no two machines ever write the same
    file. Never raises."""
    if not _identity_shared_dir or not _identity_machine:
        return
    try:
        shared = Path(_identity_shared_dir).expanduser() / "telemetry" / "audit"
        shared.mkdir(parents=True, exist_ok=True)
        path = shared / f"{_identity_machine}-{now.strftime('%Y-%m-%d')}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def load_events(start: Optional[date] = None, end: Optional[date] = None,
                kind: Optional[Kind] = None,
                directory: Path = None) -> List[Dict[str, Any]]:
    """Events between ``start``/``end`` (inclusive; None = unbounded),
    optionally filtered to one ``kind`` — this IS how each Monitoring
    Dashboard panel gets its own slice of the same underlying log."""
    directory = directory or AUDIT_DIR
    if not directory.exists():
        return []
    events: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (start and day < start) or (end and day > end):
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if kind is not None and event.get("kind") != kind:
                    continue
                events.append(event)
        except (OSError, json.JSONDecodeError):
            continue
    return events
