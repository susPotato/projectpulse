"""Drive the ingestion pipeline from the command line.

    python -m scripts.sync init
    python -m scripts.sync run [--trigger scheduled|manual]
    python -m scripts.sync changes
    python -m scripts.sync rejects

`run` is the same call the scheduler and the "Update now" button make.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import logging  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import select  # noqa: E402

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


def cmd_insight(args) -> None:
    """The whole intelligence layer, printed.

    This is the command that shows what the product actually produces: what is at
    risk, why, what it will impact, and what to do - each finding with the rule
    that fired, the values it compared, and the source rows behind it.
    """
    from app.intelligence.pipeline import analyze_project

    with session_scope() as session:
        bundle = analyze_project(
            session,
            project_id=args.project,
            also=args.also or [],
        )

        print(f"{bundle.project_id}  as of {bundle.as_of:%Y-%m-%d %H:%M}")
        print(
            f"{len(bundle.findings)} finding(s), "
            f"highest severity: {bundle.top_severity}\n"
        )

        for finding in bundle.by_severity():
            print(f"[{finding.severity.upper():8}] {finding.headline}")
            if finding.recommendation:
                print(f"           -> {finding.recommendation}")
            if finding.rule_trace:
                for condition in finding.rule_trace.conditions:
                    print(f"           rule {finding.rule_trace.rule_id}: {condition}")
            if finding.causal_link:
                link = finding.causal_link
                lag = (
                    f"{link.lag_days_min:g} days"
                    if link.lag_days_min == link.lag_days_max
                    else f"{link.lag_days_min:g}-{link.lag_days_max:g} days"
                )
                print(
                    f"           chain {link.template_id} "
                    f"({link.evidence_basis}, {link.ordering_basis}, lag {lag})"
                )
            for ref in finding.evidence[:3]:
                print(f"           evidence #{ref.raw_data_id} {ref.url}")
            print()

        quality = bundle.data_quality
        print(
            f"data quality: {quality.rows_rejected} row(s) rejected, "
            f"{quality.changes_low_confidence} of {quality.changes_total} changes "
            f"low-confidence, {quality.edges_stated} stated + "
            f"{quality.edges_inferred} inferred edges"
        )
        if quality.depends_on_inferred_edges:
            print(
                "  the schedule conclusion changes without inferred edges - "
                "it is a derived claim, not a stated one"
            )

        if args.narrative:
            print("\n" + "=" * 72)
            print(bundle.narrative)


def cmd_explain(args) -> None:
    """Show the arithmetic behind every number, so it can be checked by hand.

    The product's claim is that every figure is arithmetic on dates a human typed.
    That is only worth claiming if someone can verify it, which is what this is
    for - it reads the same report the findings were built from and prints the
    working.
    """
    from app.intelligence import explain
    from app.intelligence.context import build_context
    from app.intelligence.pipeline import load_edges, load_tasks
    from app.intelligence.schedule.graph import build_graph
    from app.intelligence.schedule.impact import project_schedule

    project_ids = [args.project, *(args.also or [])]

    with session_scope() as session:
        tasks = load_tasks(session, project_ids)
        edges = load_edges(session, project_ids)

        if not tasks:
            print(f"no tasks for {project_ids}. Run: python -m scripts.replay")
            return

        schedule = build_graph(tasks, edges)
        impact = project_schedule(schedule)
        stated = project_schedule(build_graph(tasks, edges, stated_only=True))

        print("FORWARD PASS - every date below is arithmetic you can check\n")
        print(explain.table(schedule, impact))

        print("\n'hidden' is slip the dependency chain implies that the sheet")
        print("does not show. It is the only column a PM cannot read off their")
        print("own spreadsheet.\n")

        targets = (
            [t.entity_id for t in tasks if t.entity_id.endswith(args.task)]
            if args.task
            else [p.entity_id for p in impact.inconsistent()]
        )

        if targets:
            print("WORKING\n")
            for entity_id in targets:
                print(explain.working(schedule, impact, entity_id))
                print()

        if schedule.dropped:
            print(f"EDGES REFUSED ({len(schedule.dropped)})\n")
            for edge, reason in schedule.dropped:
                print(f"  {edge.predecessor_id} -> {edge.successor_id}: {reason}")
            print()

        print("DRIVING PATH")
        print("  " + " -> ".join(
            e.split(":")[-1] for e in impact.driving_path
        ) or "  (none)")
        print(
            f"  project finish: planned {impact.project_end_planned}, "
            f"projected {impact.project_end_projected} "
            f"({impact.project_slip_days} day(s))"
        )

        if stated.project_end_projected != impact.project_end_projected:
            print(
                f"\n  Using ONLY human-stated dependencies the projection is "
                f"{stated.project_end_projected} instead. The difference rests on "
                "inferred edges."
            )

        if args.scalars:
            qa = []
            context = build_context(
                project_id=args.project,
                as_of=impact.project_end_projected or datetime.now(timezone.utc).date(),
                schedule=schedule,
                impact=impact,
                edges=edges,
                qa_items=qa,
            )
            print("\nCONTEXT SCALARS - what every rule compares against\n")
            for key, value in sorted(context.as_record().items()):
                print(f"  {key:32} {value}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    insight = sub.add_parser("insight", help="run the intelligence layer")
    insight.add_argument("--project", default="excel:Project:1:HRMS")
    insight.add_argument(
        "--also",
        action="append",
        help="the same delivery project as another source names it, e.g. "
             "jira:Project:1:HRMS",
    )
    insight.add_argument(
        "--narrative", action="store_true", help="print the narrative summary too"
    )
    insight.set_defaults(func=cmd_insight)

    explain_cmd = sub.add_parser(
        "explain", help="show the arithmetic behind every number"
    )
    explain_cmd.add_argument("--project", default="excel:Project:1:HRMS")
    explain_cmd.add_argument("--also", action="append")
    explain_cmd.add_argument(
        "--task", help="long-hand working for one task, e.g. WBS-114"
    )
    explain_cmd.add_argument(
        "--scalars", action="store_true", help="also print every rule input"
    )
    explain_cmd.set_defaults(func=cmd_explain)

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
