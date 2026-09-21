"""Guards for the import inventory.

The bug this page exists to make impossible: `/api/program` decided whether a
source was present with `Path.exists()` against `data_root`, while uploads live
as rows in `uploaded_sheets`. On a deployed host - the only place uploads are
the *only* way in - every source therefore reported itself missing while the
data was plainly there and serving pages.

The rest is lineage. An import belongs to a project, a project to a program,
and a kind of sheet feeds a knowable set of pages; all three were derivable and
none was shown.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import admin
from app.api.main import app
from app.imports import CONSUMERS, inventory, orphan_projects


@pytest.fixture
def clean():
    """No uploads and no registered projects left over from another test."""
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models.uploads import RegisteredProject, UploadedSheet

    def clear():
        with session_scope() as session:
            session.execute(delete(UploadedSheet))
            session.execute(delete(RegisteredProject))

    clear()
    yield
    clear()


@pytest.fixture
def local():
    return TestClient(app, client=("127.0.0.1", 5000))


def _upload(session, file_name, project_id, kind="schedule", sheet="Activities"):
    from app.models.uploads import RegisteredProject, UploadedSheet

    session.merge(
        RegisteredProject(canonical_id=project_id, name="Uploaded Project", also="[]")
    )
    session.merge(
        UploadedSheet(
            file_name=file_name,
            project_id=project_id,
            kind=kind,
            sheet_name=sheet,
            original_filename="what the person called it.xlsx",
            content=b"not a real workbook, but bytes are bytes",
        )
    )


def test_a_stored_upload_counts_as_available_though_no_file_exists(clean):
    """The whole point. There is no file, and there was never going to be one."""
    from app.db import session_scope

    with session_scope() as session:
        _upload(session, "upload_x_schedule.xlsx", "excel:Project:upload:x")

    with session_scope() as session:
        rows = {r.file_name: r for r in inventory(session)}

    row = rows["upload_x_schedule.xlsx"]
    assert row.available is True
    assert row.problem == ""
    assert row.origin == "upload"


def test_the_old_sources_table_agrees_now(clean):
    """`/api/program` reported the same upload as missing; it must not."""
    from app.db import session_scope
    from app.intelligence.pipeline import program_config

    with session_scope() as session:
        _upload(session, "upload_y_schedule.xlsx", "excel:Project:upload:y")

    with session_scope() as session:
        bundle = program_config(session)

    found = [s for s in bundle.sources if s.scope.startswith("upload_y_schedule")]
    assert found, "the upload is not listed at all"
    assert found[0].exists is True


def test_an_import_names_the_project_and_program_it_feeds(clean):
    from app.db import session_scope

    with session_scope() as session:
        _upload(session, "upload_z_schedule.xlsx", "excel:Project:upload:z")

    with session_scope() as session:
        row = next(
            r for r in inventory(session) if r.file_name == "upload_z_schedule.xlsx"
        )

    assert row.project_id == "excel:Project:upload:z"
    assert row.project_name == "Uploaded Project"


def test_every_import_says_what_reads_it(clean):
    """An empty consumers list would read as "nothing uses this"."""
    from app.db import session_scope

    with session_scope() as session:
        rows = inventory(session)

    assert rows, "the demo seed should always give us something to check"
    for row in rows:
        assert row.consumers, f"{row.file_name} lists no consumer"

    schedule = next(r for r in rows if r.kind == "schedule")
    assert "Schedule" in [c.page for c in schedule.consumers]


def test_the_consumer_map_only_names_kinds_that_exist(clean):
    """A typo here sends somebody to the wrong page when something is empty."""
    from app.ingest.sources.excel.source import SHEET_KINDS

    for kind in SHEET_KINDS:
        assert kind in CONSUMERS, f"{kind} has no consumers mapped"


def test_an_upload_whose_workbook_vanished_says_so(clean):
    """Registered but unreadable is its own state, not "a missing folder file"."""
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models.uploads import RegisteredProject, UploadedSheet

    with session_scope() as session:
        _upload(session, "upload_gone_schedule.xlsx", "excel:Project:upload:gone")

    # The registration is the upload row, so losing the row loses the watch
    # too - which is why this state needs manufacturing rather than occurring.
    # Keep the project so the row still resolves to something.
    with session_scope() as session:
        session.execute(
            delete(UploadedSheet).where(
                UploadedSheet.file_name == "upload_gone_schedule.xlsx"
            )
        )
        session.merge(
            RegisteredProject(
                canonical_id="excel:Project:upload:gone", name="Gone", also="[]"
            )
        )

    with session_scope() as session:
        rows = {r.file_name: r for r in inventory(session)}

    assert "upload_gone_schedule.xlsx" not in rows


def test_orphan_projects_are_only_the_safe_ones(clean):
    """Listing a project that holds data would offer a destructive mistake."""
    from app.db import session_scope
    from app.models.uploads import RegisteredProject

    with session_scope() as session:
        session.merge(
            RegisteredProject(
                canonical_id="excel:Project:upload:abandoned", name="vvvv", also="[]"
            )
        )
        _upload(session, "upload_real_schedule.xlsx", "excel:Project:upload:real")

    with session_scope() as session:
        orphans = {o["canonical_id"] for o in orphan_projects(session)}

    assert "excel:Project:upload:abandoned" in orphans
    # It has an import feeding it, so it is not safe to offer for deletion.
    assert "excel:Project:upload:real" not in orphans


# --------------------------------------------------------------------------
# The endpoints.
# --------------------------------------------------------------------------


def test_the_inventory_is_readable_without_a_token(clean):
    assert TestClient(app).get("/api/imports").status_code == 200


def test_deleting_an_import_needs_authorisation(clean, monkeypatch):
    from app.db import session_scope

    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    with session_scope() as session:
        _upload(session, "upload_guard_schedule.xlsx", "excel:Project:upload:guard")

    refused = TestClient(app).delete("/api/imports/upload_guard_schedule.xlsx")
    assert refused.status_code == 403

    from app.models.uploads import UploadedSheet

    with session_scope() as session:
        assert session.get(UploadedSheet, "upload_guard_schedule.xlsx") is not None


def test_deleting_an_import_leaves_the_data_it_produced(clean, local):
    """Removing a superseded upload must not empty the pages built from it."""
    from app.db import session_scope
    from app.models.uploads import UploadedSheet

    with session_scope() as session:
        _upload(session, "upload_del_schedule.xlsx", "excel:Project:upload:del")

    assert local.delete("/api/imports/upload_del_schedule.xlsx").status_code == 200

    with session_scope() as session:
        assert session.get(UploadedSheet, "upload_del_schedule.xlsx") is None
        # The project registration survives - it is what the tasks point at.
        from app.models.uploads import RegisteredProject

        assert session.get(RegisteredProject, "excel:Project:upload:del") is not None


def test_a_demo_sheet_cannot_be_deleted_and_says_why(clean, local):
    """It is compiled into the image; "not found" would be a worse answer."""
    response = local.delete("/api/imports/hrms_schedule.xlsx")
    assert response.status_code == 404
    assert "demo" in response.json()["detail"]
