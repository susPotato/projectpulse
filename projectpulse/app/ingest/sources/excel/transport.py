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

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.ingest.sources.excel.reader import SheetContract


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
