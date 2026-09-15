"""Small OS helpers for the UI (open a folder / file in the system manager)."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def open_folder(path: str | Path) -> None:
    """Open the folder containing ``path`` (or the folder itself) in the OS."""
    p = Path(path).expanduser()
    target = p if p.is_dir() else p.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def open_path(path: str | Path) -> None:
    """Open a file/folder directly with the default OS handler."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).expanduser())))


def open_location(value: str) -> None:
    """Open a graph node's associated location — a local file/folder OR a web
    URL, whichever ``value`` looks like. Used by the Structure (GraphRAG) view
    so a Shift+click behaves correctly whether the node points at a path on
    disk or a link, in both the embedded and browser-served D3 graph."""
    text = str(value or "").strip()
    if not text:
        return
    if _URL_RE.match(text):
        QDesktopServices.openUrl(QUrl(text))
    else:
        open_folder(text)
