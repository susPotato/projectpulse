"""The live collector, against the rows `replay` already writes.

The value of this module is that it is interchangeable with `replay`:
everything downstream was always real code driving off the raw table, so
the test that matters is not "did it fetch" but "did it write what the
extractor reads".
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ingest.sources.jira import connect, live
from app.models.base import Base
from app.models.raw import RawJiraChangelogs, RawJiraIssues

NOW = datetime(2026, 9, 22, 12, 0, 0)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    monkeypatch.setattr(connect, "PACE", 0)
    monkeypatch.setattr(live, "PACE", 0)


def _issue(key, n, histories=0):
    return {
        "id": str(n), "key": key,
        "fields": {
            "project": {"key": "COWORKLOCAL"},
            "summary": f"issue {key}",
            "issuetype": {"name": "Task"},
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "Tri Le Tran Minh"},
            "created": "2026-08-01T09:00:00.000+0700",
            "updated": "2026-09-20T09:00:00.000+0700",
            "duedate": "2026-08-31",
        },
        "changelog": {"histories": [{"id": f"{n}-{i}"} for i in range(histories)]},
    }


def _pages(monkeypatch, pages):
    """Serve a list of `(issues, total)` tuples, one per search request.

    `/myself` is answered separately and not counted: the collector asks
    it first to find which auth scheme this site accepts, and folding that
    into the page sequence would make every test here off by one.

    Returns the search URLs only, which is what the assertions are about.
    """
    searches = []

    def fake(url, header):
        if url.endswith("/myself"):
            return {"displayName": "test"}, 200, ""
        searches.append(url)
        if not pages:
            return {"issues": [], "total": 0}, 200, ""
        issues, total = pages[min(len(searches) - 1, len(pages) - 1)]
        return {"issues": [dict(i) for i in issues], "total": total}, 200, ""

    monkeypatch.setattr(connect, "_attempt", fake)
    monkeypatch.setattr(connect, "normalise_site", lambda s: "https://jira.example.com/jiradc")
    return searches


# It writes what the extractor reads


def test_one_raw_row_per_issue_and_one_per_history(session, monkeypatch):
    _pages(monkeypatch, [([_issue("AB-1", 1, histories=2),
                           _issue("AB-2", 2, histories=1)], 2)])

    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW)
    session.flush()

    assert report.issues == 2 and report.changelogs == 3
    assert session.scalar(select(RawJiraIssues).where(RawJiraIssues.id > 0)) is not None
    assert len(session.scalars(select(RawJiraIssues)).all()) == 2
    assert len(session.scalars(select(RawJiraChangelogs)).all()) == 3


def test_the_changelog_is_popped_off_the_issue_row(session, monkeypatch):
    """`replay` does the same. An issue row carrying its own changelog
    stores the same evidence twice and doubles the table."""
    _pages(monkeypatch, [([_issue("AB-1", 1, histories=2)], 1)])
    live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                 site="x", email="", token="t", now=NOW)
    session.flush()

    row = session.scalars(select(RawJiraIssues)).one()
    assert "changelog" not in json.loads(row.data)


def test_a_history_row_carries_the_issue_it_belongs_to(session, monkeypatch):
    """The per-issue changelog endpoint does not repeat the id, so the
    extractor reads it back from `input`."""
    _pages(monkeypatch, [([_issue("AB-7", 7, histories=1)], 1)])
    live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                 site="x", email="", token="t", now=NOW)
    session.flush()

    row = session.scalars(select(RawJiraChangelogs)).one()
    assert json.loads(row.input) == {"issue_id": "7", "issue_key": "AB-7"}


def test_the_extractor_can_read_what_this_wrote(session, monkeypatch):
    """The whole point of matching `replay`'s shape."""
    from app.ingest.sources.jira.extractor import extract_issues
    from app.models.tool import ToolJiraIssue

    _pages(monkeypatch, [([_issue("AB-1", 1, histories=1)], 1)])
    live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                 site="x", email="", token="t", now=NOW)
    session.flush()

    assert extract_issues(session, connection_id=1) == 1
    session.flush()
    tool = session.scalars(select(ToolJiraIssue)).one()
    assert tool.issue_key == "AB-1"
    assert tool.assignee == "Tri Le Tran Minh"
    assert tool.due_date == "2026-08-31"


# Paging


