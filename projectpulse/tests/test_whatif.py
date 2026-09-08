"""Guards for the recovery-scenario engine.

Pure, so all of this runs without a database. What is being protected is mostly
honesty rather than arithmetic: the engine's whole value is that a scenario's
figures are the forward pass re-run, so the tests that matter are the ones that
stop it flattering itself.
"""

from __future__ import annotations

from datetime import date

from app.intelligence.schedule.graph import EdgeRecord, TaskNode, build_graph
from app.intelligence.schedule.impact import project_schedule
from app.intelligence.schedule.whatif import (
    MAX_COMPRESS_DAYS,
    Move,
    _apply,
    recovery_scenarios,
)

PROJECT = "excel:Project:1:HRMS"


def _task(name: str, start: date, end: date) -> TaskNode:
    return TaskNode(
        entity_id=f"excel:Task:1:{name}",
        project_id=PROJECT,
        title=name.lower(),
        status="In Progress",
        start_date=start,
        planned_end=end,
    )


def _edge(pred: str, succ: str, lag: int = 0) -> EdgeRecord:
    return EdgeRecord(
        predecessor_id=f"excel:Task:1:{pred}",
        successor_id=f"excel:Task:1:{succ}",
        dep_type="FS",
        lag_days=lag,
        source="excel_predecessor",
    )


def _chain():
    """A two-link chain whose plan cannot survive its own dependencies.

    B is planned to finish before A's projection allows it to start, so the slip
    is real rather than arranged by the test.
    """
    tasks = [
        _task("A", date(2026, 3, 1), date(2026, 3, 31)),
        _task("B", date(2026, 3, 10), date(2026, 4, 10)),
        _task("C", date(2026, 4, 11), date(2026, 4, 30)),
    ]
    edges = [_edge("A", "B"), _edge("B", "C")]
    return tasks, edges


def _baseline(tasks, edges):
    return project_schedule(build_graph(tasks, edges))


def test_the_chain_is_actually_late_before_anything_is_simulated():
    """The fixture has to be inconsistent or every assertion below is vacuous."""
    tasks, edges = _chain()

    assert (_baseline(tasks, edges).project_slip_days or 0) > 0


def test_applying_a_move_never_mutates_the_caller_s_rows():
    """A simulation is not a mutation - it is what keeps the app read-only.

    If `_apply` edited in place, the baseline it is compared against would
    change underneath it and every scenario would report zero.
    """
    tasks, edges = _chain()
    before_ends = [t.planned_end for t in tasks]
    before_lags = [e.lag_days for e in edges]

    _apply(
        tasks,
        edges,
        (
            Move(kind="compress", entity_id="excel:Task:1:A", label="A", days=5),
            Move(
                kind="overlap",
                entity_id="excel:Task:1:B",
                label="B",
                days=5,
                against_id="excel:Task:1:A",
                against_label="A",
            ),
        ),
    )

    assert [t.planned_end for t in tasks] == before_ends
    assert [e.lag_days for e in edges] == before_lags


def test_compressing_the_first_task_pulls_delivery_earlier():
    tasks, edges = _chain()
    base = _baseline(tasks, edges)

    scenarios = recovery_scenarios(tasks, edges, baseline=base)

    assert scenarios, "a late chain must yield at least one scenario"
    best = scenarios[0]
    assert best.days_earlier > 0
    assert best.projected_end is not None
    assert best.projected_end < base.project_end_projected


def test_a_scenario_is_measured_against_the_original_commitment():
    """The subtle one, and the reason `days_late` exists separately.

    Compressing a task moves `project_end_planned` as well as the projection, so
    a scenario compared against its *own* plan would report "on time" for having
    moved the goalposts. `days_late` is measured against the plan as it stands
    today.
    """
    tasks, edges = _chain()
    base = _baseline(tasks, edges)
    committed = base.project_end_planned
    assert committed is not None

    for scenario in recovery_scenarios(tasks, edges, baseline=base):
        assert scenario.projected_end is not None
        assert scenario.days_late == (scenario.projected_end - committed).days


def test_scenarios_with_the_same_outcome_are_collapsed():
    """Three rows reading "22 Jun, 10 days earlier" look like a broken sum.

    They are genuinely different choices with one outcome; showing every one
    reads as a miscalculation, so the simplest survives.
    """
    tasks, edges = _chain()

    scenarios = recovery_scenarios(tasks, edges, baseline=_baseline(tasks, edges))
    outcomes = [s.outcome for s in scenarios]

    assert len(outcomes) == len(set(outcomes))


def test_a_consistent_plan_yields_no_scenarios():
    """Nothing to recover, so nothing is offered.

    A list of zero-day scenarios would teach a reader the feature is broken.
    """
    tasks = [
        _task("A", date(2026, 3, 1), date(2026, 3, 31)),
        _task("B", date(2026, 4, 1), date(2026, 4, 30)),
    ]
    edges = [_edge("A", "B")]

    assert recovery_scenarios(tasks, edges) == []


def test_only_the_driving_path_is_offered():
    """A task the finish date does not depend on cannot move the finish date.

    Offering it would imply otherwise, which is worse than offering nothing.
    """
    tasks, edges = _chain()
    # A long task hanging off nothing, nowhere near the driving path.
    tasks.append(_task("SIDE", date(2026, 3, 1), date(2026, 3, 20)))

    scenarios = recovery_scenarios(tasks, edges, baseline=_baseline(tasks, edges))
    touched = {move.label for s in scenarios for move in s.moves}

    assert "SIDE" not in touched


def test_no_single_compression_claims_more_than_the_cap():
    """The premise has to stay credible even where the arithmetic would allow."""
    tasks, edges = _chain()

    scenarios = recovery_scenarios(tasks, edges, baseline=_baseline(tasks, edges))

    for scenario in scenarios:
        for move in scenario.moves:
            assert move.days <= MAX_COMPRESS_DAYS


def test_a_scenario_summary_carries_no_digits():
    """Figures are fields, not prose - invariant 1 reaches this module too.

    The UI renders `days_earlier` and `projected_end`; a number baked into the
    sentence would be a second place a figure is formatted.
    """
    tasks, edges = _chain()

    for scenario in recovery_scenarios(tasks, edges, baseline=_baseline(tasks, edges)):
        assert not any(char.isdigit() for char in scenario.summary), scenario.summary
