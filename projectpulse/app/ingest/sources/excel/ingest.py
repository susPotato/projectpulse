"""Ingest one spreadsheet: read, identify, diff, persist.

This is where a change acquires its time bounds, and it is the only place in the
Excel path that decides them. A spreadsheet tells us *what* is true, never *when*
it became true, so the honest claim is an interval:

    occurred_at_lower = the previous scan   (we know it had not happened yet)
    occurred_at       = this scan           (we know it has happened by now)

Both bounds come from :class:`~app.models.sync.SheetScan` rows, which is why the
scan log is not optional bookkeeping - without it there is no lower bound, and
every Excel change would have to pretend it happened the instant we noticed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.ids import domain_id
from app.ingest.sources.excel.identity import (
    MissingKeyColumn,
    resolve_identities,
)
from app.ingest.sources.excel.reader import SheetContract, read_sheet, sha256_file
from app.ingest.sources.excel.snapshot_diff import diff_snapshot, normalized_payload
from app.models.domain import PRECISION_BOUNDED, StateChange
from app.models.raw import RawExcelRows
from app.models.sync import RawReject, SheetScan
from app.models.tool import ToolExcelRow

ENTITY_NAMES = {"task": "Task", "qa_item": "QaItem"}


@dataclass
class IngestReport:
    scope: str
    skipped_unchanged: bool = False
    rows_ok: int = 0
    rows_rejected: int = 0
    changes_emitted: int = 0
    low_confidence_changes: int = 0
    unknown_headers: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _previous_rows(session, scope: str) -> dict[str, dict]:
    rows = session.scalars(
        select(ToolExcelRow).where(ToolExcelRow.scope == scope)
    ).all()
    return {row.row_key: dict(row.payload or {}) for row in rows}


def _last_scan(session, source: str, scope: str) -> SheetScan | None:
    return session.scalars(
        select(SheetScan)
        .where(SheetScan.source == source, SheetScan.scope == scope)
        .order_by(SheetScan.scanned_at.desc())
        .limit(1)
    ).first()


def ingest_sheet(
    session,
    *,
    source: str,
    connection_id: int,
    file_path: str | Path,
    sheet_name: str,
    contract: SheetContract,
    project_id: str,
    now: datetime,
    sync_run_id: int | None = None,
) -> IngestReport:
    """Ingest one sheet of one workbook.

    Returns a report rather than raising, so one broken sheet does not abort a
    sync across the others. The exception is a sheet with no identity column,
    which is recorded as an error on the report and skipped entirely - guessing
    identity would be worse than ingesting nothing.
    """
    file_path = Path(file_path)
    scope = f"{file_path.name}#{sheet_name}"
    report = IngestReport(scope=scope)
    params = json.dumps({"connection_id": connection_id, "scope": scope})

    previous_scan = _last_scan(session, source, scope)

    # Unchanged bytes: nothing to do. This is what makes frequent polling cheap.
    if previous_scan is not None and previous_scan.sha256 == sha256_file(file_path):
        report.skipped_unchanged = True
        return report

    read = read_sheet(file_path, sheet_name, contract)
    report.unknown_headers = read.unknown_headers

    # No baseline means no claims: the first sight of a sheet is history we did
    # not witness, not change we observed.
    baseline = _previous_rows(session, scope) if previous_scan is not None else None

    try:
        resolved, rejected = resolve_identities(
            read.rows,
            key_field=contract.key_field,
            title_field=contract.title_field,
            previous=baseline,
            has_key_column=read.has_key_column,
        )
    except MissingKeyColumn as exc:
        report.error = str(exc)
        session.add(
            RawReject(
                sync_run_id=sync_run_id,
                file_path=str(file_path),
                sheet_name=sheet_name,
                row_index=read.header_row,
                raw_row={},
                reason=str(exc),
            )
        )
        report.rows_rejected = len(read.rows)
        return report

    for rejection in [*read.rejects, *rejected]:
        session.add(
            RawReject(
                sync_run_id=sync_run_id,
                file_path=str(file_path),
                sheet_name=sheet_name,
                row_index=rejection.row_index,
                # Normalized so the quarantined row is JSON-storable; a rejected
                # row still has to be readable by whoever fixes the sheet.
                raw_row=normalized_payload(rejection.raw_row),
                reason=rejection.reason,
            )
        )
    report.rows_rejected = len(read.rejects) + len(rejected)

    scan = SheetScan(
        source=source,
        scope=scope,
        file_path=str(file_path),
        sheet_name=sheet_name,
        sha256=read.sha256,
        scanned_at=now,
        row_count=len(resolved),
    )
    session.add(scan)
    session.flush()  # need scan.id for state_changes

    # One raw row per spreadsheet row. This is the evidence pointer: a PM clicking
    # a finding gets back the actual cells, from the actual file, at the actual
    # time we read them.
    raw_ids: dict[str, int] = {}
    for row in resolved:
        raw_row = RawExcelRows(
            params=params,
            data=json.dumps(row.payload, default=str).encode("utf-8"),
            url=f"file://{file_path.as_posix()}#{sheet_name}!row{row.row_index}",
            input=json.dumps({"row_key": row.row_key, "row_index": row.row_index}),
            fetched_at=now,
        )
        session.add(raw_row)
        session.flush()
        raw_ids[row.row_key] = raw_row.id

    # Normalized from here on. The raw layer above keeps the original cell values;
    # everything downstream compares like for like.
    current = {row.row_key: normalized_payload(row.payload) for row in resolved}
    confidences = {row.row_key: row.confidence for row in resolved}

    diff = diff_snapshot(
        baseline,
        current,
        tracked_fields=contract.tracked_fields,
        confidences=confidences,
    )

    entity_name = ENTITY_NAMES.get(contract.entity_type, "Task")
    lower_bound = previous_scan.scanned_at if previous_scan is not None else now

    for change in diff.changes:
        entity_id = domain_id(source, entity_name, connection_id, change.row_key)
        state_change = StateChange(
            id=domain_id(
                source,
                "StateChange",
                connection_id,
                change.row_key,
                change.field,
                str(int(now.timestamp())),
            ),
            entity_type=contract.entity_type,
            entity_id=entity_id,
            field=change.field,
            old_value=change.old_value,
            new_value=change.new_value,
            # The whole point of this module.
            occurred_at=now,
            occurred_at_lower=lower_bound,
            precision=PRECISION_BOUNDED,
            ingested_at=now,
            scan_id=scan.id,
            identity_confidence=change.identity_confidence,
            source_ref=scope,
        )
        state_change.raw_data_table = RawExcelRows.__tablename__
        state_change.raw_data_params = params
        state_change.raw_data_id = raw_ids.get(change.row_key)
        session.merge(state_change)

        if change.identity_confidence == "low":
            report.low_confidence_changes += 1

    report.changes_emitted = len(diff.changes)

    # The tool layer becomes the baseline for next scan.
    for row in resolved:
        session.merge(
            ToolExcelRow(
                scope=scope,
                row_key=row.row_key,
                payload=current[row.row_key],
                identity_confidence=row.confidence,
                last_seen_scan_id=scan.id,
                last_seen_at=now,
                raw_data_table=RawExcelRows.__tablename__,
                raw_data_params=params,
                raw_data_id=raw_ids.get(row.row_key),
                raw_data_remark=f"{sheet_name}!row{row.row_index}",
            )
        )

    for gone in diff.removed_keys:
        stale = session.get(ToolExcelRow, {"scope": scope, "row_key": gone})
        if stale is not None:
            session.delete(stale)

    report.rows_ok = len(resolved)
    return report
