"""Best-effort URL preview for task/attachment links.

A link that points at a real document (PDF/Office/OpenDocument — by
Content-Type or, failing that, the URL's own extension) is downloaded in
full and run through ``doc_extract.extract_text`` — the SAME parser local
file attachments already use — so a link to a file behaves like an actual
attached file, not garbled text. Only when the link is NOT a recognized
document does this fall back to fetching a bounded preview and, for HTML,
stripping tags with a lightweight regex (no heavy dependency for that path).

Never raises: network failures, non-HTML/non-document content, oversized
pages and unparseable documents all degrade to a short explanatory note so a
bad link never breaks a task run.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

_TIMEOUT = (10, 20)              # (connect, read) seconds
_MAX_FETCH_BYTES = 2_000_000      # generic text/HTML preview cap (~2MB)
_MAX_DOC_FETCH_BYTES = 20_000_000  # a real document is downloaded in full, up to this cap
_MAX_PREVIEW_CHARS = 8_000

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# Content-Type → the suffix doc_extract.extract_text() dispatches on.
_CONTENT_TYPE_SUFFIX = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}
_DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm",
                 ".ppt", ".pptx", ".odt", ".ods", ".odp"}


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub("\n", text)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def _doc_suffix_for(url: str, content_type: str, disposition: str = "") -> str:
    """The doc_extract-recognized suffix for this response, or "" when it
    isn't a document at all. Checks Content-Type first, then the URL's own
    extension, then the Content-Disposition filename — share-link downloads
    (SharePoint/OneDrive) have extension-less URLs and often ship as
    application/octet-stream, so the disposition filename is the only tell."""
    suffix = _CONTENT_TYPE_SUFFIX.get(content_type, "")
    if suffix:
        return suffix
    path_suffix = Path(urlparse(url).path).suffix.lower()
    if path_suffix in _DOC_SUFFIXES:
        return path_suffix
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition or "")
    if m:
        disp_suffix = Path(m.group(1).strip()).suffix.lower()
        if disp_suffix in _DOC_SUFFIXES:
            return disp_suffix
    return ""


# ---- SharePoint / OneDrive share links --------------------------------------
_SHAREPOINT_HOST_RE = re.compile(r"(^|\.)sharepoint\.com$", re.IGNORECASE)
_ONEDRIVE_HOSTS = {"1drv.ms", "onedrive.live.com"}


def _is_share_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(_SHAREPOINT_HOST_RE.search(host)) or host in _ONEDRIVE_HOSTS


def _share_download_url(url: str) -> Optional[str]:
    """Turn a SharePoint / OneDrive SHARE link into a direct-download URL, or
    None when ``url`` isn't a share link. No auth is used — this works for
    links shared as "Anyone with the link"; an access-protected link comes
    back as an HTML sign-in page, which the caller detects and explains.

    - ``https://<tenant>.sharepoint.com/:x:/...`` (and /personal/, /sites/
      Shared Documents file links) → same URL + ``download=1``.
    - ``https://1drv.ms/...`` / ``onedrive.live.com`` → the public OneDrive
      shares API: ``https://api.onedrive.com/v1.0/shares/u!<b64url>/root/content``.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in _ONEDRIVE_HOSTS:
        import base64

        token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        return f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"
    if _SHAREPOINT_HOST_RE.search(host):
        sep = "&" if parsed.query else "?"
        if "download=1" in (parsed.query or ""):
            return url
        return f"{url}{sep}download=1"
    return None


def _extract_document(raw: bytes, suffix: str):
    """``(text, note)`` via doc_extract.extract_text() on a temp copy of
    ``raw`` — mirrors how a local file attachment of the same type is read."""
    from . import doc_extract

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(raw)
            tmp_path = f.name
        return doc_extract.extract_text(tmp_path)
    except OSError as exc:
        return None, str(exc)
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass


def fetch_link_preview(url: str) -> str:
    """A short text preview of ``url``'s content, or a note explaining why
    none is available. Always returns a string, never raises."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return f"[Link: {url}] (not a fetchable http(s) URL — referenced by address only)"
    # SharePoint / OneDrive share links are rewritten to their direct-download
    # form so the shared FILE itself is fetched and parsed (like an attachment),
    # not the share page's HTML shell.
    is_share = _is_share_link(url)
    fetch_target = _share_download_url(url) or url
    try:
        from . import tls_trust

        # Same TLS auto-recovery the LLM provider calls already get: a
        # corporate gateway that terminates TLS with its own certificate used
        # to break fetch_url outright (SSLCertVerificationError) even when
        # "Allow the agent to fetch URLs" was on and network wasn't blocked —
        # this call site just never had the same self-signed-cert recovery.
        resp = tls_trust.request("get", fetch_target, timeout=_TIMEOUT, stream=True,
                                 headers={"User-Agent": "Mozilla/5.0 (CoworkLocal)"})
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        doc_suffix = _doc_suffix_for(url, content_type,
                                     resp.headers.get("Content-Disposition", ""))
        cap = _MAX_DOC_FETCH_BYTES if doc_suffix else _MAX_FETCH_BYTES
        raw = resp.raw.read(cap, decode_content=True) or b""
    except Exception as exc:  # noqa: BLE001 — a bad link must never break a task
        if is_share:
            return (f"[Link: {url}] (SharePoint/OneDrive share link — could not download: {exc}. "
                    "If the file needs sign-in, share it as 'Anyone with the link', or use the "
                    "locally-synced OneDrive folder path instead.)")
        return f"[Link: {url}] (could not fetch: {exc})"

    # A share link answered with an HTML page = an auth wall: a real shared
    # file downloads as the document itself, so an HTML response means a
    # sign-in/redirect page (even when the URL's own suffix looks like a doc).
    # Say that instead of dumping the login page's text into the prompt.
    if is_share and "html" in content_type:
        return (f"[Link: {url}] (SharePoint/OneDrive share link requires sign-in — the link "
                "returned a login page, not the file. Share it as 'Anyone with the link', or "
                "attach the file from the locally-synced OneDrive folder instead.)")

    if doc_suffix:
        text, note = _extract_document(raw, doc_suffix)
        if text is None:
            return f"[Link: {url}] (file: {doc_suffix}; could not read it: {note or 'unknown error'})"
        preview = text[:_MAX_PREVIEW_CHARS]
        trunc = "…" if len(text) > _MAX_PREVIEW_CHARS else ""
        return f"[Link: {url}] (file: {doc_suffix})\n{preview}{trunc}"

    text = raw.decode(resp.encoding or "utf-8", errors="replace")
    if "html" in content_type or "<html" in text[:500].lower():
        text = _html_to_text(text)
    preview = text[:_MAX_PREVIEW_CHARS]
    suffix = "…" if len(text) > _MAX_PREVIEW_CHARS else ""
    return f"[Link: {url}]\n{preview}{suffix}"
