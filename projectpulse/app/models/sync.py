"""Operational tables: what ran, what it read, and what it refused.

`SyncState` is the port of DevLake's `_devlake_subtask_states`
(backend/core/models/subtask_state.go:24-38) - a per-(task, scope) watermark that
only advances on success.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntPK

TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"


class SyncState(Base):
    """Incremental watermark, keyed per task *and* per scope.

    Scoping matters: board 8 and board 9 advance independently, so one failing
    scope cannot stall the others.

    A full sync is forced when the task has never run, or when `prev_config`
    changes - a threshold or column-map edit must not be applied to only the slice
    of data that happened to arrive after it.
    """

    __tablename__ = "sync_state"

    task: Mapped[str] = mapped_column(String(100), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: Deliberately the *start* of the last successful run, not its end, so the
    #: next window overlaps. Upserts absorb the duplicates; a gap would not be
    #: recoverable.
    prev_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    prev_config: Mapped[str | None] = mapped_column(String(64), default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class SyncRun(Base):
    """One execution. `trigger` is the *only* difference between the scheduled
    poll and the PM pressing "Update now" - they call the same function."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    trigger: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    status: Mapped[str] = mapped_column(String(20), default="running")
    rows_ok: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    changes_emitted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)


class SheetScan(Base):
    """One observation of one spreadsheet.

    Two jobs. The hash lets an unchanged sheet skip parsing entirely, which is what
    makes two-hourly polling free. And `scanned_at` of the *previous* scan is the
    lower bound for every change this scan detects - without this table there is no
    honest lower bound, and every Excel change would have to claim it happened at
    the instant we noticed.
    """

    __tablename__ = "sheet_scans"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    scope: Mapped[str] = mapped_column(String(255), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    sheet_name: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(Integer, default=0)


class RawReject(Base):
    """A row we could not parse.

    A product feature, not bookkeeping. A PM must see "12 rows in Team A's worklog
    did not parse" rather than silently receive a health score computed on 60% of
    the data.
    """

    __tablename__ = "raw_rejects"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    sync_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("sync_runs.id"), default=None
    )
    file_path: Mapped[str | None] = mapped_column(Text, default=None)
    sheet_name: Mapped[str | None] = mapped_column(Text, default=None)
    row_index: Mapped[int | None] = mapped_column(Integer, default=None)
    raw_row: Mapped[dict | None] = mapped_column(JSON, default=None)
    reason: Mapped[str] = mapped_column(Text)
