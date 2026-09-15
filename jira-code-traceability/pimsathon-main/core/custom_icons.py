"""User-added custom icons for agents / flows.

Built-in glyphs live in ``ui/icons.py`` (``_PATHS``). This module lets a user
add their OWN icons (SVG files) under ``~/.cowork_local/icons/<slug>.svg`` so
they can be used by name anywhere an icon name is accepted (Co4E step/agent
``icon`` field, etc.). ``ui/icons.icon()`` resolves an unknown name against this
store before falling back. Qt-free so it's unit-testable.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..config import CONFIG_DIR

ICONS_DIR = CONFIG_DIR / "icons"
_MAX_BYTES = 200_000


def icons_dir() -> Path:
    return ICONS_DIR


def slugify(name: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (name or "").strip().lower())
    return "-".join(filter(None, s.split("-"))) or "icon"


def list_custom(directory: Optional[Path] = None) -> List[str]:
    directory = directory or ICONS_DIR
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.svg"))


def get_svg(name: str, directory: Optional[Path] = None) -> Optional[str]:
    """The raw SVG text for a custom icon slug, or None if there isn't one."""
    directory = directory or ICONS_DIR
    if not name:
        return None
    path = directory / f"{slugify(name)}.svg"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")[:_MAX_BYTES]
    except OSError:
        return None


def add_svg(name: str, svg_text: str, directory: Optional[Path] = None) -> str:
    """Save raw SVG under a slug; returns the slug. Raises ValueError if the
    text isn't SVG."""
    if "<svg" not in (svg_text or "").lower():
        raise ValueError("Not an SVG (no <svg> tag found).")
    directory = directory or ICONS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    (directory / f"{slug}.svg").write_text(svg_text[:_MAX_BYTES], encoding="utf-8")
    return slug


def add_from_file(path, name: str = "", directory: Optional[Path] = None) -> str:
    """Import an .svg file. ``name`` defaults to the file's own stem."""
    p = Path(path)
    if p.suffix.lower() != ".svg":
        raise ValueError("Only .svg icon files are supported.")
    svg = p.read_text(encoding="utf-8", errors="replace")
    return add_svg(name or p.stem, svg, directory)


def delete_custom(name: str, directory: Optional[Path] = None) -> None:
    directory = directory or ICONS_DIR
    path = directory / f"{slugify(name)}.svg"
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
