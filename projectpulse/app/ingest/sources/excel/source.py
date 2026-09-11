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
    StoredSheetSource,
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
    """Uploaded sheets, from the database, or nothing if they cannot be read.

    In the database rather than a JSON file under `PULSE_STATE_DIR`, for the
    reason `app/models/uploads.py` spells out: a container's disk does not
    survive a deploy, so an imported sheet silently stopped being watched on
    the next release. Every failure collapses to "none registered" - the same
    answer a missing file gave, and it leaves the demo seed working.
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models.uploads import UploadedSheet

    try:
        with session_scope() as session:
            rows = session.execute(
                select(
                    UploadedSheet.file_name,
                    UploadedSheet.kind,
                    UploadedSheet.project_id,
                    UploadedSheet.sheet_name,
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("ignoring unreadable watched-sheet registry: %s", exc)
        return []

    out: list[WatchedSheet] = []
    for file_name, kind, project_id, sheet_name in rows:
        spec = SHEET_KINDS.get(kind)
        if not file_name or not project_id or spec is None:
            continue
        conventional, contract = spec
        # The tab this workbook actually keeps the table on, resolved once when
        # the sheet was imported. Absent, the conventional name is what it
        # meant - which is what our own generated workbooks use.
        out.append(
            WatchedSheet(file_name, sheet_name or conventional, contract, project_id)
        )
    return out


def all_watched() -> tuple[WatchedSheet, ...]:
    """Every sheet this source reads: the demo seed, plus anything uploaded
    since. Read fresh each call - a second worker process must see what the
    first one wrote."""
    merged: dict[str, WatchedSheet] = {w.file_name: w for w in _SEED}
    for entry in _load_registered():
        merged[entry.file_name] = entry
    return tuple(merged.values())


def register_watched(
    file_name: str,
    kind: str,
    project_id: str,
    sheet_name: str | None = None,
    content: bytes | None = None,
    original_filename: str | None = None,
) -> WatchedSheet:
    """Store one imported workbook and start watching it.

    Called by `POST /api/sources/upload`. The bytes go in the same row as the
    registration, so the two can never disagree - a sheet we are watching and
    whose document is missing is the broken state this replaces, and it was
    the ordinary state on a deployed host, where both the file and the old
    JSON registry lived on a disk that a deploy throws away.

    `content` is optional only so a caller that already has the bytes on disk
    (the demo seed's own files, which are generated rather than uploaded) can
    register without duplicating them into the database. An upload always
    passes them.

    `sheet_name` is the tab the table is actually on, resolved from the
    workbook's contents by `reader.find_sheet`. It defaults to the
    conventional name for the kind, which is what our own generated workbooks
    use. ⚠️ It is **half the scope key**, so it is stored here and never
    re-derived per scan - see `find_sheet`'s own warning.
    """
    from app.db import session_scope
    from app.models.uploads import UploadedSheet

    spec = SHEET_KINDS.get(kind)
    if spec is None:
        raise ValueError(f"unknown sheet kind {kind!r}; expected one of {list(SHEET_KINDS)}")
    conventional, contract = spec
    entry = WatchedSheet(file_name, sheet_name or conventional, contract, project_id)

    with session_scope() as session:
        values = {
            "file_name": file_name,
            "kind": kind,
            "project_id": project_id,
            "sheet_name": entry.sheet_name,
        }
        if content is not None:
            values["content"] = content
            values["original_filename"] = original_filename
        else:
            # Re-registering without new bytes: keep whatever is stored, and
            # require *something* - a registration with no document behind it
            # is the state this row shape exists to make impossible.
            existing = session.get(UploadedSheet, file_name)
            if existing is None:
                raise ValueError(
                    f"no stored workbook for {file_name!r}; pass content on first register"
                )
            values["content"] = existing.content
            values["original_filename"] = existing.original_filename
        session.merge(UploadedSheet(**values))

    return entry


def _default_transport(session, data_root: Path | None) -> SheetSource:
    """`settings.excel_transport` picks between the demo route and the
    production one - see `app/config.py`.

    The local route is a **stored-then-folder** pair rather than a folder
    alone: an imported document lives in the database (so it survives a
    deploy) and the demo's generated sheets live in `data_root` (so they are
    never copied into a table they can be regenerated from). Neither knows
    about the other; `StoredSheetSource` just asks the folder for anything it
    does not hold.
    """
    if settings.excel_transport == "graph":
        from app.ingest.sources.excel.graph_source import GraphSheetSource

        return GraphSheetSource(session, folder=settings.onedrive_folder)
    return StoredSheetSource(LocalFolderSource(data_root or settings.data_root))


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
