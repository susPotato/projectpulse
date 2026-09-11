"""Re-key Excel-derived rows onto project-namespaced domain ids.

    python -m scripts.migrate_ids            # report what would change
    python -m scripts.migrate_ids --apply    # change it

Why this exists. `domain_id()` used to namespace an Excel Task by
connection + row key, every Excel project shares one connection_id, and a row
key is unique only within its own sheet - so two projects that both numbered
their tasks `1, 2, 3` collided into one row and the second import silently
took the first's tasks. Task, QaItem, StateChange and Dependency ids now carry
the project, as Milestone always did.

A database written before that change keeps working: nothing reads an id's
structure except the label, which takes the last component either way. The
damage arrives on the **next sync** of a sheet that has changed - the convertor
writes new-style rows beside the old ones and the project has every task twice,
which is not only wrong on screen but changes the findings, because the
schedule graph is then computed over a doubled and partly orphaned set of rows.

Two ways out. `scripts.replay` rebuilds from the sheets, which is right for a
demo database and wrong for a deployed one: the risk register, the dashboards
people arranged, the custom tiles and the narration cache are the only data
here with no source system behind them, and a rebuild destroys all of it. This
is the other way - rename in place, keep everything.

The new id is built by calling `domain_id` rather than by string surgery, so
this script cannot disagree with the convertor about the format. The missing
component is read from each row's own `project_id`, so the mapping is derived
from the data and never guessed.

Safe to run twice: a row already carrying its project is skipped. Safe to run
late: where a sync has already written the new-style row, the old one is
deleted rather than renamed onto a duplicate.
"""

from __future__ import annotations

import argparse
from collections import Counter

import scripts._bootstrap as _bootstrap  # noqa: F401  - must precede app imports

from sqlalchemy import delete, select, update

from app.db import session_scope
from app.ids import domain_id
from app.models.domain import Dependency, QaItem, StateChange, Task

#: Only the Excel path changed. A Jira task is keyed by its issue id, which is
#: unique across that vendor's projects, so those rows were never touched and
#: must not be touched now.
SOURCE = "excel"

#: `StateChange.entity_type` -> the entity name in the id.
ENTITY_NAMES = {"task": "Task", "qa_item": "QaItem"}


def _parts(value: str) -> list[str]:
    return value.split(":")


def _already_namespaced(value: str) -> bool:
    """Whether an id already carries a project component.

    An encoded project id is the tell: `domain_id` escapes the colons inside a
    component, so `excel%3AProject%3A1%3AHRMS` appears as one part and cannot
    be confused with a row key a person typed.
    """
    parts = _parts(value)
    return len(parts) >= 5 and "%3AProject%3A" in parts[3]


def _is_excel(value: str) -> bool:
    return value.startswith(f"{SOURCE}:")


def _renamed(old: str, project_id: str, *, entity: str | None = None) -> str:
    """The same id with the project inserted, built by `domain_id` itself."""
    source, _entity, connection, *pks = _parts(old)
    return domain_id(source, entity or _entity, int(connection), project_id, *pks)


