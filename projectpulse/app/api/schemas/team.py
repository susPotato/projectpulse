"""Who is carrying what, and what has moved - from data the sheets actually have.

The design and the function list ask for a burn chart, a member calendar and
productivity tiles. This serves the ones the source data supports and says
plainly which it does not, because the alternative is a chart of invented
numbers, and a chart is the most persuasive way to be wrong.

**Available, and served here**

``members``
    Every assignee, their dated tasks, and the QA hours logged against them.
    Tasks carry `Owner`, `Start` and `Planned Finish`; the worklog sheet
    carries `Owner` and `Hours`. Both are real columns a PM fills in.

``activity``
    Observed state changes per week. What actually moved, from the differ
    rather than from a self-reported percentage - the count of edits beside the
    ``burn``'s hours, and the reason a flat burn can be read as a real stall.

``burn``
    Cumulative logged effort against planned effort, one point per scan of the
    worklog sheet. This panel was absent for a while and the reason it now
    exists is worth keeping: the worklog sheet had actual `Hours` and no planned
    column, and those hours carried **no date** - `log_date` was parsed by the
    contract and dropped. Hours with no baseline and no time axis are a total,
    not a series, and a burn chart drawn from a total is drawn from nothing.

    Both columns were added to the blank template, which we own - the same move
    that closed the dependency-edge gap by adding `Predecessor`. What was NOT
    done is worth stating too: the log dates were not back-derived from scan
    windows. A scan interval bounds when we *noticed* an edit, not when the
    work happened, and turning one into the other would invent precision the
    source never had.

    `Hours` is a tracked field, so every increment is a real `state_change` and
    the series is reconstructed from observations rather than from the final
    sheet. See `intelligence/effort.py`.

**Not available, and deliberately absent**

*A planned-effort line that moves.* `estimate_hours` is not tracked, so we have
never observed the plan change and cannot say where it used to be. It is one
reference value, and scope growth is reported as the count of rows we saw
added.

*Productivity as output per unit of effort.* The effort half now exists; the
output half does not. `progress` is a self-reported percentage, so a ratio
built on it would inherit that and present it as measurement. Effort variance -
logged against planned, both real columns - is served instead, because its
derivation can be shown.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.api.schemas.base import Response


class MemberTask(Response):
    """One dated task on someone's plate."""

    entity_id: str
    label: str
    title: str | None = None
    start: date | None = None
    planned_end: date | None = None
    #: Where the dependency chain says it actually lands.
    projected_end: date | None = None
    #: Slip the chain implies and the sheet does not show.
    propagated_days: int | None = None
    phase: str | None = None
    progress: float | None = None


class Member(Response):
    """One person, and everything the sheets say they are carrying."""

    name: str
    tasks: list[MemberTask] = Field(default_factory=list)
    #: QA hours logged against this owner. Actual, never planned.
    hours_logged: float = 0.0
    #: Planned effort on the same rows, from the worklog's `Estimate` column.
    #: The pair is the only honest variance figure available - both halves are
    #: columns a person filled in, so the derivation can be shown.
    hours_planned: float = 0.0
    qa_items: int = 0
    qa_blocked: int = 0

    @property
    def days_committed(self) -> int:
        """Calendar days spanned by their dated tasks - not effort.

        Named carefully: it is the span their work covers, and it says nothing
        about how full those days are. Calling it capacity would be a claim the
        data cannot support.
        """
        spans = [
            (t.planned_end - t.start).days
            for t in self.tasks
            if t.start and t.planned_end
        ]
        return sum(spans)


class ActivityWeek(Response):
    """What moved in one week, from the differ rather than from a report."""

    week_start: date
    changes: int = 0
    #: Timestamped by a changelog. Both bounds equal.
    exact: int = 0
    #: Seen by comparing two snapshots, so dated to an interval.
    bounded: int = 0


class BurnPoint(Response):
    """Cumulative logged effort as at one scan of the worklog."""

    observed_at: date
    logged_hours: float = 0.0
    #: Logged since the previous scan. Zero where the sheet was untouched,
    #: which is what makes a flat stretch evidence rather than a gap.
    delta_hours: float = 0.0
    #: Worklog rows seen to be added in this window - scope growth, observed.
    items_added: int = 0
    #: Rows seen to become blocked. A marker on the point, never a second
    #: series: it is on a different scale and would need a second y-axis.
    blocked_added: int = 0
    #: False when the sheet's bytes were identical to the previous scan.
    changed: bool = True


class BurnSeries(Response):
    """Effort logged over time against the plan it is burning."""

    #: One value, not a series: we never observed the plan change. See the
    #: module docstring.
    planned_hours: float = 0.0
    logged_hours: float = 0.0
    remaining_hours: float = 0.0
    #: The first scan of the trailing run where nothing was logged, or absent
    #: while effort is still moving.
    stalled_from: date | None = None
    points: list[BurnPoint] = Field(default_factory=list)


class TeamBundle(Response):
    project_id: str
    members: list[Member] = Field(default_factory=list)
    activity: list[ActivityWeek] = Field(default_factory=list)
    burn: BurnSeries = Field(default_factory=BurnSeries)

    #: The shared time window every member row is drawn against, so two rows
    #: are comparable by eye.
    window_start: date | None = None
    window_end: date | None = None

    total_hours: float = 0.0
    #: True when no sheet carried an Hours column with anything in it, so the
    #: page can say why an effort panel is empty rather than showing a zero.
    has_effort_data: bool = False
