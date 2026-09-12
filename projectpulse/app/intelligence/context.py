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
from datetime import date, datetime
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
