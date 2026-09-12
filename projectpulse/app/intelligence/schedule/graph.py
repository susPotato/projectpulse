"""The dependency DAG, and what connects any two entities.

`dependencies.py` already guarantees each *sheet's* edges are acyclic, but this
graph merges every sheet in a project, and two individually-fine sheets can close
a loop between them. So the guard is repeated here rather than assumed - a cycle
would make `impact.py`'s forward pass non-terminating, and NetworkX would raise
rather than return a wrong number.

Only `dep_type='FS'` is traversed. SS, FF and SF are parsed and stored so a PM's
paste from MS Project survives, but nothing here reasons about them yet, and
silently treating an SS edge as FS would move dates by the length of a task.

**`stated_only` is not a debug flag.** A critical path computed partly from
`wbs_implicit` edges is a weaker claim than one computed from edges a human wrote
down, and a PM taking a date to a steering committee is entitled to know which
they are looking at. Both graphs are cheap; build both and let the finding say.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

import networkx as nx

from app.intelligence.temporal.templates import LinkBasis

log = logging.getLogger(__name__)

#: The only relation the schedule engine understands today.
TRAVERSABLE = "FS"

SOURCE_STATED = "excel_predecessor"


@dataclass(frozen=True)
class TaskNode:
    """A task, reduced to what scheduling actually needs."""

    entity_id: str
    project_id: str
    title: str | None = None
    status: str | None = None
    start_date: date | None = None
    planned_end: date | None = None
    baseline_end: date | None = None
    milestone_id: str | None = None
    raw_data_id: int | None = None
    #: What the source system says about when this row last changed. Not a
    #: scheduling input - nothing in the forward pass reads it - but it rides
    #: with the task because the alternative is a second query returning a
    #: parallel list that has to be kept in the same order as this one.
    source_updated_at: date | None = None

    @property
    def committed_end(self) -> date | None:
        """What we promised. Falls back to the plan when no baseline was kept.

        A sheet that never recorded a baseline cannot show variance, and inventing
        one from the current plan would report zero slip forever - so the caller
        gets `None` variance rather than a comfortable lie.
        """
        return self.baseline_end


@dataclass(frozen=True)
class EdgeRecord:
    """One dependency, as stored."""

    predecessor_id: str
    successor_id: str
    dep_type: str = TRAVERSABLE
    lag_days: int = 0
    source: str | None = None
    raw_data_id: int | None = None

    @property
    def is_stated(self) -> bool:
        return self.source == SOURCE_STATED


@dataclass
class ScheduleGraph:
    """A DAG plus the rows it was built from."""

    graph: nx.DiGraph
    tasks: dict[str, TaskNode]
    #: Edges refused during construction, with the reason. Surfaced, not swallowed.
    dropped: list[tuple[EdgeRecord, str]] = field(default_factory=list)
    stated_only: bool = False

    @property
    def edge_count(self) -> int:
        return self.graph.number_of_edges()

    def predecessors(self, entity_id: str) -> list[str]:
        if entity_id not in self.graph:
            return []
        return list(self.graph.predecessors(entity_id))

    def lag(self, predecessor_id: str, successor_id: str) -> int:
        return int(self.graph.edges[predecessor_id, successor_id].get("lag_days", 0))

    def topological_order(self) -> list[str]:
        return list(nx.topological_sort(self.graph))


def build_graph(
    tasks: Iterable[TaskNode],
    edges: Iterable[EdgeRecord],
    *,
    stated_only: bool = False,
) -> ScheduleGraph:
    """Assemble the DAG, refusing anything that would make it lie.

    Args:
        stated_only: traverse only edges a human wrote in the Predecessor column.
            Use it to check whether a conclusion survives without inferred edges.
    """
    nodes = {task.entity_id: task for task in tasks}
    graph = nx.DiGraph()
    for entity_id, task in nodes.items():
        graph.add_node(entity_id, task=task)

    dropped: list[tuple[EdgeRecord, str]] = []

    for edge in edges:
        if edge.dep_type != TRAVERSABLE:
            dropped.append(
                (edge, f"{edge.dep_type} relations are not traversed yet; only FS is")
            )
            continue
        if stated_only and not edge.is_stated:
            dropped.append((edge, "inferred edge excluded from a stated-only graph"))
            continue

        missing = [
            entity_id
            for entity_id in (edge.predecessor_id, edge.successor_id)
            if entity_id not in nodes
        ]
        if missing:
            # An edge to a task we do not hold would let the forward pass walk off
            # the end of the project.
            dropped.append((edge, f"endpoint not among this project's tasks: {missing}"))
            continue

        # Two individually-acyclic sheets can still close a loop between them.
        if edge.predecessor_id != edge.successor_id and nx.has_path(
            graph, edge.successor_id, edge.predecessor_id
        ):
            dropped.append(
                (edge, "would close a cycle; a cyclic schedule has no critical path")
            )
            continue
        if edge.predecessor_id == edge.successor_id:
            dropped.append((edge, "self-edge"))
            continue

        graph.add_edge(
            edge.predecessor_id,
            edge.successor_id,
            lag_days=edge.lag_days,
            source=edge.source,
            raw_data_id=edge.raw_data_id,
        )

    if dropped:
        log.info("schedule graph dropped %d edge(s)", len(dropped))

    return ScheduleGraph(
        graph=graph, tasks=nodes, dropped=dropped, stated_only=stated_only
    )


class DependencyLinks:
    """Answers `chains.py`'s question: what connects these two entities?

    Returns the **strongest** available link, so a direct dependency is never
    reported as a project-level coincidence. Direction matters: a cause must be
    upstream of its effect, never the reverse.
    """

    def __init__(
        self,
        schedule: ScheduleGraph,
        *,
        project_of: dict[str, str] | None = None,
    ) -> None:
        self.schedule = schedule
        # Entities the graph does not hold - QA items, mainly - still need a
        # project so a SAME_PROJECT chain can be formed for them.
        self.project_of = dict(project_of or {})
        for entity_id, task in schedule.tasks.items():
            self.project_of.setdefault(entity_id, task.project_id)

    def link_between(self, cause_entity: str, effect_entity: str) -> LinkBasis | None:
        if cause_entity == effect_entity:
            return LinkBasis.SAME_ENTITY

        graph = self.schedule.graph
        if cause_entity in graph and effect_entity in graph:
            if graph.has_edge(cause_entity, effect_entity):
                return LinkBasis.DEPENDENCY_EDGE
            if nx.has_path(graph, cause_entity, effect_entity):
                return LinkBasis.DEPENDENCY_PATH

        cause_project = self.project_of.get(cause_entity)
        effect_project = self.project_of.get(effect_entity)
        if cause_project is not None and cause_project == effect_project:
            return LinkBasis.SAME_PROJECT

        # Nothing connects them. Two events in the right order and no relation is
        # a coincidence, and coincidences get no chain.
        return None


def descendants(schedule: ScheduleGraph, entity_id: str) -> set[str]:
    """Everything downstream of a task. The blast radius of a slip."""
    if entity_id not in schedule.graph:
        return set()
    return set(nx.descendants(schedule.graph, entity_id))


def driving_predecessors(
    schedule: ScheduleGraph, entity_id: str, projections: dict[str, date | None]
) -> Sequence[str]:
    """Which upstream tasks actually determine this one's date.

    Not all predecessors matter - only the ones whose finish plus lag lands on or
    after this task's own plan. Those are what a PM has to fix to pull the date in.
    """
    task = schedule.tasks.get(entity_id)
    if task is None:
        return []

    driving: list[str] = []
    for predecessor in schedule.predecessors(entity_id):
        finish = projections.get(predecessor)
        if finish is None:
            continue
        if task.start_date is None or finish >= task.start_date:
            driving.append(predecessor)
    return driving
