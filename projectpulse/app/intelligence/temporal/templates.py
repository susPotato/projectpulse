"""The named hypotheses a causal chain is allowed to claim.

**Orderable is not causal.** `ordering.py` can prove that 121 pairs of events
happened in a particular order; almost none of those pairs mean anything. A
spreadsheet where someone fixed a typo before a milestone slipped is a provably
ordered pair and an absurd cause.

So a chain is only built when an ordered pair matches a *pattern a PM actually
asked about*. That is what this module holds: six named hypotheses, each one a
question a delivery manager already asks in a status meeting. Anything not on this
list produces no chain at all - the system reports what moved and stays silent
about why.

Three things every template must specify, and the second is the one that does the
real work:

**A cause and effect pattern.** Which entity, which field, and which direction the
value moved. "planned finish moved later by at least 2 days" is a pattern;
"planned finish changed" is not, because a correction *earlier* is not a slip.

**A link.** Temporal order alone connects nothing - two unrelated tasks slipping in
the same week are ordered but unrelated. Every template names what has to connect
the two entities, and the strongest available link is a dependency edge, which is
why `dependencies` had to exist before this module could. `link` is carried onto
the finished chain so the evidence panel can say *why* we think these are related,
and so a project-scoped guess is never presented as an edge-backed fact.

**A lag ceiling.** A cause six months before its effect is not a cause. The ceiling
is per-template because the plausible interval genuinely differs: a blocked
environment reaches QA in days, a scope addition reaches a milestone in weeks.

Pure - no session, no ORM, no clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

#: Row-level events, mirrored from `snapshot_diff` so this module needs no import
#: from the ingestion layer.
FIELD_ROW_PRESENT = "__row__"

_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


class HasChange(Protocol):
    """The shape of a state change. `StateChange`, or a test double."""

    entity_type: str
    entity_id: str
    field: str
    old_value: str | None
    new_value: str | None
    occurred_at: datetime
    occurred_at_lower: datetime
    precision: str
    identity_confidence: str


class Direction(StrEnum):
    """Which way a value has to have moved for a pattern to match."""

    ANY = "any"
    #: A date moved later, or a number went up. A slip.
    INCREASED = "increased"
    #: A date moved earlier, or a number went down. A pull-in, or a loss.
    DECREASED = "decreased"


class LinkBasis(StrEnum):
    """Why we believe two entities are related at all.

    Ordered from strongest to weakest, and :func:`link_rank` depends on that
    order: a chain backed by a stated dependency edge outranks one that merely
    shares a project.
    """

    #: The same task changed twice.
    SAME_ENTITY = "same_entity"
    #: A direct edge in `dependencies`. The strongest cross-entity claim we have.
    DEPENDENCY_EDGE = "dependency_edge"
    #: Reachable through the DAG, but not adjacent. Real, and weaker.
    DEPENDENCY_PATH = "dependency_path"
    #: Nothing connects them but the project. A hypothesis, honestly labelled.
    SAME_PROJECT = "same_project"


_LINK_ORDER = (
    LinkBasis.SAME_ENTITY,
    LinkBasis.DEPENDENCY_EDGE,
    LinkBasis.DEPENDENCY_PATH,
    LinkBasis.SAME_PROJECT,
)


def link_rank(link: LinkBasis) -> int:
    """Lower is stronger. Used to sort findings so the best-evidenced lead."""
    return _LINK_ORDER.index(link)


# --------------------------------------------------------------------------
# Value comparison
# --------------------------------------------------------------------------


def _as_number(text: str | None) -> float | None:
    if text is None or not _NUMBER.match(text.strip()):
        return None
    return float(text)


def _as_date(text: str | None) -> date | None:
    """Values arrive already normalized to ISO by `snapshot_diff.normalize_value`."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def value_delta(old: str | None, new: str | None) -> float | None:
    """Signed magnitude of a change - days for dates, units for numbers.

    Returns None when the values are not comparable quantities (a status moving
    from "Open" to "Blocked" has a direction a human understands but no
    magnitude). Callers must treat None as "no magnitude", never as zero: a
    template with a `min_magnitude` would otherwise silently match every status
    change in the project.
    """
    old_date, new_date = _as_date(old), _as_date(new)
    if old_date is not None and new_date is not None:
        return float((new_date - old_date).days)

    old_number, new_number = _as_number(old), _as_number(new)
    if old_number is not None and new_number is not None:
        return new_number - old_number

    return None


# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------


