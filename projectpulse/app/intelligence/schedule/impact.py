"""What a slip costs, by forward-scheduling the DAG.

The useful number in this module is not "how late is this task" - a PM can read
that off their own sheet. It is **`propagated_days`: how late a task will be that
nobody has written down yet.**

A hand-maintained schedule records the slip a person noticed and typed in. It does
not record the consequence of that slip on everything downstream, because keeping
that consistent by hand across two hundred rows is exactly the work nobody has time
for. So the sheet says UAT finishes on the 26th, the dependency chain says it
finishes on the 3rd, and the gap between those two dates is the finding.

Every date here is arithmetic on dates a human wrote:

    earliest start  = max(own start, each predecessor's projected finish + lag)
    projected end   = earliest start + the duration the plan already implies

No estimate, no model, no judgement. That is the point - `intelligence/schedule/`
must never import the ML duration classifier, which returns a bucket and never a
number.

Run it twice - once on the full graph, once with `stated_only=True` - and the
difference tells you how much of the answer rests on edges we inferred rather than
edges a human wrote. `answer_survives_stated_only` does exactly that comparison.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.intelligence.schedule.graph import ScheduleGraph, build_graph


@dataclass(frozen=True)
class TaskProjection:
    """When a task actually finishes, once its upstream reality is applied."""

    entity_id: str
    title: str | None
    planned_end: date | None
    baseline_end: date | None
    projected_end: date | None
    #: Which predecessor's finish set this task's start. The answer to "what is
    #: holding this up", and None when the task's own plan is the constraint.
    driving_predecessor: str | None = None
    milestone_id: str | None = None
    raw_data_id: int | None = None

    @property
    def propagated_days(self) -> int | None:
        """Slip the schedule implies but the sheet does not yet show.

        **The headline number.** Positive means the plan is internally
        inconsistent: this date cannot survive its own dependencies.
        """
        if self.projected_end is None or self.planned_end is None:
            return None
        return (self.projected_end - self.planned_end).days

    @property
    def variance_days(self) -> int | None:
        """Total drift from the committed baseline, including what is on the sheet."""
        if self.projected_end is None or self.baseline_end is None:
            return None
        return (self.projected_end - self.baseline_end).days

    @property
    def recorded_slip_days(self) -> int | None:
        """Drift a human already typed into the sheet."""
        if self.planned_end is None or self.baseline_end is None:
            return None
        return (self.planned_end - self.baseline_end).days

    @property
    def is_inconsistent(self) -> bool:
        """The plan contradicts its own dependencies."""
        return (self.propagated_days or 0) > 0


@dataclass
class ImpactReport:
    projections: dict[str, TaskProjection] = field(default_factory=dict)
    #: The chain of tasks that determines the project's finish date.
    driving_path: list[str] = field(default_factory=list)
    project_end_planned: date | None = None
    project_end_projected: date | None = None
    #: Milestones downstream of a task whose date cannot hold.
    affected_milestones: list[str] = field(default_factory=list)
    stated_only: bool = False

    @property
    def project_slip_days(self) -> int | None:
        if self.project_end_projected is None or self.project_end_planned is None:
            return None
        return (self.project_end_projected - self.project_end_planned).days

    def inconsistent(self) -> list[TaskProjection]:
        """Tasks whose plan cannot survive its dependencies, worst first."""
        return sorted(
            (p for p in self.projections.values() if p.is_inconsistent),
            key=lambda p: -(p.propagated_days or 0),
        )


def _duration_days(start: date | None, end: date | None) -> int | None:
    if start is None or end is None:
        return None
    return max((end - start).days, 0)


def project_schedule(schedule: ScheduleGraph) -> ImpactReport:
    """Forward-pass the DAG and report where the plan stops being consistent."""
    report = ImpactReport(stated_only=schedule.stated_only)
    projected: dict[str, date | None] = {}

    # Topological order guarantees every predecessor is already projected, which
    # is the whole reason the graph has to be acyclic.
    for entity_id in schedule.topological_order():
        task = schedule.tasks[entity_id]

        earliest_start = task.start_date
        driver: str | None = None

        for predecessor in schedule.predecessors(entity_id):
            upstream_finish = projected.get(predecessor)
            if upstream_finish is None:
                continue
            # Finish-to-start: this task cannot begin until its predecessor is
            # done, plus whatever lag the plan states.
            candidate = upstream_finish + timedelta(
                days=schedule.lag(predecessor, entity_id)
            )
            if earliest_start is None or candidate > earliest_start:
                earliest_start = candidate
                driver = predecessor

        duration = _duration_days(task.start_date, task.planned_end)
        if earliest_start is not None and duration is not None:
            projected_end = earliest_start + timedelta(days=duration)
        else:
            # No start date or no plan: fall back to the later of the plan and the
            # upstream constraint. Less precise, never wrong in the optimistic
            # direction.
            projected_end = task.planned_end
            if earliest_start is not None and (
                projected_end is None or earliest_start > projected_end
            ):
                projected_end = earliest_start

        # A task can never finish earlier than its own plan just because its
        # predecessors are quick - the plan is a commitment, not an estimate.
        if (
            projected_end is not None
            and task.planned_end is not None
            and projected_end < task.planned_end
        ):
            projected_end = task.planned_end
            driver = None

        projected[entity_id] = projected_end
        report.projections[entity_id] = TaskProjection(
            entity_id=entity_id,
            title=task.title,
            planned_end=task.planned_end,
            baseline_end=task.baseline_end,
            projected_end=projected_end,
            driving_predecessor=driver,
            milestone_id=task.milestone_id,
            raw_data_id=task.raw_data_id,
        )

    _finalize(report, schedule, projected)
    return report


def _finalize(
    report: ImpactReport, schedule: ScheduleGraph, projected: dict[str, date | None]
) -> None:
    dated = {k: v for k, v in projected.items() if v is not None}
    if not dated:
        return

    last_entity = max(dated, key=lambda k: dated[k])
    report.project_end_projected = dated[last_entity]

    planned_ends = [
        task.planned_end for task in schedule.tasks.values() if task.planned_end
    ]
    report.project_end_planned = max(planned_ends) if planned_ends else None

    # Walk back along whichever predecessor actually set each date. This is the
    # driving path: the only chain a PM can shorten to pull the finish in.
    path: list[str] = []
    cursor: str | None = last_entity
    seen: set[str] = set()
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        path.append(cursor)
        cursor = report.projections[cursor].driving_predecessor
    report.driving_path = list(reversed(path))

    report.affected_milestones = sorted(
        {
            projection.milestone_id
            for projection in report.projections.values()
            if projection.milestone_id and projection.is_inconsistent
        }
    )


def answer_survives_stated_only(
    tasks: Sequence, edges: Sequence
) -> tuple[ImpactReport, ImpactReport]:
    """Project twice - all edges, then human-stated edges only.

    Returns ``(full, stated_only)``. When the two disagree, the conclusion depends
    on edges we inferred, and any finding built on it has to say so rather than
    presenting an inferred date as a stated one.
    """
    full = project_schedule(build_graph(tasks, edges))
    stated = project_schedule(build_graph(tasks, edges, stated_only=True))
    return full, stated
