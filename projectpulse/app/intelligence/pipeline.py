"""Run the whole intelligence layer for one project and return one bundle.

The only module in `intelligence/` that touches a session. Everything it calls is
pure, which is why the hard parts are testable in milliseconds and this file is
mostly plumbing.

The order is not arbitrary - each stage needs the one before it:

1. **Load** tasks, edges, QA items and changes for the project.
2. **Graph** the dependencies, twice: once with every edge, once with only the
   edges a human stated. The pair is what lets a finding admit that its date rests
   on an inference.
3. **Project** the schedule forward to find slip the sheet does not yet show.
4. **Chains** - match provably-ordered pairs against the named hypotheses, using
   the graph to answer what connects any two entities.
5. **Context** - flatten everything to ~30 scalars.
6. **Rules** - evaluate. This is the only stage a delivery manager edits.
7. **Assemble** - substitute numbers, attach evidence, and narrate.

8. **Narrate** - `narration.client.narrate`, which renders the deterministic
   template and then, only if a `narrator` was supplied, asks a model to phrase
   the same findings and keeps the draft if it survives the validator. This
   stage cannot fail: a missing model, a rejected draft or a dead socket all
   leave the template in place with a reason recorded beside it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.api.schemas.insight import DeliveryConfidence, EvidenceRef, InsightBundle
from app.intelligence import confidence as confidence_calc
from app.intelligence.assembler import build_bundle, entity_label
from app.intelligence.context import build_context, shift_date
from app.intelligence.rules.engine import RulesEngine
from app.intelligence.rules.tables import DEFAULT_TABLE, RuleTable
from app.intelligence.schedule.graph import (
    DependencyLinks,
    EdgeRecord,
    TaskNode,
    build_graph,
)
from app.intelligence.schedule.impact import project_schedule
from app.intelligence.temporal.chains import find_chains
from app.models.domain import Dependency, QaItem, StateChange, Task
from app.models.raw import RAW_TABLES
from app.models.sync import RawReject, SyncRun

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from app.intelligence.contention import ProgramContext
    from app.narration.client import Drafter

log = logging.getLogger(__name__)


class _Unset:
    """Sentinel distinguishing "resolve the program" from "there is none".

    `analyze_project(program=None)` has to mean *analyse this project alone* -
    some callers genuinely want that, and a project that belongs to no program
    reports exactly that way. So "the caller did not say" needs a third value,
    or the correct default (analyse a project inside its program) cannot be the
    default without taking the opt-out away.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


def _known_fields() -> set[str]:
    from dataclasses import fields

    from app.intelligence.context import DeliveryContext

    return {f.name for f in fields(DeliveryContext)} - {"notes"}


def _hours_since_last_sync(session, generated_at: datetime) -> float | None:
    """How long ago *any* source last synced successfully, or `None`.

    Global rather than scoped to one project: `SyncRun` does not carry a
    project id (a run touches whichever sheets its source watches), and a
    portfolio of this size makes "the whole app's last successful sync" a
    reasonable stand-in. A per-project watermark is a real gap - `sync_state`
    is keyed by scope, not project - and worth closing before this formula
    is trusted for a multi-project deployment.
    """
    finished = session.scalar(
        select(func.max(SyncRun.finished_at)).where(SyncRun.status == "success")
    )
    if finished is None:
        return None
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    return (generated_at - finished).total_seconds() / 3600


def load_owners(session, project_ids: Sequence[str]) -> dict[str, str | None]:
    """Who each task belongs to, keyed by the task's id.

    Its own read because `load_tasks` returns `TaskNode`, which is deliberately
    reduced to what scheduling needs and carries no assignee. Widening that type
    would put a field in the scheduling model the scheduler never looks at; a
    named query costs one cheap column scan and says what it is for.

    Selects the id alongside the assignee, where it used to select the assignee
    alone. A bare column with no `ORDER BY` can be counted but not joined, and
    "is *this* overdue task unowned" is a join - done by position it would have
    agreed with `load_tasks` on most runs and quietly disagreed on some.
    """
    return {
        task_id: assignee
        for task_id, assignee in session.execute(
            select(Task.id, Task.assignee).where(Task.project_id.in_(list(project_ids)))
        ).all()
    }


def load_tasks(session, project_ids: Sequence[str]) -> list[TaskNode]:
    rows = session.scalars(
        select(Task).where(Task.project_id.in_(list(project_ids)))
    ).all()

    nodes: list[TaskNode] = []
    for task in rows:
        nodes.append(
            TaskNode(
                entity_id=task.id,
                project_id=task.project_id,
                title=task.title,
                status=task.status,
                start_date=task.start_date,
                # `due_date` is where the convertor puts the planned finish.
                planned_end=task.due_date,
                baseline_end=task.baseline_end,
                # The real foreign key, now that the convertor creates the rows.
                milestone_id=task.milestone_id,
                raw_data_id=task.raw_data_id,
                source_updated_at=task.source_updated_at,
            )
        )
    return nodes


def load_edges(session, project_ids: Sequence[str]) -> list[EdgeRecord]:
    rows = session.scalars(
        select(Dependency).where(Dependency.project_id.in_(list(project_ids)))
    ).all()
    return [
        EdgeRecord(
            predecessor_id=row.predecessor_id,
            successor_id=row.successor_id,
            dep_type=row.dep_type,
            lag_days=row.lag_days or 0,
            source=row.source,
            raw_data_id=row.raw_data_id,
        )
        for row in rows
    ]


def evidence_resolver(session):
    """Turn a `_raw_data_id` into something a PM can look at.

    The raw tables all share a shape, so the lookup is generic - which is the
    whole reason `_raw_data_table` exists rather than a hardcoded join.
    """
    cache: dict[int, EvidenceRef] = {}

    def resolve(raw_id: int) -> EvidenceRef | None:
        if raw_id in cache:
            return cache[raw_id]
        for name, model in RAW_TABLES.items():
            row = session.get(model, raw_id)
            if row is not None:
                ref = EvidenceRef(
                    raw_data_id=raw_id,
                    raw_table=name,
                    url=row.url,
                    remark=row.input,
                    label=name.replace("_raw_", "").replace("_", " "),
                )
                cache[raw_id] = ref
                return ref
        return None

    return resolve