def _fold(values) -> frozenset[str] | None:
    return frozenset(v.casefold() for v in values) if values else None


@dataclass(frozen=True)
class ChangePattern:
    """What a state change has to look like to play a role in a chain."""

    label: str
    entity_types: frozenset[str] | None = None
    fields: frozenset[str] | None = None
    from_values: frozenset[str] | None = None
    to_values: frozenset[str] | None = None
    direction: Direction = Direction.ANY
    #: Minimum absolute movement. Only meaningful with a direction; a one-day
    #: date correction is noise, not a slip.
    min_magnitude: float = 0.0

    def matches(self, change: HasChange) -> bool:
        if self.entity_types and change.entity_type not in self.entity_types:
            return False
        if self.fields and change.field not in self.fields:
            return False
        if self.from_values is not None:
            current = (change.old_value or "").casefold()
            if current not in self.from_values:
                return False
        if self.to_values is not None:
            current = (change.new_value or "").casefold()
            if current not in self.to_values:
                return False

        if self.direction is Direction.ANY and self.min_magnitude == 0.0:
            return True

        delta = value_delta(change.old_value, change.new_value)
        if delta is None:
            # Not a quantity. A pattern asking for a direction cannot be satisfied
            # by something that has none - matching here would let every status
            # edit masquerade as a schedule slip.
            return False
        if self.direction is Direction.INCREASED and delta <= 0:
            return False
        if self.direction is Direction.DECREASED and delta >= 0:
            return False
        return abs(delta) >= self.min_magnitude


@dataclass(frozen=True)
class CausalTemplate:
    """One named hypothesis, and everything needed to test it."""

    id: str
    name: str
    #: The question a PM asks that this answers. If you cannot write one, the
    #: template does not belong here.
    question: str
    cause: ChangePattern
    effect: ChangePattern
    link: LinkBasis
    #: A cause this long before its effect is not a cause.
    max_lag_days: int
    rationale: str

    def matches(self, cause: HasChange, effect: HasChange) -> bool:
        """Pattern match only. Ordering, linking and lag are `chains.py`'s job."""
        return self.cause.matches(cause) and self.effect.matches(effect)


# --------------------------------------------------------------------------
# The six. Each is a question, not a correlation.
# --------------------------------------------------------------------------

TASK = frozenset({"task"})
QA = frozenset({"qa_item"})
BLOCKED = frozenset({"blocked"})
SCHEDULE_FIELDS = frozenset({"planned_end"})

#: A slip worth reporting. Below this a date edit is housekeeping.
MIN_SLIP_DAYS = 2.0


DEPENDENCY_SLIP_HITS_SUCCESSOR = CausalTemplate(
    id="dependency_slip_hits_successor",
    name="Dependency slip hit its successor",
    question="Which of my tasks slipped because something upstream slipped?",
    cause=ChangePattern(
        label="an upstream task's planned finish moved later",
        entity_types=TASK,
        fields=SCHEDULE_FIELDS,
        direction=Direction.INCREASED,
        min_magnitude=MIN_SLIP_DAYS,
    ),
    effect=ChangePattern(
        label="the task that depends on it moved later too",
        entity_types=TASK,
        fields=SCHEDULE_FIELDS,
        direction=Direction.INCREASED,
        min_magnitude=MIN_SLIP_DAYS,
    ),
    link=LinkBasis.DEPENDENCY_EDGE,
    max_lag_days=30,
    rationale=(
        "The strongest chain the system can make: both ends are schedule "
        "movements and a stated edge connects them. This is the one case where "
        "'because' is close to arithmetic rather than inference."
    ),
)

SLIP_CASCADES_DOWNSTREAM = CausalTemplate(
    id="slip_cascades_downstream",
    name="A slip reached further down the chain",
    question="How far downstream has this delay actually spread?",
    cause=ChangePattern(
        label="a task's planned finish moved later",
        entity_types=TASK,
        fields=SCHEDULE_FIELDS,
        direction=Direction.INCREASED,
        min_magnitude=MIN_SLIP_DAYS,
    ),
    effect=ChangePattern(
        label="a task further down the dependency path moved later",
        entity_types=TASK,
        fields=SCHEDULE_FIELDS,
        direction=Direction.INCREASED,
        min_magnitude=MIN_SLIP_DAYS,
    ),
    link=LinkBasis.DEPENDENCY_PATH,
    max_lag_days=45,
    rationale=(
        "The same claim as the direct case but through a path, so it is ranked "
        "below it. Reported separately because 'two hops away' is the question a "
        "PM asks second, and answering it with the direct template would "
        "overstate how tight the connection is."
    ),
)

