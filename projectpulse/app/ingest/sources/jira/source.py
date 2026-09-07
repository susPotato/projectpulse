"""The Jira source: collect -> extract -> convert, wired into the runner.

Registered as `jira_replay` rather than `jira` so the name states plainly that
the transport is captured payloads. Swapping in a live client means adding a
`jira` source whose collector makes HTTP calls; extract and convert stay as they
are, because they only ever see the raw table.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.ingest.runner import register_source
from app.ingest.sources.jira import convertor, extractor, replay

log = logging.getLogger(__name__)

SOURCE = "jira_replay"
BOARD_KEY = "HRMS"


@register_source(SOURCE)
def run_jira_sync(
    session,
    *,
    connection_id: int,
    now: datetime,
    sync_run_id: int | None = None,
    data_root: Path | None = None,
) -> dict:
    data_dir = Path(data_root or settings.data_root) / "jira"
    notes: list[str] = []

    if not data_dir.exists():
        return {
            "rows_ok": 0,
            "rows_rejected": 0,
            "changes_emitted": 0,
            "notes": [
                f"{data_dir} not found - run `python -m scripts.gen_jira_data` first"
            ],
        }

    collected = replay.collect(
        session,
        connection_id=connection_id,
        board_key=BOARD_KEY,
        data_dir=data_dir,
        now=now,
    )
    session.flush()
    notes.append(
        f"collected {collected.issues} issues and {collected.changelogs} "
        f"changelog entries from {collected.source_files} payload(s)"
    )

    issues = extractor.extract_issues(session, connection_id=connection_id)
    changelogs = extractor.extract_changelogs(session, connection_id=connection_id)
    session.flush()
    notes.append(f"extracted {issues} issues, {changelogs} field changes")

    project_id = convertor.ensure_project(
        session,
        connection_id=connection_id,
        project_key=BOARD_KEY,
        name="HRMS Portal V2",
    )
    session.flush()

    tasks = convertor.convert_issues(
        session, connection_id=connection_id, project_id=project_id
    )
    changes = convertor.convert_changelogs(
        session, connection_id=connection_id, now=now
    )
    notes.append(f"converted {tasks} tasks, {changes} exact state changes")

    return {
        "rows_ok": tasks,
        "rows_rejected": 0,
        "changes_emitted": changes,
        "notes": notes,
    }
