"""Engine, session, and schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base

# Importing the model modules registers their tables on Base.metadata. Without
# these, create_all() would produce an empty schema.
from app.models import (  # noqa: F401
    dashboard,
    domain,
    jira,
    llm,
    narration,
    onedrive,
    raw,
    sync,
    tool,
    uploads,
)

def _connect_args() -> dict:
    """Fail fast when the database is not there.

    psycopg's default connect timeout is effectively minutes, and the failure
    mode that produces is far worse than an error: `python -m scripts.demo` with
    no container running appears to *work*, and only the request hangs. A test
    run did the same for 8m44s before erroring.

    Five seconds is longer than any healthy local or managed connection needs and
    short enough that a missing container reads as a missing container.
    """
    if settings.database_url.startswith("postgresql"):
        return {"connect_timeout": 5}
    return {}


engine = create_engine(
    settings.database_url,
    echo=settings.echo_sql,
    future=True,
    # Managed Postgres closes idle connections and moves the primary during a
    # failover, either of which leaves a dead socket in the pool that fails on
    # first use. Pre-ping costs one round trip and turns that into a transparent
    # reconnect. Harmless against local Postgres and SQLite.
    pool_pre_ping=True,
    connect_args=_connect_args(),
)


def check_connection() -> str | None:
    """None when the database is reachable, else a message a human can act on.

    Called by the entry points rather than left to blow up mid-request: the whole
    difference between "Docker isn't running" and an opaque 500 on a page a judge
    is looking at.
    """
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return None
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        hint = ""
        if settings.database_url.startswith("postgresql"):
            hint = (
                " Start it with `docker compose up -d`, or run without Docker: "
                "set DATABASE_URL=sqlite:///pulse.db"
            )
        return f"cannot reach {settings.database_url.split('@')[-1]}: {exc}.{hint}"
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transaction boundary. Commits on success, rolls back on any exception.

    Sync watermarks live in the same transaction as the data they describe, so a
    failure cannot leave the watermark advanced past rows that were never written.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


#: A column added to a model after its table already exists somewhere
#: deployed. `create_all()` only creates *missing* tables - that is
#: SQLAlchemy's contract, not a bug - so it never picks these up on its own,
#: and the alternative is a manual migration step someone has to remember to
#: run against production, which is exactly the failure this project has
#: already hit twice (CLAUDE.md's deploy history). Every entry here must
#: stay genuinely additive - nullable, no default that rewrites existing
#: rows - so running this against a database that already has the column is
#: a no-op rather than a hazard. A rename or a NOT NULL still needs a real
#: migration script (see `scripts/migrate_ids.py`), not an entry here.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("custom_tiles", "live_source_json", "TEXT"),
    # Which program a registered project belongs to. `scripts/migrate_programs.py`
    # also adds this, because that script has to work on a database nobody has
    # booted the new code against yet - but the entry belongs here too, so a
    # deployment that skips the migration still self-heals rather than losing
    # every browser-imported project: `scope._rows()` degrades an unreadable
    # registry to "there are none" by design, which makes the failure silent.
    ("registered_projects", "program_id", "VARCHAR(255)"),
    # The issue body, and the two columns that let a model-proposed risk be
    # told apart from a typed one and checked against the rows it was read
    # from. All three nullable with no default, so a database that predates
    # them self-heals on boot - which is what keeps this off the deploy
    # checklist entirely. `/api/portfolio` is `fly.toml`'s health check and
    # SELECTs tasks, so a column added any other way would 503 the whole app
    # between the deploy and the migration.
    ("tasks", "description", "TEXT"),
    ("risks", "origin", "VARCHAR(20)"),
    ("risks", "cited_task_ids", "TEXT"),
)


def _ensure_additive_columns() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl_type in _ADDITIVE_COLUMNS:
            if table not in existing_tables:
                continue  # create_all() just made it, column and all
            columns = {c["name"] for c in inspector.get_columns(table)}
            if column in columns:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def create_all() -> None:
    Base.metadata.create_all(engine)
    _ensure_additive_columns()


def drop_all() -> None:
    Base.metadata.drop_all(engine)
