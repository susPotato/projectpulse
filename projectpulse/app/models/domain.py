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
    Boolean,
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

from app.models.base import Base, BigIntPK, DomainEntity, Timestamped


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
    #: The issue's own body text, carried verbatim and never parsed.
    #:
    #: Deliberately **not** in `SCHEDULE_CONTRACT.tracked_fields`: a description
    #: being edited is not a delivery event, and letting it into the differ
    #: would build causal chains out of somebody tidying their prose.
    #:
    #: Nothing in `app/intelligence/` reads it, and that is the point - a rule
    #: conditioning on free text could not call itself deterministic. Its one
    #: reader is `app/risks/drafts.py`, which is explicitly the advisory lane
    #: and whose output a person has to accept before it is a risk at all.
    description: Mapped[str | None] = mapped_column(Text, default=None)
    #: The sheet's own Phase column - "Planning", "Development", "Testing".
    #: Carried because it is the vocabulary a delivery constraint is written in
    #: ("a Testing task needs an Environment predecessor"); a rule of that shape
    #: is not expressible without it. Nothing reads it yet.
    phase: Mapped[str | None] = mapped_column(String(50), default=None)
    status: Mapped[str | None] = mapped_column(String(50), default=None)
    #: The vendor's own value, never discarded. The rules read `status`; the
    #: evidence panel shows `original_status`, because that is what the PM typed.
    original_status: Mapped[str | None] = mapped_column(String(100), default=None)
    assignee: Mapped[str | None] = mapped_column(Text, default=None)
    start_date: Mapped[date | None] = mapped_column(Date, default=None)
    due_date: Mapped[date | None] = mapped_column(Date, default=None)
    #: When the work actually finished, from the tracker's own record of the
    #: transition into a closed status - never from a plan. Distinct from
    #: `due_date`, which is when it was promised: holding both is what lets a
    #: page say a task landed early rather than only that it is no longer open.
    actual_end: Mapped[date | None] = mapped_column(Date, default=None)
    baseline_end: Mapped[date | None] = mapped_column(Date, default=None)
    progress: Mapped[float | None] = mapped_column(Numeric(5, 2), default=None)
    #: When the *source system* last says this row changed - Jira's `Updated`.
    #:
    #: Deliberately distinct from anything in `state_changes`, which is what
    #: *we* observed between two scans. This is a claim the vendor makes, in the
    #: same relationship as `original_status` to `status`: carried because it is
    #: what the tool says, never mistaken for what we saw. It is the only
    #: movement signal available from a single export, where we have observed
    #: nothing yet because there is no earlier scan to diff against.
    source_updated_at: Mapped[date | None] = mapped_column(Date, default=None)


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
    #: Both are read by `WORKLOG_CONTRACT` and were being dropped by the
    #: convertor - the same gap `Task.phase` had. Without them there is no
    #: effort in the system at all, so no burn or workload chart can be honest.
    assignee: Mapped[str | None] = mapped_column(Text, default=None)
    hours_spent: Mapped[float | None] = mapped_column(Numeric(7, 2), default=None)
    #: Planned effort, and the date the hours were last logged. Both exist
    #: because we own this template - the same reason the schedule sheet has a
    #: `Predecessor` column. Without the pair there is no burn chart that is
    #: not invented: `hours_spent` alone is a total with no baseline and no
    #: time axis.
    estimate_hours: Mapped[float | None] = mapped_column(Numeric(7, 2), default=None)
    log_date: Mapped[date | None] = mapped_column(Date, default=None)


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


# --------------------------------------------------------------------------
# Risk register
# --------------------------------------------------------------------------


class Risk(Base, Timestamped):
    """A risk a PM tracks by hand - probability, impact, cost and delay
    estimates, mitigation ownership.

    Genuinely unlike everything above: nothing here is derived from a sheet,
    so it carries no `RawDataOrigin` - there is no raw row to point evidence
    at, because a PM's own judgement *is* the data. For the same reason
    `app/intelligence/` must never read this table: a rule conditioning on an
    opinion could not still call itself deterministic. `project_id` is not a
    foreign key, matching `RawReject` - a risk entered before a project's
    first sync must not be rejected for a row that does not exist yet.

    `pre_rating` / `post_rating` are deliberately absent as columns. They are
    a pure lookup of (likelihood, impact) - see `app/risks/matrix.py` - so a
    badge can never disagree with the pair a PM actually chose, the same
    reason `intelligence/confidence.py` computes a band rather than storing
    one.
    """

    __tablename__ = "risks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(255), index=True)

    title: Mapped[str] = mapped_column(Text)
    #: PM-typed, per-project sequence number - "Risk No." in the register.
    #: Text because a PM may type "4a"; nothing here parses it as an integer.
    risk_no: Mapped[str | None] = mapped_column(String(20), default=None)
    status: Mapped[str] = mapped_column(String(20), default="Active")
    key_risk: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    category: Mapped[str | None] = mapped_column(String(50), default=None)
    secondary_categories: Mapped[str | None] = mapped_column(Text, default=None)

    #: Who proposed this row. `None` - the only value existing rows have, and
    #: the only one `POST /api/risks` writes - means a person typed it. The
    #: sole other value is `ai_draft`, set by `app/risks/drafts.py`.
    #:
    #: Kept after a draft is accepted rather than cleared. Provenance is not
    #: undone by agreement: a reader six weeks later is entitled to know the
    #: sentence began as a model's reading of an issue body, and a column that
    #: erased itself on accept would quietly turn a suggestion into testimony.
    origin: Mapped[str | None] = mapped_column(String(20), default=None)
    #: The task ids whose text the draft was read from, comma-separated.
    #:
    #: A proposal that cites nothing is discarded rather than shown - see
    #: `drafts.py`. This column is what makes that checkable afterwards, and
    #: what the Evidence tab renders: the claim is only as good as the rows
    #: under it, and a reader must be able to go and look at them.
    cited_task_ids: Mapped[str | None] = mapped_column(Text, default=None)

    review_date: Mapped[date | None] = mapped_column(Date, default=None)
    possible_realise_date: Mapped[date | None] = mapped_column(Date, default=None)
    retired_date: Mapped[date | None] = mapped_column(Date, default=None)

    cause_title: Mapped[str | None] = mapped_column(Text, default=None)
    cause_description: Mapped[str | None] = mapped_column(Text, default=None)

    # Pre-treatment: the assessment before any mitigation.
    pre_likelihood: Mapped[str | None] = mapped_column(String(20), default=None)
    pre_impact: Mapped[str | None] = mapped_column(String(20), default=None)
    pre_cost: Mapped[float | None] = mapped_column(Numeric(14, 2), default=None)
    pre_delay_days: Mapped[int | None] = mapped_column(Integer, default=None)

    # Post-treatment: the assessment after the mitigation below is applied.
    post_likelihood: Mapped[str | None] = mapped_column(String(20), default=None)
    post_impact: Mapped[str | None] = mapped_column(String(20), default=None)
    post_cost: Mapped[float | None] = mapped_column(Numeric(14, 2), default=None)
    post_delay_days: Mapped[int | None] = mapped_column(Integer, default=None)

    responsible: Mapped[str | None] = mapped_column(Text, default=None)
