"""Add `tasks.source_updated_at` to a database that predates it.

    python -m scripts.migrate_task_columns            # report only
    python -m scripts.migrate_task_columns --apply    # write it

`create_all()` on boot creates missing *tables*; it never adds a column to a
table that already exists. So a database seeded before this change has a `tasks`
table without the column, and every read of it raises.

That failure is **not** silent, unlike the one `scripts.migrate_programs` was
written for: `load_tasks` selects the ORM entity, so a missing column takes out
the schedule, the insight bundle and the portfolio with a database error rather
than quietly returning nothing. Loud is better here, but only if the fix is one
command - which is what this is.

Nullable with no default, so the statement is instant even on a large table and
an existing row reads as "the source has not said", which is true of every task
ingested before there was a column to say it in. Nothing back-fills it: the
value is a claim the source system makes about when a row last changed, and we
cannot make that claim retrospectively on its behalf.

Safe to run twice - it checks before it writes, and does nothing if the column
is already there.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402

from sqlalchemy import inspect, text  # noqa: E402

from app.db import session_scope  # noqa: E402

#: `(table, column, DDL type)`. A list so the next column of this kind is one
#: row here rather than another script - the pattern this file establishes is
#: meant to be reused, and a second copy of it would drift.
COLUMNS: tuple[tuple[str, str, str], ...] = (("tasks", "source_updated_at", "DATE"),)


def missing(session) -> list[tuple[str, str, str]]:
    """The columns this database does not have yet.

    A table that does not exist at all is skipped rather than reported: nothing
    to alter, and `create_all()` will build it with the column already on it.
    """
    inspector = inspect(session.get_bind())
    tables = set(inspector.get_table_names())

    absent = []
    for table, column, ddl in COLUMNS:
        if table not in tables:
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            absent.append((table, column, ddl))
    return absent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: report only)"
    )
    args = parser.parse_args()

    with session_scope() as session:
        absent = missing(session)

        if not absent:
            print("nothing to migrate: every task column is already present")
            return

        for table, column, ddl in absent:
            print(f"{table} is missing `{column}` ({ddl})")

        if not args.apply:
            print("\nreport only - re-run with --apply to write")
            return

        for table, column, ddl in absent:
            session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            print(f"  added {table}.{column}")


if __name__ == "__main__":
    main()
