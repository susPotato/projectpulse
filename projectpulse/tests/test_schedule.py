"""Guards for the schedule graph and the forward pass.

The number these protect is `propagated_days`: slip the dependency chain implies
but the sheet does not yet show. It is the one figure in the product a PM cannot
get by reading their own spreadsheet, so it has to be arithmetic they can check by
hand, never an estimate.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.intelligence.schedule.graph import (
    DependencyLinks,
    EdgeRecord,
    build_graph,
    descendants,
)
from app.intelligence.schedule.impact import (
    answer_survives_stated_only,
    project_schedule,
)
from app.intelligence.schedule.graph import TaskNode
from app.intelligence.temporal.templates import LinkBasis

PROJECT = "excel:Project:1:HRMS"


def task(entity_id, start, planned, baseline=None, **kw):
    return TaskNode(
        entity_id=entity_id,
        project_id=kw.pop("project_id", PROJECT),
        title=kw.pop("title", entity_id),
        start_date=start,
        planned_end=planned,
        baseline_end=baseline if baseline is not None else planned,
        **kw,
    )


def edge(pred, succ, *, lag=0, source="excel_predecessor", dep_type="FS"):
    return EdgeRecord(
        predecessor_id=pred,
        successor_id=succ,
        lag_days=lag,
        source=source,
        dep_type=dep_type,
    )


#: The demo chain: environment -> integration -> UAT.
CHAIN_TASKS = [
    task("A", date(2026, 2, 16), date(2026, 3, 4)),
    task("B", date(2026, 3, 5), date(2026, 3, 20)),
    task("C", date(2026, 3, 23), date(2026, 5, 14)),
]
CHAIN_EDGES = [edge("A", "B"), edge("B", "C", lag=2)]


# --------------------------------------------------------------------------
# Building the graph
# --------------------------------------------------------------------------


def test_a_chain_becomes_a_dag():
    schedule = build_graph(CHAIN_TASKS, CHAIN_EDGES)

    assert schedule.edge_count == 2
    assert schedule.topological_order() == ["A", "B", "C"]
    assert not schedule.dropped


def test_only_fs_relations_are_traversed():
    """Treating an SS edge as FS would move dates by the length of a task."""
    schedule = build_graph(CHAIN_TASKS, [edge("A", "B", dep_type="SS")])

    assert schedule.edge_count == 0
    assert "not traversed yet" in schedule.dropped[0][1]


def test_an_edge_to_an_unknown_task_is_refused():
    """Otherwise the forward pass walks off the end of the project."""
    schedule = build_graph(CHAIN_TASKS, [edge("A", "GHOST")])

    assert schedule.edge_count == 0
    assert "endpoint not among" in schedule.dropped[0][1]


def test_a_cycle_across_two_sheets_is_still_refused():
    """Each sheet's edges are acyclic; merged, they need not be."""
    schedule = build_graph(CHAIN_TASKS, [*CHAIN_EDGES, edge("C", "A")])

    assert schedule.edge_count == 2
    assert "cycle" in schedule.dropped[0][1]
    assert schedule.topological_order()  # still sortable, so still projectable


def test_a_stated_only_graph_excludes_inferred_edges():
    edges = [edge("A", "B"), edge("B", "C", source="wbs_implicit")]

    schedule = build_graph(CHAIN_TASKS, edges, stated_only=True)

    assert schedule.edge_count == 1
    assert schedule.stated_only


# --------------------------------------------------------------------------
# DependencyLinks - what the causal engine asks
# --------------------------------------------------------------------------


def test_link_resolution_prefers_the_strongest_relation():
    links = DependencyLinks(build_graph(CHAIN_TASKS, CHAIN_EDGES))

    assert links.link_between("A", "A") is LinkBasis.SAME_ENTITY
    assert links.link_between("A", "B") is LinkBasis.DEPENDENCY_EDGE
    assert links.link_between("A", "C") is LinkBasis.DEPENDENCY_PATH
    assert links.link_between("B", "A") is LinkBasis.SAME_PROJECT  # wrong direction


def test_entities_outside_the_graph_can_still_share_a_project():
    """QA items carry no dependency edges but still belong to the project."""
    links = DependencyLinks(
        build_graph(CHAIN_TASKS, CHAIN_EDGES),
        project_of={"QA-001": PROJECT},
    )

    assert links.link_between("A", "QA-001") is LinkBasis.SAME_PROJECT


def test_entities_in_different_projects_are_not_linked_at_all():
    other = task("Z", date(2026, 1, 1), date(2026, 1, 2), project_id="excel:Project:2:X")
    links = DependencyLinks(build_graph([*CHAIN_TASKS, other], CHAIN_EDGES))

    assert links.link_between("A", "Z") is None


