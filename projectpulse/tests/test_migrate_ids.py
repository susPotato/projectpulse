"""Guards for `scripts.migrate_ids`.

The migration exists because Excel domain ids gained a project component, and
a database written before that keeps working until its next sync - at which
point the convertor writes new-style rows beside the old ones and every task
appears twice. `scripts.replay` is the other way out and the wrong one for a
deployed database: the risk register, the arranged dashboards and the custom
tiles have no source system behind them and a rebuild destroys them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.domain import Dependency, Project, QaItem, StateChange, Task
from scripts.migrate_ids import apply, plan

PROJECT = "excel:Project:1:HRMS"
OTHER = "excel:Project:1:SAIN"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        for project_id in (PROJECT, OTHER):
            handle.add(Project(id=project_id, name=project_id.split(":")[-1]))
        handle.flush()
        yield handle


def _old_task(session, project_id: str, key: str) -> Task:
    """A task keyed the way the pre-change convertor keyed one."""
    task = Task(id=f"excel:Task:1:{key}", project_id=project_id, title=key)
    session.add(task)
    return task


def _migrate(session):
    work = plan(session)
    counts = apply(session, work)
    session.flush()
    return counts


def test_an_old_task_is_re_keyed_onto_its_own_project(session):
    _old_task(session, PROJECT, "WBS-101")
    session.flush()

    _migrate(session)

    ids = list(session.scalars(select(Task.id)))
    assert ids == ["excel:Task:1:excel%3AProject%3A1%3AHRMS:WBS-101"]


def test_a_jira_task_is_left_alone(session):
    """Only the Excel path changed. A Jira issue id is unique across that
    vendor's projects, so those rows were never wrong and must not move."""
    session.add(Task(id="jira:Task:1:10108", project_id=PROJECT, title="from jira"))
    session.flush()

    _migrate(session)

    assert "jira:Task:1:10108" in set(session.scalars(select(Task.id)))


def test_running_it_twice_changes_nothing_the_second_time(session):
    _old_task(session, PROJECT, "WBS-101")
    session.flush()

    _migrate(session)
    before = sorted(session.scalars(select(Task.id)))

    assert plan(session) == {}
    assert sorted(session.scalars(select(Task.id))) == before


def test_a_state_change_follows_the_task_it_describes(session):
    """Re-keying a task and leaving its changes behind would empty the
    evidence panel, which is the one thing this product cannot lose."""
    from datetime import datetime

    _old_task(session, PROJECT, "WBS-108")
    session.add(
        StateChange(
            id="excel:StateChange:1:WBS-108:planned_end:1772000000",
            entity_type="task",
            entity_id="excel:Task:1:WBS-108",
            field="planned_end",
            old_value="2026-03-04",
            new_value="2026-03-16",
            occurred_at=datetime(2026, 3, 6, 9, 0),
            occurred_at_lower=datetime(2026, 3, 2, 9, 0),
            precision="bounded",
            ingested_at=datetime(2026, 3, 6, 9, 0),
        )
    )
    session.flush()

    _migrate(session)

    change = session.scalars(select(StateChange)).one()
    task_id = session.scalars(select(Task.id)).one()
    assert change.entity_id == task_id
    assert change.id.startswith("excel:StateChange:1:excel%3AProject%3A1%3AHRMS:")
    # The scan timestamp is still the last component - it is what keeps two
    # changes to one field on one row distinct.
    assert change.id.endswith(":planned_end:1772000000")


def test_a_dependency_re_points_at_both_of_its_ends(session):
    _old_task(session, PROJECT, "WBS-108")
    _old_task(session, PROJECT, "WBS-114")
    session.add(
        Dependency(
            id="excel:Dependency:1:WBS-108:WBS-114",
            project_id=PROJECT,
            predecessor_id="excel:Task:1:WBS-108",
            successor_id="excel:Task:1:WBS-114",
            dep_type="FS",
            lag_days=0,
            source="excel_predecessor",
        )
    )
    session.flush()

    _migrate(session)

    edge = session.scalars(select(Dependency)).one()
    task_ids = set(session.scalars(select(Task.id)))
    assert edge.predecessor_id in task_ids
    assert edge.successor_id in task_ids


def test_the_duplicate_a_later_sync_wrote_is_removed_not_renamed_over(session):
    """Run late, after the damage.

    A sync on an un-migrated database writes the new-style row beside the old
    one. Renaming the old row onto an id that now exists would fail on the
    primary key; the old row is the duplicate, so it is the one to drop.
    """
    _old_task(session, PROJECT, "WBS-101")
    session.add(
        Task(
            id="excel:Task:1:excel%3AProject%3A1%3AHRMS:WBS-101",
            project_id=PROJECT,
            title="written by the sync after the change",
        )
    )
    session.flush()
    assert session.scalar(select(func.count()).select_from(Task)) == 2

    counts = _migrate(session)

    remaining = session.scalars(select(Task)).all()
    assert len(remaining) == 1
    assert remaining[0].title == "written by the sync after the change"
    assert counts["tasks deleted (duplicate)"] == 1


def test_two_projects_sharing_a_row_key_end_up_as_two_rows(session):
    """The whole point. Before the change these were one row; the migration
    cannot invent the one that was lost, but it must not merge the two a
    re-sync then creates."""
    _old_task(session, PROJECT, "1")
    session.add(
        Task(
            id="excel:Task:1:excel%3AProject%3A1%3ASAIN:1",
            project_id=OTHER,
            title="SAIN's own task 1",
        )
    )
    session.flush()

    _migrate(session)

    by_project = dict(
        session.execute(
            select(Task.project_id, func.count()).group_by(Task.project_id)
        ).all()
    )
    assert by_project == {PROJECT: 1, OTHER: 1}


def test_a_qa_item_is_re_keyed_too(session):
    session.add(QaItem(id="excel:QaItem:1:QA-7", project_id=PROJECT, test_case="QA-7"))
    session.flush()

    _migrate(session)

    assert list(session.scalars(select(QaItem.id))) == [
        "excel:QaItem:1:excel%3AProject%3A1%3AHRMS:QA-7"
    ]


def test_a_state_change_whose_task_is_gone_is_left_alone_rather_than_guessed(session):
    """A state change is evidence. With no row to read the project from, the
    honest move is to leave it as it is, not to pick a project for it."""
    from datetime import datetime

    session.add(
        StateChange(
            id="excel:StateChange:1:GONE-1:status:1772000000",
            entity_type="task",
            entity_id="excel:Task:1:GONE-1",
            field="status",
            occurred_at=datetime(2026, 3, 6, 9, 0),
            occurred_at_lower=datetime(2026, 3, 2, 9, 0),
            precision="bounded",
            ingested_at=datetime(2026, 3, 6, 9, 0),
        )
    )
    session.flush()

    _migrate(session)

    change = session.scalars(select(StateChange)).one()
    assert change.id == "excel:StateChange:1:GONE-1:status:1772000000"
    assert change.entity_id == "excel:Task:1:GONE-1"
