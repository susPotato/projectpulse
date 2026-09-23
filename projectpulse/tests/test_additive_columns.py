"""The self-healing column list, and what it is worth.

`create_all()` creates missing *tables* and never missing *columns* - that is
SQLAlchemy's contract. `_ADDITIVE_COLUMNS` is this project's answer: a column
added to a model after the table exists somewhere deployed gets an entry, and
boot repairs the database rather than a human remembering a migration.

It only works if the entries are actually added. `tasks.source_updated_at` was
added to the model by 94b3b4e with no entry here, and the result is that every
database predating that commit answers `column tasks.source_updated_at does
not exist` to the first request that reads a task - which is
`/api/portfolio`, which is the Fly health check, so the machine leaves the
pool and the whole app is down rather than one feature.

These tests cannot detect the *next* forgotten entry - nothing can tell a
newly added column from an original one by looking at the model alone. What
they do is keep the mechanism honest: every entry names a real column, every
entry is genuinely additive, and the repair actually repairs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app.db import _ADDITIVE_COLUMNS, _ensure_additive_columns, engine
from app.models.base import Base


def _model_column(table_name: str, column_name: str):
    table = Base.metadata.tables.get(table_name)
    if table is None:
        return None
    return table.columns.get(column_name)


@pytest.mark.parametrize("table,column,ddl", _ADDITIVE_COLUMNS)
def test_every_entry_names_a_column_the_model_still_has(table, column, ddl):
    """A stale entry is dead DDL run against every database on every boot.

    It survives a rename silently, because `ALTER TABLE ... ADD COLUMN` on a
    name nothing reads succeeds.
    """
    assert Base.metadata.tables.get(table) is not None, (
        f"{table!r} is in _ADDITIVE_COLUMNS but is not a model table any more"
    )
    assert _model_column(table, column) is not None, (
        f"{table}.{column} is in _ADDITIVE_COLUMNS but the model no longer "
        f"declares it. Remove the entry, or restore the column."
    )


@pytest.mark.parametrize("table,column,ddl", _ADDITIVE_COLUMNS)
def test_every_entry_is_genuinely_additive(table, column, ddl):
    """Nullable, and no server default.

    The list's own docstring requires it: running an entry against a database
    that already holds rows must be a no-op rather than a rewrite. A NOT NULL
    column, or one with a server default, needs a real migration script.
    """
    col = _model_column(table, column)
    assert col is not None
    assert col.nullable, (
        f"{table}.{column} is NOT NULL, so adding it to a table that already "
        f"has rows would fail or rewrite them. This needs a migration script, "
        f"not an _ADDITIVE_COLUMNS entry."
    )
    assert col.server_default is None, (
        f"{table}.{column} carries a server default, which rewrites every "
        f"existing row when the column is added."
    )
    assert "NOT NULL" not in ddl.upper()
    assert "DEFAULT" not in ddl.upper()


def test_the_repair_actually_adds_a_missing_column():
    """Drop one, boot, and it comes back.

    The mechanism's whole value is this path, and nothing else exercises it -
    every other test runs against a schema `create_all()` just made complete.
    """
    table, column, _ = ("tasks", "source_updated_at", "DATE")
    assert (table, column) in {(t, c) for t, c, _d in _ADDITIVE_COLUMNS}

    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        pytest.skip("no tasks table on this database")

    # No `IF EXISTS`: SQLite has `DROP COLUMN` (3.35+) but not that clause,
    # and this suite runs on SQLite while the deployment is Postgres. The
    # column is known to be there - `create_all()` just ran.
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    except Exception as exc:  # noqa: BLE001 - dialect capability, not a defect
        pytest.skip(f"this database cannot drop a column: {exc}")

    assert column not in {c["name"] for c in inspect(engine).get_columns(table)}

    # A pooled connection caches the schema it was prepared against, so the
    # one `_ensure_additive_columns` borrows can still believe the column is
    # there and refuse to add it. A real boot never sees this - the process is
    # new and so is every connection - so the pool is dropped here rather than
    # the mechanism being changed to suit a test.
    engine.dispose()

    _ensure_additive_columns()

    assert column in {c["name"] for c in inspect(engine).get_columns(table)}, (
        "_ensure_additive_columns did not restore a column listed in "
        "_ADDITIVE_COLUMNS - the self-healing boot is not self-healing."
    )
