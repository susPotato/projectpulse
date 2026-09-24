"""Starting a run through the API, and what it refuses.

The route's job is to fail *before* it starts anything. A run is minutes of a
shared CPU and it reports progress by polling, so every refusal it can make
synchronously is one a person reads immediately instead of finding in a log
they had to go and ask for. What it must refuse: no registration, no backlog,
and a repository it cannot read.

`TestClient(app, client=("127.0.0.1", 5000))` throughout, as in
`test_repo_api.py` - these are gated routes and loopback is how a local run
gets past the gate. `tests/test_route_auth.py` covers the gate itself.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import scope, traceability_run
from app.api.main import app
from app.db import session_scope
from app.ingest.sources.git import store
from app.models.traceability import TicketExport

PROJECT = "excel:Project:1:TRACETEST"


@pytest.fixture(autouse=True)
def _pipeline(monkeypatch):
    """The vendored copy - what the image ships. See test_repo_source.py."""
    from app.config import REPO_ROOT

    if not (REPO_ROOT / "tracelink" / "source.py").exists():
        pytest.skip("run `python -m scripts.vendor_tracelink --apply` first")
    monkeypatch.syspath_prepend(str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clean():
    """These tests share one database file, and one module-level run record."""
    def drop():
        with session_scope() as session:
            store.delete(session, PROJECT)
            row = session.get(TicketExport, PROJECT)
            if row is not None:
                session.delete(row)

    drop()
    traceability_run._current = None
    scope.register(PROJECT, "Traceability Test Project")
    yield
    drop()
    traceability_run._current = None


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 5000))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "architecture.md").write_text("# arch\n", encoding="utf-8")
    return root


@pytest.fixture
def registered(client, repo):
    response = client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    assert response.status_code == 200, response.text
    return repo


def _upload(client, content=b"not really a workbook", **form):
    return client.post(
        "/api/traceability/export",
        files={"file": ("backlog.xlsx", content,
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet")},
        data={"project_id": PROJECT, **form},
    )


# --------------------------------------------------------------------------
# The export
# --------------------------------------------------------------------------

def test_an_export_is_stored_byte_for_byte(client):
    """Not converted. `POST /api/sources/upload` converts and discards the
    original, which is right for the dashboard and useless to `governance`."""
    payload = b"PK\x03\x04 pretend this is a workbook"
    assert _upload(client, payload).status_code == 200

    with session_scope() as session:
        row = session.get(TicketExport, PROJECT)
        assert row is not None
        assert row.content == payload
        assert row.original_filename == "backlog.xlsx"


def test_re_uploading_replaces_rather_than_accumulates(client):
    """One project, one backlog - see the model docstring."""
    _upload(client, b"first")
    _upload(client, b"second")
    with session_scope() as session:
        assert session.get(TicketExport, PROJECT).content == b"second"


def test_the_export_options_are_stored_with_it(client):
    """`--project` and `--done-status` are properties of the file, not of the
    run, so storing them here is what stops two runs disagreeing."""
    assert _upload(client, project_filter="CoWorkLocal",
                   done_status="Done,Closed").status_code == 200
    with session_scope() as session:
        row = session.get(TicketExport, PROJECT)
        assert row.project_filter == "CoWorkLocal"
        assert row.done_status == "Done,Closed"


def test_the_listing_shows_the_export_without_its_bytes(client):
    """The size, never the blob - the rule `app/imports.py` already states.

    This runs on a settings page load, and selecting the entity would pull
    every stored workbook into memory to render a table of how big they are.
    """
    payload = b"PK\x03\x04" + b"x" * 5000
    _upload(client, payload, project_filter="CoWorkLocal", done_status="Done")

    body = client.get("/api/traceability/export").json()
    [row] = [r for r in body["exports"] if r["project_id"] == PROJECT]
    assert row["size"] == len(payload)
    assert row["original_filename"] == "backlog.xlsx"
    assert row["project_filter"] == "CoWorkLocal"
    assert row["done_status"] == "Done"
    assert "content" not in row


def test_the_listing_can_be_scoped_to_one_project(client):
    _upload(client)
    body = client.get("/api/traceability/export",
                      params={"project": PROJECT}).json()
    assert [r["project_id"] for r in body["exports"]] == [PROJECT]

    other = client.get("/api/traceability/export",
                       params={"project": "excel:Project:1:NOTHING"}).json()
    assert other["exports"] == []


def test_an_export_for_an_unknown_project_is_refused(client):
    response = client.post(
        "/api/traceability/export",
        files={"file": ("b.xlsx", b"x", "application/octet-stream")},
        data={"project_id": "excel:Project:1:NOPE"},
    )
    assert response.status_code == 400
    assert "unknown project" in response.json()["detail"]


def test_a_non_workbook_is_refused(client):
    response = client.post(
        "/api/traceability/export",
        files={"file": ("backlog.csv", b"a,b\n1,2\n", "text/csv")},
        data={"project_id": PROJECT},
    )
    assert response.status_code == 400
    assert "not an Excel workbook" in response.json()["detail"]


def test_an_empty_file_is_refused(client):
    response = client.post(
        "/api/traceability/export",
        files={"file": ("backlog.xlsx", b"", "application/octet-stream")},
        data={"project_id": PROJECT},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Starting a run
# --------------------------------------------------------------------------

def test_a_run_without_a_registration_is_refused(client):
    """And says where to go, rather than only that something is missing."""
    _upload(client)
    response = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "no repository is registered" in detail
    assert "Settings > Sources" in detail


def test_a_run_without_a_backlog_is_refused(client, registered):
    response = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "no export is uploaded and no Jira issues are synced" in detail


# --------------------------------------------------------------------------
# The synced Jira issues as the backlog, when nothing was uploaded
# --------------------------------------------------------------------------

JIRA_KEY = "TRJ"


CATEGORY = {"Done": "done", "Release": "done", "Cancelled": "done",
            "In Progress": "indeterminate"}


def _issue(n, summary, *, description=None, status="In Progress",
           components=(), roles=None, template=None):
    fields = {
        "summary": summary,
        "description": description,
        "status": {"name": status,
                   "statusCategory": {"key": CATEGORY.get(status, "new")}},
        "issuetype": {"name": "Task"},
        "assignee": {"displayName": "Lan Nguyen"},
        "components": [{"name": c} for c in components],
        # Per-issue prose in a custom field - kept.
        "customfield_20001": roles,
        # The same placeholder on every issue - template text, dropped.
        "customfield_20002": template,
    }
    return {"id": str(9000 + n), "key": f"{JIRA_KEY}-{n}", "fields": fields}


@pytest.fixture
def jira_synced():
    import json
    from datetime import datetime, timezone

    from app.models.jira import JiraConnection
    from app.models.raw import RawJiraIssues

    issues = [
        _issue(1, "Export the cart as PDF", description="Users download a PDF.",
               components=["Cart"], roles="PO: An / BA: Binh",
               template="Fill in the acceptance criteria"),
        _issue(2, "Sign in with SSO", status="Done",
               roles="PO: Chi / Developer: Dung",
               template="Fill in the acceptance criteria"),
        # An older copy of issue 2, synced earlier: the newest one wins.
        _issue(2, "Sign in (old title)"),
        _issue(3, "Belongs to another Jira project"),
    ]
    issues[3]["key"] = "OTHER-3"
    with session_scope() as session:
        link = JiraConnection(project_id=PROJECT, site="https://jira.example",
                              email="", project_key=JIRA_KEY, ciphertext="",
                              hint="")
        session.add(link)
        now = datetime.now(timezone.utc)
        # Oldest first, as the sync appends: issue 2's stale copy is added
        # before its current one.
        for issue in (issues[2], issues[0], issues[1], issues[3]):
            session.add(RawJiraIssues(params="{}", data=json.dumps(issue).encode(),
                                      url="", input=None, fetched_at=now))
    yield
    with session_scope() as session:
        for row in session.scalars(select_all(JiraConnection)):
            if row.project_id == PROJECT:
                session.delete(row)
        for row in session.scalars(select_all(RawJiraIssues)):
            if json.loads(row.data).get("id", "").startswith("900"):
                session.delete(row)


def select_all(model):
    from sqlalchemy import select

    return select(model)


def test_the_synced_jira_issues_become_a_backlog_the_reader_accepts(
        jira_synced, tmp_path):
    from tracelink.adapters.tickets_tabular import read_tickets

    from app.ingest.sources.jira.backlog import backlog_for_project

    with session_scope() as session:
        backlog = backlog_for_project(session, PROJECT)
    assert backlog["issues"] == 2
    assert backlog["name"] == "jira-TRJ.csv"

    path = tmp_path / backlog["name"]
    path.write_bytes(backlog["content"])
    text = path.read_text(encoding="utf-8-sig")
    assert "Sign in with SSO" in text and "old title" not in text
    assert "OTHER-3" not in text
    # Per-issue custom prose is carried; the shared placeholder is not.
    assert "BA: Binh" in text
    assert "Fill in the acceptance criteria" not in text
    # No components on issue 2, so it groups under its issue type.
    assert ",Task," in text

    tickets, shape = read_tickets(path)
    assert {t.key for t in tickets} == {"TRJ-1", "TRJ-2"}
    # From Jira's own status categories - the only done-category status here.
    assert backlog["done_status"] == "Done"


def test_a_cancelled_status_is_not_counted_as_delivered():
    """Jira files Cancelled under the done category by default; delivered it
    is not, and counting it would ask the code to prove unbuilt features."""
    from app.ingest.sources.jira.backlog import done_statuses

    issues = [_issue(1, "a", status="Release"), _issue(2, "b", status="Cancelled"),
              _issue(3, "c", status="In Progress")]
    assert done_statuses(issues) == ["Release"]


def test_a_run_with_no_upload_traces_the_synced_jira_issues(
        client, registered, jira_synced, monkeypatch, tmp_path):
    seen = {}

    def commands(**kw):
        import sys

        seen["export"] = kw["export"]
        seen["text"] = kw["export"].read_text(encoding="utf-8-sig")
        return [("pipeline", [sys.executable, "-c", "print('ok')"])]

    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr(traceability_run, "_commands", commands)

    response = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert response.status_code == 200, response.text
    assert response.json()["run"]["export_name"] == "jira-TRJ.csv"
    # Written with its own suffix, so the reader treats it as CSV.
    assert seen["export"].suffix == ".csv"
    assert "Export the cart as PDF" in seen["text"]
    _wait(client)


def test_an_uploaded_export_still_wins_over_jira(client, registered, jira_synced,
                                                 monkeypatch, tmp_path):
    import sys

    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr(traceability_run, "_commands",
                        lambda **kw: [("pipeline", [sys.executable, "-c", "0"])])
    _upload(client)
    response = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert response.status_code == 200, response.text
    assert response.json()["run"]["export_name"] != "jira-TRJ.csv"
    _wait(client)


def test_a_run_without_a_project_is_refused(client):
    response = client.post("/api/traceability/run", json={"project_id": ""})
    assert response.status_code == 400
    assert "Choose which delivery project" in response.json()["detail"]


def test_a_repository_that_cannot_be_read_fails_the_request(client, tmp_path):
    """Synchronously, with `tracelink.source`'s own message.

    The alternative is a 200, a background thread, and a person polling a
    status endpoint to discover the clone never happened.
    """
    bare = tmp_path / "bare"
    (bare / "src").mkdir(parents=True)
    (bare / "docs").mkdir()
    (bare / "docs" / "a.md").write_text("# a\n", encoding="utf-8")
    assert client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(bare), "docs_path": "docs",
    }).status_code == 200

    # Remove the documentation tree the registration was accepted against.
    (bare / "docs" / "a.md").unlink()
    (bare / "docs").rmdir()

    _upload(client)
    response = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert response.status_code == 400
    assert "docs" in response.json()["detail"]


def test_a_failed_fetch_is_remembered_on_the_registration(client, tmp_path):
    """`last_error` exists for the failure nobody was watching - and a run
    started from a page is exactly that once the request is over."""
    bare = tmp_path / "bare2"
    (bare / "docs").mkdir(parents=True)
    (bare / "docs" / "a.md").write_text("# a\n", encoding="utf-8")
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(bare), "docs_path": "docs",
    })
    (bare / "docs" / "a.md").unlink()

    _upload(client)
    client.post("/api/traceability/run", json={"project_id": PROJECT})

    with session_scope() as session:
        row = store.for_project(session, PROJECT)
        assert row is not None and row.last_error


def test_a_run_starts_and_is_reported(client, registered, monkeypatch, tmp_path):
    """The whole seam, with the pipeline itself stubbed.

    What the real stages do is `tracelink`'s business and is tested there;
    what this asserts is that a started run is visible, attributed to the
    right project, and lands in a directory under the runs root.
    """
    import sys

    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr(
        traceability_run, "_commands",
        lambda **kw: [("pipeline", [sys.executable, "-c",
                                    "print('=== tickets'); print('ok')"])],
    )

    _upload(client)
    response = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["run"]["project_id"] == PROJECT
    assert body["doc_count"] == 1
    # The commit a run was built from, which is what makes staleness
    # answerable later. A plain directory has none, and "" is the honest
    # answer rather than an absent key.
    assert "commit" in body

    status = client.get("/api/traceability/run").json()
    assert status["run"]["project_id"] == PROJECT
    assert isinstance(status["run"]["log"], list)


# --------------------------------------------------------------------------
# New trace against sync
# --------------------------------------------------------------------------

def _stub_pipeline(monkeypatch, tmp_path):
    """A run that succeeds instantly and writes the file `has_run` looks for."""
    import sys

    root = tmp_path / "runs"
    monkeypatch.setenv("TRACELINK_RUNS", str(root))

    def commands(**kw):
        target = kw["run_dir"] / "tickets.json"
        return [("pipeline", [
            sys.executable, "-c",
            f"import pathlib; pathlib.Path(r'{target}').write_text('{{}}')",
        ])]

    monkeypatch.setattr(traceability_run, "_commands", commands)
    return root


def _wait(client, timeout=30.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get("/api/traceability/run").json()
        if not body["running"]:
            return body
        time.sleep(0.02)
    raise AssertionError("the stubbed run did not finish")


def test_syncing_a_project_that_has_never_been_traced_is_refused(
        client, registered, monkeypatch, tmp_path):
    """Promoting it to a first run would answer a question nobody asked.

    A sync reports what changed since last time. With no last time there is
    nothing to compare against, so the honest answer is the sentence naming
    the other button - not a full first-run analysis labelled as an update.
    """
    _stub_pipeline(monkeypatch, tmp_path)
    _upload(client)

    response = client.post("/api/traceability/run",
                           json={"project_id": PROJECT, "mode": "sync"})
    assert response.status_code == 400
    assert "never been traced" in response.json()["detail"]
    assert "New trace" in response.json()["detail"]


def test_a_missing_registration_is_reported_before_the_sync_complaint(
        client, monkeypatch, tmp_path):
    """Ordering, and it is the whole point of where that check sits.

    A project with no repository has also never been traced, so both are
    true - and "register a repository" is the one they have to act on. The
    sync refusal arriving first would send them to press the other button,
    which fails for the real reason a moment later.
    """
    _stub_pipeline(monkeypatch, tmp_path)
    response = client.post("/api/traceability/run",
                           json={"project_id": PROJECT, "mode": "sync"})
    assert response.status_code == 400
    assert "no repository is registered" in response.json()["detail"]


def test_the_mode_defaults_to_new_then_sync(client, registered, monkeypatch,
                                            tmp_path):
    """No mode means "decide from what this project has".

    Both fixed defaults are wrong for somebody: `sync` refuses a first run,
    and `new` throws away the run an old caller meant to update.
    """
    _stub_pipeline(monkeypatch, tmp_path)
    _upload(client)

    first = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert first.status_code == 200, first.text
    assert first.json()["run"]["mode"] == "new"
    _wait(client)

    second = client.post("/api/traceability/run", json={"project_id": PROJECT})
    assert second.status_code == 200, second.text
    assert second.json()["run"]["mode"] == "sync"


def test_an_unknown_mode_is_refused_by_name(client, registered, monkeypatch,
                                            tmp_path):
    _stub_pipeline(monkeypatch, tmp_path)
    _upload(client)
    response = client.post("/api/traceability/run",
                           json={"project_id": PROJECT, "mode": "refresh"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "refresh" in detail and "'new'" in detail and "'sync'" in detail


def test_a_new_trace_clears_the_previous_artifacts_but_keeps_the_cache(
        client, registered, monkeypatch, tmp_path):
    """"Start over" means the conclusions, not the receipts.

    The verdict cache is content-addressed, so an entry whose inputs have
    changed is unreachable rather than wrong. Deleting it would re-bill every
    ticket to gain nothing, which is the opposite of what a person pressing
    a button labelled "new" is asking for.
    """
    root = _stub_pipeline(monkeypatch, tmp_path)
    _upload(client)

    assert client.post("/api/traceability/run",
                       json={"project_id": PROJECT}).status_code == 200
    _wait(client)

    run_dir = root / traceability_run._slug(PROJECT)
    stale = run_dir / "verdicts.json"
    stale.write_text('{"payload": []}', encoding="utf-8")
    cache = run_dir / "cache"
    cache.mkdir(exist_ok=True)
    (cache / "deadbeef.json").write_text('{"verdict": "corroborated"}',
                                         encoding="utf-8")

    started = client.post("/api/traceability/run",
                          json={"project_id": PROJECT, "mode": "new"})
    assert started.status_code == 200, started.text
    assert started.json()["run"]["cleared"] >= 2      # tickets + verdicts
    _wait(client)

    assert not stale.exists(), "a new trace kept the previous run's verdicts"
    assert (cache / "deadbeef.json").exists(), "a new trace discarded the cache"


def test_a_sync_keeps_what_the_previous_run_wrote(client, registered,
                                                  monkeypatch, tmp_path):
    root = _stub_pipeline(monkeypatch, tmp_path)
    _upload(client)

    assert client.post("/api/traceability/run",
                       json={"project_id": PROJECT}).status_code == 200
    _wait(client)

    run_dir = root / traceability_run._slug(PROJECT)
    kept = run_dir / "verdicts.json"
    kept.write_text('{"payload": []}', encoding="utf-8")

    started = client.post("/api/traceability/run",
                          json={"project_id": PROJECT, "mode": "sync"})
    assert started.status_code == 200, started.text
    assert started.json()["run"]["cleared"] == 0
    _wait(client)
    assert kept.exists(), "a sync threw away the run it was meant to update"


def test_the_status_route_says_whether_verdict_stages_run_here(client,
                                                               monkeypatch):
    """The page prints a caveat about cost, and the same build answers
    differently depending on one environment variable."""
    monkeypatch.delenv(traceability_run.VERDICTS_ENV, raising=False)
    assert client.get("/api/traceability/run").json()["verdicts"] is False
    monkeypatch.setenv(traceability_run.VERDICTS_ENV, "1")
    assert client.get("/api/traceability/run").json()["verdicts"] is True


def test_the_status_route_answers_before_any_run_has_happened(client):
    body = client.get("/api/traceability/run").json()
    assert body["running"] is False
    assert body["run"] is None
    # Whether this host could run one at all, so the page can disable the
    # button with a reason rather than after a failed press.
    assert "available" in body