def test_descendants_are_the_blast_radius():
    assert descendants(build_graph(CHAIN_TASKS, CHAIN_EDGES), "A") == {"B", "C"}


# --------------------------------------------------------------------------
# The forward pass
# --------------------------------------------------------------------------


def test_a_consistent_plan_propagates_nothing():
    """If the sheet already agrees with its own dependencies, there is no finding."""
    report = project_schedule(build_graph(CHAIN_TASKS, CHAIN_EDGES))

    assert all(p.propagated_days == 0 for p in report.projections.values())
    assert report.inconsistent() == []


def test_an_upstream_slip_propagates_to_a_date_nobody_wrote_down():
    """The headline number.

    A finishes 12 days late. Nobody updated B or C, so the sheet still claims the
    old dates - and the chain says they cannot hold.
    """
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), baseline=date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20)),
        task("C", date(2026, 3, 23), date(2026, 5, 14)),
    ]

    report = project_schedule(build_graph(tasks, CHAIN_EDGES))

    b = report.projections["B"]
    assert b.projected_end == date(2026, 3, 31)  # 15-day task, now starting 3/16
    assert b.propagated_days == 11
    assert b.recorded_slip_days == 0  # the sheet still shows no slip at all
    assert report.projections["C"].is_inconsistent


def test_the_driving_path_is_the_chain_that_sets_the_date():
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), baseline=date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20)),
        task("C", date(2026, 3, 23), date(2026, 5, 14)),
    ]

    report = project_schedule(build_graph(tasks, CHAIN_EDGES))

    assert report.driving_path == ["A", "B", "C"]
    assert report.project_end_projected > report.project_end_planned
    assert report.project_slip_days > 0


def test_a_task_never_finishes_early_because_its_predecessor_was_quick():
    """A plan is a commitment, not an estimate."""
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 2, 20)),
        task("B", date(2026, 3, 5), date(2026, 3, 20)),
    ]

    report = project_schedule(build_graph(tasks, [edge("A", "B")]))

    assert report.projections["B"].projected_end == date(2026, 3, 20)
    assert report.projections["B"].propagated_days == 0


def test_lag_is_applied_to_the_successor_start():
    tasks = [
        task("A", date(2026, 3, 1), date(2026, 3, 10)),
        task("B", date(2026, 3, 11), date(2026, 3, 15)),
    ]

    with_lag = project_schedule(build_graph(tasks, [edge("A", "B", lag=5)]))

    # B is a 4-day task; it can now only start on 3/15, so it finishes 3/19.
    assert with_lag.projections["B"].projected_end == date(2026, 3, 19)


def test_a_task_with_no_baseline_reports_no_variance_rather_than_zero():
    """Inventing a baseline from the plan would report zero slip forever."""
    tasks = [TaskNode(entity_id="A", project_id=PROJECT, planned_end=date(2026, 3, 4))]

    report = project_schedule(build_graph(tasks, []))

    assert report.projections["A"].variance_days is None
    assert report.projections["A"].recorded_slip_days is None


def test_affected_milestones_are_only_the_ones_actually_at_risk():
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), baseline=date(2026, 3, 4),
             milestone_id="M-ENV"),
        task("B", date(2026, 3, 5), date(2026, 3, 20), milestone_id="M-DEV"),
        TaskNode(entity_id="Z", project_id=PROJECT, start_date=date(2026, 1, 1),
                 planned_end=date(2026, 1, 5), baseline_end=date(2026, 1, 5),
                 milestone_id="M-DONE"),
    ]

    report = project_schedule(build_graph(tasks, [edge("A", "B")]))

    assert report.affected_milestones == ["M-DEV"]


# --------------------------------------------------------------------------
# Stated vs inferred
# --------------------------------------------------------------------------


def test_a_conclusion_resting_on_an_inferred_edge_is_detectable():
    """A PM taking a date to a steering committee may ask where it came from."""
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), baseline=date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20)),
    ]
    edges = [edge("A", "B", source="wbs_implicit")]

    full, stated = answer_survives_stated_only(tasks, edges)

    assert full.projections["B"].propagated_days == 11
    assert stated.projections["B"].propagated_days == 0
    assert full.project_end_projected != stated.project_end_projected


def test_a_conclusion_from_stated_edges_survives_the_check():
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), baseline=date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20)),
    ]

    full, stated = answer_survives_stated_only(tasks, [edge("A", "B")])

    assert full.project_end_projected == stated.project_end_projected


@pytest.mark.parametrize("stated_only", [False, True])
def test_an_empty_project_does_not_crash(stated_only):
    report = project_schedule(build_graph([], [], stated_only=stated_only))

    assert report.projections == {}
    assert report.project_slip_days is None
