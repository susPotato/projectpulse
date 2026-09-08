"""Recovery scenarios: what the dependency graph would do if one thing changed.

The design's "Recovery Scenarios" screen, and the feature that turns this tool
from diagnostic into prescriptive. It is cheap only because
:func:`~app.intelligence.schedule.impact.project_schedule` is a **pure function
of (tasks, edges)** - so a scenario is copy, change one thing, re-run, diff.

Pure like the rest of `intelligence/`: no session, no ORM, no clock.

**A simulation is not a mutation.** Nothing here writes to a task, an edge or a
spreadsheet; `_with` builds new frozen dataclasses. That is what lets the
product stay read-only (design section 8) while still answering "what if" - the
observed sheet is never touched, so the precision model is unaffected.

Two moves, and both are chosen because the forward pass actually honours them:

``compress``
    Shorten a task's own plan. The pass computes
    ``projected_end = earliest_start + duration``, so a shorter duration moves
    everything downstream.

``overlap``
    Make a dependency's lag negative - MS-Project's ``FS-10d``. The pass reads
    ``lag`` when deriving a successor's earliest start, so a negative lag lets
    the successor begin before its predecessor finishes.

⚠️ **Changing ``dep_type`` to SS would do nothing.** The engine reasons about
finish-to-start only (see `excel/dependencies.py`), so a scenario expressed as
"make this start-to-start" would re-run to an identical answer and report zero
days recovered - a wrong answer that looks like a computed one. Negative lag is
the encoding that works today.

⚠️ **Recovery is capped by each task's own planned date.** The forward pass
floors a projection at ``task.planned_end`` on purpose - a plan is a commitment,
not an estimate - so a scenario can only remove slip that *upstream* imposed. A
task nobody is waiting on cannot be pulled earlier by fixing something else, and
these numbers correctly refuse to pretend otherwise.

⚠️ **Feasibility is not modelled and must not be implied.** Whether a team can
absorb a six-day compression is a resource question, and `resources` is
deliberately empty. Every scenario is "if this were true, the graph says X",
never "you can do this". The API and the UI say so.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from app.intelligence.schedule.graph import EdgeRecord, TaskNode, build_graph
from app.intelligence.schedule.impact import ImpactReport, project_schedule

#: The most days a single compression is allowed to claim. A sprint's worth: far
#: enough to matter, near enough that a PM can judge it. Beyond this the
#: arithmetic is still right and the premise stops being credible.
MAX_COMPRESS_DAYS = 10

#: The most days an overlap is allowed to claim, for the same reason.
MAX_OVERLAP_DAYS = 10

#: How many scenarios to return. Enough to show there is a choice, few enough
#: that each one gets read.
MAX_SCENARIOS = 4


@dataclass(frozen=True)
class Move:
    """One change to the plan, described in terms a PM would recognise."""

    kind: str
    #: `compress`: the task. `overlap`: the successor whose start moves.
    entity_id: str
    label: str
    days: int
    #: For `overlap` only: the predecessor it now overlaps with.
    against_id: str | None = None
    against_label: str | None = None


@dataclass(frozen=True)
class Scenario:
    """One re-run of the forward pass, and what it produced."""

    id: str
    summary: str
    moves: tuple[Move, ...]
    projected_end: date | None
    #: How much sooner delivery lands than doing nothing. The scenario's value.
    days_earlier: int
    #: Where that leaves the *original* commitment. Negative means ahead of it.
    #:
    #: Measured against the plan as it stands today, not against the scenario's
    #: own shifted plan - compressing a task moves `project_end_planned` too, so
    #: a scenario compared against its own plan would flatter itself by
    #: construction and report "on time" for having moved the goalposts.
    days_late: int

    @property
    def is_worthwhile(self) -> bool:
        return self.days_earlier > 0

    @property
    def outcome(self) -> tuple[int, date | None]:
        """What this scenario achieves, for collapsing equivalent choices."""
        return (self.days_earlier, self.projected_end)


def _duration(task: TaskNode) -> int | None:
    if task.start_date is None or task.planned_end is None:
        return None
    return (task.planned_end - task.start_date).days


def _compress(task: TaskNode, days: int) -> TaskNode:
    """A copy of the task finishing `days` earlier.

    `replace` rather than mutation: `TaskNode` is frozen, and the caller's list
    has to stay exactly as it was so the baseline projection remains comparable.
    """
    assert task.planned_end is not None
    return replace(task, planned_end=task.planned_end - timedelta(days=days))


def _overlap(edge: EdgeRecord, days: int) -> EdgeRecord:
    """A copy of the edge with `days` of negative lag - MS-Project's `FS-Nd`."""
    return replace(edge, lag_days=edge.lag_days - days)


def _apply(
    tasks: list[TaskNode], edges: list[EdgeRecord], moves: tuple[Move, ...]
) -> tuple[list[TaskNode], list[EdgeRecord]]:
    """The plan as it would be under `moves`. Inputs are never modified."""
    compress = {m.entity_id: m.days for m in moves if m.kind == "compress"}
    overlap = {
        (m.against_id, m.entity_id): m.days for m in moves if m.kind == "overlap"
    }

    new_tasks = [
        _compress(t, compress[t.entity_id])
        if t.entity_id in compress and t.planned_end is not None
        else t
        for t in tasks
    ]
    new_edges = [
        _overlap(e, overlap[(e.predecessor_id, e.successor_id)])
        if (e.predecessor_id, e.successor_id) in overlap
        else e
        for e in edges
    ]
    return new_tasks, new_edges


