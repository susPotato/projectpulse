"""One sync path, two triggers.

The two-hourly poll and the PM pressing "Update now" call this same function and
differ only in the ``trigger`` column. Two code paths would drift, and drift is
exactly what breaks the idempotency the design depends on.

Three properties this module is responsible for:

* **Mutual exclusion.** A manual run started while the scheduled one is mid-flight
  must not become a second writer against the same rows. It joins instead.
* **Watermarks that only advance on success.** An exception leaves the watermark
  where it was, so the next run re-processes the same window. At-least-once plus
  idempotent upserts is the whole safety argument.
* **A visible record.** Every run lands in ``sync_runs`` with its counts, including
  how many rows it refused.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.models.sync import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    SyncRun,
    SyncState,
)

log = logging.getLogger(__name__)

VALID_TRIGGERS = {TRIGGER_SCHEDULED, TRIGGER_MANUAL}


@dataclass
class SyncOutcome:
    run_id: int | None
    source: str
    status: str
    rows_ok: int = 0
    rows_rejected: int = 0
    changes_emitted: int = 0
    joined_existing: bool = False
    notes: list[str] = field(default_factory=list)
    error: str | None = None


#: source name -> callable(session, *, connection_id, now, sync_run_id) -> SyncOutcome-ish
SOURCE_REGISTRY: dict[str, Callable] = {}


def register_source(name: str):
    def decorator(fn: Callable) -> Callable:
        SOURCE_REGISTRY[name] = fn
        return fn

    return decorator


def _lock_key(source: str) -> int:
    """A stable 63-bit key for pg_try_advisory_lock."""
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _try_lock(session, source: str) -> bool:
    """Take the per-source advisory lock. True if we got it.

    Postgres only. On SQLite (used by the unit tests, which are single-threaded)
    there is nothing to contend with, so we proceed.
    """
    if session.bind.dialect.name != "postgresql":
        return True
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": _lock_key(source)}
        ).scalar()
    )


def _unlock(session, source: str) -> None:
    if session.bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(source)}
        )


def _running_run(session, source: str) -> SyncRun | None:
    return session.scalars(
        select(SyncRun)
        .where(SyncRun.source == source, SyncRun.status == "running")
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    ).first()


def config_hash(config: dict) -> str:
    """Hash of a task's configuration.

    A changed hash forces a full sync. A threshold or column-map edit applied to
    only the slice of data that arrived after it would produce a dataset half
    computed one way and half the other, which nobody can reason about.
    """
    payload = repr(sorted(config.items())).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def get_state(session, task: str, scope: str) -> SyncState:
    state = session.get(SyncState, {"task": task, "scope": scope})
    if state is None:
        state = SyncState(task=task, scope=scope)
        session.add(state)
    return state


def should_full_sync(state: SyncState, current_config: str) -> bool:
    if state.prev_started_at is None:
        return True  # never run
    if state.prev_config != current_config:
        return True  # configuration moved under us
    return False


def run_sync(
    session,
    source: str,
    trigger: str = TRIGGER_MANUAL,
    *,
    connection_id: int = 1,
    now: datetime | None = None,
) -> SyncOutcome:
    """Run one source's ingestion.

    Args:
        source: registered source name, e.g. ``"excel"``.
        trigger: ``"scheduled"`` or ``"manual"`` - recorded, never branched on.
    """
    if trigger not in VALID_TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}; expected one of {VALID_TRIGGERS}")
    if source not in SOURCE_REGISTRY:
        raise ValueError(
            f"unknown source {source!r}; registered: {sorted(SOURCE_REGISTRY)}"
        )

    now = now or datetime.now(timezone.utc)

    if not _try_lock(session, source):
        existing = _running_run(session, source)
        log.info("sync for %s already running; joining run %s", source, existing and existing.id)
        return SyncOutcome(
            run_id=existing.id if existing else None,
            source=source,
            status="running",
            joined_existing=True,
            notes=["a sync for this source was already in flight; watching that one"],
        )

    run = SyncRun(source=source, trigger=trigger, started_at=now, status="running")
    session.add(run)
    session.flush()

    outcome = SyncOutcome(run_id=run.id, source=source, status="running")

    try:
        result = SOURCE_REGISTRY[source](
            session,
            connection_id=connection_id,
            now=now,
            sync_run_id=run.id,
        )
        outcome.rows_ok = result.get("rows_ok", 0)
        outcome.rows_rejected = result.get("rows_rejected", 0)
        outcome.changes_emitted = result.get("changes_emitted", 0)
        outcome.notes = result.get("notes", [])

        run.rows_ok = outcome.rows_ok
        run.rows_rejected = outcome.rows_rejected
        run.changes_emitted = outcome.changes_emitted
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        outcome.status = "success"

        # Only now. A watermark advanced past rows we failed to write would make
        # the gap permanent.
        state = get_state(session, task=source, scope=str(connection_id))
        state.prev_started_at = now
        state.last_success_at = run.finished_at

    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(timezone.utc)
        outcome.status = "failed"
        outcome.error = run.error
        log.exception("sync failed for %s", source)
        raise
    finally:
        _unlock(session, source)

    return outcome
