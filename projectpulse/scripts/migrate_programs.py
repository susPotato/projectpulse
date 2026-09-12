"""Merge the duplicate program rows onto one source-neutral id.

    python -m scripts.migrate_programs            # report what would change
    python -m scripts.migrate_programs --apply    # change it

Why this exists. Both convertors built their program id as
`domain_id(SOURCE, "Program", connection_id, "DEFAULT")`, so the source system a
document happened to arrive from ended up *inside the program's identity*. One
program therefore became two rows:

    excel:Program:1:DEFAULT   'Digital Transformation 2026'
    jira:Program:1:DEFAULT    'Digital Transformation 2026'

and the two `projects` rows of a single delivery (invariant 7 - HRMS is tracked
in both a spreadsheet and Jira) hung off different ones. Programs are now keyed
`program:Program:0:<KEY>`, membership is declared in `app/scope.py`, and
`app/ingest/programs.py` is the only path a collector takes to attach a project
to one.

A database written before that change keeps *reading* fine, which is exactly
what makes it worth migrating deliberately rather than hoping: the Programs list
hid the duplicate by dropping an empty program that shared a name with a
non-empty one, so nothing looked wrong, while `/api/programs/jira:Program:1:DEFAULT`
answered 200 with zero projects and the rollup for the real program silently
omitted whatever was filed under the other. The name-equality suppression is
gone as of this change, so an unmigrated database will now *show* its duplicate
on the Programs list - which is honest, and is why this should run with the
deploy rather than after somebody notices.

Two ways out, and this is the gentle one. `scripts.replay` rebuilds from the
sheets, which is right for a demo database and wrong for a deployed one: the risk
register, the dashboards people arranged, the custom tiles and the narration
cache are the only data here with no source system behind them, and a rebuild
destroys all of it. This renames and re-points in place, keeping everything.

What it does, in order:

1. Groups existing `programs` rows by name. One group is one program.
2. Picks the surviving id per group - the new `program:Program:0:<KEY>` id,
   derived from the old id's own trailing key so the mapping comes from the data
   rather than from a guess.
3. Re-points every `projects.program_id` at the survivor.
4. Re-points dashboards scoped to an old program id, so an arranged Program
   canvas is not orphaned by the rename.
5. Deletes the now-empty duplicates.

Safe to run twice: a program already on the new id is skipped, and step 5 only
deletes a row nothing points at. Safe to run late: where the new id already
exists, the old row's projects are moved onto it rather than renamed into a
primary-key collision.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import scripts._bootstrap as _bootstrap  # noqa: F401  - must precede app imports

from sqlalchemy import delete, select, update

from app.db import session_scope
from app.ids import parse_domain_id
from app.models.domain import Program, Project
from app.scope import PROGRAM_SOURCE, program_domain_id


def _target_id(old_id: str) -> str | None:
    """The source-neutral id this program should live at, or `None` if it already does.

    The key is read from the old id's last component, so `excel:Program:1:DEFAULT`
    and `jira:Program:1:DEFAULT` both map onto `program:Program:0:DEFAULT` without
    this script needing to know anything about either collector.
    """
    try:
        source, _entity, _connection, pks = parse_domain_id(old_id)
    except ValueError:
        # Not a domain id at all - a hand-inserted row. Leave it alone rather
        # than inventing an id for something we do not understand.
        return None
    if source == PROGRAM_SOURCE:
        return None
    return program_domain_id(pks[-1])


def plan(session) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Work out the moves without making any.

    Returns `(program_id -> surviving id, surviving id -> name, collisions)`.
    """
    programs = session.scalars(select(Program)).all()

    #: Group by name: two rows with the same name are the same program, which is
    #: the one inference this script makes. It is safe *here* in a way it was not
    #: safe in the Programs list, because the duplicates being merged were
    #: created by one hardcoded string in two convertors - so identical names are
    #: not a heuristic about client data, they are the signature of the bug. Any
    #: group that does not collapse to a single target is reported, not guessed.
    by_name: dict[str, list[Program]] = defaultdict(list)
    for program in programs:
        by_name[program.name].append(program)

    moves: dict[str, str] = {}
    names: dict[str, str] = {}
    collisions: list[str] = []

    for name, group in by_name.items():
        targets = {_target_id(p.id) or p.id for p in group}
        if len(targets) > 1:
            # Same name, different trailing keys - genuinely different programs
            # that happen to share a name, or a key this script cannot read.
            # Report and leave them, because merging them would lose one.
            collisions.append(
                f"{name!r}: {sorted(p.id for p in group)} -> {sorted(targets)}"
            )
            continue
        target = targets.pop()
        names[target] = name
        for program in group:
            if program.id != target:
                moves[program.id] = target

    return moves, names, collisions