def plan(session) -> dict[str, list[tuple]]:
    """What would change, as (table, old, new) triples. Reads only."""
    work: dict[str, list[tuple]] = {}

    # A task's project is on the task. Everything else's project is found
    # through the task or carried on the row itself.
    project_of: dict[str, str] = {}

    for model, name in ((Task, "tasks"), (QaItem, "qa_items")):
        rows = []
        for entity_id, project_id in session.execute(
            select(model.id, model.project_id)
        ):
            if not _is_excel(entity_id) or _already_namespaced(entity_id):
                if _is_excel(entity_id):
                    project_of[entity_id] = project_id
                continue
            project_of[entity_id] = project_id
            rows.append((entity_id, _renamed(entity_id, project_id)))
        if rows:
            work[name] = rows

    changes = []
    for change_id, entity_type, entity_id in session.execute(
        select(StateChange.id, StateChange.entity_type, StateChange.entity_id)
    ):
        if not _is_excel(change_id) or _already_namespaced(change_id):
            continue
        project_id = project_of.get(entity_id)
        if project_id is None:
            # The row it describes is gone. Left alone and reported rather
            # than re-keyed onto a guess - a state change is evidence.
            continue
        entity = ENTITY_NAMES.get(entity_type, "Task")
        changes.append(
            (
                change_id,
                _renamed(change_id, project_id),
                entity_id,
                _renamed(entity_id, project_id, entity=entity),
            )
        )
    if changes:
        work["state_changes"] = changes

    edges = []
    for edge_id, project_id, predecessor, successor in session.execute(
        select(
            Dependency.id,
            Dependency.project_id,
            Dependency.predecessor_id,
            Dependency.successor_id,
        )
    ):
        if not _is_excel(edge_id) or _already_namespaced(edge_id):
            continue
        entity = "Task"
        edges.append(
            (
                edge_id,
                _renamed(edge_id, project_id),
                predecessor,
                _renamed(predecessor, project_id, entity=entity),
                successor,
                _renamed(successor, project_id, entity=entity),
            )
        )
    if edges:
        work["dependencies"] = edges

    return work


def _existing(session, model) -> set[str]:
    return set(session.scalars(select(model.id)))


def apply(session, work: dict[str, list[tuple]]) -> Counter:
    """Rename what can be renamed; delete what a later sync already wrote."""
    counts: Counter = Counter()

    # Order matters only for readability - these are string columns, not
    # enforced references between them - but do the entities first so the
    # duplicate check below sees the final state.
    for model, name in ((Task, "tasks"), (QaItem, "qa_items")):
        rows = work.get(name, [])
        if not rows:
            continue
        taken = _existing(session, model)
        for old, new in rows:
            if new in taken:
                # A sync after the change already created this row. The old
                # one is the duplicate, and it is the one to remove.
                session.execute(delete(model).where(model.id == old))
                counts[f"{name} deleted (duplicate)"] += 1
            else:
                session.execute(
                    update(model).where(model.id == old).values(id=new)
                )
                taken.add(new)
                counts[f"{name} renamed"] += 1

    # No duplicate check here: a state-change id carries the scan timestamp,
    # so an old row and a new one can never land on the same id.
    for old_id, new_id, _old_entity, new_entity in work.get("state_changes", []):
        session.execute(
            update(StateChange)
            .where(StateChange.id == old_id)
            .values(id=new_id, entity_id=new_entity)
        )
        counts["state_changes re-pointed"] += 1

    taken = _existing(session, Dependency)
    for old_id, new_id, _op, new_pred, _os, new_succ in work.get("dependencies", []):
        if new_id in taken:
            session.execute(delete(Dependency).where(Dependency.id == old_id))
            counts["dependencies deleted (duplicate)"] += 1
            continue
        session.execute(
            update(Dependency)
            .where(Dependency.id == old_id)
            .values(id=new_id, predecessor_id=new_pred, successor_id=new_succ)
        )
        taken.add(new_id)
        counts["dependencies re-pointed"] += 1

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes; without it, only report them",
    )
    args = parser.parse_args(argv)

    with session_scope() as session:
        work = plan(session)

        if not work:
            print("nothing to migrate - every Excel id already carries its project")
            return 0

        print("rows to re-key:")
        for table, rows in work.items():
            print(f"  {table:16} {len(rows)}")
        sample = next(iter(work.values()))[0]
        print(f"\n  e.g. {sample[0]}\n    -> {sample[1]}")

        if not args.apply:
            print("\nnothing written. Re-run with --apply to make these changes.")
            return 0

        counts = apply(session, work)

    print("\napplied:")
    for label, count in sorted(counts.items()):
        print(f"  {label:34} {count}")
    print("\nRe-check the app before trusting it: the task count per project is")
    print("the number this was about.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
