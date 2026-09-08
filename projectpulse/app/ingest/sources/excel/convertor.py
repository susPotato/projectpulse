"""Convertor: Excel tool rows -> the vendor-neutral domain model.

The counterpart of `jira/convertor.py`, and the piece that was missing: the Excel
path produced `state_changes` and `dependencies` but never the `tasks` those edges
join, so the schedule engine had a graph of ids and no dates to put on them.

Two rules carried over from the Jira convertor, for the same reasons:

**Provenance is copied, never re-derived.** `copy_origin_from` moves the same
`_raw_data_id` from the tool row onto the domain row, so a task on the insight
screen still resolves to the spreadsheet cell it came from. Re-deriving it here
would produce an id that looks right and points somewhere else.

**The vendor's own value is kept.** `status` holds the normalized vocabulary the
rules read; `original_status` holds what the PM actually typed, because that is
what the evidence panel has to show back to them.

Conversion is a full upsert over the tool layer rather than an incremental pass.
The tool layer is small (one row per spreadsheet row) and `didgen` ids make every
write idempotent, so re-running is a no-op rather than a duplicate.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.ids import domain_id
from app.models.domain import Milestone, Program, Project, QaItem, Task
from app.models.tool import ToolExcelRow

SOURCE = "excel"

#: Spreadsheet status text -> the normalized vocabulary the rules compare against.
#: Anything unmapped becomes OTHER rather than being guessed at.
STATUS_MAP = {
    "not started": "TODO",
    "todo": "TODO",
    "to do": "TODO",
    "open": "TODO",
    "in progress": "IN_PROGRESS",
    "wip": "IN_PROGRESS",
    "blocked": "BLOCKED",
    "on hold": "BLOCKED",
    "done": "DONE",
    "complete": "DONE",
    "completed": "DONE",
    "closed": "DONE",
}

#: A worklog row is blocked if either column says so. The sheets in the wild use
#: one or the other, and sometimes both.
BLOCKED_TRUTHY = {"yes", "y", "true", "blocked"}


def _to_date(value) -> date | None:
    """Values in the tool layer are already normalized to ISO by the differ."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_status(raw: str | None) -> str:
    return STATUS_MAP.get((raw or "").strip().casefold(), "OTHER")


def ensure_project(
    session, *, connection_id: int, project_id: str, name: str
) -> str:
    """Create the program/project rows the tasks hang off, if absent.

    `project_id` is passed in rather than derived because the watched-sheet config
    already declares which project a sheet belongs to, and inventing a second
    identity for it here would split one project into two.
    """
    program_id = domain_id(SOURCE, "Program", connection_id, "DEFAULT")
    if session.get(Program, program_id) is None:
        session.add(
            Program(id=program_id, name="Digital Transformation 2026", status="Active")
        )

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


def convert_scope(
    session, *, connection_id: int, scope: str, project_id: str, entity_type: str
) -> int:
    """`_tool_excel_rows` for one sheet -> `tasks` or `qa_items`.

    Returns how many domain rows were written.
    """
    rows = session.scalars(
        select(ToolExcelRow).where(ToolExcelRow.scope == scope)
    ).all()

    if entity_type == "qa_item":
        return sum(
            _convert_qa_item(
                session, connection_id, project_id, tool, dict(tool.payload or {})
            )
            for tool in rows
        )

    # Milestones first, so tasks can carry a real foreign key rather than a label.
    milestones = _convert_milestones(session, connection_id, project_id, rows)

    return sum(
        _convert_task(
            session,
            connection_id,
            project_id,
            tool,
            dict(tool.payload or {}),
            milestones,
        )
        for tool in rows
    )


def _convert_milestones(session, connection_id: int, project_id: str, rows) -> dict:
    """Derive one Milestone per distinct name in the sheet's Milestone column.

    A milestone's date is **the latest of its tasks' dates**, because a milestone
    is reached when the last thing under it is done. Derived rather than read: the
    template has no separate milestone sheet, and inventing one would be more
    work for the PM than reading the column they already fill in.

    Returns ``{milestone name: domain id}`` so the caller can link its tasks.
    """
    latest: dict[str, tuple[date | None, date | None]] = {}

    for tool in rows:
        payload = dict(tool.payload or {})
        name = (payload.get("milestone") or "").strip()
        if not name:
            continue
        planned = _to_date(payload.get("planned_end"))
        baseline = _to_date(payload.get("baseline_end"))
        held_planned, held_baseline = latest.get(name, (None, None))
        latest[name] = (
            max(filter(None, (planned, held_planned)), default=None),
            max(filter(None, (baseline, held_baseline)), default=None),
        )

    ids: dict[str, str] = {}
    for name, (planned, baseline) in latest.items():
        milestone_id = domain_id(SOURCE, "Milestone", connection_id, project_id, name)
        session.merge(
            Milestone(
                id=milestone_id,
                project_id=project_id,
                name=name,
                planned_date=planned,
                baseline_date=baseline,
            )
        )
        ids[name] = milestone_id
    return ids


def _convert_task(
    session, connection_id: int, project_id: str, tool, payload, milestones=None
) -> int:
    name = (payload.get("milestone") or "").strip()
    task = Task(
        id=domain_id(SOURCE, "Task", connection_id, tool.row_key),
        project_id=project_id,
        milestone_id=(milestones or {}).get(name),
        title=payload.get("title"),
        # The sheet's own Phase column. Parsed by the reader and dropped here
        # until now, which left `Task.phase` permanently null. It is what a
        # constraint like "a Testing task needs an Environment predecessor"
        # keys on, so a rule of that kind was not expressible at all.
        phase=payload.get("phase"),
        status=_normalize_status(payload.get("status")),
        # What the PM typed, kept verbatim.
        original_status=payload.get("status"),
        assignee=payload.get("assignee"),
        start_date=_to_date(payload.get("start_date")),
        due_date=_to_date(payload.get("planned_end")),
        baseline_end=_to_date(payload.get("baseline_end")),
        progress=_to_float(payload.get("progress")),
    )
    task.copy_origin_from(tool)
    task.raw_data_remark = tool.raw_data_remark
    session.merge(task)
    return 1


def _convert_qa_item(session, connection_id: int, project_id: str, tool, payload) -> int:
    blocked_flag = (payload.get("blocked") or "").strip().casefold()
    status = _normalize_status(payload.get("status"))
    if blocked_flag in BLOCKED_TRUTHY:
        status = "BLOCKED"

    item = QaItem(
        id=domain_id(SOURCE, "QaItem", connection_id, tool.row_key),
        project_id=project_id,
        test_case=payload.get("title"),
        status=status,
        blocked_by=payload.get("blocked_by"),
        # Parsed by the worklog contract and dropped here until now, which left
        # the system with no effort data and no QA ownership at all.
        assignee=payload.get("assignee"),
        hours_spent=_to_float(payload.get("hours_spent")),
        estimate_hours=_to_float(payload.get("estimate_hours")),
        log_date=_to_date(payload.get("log_date")),
    )
    item.copy_origin_from(tool)
    item.raw_data_remark = tool.raw_data_remark
    session.merge(item)
    return 1
