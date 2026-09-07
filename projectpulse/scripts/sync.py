"""Drive the ingestion pipeline from the command line.

    python -m scripts.sync init
    python -m scripts.sync run [--trigger scheduled|manual]
    python -m scripts.sync changes
    python -m scripts.sync rejects

`run` is the same call the scheduler and the "Update now" button make.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import create_all, session_scope
from app.ingest.runner import run_sync
from app.models.domain import StateChange
from app.models.sync import RawReject, SyncRun

# Importing a source module registers it in the runner registry.
import app.ingest.sources.excel.source  # noqa: F401
import app.ingest.sources.jira.source  # noqa: F401
from app.intelligence.temporal.ordering import OrderingBasis, ordering_basis


def cmd_init(_args) -> None:
    create_all()
    print("schema created")


def _parse_now(value: str | None) -> datetime:
    """Simulated clock, so a demo can replay a timeline instead of waiting weeks.

    Scan times are what bound every Excel change, so being able to set them is the
    difference between a demo that shows real intervals and one that shows every
    event happening within the same few seconds.
    """
    if value is None:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def cmd_run(args) -> None:
    now = _parse_now(args.now)
    with session_scope() as session:
        outcome = run_sync(session, args.source, args.trigger, now=now)

    print(f"run #{outcome.run_id}  source={args.source}  "
          f"trigger={args.trigger}  at={now:%Y-%m-%d %H:%M}  status={outcome.status}")
    if outcome.joined_existing:
        print("  (joined a sync already in flight)")
    print(f"  rows ok        : {outcome.rows_ok}")
    print(f"  rows rejected  : {outcome.rows_rejected}")
    print(f"  changes emitted: {outcome.changes_emitted}")
    for note in outcome.notes:
        print(f"  - {note}")


def cmd_changes(_args) -> None:
    with session_scope() as session:
        rows = session.scalars(
            select(StateChange).order_by(
                StateChange.occurred_at, StateChange.entity_id, StateChange.field
            )
        ).all()

        if not rows:
            print("no state changes recorded")
            return

        print(f"{len(rows)} state change(s)\n")
        header = f"{'entity':<28} {'field':<14} {'from':<12} {'to':<12} {'precision':<9} conf"
        print(header)
        print("-" * len(header))
        for change in rows:
            entity = change.entity_id.split(":", 3)[-1]
            print(
                f"{entity:<28} {change.field:<14} "
                f"{str(change.old_value or '-'):<12} {str(change.new_value or '-'):<12} "
                f"{change.precision:<9} {change.identity_confidence}"
            )

        bounded = [c for c in rows if c.precision == "bounded"]
        if bounded:
            sample = bounded[0]
            span = sample.occurred_at - sample.occurred_at_lower
            print(
                f"\nbounded changes carry a {span} window "
                f"({sample.occurred_at_lower:%Y-%m-%d %H:%M} .. {sample.occurred_at:%Y-%m-%d %H:%M})"
            )
        low = [c for c in rows if c.identity_confidence == "low"]
        if low:
            print(
                f"{len(low)} change(s) came from rows matched by title, not by id; "
                "the causal engine will not build on these"
            )


def cmd_rejects(_args) -> None:
    with session_scope() as session:
        rows = session.scalars(select(RawReject).order_by(RawReject.id)).all()
        if not rows:
            print("no rejected rows")
            return
        print(f"{len(rows)} rejected row(s)\n")
        for reject in rows:
            location = f"{reject.sheet_name}!row{reject.row_index}"
            print(f"  {location:<24} {reject.reason}")


def cmd_runs(_args) -> None:
    with session_scope() as session:
        rows = session.scalars(select(SyncRun).order_by(SyncRun.id)).all()
        for run in rows:
            print(
                f"#{run.id} {run.source:<8} {run.trigger:<10} {run.status:<8} "
                f"ok={run.rows_ok} rejected={run.rows_rejected} changes={run.changes_emitted}"
            )


def _label(change: StateChange) -> str:
    entity = change.entity_id.split(":", 3)[-1]
    return f"{entity}.{change.field}"


def cmd_order(_args) -> None:
    """Which orderings the data actually supports.

    The number that matters is not how many changes we hold, but how many pairs
    can be ordered. A causal chain needs an ordered pair; without one, the system
    can report what moved and must stay silent about why.
    """
    with session_scope() as session:
        rows = session.scalars(
            select(StateChange).order_by(StateChange.occurred_at)
        ).all()

    exact = [c for c in rows if c.precision == "exact"]
    bounded = [c for c in rows if c.precision == "bounded"]
    print(f"{len(rows)} state change(s): {len(exact)} exact, {len(bounded)} bounded\n")

    pairs = []
    for a in rows:
        for b in rows:
            if a is b:
                continue
            basis = ordering_basis(a, b)
            if basis is not OrderingBasis.UNPROVABLE:
                pairs.append((a, b, basis))

    total = len(rows) * (len(rows) - 1)
    print(f"orderable pairs: {len(pairs)} of {total} possible\n")
    if not pairs:
        print("no ordering is provable - no causal chain can be built")
        return

    # Cross-source pairs are the interesting ones: an exact Jira event shown to
    # precede a bounded Excel observation is a claim neither source could make
    # on its own.
    cross = [
        (a, b, basis)
        for a, b, basis in pairs
        if a.precision == "exact" and b.precision == "bounded"
    ]
    print(f"{len(cross)} of those cross from an exact event to a bounded one:\n")
    header = f"{'earlier (exact)':<26} {'later (bounded)':<26} basis"
    print(header)
    print("-" * len(header))
    for a, b, basis in cross[:12]:
        print(f"{_label(a):<26} {_label(b):<26} {basis}")
    if len(cross) > 12:
        print(f"... and {len(cross) - 12} more")

    unprovable = total - len(pairs)
    print(
        f"\n{unprovable} pair(s) remain unprovable and are dropped, not hedged - "
        "mostly changes seen in the same scan window."
    )


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--source", default="excel",
                            choices=["excel", "jira_replay"])
    run_parser.add_argument("--trigger", default="manual",
                            choices=["manual", "scheduled"])
    run_parser.add_argument("--now", default=None,
                            help="simulated scan time, e.g. 2026-03-06T09:00")
    run_parser.set_defaults(func=cmd_run)

    sub.add_parser("order").set_defaults(func=cmd_order)

    sub.add_parser("changes").set_defaults(func=cmd_changes)
    sub.add_parser("rejects").set_defaults(func=cmd_rejects)
    sub.add_parser("runs").set_defaults(func=cmd_runs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
