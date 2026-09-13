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
from app.ingest.programs import ensure_program
from app.models.domain import Milestone, Project, QaItem, Task
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
    # `Resolved` is one of the commonest states in a real Jira workflow and was
    # falling through to OTHER, which every downstream count reads as *open* -
    # so a finished task was reported overdue, in progress and stale at once.
    "resolved": "DONE",
    "fixed": "DONE",
    "delivered": "DONE",
    # The terminal state in the FPT Jira workflow this app reads: the work is
    # shipped. Spelled as an instruction rather than a state, which is why it
    # read as ambiguous and fell through to OTHER - and OTHER is counted as
    # *open* everywhere downstream, so 142 released items on one board were
    # reporting as unfinished work and suppressing every completion figure.
    "release it": "DONE",
    "released": "DONE",
    # Work that will not happen. Its own state rather than DONE, because it was
    # not delivered - counting it as complete would inflate a completion figure,
    # and counting it as open would report a cancelled task as late forever.
    "cancelled": "DROPPED",
    "canceled": "DROPPED",
    "won't do": "DROPPED",
    "wont do": "DROPPED",
    "will not do": "DROPPED",
    "rejected": "DROPPED",
    "duplicate": "DROPPED",
    "abandoned": "DROPPED",
    "obsolete": "DROPPED",
    # Common board columns that are unambiguously one of the three live states.
    # Anything genuinely ambiguous is still left as OTHER rather than guessed:
    # a wrong mapping is worse than an honest unknown.
    "backlog": "TODO",
    "new": "TODO",
    "in review": "IN_PROGRESS",
    "review": "IN_PROGRESS",
    "in testing": "IN_PROGRESS",
    "testing": "IN_PROGRESS",
    "impeded": "BLOCKED",
    "waiting": "BLOCKED",
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

    **The program is resolved, never invented.** This function used to build
    `domain_id(SOURCE, "Program", connection_id, "DEFAULT")`, which put the
    collector's name inside the program's identity - so the Jira convertor,
    doing the same thing with its own `SOURCE`, created a second program row for
    the same program, and HRMS's two source projects hung off different ones.
    `app.scope` owns program membership for exactly the reason it owns project
    pairing, and `ensure_program` is the shared path both convertors take.
    """
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
        # Namespaced by project, like `Milestone` above. Every Excel project
        # shares one `connection_id`, and a row key is only unique *within its
        # own sheet* - so without the project two workbooks that both number
        # their tasks `1, 2, 3` (or `WBS-101`, which is not an unusual thing
        # for two teams to pick) collide into one row, and the second import
        # silently takes the first project's tasks. `gen_portfolio_data.py`
        # prefixes its ids per project to dodge exactly this, which a document
        # somebody uploads cannot be asked to do.
        id=domain_id(SOURCE, "Task", connection_id, project_id, tool.row_key),
        project_id=project_id,
        milestone_id=(milestones or {}).get(name),
        title=payload.get("title"),
        # The issue body, verbatim and untouched. Nothing derives anything from
        # it - see `Task.description` - so there is no normalizing to do and
        # any would only put distance between what a reader checks and what the
        # source said.
        description=payload.get("description"),
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
        source_updated_at=_to_date(payload.get("source_updated_at")),
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
        # Per project, for the reason `_convert_task` spells out.
        id=domain_id(SOURCE, "QaItem", connection_id, project_id, tool.row_key),
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
