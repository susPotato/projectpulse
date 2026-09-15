"""Cross-machine telemetry aggregation.

Every machine best-effort-mirrors its own local usage/audit events into a
shared folder (see ``usage_tracker.py``/``audit_log.py``'s ``set_identity``/
``_write_shared``) — one file PER MACHINE per day, so no two machines ever
write the same file. This module just globs + concatenates them; there is no
database and no Microsoft Graph API involved (Graph has no write access to an
arbitrary share link, only to the signed-in user's own drive — see
``config.py``'s ``auth.shared_dir`` docstring), so reading is plain file I/O
too.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_events(shared_dir: str, subdir: str, start: Optional[date],
                 end: Optional[date]) -> List[Dict[str, Any]]:
    directory = Path(shared_dir).expanduser() / "telemetry" / subdir
    if not directory.exists():
        return []
    events: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if start or end:
                    try:
                        day = datetime.fromisoformat(event.get("ts", "")).date()
                    except ValueError:
                        continue
                    if (start and day < start) or (end and day > end):
                        continue
                events.append(event)
        except (OSError, json.JSONDecodeError):
            continue
    return events


def load_shared_usage_events(shared_dir: str, start: Optional[date] = None,
                             end: Optional[date] = None) -> List[Dict[str, Any]]:
    """Every machine's usage events under ``<shared_dir>/telemetry/usage/``,
    concatenated — pass straight into ``usage_tracker.summarize()``/
    ``cost_usd()`` (they only aggregate, no identity awareness needed)."""
    return _load_events(shared_dir, "usage", start, end)


def load_shared_audit_events(shared_dir: str, start: Optional[date] = None,
                             end: Optional[date] = None,
                             kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every machine's audit events under ``<shared_dir>/telemetry/audit/``,
    concatenated, optionally filtered to one ``kind`` (mirrors
    ``audit_log.load_events``'s own filter)."""
    events = _load_events(shared_dir, "audit", start, end)
    if kind is not None:
        events = [e for e in events if e.get("kind") == kind]
    return events
