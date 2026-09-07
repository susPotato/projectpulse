"""The tool layer: parsed into each source's own shape, before normalization.

Why this layer exists at all, when raw -> domain would be shorter: it is the
"last known state" the snapshot differ compares against. An Excel sheet only ever
shows the present, so detecting change requires somewhere to have kept the past.
That is this table, and it is also what lets the domain layer be re-derived after a
schema change without going back to the source.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, RawDataOrigin, Timestamped


class ToolExcelRow(Timestamped, RawDataOrigin, Base):
    """One spreadsheet row as last observed, keyed by its resolved identity."""

    __tablename__ = "_tool_excel_rows"

    #: Which sheet, e.g. 'hrms/schedule.xlsx#Activities'.
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: The resolved row identity - a stable `Task ID` where the template provides
    #: one, otherwise a synthetic key from the identity resolver.
    row_key: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: The normalized row, header-name keyed. Compared field by field by the differ.
    payload: Mapped[dict] = mapped_column(JSON)

    #: 'high' when matched on a stable key, 'low' when matched by similarity.
    identity_confidence: Mapped[str] = mapped_column(String(10), default="high")
    #: Which scan last observed this row. A row absent from the newest scan has a
    #: stale value here, which is how deletions are detected.
    last_seen_scan_id: Mapped[int | None] = mapped_column(Integer, default=None)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class ToolJiraIssue(Timestamped, RawDataOrigin, Base):
    __tablename__ = "_tool_jira_issues"

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    issue_key: Mapped[str | None] = mapped_column(String(255), default=None)
    project_key: Mapped[str | None] = mapped_column(String(255), default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    issue_type: Mapped[str | None] = mapped_column(String(100), default=None)
    status: Mapped[str | None] = mapped_column(String(100), default=None)
    status_category: Mapped[str | None] = mapped_column(String(100), default=None)
    assignee: Mapped[str | None] = mapped_column(Text, default=None)
    created_at_src: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    updated_at_src: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    due_date: Mapped[str | None] = mapped_column(String(32), default=None)
    story_points: Mapped[float | None] = mapped_column(default=None)


class ToolJiraChangelog(Timestamped, RawDataOrigin, Base):
    """One field transition from a Jira changelog.

    This is the source of every ``precision='exact'`` state change we have, and it
    is the reason a demo that includes Jira can make ordering claims a
    spreadsheet-only demo cannot.
    """

    __tablename__ = "_tool_jira_changelogs"

    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    changelog_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: One changelog entry can move several fields at once; each is its own row.
    field: Mapped[str] = mapped_column(String(100), primary_key=True)

    issue_id: Mapped[str] = mapped_column(String(255), index=True)
    author: Mapped[str | None] = mapped_column(Text, default=None)
    from_value: Mapped[str | None] = mapped_column(Text, default=None)
    to_value: Mapped[str | None] = mapped_column(Text, default=None)
    created_at_src: Mapped[datetime] = mapped_column(DateTime(timezone=True))
