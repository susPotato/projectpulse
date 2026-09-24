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

from collections.abc import Mapping, Sequence
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

#: Work that will not happen: cancelled, rejected, a duplicate. Neither
#: delivered nor outstanding, so it is excluded from both counts.
#:
#: Counting it as done would inflate completion; counting it as open would
#: report a cancelled task as overdue forever, which is the trap a real Jira
#: export walks straight into - `Cancelled` and `Won't Do` used to normalise to
#: OTHER and every "is it finished?" test read OTHER as no.
DROPPED_STATES = frozenset({"dropped"})

#: Not outstanding, whatever the reason. The right test for "is this task still
#: someone's problem" - overdue, due soon, stale and in-progress all mean the
#: open set, not the not-done set.
CLOSED_STATES = DONE_STATES | DROPPED_STATES
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

#: Open tasks a person must hold before "all of them overdue" says something
#: about the person rather than about one late task.
OVERLOAD_MIN_OPEN = 3


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
    #: Open tasks carrying no assignee at all.
    #:
    #: Kept apart from `distinct_owners` because the two answer different
    #: questions and one used to be read as the other: a project where three
    #: rows say "Alice" and seven say nothing has `distinct_owners == 1`, and
    #: `single_owner_project` reported that as "all ten tasks are assigned to
    #: one person" - which is false about seven of them. Counted over open
    #: tasks only: nobody needs an owner for work that is finished.
    tasks_unowned: int = 0
    #: Open, past its own planned finish, and carrying no assignee.
    #:
    #: The conjunction rather than either half, because the halves are already
    #: reported and neither is this. An overdue task with an owner has someone
    #: who can be asked about it; an overdue task with nobody has no one, and
    #: it is the second that will still be overdue next month. Fires only where
    #: the source carries both a date and an assignee column with content - a
    #: backlog with no dates produces 0 here and that is the honest answer, not
    #: a clean bill of health.
    tasks_overdue_unowned: int = 0
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
    #: How far past its own date the latest open task is, in days against
    #: `as_of`. The number `tasks_overdue` leaves out and a PM asks first:
    #: thirty-five tasks a day late and thirty-five tasks a month late are not
    #: the same conversation.
    worst_overdue_days: int = 0
    #: Due within `DUE_SOON_DAYS` and not even started - the at-risk-next list,
    #: narrower than `tasks_due_soon` because started work at least has someone
    #: on it.
    tasks_due_soon_not_started: int = 0

    # -- People -----------------------------------------------------------
    #: The person with the most overdue open work (ties broken by open work),
    #: and what they hold. A name is a field like `busiest_due_date` is: a rule
    #: may only say it by substituting it, never by typing it.
    top_owner: str = ""
    top_owner_open: int = 0
    top_owner_overdue: int = 0
    #: The person holding the most tasks overall, open or not, and their share
    #: of the project - the single-point-of-failure question, which is about
    #: who knows the work rather than who is late on it.
    busiest_owner: str = ""
    busiest_owner_tasks: int = 0
    busiest_owner_share: float = 0.0
    #: People with at least `OVERLOAD_MIN_OPEN` open tasks, every one of them
    #: overdue. Not "busy" - underwater: nothing they hold is on time.
    owners_underwater: int = 0

    # -- Tracker against code (the traceability run) ------------------------
    #: Whether a traceability run with verdicts exists for this project. Every
    #: rule below requires it: with no run, "no mismatch" would be absence of
    #: evidence read as evidence of absence.
    trace_available: bool = False
    #: Closed in the tracker, and the model reading the code says it does not
    #: do what the ticket claims.
    trace_done_contradicted: int = 0
    #: Closed in the tracker, and nothing in the code could be found to support
    #: it. Weaker than contradicted: "could not confirm", not "refuted".
    trace_done_unverified: int = 0
    #: Still open in the tracker, and the code already implements it - either
    #: finished work nobody closed, or a ticket nobody needs.
    trace_open_built: int = 0
    #: The code area (the leading directories of a ticket's strongest
    #: candidate file) holding the most overdue open tickets, and how many.
    #: The tracker's own grouping is often empty - CoWorkLocal has no
    #: components on any issue - so the area comes from where the code is.
    worst_area: str = ""
    worst_area_overdue: int = 0
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
    #: The two causes `max_propagated_days` pools, apart. A task whose own
    #: start is after its own due date is inconsistent with *itself* - a typo,
    #: not a schedule problem - and reporting it as "dated earlier than its
    #: dependencies allow" was false on a project with no dependencies at all.
    tasks_dated_backwards: int = 0
    worst_dated_backwards_days: int = 0
    tasks_dependency_inconsistent: int = 0
    max_dependency_slip_days: int = 0
    #: Slip already typed into the sheet by a human.
    max_recorded_slip_days: int = 0
    project_slip_days: int = 0
    milestones_at_risk: int = 0
    avg_progress: float = 0.0

    # -- QA -----------------------------------------------------------------
    #: Hours logged against hours planned, both from columns a person filled in
    #: on the worklog.
    #:
    #: The *only* effort comparison this product will make. `api/schemas/team.py`
    #: sets out why: productivity as output-per-effort needs an output measure,
    #: and `progress` is self-reported, so a ratio built on it would present a
    #: claim as a measurement. Logged against planned is two real columns and
    #: its derivation can be shown, which is what makes it sayable at all.
    hours_logged: float = 0.0
    hours_planned: float = 0.0
    #: `hours_logged / hours_planned`, or 0.0 when nothing was planned - an
    #: overrun against no plan is not an overrun, it is an absent plan, and the
    #: rules must not be able to read the second as the first.
    effort_ratio: float = 0.0

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


