"""The raw layer: immutable, append-only records of what a source actually returned.

One physical table per (source, endpoint), all with the identical shape - the same
arrangement as DevLake's `_raw_*` tables
(backend/helpers/pluginhelper/api/api_rawdata.go:30-38).

Separate tables rather than one wide table with a discriminator, for two reasons:
a full refresh of one endpoint is a scoped DELETE that cannot touch another, and a
raw table can be truncated independently once its domain rows are settled.

**Nothing ever updates a raw row.** Collectors append; extractors read. That is what
makes it safe to re-derive the whole domain layer after a schema change without
going back to the source.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntPK


class RawRowMixin:
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)

    #: JSON scope slice this fragment belongs to, e.g. '{"connection_id":1,"board_id":8}'.
    params: Mapped[str] = mapped_column(String(255), index=True, default="")
    #: The response fragment exactly as received - one issue, one changelog entry,
    #: one spreadsheet row. Bytes, not parsed JSON: we store what arrived.
    data: Mapped[bytes] = mapped_column(LargeBinary)
    #: The request this came from. The evidence panel shows this to a PM verbatim.
    url: Mapped[str] = mapped_column(Text, default="")
    #: The iterator row that drove the request, where one exists. A Jira changelog
    #: page does not repeat its issue id, so the extractor reads it back from here.
    input: Mapped[str | None] = mapped_column(Text, default=None)
    #: Ingestion time. Indexed - it is the extractor's incremental watermark.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


def raw_table(name: str) -> type:
    """Define a raw table named `_raw_<name>`.

    A factory rather than repeated class bodies: every raw table is the same
    shape by definition, and a divergence between two of them would be a bug.
    """
    return type(
        "Raw" + "".join(p.title() for p in name.split("_")),
        (RawRowMixin, Base),
        {"__tablename__": f"_raw_{name}"},
    )


# Registered raw tables. Adding a source endpoint means adding a line here.
RawJiraIssues = raw_table("jira_issues")
RawJiraChangelogs = raw_table("jira_changelogs")
RawExcelRows = raw_table("excel_rows")

RAW_TABLES = {
    t.__tablename__: t for t in (RawJiraIssues, RawJiraChangelogs, RawExcelRows)
}
