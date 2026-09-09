"""The probabilistic forecast, and the three cases where it refuses.

The refusals carry as much weight as the range. A forecast that always produces
a number is a forecast that produces one when it cannot support one, and the
figure it invents is the one a steering committee repeats.

Pure, like the module: no session, no database, no clock.
"""

from __future__ import annotations

from datetime import date

from app.intelligence.schedule.forecast import (
    MIN_OBSERVATIONS,
    Forecast,
    forecast_project,
    observed_drift,
)
from app.intelligence.schedule.graph import EdgeRecord, TaskNode


def _task(
    key: str,
    start: date | None,
    planned: date | None,
    baseline: date | None = None,
    status: str = "In Progress",
) -> TaskNode:
    return TaskNode(
        entity_id=f"excel:Task:1:{key}",
        project_id="excel:Project:1:HRMS",
        title=key,
        status=status,
        start_date=start,
        planned_end=planned,
        baseline_end=baseline,
    )


def _chain(drifts: list[int], *, length: int = 3) -> tuple[list[TaskNode], list[EdgeRecord]]:
    """A dated chain, plus enough baselined tasks to carry `drifts` as a sample."""
    tasks = [
        _task(f"W{i}", date(2026, 1, 1 + i * 10), date(2026, 1, 8 + i * 10))
        for i in range(length)
    ]
    edges = [
        EdgeRecord(
            predecessor_id=tasks[i].entity_id, successor_id=tasks[i + 1].entity_id
        )
        for i in range(length - 1)
    ]
    # The sample lives on separate tasks so a test can set the spread without
    # also moving the chain the forecast projects. The baseline is placed
    # `drift` days before the plan, so `planned - baseline == drift` exactly.
    planned = date(2026, 3, 1)
    for index, drift in enumerate(drifts):
        tasks.append(
            _task(
                f"S{index}",
                date(2026, 2, 1),
                planned,
                baseline=date.fromordinal(planned.toordinal() - drift),
            )
        )
    return tasks, edges


# --------------------------------------------------------------------------
# The sample
# --------------------------------------------------------------------------

def test_a_task_with_no_baseline_is_not_a_zero():
    """Absence of a baseline is unknown drift, never zero drift.

    Counting it as zero would pull every percentile toward the current plan, so
    a team that kept no baselines would forecast itself as certain - the exact
    inversion of what missing data should do to a claim.
    """
    tasks = [
        _task("A", date(2026, 1, 1), date(2026, 1, 8), baseline=date(2026, 1, 1)),
        _task("B", date(2026, 1, 1), date(2026, 1, 8), baseline=None),
    ]

    sample = observed_drift(tasks)

    assert [o.label for o in sample] == ["A"]


def test_drift_is_measured_from_the_commitment_to_the_plan():
    tasks = [_task("A", date(2026, 1, 1), date(2026, 1, 20), baseline=date(2026, 1, 8))]

    assert observed_drift(tasks)[0].days == 12


# --------------------------------------------------------------------------
# The three refusals
# --------------------------------------------------------------------------

def test_too_few_observations_refuses_with_a_reason():
    """A percentile over three points is arithmetic theatre."""
    tasks, edges = _chain([0, 12])

    result = forecast_project(tasks, edges)

    assert result.available is False
    assert str(MIN_OBSERVATIONS) in result.reason
    assert "Baseline" in result.reason  # names the column that would fix it
    assert result.points == ()


def test_a_sample_with_no_variance_refuses():
    """Every task drifting by the same amount gives one date, not a range.

    This is the case the demo data was actually in before the baseline column
    was used as the sample, and it is the one most likely to recur: a generated
    or bulk-edited plan moves everything by the same step.
    """
    tasks, edges = _chain([12, 12, 12, 12, 12, 12])

    result = forecast_project(tasks, edges)

    assert result.available is False
    assert "no observed variation" in result.reason
    assert "12" in result.reason
    assert result.observations == 6  # it still reports what it looked at


def test_a_finished_project_has_a_result_rather_than_a_forecast():
    tasks, edges = _chain([0, 5, 12, 3, 8, 1])
    tasks = [
        TaskNode(
            entity_id=t.entity_id,
            project_id=t.project_id,
            title=t.title,
            status="Done",
            start_date=t.start_date,
            planned_end=t.planned_end,
            baseline_end=t.baseline_end,
        )
        for t in tasks
    ]

    result = forecast_project(tasks, edges)

    assert result.available is False
    assert "nothing is left to move" in result.reason


def test_an_unknown_status_counts_as_still_open():
    """Assuming a task is finished is the error that shortens a forecast."""
    tasks, edges = _chain([0, 5, 12, 3, 8, 1])
    tasks = [
        TaskNode(
            entity_id=t.entity_id,
            project_id=t.project_id,
            title=t.title,
            status="Awaiting sign-off from vendor",
            start_date=t.start_date,
            planned_end=t.planned_end,
            baseline_end=t.baseline_end,
        )
        for t in tasks
    ]

    assert forecast_project(tasks, edges).available is True


# --------------------------------------------------------------------------
# The range
# --------------------------------------------------------------------------

def _good() -> Forecast:
    tasks, edges = _chain([0, 5, 12, 3, 8, 1])
    return forecast_project(tasks, edges, trials=400)


def test_the_percentiles_only_ever_move_later():
    """P50 <= P80 <= P95. A range that crosses itself is a broken sort."""
    result = _good()

    finishes = [p.finish for p in result.points]
    assert finishes == sorted(finishes)
    assert [p.percentile for p in result.points] == [50, 80, 95]


def test_the_same_data_always_gives_the_same_range():
    """Seeded from the observations themselves.

    A range that shifts on every refresh with no new data destroys confidence in
    the whole screen - and the page, the CLI and the .docx have to agree to the
    day.
    """
    first, second = _good(), _good()

    assert [p.finish for p in first.points] == [p.finish for p in second.points]


def test_a_different_sample_gives_a_different_seed():
    """Two samples can share a total, so the seed is hashed, not summed."""
    a, _ = _chain([0, 5, 12, 3, 8, 1])
    b, edges = _chain([1, 4, 12, 3, 8, 1])

    # Same totals, different observations - the forecasts must not be identical
    # by construction. (They may coincide; the seeds must not.)
    from app.intelligence.schedule.forecast import _seed, observed_drift

    assert _seed(observed_drift(a)) != _seed(observed_drift(b))


def test_lateness_is_measured_against_the_commitment_not_the_current_plan():
    """The re-baselining trap `whatif.days_late` already avoids.

    Measured against the current plan, a project that keeps moving its own dates
    reports a shrinking number every time it slips.
    """
    result = _good()
    committed = result.committed_end
    assert committed is not None

    for point in result.points:
        assert point.days_late == (point.finish - committed).days


def test_the_sample_is_carried_so_a_reader_can_check_it():
    """`observations` alone is a claim; the rows are what make it checkable."""
    result = _good()

    assert result.observations == len(result.sample) == 6
    assert sorted(o.days for o in result.sample) == [0, 1, 3, 5, 8, 12]


def test_the_forecast_is_never_earlier_than_doing_nothing():
    """Resampled drift is additive, so the range starts at the forward pass.

    A forecast that could come back earlier than "nothing else moves" would mean
    a task was given negative drift, which no observation here can be unless the
    plan moved *earlier* than its baseline - and then it is real.
    """
    result = _good()
    assert result.projected_end is not None

    assert result.point(50).finish >= result.projected_end
