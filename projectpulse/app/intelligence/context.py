"""Everything the rules engine is allowed to see, flattened to scalars.

ZEN evaluates **one flat record**. Trying to make it aggregate - counting blocked
QA items inside a decision table, or reaching into a list of chains - is a day
lost to fighting the tool. So all aggregation happens here, in Python, where it is
readable and testable, and the engine only ever compares numbers.

That split is also what keeps the rule tables editable by a delivery manager
rather than a developer. A row saying ``qa_blocked_ratio > 0.6`` is something a PM
can argue with; a row containing a traversal is not.

Two properties this module must preserve:

**Every scalar is derived, never estimated.** Each field below is a count, a ratio
of counts, or arithmetic on dates a human typed. Nothing here is a model output.

**Names are stable.** These keys are the rule tables' vocabulary. Renaming one
silently stops a rule from firing rather than raising, so the record is built from
a declared dataclass and `as_record()` is the only way to produce it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from app.intelligence.contention import OVERTIME_MONTHLY_LIMIT
from app.intelligence.schedule.graph import EdgeRecord, ScheduleGraph
from app.intelligence.schedule.impact import ImpactReport
from app.intelligence.temporal.chains import CausalChain
from app.intelligence.temporal.templates import LinkBasis

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from app.intelligence.contention import ProgramContext

#: Statuses that mean "not progressing", normalized to casefold.
BLOCKED_STATES = frozenset({"blocked", "yes", "on hold", "impeded"})
DONE_STATES = frozenset({"done", "complete", "completed", "closed"})
IN_PROGRESS_STATES = frozenset({"in progress", "in_progress", "wip", "doing"})

#: What counts as "due soon". Two weeks because it is the horizon a PM can still
#: act inside - long enough to move a person or cut scope, short enough that
#: everything in it is genuinely imminent. A rule may narrow it; nothing should
#: widen it without saying why, since a 90-day window makes every project look
#: busy and says nothing.
DUE_SOON_DAYS = 14

#: How long an open task can go without the source recording a change before it
#: is worth remarking on.
#:
#: A working week, not the fortnight `DUE_SOON_DAYS` uses, because the rule that
#: reads this is not "some things are quiet" - it requires three or more *and*
#: nothing completed in the whole project. Under that conjunction a week is
#: conservative: a team that has finished nothing and touched nothing in seven
#: days is either stopped or not recording, and both are worth a question.
#:
#: A holiday can still trip it, which is why the recommendation asks whether the
#: work stopped or only the updating did rather than asserting either. Stated
#: here so the number is arguable: this is a judgement about how long is too
#: long, not a measurement.
STALE_AFTER_DAYS = 7


def _ratio(part: int, whole: int) -> float:
    """Ratios of an empty set are 0.0, not a division error and not None.

    A project with no QA items has a blocked ratio of zero; rules comparing
    against it must not have to special-case emptiness.
    """
    return round(part / whole, 4) if whole else 0.0


@dataclass(frozen=True)
class DeliveryContext:
    """~30 scalars describing one project at one moment.

    The vocabulary of `rules/tables.py`. Field names here and input fields there
    must match exactly.
    """

    project_id: str
    as_of: str

    # -- Schedule -----------------------------------------------------------
    task_count: int = 0
    tasks_blocked: int = 0
    tasks_done: int = 0
    tasks_not_started: int = 0
    tasks_with_baseline: int = 0
    baseline_coverage: float = 0.0

    #: What a *snapshot* says, for a project whose source carries no baseline,
    #: no dependency edges and no effort - a Jira export being the case this
    #: exists for. Every scalar below is a count over the task rows as they
    #: stand, so none of them needs a second observation to mean something.
    #:
    #: They are deliberately separate from the schedule scalars above rather
    #: than folded into them: `max_propagated_days` is a claim about a *chain*,
    #: and a project with no edges has no chain to make claims about. Saying
    #: "nothing is late" there would be reporting absence of data as absence of
    #: risk, which is the failure this product is built against.
    tasks_in_progress: int = 0
    #: Past its own planned finish and not done, counted against `as_of` rather
    #: than today: the analysis reflects the scan it was built from.
    tasks_overdue: int = 0
    #: Due inside `DUE_SOON_DAYS` and not done.
    tasks_due_soon: int = 0
    #: Distinct assignees on the task rows. One means every cross-project
    #: contention signal is structurally unavailable, not that there is none.
    distinct_owners: int = 0
    #: Open tasks the *source system* has not recorded a change to in
    #: `STALE_AFTER_DAYS`, and the age of the stalest.
    #:
    #: Sourced from Jira's own `Updated`, which is a claim the vendor makes -
    #: not from `state_changes`, which is what we observed between two scans.
    #: They are kept apart on purpose: the second is evidence, the first is
    #: testimony. Testimony is all a single export has, because there is no
    #: earlier scan to diff it against.
    tasks_stale: int = 0
    stalest_task_days: int = 0
    #: The most open tasks sharing a single due date, and which date that is.
    #:
    #: A schedule claim that needs no baseline and no edges: if half the project
    #: is dated the same day, the dates are a deadline everybody was handed
    #: rather than a sequence anybody worked out. That is visible in a snapshot,
    #: which is what makes it worth computing for a source that carries nothing
    #: else.
    tasks_on_busiest_due_date: int = 0
    busiest_due_date: str = ""
    #: Hours between `generated_at` and the most recent successful sync of any
    #: source, or -1 when nothing has ever synced. Not the age of the events
    #: the data describes - the demo timeline is fixed in the past by design -
    #: but of the sync itself. Feeds `intelligence.confidence`; -1 rather than
    #: None because ZEN compares numbers, and a rule can test `< 0` for "never
    #: synced" the same way it tests any other threshold.
    data_age_hours: float = -1.0
    #: Tasks whose plan contradicts its own dependencies.
    tasks_inconsistent: int = 0
    #: The worst single case of slip the sheet does not yet show.
    max_propagated_days: int = 0
    #: Slip already typed into the sheet by a human.
    max_recorded_slip_days: int = 0
    project_slip_days: int = 0
    milestones_at_risk: int = 0
    avg_progress: float = 0.0

    # -- QA -----------------------------------------------------------------
    qa_count: int = 0
    qa_blocked: int = 0
    qa_blocked_ratio: float = 0.0
    qa_newly_blocked: int = 0

    # -- Dependencies -------------------------------------------------------
    edges_total: int = 0
    edges_stated: int = 0
    edges_inferred: int = 0
    edges_dropped: int = 0
    #: True when the schedule conclusion changes if inferred edges are removed.
    depends_on_inferred_edges: bool = False

    # -- Causality ----------------------------------------------------------
    chain_count: int = 0
    chains_dependency_backed: int = 0
    chains_exact: int = 0
    #: The single most defensible chain found, as a template id. Empty when none.
    strongest_chain_template: str = ""

    # -- Data quality -------------------------------------------------------
    changes_total: int = 0
    changes_exact: int = 0
    changes_bounded: int = 0
    changes_low_confidence: int = 0
    rows_rejected: int = 0
    #: Ratio of changes we refused to build on. High means the sheet needs work,
    #: and a PM is entitled to know the analysis ran on partial data.
    low_confidence_ratio: float = 0.0

    # -- Cross-project contention (Channel 1) -------------------------------
    #: Whether this project was analysed inside its program at all. A project
    #: analysed without program context has **unknown** contention, not zero,
    #: and the two must not produce the same screen - so every rule that speaks
    #: about contention requires this to be true first.
    has_program_context: bool = False
    #: `contention_pressure` on this project, in effort-days: its apportioned
    #: share of the excess demand on people it shares with other projects. The
    #: conserved quantity, and therefore the one that is safe to sum.
    contention_pressure_days: float = 0.0
    #: How many shared people are over-committed in the assessed window.
    contention_people: int = 0
    #: Worst single `Δ` among this project's shortfalls, in working days. A
    #: **scenario**, not a finding: it holds only if the shortfall lands in a
    #: later window with free capacity, and it is not additive across victims or
    #: across windows. Never rendered without its absorption assumption.
    contention_delay_days: float = 0.0
    #: The **worst single person-month** of overtime among this project's
    #: contended people: what holding the date would cost that person, in that
    #: month, if they absorbed their whole shortfall. Deliberately not derivable
    #: from `contention_pressure_days` - that is this project's share summed over
    #: every person and month, while this is one person in one month - so the two
    #: must never be presented as a conversion of each other. An earlier headline
    #: did exactly that and read "21.8 effort-days, which is 84 overtime hours",
    #: which is not an arithmetic relationship that holds.
    contention_overtime_hours: float = 0.0
    contention_breaches_overtime_limit: bool = False
    #: The monthly overtime ceiling the check above compared against. A context
    #: field rather than a literal in the headline because the rule table forbids
    #: digits in prose - every number a finding states is substituted from the
    #: record the rule fired on, so a headline can never quote a limit the
    #: computation did not actually use.
    contention_overtime_limit_hours: float = 0.0
    #: Which mode apportioned the excess - 'proportional' when no trustworthy
    #: priority order exists (the default), 'priority' when one does. Carried
    #: because the two are different computations and a PM disputing a number
    #: needs to know which ran.
    contention_mode: str = ""

    #: Not part of the rule vocabulary - carried for the assembler.
    notes: tuple[str, ...] = field(default=())

    def as_record(self) -> dict:
        """The flat record handed to ZEN. The only supported way to build one."""
        record = asdict(self)
        record.pop("notes", None)
        return record


def _status_of(row) -> str:
    return (getattr(row, "status", None) or "").casefold()


def build_context(
    *,
    project_id: str,
    as_of: datetime | date,
    schedule: ScheduleGraph,
    impact: ImpactReport,
    chains: Sequence[CausalChain] = (),
    changes: Sequence = (),
    qa_items: Sequence = (),
    edges: Sequence[EdgeRecord] = (),
    rows_rejected: int = 0,
    stated_only_impact: ImpactReport | None = None,
    newly_blocked_qa: int = 0,
    data_age_hours: float = -1.0,
    program: "ProgramContext | None" = None,
    source_ids: Sequence[str] = (),
    owners: Sequence[str | None] = (),
) -> DeliveryContext:
    """Aggregate one project into the scalars the rules compare.

    `stated_only_impact` is the same forward pass run without inferred edges. When
    it disagrees with `impact`, the schedule conclusion rests on edges we derived
    rather than edges a human wrote, and `depends_on_inferred_edges` records that
    so a rule can soften the claim instead of a human having to notice.

    `program` is the cross-project half. Omitted, every contention scalar stays
    at its zero **and** `has_program_context` stays false, which is the honest
    encoding: a project analysed alone has unknown contention, not none.
    `source_ids` is how this project's allocations are found - every id it may
    have been filed under, not just the canonical one.

    `owners` is the assignee of each task row. Passed in rather than read off
    `schedule.tasks` because `TaskNode` is deliberately reduced to what
    scheduling needs, and who a task belongs to is not that - widening it would
    put a field in the scheduling type that the scheduler never reads.
    """
    tasks = list(schedule.tasks.values())
    projections = list(impact.projections.values())

    progresses = [
        float(p) for p in (getattr(t, "progress", None) for t in tasks) if p is not None
    ]

    propagated = [p.propagated_days or 0 for p in projections]
    recorded = [p.recorded_slip_days or 0 for p in projections]

    qa_blocked = sum(1 for q in qa_items if _status_of(q) in BLOCKED_STATES)
    low_confidence = sum(
        1 for c in changes if getattr(c, "identity_confidence", "high") == "low"
    )
    with_baseline = sum(1 for t in tasks if t.baseline_end is not None)

    #: Counted against `as_of` - the scan the analysis reflects - and not
    #: against today. The demo timeline is fixed in the past by design, so
    #: "overdue" measured from the wall clock would report every demo task as
    #: late and mean nothing.
    _today = as_of.date() if isinstance(as_of, datetime) else as_of
    _soon = _today + timedelta(days=DUE_SOON_DAYS)
    _open_tasks = [t for t in tasks if _status_of(t) not in DONE_STATES]
    _overdue = sum(
        1 for t in _open_tasks if t.planned_end is not None and t.planned_end < _today
    )
    _due_soon = sum(
        1
        for t in _open_tasks
        if t.planned_end is not None and _today <= t.planned_end <= _soon
    )
    #: Open tasks whose source-reported update is older than the window. Counted
    #: only where the source actually reports one - a blank means "this tool
    #: does not say", which is not the same as "has not moved", and counting it
    #: as stale would make every hand-kept spreadsheet look abandoned.
    _ages = [
        (_today - t.source_updated_at).days
        for t in _open_tasks
        if getattr(t, "source_updated_at", None) is not None
        and t.source_updated_at <= _today
    ]
    _stale = [age for age in _ages if age >= STALE_AFTER_DAYS]

    #: Counted over *open* tasks only: a cluster of dates that have all been met
    #: is a delivered milestone, not a pile-up.
    _by_due: dict = {}
    for _task in _open_tasks:
        if _task.planned_end is not None:
            _by_due[_task.planned_end] = _by_due.get(_task.planned_end, 0) + 1
    _busiest = max(_by_due.items(), key=lambda kv: (kv[1], kv[0]), default=None)

    dependency_backed = sum(
        1
        for c in chains
        if c.link in (LinkBasis.DEPENDENCY_EDGE, LinkBasis.DEPENDENCY_PATH)
    )

    depends_on_inferred = bool(
        stated_only_impact is not None
        and stated_only_impact.project_end_projected != impact.project_end_projected
    )

    ids = tuple(source_ids) or (project_id,)
    pressure = program.pressure_for(ids) if program is not None else 0.0
    mine = program.results_for(ids) if program is not None else ()
    # Worst single Δ, never a sum: summing Δ across victims re-creates exactly
    # the replication error the apportionment exists to remove.
    worst_delay = max(
        (s.delay_days for r in mine for s in r.shortfalls if s.project_id in set(ids)),
        default=0.0,
    )
    overtime = max(
        (r.absorption.overtime_hours for r in mine if r.absorption is not None),
        default=0.0,
    )
    breaches = any(
        r.absorption is not None and r.absorption.breaches_monthly_limit for r in mine
    )

    return DeliveryContext(
        project_id=project_id,
        as_of=as_of.isoformat(),
        task_count=len(tasks),
        tasks_blocked=sum(1 for t in tasks if _status_of(t) in BLOCKED_STATES),
        tasks_in_progress=sum(1 for t in tasks if _status_of(t) in IN_PROGRESS_STATES),
        tasks_overdue=_overdue,
        tasks_due_soon=_due_soon,
        distinct_owners=len({(o or "").strip() for o in owners if (o or "").strip()}),
        tasks_stale=len(_stale),
        stalest_task_days=max(_ages, default=0),
        tasks_on_busiest_due_date=_busiest[1] if _busiest else 0,
        busiest_due_date=_busiest[0].isoformat() if _busiest else "",
        tasks_done=sum(1 for t in tasks if _status_of(t) in DONE_STATES),
        tasks_not_started=sum(1 for t in tasks if _status_of(t) == "not started"),
        tasks_with_baseline=with_baseline,
        baseline_coverage=_ratio(with_baseline, len(tasks)),
        data_age_hours=data_age_hours,
        tasks_inconsistent=len(impact.inconsistent()),
        max_propagated_days=max(propagated, default=0),
        max_recorded_slip_days=max(recorded, default=0),
        project_slip_days=impact.project_slip_days or 0,
        milestones_at_risk=len(impact.affected_milestones),
        avg_progress=round(sum(progresses) / len(progresses), 2) if progresses else 0.0,
        qa_count=len(qa_items),
        qa_blocked=qa_blocked,
        qa_blocked_ratio=_ratio(qa_blocked, len(qa_items)),
        qa_newly_blocked=newly_blocked_qa,
        edges_total=schedule.edge_count,
        edges_stated=sum(1 for e in edges if e.is_stated),
        edges_inferred=sum(1 for e in edges if not e.is_stated),
        edges_dropped=len(schedule.dropped),
        depends_on_inferred_edges=depends_on_inferred,
        chain_count=len(chains),
        chains_dependency_backed=dependency_backed,
        chains_exact=sum(1 for c in chains if c.is_exact),
        strongest_chain_template=chains[0].template_id if chains else "",
        changes_total=len(changes),
        changes_exact=sum(
            1 for c in changes if getattr(c, "precision", "") == "exact"
        ),
        changes_bounded=sum(
            1 for c in changes if getattr(c, "precision", "") == "bounded"
        ),
        changes_low_confidence=low_confidence,
        rows_rejected=rows_rejected,
        low_confidence_ratio=_ratio(low_confidence, len(changes)),
        has_program_context=program is not None,
        contention_pressure_days=round(pressure, 2),
        # Distinct people, not results: `assess_periods` returns one result per
        # person per month, so counting rows would report one contended person
        # in four months as four people.
        contention_people=len({r.person for r in mine}),
        contention_delay_days=round(worst_delay, 2),
        contention_overtime_hours=round(overtime, 1),
        contention_breaches_overtime_limit=breaches,
        contention_overtime_limit_hours=(
            OVERTIME_MONTHLY_LIMIT if program is not None else 0.0
        ),
        contention_mode=(mine[0].mode if mine else ""),
    )
