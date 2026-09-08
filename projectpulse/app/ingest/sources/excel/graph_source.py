"""OneDrive via Microsoft Graph - the production `SheetSource` `transport.py`
was written for.

Downloads to a temp file and hands back the same `FetchedSheet` a local
folder does; everything past `fetch()` - the reader, the differ, the
identity resolver - runs unchanged (invariant 4). See `graph_auth.py` for
how sign-in works and why it needs no tenant admin consent.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from urllib.parse import quote

from app.ingest.sources.excel.graph_auth import GraphAuthError, get_access_token
from app.ingest.sources.excel.transport import FetchedSheet, WatchedSheet

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
#: Both metadata and content calls, per request - Graph's own guidance for a
#: personal OneDrive; a schedule workbook is a few hundred KB, not a bulk
#: transfer.
TIMEOUT_SECONDS = 30


def _path_segment(path: str) -> str:
    """Encode a OneDrive-relative path for Graph's `root:/{path}:` addressing."""
    return "/".join(quote(part) for part in path.strip("/").split("/"))


class _GraphFetchedSheet(FetchedSheet):
    """`FetchedSheet` whose `release()` deletes the temp file it downloaded.

    A no-op override, not a new dataclass - `FetchedSheet` already declares
    every field this needs.
    """

    def release(self) -> None:
        try:
            self.local_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort cleanup
            pass


class GraphSheetSource:
    """Reads watched sheets from one person's OneDrive, under `folder`.

    `folder` is relative to the signed-in account's OneDrive root - empty
    means the root itself. `WatchedSheet.file_name` is joined onto it, the
    same way `LocalFolderSource` joins it onto a local directory.
    """

    def __init__(self, session, *, folder: str = "") -> None:
        self.session = session
        self.folder = folder.strip("/")

    def _item_path(self, file_name: str) -> str:
        return f"{self.folder}/{file_name}" if self.folder else file_name

    def fetch(self, watched: WatchedSheet) -> FetchedSheet | None:
        import requests

        try:
            token = get_access_token(self.session)
        except GraphAuthError as exc:
            # Not signed in, or the session expired: a note for the sync
            # summary, not a failure that should take the other sheets down.
            log.warning("OneDrive unavailable for %s: %s", watched.file_name, exc)
            return None

        segment = _path_segment(self._item_path(watched.file_name))
        headers = {"Authorization": f"Bearer {token}"}

        meta = requests.get(
            f"{GRAPH_BASE}/me/drive/root:/{segment}",
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        if meta.status_code == 404:
            # Not uploaded to OneDrive yet - the same "not there" a missing
            # local file reports, never a sync failure.
            return None
        meta.raise_for_status()
        # webUrl is what a PM can actually open; the evidence panel must
        # never show them a local temp path they cannot reach.
        display_uri = meta.json().get("webUrl") or self._item_path(watched.file_name)

        content = requests.get(
            f"{GRAPH_BASE}/me/drive/root:/{segment}:/content",
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        content.raise_for_status()

        handle = tempfile.NamedTemporaryFile(
            suffix=f"_{watched.file_name}", delete=False
        )
        try:
            handle.write(content.content)
        finally:
            handle.close()

        return _GraphFetchedSheet(
            watched=watched,
            local_path=Path(handle.name),
            display_uri=display_uri,
        )
