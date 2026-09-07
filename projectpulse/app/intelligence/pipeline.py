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

Narration is deterministic here. A model client would slot in between 7's
assembly and its narration, and the fallback already produced by this pipeline is
what it must beat - and what is served if it does not.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timezone

from sqlalchemy import func, select

from app.api.schemas.insight import EvidenceRef, InsightBundle
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
from app.models.sync import RawReject

log = logging.getLogger(__name__)


def _known_fields() -> set[str]:
    from dataclasses import fields

    from app.intelligence.context import DeliveryContext

    return {f.name for f in fields(DeliveryContext)} - {"notes"}


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
) -> InsightBundle:
    """Everything the insight screen needs for one project.

    `also` names the *same delivery project* as it appears in other source
    systems. One project tracked in both Jira and a spreadsheet produces two
    `projects` rows with different ids, and without this they would be analysed as
    two unrelated projects - a Jira event could never be shown to explain a
    spreadsheet observation, which is precisely the cross-source claim neither
    source can make alone. Every entity is re-pointed at `project_id` below so
    the causal engine treats them as one.
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

    # Deterministic prose, always. A model draft would be validated against this
    # bundle and replace the narrative only if it passed.
    from app.narration.fallback import render_narrative

    bundle.narrative = render_narrative(bundle)
    bundle.narration_source = "template"
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
                label=entity_label(task.entity_id),
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
