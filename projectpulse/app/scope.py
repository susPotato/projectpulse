"""Which source ids are one delivery project.

Invariant 7: Jira and Excel each create their own `projects` row for the same
piece of delivery, so analysing either alone throws away every cross-source
claim. `analyze_project(..., also=[...])` re-points them at one canonical id -
and until now the pairing was a literal `also = ["jira:Project:1:HRMS"]` sitting
in the API route, which meant the portfolio view had no way to know that two
rows were one project and would have listed HRMS twice.

This is that pairing, in one place. The demo project is a built-in seed;
anything registered since - typically by uploading a document for a project
nobody has seen before, via Settings > Sources - is layered on top of it and
persisted the same way narration settings are: a small JSON file in
`PULSE_STATE_DIR`, gitignored, plaintext, read fresh on every call rather than
cached, because a second worker process must see what the first one wrote.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
FILENAME = "projects.json"


@dataclass(frozen=True)
class DeliveryProject:
    """One project as a delivery manager thinks of it, whatever fed it."""

    #: The id every other source id is re-pointed at.
    canonical_id: str
    name: str
    #: The same delivery project as other source systems call it.
    also: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return (self.canonical_id, *self.also)


#: The demo portfolio. HRMS is one project, two sources - the case the
#: precision model exists for, so it is the right seed to ship. SAIN and
#: Example Project (`scripts/gen_portfolio_data.py`) are single-source and
#: exist so the portfolio, and the Program-level rollups built on it, have
#: more than one row - the names match the PM's own cross-project mockups
#: (`Layout_Program`'s Risk/Schedule screens list "Example Project, SAIN,
#: HRMS" together). A registered project of the same canonical_id overrides
#: its entry here rather than duplicating it, so re-uploading the demo's own
#: files is a no-op.
_SEED: tuple[DeliveryProject, ...] = (
    DeliveryProject(
        canonical_id="excel:Project:1:HRMS",
        name="HRMS Platform",
        also=("jira:Project:1:HRMS",),
    ),
    DeliveryProject(canonical_id="excel:Project:1:SAIN", name="SAIN"),
    DeliveryProject(
        canonical_id="excel:Project:1:EXPROJ", name="Example Project"
    ),
)


def _state_path() -> Path:
    from app.config import settings

    return Path(settings.state_dir) / FILENAME


def _load_registered() -> list[DeliveryProject]:
    path = _state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable project registry at %s: %s", path, exc)
        return []

    if not isinstance(raw, list):
        return []

    out: list[DeliveryProject] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        canonical_id = str(item.get("canonical_id") or "")
        name = str(item.get("name") or "")
        if not canonical_id or not name:
            continue
        also = tuple(str(a) for a in item.get("also") or ())
        out.append(DeliveryProject(canonical_id=canonical_id, name=name, also=also))
    return out


def all_projects() -> tuple[DeliveryProject, ...]:
    """Every delivery project: the built-in demo, plus anything registered
    since. Read fresh each call - see the module docstring for why."""
    merged: dict[str, DeliveryProject] = {p.canonical_id: p for p in _SEED}
    for entry in _load_registered():
        merged[entry.canonical_id] = entry
    return tuple(merged.values())


def register(canonical_id: str, name: str, also: tuple[str, ...] = ()) -> DeliveryProject:
    """Add a project, or update its pairing/name if the id already exists.

    Called when a document is uploaded for a project id nobody has ingested
    before - see `POST /api/sources/upload`. Persisted immediately so the
    portfolio picks it up (as `no_data`, correctly, until something is
    actually ingested for it) without a restart.
    """
    entry = DeliveryProject(canonical_id=canonical_id, name=name, also=tuple(also))
    with _LOCK:
        registered = {p.canonical_id: p for p in _load_registered()}
        registered[canonical_id] = entry
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    {"canonical_id": p.canonical_id, "name": p.name, "also": list(p.also)}
                    for p in registered.values()
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
    return entry


def find(canonical_id: str) -> DeliveryProject | None:
    return next((p for p in all_projects() if p.canonical_id == canonical_id), None)


def also_for(canonical_id: str) -> list[str]:
    """The other source ids for this project, for `analyze_project(also=...)`.

    An unknown id gets an empty list rather than an error: a caller asking
    about a project nobody has paired is asking a legitimate question, and the
    answer is "just this one source".
    """
    project = find(canonical_id)
    return list(project.also) if project else []


def __getattr__(name: str):
    """`from app.scope import PORTFOLIO` still works, read fresh each time.

    A handful of call sites predate the registry and import the name directly
    rather than calling `all_projects()`; PEP 562 makes that import re-resolve
    on every access instead of freezing the seed at whatever moment this
    module first loaded.
    """
    if name == "PORTFOLIO":
        return all_projects()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
