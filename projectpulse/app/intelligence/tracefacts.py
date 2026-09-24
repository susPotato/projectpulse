"""What a project's traceability run says, counted for the rules engine.

`context.py` compares numbers and reads nothing; the run is files on disk. This
is the one place the two meet: it opens the run through `tracelink_view` - the
same reader the Trace page and the Insight card use, so the counts cannot
disagree with what those screens show - and joins each verdict to the task it
is about by its tracker key.

**The tracker's status is read from the task, not from the run.** The run
carries the status as it was when the trace was built; the task row carries it
as of the last sync. A ticket closed since the trace should be judged as closed.

**Area is where the code is.** A PM asks "which part of the product is late",
and the tracker often cannot answer - CoWorkLocal has no component on any of
its 190 issues. The run can: every ticket is matched to candidate files, and
the leading directories of the file a verdict cites first (or of its first
candidate) are the code area that ticket lives in.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from app.intelligence.context import CLOSED_STATES, DONE_STATES

#: How many leading directories name an area. Two reads as a feature -
#: `presentation/chat`, `core/scheduling` - where one is too coarse
#: (`presentation` is half the app) and three starts naming implementation.
AREA_DEPTH = 2


def _area(path: str) -> str:
    """The leading directories, or "" for a file at the repository root.

    A root file - `config.py`, `app.py` - is shared by everything, so it names
    no part of the product. The first report built on this said "late work is
    concentrated in (repository root)", which tells a PM nothing.
    """
    parts = PurePosixPath(path.replace("\\", "/")).parts[:-1]
    return "/".join(parts[:AREA_DEPTH])


def _key_of(entity_id: str) -> str:
    """`jira:Task:2:COWORKLOCAL-41` -> `COWORKLOCAL-41`."""
    return entity_id.rsplit(":", 1)[-1].upper()


def trace_facts(project_id: str, tasks: Sequence, as_of: date) -> dict[str, Any]:
    """Counts for `build_context(trace=...)`. `{"available": False}` when there
    is no run, or a run nobody has adjudicated - silence, never a zero match."""
    from app.api import tracelink_view

    run, _ = tracelink_view.run_for_project(project_id)
    if run is None:
        return {"available": False}
    payload = tracelink_view.collect(run)
    if payload.get("project_id") != project_id:
        return {"available": False}
    rows = [r for r in payload.get("tickets") or [] if r.get("verdict")]
    if not rows:
        return {"available": False}

    by_key = {_key_of(t.entity_id): t for t in tasks}
    done_contradicted = done_unverified = open_built = 0
    overdue_areas: Counter[str] = Counter()
    for row in rows:
        task = by_key.get(str(row.get("key") or "").upper())
        if task is None:
            continue
        status = (task.status or "").casefold()
        verdict = row["verdict"].get("verdict")
        if status in DONE_STATES:
            done_contradicted += verdict == "contradicted"
            done_unverified += verdict == "unverified"
        elif status not in CLOSED_STATES:
            open_built += verdict == "corroborated"
            if task.planned_end is not None and task.planned_end < as_of:
                cited = [e.get("file") for e in row["verdict"].get("evidence") or []
                         if e.get("file")]
                areas = [_area(f) for f in [*cited, *(row.get("candidates") or [])]]
                where = next((a for a in areas if a), "")
                if where:
                    overdue_areas[where] += 1

    worst = max(overdue_areas.items(), key=lambda kv: (kv[1], kv[0]), default=None)
    return {
        "available": True,
        "done_contradicted": done_contradicted,
        "done_unverified": done_unverified,
        "open_built": open_built,
        "worst_area": worst[0] if worst else "",
        "worst_area_overdue": worst[1] if worst else 0,
    }
