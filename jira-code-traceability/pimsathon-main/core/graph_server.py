"""Serve the D3 Structure (RAG) graph over localhost for the default browser.

This is what keeps the FULL D3 knowledge-graph experience (drag/zoom, legend
filters, search, tooltips, click-a-node-to-open-its-folder) available in
builds without QtWebEngine — e.g. the standalone PyInstaller .exe. The tab
renders the same HTML as the embedded WebEngine view, but hands it to this
tiny HTTP server and opens the user's browser at its URL; node clicks come
back over an ``/open`` request instead of the QWebChannel bridge.

Security: the server binds to 127.0.0.1 only and every request must carry a
random per-session token, so another local process (or a web page attempting
DNS rebinding) can neither read the graph nor trigger folder-opens.
"""
from __future__ import annotations

import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

_PLACEHOLDER = ("<!DOCTYPE html><html><body style='background:#111;color:#ddd;"
                "font-family:sans-serif'><p>No graph yet — scan one in the "
                "Structure (RAG) tab first.</p></body></html>")


class GraphServer:
    """Lazy singleton-per-instance localhost server for the D3 graph page."""

    def __init__(self) -> None:
        self._html = _PLACEHOLDER
        self._token = secrets.token_urlsafe(16)
        self._lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._open_cb: Optional[Callable[[str], None]] = None

    # ---- content / callbacks ----------------------------------------
    def set_html(self, html: str) -> None:
        with self._lock:
            self._html = html

    def set_open_callback(self, cb: Callable[[str], None]) -> None:
        """Called (from the server thread) with the node's storage path."""
        self._open_cb = cb

    # ---- lifecycle ----------------------------------------------------
    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def url(self) -> str:
        if self._httpd is None:
            return ""
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}/?t={self._token}"

    def start(self) -> str:
        """Start (idempotent) and return the tokenised URL to open."""
        if self._httpd is not None:
            return self.url
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:  # keep the GUI console silent
                pass

            def _authorized(self, query: dict) -> bool:
                supplied = (query.get("t") or [""])[0]
                return secrets.compare_digest(supplied, server._token)

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if not self._authorized(query):
                    self.send_error(403)
                    return
                if parsed.path == "/":
                    with server._lock:
                        body = server._html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    # The page must never end up cached with a stale graph.
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif parsed.path == "/open":
                    path = (query.get("path") or [""])[0]
                    cb = server._open_cb
                    if path and cb is not None:
                        try:
                            cb(path)
                        except Exception:  # noqa: BLE001 - never kill the server
                            pass
                    self.send_response(204)
                    self.end_headers()
                else:
                    self.send_error(404)

        # Port 0 = let the OS pick a free port; loopback only.
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="graph-server", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        self._thread = None
