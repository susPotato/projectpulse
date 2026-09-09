"""The Excel source: which workbooks we watch, and what each sheet means.

The demo's two sheets are a built-in seed; anything registered since -
uploaded for a project via Settings > Sources - is layered on top of it and
persisted as JSON in `PULSE_STATE_DIR`, the same pattern `app.scope` uses for
the project pairing it names. It moved off the module-level constant this
docstring used to describe as the eventual step "when a PM can add their own
sheet" - that is now what an upload does.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.ingest.runner import register_source
from app.ingest.sources.excel.convertor import convert_scope, ensure_project
from app.ingest.sources.excel.ingest import ingest_sheet
from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT, WORKLOG_CONTRACT, SheetContract
from app.ingest.sources.excel.transport import (
    LocalFolderSource,
    SheetSource,
    WatchedSheet,
)

log = logging.getLogger(__name__)

SOURCE = "excel"

__all__ = [
    "SOURCE",
    "WATCHED",
    "WatchedSheet",
    "all_watched",
    "register_watched",
    "SHEET_KINDS",
    "run_excel_sync",
]

_LOCK = threading.Lock()
_FILENAME = "watched_sheets.json"

#: What an upload may say a sheet is. Keyed by the same short name the
#: settings-page form sends, valued by what `WatchedSheet` actually needs -
#: kept in one place so the two can never name the sheet differently or point
#: at the wrong contract.
SHEET_KINDS: dict[str, tuple[str, SheetContract]] = {
    "schedule": ("Activities", SCHEDULE_CONTRACT),
    "worklog": ("Worklog", WORKLOG_CONTRACT),
}

#: The demo's sheets. A registered entry with the same `file_name`
#: overrides one of these rather than duplicating it.
#:
#: SAIN and Example Project (`scripts/gen_portfolio_data.py`) are watched from
#: the same connection as HRMS on purpose: `ensure_project` gives every
#: project on one connection the same `excel:Program:1:DEFAULT` program row,
#: so these three land under one real `Program` - "Digital Transformation
#: 2026" - without any separate program-assignment step. That is the
#: multi-project portfolio the Program dashboard rolls up.
_SEED: tuple[WatchedSheet, ...] = (
    WatchedSheet(
        "hrms_schedule.xlsx", "Activities", SCHEDULE_CONTRACT, "excel:Project:1:HRMS"
    ),
    WatchedSheet(
        "hrms_worklog.xlsx", "Worklog", WORKLOG_CONTRACT, "excel:Project:1:HRMS"
    ),
    WatchedSheet(
        "sain_schedule.xlsx", "Activities", SCHEDULE_CONTRACT, "excel:Project:1:SAIN"
    ),
    WatchedSheet(
        "sain_worklog.xlsx", "Worklog", WORKLOG_CONTRACT, "excel:Project:1:SAIN"
    ),
    WatchedSheet(
        "example_project_schedule.xlsx", "Activities", SCHEDULE_CONTRACT,
        "excel:Project:1:EXPROJ",
    ),
    WatchedSheet(
        "example_project_worklog.xlsx", "Worklog", WORKLOG_CONTRACT,
        "excel:Project:1:EXPROJ",
    ),
)

#: Kept for the handful of call sites written before this became a registry -
#: the seed only. Prefer `all_watched()`, which also sees what was uploaded.
WATCHED = _SEED


def _state_path() -> Path:
    return Path(settings.state_dir) / _FILENAME


def _load_registered() -> list[WatchedSheet]:
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable watched-sheet registry at %s: %s", path, exc)
        return []

    if not isinstance(raw, list):
        return []

    out: list[WatchedSheet] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name") or "")
        kind = str(item.get("kind") or "")
        project_id = str(item.get("project_id") or "")
        spec = SHEET_KINDS.get(kind)
        if not file_name or not project_id or spec is None:
            continue
        sheet_name, contract = spec
        out.append(WatchedSheet(file_name, sheet_name, contract, project_id))
    return out


def all_watched() -> tuple[WatchedSheet, ...]:
    """Every sheet this source reads: the demo seed, plus anything uploaded
    since. Read fresh each call - a second worker process must see what the
    first one wrote."""
    merged: dict[str, WatchedSheet] = {w.file_name: w for w in _SEED}
    for entry in _load_registered():
        merged[entry.file_name] = entry
    return tuple(merged.values())


def register_watched(file_name: str, kind: str, project_id: str) -> WatchedSheet:
    """Start watching one more sheet, keyed by its logical file name.

    Called by `POST /api/sources/upload` after the bytes are written to
    `settings.data_root / file_name` - the same folder `LocalFolderSource`
    already reads, so nothing about the transport has to change for this
    sheet to start syncing on the next run.
    """
    spec = SHEET_KINDS.get(kind)
    if spec is None:
        raise ValueError(f"unknown sheet kind {kind!r}; expected one of {list(SHEET_KINDS)}")
    sheet_name, contract = spec
    entry = WatchedSheet(file_name, sheet_name, contract, project_id)

    with _LOCK:
        registered = {w.file_name: w for w in _load_registered()}
        registered[file_name] = entry
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {
                        "file_name": w.file_name,
                        "kind": next(k for k, (sn, _c) in SHEET_KINDS.items() if sn == w.sheet_name),
                        "project_id": w.project_id,
                    }
                    for w in registered.values()
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
    return entry


def _default_transport(session, data_root: Path | None) -> SheetSource:
    """`settings.excel_transport` picks between the demo route and the
    production one - see `app/config.py`."""
    if settings.excel_transport == "graph":
        from app.ingest.sources.excel.graph_source import GraphSheetSource

        return GraphSheetSource(session, folder=settings.onedrive_folder)
    return LocalFolderSource(data_root or settings.data_root)


@register_source(SOURCE)
def run_excel_sync(
    session,
    *,
    connection_id: int,
    now: datetime,
    sync_run_id: int | None = None,
    data_root: Path | None = None,
    sheet_source: SheetSource | None = None,
) -> dict:
    """Ingest every watched sheet that is present.

    A missing workbook is a note, not a failure: someone has not uploaded this
    week's file yet, which is normal and should not fail the other sheets.

    ``sheet_source`` is the only thing that changes between a laptop reading a
    synced folder and a server reading Microsoft Graph. Everything below this line
    is transport-independent by construction.
    """
    provider = sheet_source or _default_transport(session, data_root)
    totals = {"rows_ok": 0, "rows_rejected": 0, "changes_emitted": 0, "notes": []}

    for watched in all_watched():
        fetched = provider.fetch(watched)
        if fetched is None:
            totals["notes"].append(f"{watched.file_name}: not present, skipped")
            continue

        # Must exist before ingest_sheet: it queues Dependency rows that carry
        # a foreign key to this project, and ensure_project's own existence
        # check (session.get) autoflushes the session - if that check runs
        # after ingest_sheet, it flushes the pending Dependency inserts before
        # the Project row they reference has been added, and Postgres (unlike
        # SQLite, which the test suite runs on and never enforces this) rejects
        # the insert.
        ensure_project(
            session,
            connection_id=connection_id,
            project_id=watched.project_id,
            name=watched.project_id.split(":")[-1],
        )

        try:
            report = ingest_sheet(
                session,
                source=SOURCE,
                connection_id=connection_id,
                file_path=fetched.local_path,
                sheet_name=watched.sheet_name,
                contract=watched.contract,
                project_id=watched.project_id,
                now=now,
                sync_run_id=sync_run_id,
                # The stable half of the scope key. Never the local path - see
                # `transport.py` for what breaks if these are confused.
                logical_name=watched.file_name,
                display_uri=fetched.display_uri,
            )
        finally:
            # Always, so a remote source's temp copy does not outlive the scan.
            fetched.release()

        if report.skipped_unchanged:
            totals["notes"].append(f"{report.scope}: unchanged since last scan")
            continue

        if report.ok:
            # Tool rows -> domain rows. Without this the dependency edges join
            # task ids that have no dates on them, and the schedule engine has a
            # graph it cannot forward-pass.
            converted = convert_scope(
                session,
                connection_id=connection_id,
                scope=report.scope,
                project_id=watched.project_id,
                entity_type=watched.contract.entity_type,
            )
            report.rows_converted = converted

        if not report.ok:
            # Recorded and surfaced, but it must not take the other sheets down.
            totals["notes"].append(f"{report.scope}: REJECTED - {report.error}")
            totals["rows_rejected"] += report.rows_rejected
            continue

        totals["rows_ok"] += report.rows_ok
        totals["rows_rejected"] += report.rows_rejected
        totals["changes_emitted"] += report.changes_emitted

        detail = f"{report.scope}: {report.rows_ok} rows, {report.changes_emitted} changes"
        if report.deps_stated or report.deps_inferred:
            detail += (
                f", {report.deps_stated} stated deps"
                f" + {report.deps_inferred} inferred"
            )
        if report.low_confidence_changes:
            detail += f" ({report.low_confidence_changes} low-confidence)"
        if report.rows_rejected:
            detail += f", {report.rows_rejected} rejected"
        if report.unknown_headers:
            detail += f", unknown headers: {report.unknown_headers}"
        totals["notes"].append(detail)

    return totals