def _label(task: TaskNode) -> str:
    """`excel:Task:1:WBS-114` -> `WBS-114`, matching the rest of the product."""
    return task.entity_id.split(":", 3)[-1]


def _candidate_moves(
    tasks: dict[str, TaskNode], edges: list[EdgeRecord], baseline: ImpactReport
) -> list[Move]:
    """The moves worth simulating, drawn only from the driving path.

    Anywhere else is wasted arithmetic: a task the finish date does not depend
    on cannot move the finish date, and offering it as a scenario would imply
    otherwise.
    """
    driving = list(baseline.driving_path)
    on_path = set(driving)
    moves: list[Move] = []

    for entity_id in driving:
        task = tasks.get(entity_id)
        if task is None:
            continue
        span = _duration(task)
        if not span or span < 2:
            continue
        # Half the task, capped. Halving is the optimistic bound anyone would
        # try first, and it keeps the premise inspectable.
        days = min(MAX_COMPRESS_DAYS, span // 2)
        if days > 0:
            moves.append(
                Move(
                    kind="compress",
                    entity_id=entity_id,
                    label=_label(task),
                    days=days,
                )
            )

    for edge in edges:
        if edge.successor_id not in on_path or edge.predecessor_id not in on_path:
            continue
        successor = tasks.get(edge.successor_id)
        predecessor = tasks.get(edge.predecessor_id)
        if successor is None or predecessor is None:
            continue
        span = _duration(successor)
        if not span or span < 2:
            continue
        days = min(MAX_OVERLAP_DAYS, span // 2)
        if days > 0:
            moves.append(
                Move(
                    kind="overlap",
                    entity_id=edge.successor_id,
                    label=_label(successor),
                    days=days,
                    against_id=edge.predecessor_id,
                    against_label=_label(predecessor),
                )
            )

    return moves


def _summarise(moves: tuple[Move, ...]) -> str:
    """A digit-free description. The figures are fields, not prose."""
    if len(moves) > 1:
        return "Every change below, applied together"
    move = moves[0]
    if move.kind == "compress":
        return f"Deliver {move.label} in less time"
    return f"Start {move.label} before {move.against_label} finishes"


def _score(
    tasks: list[TaskNode],
    edges: list[EdgeRecord],
    moves: tuple[Move, ...],
    baseline: ImpactReport,
    stated_only: bool,
) -> Scenario:
    changed_tasks, changed_edges = _apply(tasks, edges, moves)
    after = project_schedule(build_graph(changed_tasks, changed_edges, stated_only=stated_only))

    before_end = baseline.project_end_projected
    after_end = after.project_end_projected
    committed = baseline.project_end_planned

    earlier = (
        (before_end - after_end).days
        if before_end is not None and after_end is not None
        else 0
    )
    late = (
        (after_end - committed).days
        if after_end is not None and committed is not None
        else 0
    )

    return Scenario(
        id="+".join(f"{m.kind}:{m.label}" for m in moves),
        summary=_summarise(moves),
        moves=moves,
        projected_end=after_end,
        days_earlier=max(earlier, 0),
        days_late=late,
    )


def recovery_scenarios(
    tasks: list[TaskNode],
    edges: list[EdgeRecord],
    *,
    stated_only: bool = False,
    baseline: ImpactReport | None = None,
    limit: int = MAX_SCENARIOS,
) -> list[Scenario]:
    """Scenarios worth showing, best recovery first.

    Takes the rows rather than a built `ScheduleGraph` because every scenario
    needs to build its *own* graph from modified rows - and because that is how
    the rest of `intelligence/` works: modules speak in rows and ids, and the
    caller does the mapping.

    Every figure is the forward pass re-run, not an estimate. A scenario that
    recovers nothing is dropped rather than listed at zero: the graph saying "no
    effect" is a true fact about that move, but a list of them teaches a reader
    that the feature does not work.
    """
    by_id = {task.entity_id: task for task in tasks}
    baseline = baseline or project_schedule(
        build_graph(tasks, edges, stated_only=stated_only)
    )

    if baseline.project_end_projected is None:
        return []

    # Only a late plan has anything to recover. Without this the engine happily
    # offers to finish a healthy project early - arithmetically true, and not
    # what this feature is: compressing a task pulls its own planned end in, so
    # *any* plan can be made shorter. That is schedule optimisation, a different
    # product, and here it would be noise on a project with no problem. The
    # Outlook panel hides itself at zero slip for the same reason.
    if (baseline.project_slip_days or 0) <= 0:
        return []

    candidates = _candidate_moves(by_id, edges, baseline)
    if not candidates:
        return []

    scored = [
        _score(tasks, edges, (move,), baseline, stated_only) for move in candidates
    ]

    # The ceiling: everything at once. Worth its own row because a PM asking
    # "how much is even available?" is asking about this one.
    if len(candidates) > 1:
        scored.append(_score(tasks, edges, tuple(candidates), baseline, stated_only))

    worthwhile = [s for s in scored if s.is_worthwhile]
    worthwhile.sort(key=lambda s: (-s.days_earlier, len(s.moves), s.id))

    # Collapse scenarios that land on the same date having recovered the same
    # amount. Three rows reading "22 Jun, 10 days earlier" are three genuinely
    # different choices with one outcome, and printed side by side they look
    # like a broken calculation rather than a real equivalence. The simplest
    # one survives - the sort puts fewest-moves first.
    best: dict[tuple[int, date | None], Scenario] = {}
    for scenario in worthwhile:
        best.setdefault(scenario.outcome, scenario)

    return list(best.values())[:limit]
