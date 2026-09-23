"""The contract for the schedule view.

A read-only Gantt, and deliberately read-only. This product is an intelligence
layer over Jira and Excel, not a system of record: the precision model in
`intelligence/temporal/ordering.py` exists *because* we observe someone else's
spreadsheet rather than own it. Make this editable and every change becomes
`precision='exact'`, `bounded` intervals stop existing, and the most defensible
engineering in the project turns into dead code. The PM keeps editing in Excel
and presses Sync.

Three dates per row, which is the whole point of showing it as a chart:

``baseline_end``
    what was originally committed.
``planned_end``
    what the sheet says today. The gap from baseline is slip a human typed in.
``projected_end``
    what the dependency chain implies. The gap from *planned* is slip nobody has
    written down - the one bar on this chart a PM cannot draw from their own file.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.api.schemas.base import Response


class GanttRow(Response):
    """One task, as a row of bars."""

    entity_id: str
    label: str
    title: str | None = None
    status: str | None = None
    assignee: str | None = None
    #: What kind of work this row is - the tracker's own issue type, not a
    #: judgement. On the CoWorkLocal export it separates 16 `PM Task` rows
    #: from 173 `Task` rows, and the two report themselves completely
    #: differently: the coding side says 82% done, the management side says
    #: nothing is finished at all. A board that pools them shows neither.
    phase: str | None = None

    start: date | None = None
    baseline_end: date | None = None
    planned_end: date | None = None
    projected_end: date | None = None
    #: When the work actually finished, from the tracker's changelog. A chart
    #: that holds this alongside `planned_end` can draw a bar that *ended*
    #: rather than one that merely stopped being updated.
    actual_end: date | None = None

    #: Days between a passed planned finish and today, on a row still open.
    #:
    #: Absent until now, and the Schedule page's "most overdue" ordering read
    #: it anyway - so that sort compared `undefined` against `undefined` on
    #: every row and did nothing at all, on a project with thirty-four late
    #: tasks. Same rule and same import as `MemberTask.days_past_due`
    #: (`agent.brief.CLOSED`), because two definitions of late is how a chart
    #: and a chat come to disagree about the same task.
    #:
    #: `None` on a row that is closed or not yet due - "not late" and "late by
    #: nothing" are different answers and must not share a zero.
    days_past_due: int | None = None
    #: Slip the chain implies that the sheet does not show.
    propagated_days: int | None = None
    #: Slip a human already recorded against the baseline.
    recorded_slip_days: int | None = None
    is_inconsistent: bool = False

    progress: float | None = None
    milestone_id: str | None = None
    milestone_name: str | None = None

    #: Predecessor entity ids, for the dependency arrows.
    depends_on: list[str] = Field(default_factory=list)
    #: True when this task sits on the chain that sets the project's finish - the
    #: only sequence a PM can shorten to pull the date in.
    on_driving_path: bool = False


class GanttMilestone(Response):
    """A milestone marker, dated from the last task beneath it."""

    id: str
    name: str
    planned_date: date | None = None
    baseline_date: date | None = None
    #: Days between the two, so a slipped marker can be drawn as slipped.
    slipped_days: int | None = None
    at_risk: bool = False


class GanttBundle(Response):
    """Everything the schedule view renders, for one project."""

    project_id: str
    rows: list[GanttRow] = Field(default_factory=list)
    milestones: list[GanttMilestone] = Field(default_factory=list)

    #: The time window every row shares. A Gantt with per-row scales is not a
    #: Gantt - the whole value is that two bars can be compared by eye.
    window_start: date | None = None
    window_end: date | None = None
    #: The scan time the view reflects, drawn as a marker.
    as_of: date | None = None

    driving_path: list[str] = Field(default_factory=list)
    project_end_planned: date | None = None
    project_end_projected: date | None = None

    @property
    def inconsistent_count(self) -> int:
        return sum(1 for row in self.rows if row.is_inconsistent)
