"""What has been imported, what it feeds, and what reads it.

Three questions that had no single answer before this module.

**What is here.** `app/api/main.py`'s `/api/program` listed watched sheets, but
decided whether each one was present with `Path.exists()` against
`settings.data_root`. Uploads are not files: they are rows in `uploaded_sheets`
with the workbook in a column, precisely so a deployed host with no synced
folder can take data at all. So on the one deployment that matters, every
source reported itself missing while the data was plainly there and serving
pages. `availability` below asks the question the storage actually answers.

**What it feeds.** An import belongs to a delivery project, and a project
belongs to a program. Both were knowable and neither was shown beside the
import.

**What reads it.** A schedule sheet and a worklog are not interchangeable -
they fill different pages, and a Jira export cannot produce recorded slip until
a second one arrives for the differ to compare against. `CONSUMERS` states that
mapping once, rather than leaving it to be rediscovered from the code each time
somebody wonders why a page is empty.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Consumer:
    """One thing downstream that reads a kind of sheet."""

    #: What a person would click.
    page: str
    #: What it does with it, in one line.
    uses: str


#: Which pages and features read each kind of import.
#:
#: Written down rather than derived: the dependency is real but it runs through
#: the convertor, the domain tables and the rule engine, so no import graph
#: would recover it. Keep it honest by hand - a wrong entry here sends someone
#: to the wrong page when something is empty.
CONSUMERS: dict[str, tuple[Consumer, ...]] = {
    "schedule": (
        Consumer("Schedule", "the Gantt, the dependency graph and the critical path"),
        Consumer("Insight", "slip, drift and the findings the rules fire on"),
        Consumer("Portfolio", "the project's health tile and its rollup into a program"),
        Consumer("Reports", "the .docx status report"),
        Consumer("Risk", "what the risk drafter reads when proposing risks"),
    ),
    "worklog": (
        Consumer("Insight", "effort spent against planned, and the capacity findings"),
        Consumer("Team", "who is loaded, and by how much"),
        Consumer("Reports", "the effort section of the .docx report"),
    ),
    "jira_export": (
        Consumer("Schedule", "converted into a schedule sheet, so the Gantt fills"),
        Consumer("Insight", "slip - but only once a *second* export lands to diff against"),
    ),
    "jira_replay": (
        Consumer("Insight", "the event history behind an observation"),
        Consumer("Trace", "the evidence chain from a finding back to an issue"),
    ),
}

#: A kind nobody has mapped still lists something, rather than an empty cell
#: that reads as "nothing uses this".
_UNKNOWN_CONSUMER = (Consumer("—", "not mapped; see app/imports.py"),)


@dataclass
class ImportRow:
    """One imported thing, everything known about it in one place."""

    file_name: str
    #: "upload" when somebody sent it through the browser, "folder" when it is
    #: read from the synced directory, "demo" for the seeded timeline.
    origin: str
    kind: str
    sheet_name: str
    project_id: str
    project_name: str = ""
    program_id: str | None = None
    program_name: str = ""
    #: What the person called the file. `file_name` is our identifier and would
    #: mean nothing to them.
    original_filename: str = ""
    last_scan: datetime | None = None
    rows: int = 0
    size_bytes: int = 0
    #: Whether the bytes can actually be read right now - a stored blob, or a
    #: file present on disk. Not `Path.exists()`; see the module docstring.
    available: bool = False
    #: Why it is unavailable, when it is. Empty otherwise.
    problem: str = ""
    consumers: list[Consumer] = field(default_factory=list)
    #: Live counts for the project this feeds, so "imported" can be told apart
    #: from "actually reached the pages".
    task_count: int = 0
    risk_count: int = 0

    @property
    def ingested(self) -> bool:
        """Whether anything downstream exists for the project it feeds."""
        return self.task_count > 0


def _program_names() -> dict[str, str]:
    from app import scope

    return {p.program_id: p.name for p in scope.all_programs()}


def inventory(session) -> list[ImportRow]:
    """Every import, newest scan first.

    One pass over the watched list, the upload table and the scan log, joined
    to the project registry. Ordering puts what changed most recently at the
    top, because that is what somebody checking an upload came to see.
    """
    from sqlalchemy import func, select

    from app import scope
    from app.config import settings
    from app.ingest.sources.excel.source import all_watched
    from app.models.domain import Risk, Task
    from app.models.sync import SheetScan
    from app.models.uploads import UploadedSheet

    scans: dict[str, SheetScan] = {}
    for scan in session.scalars(select(SheetScan)).all():
        seen = scans.get(scan.scope)
        if seen is None or scan.scanned_at > seen.scanned_at:
            scans[scan.scope] = scan

    # The size, never the blob. This runs on a page load, and selecting the
    # entity would pull every workbook into memory to render a table that only
    # shows how big they are.
    uploads: dict[str, dict] = {}
    for row in session.execute(
        select(
            UploadedSheet.file_name,
            UploadedSheet.original_filename,
            UploadedSheet.kind,
            func.length(UploadedSheet.content),
        )
    ).all():
        uploads[row[0]] = {
            "original_filename": row[1] or "",
            # The kind as it was imported. `WatchedSheet` carries a contract
            # rather than a kind, so without this the only way back is matching
            # the tab name - which is right for our own workbooks and a guess
            # for a renamed tab.
            "kind": row[2] or "",
            "size": int(row[3] or 0),
        }

    projects = {p.canonical_id: p for p in scope.all_projects()}
    programs = _program_names()

    task_counts = dict(
        session.execute(select(Task.project_id, func.count(Task.id)).group_by(Task.project_id)).all()
    )
    risk_counts = dict(
        session.execute(
            select(Risk.project_id, func.count(Risk.id)).group_by(Risk.project_id)
        ).all()
    )

    out: list[ImportRow] = []
    for watched in all_watched():
        scope_key = f"{watched.file_name}#{watched.sheet_name}"
        scan = scans.get(scope_key)
        upload = uploads.get(watched.file_name)

        # `upload_` is the prefix `register_watched` gives a browser import;
        # the rest is either the demo seed or a genuinely watched folder file.
        if upload is not None:
            origin = "upload"
        elif watched.file_name.startswith("upload_"):
            # Registered as an upload but the blob is gone - worth showing as
            # its own state rather than silently as a folder file that happens
            # to be missing.
            origin = "upload"
        else:
            origin = "demo" if _is_seed(watched.file_name) else "folder"

        path = Path(settings.data_root) / watched.file_name
        on_disk = path.exists()
        available = bool(upload) or on_disk
        problem = ""
        if not available:
            problem = (
                "registered as an upload, but its stored workbook is gone"
                if origin == "upload"
                else f"no file at {path}"
            )

        kind = (upload or {}).get("kind") or _kind_of(watched)
        resolved = scope.resolve(watched.project_id)
        canonical = resolved.canonical_id if resolved else watched.project_id
        project = projects.get(canonical)

        out.append(
            ImportRow(
                file_name=watched.file_name,
                origin=origin,
                kind=kind,
                sheet_name=watched.sheet_name,
                project_id=canonical,
                project_name=project.name if project else "",
                program_id=project.program_id if project else None,
                program_name=programs.get(project.program_id or "", "") if project else "",
                original_filename=(upload or {}).get("original_filename", ""),
                last_scan=scan.scanned_at if scan else None,
                rows=scan.row_count if scan else 0,
                size_bytes=(upload or {}).get("size", 0)
                or (path.stat().st_size if on_disk else 0),
                available=available,
                problem=problem,
                consumers=list(CONSUMERS.get(kind, _UNKNOWN_CONSUMER)),
                task_count=int(task_counts.get(canonical, 0) or 0),
                risk_count=int(risk_counts.get(canonical, 0) or 0),
            )
        )

    out.sort(key=lambda r: (r.last_scan is None, r.last_scan or datetime.min), reverse=True)
    return out


def _kind_of(watched) -> str:
    """`schedule` or `worklog`, from the sheet the contract names.

    The kind is not stored on `WatchedSheet` - it carries the contract itself -
    so it is recovered from `SHEET_KINDS`, which is the one table that maps
    the two together.
    """
    from app.ingest.sources.excel.source import SHEET_KINDS

    for kind, (sheet_name, _contract) in SHEET_KINDS.items():
        if watched.sheet_name == sheet_name:
            return kind
    return "schedule"


def _is_seed(file_name: str) -> bool:
    from app.ingest.sources.excel.source import WATCHED

    return any(w.file_name == file_name for w in WATCHED)


def orphan_projects(session) -> list[dict]:
    """Registered projects with no import and nothing ingested.

    The registry accumulates: a project is created the moment somebody starts
    an upload, and a failed or abandoned one leaves the row behind. These are
    the rows safe to remove, and saying so is the whole point - deleting a
    project that *does* hold data is a different decision.
    """
    from sqlalchemy import func, select

    from app import scope
    from app.ingest.sources.excel.source import all_watched
    from app.models.domain import Task

    fed = set()
    for watched in all_watched():
        resolved = scope.resolve(watched.project_id)
        fed.add(resolved.canonical_id if resolved else watched.project_id)

    counts = dict(
        session.execute(select(Task.project_id, func.count(Task.id)).group_by(Task.project_id)).all()
    )

    out = []
    for project in scope.all_projects():
        if project.canonical_id in fed:
            continue
        if int(counts.get(project.canonical_id, 0) or 0):
            continue
        out.append(
            {
                "canonical_id": project.canonical_id,
                "name": project.name,
                "program_id": project.program_id,
                # Stated rather than implied, because the button beside it
                # deletes something somebody once created on purpose.
                "reason": "no import feeds it and nothing has been ingested for it",
            }
        )
    return out
