"""The burn series - reconstructed backwards, and no database in sight."""

from __future__ import annotations

from datetime import date, datetime

from app.intelligence.effort import Increment, Observation, burn_series


def _scan(day: int, *, changed: bool = True) -> Observation:
    return Observation(scanned_at=datetime(2026, 3, day, 9, 0), changed=changed)


def _logged(entity: str, delta: float, *, lower: int, upper: int) -> Increment:
    return Increment(
        entity_id=entity,
        delta=delta,
        lower=datetime(2026, 3, lower, 9, 0),
        upper=datetime(2026, 3, upper, 9, 0),
    )


def test_the_last_point_always_equals_the_snapshot_total():
    """The invariant the whole backwards walk exists to hold.

    A chart whose final point disagrees with the figure beside it is worse than
    no chart: both are on screen and one of them is wrong.
    """
    series = burn_series(
        [_scan(2), _scan(6), _scan(14)],
        [_logged("QA-001", 6, lower=2, upper=6), _logged("QA-001", 6, lower=6, upper=14)],
        planned_hours=125,
        logged_hours=28,
    )

    assert series.points[-1].logged_hours == 28.0
    assert series.logged_hours == 28.0


def test_the_series_starts_at_the_total_before_any_observed_increment():
    """Row creations are not state changes, so the first point is derived.

    Summing increments forwards would start the chart at zero and never reach
    today's total - the sheet's opening 16 hours arrived with the rows
    themselves and no differ ever saw them appear.
    """
    series = burn_series(
        [_scan(2), _scan(6), _scan(14)],
        [_logged("QA-001", 6, lower=2, upper=6), _logged("QA-001", 6, lower=6, upper=14)],
        planned_hours=125,
        logged_hours=28,
    )

    assert [p.logged_hours for p in series.points] == [16.0, 22.0, 28.0]


def test_an_unchanged_scan_holds_the_line_flat():
    """The flat stretch is evidence, not a gap.

    An unchanged scan proves nobody logged an hour in that window. Dropping it
    would leave a chart that merely fails to show the stall.
    """
    series = burn_series(
        [_scan(2), _scan(6), _scan(10, changed=False), _scan(14)],
        [_logged("QA-001", 6, lower=2, upper=6), _logged("QA-001", 6, lower=10, upper=14)],
        planned_hours=125,
        logged_hours=28,
    )

    assert [p.logged_hours for p in series.points] == [16.0, 22.0, 22.0, 28.0]
    assert [p.changed for p in series.points] == [True, True, False, True]


def test_stalled_from_names_the_first_observation_of_the_trailing_flat_run():
    series = burn_series(
        [_scan(2), _scan(6), _scan(14), _scan(18), _scan(22)],
        [_logged("QA-001", 12, lower=2, upper=6)],
        planned_hours=125,
        logged_hours=28,
    )

    assert series.stalled_from == date(2026, 3, 14)


def test_a_series_still_moving_reports_no_stall():
    """So the page can say "still moving" rather than inventing a stall."""
    series = burn_series(
        [_scan(2), _scan(6)],
        [_logged("QA-001", 6, lower=2, upper=6)],
        planned_hours=125,
        logged_hours=28,
    )

    assert series.stalled_from is None


def test_a_correction_downwards_is_kept():
    """A PM fixing an over-entry is a real edit.

    Clamping it to zero would make the series stop matching the sheet, and the
    sheet is the thing being observed.
    """
    series = burn_series(
        [_scan(2), _scan(6)],
        [_logged("QA-001", -4, lower=2, upper=6)],
        planned_hours=125,
        logged_hours=10,
    )

    assert [p.logged_hours for p in series.points] == [14.0, 10.0]


def test_an_increment_from_an_unknown_scan_still_reaches_the_total():
    """The total wins over the series when they cannot both be honoured.

    An increment whose observing scan is outside the window handed in has
    nowhere to sit. It must still be inside the final figure, because that
    figure comes from the snapshot and the snapshot is not in doubt.
    """
    series = burn_series(
        [_scan(6), _scan(14)],
        [
            _logged("QA-001", 6, lower=1, upper=2),  # a scan we were not given
            _logged("QA-001", 6, lower=6, upper=14),
        ],
        planned_hours=125,
        logged_hours=28,
    )

    assert series.points[-1].logged_hours == 28.0
    assert [p.logged_hours for p in series.points] == [22.0, 28.0]


def test_no_observations_gives_a_plan_and_no_points():
    """A project with no scans yet renders a planned figure and no chart."""
    series = burn_series([], [], planned_hours=40, logged_hours=0)

    assert series.points == []
    assert series.planned_hours == 40.0
    assert series.logged_hours == 0.0
    assert series.stalled_from is None


def test_remaining_is_the_plan_less_what_was_logged():
    series = burn_series(
        [_scan(2)], [], planned_hours=125, logged_hours=28
    )

    assert series.remaining_hours == 97.0


def test_scope_growth_and_blocking_ride_along_on_the_point_that_saw_them():
    """Both are markers on the hours axis, never series of their own.

    A blocked count on the same y-axis as hours would be a dual-axis chart,
    which is the one chart mistake this project will not make.
    """
    series = burn_series(
        [_scan(14), _scan(22)],
        [],
        planned_hours=125,
        logged_hours=28,
        rows_added=[_logged("QA-008", 1, lower=14, upper=22)],
        blocked_added=[
            _logged("QA-001", 1, lower=14, upper=22),
            _logged("QA-006", 1, lower=14, upper=22),
        ],
    )

    assert (series.points[0].items_added, series.points[0].blocked_added) == (0, 0)
    assert (series.points[1].items_added, series.points[1].blocked_added) == (1, 2)


def test_observations_out_of_order_are_sorted():
    """The caller's query ordering is not load-bearing."""
    series = burn_series(
        [_scan(14), _scan(2), _scan(6)],
        [_logged("QA-001", 6, lower=2, upper=6), _logged("QA-001", 6, lower=6, upper=14)],
        planned_hours=125,
        logged_hours=28,
    )

    assert [p.observed_at.day for p in series.points] == [2, 6, 14]
    assert [p.logged_hours for p in series.points] == [16.0, 22.0, 28.0]
