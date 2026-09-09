"""Uploading a document for a project through the browser.

The alternative ingestion path added alongside the project switcher: a
deployed host has no OneDrive-synced folder for `scripts.sync` to read, so
`POST /api/sources/upload` is how it gets data at all. These tests exercise
the whole thing - save the file, register it, run the same sync the PM's
button runs - against in-memory SQLite, no Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app import scope
from app.ingest.sources.excel import source as excel_source


@pytest.fixture()
def client():
    return TestClient(__import__("app.api.main", fromlist=["app"]).app)


@pytest.fixture(autouse=True)
def _isolated_registries(tmp_path, monkeypatch):
    """Each test gets its own project/watched-sheet registry and data root.

    Without this, one test's upload would persist into `PULSE_STATE_DIR` and
    leak into every other test that lists projects or watched sheets.

    `settings` is a frozen dataclass, so this replaces the module-level
    singleton itself rather than one of its fields - and does so on every
    module that imported it by name at its own top level (`app.api.main`,
    `app.ingest.sources.excel.source`), since a top-level `from x import y`
    binds a snapshot that a patch to `x.y` alone would not reach.
    """
    import dataclasses

    import app.api.main as main_module
    import app.config as config_module
    import app.ingest.sources.excel.source as excel_source_module

    patched = dataclasses.replace(
        config_module.settings,
        state_dir=tmp_path / "state",
        data_root=tmp_path / "data",
    )
    monkeypatch.setattr(config_module, "settings", patched)
    monkeypatch.setattr(main_module, "settings", patched)
    monkeypatch.setattr(excel_source_module, "settings", patched)
    yield


def _schedule_workbook(path: Path) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Activities"
    sheet.append(
        [
            "Task ID",
            "Activity",
            "Phase",
            "Milestone",
            "Status",
            "Owner",
            "Start",
            "Baseline Finish",
            "Planned Finish",
            "Progress",
            "Predecessor",
        ]
    )
    sheet.append(
        [
            "WBS-1",
            "Kickoff",
            "Planning",
            "No",
            "Done",
            "A. Owner",
            "2026-01-05",
            "2026-01-12",
            "2026-01-12",
            100,
            None,
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path.read_bytes()


def test_uploading_a_new_project_registers_and_ingests_it(client, tmp_path):
    contents = _schedule_workbook(tmp_path / "upload.xlsx")

    response = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "schedule", "project_name": "New Client Rollout"},
        files={"file": ("upload.xlsx", contents, "application/vnd.ms-excel")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["project_id"].startswith("excel:Project:upload:")
    assert body["project_name"] == "New Client Rollout"
    assert body["rows_ok"] >= 1

    # Registered for real, not just echoed back in the response.
    found = scope.find(body["project_id"])
    assert found is not None
    assert found.name == "New Client Rollout"

    portfolio = client.get("/api/portfolio").json()
    ids = [row["project_id"] for row in portfolio["projects"]]
    assert body["project_id"] in ids


def test_a_second_upload_for_the_same_project_updates_rather_than_duplicates(client, tmp_path):
    contents = _schedule_workbook(tmp_path / "upload.xlsx")
    first = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "schedule", "project_name": "Repeat Co"},
        files={"file": ("upload.xlsx", contents, "application/vnd.ms-excel")},
    ).json()

    second = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "schedule", "project_id": first["project_id"]},
        files={"file": ("upload.xlsx", contents, "application/vnd.ms-excel")},
    )

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["ok"] is True
    assert body["project_id"] == first["project_id"]
    assert body["file_name"] == first["file_name"]

    watched = [w.file_name for w in excel_source.all_watched() if w.project_id == first["project_id"]]
    assert watched == [first["file_name"]]  # one entry, not two


def test_an_unknown_sheet_kind_is_refused(client, tmp_path):
    contents = _schedule_workbook(tmp_path / "upload.xlsx")

    response = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "not-a-real-kind", "project_name": "Whatever"},
        files={"file": ("upload.xlsx", contents, "application/vnd.ms-excel")},
    )

    assert response.status_code == 400
    assert "sheet_kind" in response.json()["detail"]


def test_a_new_project_needs_a_name(client, tmp_path):
    contents = _schedule_workbook(tmp_path / "upload.xlsx")

    response = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "schedule"},
        files={"file": ("upload.xlsx", contents, "application/vnd.ms-excel")},
    )

    assert response.status_code == 400
    assert "project_name" in response.json()["detail"]


def test_an_unknown_project_id_is_refused(client, tmp_path):
    contents = _schedule_workbook(tmp_path / "upload.xlsx")

    response = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "schedule", "project_id": "excel:Project:upload:nope"},
        files={"file": ("upload.xlsx", contents, "application/vnd.ms-excel")},
    )

    assert response.status_code == 400
    assert "unknown project" in response.json()["detail"]


def test_a_non_excel_file_is_refused_by_extension_before_anything_is_read(client):
    response = client.post(
        "/api/sources/upload",
        data={"sheet_kind": "schedule", "project_name": "Wrong Format"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 400
    assert "Excel workbook" in response.json()["detail"]
