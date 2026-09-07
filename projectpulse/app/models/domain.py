"""The domain layer: one vendor-neutral model, whatever the source system was.

Entities split into two groups.

*Engineering-shaped* rows (tasks, and the change history behind them) follow the
shape DevLake's domain layer settled on, so the two schemas stay comparable.

*Delivery-management* rows - Program, Milestone, Dependency, Resource - have no
DevLake counterpart. They are the half of the model the product actually lives in,
and they are ours to build.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, DomainEntity


# --------------------------------------------------------------------------
# Delivery management
# --------------------------------------------------------------------------


class Program(DomainEntity, Base):
    __tablename__ = "programs"

    name: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str | None] = mapped_column(String(50), default=None)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    end_date: Mapped[date | None] = mapped_column(Date, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)


class Project(DomainEntity, Base):
    __tablename__ = "projects"

    program_id: Mapped[str | None] = mapped_column(
        ForeignKey("programs.id"), default=None
    )
    name: Mapped[str] = mapped_column(Text)
    project_manager: Mapped[str | None] = mapped_column(Text, default=None)
    phase: Mapped[str | None] = mapped_column(String(50), default=None)
    status: Mapped[str | None] = mapped_column(String(50), default=None)
    health_score: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    last_updated: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )


class Milestone(DomainEntity, Base):
    __tablename__ = "milestones"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(Text)
    planned_date: Mapped[date | None] = mapped_column(Date, default=None)
    #: The originally committed date. Schedule variance is meaningless without it,
    #: and it is the first thing a hand-maintained sheet loses.
    baseline_date: Mapped[date | None] = mapped_column(Date, default=None)
    actual_date: Mapped[date | None] = mapped_column(Date, default=None)
    status: Mapped[str | None] = mapped_column(String(50), default=None)


class Task(DomainEntity, Base):
    __tablename__ = "tasks"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(
        ForeignKey("milestones.id"), default=None
    )
    title: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str | None] = mapped_column(String(50), default=None)
    #: The vendor's own value, never discarded. The rules read `status`; the
    #: evidence panel shows `original_status`, because that is what the PM typed.
    original_status: Mapped[str | None] = mapped_column(String(100), default=None)
    assignee: Mapped[str | None] = mapped_column(Text, default=None)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)
    baseline_end: Mapped[date | None] = mapped_column(Date, default=None)
    progress: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)


class Dependency(DomainEntity, Base):
    """Edges of the DAG the schedule engine traverses.

    No source we ingest populates this natively: the Excel template has no
    predecessor column yet, and Jira `issuelinks` is sparse in practice. `source`
    records how each edge was obtained so the evidence panel can be honest about
    an edge that was inferred rather than stated.
    """

    __tablename__ = "dependencies"
    __table_args__ = (
        UniqueConstraint("predecessor_id", "successor_id", name="uq_dep_edge"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    predecessor_id: Mapped[str] = mapped_column(String(255))
    successor_id: Mapped[str] = mapped_column(String(255))
    dep_type: Mapped[str] = mapped_column(String(20), default="FS")
    lag_days: Mapped[int] = mapped_column(Integer, default=0)
    #: 'excel_predecessor' | 'jira_issuelink' | 'wbs_implicit'
    source: Mapped[str | None] = mapped_column(String(50), default=None)


class Resource(DomainEntity, Base):
    __tablename__ = "resources"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    resource_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text, default=None)
    allocation_percent: Mapped[float | None] = mapped_column(
        Numeric(5, 2), default=None
    )
    period_start: Mapped[date | None] = mapped_column(Date, default=None)
    period_end: Mapped[date | None] = mapped_column(Date, default=None)


class QaItem(DomainEntity, Base):
    __tablename__ = "qa_items"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    test_case: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str | None] = mapped_column(String(50), default=None)
    priority: Mapped[str | None] = mapped_column(String(20), default=None)
    blocked_by: Mapped[str | None] = mapped_column(String(255), default=None)
    aging_days: Mapped[int | None] = mapped_column(Integer, default=None)


# --------------------------------------------------------------------------
# State history - the basis for every causal claim
# --------------------------------------------------------------------------

PRECISION_EXACT = "exact"
PRECISION_BOUNDED = "bounded"

IDENTITY_HIGH = "high"
IDENTITY_LOW = "low"


class StateChange(DomainEntity, Base):
    """One field of one entity changing value, with honest bounds on *when*.

    Our two source classes carry different time information, and conflating them
    is how a system starts asserting causes it cannot support:

    * A Jira changelog timestamps each transition exactly.
      ``precision='exact'``, and both bounds are that timestamp.
    * A spreadsheet only shows what is true now, so a change found by diffing two
      scans happened *somewhere between them*. ``precision='bounded'``, where
      ``occurred_at`` is the observing scan and ``occurred_at_lower`` the previous
      one.

    The ordering test is therefore interval arithmetic - see
    ``app.intelligence.temporal.ordering.provably_before`` - never a comparison of
    ``scan_id``. Sync windows deliberately overlap, so two bounded events from
    *different* scans can still be unorderable.
    """

    __tablename__ = "state_changes"
    __table_args__ = (
        CheckConstraint(
            "occurred_at_lower <= occurred_at", name="ck_state_change_interval"
        ),
        CheckConstraint(
            "\"precision\" <> 'exact' OR occurred_at_lower = occurred_at",
            name="ck_state_change_exact_is_a_point",
        ),
        Index("ix_state_change_entity", "entity_type", "entity_id", "occurred_at"),
    )

    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(255))
    field: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text, default=None)
    new_value: Mapped[str | None] = mapped_column(Text, default=None)

    #: Upper bound of when the change happened.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: Lower bound. Equal to `occurred_at` when precision is 'exact'.
    occurred_at_lower: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    precision: Mapped[str] = mapped_column(String(10))

    #: When we learned about it, as distinct from when it happened. The second
    #: axis of the bi-temporal model.
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: Which scan observed this, for bounded changes.
    scan_id: Mapped[int | None] = mapped_column(Integer, default=None)

    #: How confident we are that this row is the *same* row we saw last scan.
    #: A renamed task in a hand-maintained sheet looks like delete + insert, which
    #: would manufacture a change that never happened. Anything matched more
    #: weakly than a stable key is 'low', and the causal engine excludes it.
    identity_confidence: Mapped[str] = mapped_column(String(10), default=IDENTITY_HIGH)

    source_ref: Mapped[str | None] = mapped_column(String(255), default=None)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<StateChange {self.entity_id}.{self.field}: "
            f"{self.old_value!r} -> {self.new_value!r} "
            f"({self.precision})>"
        )