def shift_date(value: date, days: int) -> date:
    """`value` moved by `days`, clamped to what a `date` can represent.

    `date.max + timedelta(days=1)` raises rather than saturating, so any date
    arithmetic on a value that came from outside needs this. A source can hand
    us 9999-12-31 - a real "no due date" sentinel in some trackers and a trivial
    thing to mistype - and an unclamped `+` then takes down whichever page did
    the arithmetic with a 500.

    Saturating is the right answer for both callers: each wants a window bound,
    and a bound already at the end of representable time does not need to go
    further.
    """
    try:
        return value + timedelta(days=days)
    except OverflowError:
        return date.max if days > 0 else date.min


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
    owners: Mapping[str, str | None] | None = None,
    trace: Mapping | None = None,
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

    `trace` is `trace_facts(...)` from `app.intelligence.tracefacts`: what the
    project's traceability run says about closed and open tickets. Passed in,
    already counted, because the run is files on disk and this module reads
    nothing - omitted, `trace_available` stays false and every tracker-against-
    code rule stays silent rather than reporting a clean match.

    `owners` maps a task's entity id to its assignee. Passed in rather than
    read off `schedule.tasks` because `TaskNode` is deliberately reduced to
    what scheduling needs, and who a task belongs to is not that - widening it
    would put a field in the scheduling type that the scheduler never reads.

    A *mapping*, not the parallel list it used to be. The list came from its
    own query with no `ORDER BY`, so it was only ever safe to count distinct
    values out of - which is all anything did with it. The moment a rule needs
    to know whether *this* overdue task has an owner, position-matching two
    independent result sets is a bug waiting for a query planner to change its
    mind, and it would have been a silent one.
    """
    tasks = list(schedule.tasks.values())
    projections = list(impact.projections.values())

    progresses = [
        float(p) for p in (getattr(t, "progress", None) for t in tasks) if p is not None
    ]

    propagated = [p.propagated_days or 0 for p in projections]
    recorded = [p.recorded_slip_days or 0 for p in projections]

    qa_blocked = sum(1 for q in qa_items if _status_of(q) in BLOCKED_STATES)

    _logged = sum(float(getattr(q, "hours_spent", None) or 0) for q in qa_items)
    _planned = sum(float(getattr(q, "estimate_hours", None) or 0) for q in qa_items)
    low_confidence = sum(
        1 for c in changes if getattr(c, "identity_confidence", "high") == "low"
    )
    with_baseline = sum(1 for t in tasks if t.baseline_end is not None)

    #: Counted against `as_of` - the scan the analysis reflects - and not
    #: against today. The demo timeline is fixed in the past by design, so
    #: "overdue" measured from the wall clock would report every demo task as
    #: late and mean nothing.
    _today = as_of.date() if isinstance(as_of, datetime) else as_of
    _soon = shift_date(_today, DUE_SOON_DAYS)
    _open_tasks = [t for t in tasks if _status_of(t) not in CLOSED_STATES]
    _by_owner = dict(owners or {})

    def _unowned(task) -> bool:
        return not (_by_owner.get(task.entity_id) or "").strip()

    _overdue_tasks = [
        t for t in _open_tasks if t.planned_end is not None and t.planned_end < _today
    ]
    _overdue = len(_overdue_tasks)
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

    _worst_overdue = max(
        ((_today - t.planned_end).days for t in _overdue_tasks), default=0
    )
    _due_soon_not_started = sum(
        1
        for t in _open_tasks
        if t.planned_end is not None
        and _today <= t.planned_end <= _soon
        and _status_of(t) not in IN_PROGRESS_STATES
        and _status_of(t) not in BLOCKED_STATES
    )

    # People. Counted per assignee over the task rows, open work first.
    _open_by: dict[str, int] = {}
    _overdue_by: dict[str, int] = {}
    _all_by: dict[str, int] = {}
    _overdue_ids = {t.entity_id for t in _overdue_tasks}
    for _task in tasks:
        _who = (_by_owner.get(_task.entity_id) or "").strip()
        if not _who:
            continue
        _all_by[_who] = _all_by.get(_who, 0) + 1
        if _status_of(_task) not in CLOSED_STATES:
            _open_by[_who] = _open_by.get(_who, 0) + 1
            if _task.entity_id in _overdue_ids:
                _overdue_by[_who] = _overdue_by.get(_who, 0) + 1
    _top = max(
        _open_by,
        key=lambda w: (_overdue_by.get(w, 0), _open_by[w], w),
        default="",
    )
    _busiest_owner = max(_all_by, key=lambda w: (_all_by[w], w), default="")
    _underwater = sum(
        1
        for w, n in _open_by.items()
        if n >= OVERLOAD_MIN_OPEN and _overdue_by.get(w, 0) == n
    )

    # The two causes of an inconsistent plan, apart: a task with no driving
    # predecessor that is still inconsistent is inconsistent with itself.
    _backwards = [p for p in impact.inconsistent() if p.driving_predecessor is None]
    _dependency = [p for p in impact.inconsistent() if p.driving_predecessor is not None]

    _trace = dict(trace or {})

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
        distinct_owners=len(
            {(o or "").strip() for o in _by_owner.values() if (o or "").strip()}
        ),
        tasks_unowned=sum(1 for t in _open_tasks if _unowned(t)),
        tasks_overdue_unowned=sum(1 for t in _overdue_tasks if _unowned(t)),
        tasks_stale=len(_stale),
        stalest_task_days=max(_ages, default=0),
        tasks_on_busiest_due_date=_busiest[1] if _busiest else 0,
        busiest_due_date=_busiest[0].isoformat() if _busiest else "",
        worst_overdue_days=_worst_overdue,
        tasks_due_soon_not_started=_due_soon_not_started,
        top_owner=_top,
        top_owner_open=_open_by.get(_top, 0),
        top_owner_overdue=_overdue_by.get(_top, 0),
        busiest_owner=_busiest_owner,
        busiest_owner_tasks=_all_by.get(_busiest_owner, 0),
        busiest_owner_share=_ratio(_all_by.get(_busiest_owner, 0), len(tasks)),
        owners_underwater=_underwater,
        tasks_dated_backwards=len(_backwards),
        worst_dated_backwards_days=max(
            (p.propagated_days or 0 for p in _backwards), default=0
        ),
        tasks_dependency_inconsistent=len(_dependency),
        max_dependency_slip_days=max(
            (p.propagated_days or 0 for p in _dependency), default=0
        ),
        trace_available=bool(_trace.get("available")),
        trace_done_contradicted=int(_trace.get("done_contradicted", 0)),
        trace_done_unverified=int(_trace.get("done_unverified", 0)),
        trace_open_built=int(_trace.get("open_built", 0)),
        worst_area=str(_trace.get("worst_area", "")),
        worst_area_overdue=int(_trace.get("worst_area_overdue", 0)),
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
        hours_logged=round(_logged, 2),
        hours_planned=round(_planned, 2),
        effort_ratio=round(_logged / _planned, 3) if _planned else 0.0,
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
