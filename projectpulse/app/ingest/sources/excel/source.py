"""The Excel source: which workbooks we watch, and what each sheet means.

Scope configuration is declarative and lives here rather than in a database
table, because for now the set of watched sheets changes with the code that
understands them. When a PM can add their own sheet, this moves to a table and
the shape stays the same.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.ingest.runner import register_source
from app.ingest.sources.excel.convertor import convert_scope, ensure_project
from app.ingest.sources.excel.ingest import ingest_sheet
from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT, WORKLOG_CONTRACT
from app.ingest.sources.excel.transport import (
    LocalFolderSource,
    SheetSource,
    WatchedSheet,
)

log = logging.getLogger(__name__)

SOURCE = "excel"

__all__ = ["SOURCE", "WATCHED", "WatchedSheet", "run_excel_sync"]


#: The demo portfolio. A real deployment reads this from scope config.
WATCHED: tuple[WatchedSheet, ...] = (
    WatchedSheet(
        "hrms_schedule.xlsx", "Activities", SCHEDULE_CONTRACT, "excel:Project:1:HRMS"
    ),
    WatchedSheet(
        "hrms_worklog.xlsx", "Worklog", WORKLOG_CONTRACT, "excel:Project:1:HRMS"
    ),
)


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

    for watched in WATCHED:
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
