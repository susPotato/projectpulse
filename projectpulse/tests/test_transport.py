"""Guards for the Excel transport seam.

These are the only tests here that touch a database, and they use in-memory
SQLite - still no Docker, still milliseconds. They exist because the property
being protected cannot be observed in a pure function: it is about what
``ingest_sheet`` looks up from a *previous* scan.

The property: **where a workbook was read from must not change its identity.**
A hosted deployment downloads to a temp file whose name is different every run.
If that name reached the scope key, every scan would find no baseline, treat every
row as new, and emit a state change per row with no lower bound - fabricating
history and poisoning the ordering model at the same time.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingest.sources.excel.ingest import ingest_sheet
from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT
from app.ingest.sources.excel.transport import (
    FetchedSheet,
    LocalFolderSource,
    SheetSource,
    WatchedSheet,
)
from app.models.base import Base

WATCHED = WatchedSheet(
    file_name="hrms_schedule.xlsx",
    sheet_name="Activities",
    contract=SCHEDULE_CONTRACT,
    project_id="excel:Project:1:HRMS",
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def make_workbook(path, *, status="In Progress"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Activities"
    sheet.append(["HRMS Portal V2 - Delivery Schedule"])
    sheet.append([])
    sheet.append(["Task ID", "Activity", "Status", "Predecessor"])
    sheet.append(["WBS-101", "Requirements sign-off", "Done", None])
    sheet.append(["WBS-108", "Environment Setup", status, "WBS-101"])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def ingest(session, path, *, logical_name=None, display_uri=None, at="2026-03-02T09:00"):
    return ingest_sheet(
        session,
        source="excel",
        connection_id=1,
        file_path=path,
        sheet_name="Activities",
        contract=SCHEDULE_CONTRACT,
        project_id=WATCHED.project_id,
        now=datetime.fromisoformat(at).replace(tzinfo=timezone.utc),
        logical_name=logical_name,
        display_uri=display_uri,
    )


# --------------------------------------------------------------------------
# LocalFolderSource
# --------------------------------------------------------------------------


def test_a_missing_workbook_is_absent_not_an_error(tmp_path):
    """Nobody has uploaded this week's file yet. That is normal."""
    assert LocalFolderSource(tmp_path).fetch(WATCHED) is None


def test_a_present_workbook_is_fetched_with_a_human_reachable_uri(tmp_path):
    make_workbook(tmp_path / WATCHED.file_name)

    fetched = LocalFolderSource(tmp_path).fetch(WATCHED)

    assert fetched is not None
    assert fetched.local_path.exists()
    assert fetched.display_uri.startswith("file://")
    assert WATCHED.file_name in fetched.display_uri
    fetched.release()  # a no-op locally, but it must never raise


def test_local_folder_source_satisfies_the_protocol():
    assert isinstance(LocalFolderSource("."), SheetSource)


# --------------------------------------------------------------------------
# The property the seam exists for
# --------------------------------------------------------------------------


def test_the_same_sheet_read_from_a_different_path_keeps_its_identity(
    session, tmp_path
):
    """A downloaded copy is the same sheet, and must find its own baseline.

    This is the hosted case in miniature: run one reads the synced folder, run two
    reads a temp download of identical bytes. Same content, so the hash guard must
    fire - which it can only do if the scope key found the earlier scan.
    """
    first = make_workbook(tmp_path / "sync" / WATCHED.file_name)
    ingest(session, first, logical_name=WATCHED.file_name)

    second = tmp_path / "tmp8f3k2j.xlsx"  # what a download actually looks like
    shutil.copy(first, second)
    report = ingest(
        session, second, logical_name=WATCHED.file_name, at="2026-03-04T09:00"
    )

    assert report.scope == "hrms_schedule.xlsx#Activities"
    assert report.skipped_unchanged