def analyze_project(
    session,
    *,
    project_id: str,
    also: Sequence[str] = (),
    as_of: datetime | None = None,
    generated_at: datetime | None = None,
    table: RuleTable = DEFAULT_TABLE,
    engine: RulesEngine | None = None,
    narrator: "Drafter | None" = None,
    program: "ProgramContext | None | _Unset" = UNSET,
) -> InsightBundle:
    """Everything the insight screen needs for one project.

    `also` names the *same delivery project* as it appears in other source
    systems. One project tracked in both Jira and a spreadsheet produces two
    `projects` rows with different ids, and without this they would be analysed as
    two unrelated projects - a Jira event could never be shown to explain a
    spreadsheet observation, which is precisely the cross-source claim neither
    source can make alone. Every entity is re-pointed at `project_id` below so
    the causal engine treats them as one.

    `program` is the cross-project half, and it is the design's stated shape for
    the program layer: *a context object passed into this same function*, not a
    second computation over the same rows. That is what makes a Program rollup
    unable to disagree with a project's own page - the rollup is this function's
    output, folded up.

    **Left unset it is resolved here**, from the project's own program. That is
    the default because the alternative made the two screens contradict each
    other: the portfolio built a context and banded HRMS red for contention,
    while HRMS's own page - one of eight call sites that did not - showed no
    contention finding at all. Passing `None` explicitly still means "analyse
    this project alone", and then the contention scalars stay zero with
    `has_program_context` false, so a project analysed alone reports contention
    as *unknown* rather than as none.

    `narrator` is the one optional input. Without it the narrative is the
    deterministic template, which is complete; with it a model is asked to
    phrase the same findings and the result is used only if it survives the
    validator. Either way a narrative comes back, so no caller has to handle
    the model being absent.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    project_ids = [project_id, *also]

    tasks = load_tasks(session, project_ids)
    edges = load_edges(session, project_ids)
    qa_items = session.scalars(
        select(QaItem).where(QaItem.project_id.in_(project_ids))
    ).all()

    entity_ids = {t.entity_id for t in tasks} | {q.id for q in qa_items}
    changes: Sequence[StateChange] = (
        session.scalars(
            select(StateChange)
            .where(StateChange.entity_id.in_(entity_ids))
            .order_by(StateChange.occurred_at)
        ).all()
        if entity_ids
        else []
    )

    if as_of is None:
        as_of = max((c.occurred_at for c in changes), default=generated_at)

    # Both graphs. The difference between their conclusions is itself a finding.
    schedule = build_graph(tasks, edges)
    impact = project_schedule(schedule)
    stated_impact = project_schedule(build_graph(tasks, edges, stated_only=True))

    # Canonicalise: every entity from every source belongs to one delivery
    # project, whatever its own source system called it.
    canonical = {q.id: project_id for q in qa_items}
    canonical.update({t.entity_id: project_id for t in tasks})
    links = DependencyLinks(schedule, project_of=canonical)
    chains = find_chains(changes, links)

    # Scoped to this project. A portfolio-wide count would tell a PM that another
    # team's unparseable rows were theirs.
    rejected = (
        session.scalar(
            select(func.count())
            .select_from(RawReject)
            .where(RawReject.project_id.in_(project_ids))
        )
        or 0
    )

    hours_since_sync = _hours_since_last_sync(session, generated_at)

    if isinstance(program, _Unset):
        from app.scope import program_for

        resolved = program_for(project_id)
        program = program_context(session, resolved) if resolved else None

    context = build_context(
        project_id=project_id,
        as_of=as_of,
        schedule=schedule,
        impact=impact,
        chains=chains,
        changes=changes,
        qa_items=qa_items,
        edges=edges,
        rows_rejected=rejected,
        stated_only_impact=stated_impact,
        data_age_hours=-1.0 if hours_since_sync is None else hours_since_sync,
        program=program,
        source_ids=project_ids,
        owners=load_owners(session, project_ids),
    )

    engine = engine or RulesEngine(table, known_fields=_known_fields())
    hits = engine.evaluate(context.as_record())

    bundle = build_bundle(
        project_id=project_id,
        as_of=as_of,
        generated_at=generated_at,
        context=context,
        hits=hits,
        chains=chains,
        impact=impact,
        evidence_for=evidence_resolver(session),
    )

    # Narration last, because it reads the finished bundle. `cached_narrate`
    # renders the deterministic template first and only replaces it with a
    # model draft that passed every stage - so this assignment cannot leave
    # the page empty, whatever the model or the network did. A model is asked
    # at most once per unique set of facts; see `narration/cache.py`.
    from app.narration.cache import cached_narrate

    outcome = cached_narrate(session, bundle, project_id=project_id, drafter=narrator)
    bundle.narrative = outcome.narrative
    bundle.narration_source = outcome.source  # type: ignore[assignment]
    bundle.narration_fallback_reason = outcome.fallback_reason

    result = confidence_calc.compute(
        coverage=context.baseline_coverage, hours_since_sync=hours_since_sync
    )
    bundle.delivery_confidence = DeliveryConfidence(
        band=result.band,
        score=result.score,
        coverage=result.coverage,
        freshness=result.freshness,
        data_age_hours=result.data_age_hours,
        precedent_available=result.precedent_available,
    )
    return bundle


def explain_project(
    session,
    *,
    project_id: str,
    also: Sequence[str] = (),
) -> "ExplainBundle":
    """The schedule arithmetic for one project, ready to serve.

    Shares `load_tasks` / `load_edges` / `project_schedule` with
    `analyze_project`, so the numbers a PM checks here are the same objects the
    findings were built from rather than a second implementation that could
    drift from them.
    """
    from app.api.schemas.explain import (
        Calc,
        ExplainBundle,
        ForwardStep,
        Operand,
        RefusedEdge,
    )
    from app.intelligence import explain

    project_ids = [project_id, *also]
    tasks = load_tasks(session, project_ids)
    edges = load_edges(session, project_ids)

    schedule = build_graph(tasks, edges)
    impact = project_schedule(schedule)
    stated = project_schedule(build_graph(tasks, edges, stated_only=True))

    qa_items = session.scalars(
        select(QaItem).where(QaItem.project_id.in_(project_ids))
    ).all()

    context = build_context(
        project_id=project_id,
        as_of=impact.project_end_projected or datetime.now(timezone.utc),
        schedule=schedule,
        impact=impact,
        qa_items=qa_items,
        edges=edges,
        stated_only_impact=stated,
    )

    return ExplainBundle(
        project_id=project_id,
        steps=_forward_steps(schedule, impact, ForwardStep, Operand, Calc),
        driving_path=list(impact.driving_path),
        project_end_planned=impact.project_end_planned,
        project_end_projected=impact.project_end_projected,
        project_slip_days=impact.project_slip_days,
        stated_only_end=stated.project_end_projected,
        depends_on_inferred_edges=context.depends_on_inferred_edges,
        edges_stated=context.edges_stated,
        edges_inferred=context.edges_inferred,
        refused_edges=[
            RefusedEdge(
                predecessor=edge.predecessor_id,
                successor=edge.successor_id,
                reason=reason,
            )
            for edge, reason in schedule.dropped
        ],
        scalars=context.as_record(),
    )


#: Which finding categories speak to which heatmap dimension. A category the
#: table does not mention simply does not colour a cell - better than forcing
#: everything into four buckets it was not written for.
_DIMENSION_CATEGORIES = {
    "schedule": ("schedule_risk", "milestone_risk"),
    "quality": ("quality_risk",),
    "qa": ("quality_risk",),
    "evidence": ("evidence_quality", "data_quality"),
    "resource": ("resource_risk",),
}

#: Severity to band. `info` is not a problem, so it reads healthy.
_SEVERITY_BAND = {
    "critical": "critical",
    "high": "critical",
    "medium": "watch",
    "low": "watch",
    "info": "healthy",
}


def _band_for(severities: list[str]) -> str:
    """The worst band among some findings, or healthy if there are none."""
    order = ("critical", "watch", "healthy")
    bands = [_SEVERITY_BAND.get(s, "watch") for s in severities]
    for level in order:
        if level in bands:
            return level
    return "healthy"


def load_allocations(session, program_id: str) -> list["Allocation"]:
    """Every allocation on every project in one program, with a dated window.

    Two things here are the fix for how resource conflict used to be computed.

    **Allocations are found by every source id a project may carry.** The old
    query filtered on canonical ids alone, so a `Resource` row written against
    `jira:Project:1:HRMS` was invisible to HRMS's own rollup - invariant 7, one
    level down from where `scope.source_ids_for` already solves it.

    **A window with no dates is derived, not assumed to be "always".** A
    resource plan that states no period is the common case in a hand-maintained
    sheet, and treating it as unbounded makes every allocation overlap every
    other, which is how a window-free conflict check gets both its false
    positives and its false negatives. The fallback is the project's own
    schedule span - real data, derived from tasks a human dated - and the
    allocation records `window_source='derived_from_schedule'` so a disputed
    finding can say where its dates came from.
    """
    from app.intelligence.contention import Allocation
    from app.models.domain import Resource
    from app.scope import projects_in

    entries = projects_in(program_id)
    if not entries:
        return []

    # Canonical id per source id, so an allocation filed under a paired id is
    # attributed to the delivery project rather than to a second one.
    canonical: dict[str, tuple[str, str]] = {}
    for entry in entries:
        for source_id in entry.source_ids:
            canonical[source_id] = (entry.canonical_id, entry.name)

    spans = _schedule_spans(session, list(canonical))

    rows = session.scalars(
        select(Resource).where(Resource.project_id.in_(list(canonical)))
    ).all()

    out: list[Allocation] = []
    for row in rows:
        project_id, project_name = canonical[row.project_id]
        start, end = row.period_start, row.period_end
        source = "stated"
        if start is None or end is None:
            derived = spans.get(project_id) or spans.get(row.project_id)
            if derived is None:
                # No stated window and no dated tasks to derive one from. The
                # allocation cannot be placed in time, so it is dropped rather
                # than defaulted into the assessed window - a demand invented at
                # a date nobody stated is the confident-zero problem inverted.
                log.info(
                    "dropping undated allocation for %s on %s: no schedule to "
                    "derive a window from",
                    row.resource_name,
                    row.project_id,
                )
                continue
            start, end = derived
            source = "derived_from_schedule"
        out.append(
            Allocation(
                person=row.resource_name,
                project_id=project_id,
                project_name=project_name,
                allocation_percent=float(row.allocation_percent or 0),
                window_start=start,
                window_end=end,
                role=row.role,
                window_source=source,
            )
        )
    return out


def _schedule_spans(session, source_ids: Sequence[str]) -> dict[str, tuple[date, date]]:
    """First start and last finish per project, keyed by canonical-ish id.

    Keyed by the `project_id` the tasks actually carry; the caller maps that
    onto the delivery project. Projects with no dated tasks are absent rather
    than present with a `None`, so the caller's `.get` is the whole check.
    """
    rows = session.execute(
        select(
            Task.project_id,
            func.min(Task.start_date),
            func.max(Task.due_date),
        )
        .where(Task.project_id.in_(list(source_ids)))
        .group_by(Task.project_id)
    ).all()

    spans: dict[str, tuple[date, date]] = {}
    for project_id, start, end in rows:
        start = _as_date(start)
        end = _as_date(end)
        if start is not None and end is not None and end >= start:
            spans[project_id] = (start, end)
    return spans


def _as_date(value) -> date | None:
    """SQLite hands back strings where Postgres hands back dates."""
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def program_context(
    session,
    program_id: str | None,
    *,
    window: "Window | None" = None,
    priority: Sequence[str] | None = None,
) -> "ProgramContext | None":
    """Build the context one program's projects are analysed inside.

    Returns `None` for a project that belongs to no program - which callers must
    pass straight through to `analyze_project(program=None)` so the analysis
    reports contention as unknown rather than as zero.

    The assessed window defaults to the span the program's own schedules cover.
    That is deliberately not "today forward": the demo timeline is fixed in the
    past by design, and a window anchored on the wall clock would report no
    contention at all on data that plainly contains some.

    `priority` selects Mode B. It is not derived from anything in the database
    on purpose - the design's default is that no trustworthy priority order
    exists, because a written one is usually stale and the live one is
    political, so Mode A runs unless a caller states an order explicitly.
    """
    from app.intelligence.contention import (
        ProgramContext,
        Window,
        assess_periods,
        month_periods,
        pressure_by_project,
    )
    from app.models.domain import Program
    from app.scope import find_program, projects_in
    from app.units import units_for

    if program_id is None:
        return None

    row = session.get(Program, program_id)
    declared = find_program(program_id)
    if row is None and declared is None:
        return None

    name = row.name if row is not None else declared.name  # type: ignore[union-attr]
    entries = projects_in(program_id)
    allocations = load_allocations(session, program_id)

    # Assessed month by month, not over one span. A single window covering
    # every allocation would pool demands that never coexist - the false
    # positive the dated windows exist to remove - and would compare a
    # multi-month overtime figure against a monthly statutory ceiling.
    if window is None:
        starts = [a.window_start for a in allocations]
        ends = [a.window_end for a in allocations]
        periods = month_periods(min(starts), max(ends)) if starts and ends else []
    else:
        periods = month_periods(window.start, window.end)

    results = ()
    if periods and allocations:
        results = tuple(
            assess_periods(
                allocations,
                periods,
                program_id=program_id,
                priority=priority,
            )
        )

    return ProgramContext(
        program_id=program_id,
        program_name=name,
        units=units_for(program_id),
        contention=results,
        pressure=pressure_by_project(results),
        project_ids=frozenset(
            source_id for e in entries for source_id in e.source_ids
        ),
    )


def _project_row(session, entry, program: "ProgramContext | None" = None) -> tuple["ProjectRow", str | None]:
    """One project's `ProjectRow`, plus the `program_id` it resolved to.

    Factored out of `portfolio()` so `program_rollup()` can build the same row
    a project's own page would show, filtered to one program, without a
    second and possibly-diverging aggregation. Runs `analyze_project` rather
    than a shortcut: a rollup then cannot disagree with the project view,
    because it *is* the project view, folded up.

    `program` is passed through unchanged. A caller that has one gets rows whose
    bands include cross-project contention; a caller that does not gets rows
    that say so, via `has_program_context`. What no caller gets is a row that
    silently reports zero contention because nobody looked.
    """
    from app.api.schemas.portfolio import DIMENSIONS, ProjectRow
    from app.models.domain import Project

    # `scope` first, the `projects` column only as a fallback. The column is
    # written per source, so reading it directly is what let one program exist
    # twice and put the two source rows of one delivery project in different
    # programs; `scope.program_for` resolves the pairing before answering.
    # The fallback covers a project ingested before this change and not yet
    # migrated - `scripts.migrate_programs` is what removes the need for it.
    program_id = entry.program_id
    if program_id is None:
        project_row = session.get(Project, entry.canonical_id)
        program_id = project_row.program_id if project_row is not None else None

    tasks = load_tasks(session, list(entry.source_ids))
    if not tasks:
        # Known to the portfolio, nothing ingested. `no_data`, never green.
        return (
            ProjectRow(
                project_id=entry.canonical_id,
                name=entry.name,
                source_ids=list(entry.source_ids),
                bands={d: "no_data" for d in DIMENSIONS},
            ),
            program_id,
        )

    bundle = analyze_project(
        session,
        project_id=entry.canonical_id,
        also=list(entry.also),
        program=program,
    )
    edges = load_edges(session, list(entry.source_ids))
    impact = project_schedule(build_graph(tasks, edges))
    context = bundle.context

    severities = [f.severity for f in bundle.findings]
    worst = next(
        (s for s in ("critical", "high", "medium", "low", "info") if s in severities),
        None,
    )
    top = bundle.by_severity()[0] if bundle.findings else None

    row = ProjectRow(
        project_id=entry.canonical_id,
        name=entry.name,
        source_ids=list(entry.source_ids),
        band=_band_for(severities),
        worst_severity=worst,
        findings=len(bundle.findings),
        task_count=len(tasks),
        committed_end=impact.project_end_planned,
        projected_end=impact.project_end_projected,
        days_late=impact.project_slip_days or 0,
        milestones_at_risk=int(context.get("milestones_at_risk") or 0),
        qa_blocked=int(context.get("qa_blocked") or 0),
        qa_count=int(context.get("qa_count") or 0),
        headline=top.headline if top else None,
        depends_on_inferred_edges=bundle.data_quality.depends_on_inferred_edges,
        bands={
            dimension: _band_for(
                [
                    f.severity
                    for f in bundle.findings
                    if f.category in _DIMENSION_CATEGORIES[dimension]
                ]
            )
            for dimension in DIMENSIONS
        },
    )
    return row, program_id


def portfolio(session) -> "PortfolioBundle":
    """Every delivery project in the program, ranked worst first."""
    from datetime import date as _date

    from app.api.schemas.portfolio import PortfolioBundle, ProjectRow
    from app.models.domain import Program
    from app.scope import all_projects

    rows: list[ProjectRow] = []
    #: Which Program each canonical project actually belongs to, so the
    #: header can say so honestly instead of picking an arbitrary row -
    #: `session.scalars(select(Program)).first()` was fine when exactly one
    #: Program existed; it stopped being fine the moment a second one did.
    program_names: set[str] = set()

    #: One context per program, built once and shared by every project in it.
    #: Building it per project would re-run the apportionment for each victim
    #: and, worse, let two projects in one program disagree about the same
    #: person's excess.
    contexts: dict[str, "ProgramContext | None"] = {}

    for entry in all_projects():
        program_id = entry.program_id
        if program_id and program_id not in contexts:
            contexts[program_id] = program_context(session, program_id)
        row, program_id = _project_row(
            session, entry, contexts.get(program_id) if program_id else None
        )
        rows.append(row)
        if program_id:
            program = session.get(Program, program_id)
            if program is not None:
                program_names.add(program.name)

    # Worst first: the screen exists to answer "where do I look today".
    rank = {"critical": 0, "watch": 1, "healthy": 2, "no_data": 3}
    rows.sort(key=lambda r: (rank.get(r.band, 9), -r.days_late, r.name))

    # Exactly one distinct program among these projects: name it, unchanged
    # from today's single-program behavior. More than one (or none resolved):
    # this view is honestly cross-program, so say that rather than guess.
    program_name = next(iter(program_names)) if len(program_names) == 1 else "Portfolio"

    return PortfolioBundle(
        program_name=program_name,
        generated_at=_date.today(),
        projects=rows,
    )


def list_programs(session) -> "ProgramListBundle":
    """Every Program, each with its own ranked projects.

    Backs the Programs list (`Layout_Program` image12) - the single entry
    point into the Program -> Project hierarchy, so a project has to be
    reachable from here directly, not only from within its program's own
    dashboard. A Program with no projects yet - the deliberately-empty
    second seed row - is a legitimate `no_data` row, not an error.
    """
    from datetime import date as _date

    from app.api.schemas.programs import ProgramListBundle, ProgramSummary
    from app.models.domain import Program
    from app.scope import all_programs, all_projects

    rows_by_program: dict[str, list] = {}
    contexts: dict[str, "ProgramContext | None"] = {}
    for entry in all_projects():
        program_id = entry.program_id
        if program_id and program_id not in contexts:
            contexts[program_id] = program_context(session, program_id)
        row, program_id = _project_row(
            session, entry, contexts.get(program_id) if program_id else None
        )
        if program_id:
            rows_by_program.setdefault(program_id, []).append(row)

    # There is no longer a duplicate to suppress here, and that is the point.
    # This loop used to drop an empty program that shared a *name* with a
    # non-empty one, because each convertor built its program id from its own
    # `SOURCE` and so created a second row for the same program. That was
    # name-equality entity resolution in the display layer - the exact fuzzy
    # merge `app/scope.py` exists to avoid - and it hid the duplicate from this
    # list while leaving it reachable at `/api/programs/<orphan id>`. Programs
    # are now keyed source-neutrally (`app/ingest/programs.py`), so an empty
    # program is believed: it is a program nobody has filed a project against.
    # Declared programs *and* materialized ones. `app/scope.py` is where a
    # program is declared - the demo seed, and anything somebody created on this
    # screen - while the `programs` table is where one is materialized, by a
    # collector ingesting into it. A program created a moment ago has no domain
    # row yet and would be invisible here if this read only the table, so the
    # button that creates it would look like it had done nothing.
    declared = {p.program_id: p for p in all_programs()}
    materialized = {p.id: p for p in session.scalars(select(Program)).all()}

    rank = {"critical": 0, "watch": 1, "healthy": 2, "no_data": 3}
    summaries: list[ProgramSummary] = []
    for program_id in {**declared, **materialized}:
        program = materialized.get(program_id)
        entry = declared.get(program_id)
        rows = rows_by_program.get(program_id, [])
        band = min((r.band for r in rows), key=lambda b: rank.get(b, 9), default="no_data")
        ranked_rows = sorted(rows, key=lambda r: (rank.get(r.band, 9), -r.days_late, r.name))
        summaries.append(
            ProgramSummary(
                id=program_id,
                # The declaration wins on name and owner: it is what a person
                # typed, and the domain row may carry a name a collector
                # invented before anybody said otherwise.
                name=(entry.name if entry is not None else program.name),
                owner=(entry.owner if entry is not None else program.owner)
                or (program.owner if program is not None else None),
                status=(entry.status if entry is not None else program.status),
                start_date=program.start_date if program is not None else None,
                end_date=program.end_date if program is not None else None,
                project_count=len(rows),
                band=band,
                projects=ranked_rows,
            )
        )
    summaries.sort(key=lambda p: (-p.project_count, p.name))

    return ProgramListBundle(programs=summaries, generated_at=_date.today())


def program_rollup(session, program_id: str) -> "ProgramRollupBundle | None":
    """One program's cross-project rollup: ranked projects, resources, and
    where the same person is overallocated across more than one of them.

    Returns `None` for an unknown program id so the route can 404 rather than
    render an empty screen that looks like a program with no projects.
    """
    from collections import defaultdict
    from datetime import date as _date

    from app.api.schemas.programs import (
        ProgramRollupBundle,
        ProgramSummary,
        ProjectShortfall,
        ResourceConflict,
        ResourceRow,
    )
    from app.intelligence.contention import normalize_person
    from app.models.domain import Program, Resource
    from app.scope import find_program, projects_in, source_ids_for

    # Declared or materialized - see `list_programs`. A program somebody created
    # a moment ago has no `programs` row until a collector ingests into it, and
    # 404ing it would mean the Programs list linked to a page that does not
    # exist. Unknown to *both* is still a 404, which is what the old program ids
    # correctly get after `scripts.migrate_programs`.
    program = session.get(Program, program_id)
    declared = find_program(program_id)
    if program is None and declared is None:
        return None

    name = declared.name if declared is not None else program.name
    owner = (declared.owner if declared is not None else None) or (
        program.owner if program is not None else None
    )
    status = declared.status if declared is not None else program.status

    # One context for the whole program, built before the project rows so every
    # row is banded against the same apportionment.
    program_ctx = program_context(session, program_id)

    entries = projects_in(program_id)
    rows = []
    #: Every source id -> the delivery project's display name. Keyed by *all*
    #: of a project's ids, not just the canonical one: a `Resource` row filed
    #: against `jira:Project:1:HRMS` belongs to HRMS, and the old query - which
    #: filtered on canonical ids alone - simply did not see it. Invariant 7, one
    #: level below where `scope.source_ids_for` already solves it.
    project_names: dict[str, str] = {}
    for entry in entries:
        row, _ = _project_row(session, entry, program_ctx)
        rows.append(row)
        for source_id in source_ids_for(entry.canonical_id):
            project_names[source_id] = entry.name

    rank = {"critical": 0, "watch": 1, "healthy": 2, "no_data": 3}
    rows.sort(key=lambda r: (rank.get(r.band, 9), -r.days_late, r.name))
    band = min((r.band for r in rows), key=lambda b: rank.get(b, 9), default="no_data")

    project_ids = list(project_names)
    resource_rows_db = (
        session.scalars(
            select(Resource).where(Resource.project_id.in_(project_ids))
        ).all()
        if project_ids
        else []
    )
    resources = [
        ResourceRow(
            resource_name=r.resource_name,
            role=r.role,
            project_id=r.project_id,
            project_name=project_names.get(r.project_id, r.project_id),
            allocation_percent=(
                float(r.allocation_percent) if r.allocation_percent is not None else None
            ),
        )
        for r in resource_rows_db
    ]

    #: Nominal totals, for the label only. Grouped on the normalized name so one
    #: person spelled two ways (full-width, different spacing, different case) is
    #: one person - a raw `group by resource_name` silently halves their load.
    nominal: dict[str, float] = defaultdict(float)
    for r in resources:
        nominal[normalize_person(r.resource_name)] += r.allocation_percent or 0

    # The conflicts are the contention results, not a re-derivation of them.
    # Computing them here from allocation percentages is what let this screen
    # disagree with the project pages about the same overload.
    conflicts = [
        ResourceConflict(
            resource_name=result.person,
            total_allocation_percent=round(nominal[normalize_person(result.person)], 1),
            projects=list(result.projects),
            demand_days=round(result.demand_days, 2),
            supply_days=round(result.supply_days, 2),
            excess_days=round(result.excess_days, 2),
            window_label=result.window.label(),
            working_days=result.working_days,
            mode=result.mode,
            shortfalls=[
                ProjectShortfall(
                    project_id=s.project_id,
                    project_name=s.project_name,
                    effort_days=round(s.effort_days, 2),
                    delay_days=round(s.delay_days, 2),
                )
                for s in result.shortfalls
            ],
            absorption=(
                result.absorption.describe() if result.absorption is not None else ""
            ),
            overtime_hours=(
                round(result.absorption.overtime_hours, 1)
                if result.absorption is not None
                else 0.0
            ),
            breaches_overtime_limit=(
                result.absorption.breaches_monthly_limit
                if result.absorption is not None
                else False
            ),
            notes=list(result.notes),
        )
        for result in (program_ctx.contention if program_ctx is not None else ())
    ]
    # Worst excess first, tie-broken on the name so the order is deterministic
    # (I1) rather than dependent on dict insertion.
    conflicts.sort(key=lambda c: (-c.excess_days, c.resource_name))

    return ProgramRollupBundle(
        program=ProgramSummary(
            id=program_id,
            name=name,
            owner=owner,
            status=status,
            start_date=program.start_date if program is not None else None,
            end_date=program.end_date if program is not None else None,
            project_count=len(rows),
            band=band,
        ),
        projects=rows,
        resources=resources,
        resource_conflicts=conflicts,
        generated_at=_date.today(),
    )


def _single_program_name(session, program_ids) -> str:
    """The program's name when there is exactly one in scope, else "Portfolio".

    A header naming one program while showing several projects' data is a quiet
    lie, and it is the shape `select(Program).first()` produced. Falls back to
    the declaration in `app/scope.py` when the row is not in the database yet, so
    a program that has been set up but not ingested against still has a name.
    """
    from app.models.domain import Program
    from app.scope import find_program

    ids = {pid for pid in program_ids if pid}
    if len(ids) != 1:
        return "Portfolio"
    program_id = next(iter(ids))
    row = session.get(Program, program_id)
    if row is not None:
        return row.name
    declared = find_program(program_id)
    return declared.name if declared is not None else "Portfolio"


def program_config(session) -> "ProgramBundle":
    """What the retriever reads, how projects are paired, and the rule table.

    Read-only by decision. A rule table edited in a browser is one with no
    review and no history, and every finding in this product is defended by
    pointing at these thresholds.
    """
    from app.api.schemas.program import (
        ProgramBundle,
        RuleRow,
        ScopeEntry,
        WatchedSource,
    )
    from app.config import settings
    from app.ingest.sources.excel.reader import sha256_file  # noqa: F401
    from app.ingest.sources.excel.source import all_watched
    from app.models.domain import Program
    from app.models.sync import SheetScan
    from app.scope import all_projects, find_program

    #: Not `select(Program).first()`. That was fine when exactly one Program
    #: existed and stopped being fine the moment a second one did - it names an
    #: arbitrary row, and which row depends on insertion order. Same reasoning as
    #: `portfolio()`: name the program when the projects in scope agree on one,
    #: and say "Portfolio" when the view is honestly cross-program.
    programs = {p.program_id for p in all_projects() if p.program_id}
    scans = session.scalars(select(SheetScan)).all()

    latest: dict[str, SheetScan] = {}
    for scan in scans:
        seen = latest.get(scan.scope)
        if seen is None or scan.scanned_at > seen.scanned_at:
            latest[scan.scope] = scan

    # An upload is a row in `uploaded_sheets`, not a file - that is the whole
    # point of it, so a deployed host with no synced folder can take data at
    # all. Asking the filesystem alone reported *every* source as missing on
    # exactly that host, while the data was there and serving pages.
    from app.models.uploads import UploadedSheet

    stored = set(session.scalars(select(UploadedSheet.file_name)).all())

    sources: list[WatchedSource] = []
    for watched in all_watched():
        path = Path(settings.data_root) / watched.file_name
        scope = f"{watched.file_name}#{watched.sheet_name}"
        scan = latest.get(scope)
        sources.append(
            WatchedSource(
                kind="excel",
                scope=scope,
                path=str(path),
                exists=path.exists() or watched.file_name in stored,
                last_scan=scan.scanned_at if scan else None,
                rows=scan.row_count if scan else 0,
                project_id=watched.project_id,
                file_name=watched.file_name,
                # Only a stored upload can be forgotten: `delete_import`
                # removes an `uploaded_sheets` row, and a demo sheet is
                # compiled into the image rather than stored.
                removable=watched.file_name in stored,
            )
        )

    jira_dir = Path(settings.data_root) / "jira"
    sources.append(
        WatchedSource(
            kind="jira_replay",
            scope="jira/*.json",
            path=str(jira_dir),
            exists=jira_dir.exists(),
        )
    )

    return ProgramBundle(
        program_name=_single_program_name(session, programs),
        data_root=str(settings.data_root),
        sources=sources,
        scope=[
            ScopeEntry(
                canonical_id=e.canonical_id,
                name=e.name,
                also=list(e.also),
                program_id=e.program_id or "",
                program_name=(
                    (find_program(e.program_id).name if find_program(e.program_id) else "")
                    if e.program_id
                    else ""
                ),
            )
            for e in all_projects()
        ],
        rule_table=DEFAULT_TABLE.name,
        rules=[
            RuleRow(
                id=rule.id,
                category=rule.category,
                severity=rule.severity,
                conditions=[f"{c.field} {c.op} {c.value}" for c in rule.when],
                headline=rule.headline,
                recommendation=rule.recommendation,
                rationale=rule.rationale,
            )
            for rule in DEFAULT_TABLE.rules
        ],
    )


def team_project(
    session,
    *,
    project_id: str,
    also: Sequence[str] = (),
) -> "TeamBundle":
    """Who is carrying what, and what moved - from real sheet columns only.

    See `api/schemas/team.py` for what is served and what is deliberately
    absent. The effort burn is built from observed `hours_spent` changes rather
    than from the final sheet, so a flat stretch in it is evidence that nothing
    was logged and not evidence that nobody looked.
    """
    from collections import defaultdict
    from datetime import timedelta

    from app.api.schemas.team import (
        ActivityWeek,
        BurnPoint as BurnPointOut,
        BurnSeries as BurnSeriesOut,
        Member,
        MemberTask,
        TeamBundle,
    )
    from app.intelligence.effort import Increment, Observation, burn_series
    from app.models.domain import QaItem, StateChange, Task
    from app.models.sync import SheetScan

    project_ids = [project_id, *also]
    nodes = load_tasks(session, project_ids)
    edges = load_edges(session, project_ids)
    impact = project_schedule(build_graph(nodes, edges))

    # The ORM rows, not the graph's `TaskNode`: assignee, phase and progress are
    # presentation fields, and `TaskNode` deliberately carries only what the
    # forward pass needs. Widening it to serve a screen would put display
    # concerns inside the schedule engine.
    rows = session.scalars(select(Task).where(Task.project_id.in_(project_ids))).all()

    qa_items = session.scalars(
        select(QaItem).where(QaItem.project_id.in_(project_ids))
    ).all()

    # Same rule the agent brief answers "what's overdue?" with - open, and a
    # planned finish already behind us. Imported rather than restated: two
    # definitions of late is how a page and a chat answer disagree about the
    # same task.
    from app.agent.brief import CLOSED
    from app.ingest.sources.jira.export_sheet import people_in_note

    today = date.today()

    by_owner: dict[str, list[MemberTask]] = defaultdict(list)
    for task in rows:
        owner = (task.assignee or "").strip() or "Unassigned"
        projection = impact.projections.get(task.id)
        open_row = (task.status or "").upper() not in CLOSED
        past_due = (
            (today - task.due_date).days
            if open_row and task.due_date and task.due_date < today
            else None
        )
        by_owner[owner].append(
            MemberTask(
                entity_id=task.id,
                label=entity_label(task.id, title=task.title),
                title=task.title,
                start=task.start_date,
                # `due_date` is where the convertor puts the planned finish.
                planned_end=task.due_date,
                projected_end=projection.projected_end if projection else None,
                propagated_days=projection.propagated_days if projection else None,
                days_past_due=past_due,
                also_named=people_in_note(task.description),
                phase=task.phase,
                progress=float(task.progress) if task.progress is not None else None,
            )
        )

    hours: dict[str, float] = defaultdict(float)
    planned: dict[str, float] = defaultdict(float)
    qa_count: dict[str, int] = defaultdict(int)
    qa_blocked: dict[str, int] = defaultdict(int)
    for item in qa_items:
        owner = (item.assignee or "").strip() or "Unassigned"
        qa_count[owner] += 1
        if item.hours_spent is not None:
            hours[owner] += float(item.hours_spent)
        if item.estimate_hours is not None:
            planned[owner] += float(item.estimate_hours)
        if (item.status or "").upper() == "BLOCKED":
            qa_blocked[owner] += 1

    names = sorted(set(by_owner) | set(qa_count))
    members = [
        Member(
            name=name,
            tasks=sorted(
                by_owner.get(name, []),
                key=lambda t: (t.start or date.max, t.label),
            ),
            hours_logged=round(hours.get(name, 0.0), 2),
            hours_planned=round(planned.get(name, 0.0), 2),
            qa_items=qa_count.get(name, 0),
            qa_blocked=qa_blocked.get(name, 0),
        )
        for name in names
    ]
    # Busiest first: the screen answers "who is carrying the most".
    members.sort(key=lambda m: (-m.days_committed, -m.hours_logged, m.name))

    dated = [t for m in members for t in m.tasks if t.start and t.planned_end]
    window_start = min((t.start for t in dated), default=None)
    window_end = max(
        (t.projected_end or t.planned_end for t in dated),
        default=None,
    )

    entity_ids = {t.id for t in rows} | {q.id for q in qa_items}
    changes = (
        session.scalars(
            select(StateChange)
            .where(StateChange.entity_id.in_(entity_ids))
            .order_by(StateChange.occurred_at)
        ).all()
        if entity_ids
        else []
    )

    # Bucketed by ISO week start. A week is the smallest unit a PM plans in,
    # and with a two-month window a daily axis would be mostly empty columns.
    weeks: dict[date, list[StateChange]] = defaultdict(list)
    for change in changes:
        day = change.occurred_at.date()
        weeks[day - timedelta(days=day.weekday())].append(change)

    activity = [
        ActivityWeek(
            week_start=start,
            changes=len(rows),
            exact=sum(1 for c in rows if c.precision == "exact"),
            bounded=sum(1 for c in rows if c.precision == "bounded"),
        )
        for start, rows in sorted(weeks.items())
    ]

    total = round(sum(hours.values()), 2)

    # The burn is built from the worklog sheet's own scans, found by asking the
    # QA changes which scan observed them and then loading every scan of that
    # sheet. Two hops rather than one, because the hop that matters is the
    # second: a scan where the sheet was untouched produces no change to point
    # at it, and those are exactly the scans that make a flat stretch in the
    # chart evidence rather than a gap. Other sheets' scans are excluded - a
    # schedule scan proves nothing about whether anyone logged an hour.
    qa_ids = {item.id for item in qa_items}
    qa_changes = [c for c in changes if c.entity_id in qa_ids]
    seen_scan_ids = {c.scan_id for c in qa_changes if c.scan_id is not None}
    worklog_scopes = (
        set(
            session.scalars(
                select(SheetScan.scope).where(SheetScan.id.in_(seen_scan_ids))
            ).all()
        )
        if seen_scan_ids
        else set()
    )
    scans = (
        session.scalars(
            select(SheetScan)
            .where(SheetScan.scope.in_(worklog_scopes))
            .order_by(SheetScan.scanned_at)
        ).all()
        if worklog_scopes
        else []
    )

    def _increments(field_name: str, *, delta_of) -> list[Increment]:
        return [
            Increment(
                entity_id=c.entity_id,
                delta=delta_of(c),
                lower=c.occurred_at_lower,
                upper=c.occurred_at,
            )
            for c in qa_changes
            if c.field == field_name and delta_of(c)
        ]

    burn = burn_series(
        [
            Observation(scanned_at=scan.scanned_at, changed=bool(scan.changed))
            for scan in scans
        ],
        _increments("hours_spent", delta_of=lambda c: _delta(c.old_value, c.new_value)),
        planned_hours=sum(planned.values()),
        logged_hours=total,
        rows_added=_increments(
            "__row__", delta_of=lambda c: 1.0 if c.old_value is None else 0.0
        ),
        blocked_added=_increments(
            "blocked",
            delta_of=lambda c: (
                1.0 if str(c.new_value).strip().lower() in {"yes", "true", "1"} else 0.0
            ),
        ),
    )

    return TeamBundle(
        project_id=project_id,
        members=members,
        activity=activity,
        window_start=window_start,
        window_end=window_end,
        total_hours=total,
        has_effort_data=total > 0,
        burn=BurnSeriesOut(
            planned_hours=burn.planned_hours,
            logged_hours=burn.logged_hours,
            remaining_hours=burn.remaining_hours,
            stalled_from=burn.stalled_from,
            points=[
                BurnPointOut(
                    observed_at=point.observed_at,
                    logged_hours=point.logged_hours,
                    delta_hours=point.delta_hours,
                    items_added=point.items_added,
                    blocked_added=point.blocked_added,
                    changed=point.changed,
                )
                for point in burn.points
            ],
        ),
    )


def _delta(before, after) -> float:
    """How much a numeric field moved, or 0.0 when either end is not a number.

    A worklog cell can hold anything a person can type. A blank, a dash or
    "TBC" means the increment is unknown, and unknown must contribute nothing
    rather than being read as zero hours logged - the difference matters when
    the series is reconstructed backwards from a known total.
    """
    try:
        return round(float(after or 0) - float(before or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def scenarios_project(
    session,
    *,
    project_id: str,
    also: Sequence[str] = (),
) -> "ScenarioBundle":
    """Recovery scenarios for one project.

    Shares `load_tasks` / `load_edges` / `project_schedule` with
    `analyze_project` and `explain_project`, so a scenario is measured against
    the same baseline the Insight and Calculation pages show. Computing it from
    a second load would let the three disagree about what "doing nothing" means.
    """
    from app.api.schemas.scenario import Scenario, ScenarioBundle, ScenarioMove
    from app.intelligence.schedule.whatif import recovery_scenarios

    project_ids = [project_id, *also]
    tasks = load_tasks(session, project_ids)
    edges = load_edges(session, project_ids)

    baseline = project_schedule(build_graph(tasks, edges))
    stated = project_schedule(build_graph(tasks, edges, stated_only=True))

    return ScenarioBundle(
        project_id=project_id,
        committed_end=baseline.project_end_planned,
        projected_end=baseline.project_end_projected,
        days_late=baseline.project_slip_days or 0,
        depends_on_inferred_edges=(
            stated.project_end_projected != baseline.project_end_projected
        ),
        scenarios=[
            Scenario(
                id=scenario.id,
                summary=scenario.summary,
                projected_end=scenario.projected_end,
                days_earlier=scenario.days_earlier,
                days_late=scenario.days_late,
                moves=[
                    ScenarioMove(
                        kind=move.kind,
                        entity_id=move.entity_id,
                        label=move.label,
                        days=move.days,
                        against_id=move.against_id,
                        against_label=move.against_label,
                    )
                    for move in scenario.moves
                ],
            )
            for scenario in recovery_scenarios(tasks, edges, baseline=baseline)
        ],
    )


#: Served on the bundle rather than written into each surface, so the page, the
#: CLI and the .docx cannot end up describing the method three ways - or one of
#: them quietly dropping the assumption.
FORECAST_METHOD = (
    "Each task's drift from its own baseline is resampled with replacement onto "
    "the tasks still open, and the same forward pass is re-run per trial. The "
    "empirical distribution is used directly - none is fitted or assumed."
)
FORECAST_ASSUMPTION = (
    "This assumes the project keeps drifting the way it has been drifting: a "
    "task that has already slipped is given another draw rather than excused "
    "from one. That is the conservative direction, and it is an assumption "
    "about the method, not a number from the sheet."
)


def forecast_project(
    session,
    *,
    project_id: str,
    also: Sequence[str] = (),
) -> "ForecastBundle":
    """A range of finish dates, resampled from this project's observed drift.

    Shares `load_tasks` / `load_edges` with `analyze_project` and
    `scenarios_project`, so the forecast, the outlook and the recovery options
    are all measured against one baseline. Computing it from a second load would
    let the three disagree about what "the plan" is.
    """
    from app.api.schemas.forecast import (
        ForecastBundle,
        ForecastObservation,
        ForecastPoint,
    )
    from app.intelligence.schedule.forecast import forecast_project as run

    project_ids = [project_id, *also]
    result = run(load_tasks(session, project_ids), load_edges(session, project_ids))

    return ForecastBundle(
        project_id=project_id,
        available=result.available,
        reason=result.reason,
        committed_end=result.committed_end,
        projected_end=result.projected_end,
        points=[
            ForecastPoint(
                percentile=point.percentile,
                finish=point.finish,
                days_late=point.days_late,
            )
            for point in result.points
        ],
        observations=result.observations,
        sample=[
            ForecastObservation(
                entity_id=observation.entity_id,
                label=observation.label,
                committed=observation.committed,
                planned=observation.planned,
                days=observation.days,
            )
            for observation in result.sample
        ],
        open_tasks=result.open_tasks,
        trials=result.trials,
        method=FORECAST_METHOD,
        assumption=FORECAST_ASSUMPTION,
    )


def gantt_project(
    session,
    *,
    project_id: str,
    also: Sequence[str] = (),
) -> "GanttBundle":
    """The schedule view for one project.

    Shares `load_tasks` / `load_edges` / `project_schedule` with the insight and
    calculation views, so the bars a PM sees are drawn from the same projection
    the findings were computed from. A separate query here would eventually
    disagree with them.
    """
    from datetime import timedelta

    from app.api.schemas.gantt import GanttBundle, GanttMilestone, GanttRow
    from app.models.domain import Milestone

    project_ids = [project_id, *also]

    #: Who each task belongs to, keyed by entity id.
    #:
    #: `GanttRow.assignee` has been in the schema from the start and nothing
    #: ever set it, so every row served `null` - the field was declared, typed,
    #: serialised and empty. It surfaced the moment a tile grouped by owner and
    #: reported a project of seventeen named tasks as entirely "Unassigned".
    #:
    #: A dict rather than a field on `TaskNode`: the scheduler has no use for an
    #: assignee, and widening the type it reasons over to carry display data is
    #: how that type stops meaning "what scheduling needs".
    owners = dict(
        session.execute(
            select(Task.id, Task.assignee).where(Task.project_id.in_(project_ids))
        ).all()
    )
    #: Same reasoning, same shape: the issue type is display data the
    #: scheduler never reasons over, and it is what separates management
    #: work from delivery work on a board that would otherwise pool them.
    phases = dict(
        session.execute(
            select(Task.id, Task.phase).where(Task.project_id.in_(project_ids))
        ).all()
    )
    tasks = load_tasks(session, project_ids)
    edges = load_edges(session, project_ids)

    schedule = build_graph(tasks, edges)
    impact = project_schedule(schedule)
    driving = set(impact.driving_path)

    names = {
        m.id: m
        for m in session.scalars(
            select(Milestone).where(Milestone.project_id.in_(project_ids))
        ).all()
    }

    rows: list[GanttRow] = []
    for task in tasks:
        projection = impact.projections[task.entity_id]
        milestone = names.get(task.milestone_id) if task.milestone_id else None
        rows.append(
            GanttRow(
                entity_id=task.entity_id,
                label=entity_label(task.entity_id, title=task.title),
                title=task.title,
                status=task.status,
                assignee=owners.get(task.entity_id),
                phase=phases.get(task.entity_id),
                start=task.start_date,
                baseline_end=task.baseline_end,
                planned_end=task.planned_end,
                projected_end=projection.projected_end,
                propagated_days=projection.propagated_days,
                recorded_slip_days=projection.recorded_slip_days,
                is_inconsistent=projection.is_inconsistent,
                milestone_id=task.milestone_id,
                milestone_name=milestone.name if milestone else None,
                depends_on=schedule.predecessors(task.entity_id),
                on_driving_path=task.entity_id in driving,
            )
        )

    # Earliest start to latest projected finish, so every bar fits. Padded by a
    # few days at each end: a bar flush against the frame reads as clipped.
    #
    # The padding is clamped, because a source can hand us a date at the edge of
    # what a `date` can hold. A single task dated 9999-12-31 - a real sentinel in
    # some trackers, and a trivial thing to type by accident - made this raise
    # `OverflowError` and took the whole Schedule page down with a 500. The view
    # is a window over whatever dates exist; it is not the place to decide that
    # one of them is implausible.
    dates = [d for row in rows for d in (row.start, row.projected_end, row.baseline_end) if d]
    window_start = shift_date(min(dates), -3) if dates else None
    window_end = shift_date(max(dates), 3) if dates else None

    # The scan time the view reflects, not the moment it was rendered - the same
    # definition `analyze_project` uses, so the tabs cannot disagree about which
    # snapshot they are showing.
    #
    # Including its fallback, which this had been missing. `analyze_project`
    # ends that expression with `default=generated_at`: a project nothing has
    # ever been *observed* to change still has a scan date, namely now. Without
    # it the Gantt got `as_of=None` on every project's first upload - no scan
    # line, and nothing could be drawn as past due, while Insight computed
    # `tasks_overdue` from the wall clock and reported six. The two tabs
    # disagreed in exactly the way this comment says they cannot.
    entity_ids = {t.entity_id for t in tasks}
    observed = (
        session.scalar(
            select(func.max(StateChange.occurred_at)).where(
                StateChange.entity_id.in_(entity_ids)
            )
        )
        if entity_ids
        else None
    )
    if observed is None and tasks:
        observed = datetime.now(timezone.utc)

    at_risk = set(impact.affected_milestones)
    milestones = [
        GanttMilestone(
            id=m.id,
            name=m.name,
            planned_date=m.planned_date,
            baseline_date=m.baseline_date,
            slipped_days=(
                (m.planned_date - m.baseline_date).days
                if m.planned_date and m.baseline_date
                else None
            ),
            at_risk=m.name in at_risk or m.id in at_risk,
        )
        for m in sorted(
            names.values(), key=lambda m: (m.planned_date or date.max, m.name)
        )
    ]

    return GanttBundle(
        project_id=project_id,
        rows=rows,
        milestones=milestones,
        window_start=window_start,
        window_end=window_end,
        as_of=observed.date() if observed else None,
        driving_path=list(impact.driving_path),
        project_end_planned=impact.project_end_planned,
        project_end_projected=impact.project_end_projected,
    )


def _forward_steps(schedule, impact, ForwardStep, Operand, Calc):
    """Build the served step from the report and the on-screen derivation.

    Both halves read the same `ImpactReport`: the scalar fields come straight off
    the projection, and the input/algorithm/output narrative comes from
    `explain.derive`. There is no second calculation here, which is what keeps the
    table, the page and the findings unable to disagree.
    """
    from app.intelligence import explain
    from app.intelligence.assembler import entity_label

    steps = []
    for entity_id in schedule.topological_order():
        task = schedule.tasks[entity_id]
        projection = impact.projections[entity_id]
        shown = explain.derive(schedule, impact, entity_id)

        driver = projection.driving_predecessor
        duration = (
            (task.planned_end - task.start_date).days
            if task.start_date and task.planned_end
            else None
        )

        steps.append(
            ForwardStep(
                entity_id=entity_id,
                label=entity_label(entity_id),
                title=task.title,
                start=task.start_date,
                duration_days=duration,
                driver=driver,
                driver_label=entity_label(driver) if driver else None,
                lag_days=schedule.lag(driver, entity_id) if driver else None,
                earliest_start=(
                    impact.projections[driver].projected_end
                    if driver
                    else task.start_date
                ),
                projected_end=projection.projected_end,
                planned_end=projection.planned_end,
                baseline_end=projection.baseline_end,
                propagated_days=projection.propagated_days,
                variance_days=projection.variance_days,
                recorded_slip_days=projection.recorded_slip_days,
                is_inconsistent=projection.is_inconsistent,
                inputs=[Operand(**vars(o)) for o in (shown.inputs if shown else ())],
                calc=[Calc(**vars(c)) for c in (shown.steps if shown else ())],
                outputs=[Operand(**vars(o)) for o in (shown.outputs if shown else ())],
            )
        )
    return steps
