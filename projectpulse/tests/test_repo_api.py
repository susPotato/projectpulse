"""Registering a repository through the API.

The order of operations is what these check. A registration is written only
after the repository has been read and its documentation tree found, so the
two states that cannot exist are: a row naming a commit nobody read, and a
project left behind by a refusal.

`TestClient(app, client=("127.0.0.1", 5000))` throughout - these are the
gated routes, and loopback is how local development gets past the gate
without a token. `tests/test_route_auth.py` covers the gate itself.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db import session_scope
from app.ingest.sources.git import store

PROJECT = "excel:Project:1:REPOTEST"


@pytest.fixture(autouse=True)
def _pipeline(monkeypatch):
    """The vendored copy - what the image ships. See test_repo_source.py."""
    from app.config import REPO_ROOT

    if not (REPO_ROOT / "tracelink" / "source.py").exists():
        pytest.skip("run `python -m scripts.vendor_tracelink --apply` first")
    monkeypatch.syspath_prepend(str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clean():
    """No row before, no row after - these tests share one database file."""
    def drop():
        with session_scope() as session:
            store.delete(session, PROJECT)

    drop()
    yield
    drop()


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


def _rows(client, project=PROJECT):
    body = client.get("/api/repos", params={"project": project}).json()
    return body["repos"]


# Registering


def test_a_repository_with_documents_registers(client, repo):
    response = client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["doc_count"] == 1
    assert body["docs_path"] == "docs"
    assert body["private"] is False


def test_the_registration_is_listed_without_a_credential(client, repo):
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    [row] = _rows(client)
    assert row["repo_url"] == str(repo)
    assert row["doc_count"] == 1
    assert row["private"] is False
    assert "token" not in row
    assert "ciphertext" not in row


def test_a_repository_without_documents_is_refused(client, tmp_path):
    root = tmp_path / "bare"
    (root / "src").mkdir(parents=True)
    response = client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(root), "docs_path": "docs",
    })
    assert response.status_code == 400
    assert "no 'docs' directory" in response.json()["detail"]


def test_a_refused_registration_leaves_no_row(client, tmp_path):
    """The fifth defect in CLAUDE.md section 0a, in its new form.

    A rejected upload used to leave a project sitting on the portfolio with
    no data, indistinguishable from one whose import had silently failed. A
    rejected repository must not leave a registration for the same reason.
    """
    root = tmp_path / "bare"
    (root / "src").mkdir(parents=True)
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(root), "docs_path": "docs",
    })
    assert _rows(client) == []


def test_a_failed_registration_does_not_disturb_the_previous_one(client, repo,
                                                                 tmp_path):
    """Re-registering against a broken URL must not lose what worked.

    The row is only written after a successful read, so the old commit and
    document count survive - which is what makes retrying safe.
    """
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    before = _rows(client)[0]

    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(tmp_path / "gone"),
        "docs_path": "docs",
    })
    after = _rows(client)[0]
    assert after["repo_url"] == before["repo_url"]
    assert after["doc_count"] == before["doc_count"]


def test_a_url_is_required(client):
    response = client.post("/api/repos", json={"project_id": PROJECT})
    assert response.status_code == 400
    assert "clone URL" in response.json()["detail"]


def test_a_project_is_required(client, repo):
    response = client.post("/api/repos", json={"repo_url": str(repo)})
    assert response.status_code == 400
    assert "delivery project" in response.json()["detail"]


# One repository per project


def test_re_registering_replaces_rather_than_adds(client, repo, tmp_path):
    """A project has one codebase. Two rows would give the corpus two
    answers to "where is this symbol?", and every verdict cites one."""
    second = tmp_path / "other"
    (second / "docs").mkdir(parents=True)
    (second / "docs" / "a.md").write_text("# a\n", encoding="utf-8")

    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(second), "docs_path": "docs",
    })
    rows = _rows(client)
    assert len(rows) == 1
    assert rows[0]["repo_url"] == str(second)


# Refreshing


def test_a_refresh_re_reads_and_reports_no_movement(client, repo):
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    response = client.post(f"/api/repos/{PROJECT}/refresh")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["moved"] is False
    assert body["doc_count"] == 1


def test_a_refresh_picks_up_a_new_document(client, repo):
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    (repo / "docs" / "adr-001.md").write_text("# adr\n", encoding="utf-8")
    body = client.post(f"/api/repos/{PROJECT}/refresh").json()
    assert body["doc_count"] == 2


def test_refreshing_an_unregistered_project_is_a_404(client):
    assert client.post(f"/api/repos/{PROJECT}/refresh").status_code == 404


def test_a_failed_refresh_keeps_the_row_and_records_why(client, repo):
    """The refresh that fails is the one nobody was watching.

    So the reason goes on the row rather than only into the response of a
    request that may have been a scheduled job.
    """
    import shutil

    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    shutil.rmtree(repo / "docs")

    response = client.post(f"/api/repos/{PROJECT}/refresh")
    assert response.status_code == 400

    [row] = _rows(client)
    assert "no 'docs' directory" in row["last_error"]
    # And the last good read survives, rather than being zeroed by a failure.
    assert row["doc_count"] == 1


# Forgetting


def test_a_registration_can_be_deleted(client, repo):
    client.post("/api/repos", json={
        "project_id": PROJECT, "repo_url": str(repo), "docs_path": "docs",
    })
    assert client.delete(f"/api/repos/{PROJECT}").status_code == 200
    assert _rows(client) == []


def test_deleting_an_unregistered_project_is_a_404(client):
    assert client.delete(f"/api/repos/{PROJECT}").status_code == 404


# What the server can do


def test_the_listing_says_whether_this_server_can_fetch_at_all(client):
    """A deployment missing git or the pipeline should say so on the page,
    not only when somebody presses the button."""
    body = client.get("/api/repos").json()
    assert "can_fetch" in body
    assert body["can_fetch"] is True
    assert body["reason"] == ""