def test_without_a_logical_name_a_moved_file_looks_like_a_new_sheet(session, tmp_path):
    """The negative control - this is the bug `logical_name` prevents.

    Identical bytes at a different path, scoped by file name, are treated as a
    sheet never seen before: no baseline, and every row would be 'new'.
    """
    first = make_workbook(tmp_path / "sync" / WATCHED.file_name)
    ingest(session, first)

    second = tmp_path / "tmp8f3k2j.xlsx"
    shutil.copy(first, second)
    report = ingest(session, second, at="2026-03-04T09:00")

    assert report.scope == "tmp8f3k2j.xlsx#Activities"
    assert not report.skipped_unchanged


def test_a_real_edit_is_still_detected_across_a_transport_change(session, tmp_path):
    """Identity must be stable without being blind: a genuine change still lands."""
    first = make_workbook(tmp_path / "sync" / WATCHED.file_name)
    ingest(session, first, logical_name=WATCHED.file_name)

    second = make_workbook(tmp_path / "tmp99.xlsx", status="Blocked")
    report = ingest(
        session, second, logical_name=WATCHED.file_name, at="2026-03-04T09:00"
    )

    assert not report.skipped_unchanged
    assert report.changes_emitted == 1


# --------------------------------------------------------------------------
# Evidence must point somewhere a person can open
# --------------------------------------------------------------------------


def test_evidence_uris_use_the_display_uri_not_the_temp_path(session, tmp_path):
    """A PM clicking through to evidence must not land on a deleted temp file."""
    path = make_workbook(tmp_path / "tmp8f3k2j.xlsx")
    remote = "https://fpt.sharepoint.com/sites/hrms/Shared%20Documents/schedule.xlsx"

    ingest(session, path, logical_name=WATCHED.file_name, display_uri=remote)

    from app.models.raw import RawExcelRows
    from app.models.sync import SheetScan

    scan = session.query(SheetScan).one()
    raw = session.query(RawExcelRows).first()

    assert scan.file_path == remote
    assert raw.url.startswith(remote)
    assert "tmp8f3k2j" not in raw.url


def test_a_fetched_sheet_carries_what_ingest_needs(tmp_path):
    """The seam's whole contract: a local path, and a human-reachable URI."""
    make_workbook(tmp_path / WATCHED.file_name)
    fetched = LocalFolderSource(tmp_path).fetch(WATCHED)

    assert isinstance(fetched, FetchedSheet)
    assert fetched.watched.file_name == WATCHED.file_name


def test_default_transport_is_local_unless_switched_on(tmp_path):
    """`PULSE_EXCEL_TRANSPORT` picks the source; local stays the default so
    installing the `onedrive` extra never changes behaviour by itself.

    "Local" is now stored-then-folder: an imported document lives in the
    database so it survives a deploy, and the demo's generated sheets stay in
    `data_root` because they are reproducible and a table would only duplicate
    them. The folder is still what answers for them, which is what this pins.
    """
    from app.ingest.sources.excel.source import _default_transport
    from app.ingest.sources.excel.transport import StoredSheetSource

    source = _default_transport(session=object(), data_root=tmp_path)
    assert isinstance(source, StoredSheetSource)
    assert isinstance(source.folder, LocalFolderSource)
    assert source.folder.root == tmp_path


def test_graph_transport_is_selected_when_configured(monkeypatch):
    import dataclasses

    from app.ingest.sources.excel.graph_source import GraphSheetSource
    from app.ingest.sources.excel.source import _default_transport

    # `settings` is a frozen dataclass - swap the module-level name for a
    # variant instance rather than mutating a field on the shared one.
    from app.config import settings as live_settings

    switched = dataclasses.replace(
        live_settings, excel_transport="graph", onedrive_folder="Documents/PP"
    )
    monkeypatch.setattr("app.ingest.sources.excel.source.settings", switched)

    session = object()
    source = _default_transport(session=session, data_root=None)

    assert isinstance(source, GraphSheetSource)
    assert source.folder == "Documents/PP"
    assert source.session is session
