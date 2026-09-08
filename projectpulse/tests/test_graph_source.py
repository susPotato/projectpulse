"""Guards for `GraphSheetSource`.

Mocks the Graph HTTP calls rather than the sign-in layer's own tests'
territory (`test_graph_auth.py`) or a real network - this module's job is
"given a token, fetch the right path and hand back a `FetchedSheet` that
behaves like `LocalFolderSource`'s", which needs neither.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("requests")

from app.ingest.sources.excel.graph_auth import GraphAuthError
from app.ingest.sources.excel.graph_source import GraphSheetSource
from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT
from app.ingest.sources.excel.transport import WatchedSheet

WATCHED = WatchedSheet(
    "hrms_schedule.xlsx", "Activities", SCHEDULE_CONTRACT, "excel:Project:1:HRMS"
)


class _Resp:
    def __init__(self, status_code=200, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _source(monkeypatch, *, folder="", token_ok=True):
    monkeypatch.setattr(
        "app.ingest.sources.excel.graph_source.get_access_token",
        lambda session: "fake-token" if token_ok else (_ for _ in ()).throw(
            GraphAuthError("not signed in")
        ),
    )
    return GraphSheetSource(session=object(), folder=folder)


def test_not_signed_in_returns_none_rather_than_raising(monkeypatch):
    source = _source(monkeypatch, token_ok=False)
    assert source.fetch(WATCHED) is None


def test_a_missing_file_returns_none(monkeypatch):
    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return _Resp(status_code=404)

    monkeypatch.setattr("requests.get", fake_get)
    source = _source(monkeypatch)

    assert source.fetch(WATCHED) is None
    assert any("hrms_schedule.xlsx" in url for url in calls)


def test_a_present_file_downloads_to_a_temp_path_with_the_web_url(monkeypatch, tmp_path):
    def fake_get(url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer fake-token"
        if url.endswith(":/content"):
            return _Resp(content=b"PK\x03\x04fake-xlsx-bytes")
        return _Resp(json_body={"webUrl": "https://onedrive.live.com/item/xyz"})

    monkeypatch.setattr("requests.get", fake_get)
    source = _source(monkeypatch)

    fetched = source.fetch(WATCHED)
    try:
        assert fetched is not None
        assert fetched.display_uri == "https://onedrive.live.com/item/xyz"
        assert Path(fetched.local_path).read_bytes() == b"PK\x03\x04fake-xlsx-bytes"
        assert fetched.watched is WATCHED
    finally:
        if fetched is not None:
            fetched.release()

    # release() must actually delete the temp file, the same guarantee
    # `transport.py`'s `FetchedSheet.release` docstring promises - a
    # long-running server must not slowly fill its disk.
    assert not Path(fetched.local_path).exists()


def test_the_watched_folder_is_joined_onto_the_file_name(monkeypatch):
    seen_urls = []

    def fake_get(url, headers=None, timeout=None):
        seen_urls.append(url)
        if url.endswith(":/content"):
            return _Resp(content=b"data")
        return _Resp(json_body={"webUrl": "https://example/whatever"})

    monkeypatch.setattr("requests.get", fake_get)
    source = _source(monkeypatch, folder="Documents/ProjectPulse")

    fetched = source.fetch(WATCHED)
    fetched.release()

    assert any("Documents/ProjectPulse" in url for url in seen_urls)


def test_a_server_error_on_metadata_propagates_rather_than_being_swallowed(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(status_code=500)

    monkeypatch.setattr("requests.get", fake_get)
    source = _source(monkeypatch)

    with pytest.raises(RuntimeError):
        source.fetch(WATCHED)
