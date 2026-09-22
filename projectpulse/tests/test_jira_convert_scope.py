"""Conversion is scoped to the one Jira project a connection names.

A connection is a pairing of one delivery project with one Jira project
key, but its *tool rows* are not guaranteed to hold only that key - the
seeded demo writes HRMS rows against connection 1, and the real
CoWorkLocal collection then wrote 190 more against the same connection.
Converting by connection alone filed four HRMS issues into the CoWorkLocal
project, which is a wrong answer that no page could show as wrong.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ingest.sources.jira import convertor
from app.models.base import Base
from app.models.domain import StateChange, Task
from app.models.tool import ToolJiraChangelog, ToolJiraIssue

NOW = datetime(2026, 9, 22, 12, 0, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def _issue(session, issue_id, key, project_key):
    session.add(ToolJiraIssue(
        connection_id=1, issue_id=str(issue_id), issue_key=key,
        project_key=project_key, summary=f"summary {key}",
        issue_type="Task", status="To Do", status_category="new",
        assignee="someone", created_at_src=NOW, updated_at_src=NOW,
    ))


def _change(session, changelog_id, issue_id, field="status"):
    session.add(ToolJiraChangelog(
        connection_id=1, changelog_id=str(changelog_id), field=field,
        issue_id=str(issue_id), author="someone",
        from_value="To Do", to_value="Done", created_at_src=NOW,
    ))


def _populate(session):
    _issue(session, 1, "COWORKLOCAL-1", "COWORKLOCAL")
    _issue(session, 2, "COWORKLOCAL-2", "COWORKLOCAL")
    _issue(session, 90, "HRMS-90", "HRMS")
    _change(session, 11, 1)
    _change(session, 12, 2)
    _change(session, 99, 90)
    session.flush()


def test_only_this_projects_issues_become_its_tasks(session):
    _populate(session)
    n = convertor.convert_issues(session, connection_id=1,
                                 project_id="jira:Project:1:COWORKLOCAL",
                                 project_key="COWORKLOCAL")
    session.flush()
    assert n == 2
    titles = sorted(t.title for t in session.scalars(select(Task)).all())
    assert titles == ["summary COWORKLOCAL-1", "summary COWORKLOCAL-2"]


def test_another_projects_issue_is_not_filed_under_this_one(session):
    """The bug itself: four HRMS issues in the CoWorkLocal project."""
    _populate(session)
    convertor.convert_issues(session, connection_id=1,
                             project_id="jira:Project:1:COWORKLOCAL",
                             project_key="COWORKLOCAL")
    session.flush()
    filed = [t.title for t in session.scalars(select(Task)).all()]
    assert "summary HRMS-90" not in filed


def test_changelogs_follow_the_same_scope(session):
    """A changelog carries no project key, so it is scoped through its issue."""
    _populate(session)
    n = convertor.convert_changelogs(session, connection_id=1, now=NOW,
                                     project_key="COWORKLOCAL")
    session.flush()
    assert n == 2
    refs = sorted(c.source_ref for c in session.scalars(select(StateChange)).all())
    assert refs == ["jira:changelog:11", "jira:changelog:12"]


def test_every_state_change_lands_on_a_task_that_exists(session):
    """The invariant worth holding: a state change about a task nobody
    created is a row no page can ever join to."""
    _populate(session)
    convertor.convert_issues(session, connection_id=1,
                             project_id="jira:Project:1:COWORKLOCAL",
                             project_key="COWORKLOCAL")
    convertor.convert_changelogs(session, connection_id=1, now=NOW,
                                 project_key="COWORKLOCAL")
    session.flush()

    task_ids = {t.id for t in session.scalars(select(Task)).all()}
    changes = session.scalars(select(StateChange)).all()
    assert changes
    assert all(c.entity_id in task_ids for c in changes)


def test_without_a_key_the_old_unscoped_behaviour_is_kept(session):
    """The argument is optional so existing callers keep working - which
    also means the unscoped call still does the wrong thing, on purpose,
    rather than changing silently underneath them."""
    _populate(session)
    n = convertor.convert_issues(session, connection_id=1,
                                 project_id="jira:Project:1:COWORKLOCAL")
    assert n == 3


# Start dates, which Jira does not have and the changelog does

def _started(session, issue_id, when, to="In Progress", cid=5):
    session.add(ToolJiraChangelog(
        connection_id=1, changelog_id=str(cid), field="status",
        issue_id=str(issue_id), author="someone",
        from_value="To Do", to_value=to, created_at_src=when,
    ))


def _convert(session):
    convertor.convert_issues(session, connection_id=1,
                             project_id="jira:Project:1:COWORKLOCAL",
                             project_key="COWORKLOCAL")
    convertor.convert_changelogs(session, connection_id=1, now=NOW,
                                 project_key="COWORKLOCAL")
    session.flush()
    return convertor.derive_start_dates(
        session, project_id="jira:Project:1:COWORKLOCAL")


def test_the_first_move_out_of_to_do_becomes_the_start(session):
    _issue(session, 1, "COWORKLOCAL-1", "COWORKLOCAL")
    _started(session, 1, datetime(2026, 9, 10, 9, 0))
    session.flush()

    assert _convert(session) == 1
    task = session.scalars(select(Task)).one()
    assert task.start_date == datetime(2026, 9, 10, 9, 0).date()


def test_the_earliest_such_move_wins_not_the_latest(session):
    """A ticket reopened and restarted still began when it first began."""
    _issue(session, 1, "COWORKLOCAL-1", "COWORKLOCAL")
    _started(session, 1, datetime(2026, 9, 15, 9, 0), cid=5)
    _started(session, 1, datetime(2026, 9, 8, 9, 0), cid=6)
    session.flush()

    _convert(session)
    task = session.scalars(select(Task)).one()
    assert task.start_date == datetime(2026, 9, 8, 9, 0).date()


def test_a_ticket_closed_in_one_step_gets_no_start(session):
    """The bug this rule exists for. 153 of 156 tickets on this board carry
    exactly one status change - the closing one - so "first move out of To
    Do" was the same event as the finish, and every finished task got a
    start equal to its end. A dot, drawn as if work began the day it ended."""
    _issue(session, 1, "COWORKLOCAL-1", "COWORKLOCAL")
    _started(session, 1, datetime(2026, 9, 10, 9, 0), to="Release")
    session.flush()

    assert _convert(session) == 0
    assert session.scalars(select(Task)).one().start_date is None


def test_a_task_that_never_left_to_do_keeps_an_empty_start(session):
    """Not started is a real answer. Borrowing the due date would draw a
    bar for work nobody has begun."""
    _issue(session, 1, "COWORKLOCAL-1", "COWORKLOCAL")
    session.add(ToolJiraChangelog(
        connection_id=1, changelog_id="7", field="status", issue_id="1",
        author="a", from_value="Open", to_value="To Do",
        created_at_src=datetime(2026, 9, 9, 9, 0)))
    session.flush()

    assert _convert(session) == 0
    assert session.scalars(select(Task)).one().start_date is None


def test_a_start_that_already_exists_is_not_overwritten(session):
    """A planned start from another source outranks one derived here."""
    _issue(session, 1, "COWORKLOCAL-1", "COWORKLOCAL")
    _started(session, 1, datetime(2026, 9, 10, 9, 0))
    session.flush()
    convertor.convert_issues(session, connection_id=1,
                             project_id="jira:Project:1:COWORKLOCAL",
                             project_key="COWORKLOCAL")
    session.flush()
    task = session.scalars(select(Task)).one()
    task.start_date = datetime(2026, 1, 1).date()
    convertor.convert_changelogs(session, connection_id=1, now=NOW,
                                 project_key="COWORKLOCAL")
    session.flush()

    assert convertor.derive_start_dates(
        session, project_id="jira:Project:1:COWORKLOCAL") == 0
    assert session.scalars(select(Task)).one().start_date == datetime(2026, 1, 1).date()
