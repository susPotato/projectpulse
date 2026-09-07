"""Declarative base and the audit/provenance mixins every table inherits.

Ported from DevLake's `common.RawDataOrigin` / `common.NoPKModel`
(backend/core/models/common/base.go:52-82, and its Python twin at
backend/python/pydevlake/pydevlake/model.py:111-140).

The six audit columns are not bookkeeping. `raw_data_id` is the pointer that makes
the evidence panel possible: it is stamped by the extractor, copied unchanged by the
convertor, and resolves a domain row back to the exact API response or spreadsheet
row it was derived from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Auto-incrementing surrogate key. Postgres is the deployment target, but SQLite
#: only auto-increments a column declared exactly INTEGER PRIMARY KEY - a BIGINT
#: there silently fails to generate ids. The variant keeps one set of models
#: usable by both the real database and the fast unit tests.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RawDataOrigin:
    """Where this row came from. Mixed into every tool and domain table.

    The Python attribute drops the leading underscore (SQLAlchemy treats
    underscore-prefixed attributes as private) while the column keeps it, matching
    DevLake's on-disk naming so the schemas stay comparable.
    """

    #: JSON scope slice, e.g. '{"connection_id":1,"board_id":8}'. Indexed because
    #: it is the delete key for a full refresh.
    raw_data_params: Mapped[str | None] = mapped_column(
        "_raw_data_params", String(255), index=True, default=None
    )
    #: Which raw table, e.g. '_raw_jira_issues'. Lets the evidence panel dispatch
    #: generically instead of hardcoding one source.
    raw_data_table: Mapped[str | None] = mapped_column(
        "_raw_data_table", String(255), default=None
    )
    #: FK-by-convention to `<raw table>.id`. **The evidence pointer.**
    raw_data_id: Mapped[int | None] = mapped_column(
        "_raw_data_id", BigInteger, default=None
    )
    #: Debugging breadcrumb - sheet name, row index, whatever helps a human.
    raw_data_remark: Mapped[str | None] = mapped_column(
        "_raw_data_remark", Text, default=None
    )

    def set_raw_origin(self, raw) -> None:
        """Stamp provenance from a raw row (used by extractors)."""
        self.raw_data_id = raw.id
        self.raw_data_params = raw.params
        self.raw_data_table = raw.__tablename__

    def copy_origin_from(self, other: "RawDataOrigin") -> None:
        """Carry provenance forward unchanged (used by convertors).

        Convertors must copy rather than re-derive: the chain raw -> tool -> domain
        is only intact if the same `raw_data_id` survives every hop.
        """
        self.raw_data_id = other.raw_data_id
        self.raw_data_params = other.raw_data_params
        self.raw_data_table = other.raw_data_table


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow
    )
    #: Bumped on every upsert. This is the incremental-conversion watermark:
    #: convertors filter on it to find tool rows touched since the last run.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        default=utcnow,
    )


class DomainEntity(Timestamped, RawDataOrigin):
    """A vendor-neutral row keyed by :func:`app.ids.domain_id`."""

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
