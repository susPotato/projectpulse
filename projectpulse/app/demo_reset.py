"""Resetting the app between demos: Jira data out, old trace runs hidden.

Two operations behind Settings › Demo reset, each with a preview so the page
can say what a button will do before anybody presses it.

**Jira data is found by id, not by project.** Every row the Jira convertor
writes carries a project id of the form `jira:Project:<connection>:<KEY>`
(`ingest/sources/jira/convertor.py`), so "everything Jira produced" is every
domain row under that prefix, plus the raw/tool staging tables and the sync
bookkeeping that would otherwise make the next collect an incremental one.
Built-in seed projects and Excel-sourced data are never touched, which is why
this is not `app.projects.remove` in a loop: that refuses seed projects and
works on whole projects, where a seed project may also carry Jira rows.

Connections are separate and optional. Clearing the data but keeping the link
is the usual demo loop - collect live, clear, collect again - and a saved
connection is the part that takes typing.

**Trace runs are archived, not deleted.** `tracelink_view.available_runs`
lists every directory under the runs root that holds a `tickets.json`, so
moving them into `_archived/<stamp>/` hides them from the page completely -
and `restore_traces` puts them back. A run's verdict cache lives inside its
directory, and deleting it would re-bill every ticket the next time that
project is traced.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, select

JIRA_PREFIX = "jira:"
ARCHIVE_DIR = "_archived"


# --------------------------------------------------------------------------
# Jira
# --------------------------------------------------------------------------


def _domain_models():
    from app.models.domain import (
        Dependency,
        Milestone,
        QaItem,
        Resource,
        Risk,
        Task,
    )
    from app.models.sync import RawReject

    return (Dependency, Task, QaItem, Milestone, Resource, Risk, RawReject)


def _jira_ids(session) -> list[str]:
    """Every project id the Jira convertor wrote, wherever a row still uses it."""
    from app.models.domain import Project

    ids = set(session.scalars(
        select(Project.id).where(Project.id.like(JIRA_PREFIX + "%"))).all())
    for model in _domain_models():
        ids.update(session.scalars(
            select(model.project_id).distinct()
            .where(model.project_id.like(JIRA_PREFIX + "%"))).all())
    return sorted(i for i in ids if i)


def _staging():
    from app.models.raw import RawJiraChangelogs, RawJiraIssues
    from app.models.tool import ToolJiraChangelog, ToolJiraIssue

    return {
        "raw_jira_issues": RawJiraIssues,
        "raw_jira_changelogs": RawJiraChangelogs,
        "tool_jira_issues": ToolJiraIssue,
        "tool_jira_changelogs": ToolJiraChangelog,
    }


def _sync_filters():
    from app.models.sync import SyncRun, SyncState

    return (
        (SyncState, SyncState.task.like("jira%")),
        (SyncRun, SyncRun.source.like("jira%")),
    )


def jira_preview(session) -> dict:
    """What `clear_jira` would delete, per table. Zeros included on purpose."""
    from app.models.dashboard import Dashboard
    from app.models.domain import Project, QaItem, StateChange, Task
    from app.models.jira import JiraConnection

    ids = _jira_ids(session)

    def count(model, where=None) -> int:
        query = select(func.count()).select_from(model)
        if where is not None:
            query = query.where(where)
        return session.scalar(query) or 0

    counts: dict[str, int] = {}
    counts["projects"] = count(Project, Project.id.in_(ids)) if ids else 0
    for model in _domain_models():
        counts[model.__tablename__] = count(model, model.project_id.in_(ids)) if ids else 0
    if ids:
        entity_ids = select(Task.id).where(Task.project_id.in_(ids)).union(
            select(QaItem.id).where(QaItem.project_id.in_(ids)))
        counts["state_changes"] = count(StateChange, StateChange.entity_id.in_(entity_ids))
        counts["dashboards"] = count(Dashboard, (Dashboard.scope_type == "project")
                                     & Dashboard.scope_id.in_(ids))
    else:
        counts["state_changes"] = counts["dashboards"] = 0
    for name, model in _staging().items():
        counts[name] = count(model)
    for model, where in _sync_filters():
        counts[model.__tablename__] = count(model, where)

    connections = session.scalars(select(JiraConnection)).all()
    return {
        "project_ids": ids,
        "counts": counts,
        "connections": [
            {"id": c.id, "project_key": c.project_key, "site": c.site,
             "project_id": c.project_id}
            for c in connections
        ],
    }


def clear_jira(session, *, forget_connections: bool = False) -> dict:
    """Delete everything Jira produced. Returns the preview it carried out."""
    from app.models.dashboard import Dashboard, DashboardTile
    from app.models.domain import Project, QaItem, StateChange, Task
    from app.models.jira import JiraConnection

    outcome = jira_preview(session)
    ids = outcome["project_ids"]

    if ids:
        # Children before parents, or the foreign keys refuse - the order
        # `app.projects.remove` already established.
        entity_ids = session.scalars(
            select(Task.id).where(Task.project_id.in_(ids)).union(
                select(QaItem.id).where(QaItem.project_id.in_(ids)))).all()
        if entity_ids:
            session.execute(delete(StateChange).where(StateChange.entity_id.in_(entity_ids)))
        boards = session.scalars(select(Dashboard.id).where(
            Dashboard.scope_type == "project", Dashboard.scope_id.in_(ids))).all()
        if boards:
            session.execute(delete(DashboardTile).where(DashboardTile.dashboard_id.in_(boards)))
            session.execute(delete(Dashboard).where(Dashboard.id.in_(boards)))
        for model in _domain_models():
            session.execute(delete(model).where(model.project_id.in_(ids)))
        session.execute(delete(Project).where(Project.id.in_(ids)))

    # Tool rows are derived from raw rows, so they go first.
    staging = _staging()
    for name in ("tool_jira_changelogs", "tool_jira_issues",
                 "raw_jira_changelogs", "raw_jira_issues"):
        session.execute(delete(staging[name]))
    for model, where in _sync_filters():
        session.execute(delete(model).where(where))

    outcome["connections_removed"] = 0
    if forget_connections:
        outcome["connections_removed"] = len(outcome["connections"])
        session.execute(delete(JiraConnection))
    return outcome


# --------------------------------------------------------------------------
# Trace runs
# --------------------------------------------------------------------------


def _root() -> Path | None:
    from app.api.tracelink_view import runs_root

    return runs_root()


def _live_runs(root: Path) -> list[Path]:
    return [p for p in sorted(root.iterdir())
            if p.is_dir() and p.name != ARCHIVE_DIR and (p / "tickets.json").exists()]


def _archives(root: Path) -> list[Path]:
    base = root / ARCHIVE_DIR
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)


def traces_preview() -> dict:
    root = _root()
    if root is None:
        return {"root": None, "runs": [], "archives": []}
    return {
        "root": str(root),
        "runs": [p.name for p in _live_runs(root)],
        "archives": [
            {"name": a.name, "runs": [p.name for p in sorted(a.iterdir()) if p.is_dir()]}
            for a in _archives(root)
        ],
    }


def archive_traces() -> dict:
    """Move every visible run into `_archived/<stamp>/`. Refused mid-run."""
    from app import traceability_run

    if traceability_run.state()["running"]:
        raise RuntimeError("a traceability run is in progress; wait for it to finish")
    root = _root()
    if root is None:
        return {"archived": [], "archive": None}
    runs = _live_runs(root)
    if not runs:
        return {"archived": [], "archive": None}
    dest = root / ARCHIVE_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for run in runs:
        shutil.move(str(run), str(dest / run.name))
    return {"archived": [r.name for r in runs], "archive": dest.name}


def restore_traces() -> dict:
    """Bring back the newest archive. A name already in use is left archived."""
    from app import traceability_run

    if traceability_run.state()["running"]:
        raise RuntimeError("a traceability run is in progress; wait for it to finish")
    root = _root()
    archives = _archives(root) if root else []
    if not archives:
        return {"restored": [], "kept_archived": [], "archive": None}
    newest = archives[0]
    restored, kept = [], []
    for run in sorted(p for p in newest.iterdir() if p.is_dir()):
        target = root / run.name
        if target.exists():
            # A newer run of the same project was made since; it wins.
            kept.append(run.name)
            continue
        shutil.move(str(run), str(target))
        restored.append(run.name)
    if not any(newest.iterdir()):
        newest.rmdir()
    return {"restored": restored, "kept_archived": kept, "archive": newest.name}


__all__ = [
    "archive_traces", "clear_jira", "jira_preview", "restore_traces", "traces_preview",
]
