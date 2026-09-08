"""Guards for the Agent tab's URL reading.

No real network calls - `urllib.request.urlopen` is monkeypatched with a
fake response object shaped like the one part of it this module reads
(`.headers`, `.read()`).
"""

from __future__ import annotations

import openpyxl
import pytest

from app.agent.link_fetch import fetch_and_extract, find_first_url


class _FakeResponse:
    def __init__(self, content_type: str, body: bytes):
        self.headers = {"Content-Type": content_type}
        self._body = body

    def read(self, _n=None):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, content_type: str, body: bytes):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout=None: _FakeResponse(content_type, body),
    )


def test_find_first_url_picks_the_first_link_in_text():
    text = "check https://example.com/a and also https://example.com/b"
    assert find_first_url(text) == "https://example.com/a"


def test_find_first_url_is_none_without_a_link():
    assert find_first_url("no links here") is None


def test_html_is_reduced_to_visible_text(monkeypatch):
    body = b"<html><head><style>body{color:red}</style></head>" \
           b"<body><h1>Title</h1><p>Hello <b>world</b>.</p><script>evil()</script></body></html>"
    _serve(monkeypatch, "text/html", body)

    text = fetch_and_extract("https://example.com/page.html")

    assert "Title" in text
    assert "Hello" in text
    assert "world" in text
    assert "evil()" not in text
    assert "color:red" not in text


def test_plain_text_passes_through(monkeypatch):
    _serve(monkeypatch, "text/plain", b"just some notes")
    assert fetch_and_extract("https://example.com/notes.txt") == "just some notes"


def test_an_xlsx_link_is_read_with_openpyxl(monkeypatch, tmp_path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Activities"
    sheet.append(["Task ID", "Status"])
    sheet.append(["WBS-1", "Blocked"])
    xlsx_path = tmp_path / "sheet.xlsx"
    workbook.save(xlsx_path)

    _serve(monkeypatch, "application/vnd.openxmlformats", xlsx_path.read_bytes())

    text = fetch_and_extract("https://example.com/sheet.xlsx")

    assert "Sheet: Activities" in text
    assert "WBS-1" in text
    assert "Blocked" in text


def test_a_pdf_link_says_so_rather_than_guessing(monkeypatch):
    _serve(monkeypatch, "application/pdf", b"%PDF-1.4 fake")
    text = fetch_and_extract("https://example.com/doc.pdf")
    assert "PDF" in text
    assert "not supported" in text


def test_a_network_failure_is_a_note_not_an_exception(monkeypatch):
    def raise_it(request, timeout=None):
        import urllib.error

        raise urllib.error.URLError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", raise_it)

    text = fetch_and_extract("https://example.com/unreachable")

    assert "could not fetch" in text


def test_an_oversized_download_is_skipped_rather_than_truncated_silently(monkeypatch):
    from app.agent import link_fetch

    _serve(monkeypatch, "text/plain", b"x" * (link_fetch.MAX_DOWNLOAD_BYTES + 10))

    text = fetch_and_extract("https://example.com/huge.txt")

    assert "too large" in text


def test_long_text_is_truncated_to_max_chars(monkeypatch):
    from app.agent import link_fetch

    _serve(monkeypatch, "text/plain", ("a" * (link_fetch.MAX_CHARS + 500)).encode())

    text = fetch_and_extract("https://example.com/long.txt")

    assert len(text) <= link_fetch.MAX_CHARS + len("\n[truncated]")
    assert text.endswith("[truncated]")