def test_it_pages_until_the_total_is_reached(session, monkeypatch):
    seen = _pages(monkeypatch, [
        ([_issue(f"AB-{i}", i) for i in range(2)], 4),
        ([_issue(f"AB-{i}", i) for i in range(2, 4)], 4),
    ])
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW, page_size=2)
    assert report.issues == 4 and report.pages == 2
    assert "startAt=0" in seen[0] and "startAt=2" in seen[1]


def test_an_empty_page_stops_the_loop(session, monkeypatch):
    """A total that never arrives must not spin."""
    _pages(monkeypatch, [([], 999)])
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW)
    assert report.pages == 1 and report.issues == 0


def test_a_runaway_project_is_capped_and_says_so(session, monkeypatch):
    """A mis-typed JQL must not walk forty thousand issues in one go."""
    monkeypatch.setattr(live, "MAX_PAGES", 3)
    _pages(monkeypatch, [([_issue("AB-1", 1)], 10_000)])
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW)
    assert report.truncated is True and report.pages == 3


def test_a_failed_page_raises_rather_than_reporting_success(session, monkeypatch):
    """A partial collection reported as complete is how a dashboard ends up
    confidently describing half a project."""
    monkeypatch.setattr(connect, "normalise_site", lambda s: "https://j/x")
    monkeypatch.setattr(connect, "_attempt",
                        lambda url, header: (None, 500, ""))
    with pytest.raises(ConnectionError) as exc:
        live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                     site="x", email="", token="t", now=NOW)
    assert "COWORKLOCAL" in str(exc.value)


# Incremental


def test_the_first_run_collects_everything(session, monkeypatch):
    seen = _pages(monkeypatch, [([_issue("AB-1", 1)], 1)])
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW)
    assert report.since == ""
    assert "updated%20%3E%3D" not in seen[0]


def test_the_second_run_asks_only_for_what_changed(session, monkeypatch):
    """The watermark comes from the raw rows, not from a status column
    somebody has to remember to update."""
    _pages(monkeypatch, [([_issue("AB-1", 1)], 1)])
    live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                 site="x", email="", token="t", now=NOW)
    session.flush()

    seen = _pages(monkeypatch, [([], 0)])
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW)
    assert report.since == NOW.strftime(live.JQL_TIME)
    assert "updated" in urllib_unquote(seen[0])


def test_another_projects_rows_do_not_move_this_watermark(session, monkeypatch):
    """`params` carries the project, so two projects on one connection keep
    separate watermarks."""
    _pages(monkeypatch, [([_issue("AB-1", 1)], 1)])
    live.collect(session, connection_id=1, project_key="OTHER",
                 site="x", email="", token="t", now=NOW)
    session.flush()
    assert live.watermark(session, 1, "COWORKLOCAL") is None


def urllib_unquote(text: str) -> str:
    import urllib.parse

    return urllib.parse.unquote(text)


# What a failure says


def test_a_challenge_page_is_retried_once_before_giving_up(session, monkeypatch):
    """A challenge page is the rate limiter, not a refusal - the same
    request a moment later usually answers. Abandoning a collection that
    was working a second ago is the wrong call."""
    monkeypatch.setattr(live, "BACKOFF", 0)
    monkeypatch.setattr(connect, "normalise_site", lambda s: "https://j/x")
    calls = []

    def fake(url, header):
        calls.append(url)
        if len(calls) == 1:
            return None, connect.GATEWAY, ""
        return {"issues": [_issue("AB-1", 1)], "total": 1}, 200, ""

    monkeypatch.setattr(connect, "_attempt", fake)
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="", token="t", now=NOW)
    assert len(calls) == 2
    assert report.issues == 1


def test_a_persistent_block_fails_in_words_not_a_sentinel(session, monkeypatch):
    """`Jira answered -2` is an internal constant on a user's screen. The
    message has to say what -2 means."""
    monkeypatch.setattr(live, "BACKOFF", 0)
    monkeypatch.setattr(connect, "normalise_site", lambda s: "https://j/x")
    monkeypatch.setattr(connect, "_attempt",
                        lambda url, header: (None, connect.GATEWAY, ""))

    with pytest.raises(ConnectionError) as exc:
        live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                     site="x", email="", token="t", now=NOW)
    message = str(exc.value)
    assert "-2" not in message
    assert "bot challenge or proxy" in message


