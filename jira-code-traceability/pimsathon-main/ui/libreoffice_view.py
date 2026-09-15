"""Embed a LibreOffice editor window inside a Qt panel (Windows only).

LibreOffice has no official embedding API, so this launches a *private*
LibreOffice instance (its own UserInstallation profile so the window is easy to
find) opening the document, then reparents that top-level window (window class
``SALFRAME``) into a Qt container via Win32 ``SetParent`` / ``SetWindowLong``.

This is best-effort and experimental. On non-Windows, when LibreOffice isn't
installed, or if the window can't be located/reparented, it degrades gracefully
to an "Open in LibreOffice" button (a normal external window) so nothing breaks.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QWindow
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core.doc_extract import find_soffice
from ..i18n import on_language_changed, tr
from .icons import icon

# Binary document formats that LibreOffice should own (plain text / csv stay in
# the built-in text editor, so they are intentionally excluded here).
DOC_SUFFIXES = {
    ".doc", ".docx", ".odt", ".rtf",
    ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp",
    ".pdf",
}


def is_document(path) -> bool:
    return Path(path).suffix.lower() in DOC_SUFFIXES


def _win_user32():
    """user32 with explicit HWND-safe signatures.

    Without argtypes, ctypes passes Python ints as 32-bit ``c_int``, which
    truncates 64-bit window handles on Win64 and makes every call operate on the
    wrong window. Declaring HWND (a pointer) keeps the full handle."""
    import ctypes
    from ctypes import wintypes

    u = ctypes.windll.user32
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongW.restype = ctypes.c_long
    u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    u.SetWindowLongW.restype = ctypes.c_long
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    u.PostMessageW.restype = wintypes.BOOL
    return u


class LibreOfficeView(QWidget):
    """Hosts an embedded LibreOffice window (Windows) or an external-open fallback."""

    POLL_MS = 400
    MAX_TRIES = 40  # ~16s to find the window before giving up

    def __init__(self):
        super().__init__()
        self._proc: subprocess.Popen | None = None
        self._profile_dir: Path | None = None
        self._container: QWidget | None = None
        self._foreign: QWindow | None = None
        self._hwnd: int | None = None
        self._path: str | None = None
        self._tries = 0

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._try_embed)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)
        self._info = QLabel("", alignment=Qt.AlignCenter)
        self._info.setObjectName("hint")
        self._info.setWordWrap(True)
        self._lay.addWidget(self._info)
        self._open_btn = QPushButton()
        self._open_btn.setIcon(icon("document"))
        self._open_btn.setObjectName("primary")
        self._open_btn.clicked.connect(self._open_external)
        self._open_btn.setVisible(False)
        self._lay.addWidget(self._open_btn, alignment=Qt.AlignCenter)

        # Make sure any embedded instance is torn down when the app quits.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.close_document)
        on_language_changed(self._retranslate)

    def _retranslate(self) -> None:
        self._open_btn.setText(tr("libreoffice.open_btn"))

    # ---- public API --------------------------------------------------
    def open_document(self, path: str) -> None:
        self.close_document()
        self._path = str(path)
        soffice = find_soffice()
        if not soffice:
            self._show_message(tr("libreoffice.not_found"), offer_open=False)
            return
        if sys.platform != "win32":
            self._show_message(tr("libreoffice.windows_only"), offer_open=True)
            return
        self._launch_and_embed(soffice)

    def close_document(self) -> None:
        self._poll.stop()
        self._tries = 0
        if self._container is not None:
            self._container.setParent(None)
            self._container.deleteLater()
            self._container = None
        self._foreign = None
        # Ask LibreOffice to close its window gracefully (lets it prompt to save),
        # then clean up the throwaway profile.
        if self._hwnd is not None:
            self._post_close(self._hwnd)
            self._hwnd = None
        self._cleanup_proc(graceful=True)
        self._clear_info()

    # ---- launch + embed (Windows) ------------------------------------
    def _launch_and_embed(self, soffice: str) -> None:
        try:
            self._profile_dir = Path(tempfile.mkdtemp(prefix=f"lo-embed-{uuid.uuid4().hex[:8]}-"))
            profile_url = "file:///" + str(self._profile_dir).replace("\\", "/")
            args = [
                soffice,
                f"-env:UserInstallation={profile_url}",
                "--norestore", "--nologo", "--nofirststartwizard",
                "--minimized",   # don't flash an external window before we embed it
                self._path,
            ]
            self._proc = subprocess.Popen(args)
        except Exception as exc:  # noqa: BLE001
            self._show_message(tr("libreoffice.start_failed", err=exc), offer_open=True)
            return
        self._show_message(tr("libreoffice.opening"))
        self._tries = 0
        self._poll.start()

    def _try_embed(self) -> None:
        self._tries += 1
        if self._tries > self.MAX_TRIES:
            self._poll.stop()
            self._show_message(tr("libreoffice.embed_failed"), offer_open=True)
            return
        hwnd = self._find_lo_window()
        if hwnd:
            self._poll.stop()
            self._embed_hwnd(hwnd)

    def _find_lo_window(self) -> int | None:
        """Find a visible top-level LibreOffice frame for this document."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = _win_user32()
        except Exception:  # noqa: BLE001
            return None
        stem = Path(self._path).stem.lower() if self._path else ""
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "SALFRAME" not in cls.value:
                return True
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, 512)
            # Match the document we just opened (title is "<file> - LibreOffice …").
            if stem and stem in title.value.lower():
                found.append(int(hwnd))
                return False
            return True

        try:
            user32.EnumWindows(_cb, 0)
        except Exception:  # noqa: BLE001
            return None
        return found[0] if found else None

    def _embed_hwnd(self, hwnd: int) -> None:
        try:
            user32 = _win_user32()
            GWL_STYLE = -16
            WS_CHILD = 0x40000000
            WS_VISIBLE = 0x10000000
            WS_CAPTION = 0x00C00000
            WS_THICKFRAME = 0x00040000
            WS_POPUP = 0x80000000
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            style = (style & ~WS_CAPTION & ~WS_THICKFRAME & ~WS_POPUP) | WS_CHILD | WS_VISIBLE
            user32.SetWindowLongW(hwnd, GWL_STYLE, style)

            self._hwnd = hwnd
            self._foreign = QWindow.fromWinId(hwnd)
            self._container = QWidget.createWindowContainer(self._foreign, self)
            self._container.setFocusPolicy(Qt.StrongFocus)
            self._clear_info()
            self._lay.addWidget(self._container, 1)
            # Reveal only now that it's reparented, so it never flashes outside.
            user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
        except Exception as exc:  # noqa: BLE001
            self._show_message(tr("libreoffice.embed_error", err=exc), offer_open=True)

    # ---- cleanup -----------------------------------------------------
    def _post_close(self, hwnd: int) -> None:
        try:
            WM_CLOSE = 0x0010
            _win_user32().PostMessageW(hwnd, WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001
            pass

    def _cleanup_proc(self, graceful: bool = True) -> None:
        proc, self._proc = self._proc, None
        profile, self._profile_dir = self._profile_dir, None
        if proc is not None and proc.poll() is None:
            try:
                if not graceful:
                    proc.terminate()
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)

    def _open_external(self) -> None:
        if not self._path:
            return
        soffice = find_soffice()
        try:
            if soffice:
                subprocess.Popen([soffice, self._path])
                return
        except Exception:  # noqa: BLE001
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    # ---- small helpers -----------------------------------------------
    def _show_message(self, text: str, offer_open: bool = False) -> None:
        self._info.setText(text)
        self._info.setVisible(True)
        self._open_btn.setVisible(bool(offer_open and self._path))

    def _clear_info(self) -> None:
        self._info.setVisible(False)
        self._open_btn.setVisible(False)
