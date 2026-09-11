"""How a watched workbook's bytes reach us - and nothing else.

This is the Excel counterpart of ``jira_replay``: it substitutes **only the
transport**, exactly as invariant 4 requires. The reader, the identity resolver,
the differ, the precision bounds and the dependency resolver all run unchanged
whether the file arrived from a synced folder on a laptop or from Microsoft Graph
on a hosted server. Swapping one for the other must never be a reason to reopen
`ingest.py`.

Today there is one implementation, :class:`LocalFolderSource`, reading the
OneDrive-synced folder. The eventual production route is Graph, which needs tenant
admin consent a synced folder does not - see `config.py`. A `GraphSource` would
download to a temp file and hand back the same two things every source hands back:

``local_path``
    Somewhere openpyxl can open. It may be a temp file; nothing downstream cares.

``display_uri``
    Where a *human* can find this document. For a synced folder that is the file
    path; for Graph it is the SharePoint web URL. This is what the evidence panel
    shows a PM, so it must never be a temp path they cannot open.

**The trap this module exists to prevent.** ``scope`` is the identity of a sheet
across scans - it is what finds the previous scan, the baseline rows, and hence
every ``occurred_at_lower`` bound. If scope were derived from the local file name,
a transport that downloads to ``/tmp/tmp8f3k.xlsx`` would produce a new scope on
every run: no baseline, every row "new", and a flood of fabricated state changes
with no lower bound. So scope comes from :attr:`WatchedSheet.file_name`, the
*logical* name, which is stable by construction. `local_path` must never reach it.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.ingest.sources.excel.reader import SheetContract

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchedSheet:
    """One sheet we watch, named logically rather than by location.

    ``file_name`` is an identifier, not a path. It is half of the scope key, so it
    has to stay the same when the transport changes underneath it.
    """

    file_name: str
    sheet_name: str
    contract: SheetContract
    project_id: str


@dataclass(frozen=True)
class FetchedSheet:
    """A workbook made available locally, plus where a human would find it."""

    watched: WatchedSheet
    local_path: Path
    display_uri: str

    def release(self) -> None:
        """Drop anything the fetch allocated.

        A no-op for a local folder. A remote source overrides this to delete the
        temp file it downloaded - always called, so a long-running server does not
        slowly fill its disk with copies of last week's schedule.
        """


@runtime_checkable
class SheetSource(Protocol):
    """Where workbooks come from."""

    def fetch(self, watched: WatchedSheet) -> FetchedSheet | None:
        """Make one workbook available locally.

        Returns ``None`` when the document is simply not there. That is a note,
        not a failure: someone has not uploaded this week's file yet, which is
        normal and must not fail the other sheets.
        """
        ...


class StoredSheetSource:
    """Read a workbook out of the database, falling back to a folder on disk.

    The transport a deployed app needs, and the one this module was written in
    anticipation of. A container's filesystem does not survive a deploy, so a
    document somebody imported through the browser had to be re-uploaded after
    every release; stored as bytes in Postgres beside its registration
    (`app/models/uploads.py`), it simply keeps working.

    It delegates to a folder source for anything not in the database, because
    the demo's own sheets are *generated* into `data_root` by
    `scripts.gen_demo_data` and there is no reason to copy them into a table -
    they are reproducible by construction, which uploaded documents are not.

    ⚠️ The temp file this writes must never reach `scope`. It hands back
    `watched.file_name` as the logical identity via `FetchedSheet.watched`, and
    `ingest_sheet` takes the scope from there - see the module docstring for
    what a temp path in the scope key would destroy.
    """

    def __init__(self, folder: SheetSource | None = None) -> None:
        self.folder = folder

    def fetch(self, watched: WatchedSheet) -> FetchedSheet | None:
        content, original = self._stored(watched.file_name)
        if content is None:
            return self.folder.fetch(watched) if self.folder else None

        handle = tempfile.NamedTemporaryFile(
            prefix="pulse-sheet-", suffix=".xlsx", delete=False
        )
        try:
            handle.write(content)
        finally:
            handle.close()

        return _TempFetched(
            watched=watched,
            local_path=Path(handle.name),
            # What a person would recognise. There is no path or URL to give
            # them - the document lives in our database because they handed it
            # to us - so name the file they uploaded and say where it went.
            display_uri=f"upload://{original or watched.file_name}",
        )

    @staticmethod
    def _stored(file_name: str) -> tuple[bytes | None, str | None]:
        """The stored bytes, or `(None, None)` if there are none to read.

        An unreachable database is "not stored here", which lets the folder
        fallback answer instead of failing the whole sync - the same courtesy
        a missing file already gets.
        """
        from app.db import session_scope
        from app.models.uploads import UploadedSheet

        try:
            with session_scope() as session:
                row = session.get(UploadedSheet, file_name)
                if row is None:
                    return None, None
                return row.content, row.original_filename
        except Exception as exc:  # noqa: BLE001 - see the docstring
            log.warning("could not read stored workbook %s: %s", file_name, exc)
            return None, None


@dataclass(frozen=True)
class _TempFetched(FetchedSheet):
    """A fetch backed by a temp file, which `release()` has to delete.

    Without this a long-running server slowly fills its disk with copies of
    every sheet it has ever scanned - the case `FetchedSheet.release`'s own
    docstring describes and had no implementation for until now.
    """

    def release(self) -> None:
        self.local_path.unlink(missing_ok=True)


class LocalFolderSource:
    """Read from a folder on disk - the OneDrive sync client's output.

    The demo route, and deliberately so: a synced folder needs no tenant admin
    consent, so the system is demonstrable without waiting on IT.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def fetch(self, watched: WatchedSheet) -> FetchedSheet | None:
        path = self.root / watched.file_name
        if not path.exists():
            return None
        return FetchedSheet(
            watched=watched,
            local_path=path,
            # The file really is at this path for a local PM, so it doubles as the
            # display URI. A remote source would diverge here.
            display_uri=f"file://{path.as_posix()}",
        )
