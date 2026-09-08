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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from app.api.schemas.insight import DeliveryConfidence, EvidenceRef, InsightBundle
from app.intelligence import confidence as confidence_calc
from app.intelligence.assembler import build_bundle, entity_label
from app.intelligence.context import build_context
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
    from app.narration.client import Drafter

log = logging.getLogger(__name__)


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
) -> InsightBundle:
    """Everything the insight screen needs for one project.

    `also` names the *same delivery project* as it appears in other source
    systems. One project tracked in both Jira and a spreadsheet produces two
    `projects` rows with different ids, and without this they would be analysed as
    two unrelated projects - a Jira event could never be shown to explain a
    spreadsheet observation, which is precisely the cross-source claim neither
    source can make alone. Every entity is re-pointed at `project_id` below so
    the causal engine treats them as one.

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


def portfolio(session) -> "PortfolioBundle":
    """Every delivery project in the program, ranked worst first.

    Runs `analyze_project` per project rather than aggregating a shortcut: the
    program view then cannot disagree with the project view, because it *is*
    the project view, folded up. It costs one pass per project, and a portfolio
    of four is not where this becomes a problem.
    """
    from datetime import date as _date

    from app.api.schemas.portfolio import DIMENSIONS, PortfolioBundle, ProjectRow
    from app.models.domain import Program
    from app.scope import PORTFOLIO

    program = session.scalars(select(Program)).first()
    rows: list[ProjectRow] = []

    for entry in PORTFOLIO:
        tasks = load_tasks(session, list(entry.source_ids))
        if not tasks:
            # Known to the portfolio, nothing ingested. `no_data`, never green.
            rows.append(
                ProjectRow(
                    project_id=entry.canonical_id,
                    name=entry.name,
                    source_ids=list(entry.source_ids),
                    bands={d: "no_data" for d in DIMENSIONS},
                )
            )
            continue

        bundle = analyze_project(
            session, project_id=entry.canonical_id, also=list(entry.also)
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

        rows.append(
            ProjectRow(
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
        )

    # Worst first: the screen exists to answer "where do I look today".
    rank = {"critical": 0, "watch": 1, "healthy": 2, "no_data": 3}
    rows.sort(key=lambda r: (rank.get(r.band, 9), -r.days_late, r.name))

    return PortfolioBundle(
        program_name=program.name if program else "Portfolio",
        generated_at=_date.today(),
        projects=rows,
    )


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
    from app.ingest.sources.excel.source import WATCHED
    from app.models.domain import Program
    from app.models.sync import SheetScan
    from app.scope import PORTFOLIO

    program = session.scalars(select(Program)).first()
    scans = session.scalars(select(SheetScan)).all()

    latest: dict[str, SheetScan] = {}
    for scan in scans:
        seen = latest.get(scan.scope)
        if seen is None or scan.scanned_at > seen.scanned_at:
            latest[scan.scope] = scan

    sources: list[WatchedSource] = []
    for watched in WATCHED:
        path = Path(settings.data_root) / watched.file_name
        scope = f"{watched.file_name}#{watched.sheet_name}"
        scan = latest.get(scope)
        sources.append(
            WatchedSource(
                kind="excel",
                scope=scope,
                path=str(path),
                exists=path.exists(),
                last_scan=scan.scanned_at if scan else None,
                rows=scan.row_count if scan else 0,
                project_id=watched.project_id,
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
        program_name=program.name if program else "Portfolio",
        data_root=str(settings.data_root),
        sources=sources,
        scope=[
            ScopeEntry(canonical_id=e.canonical_id, name=e.name, also=list(e.also))
            for e in PORTFOLIO
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

    by_owner: dict[str, list[MemberTask]] = defaultdict(list)
    for task in rows:
        owner = (task.assignee or "").strip() or "Unassigned"
        projection = impact.projections.get(task.id)
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
    dates = [d for row in rows for d in (row.start, row.projected_end, row.baseline_end) if d]
    window_start = min(dates) - timedelta(days=3) if dates else None
    window_end = max(dates) + timedelta(days=3) if dates else None

    # The scan time the view reflects, not the moment it was rendered - the same
    # definition `analyze_project` uses, so the tabs cannot disagree about which
    # snapshot they are showing.
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