BLOCKED_WORK_STALLS_QA = CausalTemplate(
    id="blocked_work_stalls_qa",
    name="Blocked delivery work stalled QA",
    question="Why has my QA queue stopped moving?",
    cause=ChangePattern(
        label="a delivery task became blocked or slipped",
        entity_types=TASK,
        fields=frozenset({"status", "planned_end"}),
        direction=Direction.ANY,
    ),
    effect=ChangePattern(
        label="a QA item became blocked",
        entity_types=QA,
        fields=frozenset({"status", "blocked"}),
        to_values=_fold({"blocked", "yes"}),
    ),
    link=LinkBasis.SAME_PROJECT,
    max_lag_days=21,
    rationale=(
        "QA items carry no dependency edges to the tasks they exercise, so the "
        "only available link is the project. Deliberately kept as a "
        "SAME_PROJECT chain rather than dressed up: it is the pattern a PM "
        "recognises instantly, and the evidence panel must show it as the "
        "weaker claim it is."
    ),
)

QA_BACKLOG_COMPOUNDS = CausalTemplate(
    id="qa_backlog_compounds",
    name="The QA backlog is compounding",
    question="Is this one blocked test, or a queue forming behind something?",
    cause=ChangePattern(
        label="a QA item became blocked",
        entity_types=QA,
        fields=frozenset({"status", "blocked"}),
        to_values=_fold({"blocked", "yes"}),
    ),
    effect=ChangePattern(
        label="another QA item became blocked afterwards",
        entity_types=QA,
        fields=frozenset({"status", "blocked"}),
        to_values=_fold({"blocked", "yes"}),
    ),
    link=LinkBasis.SAME_PROJECT,
    max_lag_days=21,
    rationale=(
        "One blocked test is noise; a sequence of them behind the same cause is "
        "the signal. This template exists to make the *count* visible - the "
        "aggregate is what a PM escalates on, not any single item."
    ),
)

SCOPE_ADDED_EXTENDS_SCHEDULE = CausalTemplate(
    id="scope_added_extends_schedule",
    name="Added scope extended the schedule",
    question="Did the work we added actually push the plan out?",
    cause=ChangePattern(
        label="a new task appeared in the plan",
        entity_types=TASK,
        fields=frozenset({FIELD_ROW_PRESENT}),
        to_values=_fold({"present"}),
    ),
    effect=ChangePattern(
        label="an existing task's planned finish moved later",
        entity_types=TASK,
        fields=SCHEDULE_FIELDS,
        direction=Direction.INCREASED,
        min_magnitude=MIN_SLIP_DAYS,
    ),
    link=LinkBasis.SAME_PROJECT,
    max_lag_days=30,
    rationale=(
        "Scope creep is invisible per-row and obvious in aggregate. The chain is "
        "project-scoped by nature: added work consumes the same capacity as "
        "everything else, whether or not an edge says so."
    ),
)

OWNER_CHANGE_PRECEDES_SLIP = CausalTemplate(
    id="owner_change_precedes_slip",
    name="A handover preceded a slip",
    question="Did reassigning this task cost us time?",
    cause=ChangePattern(
        label="the task changed owner",
        entity_types=TASK,
        fields=frozenset({"assignee"}),
    ),
    effect=ChangePattern(
        label="the same task's planned finish moved later",
        entity_types=TASK,
        fields=SCHEDULE_FIELDS,
        direction=Direction.INCREASED,
        min_magnitude=MIN_SLIP_DAYS,
    ),
    link=LinkBasis.SAME_ENTITY,
    max_lag_days=30,
    rationale=(
        "Same entity, so the link is certain even though the causation is not - "
        "a handover and a slip on one task may share a cause rather than being "
        "one. Worth surfacing because it is cheap for a PM to confirm or dismiss."
    ),
)


TEMPLATES: tuple[CausalTemplate, ...] = (
    DEPENDENCY_SLIP_HITS_SUCCESSOR,
    SLIP_CASCADES_DOWNSTREAM,
    BLOCKED_WORK_STALLS_QA,
    QA_BACKLOG_COMPOUNDS,
    SCOPE_ADDED_EXTENDS_SCHEDULE,
    OWNER_CHANGE_PRECEDES_SLIP,
)

BY_ID = {t.id: t for t in TEMPLATES}
