"""Build a dashboard from what a project actually has.

The third way to populate a board, beside a blank canvas and a fixed template.
It exists because a template is a guess about the data: `project_delivery_review`
assumes a baseline, a dependency graph and a worklog, and a project ingested
from a single Jira export has none of the three. Applied there it lays out
thirteen tiles of which most render an empty state, and **a dashboard that is
sparse because the inputs are sparse looks exactly like one that is sparse
because the project is fine.** Those are opposite situations.

So the tiles are selected rather than listed: every `TileSpec` declares the
signals it needs (`catalogue.Signal`), this asks the database which of them the
project has, and the board is the tiles whose needs are met.

**The probe is counting queries, not the pipeline.** `analyze_project` would
answer the same question and also build a graph, run the rule engine and call a
model - none of which a person pressing a layout button is waiting for. Each
signal here is one `SELECT COUNT(*) ... LIMIT 1`-shaped read, so the whole thing
is a handful of index lookups.

What it deliberately does **not** do is hide a tile because its numbers are
currently zero. "No risks logged yet" is a true and useful statement about a
project that has a register; "this source cannot express a dependency" is a
different statement, and only the second is a reason to leave a tile off. The
requirement is about the *shape* of what was ingested, never about whether the
news is good.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from app.dashboard.catalogue import Signal, TileSpec, for_scope


def project_signals(session, project_ids: Sequence[str]) -> set[Signal]:
    """Which signals this project's ingested data actually carries."""
    from app.models.domain import Dependency, QaItem, Risk, StateChange, Task

    ids = list(project_ids)
    if not ids:
        return set()

    def count(model, *where) -> int:
        return (
            session.scalar(
                select(func.count()).select_from(model).where(model.project_id.in_(ids), *where)
            )
            or 0
        )

    found: set[Signal] = set()
    if count(Task):
        found.add("tasks")
    if count(Task, Task.baseline_end.is_not(None)):
        found.add("baseline")
    if count(Dependency):
        found.add("edges")
    if count(QaItem):
        found.add("effort")
    #: More than one *observation*, not more than one row: a first scan writes
    #: no changes at all, so any change at all means the differ has had two
    #: looks. Reached through the entities rather than directly - a
    #: `state_changes` row is keyed by the entity it describes and has no
    #: project column of its own.
    entity_ids = select(Task.id).where(Task.project_id.in_(ids)).union(
        select(QaItem.id).where(QaItem.project_id.in_(ids))
    )
    changes = (
        session.scalar(
            select(func.count())
            .select_from(StateChange)
            .where(StateChange.entity_id.in_(entity_ids))
        )
        or 0
    )
    if changes:
        found.add("history")

    if count(Risk):
        found.add("risks")

    #: The one probe that is not a database read, because the one bundle that
    #: is not built from the database. A `tracelink` run is a directory on
    #: disk that names the project it is about, so this is a listing rather
    #: than a `SELECT` - comparable in cost, and the alternative is offering
    #: a code-versus-documents tile to every project that has neither.
    from app.api import tracelink_view

    if any(tracelink_view.run_for_project(pid)[0] for pid in ids):
        found.add("traceability")

    return found


def program_signals(session, program_id: str) -> set[Signal]:
    """The same question one level up.

    `siblings` rather than `tasks`, because the program tiles that matter are
    the cross-project ones and a program with a single project has nothing to
    compare. Such a program is a legitimate state - it is what a portfolio looks
    like on day one - and its board should say so with the tiles that still
    work rather than with eight empty ones.
    """
    from app.scope import projects_in

    entries = projects_in(program_id)
    source_ids = [sid for entry in entries for sid in entry.source_ids]

    found: set[Signal] = set()
    if len(entries) > 1:
        found.add("siblings")
    if entries:
        found.add("program")
    found |= project_signals(session, source_ids)
    return found


def fitted_tiles(scope: str, available: set[Signal]) -> list[TileSpec]:
    """The tiles this data can fill, in catalogue order.

    Catalogue order rather than sorted by anything: it is already the order a
    person reads a project in - what is wrong, why, what it costs - and
    reordering by "how much data backs it" would put the most-populated tile
    first rather than the most important one.
    """
    return [tile for tile in for_scope(scope) if set(tile.requires) <= available]


def missing_for(scope: str, available: set[Signal]) -> dict[Signal, list[str]]:
    """Which tiles each absent signal is holding back.

    Returned so the caller can say what a board would gain, rather than only
    what it has. A person who uploads a second export tomorrow should be able to
    find out that it unlocks four tiles, without having to press the button
    again to discover it.
    """
    held: dict[Signal, list[str]] = {}
    for tile in for_scope(scope):
        for signal in tile.requires:
            if signal not in available:
                held.setdefault(signal, []).append(tile.label)
    return held
