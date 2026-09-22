"""Convertor: Jira tool rows -> the vendor-neutral domain model.

This is where Jira changelogs become `precision='exact'` state changes, and it is
the reason a portfolio with Jira in it can support causal ordering that a
spreadsheet-only portfolio cannot. A changelog entry timestamps the transition
itself, so both bounds of the interval collapse onto one instant.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.ids import domain_id
from app.ingest.programs import ensure_program
from app.ingest.status import STATUS_MAP as _STATUS_MAP, normalise
from app.models.domain import (
    PRECISION_EXACT,
    Project,
    StateChange,
    Task,
)
from app.models.tool import ToolJiraChangelog, ToolJiraIssue

SOURCE = "jira"

#: Jira field name -> our canonical field. Anything absent is deliberately not a
#: delivery event: a description or summary edit must never enter a causal chain.
TRACKED_FIELDS = {
    "duedate": "planned_end",
    "status": "status",
    "assignee": "assignee",
    "priority": "priority",
    "Sprint": "sprint",
}

#: The shared vocabulary lives in `app/ingest/status.py`. This module used to
#: carry its own five-entry map, which had never heard of `Release`,
#: `Cancelled` or `Re-Open` - the three commonest states on the board it
#: reads - so 152 of 190 tasks normalised to OTHER and were counted as open.
STATUS_MAP = _STATUS_MAP


def _mapped_or_raw(value: str | None) -> str | None:
    """The shared vocabulary's word for this status, else the original."""
    if not value:
        return value
    return STATUS_MAP.get(value.strip().lower(), value)


def _to_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def ensure_project(session, *, connection_id: int, project_key: str, name: str) -> str:
    """Create the program/project rows the tasks hang off, if absent.

    The program is resolved from `app.scope` rather than built from `SOURCE` -
    see `app/ingest/programs.py`. Doing it the old way here is what created a
    second program row for the program the Excel side had already created one
    for, and it is why the Jira-side HRMS project belonged to a different
    program than the Excel-side HRMS project that is the same delivery.

    Order matters: the project id is needed *before* the program can be
    resolved, because resolution goes through the project's pairing.
    """
    project_id = domain_id(SOURCE, "Project", connection_id, project_key)
    program_id = ensure_program(session, project_id)

    if session.get(Project, project_id) is None:
        session.add(
            Project(
                id=project_id,
                program_id=program_id,
                name=name,
                phase="Development",
                status="Active",
            )
        )
    return project_id


def _issue_ids(session, *, connection_id: int, project_key: str) -> set[str]:
    """The tool-layer issue ids belonging to one Jira project."""
    return set(session.scalars(
        select(ToolJiraIssue.issue_id).where(
            ToolJiraIssue.connection_id == connection_id,
            ToolJiraIssue.project_key == project_key,
        )
    ).all())


def _keys_by_issue_id(session, *, connection_id: int) -> dict[str, str]:
    """issue id -> issue key, because a changelog row only carries the id.

    Tasks are keyed by the issue *key*, so a state change has to resolve the
    same way or it names a task that does not exist - which is silent: the
    row is written, joins to nothing, and every page shows one fewer event
    than the tracker recorded.
    """
    return {
        issue_id: key
        for issue_id, key in session.execute(
            select(ToolJiraIssue.issue_id, ToolJiraIssue.issue_key)
            .where(ToolJiraIssue.connection_id == connection_id)
        ).all()
        if key
    }


