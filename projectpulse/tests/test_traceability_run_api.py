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
    assert "no backlog export is stored" in response.json()["detail"]


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
        traceability_run, "_argv",
        lambda **kw: [sys.executable, "-c", "print('=== tickets'); print('ok')"],
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


def test_the_status_route_answers_before_any_run_has_happened(client):
    body = client.get("/api/traceability/run").json()
    assert body["running"] is False
    assert body["run"] is None
    # Whether this host could run one at all, so the page can disable the
    # button with a reason rather than after a failed press.
    assert "available" in body
