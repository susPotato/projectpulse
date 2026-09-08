"""Cumulative logged effort over time, from what the differ actually saw.

A burn chart is the one PM visual that is almost always fiction. The usual
version plots a self-reported "remaining" figure that somebody typed, against
an ideal line that was never a commitment. This one is built from observations:
every point is a scan of the worklog sheet, and every rise between two points
is a `hours_spent` state change the differ detected, bounded to the interval
between those two scans.

That constrains the shape of the module, and the constraints are the point:

**It works backwards from the current total.** A row's creation is not a
`state_change` - only its later edits are - so summing increments forwards
would start from zero and never reach today's figure. Subtracting the
increments observed *after* a scan from the known total gives that scan's
total exactly, with no reconstruction of row creations.

**The planned line is one value, not a series.** `estimate_hours` is not in
`WORKLOG_CONTRACT.tracked_fields`, so we have never observed the plan change
and cannot draw where it used to be. Drawing a sloping planned line would be
inventing the history of a number we only know the present value of. Scope
growth is reported as a count of rows added, which we did observe, rather than
as a second line nobody measured.

**No second y-axis.** The blocked count is the other half of this story and it
is on a different scale entirely; it arrives as a marker on the point where it
was observed to move, never as a series sharing the hours axis.

Pure: dates, floats and dataclasses. `pipeline.team_project` supplies the rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Increment:
    """One observed change to a logged-hours figure.

    `lower` and `upper` are the scan interval it fell in - the same bounded
    precision every Excel-sourced change carries. `delta` may be negative: a PM
    correcting an over-entry is a real edit, and silently clamping it would make
    the series stop matching the sheet.
    """

    entity_id: str
    delta: float
    lower: datetime
    upper: datetime


@dataclass(frozen=True)
class Observation:
    """One scan of the worklog sheet.

    `changed` is carried through because an unchanged scan is the evidence that
    the flat stretch in the chart is real rather than missing data - the
    distinction between "nobody logged anything" and "we did not look".
    """

    scanned_at: datetime
    changed: bool = True


@dataclass(frozen=True)
class BurnPoint:
    """Cumulative logged effort as at one observation."""

    observed_at: date
    logged_hours: float
    #: Logged since the previous observation. Zero on a scan where the sheet
    #: was untouched, which is what flattens the line.
    delta_hours: float
    #: How many worklog rows were seen to be added in this window. Scope
    #: growth, observed rather than assumed.
    items_added: int = 0
    #: How many rows were seen to become blocked in this window. A marker, not
    #: a series - it does not share the hours axis.
    blocked_added: int = 0
    #: False when the bytes were identical to the previous scan.
    changed: bool = True


@dataclass(frozen=True)
class BurnSeries:
    """The chart, and the sentence that has to be true for it to be honest."""

    planned_hours: float
    points: list[BurnPoint] = field(default_factory=list)

    @property
    def logged_hours(self) -> float:
        return self.points[-1].logged_hours if self.points else 0.0

    @property
    def remaining_hours(self) -> float:
        return round(self.planned_hours - self.logged_hours, 2)

    @property
    def stalled_from(self) -> date | None:
        """The first observation of the trailing run where nothing was logged.

        The finding the chart exists to make visible. `None` when effort is
        still being logged, so the page can say "still moving" rather than
        drawing attention to a stall that is not there.
        """
        if len(self.points) < 2:
            return None
        stalled = None
        for point in reversed(self.points[1:]):
            if point.delta_hours:
                break
            stalled = point.observed_at
        return stalled


def burn_series(
    observations: list[Observation],
    increments: list[Increment],
    *,
    planned_hours: float,
    logged_hours: float,
    rows_added: list[Increment] = (),
    blocked_added: list[Increment] = (),
) -> BurnSeries:
    """Cumulative logged effort at each observation.

    `logged_hours` is today's total from the current snapshot, and the series is
    reconstructed backwards from it - see the module docstring for why forwards
    does not work.

    An increment is attributed to the observation whose `scanned_at` equals its
    `upper` bound: that is the scan that *detected* it, and it is the only
    instant we can honestly place it at without narrowing an interval the
    precision model says stays open. Where no observation matches - a scan
    outside the window we were handed - the increment still counts toward the
    total, so the last point always equals the snapshot.
    """
    if not observations:
        return BurnSeries(planned_hours=round(planned_hours, 2))

    ordered = sorted(observations, key=lambda o: o.scanned_at)

    def _by_scan(items) -> dict[datetime, list[Increment]]:
        grouped: dict[datetime, list[Increment]] = {}
        for item in items:
            grouped.setdefault(item.upper, []).append(item)
        return grouped

    deltas = _by_scan(increments)
    added = _by_scan(rows_added)
    blocked = _by_scan(blocked_added)

    # Walk backwards, subtracting each window's increments from the running
    # total. `running` is the total as at the observation being written, so it
    # is decremented only after that point is built.
    points: list[BurnPoint] = []
    running = round(logged_hours, 2)
    for observation in reversed(ordered):
        at = observation.scanned_at
        window = deltas.get(at, [])
        delta = round(sum(item.delta for item in window), 2)
        points.append(
            BurnPoint(
                observed_at=at.date(),
                logged_hours=running,
                delta_hours=delta,
                items_added=len(added.get(at, [])),
                blocked_added=len(blocked.get(at, [])),
                changed=observation.changed,
            )
        )
        running = round(running - delta, 2)

    points.reverse()
    return BurnSeries(planned_hours=round(planned_hours, 2), points=points)
