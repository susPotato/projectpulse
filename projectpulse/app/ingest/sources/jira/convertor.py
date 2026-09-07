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
from app.models.domain import (
    PRECISION_EXACT,
    Program,
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

#: Jira status names -> the normalized vocabulary the rules read. The vendor's own
#: value is kept alongside in `original_status`, because that is what the PM typed
#: and what the evidence panel has to show back to them.
STATUS_MAP = {
    "To Do": "TODO",
    "Open": "TODO",
    "In Progress": "IN_PROGRESS",
    "Blocked": "BLOCKED",
    "Done": "DONE",
    "Closed": "DONE",
}


def _to_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def ensure_project(session, *, connection_id: int, project_key: str, name: str) -> str:
    """Create the program/project rows the tasks hang off, if absent."""
    program_id = domain_id(SOURCE, "Program", connection_id, "DEFAULT")
    if session.get(Program, program_id) is None:
        session.add(
            Program(id=program_id, name="Digital Transformation 2026", status="Active")
        )

    project_id = domain_id(SOURCE, "Project", connection_id, project_key)
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


def convert_issues(session, *, connection_id: int, project_id: str) -> int:
    """`_tool_jira_issues` -> `tasks`."""
    count = 0
    rows = session.scalars(
        select(ToolJiraIssue).where(ToolJiraIssue.connection_id == connection_id)
    ).all()

    for tool in rows:
        task = Task(
            id=domain_id(SOURCE, "Task", connection_id, tool.issue_id),
            project_id=project_id,
            title=tool.summary,
            status=STATUS_MAP.get(tool.status or "", "OTHER"),
            original_status=tool.status,
            assignee=tool.assignee,
            due_date=_to_date(tool.due_date),
        )
        task.copy_origin_from(tool)
        session.merge(task)
        count += 1

    return count


def convert_changelogs(session, *, connection_id: int, now: datetime) -> int:
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

    for tool in rows:
        canonical = TRACKED_FIELDS.get(tool.field)
        if canonical is None:
            continue

        new_value = tool.to_value
        old_value = tool.from_value
        if canonical == "status":
            new_value = STATUS_MAP.get(new_value or "", new_value)
            old_value = STATUS_MAP.get(old_value or "", old_value)

        change = StateChange(
            id=domain_id(
                SOURCE, "StateChange", connection_id, tool.changelog_id, tool.field
            ),
            entity_type="task",
            entity_id=domain_id(SOURCE, "Task", connection_id, tool.issue_id),
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