def convert_issues(session, *, connection_id: int, project_id: str,
                   project_key: str | None = None) -> int:
    """`_tool_jira_issues` -> `tasks`.

    `project_key` scopes the conversion to the one Jira project this
    connection names. Without it the filter is the connection alone, and a
    connection's tool rows are not guaranteed to be single-project: the
    seeded demo writes HRMS rows against connection 1, so converting
    COWORKLOCAL swept four HRMS issues into the CoWorkLocal project and
    filed them under the wrong delivery. Callers that know their key should
    pass it; the argument is optional only so existing callers keep working.
    """
    count = 0
    query = select(ToolJiraIssue).where(
        ToolJiraIssue.connection_id == connection_id)
    if project_key:
        query = query.where(ToolJiraIssue.project_key == project_key)
    rows = session.scalars(query).all()

    for tool in rows:
        task = Task(
            # Keyed by the issue *key*, not the numeric id. `entity_label`
            # prints the last component of a domain id, so keying by id put
            # "1101143" on the Team page where the person expects
            # "COWORKLOCAL-10". The key is equally stable and is what
            # everybody actually calls the ticket.
            id=domain_id(SOURCE, "Task", connection_id,
                         tool.issue_key or tool.issue_id),
            project_id=project_id,
            title=tool.summary,
            status=normalise(tool.status),
            original_status=tool.status,
            assignee=tool.assignee,
            due_date=_to_date(tool.due_date),
            # The tracker's own issue type. It is the only grouping Jira
            # carries here - `components` is empty on all 194 issues - and it
            # is the real management/delivery split: PM Task and Product
            # against Story. Left as the vendor's word rather than mapped to
            # a vocabulary of ours, because the page shows it to the person
            # who typed it.
            phase=tool.issue_type or None,
        )
        task.copy_origin_from(tool)
        session.merge(task)
        count += 1

    return count


#: Statuses that mean work is underway. A transition *into* one of these is
#: the first moment the tracker recorded anybody actually working.
#:
#: **Not "anything that is not TODO".** That was the first rule here and it
#: was wrong in a way that manufactured data: on this board 153 of 156
#: tickets carry exactly one status change ever, and it is the closing one -
#: `To Do -> Release` in a single step. "The first move out of To Do" was
#: therefore the same event as "the last move into a closed state", so every
#: finished task got a start equal to its finish, and the chart drew 151
#: zero-length bars implying the work began the day it ended.
STARTED = frozenset({"IN_PROGRESS", "BLOCKED"})

#: Statuses that mean the work will not move again. `DROPPED` is here with
#: `DONE` because a cancelled ticket is finished in the sense that matters to
#: a schedule - it has stopped - but the two are kept apart everywhere a
#: completion figure is counted, because cancelled work was not delivered.
CLOSED_STATES = frozenset({"DONE", "DROPPED"})


def derive_end_dates(session, *, project_id: str) -> int:
    """When each task actually finished, from the tracker's own changelog.

    Jira records a due date - when the work was promised - and nothing at all
    about when it landed, so a page could say a task was open and overdue but
    never that it finished, still less that it finished early. The changelog
    has the answer: the transition into a closed status is the moment work
    stopped, timestamped by the system that made the edit.

    **The last such transition wins, not the first.** A ticket closed,
    reopened and closed again finished on the second date; taking the
    earliest would report it complete while it was still being worked on.
    That is the opposite rule to `derive_start_dates`, and deliberately so -
    a beginning is the first time it happened, an ending is the last.

    A task still open, or closed before the changelog window this collection
    covers, keeps an empty `actual_end` rather than borrowing its due date.
    """
    ended: dict[str, datetime] = {}
    for change in session.scalars(
        select(StateChange).where(
            StateChange.entity_type == "task",
            StateChange.field == "status",
        )
    ).all():
        if (change.new_value or "") not in CLOSED_STATES or not change.occurred_at:
            continue
        seen = ended.get(change.entity_id)
        if seen is None or change.occurred_at > seen:
            ended[change.entity_id] = change.occurred_at

    count = 0
    for task in session.scalars(
        select(Task).where(Task.project_id == project_id)
    ).all():
        # Only rows the tracker agrees are closed. A stale transition on a
        # ticket somebody has since reopened must not stamp a finish date on
        # work that is live again.
        if task.actual_end or (task.status or "") not in CLOSED_STATES:
            continue
        if task.id in ended:
            task.actual_end = ended[task.id].date()
            count += 1
    return count


