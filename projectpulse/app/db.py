"""Engine, session, and schema creation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base

# Importing the model modules registers their tables on Base.metadata. Without
# these, create_all() would produce an empty schema.
from app.models import domain, raw, sync, tool  # noqa: F401

engine = create_engine(settings.database_url, echo=settings.echo_sql, future=True)
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


def create_all() -> None:
    Base.metadata.create_all(engine)


def drop_all() -> None:
    Base.metadata.drop_all(engine)
