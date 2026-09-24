"""Settings › Demo reset: Jira data out, trace runs archived and restored."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import demo_reset
from app.api.main import app
from app.db import session_scope
from app.models.domain import Project, Task
from app.models.jira import JiraConnection
from app.models.sync import SyncRun

JIRA = "jira:Project:1:DEMO"
EXCEL = "excel:Project:1:KEEPME"


@pytest.fixture
def rows():
    def clear():
        with session_scope() as s:
            s.execute(delete(Task).where(Task.project_id.in_([JIRA, EXCEL])))
            s.execute(delete(Project).where(Project.id.in_([JIRA, EXCEL])))
            s.execute(delete(JiraConnection))
            s.execute(delete(SyncRun).where(SyncRun.source.like("jira%")))

    clear()
    with session_scope() as s:
        # Other tests leave Jira rows behind; start from none.
        demo_reset.clear_jira(s)
    with session_scope() as s:
        s.add(Project(id=JIRA, name="From Jira"))
        s.add(Project(id=EXCEL, name="From a sheet"))
        s.add(Task(id="jira:Task:1:DEMO-1", project_id=JIRA, title="t", status="TODO"))
        s.add(Task(id="excel:Task:1:KEEPME:1", project_id=EXCEL, title="t", status="TODO"))
        s.add(JiraConnection(project_id=EXCEL, site="https://x.atlassian.net",
                             project_key="DEMO", ciphertext="c"))
        s.add(SyncRun(source="jira_replay", trigger="manual"))
    yield
    clear()


@pytest.fixture
def local():
    return TestClient(app, client=("127.0.0.1", 5000))


def test_preview_counts_only_jira_rows(rows):
    with session_scope() as s:
        p = demo_reset.jira_preview(s)
    assert JIRA in p["project_ids"] and EXCEL not in p["project_ids"]
    assert p["counts"]["projects"] == 1 and p["counts"]["tasks"] == 1
    assert p["counts"]["sync_runs"] == 1 and len(p["connections"]) == 1


def test_clearing_keeps_other_sources_and_by_default_the_connection(rows, local):
    r = local.post("/api/demo/clear-jira", json={})
    assert r.status_code == 200 and r.json()["connections_removed"] == 0
    with session_scope() as s:
        assert s.get(Project, JIRA) is None
        assert s.get(Project, EXCEL) is not None
        assert s.scalar(select(Task.id).where(Task.project_id == EXCEL))
        assert s.scalars(select(JiraConnection)).first() is not None
        assert not s.scalars(select(SyncRun).where(SyncRun.source.like("jira%"))).all()


def test_connections_go_only_when_asked(rows, local):
    r = local.post("/api/demo/clear-jira", json={"forget_connections": True})
    assert r.json()["connections_removed"] == 1
    with session_scope() as s:
        assert s.scalars(select(JiraConnection)).first() is None


def test_writes_are_gated_off_the_machine(rows):
    remote = TestClient(app, client=("203.0.113.9", 5000))
    assert remote.post("/api/demo/clear-jira", json={}).status_code in (401, 403)
    assert remote.post("/api/demo/archive-traces").status_code in (401, 403)


def _run(root, name):
    d = root / name
    d.mkdir()
    (d / "tickets.json").write_text(json.dumps({"payload": []}))
    (d / "cache").mkdir()
    return d


def test_trace_runs_are_archived_and_restored(tmp_path, monkeypatch, local):
    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path))
    _run(tmp_path, "alpha")
    _run(tmp_path, "beta")
    assert demo_reset.traces_preview()["runs"] == ["alpha", "beta"]

    r = local.post("/api/demo/archive-traces").json()
    assert r["archived"] == ["alpha", "beta"]
    after = demo_reset.traces_preview()
    assert after["runs"] == [] and after["archives"][0]["runs"] == ["alpha", "beta"]
    # The page's own run listing no longer sees them.
    from app.api.tracelink_view import available_runs
    assert available_runs() == []

    # A newer run of the same project made since the archive wins.
    _run(tmp_path, "beta")
    r = local.post("/api/demo/restore-traces").json()
    assert r["restored"] == ["alpha"] and r["kept_archived"] == ["beta"]
    assert (tmp_path / "alpha" / "cache").is_dir()
