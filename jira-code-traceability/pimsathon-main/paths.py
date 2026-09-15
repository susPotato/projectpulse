"""Workspace path helpers, including OneDrive root detection.

On Windows, OneDrive is a locally synced folder, so "running in OneDrive"
simply means using that folder as the working directory; the OS keeps it in
sync with the cloud. We detect the root from environment variables that the
OneDrive client sets, with sensible cross-platform fallbacks.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List


def detect_onedrive_roots() -> List[Path]:
    """Return existing OneDrive root folders, most-preferred first.

    Order: commercial (work/school) > generic > consumer > home fallbacks.
    Only paths that actually exist are returned.
    """
    candidates: List[Path] = []
    for var in ("OneDriveCommercial", "OneDrive", "OneDriveConsumer"):
        val = os.environ.get(var)
        if val:
            candidates.append(Path(val))

    home = Path.home()
    # Common fallbacks when the env vars are absent (e.g. mac/linux test boxes).
    candidates.append(home / "OneDrive")
    try:
        for child in home.iterdir():
            if child.is_dir() and child.name.lower().startswith("onedrive"):
                candidates.append(child)
    except OSError:
        pass

    seen: set[str] = set()
    roots: List[Path] = []
    for path in candidates:
        try:
            resolved = path.expanduser()
        except RuntimeError:
            continue
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists() and resolved.is_dir():
            roots.append(resolved)
    return roots


def primary_onedrive_root() -> Path | None:
    roots = detect_onedrive_roots()
    return roots[0] if roots else None


def is_onedrive_path(path: os.PathLike | str) -> bool:
    """True if ``path`` lives under any detected OneDrive root."""
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for root in detect_onedrive_roots():
        try:
            target.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def normalize_workdir(path: os.PathLike | str) -> Path:
    """Expand user (~) and resolve to an absolute path."""
    return Path(path).expanduser().resolve()


def default_workdir(stored: str | None = None) -> Path:
    """Pick a sensible default working directory."""
    if stored:
        p = Path(stored).expanduser()
        if p.exists():
            return p.resolve()
    return Path.cwd().resolve()
