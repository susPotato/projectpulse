"""The Excel source: which workbooks we watch, and what each sheet means.

Scope configuration is declarative and lives here rather than in a database
table, because for now the set of watched sheets changes with the code that
understands them. When a PM can add their own sheet, this moves to a table and
the shape stays the same.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.ingest.runner import register_source
from app.ingest.sources.excel.ingest import ingest_sheet
from app.ingest.sources.excel.reader import (
    SCHEDULE_CONTRACT,
    WORKLOG_CONTRACT,
    SheetContract,
)

log = logging.getLogger(__name__)

SOURCE = "excel"


@dataclass(frozen=True)
class WatchedSheet:
    file_name: str
    sheet_name: str
    contract: SheetContract
    project_id: str


#: The demo portfolio. A real deployment reads this from scope config.
WATCHED: tuple[WatchedSheet, ...] = (
    WatchedSheet(
        "hrms_schedule.xlsx", "Activities", SCHEDULE_CONTRACT, "excel:Project:1:HRMS"
    ),
    WatchedSheet(
        "hrms_worklog.xlsx", "Worklog", WORKLOG_CONTRACT, "excel:Project:1:HRMS"
    ),
)


@register_source(SOURCE)
def run_excel_sync(
    session,
    *,
    connection_id: int,
    now: datetime,
    sync_run_id: int | None = None,
    data_root: Path | None = None,
) -> dict:
    """Ingest every watched sheet that is present.

    A missing workbook is a note, not a failure: someone has not uploaded this
    week's file yet, which is normal and should not fail the other sheets.
    """
    root = Path(data_root or settings.data_root)
    totals = {"rows_ok": 0, "rows_rejected": 0, "changes_emitted": 0, "notes": []}

    for watched in WATCHED:
        path = root / watched.file_name
        if not path.exists():
            totals["notes"].append(f"{watched.file_name}: not present, skipped")
            continue

        report = ingest_sheet(
            session,
            source=SOURCE,
            connection_id=connection_id,
            file_path=path,
            sheet_name=watched.sheet_name,
            contract=watched.contract,
            project_id=watched.project_id,
            now=now,
            sync_run_id=sync_run_id,
        )

        if report.skipped_unchanged:
            totals["notes"].append(f"{report.scope}: unchanged since last scan")
            continue

        if not report.ok:
            # Recorded and surfaced, but it must not take the other sheets down.
            totals["notes"].append(f"{report.scope}: REJECTED - {report.error}")
            totals["rows_rejected"] += report.rows_rejected
            continue

        totals["rows_ok"] += report.rows_ok
        totals["rows_rejected"] += report.rows_rejected
        totals["changes_emitted"] += report.changes_emitted

        detail = f"{report.scope}: {report.rows_ok} rows, {report.changes_emitted} changes"
        if report.low_confidence_changes:
            detail += f" ({report.low_confidence_changes} low-confidence)"
        if report.rows_rejected:
            detail += f", {report.rows_rejected} rejected"
        if report.unknown_headers:
            detail += f", unknown headers: {report.unknown_headers}"
        totals["notes"].append(detail)

    return totals