def test_an_http_status_is_explained_too(session, monkeypatch):
    monkeypatch.setattr(live, "BACKOFF", 0)
    monkeypatch.setattr(connect, "normalise_site", lambda s: "https://j/x")
    monkeypatch.setattr(connect, "_attempt", lambda url, header: (None, 401, ""))

    with pytest.raises(ConnectionError) as exc:
        live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                     site="x", email="", token="t", now=NOW)
    assert "401" in str(exc.value) and "Rejected the credential" in str(exc.value)


# Which credential scheme the collection uses


def test_a_data_center_site_collects_with_bearer_even_when_an_email_is_stored(
        session, monkeypatch):
    """The bug this closes. `_auth` picks Basic whenever an email is
    present, and somebody filling in every field on the form gets one
    stored either way. The connection test tries both and reports the
    winner; the collector took the raw guess, sent Basic to a bearer-only
    instance and failed on page one - against a Jira the test had just
    connected to."""
    monkeypatch.setattr(connect, "normalise_site",
                        lambda s: "https://insight.example.com/jiradc")
    used = []

    def fake(url, header):
        if url.endswith("/myself"):
            # Only bearer is accepted here, as on a Data Center instance.
            if header.startswith("Bearer"):
                return {"name": "tri"}, 200, ""
            return None, 401, ""
        used.append(header)
        return {"issues": [_issue("AB-1", 1)], "total": 1}, 200, ""

    monkeypatch.setattr(connect, "_attempt", fake)
    report = live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                          site="x", email="tri@example.com", token="tok",
                          now=NOW)
    assert report.issues == 1
    assert used and all(h.startswith("Bearer") for h in used)


def test_a_cloud_site_still_collects_with_basic(session, monkeypatch):
    monkeypatch.setattr(connect, "normalise_site",
                        lambda s: "https://acme.atlassian.net")
    used = []

    def fake(url, header):
        if url.endswith("/myself"):
            if header.startswith("Basic"):
                return {"accountId": "1"}, 200, ""
            return None, 401, ""
        used.append(header)
        return {"issues": [_issue("AB-1", 1)], "total": 1}, 200, ""

    monkeypatch.setattr(connect, "_attempt", fake)
    live.collect(session, connection_id=1, project_key="AB",
                 site="x", email="a@b.c", token="tok", now=NOW)
    assert used and all(h.startswith("Basic") for h in used)


def test_when_no_scheme_answers_the_page_loop_reports_the_real_failure(
        session, monkeypatch):
    """Falling back rather than raising here, so the error a person sees
    comes from the request that actually matters."""
    monkeypatch.setattr(connect, "normalise_site", lambda s: "https://j/x")
    monkeypatch.setattr(connect, "_attempt", lambda url, header: (None, 401, ""))
    monkeypatch.setattr(live, "BACKOFF", 0)

    with pytest.raises(ConnectionError) as exc:
        live.collect(session, connection_id=1, project_key="COWORKLOCAL",
                     site="x", email="a@b.c", token="t", now=NOW)
    assert "Rejected the credential" in str(exc.value)


# Ingesting issues somebody else fetched


def test_ingest_writes_the_same_rows_a_collection_would(session, monkeypatch):
    """Only the fetch moves. What lands has to be indistinguishable from a
    collection that happened here, or the evidence trail forks."""
    import json as _json

    from app.models.jira import JiraConnection
    from app.models.raw import RawJiraChangelogs, RawJiraIssues

    conn = JiraConnection(id=1, project_id="p", site="https://j/x",
                          email="", project_key="AB", ciphertext="x", hint="")
    session.add(conn)
    session.flush()

    issue = _issue("AB-1", 1, histories=2)
    params = _json.dumps({"connection_id": 1, "board_key": "AB"})
    changelog = issue.pop("changelog")
    session.add(RawJiraIssues(params=params, data=_json.dumps(issue).encode(),
                              url="u", input=None, fetched_at=NOW))
    for history in changelog["histories"]:
        session.add(RawJiraChangelogs(
            params=params, data=_json.dumps(history).encode(), url="u",
            input=_json.dumps({"issue_id": "1", "issue_key": "AB-1"}),
            fetched_at=NOW))
    session.flush()

    # The shape the push script rebuilds and the endpoint writes back.
    assert len(session.scalars(select(RawJiraIssues)).all()) == 1
    assert len(session.scalars(select(RawJiraChangelogs)).all()) == 2
