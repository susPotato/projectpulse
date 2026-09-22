"""Put a project's feature grouping back from a database backup.

    python -m scripts.restore_milestones --from pulse.db.bak --project jira:Project:1:COWORKLOCAL

CoWorkLocal's 29 features - Core Platform, Cowork Chat, Knowledge & RAG -
exist only in the spreadsheet that fed the project originally; every Jira
issue has an empty `components` field, so nothing downstream of the tracker
can rebuild them. `scripts.carry_milestones` moved them onto the Jira source
before the spreadsheet rows were dropped, and a later `scripts.drop_source`
- run to clear differently-keyed rows before a rebuild - deleted them again,
because deleting a source's milestones is exactly what that script promises
to do. The grouping was recoverable only from a file copy.

So this reads the milestones and the task-to-milestone mapping out of a
backup and applies them to the live database, matching tasks **by title**
because the ids were deliberately changed in between. Nothing else is read
from the backup: not statuses, not dates, not tasks. Restoring a whole row
from a stale copy would quietly undo whatever has happened since.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import sqlite3  # noqa: E402
from datetime import date, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.domain import Milestone, Task  # noqa: E402

COLUMNS = ("id", "project_id", "name", "planned_date", "baseline_date",
           "actual_date", "status")
DATES = ("planned_date", "baseline_date", "actual_date")


def _as_date(value):
    """sqlite3 hands back the stored text; the ORM wants a date."""
    if value in (None, "") or isinstance(value, date):
        return value or None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="backup", required=True,
                        help="a SQLite copy taken before the grouping was lost")
    parser.add_argument("--project", required=True,
                        help="the source project id to restore onto")
    parser.add_argument("--backup-project", default=None,
                        help="which project's milestones to take from the "
                             "backup, when the id changed in between. "
                             "Defaults to --project. Required in practice: "
                             "every project's milestones live in one table, "
                             "and names like 'Go-Live' and 'UAT' repeat "
                             "across projects, so an unfiltered read pulls "
                             "another delivery's phases onto this chart.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.backup)
    if not path.exists():
        raise SystemExit(f"no such backup: {path}")

    old = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row

    # Every milestone the backup holds for any source, keyed by name: the
    # project id may have changed between the copy and now, and the name is
    # what a person recognises on the chart.
    source = args.backup_project or args.project
    milestones = {
        row["name"]: row
        for row in old.execute(
            "select * from milestones where project_id = ?", (source,))
        if row["name"]
    }
    # title -> milestone name, from the backup's own grouping.
    grouping: dict[str, str] = {}
    for row in old.execute(
        "select t.title as title, m.name as name from tasks t "
        "join milestones m on m.id = t.milestone_id "
        "where t.title is not null and m.project_id = ?", (source,)
    ):
        grouping[(row["title"] or "").strip()] = row["name"]
    old.close()

    print(f"backup holds {len(milestones)} milestone(s) and a grouping for "
          f"{len(grouping)} task title(s)")

    added = attached = unmatched = 0
    with session_scope() as session:
        have = {m.name for m in session.scalars(
            select(Milestone).where(Milestone.project_id == args.project)).all()}
        by_name: dict[str, str] = {}

        for name, row in milestones.items():
            if name in have:
                continue
            values = {c: row[c] for c in COLUMNS if c in row.keys()}
            values["project_id"] = args.project
            for column in DATES:
                if column in values:
                    values[column] = _as_date(values[column])
            session.add(Milestone(**values))
            by_name[name] = values["id"]
            added += 1
        session.flush()

        for milestone in session.scalars(
            select(Milestone).where(Milestone.project_id == args.project)
        ).all():
            by_name[milestone.name] = milestone.id

        for task in session.scalars(
            select(Task).where(Task.project_id == args.project)
        ).all():
            if task.milestone_id:
                continue
            name = grouping.get((task.title or "").strip())
            if name and name in by_name:
                task.milestone_id = by_name[name]
                attached += 1
            else:
                unmatched += 1

        print(f"  milestones created : {added}")
        print(f"  tasks grouped      : {attached}")
        print(f"  no title match     : {unmatched}")

        if args.dry_run:
            session.rollback()
            print("\ndry run - nothing was written")
            return 0

    print("\nrestored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