def derive_start_dates(session, *, project_id: str) -> int:
    """Give each task the date its work actually began, from the changelog.

    **Jira has no start date.** 189 of CoWorkLocal's 190 issues carry a due
    date and *none* carries a start, so every view that needs both ends of a
    bar - the workload window, the Gantt - had nothing to draw and said so.

    Sometimes the changelog knows. The first transition *into* a working
    status is the moment work began, timestamped by the tracker's own record
    of its own edit, which is why these state changes are `precision='exact'`
    rather than inferred from two snapshots.

    **Usually it does not, and that is the honest answer.** A board whose
    tickets go straight from `To Do` to `Release` never records a start, and
    reading the closing transition as one produces a start date equal to the
    finish date on every finished task. On CoWorkLocal that is 151 of 190.
    Two tickets here have a real in-progress transition; the rest get
    nothing, and the chart says so rather than drawing a bar.

    **This is an actual, not a plan.** A bar drawn from it runs from when
    work really started to when it was promised - which is a useful thing to
    see and *not* the plan-versus-actual comparison it resembles. Nothing
    here invents a planned start: a task whose history shows no such
    transition keeps an empty start rather than borrowing its due date.

    Only empty starts are filled, so a real planned start from another
    source is never overwritten by a derived one.
    """
    started: dict[str, datetime] = {}
    rows = session.scalars(
        select(StateChange).where(
            StateChange.entity_type == "task",
            StateChange.field == "status",
        )
    ).all()
    for change in rows:
        if (change.new_value or "") not in STARTED or not change.occurred_at:
            continue
        seen = started.get(change.entity_id)
        if seen is None or change.occurred_at < seen:
            started[change.entity_id] = change.occurred_at

    count = 0
    for task in session.scalars(
        select(Task).where(Task.project_id == project_id)
    ).all():
        if task.start_date or task.id not in started:
            continue
        task.start_date = started[task.id].date()
        count += 1
    return count


def convert_changelogs(session, *, connection_id: int, now: datetime,
                       project_key: str | None = None) -> int:
    """`_tool_jira_changelogs` -> `state_changes`, all of them exact.

    Both bounds are the transition's own timestamp. The CHECK constraint on
    `state_changes` enforces that an exact change is a point rather than an
    interval, so a bug here fails loudly at the database rather than quietly
    widening a claim.
    """
    count = 0
    rows = session.scalars(
        select(ToolJiraChangelog).where(
            ToolJiraChangelog.connection_id == connection_id
        )
    ).all()

    # A changelog row carries no project key of its own, so scoping goes
    # through the issues: a history belongs to this project when its issue
    # does. Anything else would attach a state change to a task that this
    # conversion never created.
    mine = (_issue_ids(session, connection_id=connection_id,
                       project_key=project_key) if project_key else None)
    keys = _keys_by_issue_id(session, connection_id=connection_id)

    for tool in rows:
        if mine is not None and str(tool.issue_id) not in mine:
            continue
        canonical = TRACKED_FIELDS.get(tool.field)
        if canonical is None:
            continue

        new_value = tool.to_value
        old_value = tool.from_value
        if canonical == "status":
            # Mapped when known, the tracker's own word when not. A state
            # change has no `original_status` to keep the vendor spelling
            # beside it, so folding an unrecognised status to OTHER here
            # would erase what the person actually chose.
            new_value = _mapped_or_raw(new_value)
            old_value = _mapped_or_raw(old_value)

        change = StateChange(
            id=domain_id(
                SOURCE, "StateChange", connection_id, tool.changelog_id, tool.field
            ),
            entity_type="task",
            entity_id=domain_id(
                SOURCE, "Task", connection_id,
                keys.get(str(tool.issue_id), tool.issue_id)),
            field=canonical,
            old_value=old_value,
            new_value=new_value,
            # An exact change is a point: both bounds are the same instant.
            occurred_at=tool.created_at_src,
            occurred_at_lower=tool.created_at_src,
            precision=PRECISION_EXACT,
            ingested_at=now,
            scan_id=None,
            # A changelog entry is the system's own record of its own edit; there
            # is no identity to resolve and nothing to be unsure about.
            identity_confidence="high",
            source_ref=f"jira:changelog:{tool.changelog_id}",
        )
        change.copy_origin_from(tool)
        session.merge(change)
        count += 1

    return count
