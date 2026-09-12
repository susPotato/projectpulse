"""Removing a delivery project, and everything that was only ever about it.

The counterpart of `POST /api/projects` and `POST /api/sources/upload`, and the
one operation in this app with no source system behind it to rebuild from: a
sync re-creates tasks, but nothing re-creates a risk somebody typed or a
dashboard somebody arranged. So this is written to be *legible before it runs* -
`plan()` says exactly what would go, and the route serves that as its own step -
rather than to be quick.

**A built-in seed project cannot be removed, and saying so is the honest
answer.** `app/scope.py` declares HRMS, SAIN and Example Project in code; they
are not registry rows. Deleting their ingested data would empty them without
removing them, so they would sit on the portfolio as `no_data` - which looks
exactly like a project awaiting its first sync, and is a worse state than the
one somebody was trying to leave. A seed is removed by editing `_SEED`, which is
a code change and should be.

**What counts as "about this project" is narrower than it looks.** Three things
are deliberately *not* deleted:

``custom_tiles``
    A custom tile is not project-scoped - `CustomTileOut` has no `project_id`,
    and the same tile can sit on several boards. Deleting one because a project
    that referenced it went away would take it off the others.

``programs``
    A program outliving its last project is a real state - it is what a new
    program looks like - and removing one as a side effect of removing a project
    is a decision the person did not ask for.

``narration_cache``
    Keyed by a hash of the project id plus the template narrative, so it is
    self-invalidating and costs nothing to leave. Deleting it is not wrong,
    merely pointless work inside a destructive operation, and destructive
    operations should do the minimum that is asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, func, select


@dataclass(frozen=True)
class RemovalPlan:
    """What removing this project would destroy.

    `counts` is per table and deliberately includes zeros: "0 risks" is
    information - it tells a person there is nothing here they typed by hand -
    and omitting it would leave them guessing whether the row was checked.
    """

    project_id: str
    name: str
    removable: bool
    reason: str = ""
    source_ids: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def irreplaceable(self) -> dict[str, int]:
        """The rows no sync can rebuild. What a confirmation should lead with."""
        return {k: v for k, v in self.counts.items() if k in _IRREPLACEABLE and v}


#: Tables whose contents came from a person rather than from a source system.
#: Re-running a sync brings tasks back; it does not bring these back.
_IRREPLACEABLE = {"risks", "dashboards"}


def plan(session, project_id: str) -> RemovalPlan:
    """What `remove` would do, without doing any of it."""
    from app.models.dashboard import Dashboard
    from app.models.domain import (
        Dependency,
        Milestone,
        Project,
        QaItem,
        Resource,
        Risk,
        StateChange,
        Task,
    )
    from app.models.sync import RawReject
    from app.models.uploads import RegisteredProject, UploadedSheet
    from app.scope import find, resolve

    entry = resolve(project_id)
    if entry is None:
        return RemovalPlan(
            project_id=project_id,
            name=project_id,
            removable=False,
            reason="no project with that id",
        )

    canonical = entry.canonical_id
    ids = list(entry.source_ids)

    registered = session.get(RegisteredProject, canonical) is not None
    if not registered and find(canonical) is not None:
        return RemovalPlan(
            project_id=canonical,
            name=entry.name,
            removable=False,
            reason=(
                "this is a built-in demo project, declared in app/scope.py rather "
                "than registered. Deleting its data would empty it without removing "
                "it, leaving a project on the portfolio with nothing in it - so it "
                "is removed by editing the seed, which is a code change."
            ),
            source_ids=tuple(ids),
        )

    def count(model, column=None) -> int:
        column = column if column is not None else model.project_id
        return session.scalar(select(func.count()).select_from(model).where(column.in_(ids))) or 0

    #: `state_changes` is keyed by the entity it describes, not by a project, so
    #: it is reached through the ids of the rows about to go.
    entity_ids = select(Task.id).where(Task.project_id.in_(ids)).union(
        select(QaItem.id).where(QaItem.project_id.in_(ids))
    )
    changes = (
        session.scalar(
            select(func.count())
            .select_from(StateChange)
            .where(StateChange.entity_id.in_(entity_ids))
        )
        or 0
    )

    return RemovalPlan(
        project_id=canonical,
        name=entry.name,
        removable=True,
        source_ids=tuple(ids),
        counts={
            "tasks": count(Task),
            "qa_items": count(QaItem),
            "milestones": count(Milestone),
            "dependencies": count(Dependency),
            "resources": count(Resource),
            "state_changes": changes,
            "risks": count(Risk),
            "rejected_rows": count(RawReject),
            "uploaded_sheets": count(UploadedSheet),
            "dashboards": session.scalar(
                select(func.count())
                .select_from(Dashboard)
                .where(Dashboard.scope_type == "project", Dashboard.scope_id.in_(ids))
            )
            or 0,
            "projects": count(Project, Project.id),
        },
    )


def remove(session, project_id: str) -> RemovalPlan:
    """Delete the project and everything that was only ever about it.

    Returns the plan it carried out, so a caller can report what went rather
    than "ok". Raises `ValueError` for anything `plan` refuses - the route turns
    that into a 400 with the reason, which is a sentence a person can act on.

    Order matters: children before the `projects` row they point at, or the
    foreign keys refuse. `state_changes` goes first of all, because finding it
    needs the task and QA rows that are about to be deleted.
    """
    from app.models.dashboard import Dashboard, DashboardTile
    from app.models.domain import (
        Dependency,
        Milestone,
        Project,
        QaItem,
        Resource,
        Risk,
        StateChange,
        Task,
    )
    from app.models.sync import RawReject
    from app.models.uploads import RegisteredProject, UploadedSheet

    outcome = plan(session, project_id)
    if not outcome.removable:
        raise ValueError(outcome.reason)

    ids = list(outcome.source_ids)

    entity_ids = [
        row
        for row in session.scalars(
            select(Task.id).where(Task.project_id.in_(ids)).union(
                select(QaItem.id).where(QaItem.project_id.in_(ids))
            )
        ).all()
    ]
    if entity_ids:
        session.execute(delete(StateChange).where(StateChange.entity_id.in_(entity_ids)))

    #: Tiles before boards, for the same foreign-key reason.
    boards = session.scalars(
        select(Dashboard.id).where(
            Dashboard.scope_type == "project", Dashboard.scope_id.in_(ids)
        )
    ).all()
    if boards:
        session.execute(delete(DashboardTile).where(DashboardTile.dashboard_id.in_(boards)))
        session.execute(delete(Dashboard).where(Dashboard.id.in_(boards)))

    for model in (Dependency, Task, QaItem, Milestone, Resource, Risk, RawReject, UploadedSheet):
        session.execute(delete(model).where(model.project_id.in_(ids)))

    session.execute(delete(Project).where(Project.id.in_(ids)))
    session.execute(delete(RegisteredProject).where(RegisteredProject.canonical_id.in_(ids)))

    return outcome