def _add_registered_program_column(session, *, apply: bool) -> bool:
    """Add `registered_projects.program_id` if the table predates it.

    `create_all()` on boot creates missing *tables*; it does not add a column to
    a table that already exists. Without this the column is absent on any
    database seeded before this change, `scope._rows()` raises on the SELECT -
    and because that read degrades to "there are no registered projects" by
    design, the failure is *silent*: every project somebody imported through the
    browser disappears from the picker and the portfolio, while its ingested rows
    sit on in the database. Exactly the orphaning that putting the registry in
    Postgres was meant to end, so it is worth the ALTER.

    Nullable with no default, so the statement is instant and an existing row
    reads as "no program stated", which is true of every project registered
    before there was a column to say otherwise.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(session.get_bind())
    if "registered_projects" not in inspector.get_table_names():
        # Nothing to alter; `create_all()` will build it with the column.
        return False
    columns = {c["name"] for c in inspector.get_columns("registered_projects")}
    if "program_id" in columns:
        return False

    print("registered_projects is missing `program_id`")
    if not apply:
        return True
    session.execute(
        text("ALTER TABLE registered_projects ADD COLUMN program_id VARCHAR(255)")
    )
    print("  added registered_projects.program_id")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: report only)"
    )
    args = parser.parse_args()

    with session_scope() as session:
        needs_column = _add_registered_program_column(session, apply=args.apply)
        moves, names, collisions = plan(session)

        if collisions:
            print("NOT merged - same name, different program keys:")
            for line in collisions:
                print(f"  {line}")

        if not moves:
            if needs_column and not args.apply:
                print("\nreport only - re-run with --apply to write")
            else:
                print("nothing to migrate: every program is already source-neutral")
            _report_unassigned(session)
            return

        print(f"{len(moves)} program row(s) to merge:")
        for old, new in sorted(moves.items()):
            holders = session.scalars(
                select(Project.id).where(Project.program_id == old)
            ).all()
            print(f"  {old}")
            print(f"    -> {new}  ({names.get(new, '?')})")
            for project_id in sorted(holders):
                print(f"       re-point project {project_id}")

        if not args.apply:
            print("\nreport only - re-run with --apply to write")
            return

        created = 0
        for old, new in sorted(moves.items()):
            old_row = session.get(Program, old)
            if old_row is None:
                continue
            # Create the survivor first, carrying the old row's own attributes,
            # so nothing is lost in the rename and the foreign key always has a
            # target during the re-point below.
            if session.get(Program, new) is None:
                session.add(
                    Program(
                        id=new,
                        name=old_row.name,
                        owner=old_row.owner,
                        status=old_row.status,
                        start_date=old_row.start_date,
                        end_date=old_row.end_date,
                        description=old_row.description,
                    )
                )
                session.flush()
                created += 1

            session.execute(
                update(Project).where(Project.program_id == old).values(program_id=new)
            )
            _move_dashboards(session, old, new)
            session.execute(delete(Program).where(Program.id == old))

        print(
            f"\napplied: {created} program row(s) created, "
            f"{len(moves)} duplicate(s) removed"
        )
        _report_unassigned(session)


def _move_dashboards(session, old: str, new: str) -> None:
    """Re-point a Program-scoped dashboard at the surviving id.

    A layout somebody arranged is data with no source system behind it - the
    same class as the risk register - so leaving it pointed at a deleted program
    id would silently empty a canvas the migration was supposed to be invisible
    to. Tolerant of the table being absent: a database from before dashboards
    existed still has programs worth merging.
    """
    try:
        from app.models.dashboard import Dashboard
    except ImportError:  # pragma: no cover - defensive
        return

    try:
        moved = session.execute(
            update(Dashboard)
            .where(Dashboard.scope_type == "program", Dashboard.scope_id == old)
            .values(scope_id=new)
        ).rowcount
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        print(f"  warning: could not re-point dashboards for {old}: {exc}")
        return
    if moved:
        print(f"  re-pointed {moved} dashboard(s) from {old}")


def _report_unassigned(session) -> None:
    """Name any project left in no program.

    Not an error - a project registered by upload before anybody chose its
    program genuinely belongs to none, and the portfolio shows it. Reported
    because "belongs to no program" and "belongs to a program that was deleted"
    look identical on screen, and only one of them is intended.
    """
    orphans = session.scalars(
        select(Project.id).where(Project.program_id.is_(None))
    ).all()
    if orphans:
        print(f"\n{len(orphans)} project(s) in no program (not an error):")
        for project_id in sorted(orphans):
            print(f"  {project_id}")


if __name__ == "__main__":
    main()
