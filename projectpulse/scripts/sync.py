"""Drive the ingestion pipeline from the command line.

    python -m scripts.sync init
    python -m scripts.sync run [--trigger scheduled|manual]
    python -m scripts.sync changes
    python -m scripts.sync rejects
    python -m scripts.sync onedrive login

`run` is the same call the scheduler and the "Update now" button make.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import logging  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from io import BytesIO  # noqa: E402
from pathlib import Path  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.config import settings
from app.db import create_all, session_scope
from app.ingest.runner import run_sync
from app.models.domain import StateChange
from app.models.sync import RawReject, SyncRun

# Importing a source module registers it in the runner registry.
import app.ingest.sources.excel.source  # noqa: F401
import app.ingest.sources.jira.source  # noqa: F401
from app.intelligence.temporal.ordering import OrderingBasis, ordering_basis
from app.narration.providers import PROVIDERS


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


def cmd_onedrive_login(_args) -> None:
    """Device-code sign-in, cached in the database - see `graph_auth.py`.

    Run once per environment you point at a given database: sign in locally
    against a Neon/Supabase URL and the deployed app reads the same cache,
    because the cache lives in the database, not on this machine.
    """
    from app.ingest.sources.excel.graph_auth import (
        GraphAuthError,
        sign_in_device_code,
        signed_in_identity,
    )

    def on_code(flow: dict) -> None:
        print(flow.get("message") or (
            f"Go to {flow.get('verification_uri')} and enter code "
            f"{flow.get('user_code')}"
        ))
        print("Waiting for sign-in to complete...")

    with session_scope() as session:
        try:
            sign_in_device_code(session, on_code)
        except GraphAuthError as exc:
            print(f"sign-in failed: {exc}")
            raise SystemExit(1) from None
        identity = signed_in_identity(session)

    print(f"signed in as {identity}")
    print(
        "Set PULSE_EXCEL_TRANSPORT=graph (and PULSE_ONEDRIVE_FOLDER, if the "
        "sheets are not at the OneDrive root) to read from OneDrive on the "
        "next sync."
    )


def cmd_onedrive_status(_args) -> None:
    from app.ingest.sources.excel.graph_auth import signed_in_identity

    with session_scope() as session:
        identity = signed_in_identity(session)

    if identity:
        print(f"signed in as {identity}")
    else:
        print("not signed in - run: python -m scripts.sync onedrive login")
    print(f"excel_transport = {settings.excel_transport!r}")
    print(f"onedrive_folder = {settings.onedrive_folder!r} (root if empty)")


def cmd_onedrive_logout(_args) -> None:
    from app.ingest.sources.excel.graph_auth import sign_out

    with session_scope() as session:
        sign_out(session)
    print("signed out")


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


def cmd_template(args) -> None:
    """Write the blank input workbooks a PM fills in.

    Generated from `SheetContract.template_headers`, so the file handed out and
    the file the reader understands are the same file by construction.
    """
    from app.exports.template import KINDS, template_bytes

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    kinds = [args.kind] if args.kind else sorted(KINDS)
    for kind in kinds:
        path = out / f"projectpulse_{kind}.xlsx"
        path.write_bytes(template_bytes(kind))
        contract, sheet_name, _ = KINDS[kind]
        print(f"{path}  sheet={sheet_name}  columns={len(contract.template_headers)}")


def cmd_forecast(args) -> None:
    """Print the delivery forecast, or the reason there is not one.

    A refusal is the normal path when the sheets are thin, and it prints the
    reason rather than an empty table - see
    `app/intelligence/schedule/forecast.py`.
    """
    from app.intelligence.pipeline import forecast_project

    with session_scope() as session:
        bundle = forecast_project(
            session, project_id=args.project, also=args.also or []
        )

    print(f"committed  {bundle.committed_end}")
    print(f"chain says {bundle.projected_end}   (nothing else moves)")
    print()

    if not bundle.available:
        print("no range:")
        for line in _wrap(bundle.reason):
            print(f"  {line}")
        return

    for point in bundle.points:
        late = f"+{point.days_late}d vs the commitment" if point.days_late > 0 else "on time"
        print(f"  P{point.percentile:<3} {point.finish}   {late}")

    print()
    print(
        f"  {bundle.trials} trials over {bundle.observations} observed drift(s), "
        f"onto {bundle.open_tasks} open task(s)"
    )
    for observation in bundle.sample:
        print(
            f"    {observation.label[:34]:<36} "
            f"{observation.committed} -> {observation.planned}  "
            f"{observation.days:+d}d"
        )
    print()
    for line in _wrap(bundle.assumption):
        print(f"  {line}")


def _wrap(text: str, width: int = 74) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width)


def cmd_report(args) -> None:
    """Write the status report, in whichever format was asked for.

    Renders the same two bundles the screens render, so the document cannot
    disagree with the app. No figure is formatted here - or in any of the three
    renderers; see `app/exports/document.py`.
    """
    from app.exports.document import build_document, resolve_sections
    from app.intelligence.pipeline import (
        analyze_project,
        explain_project,
        forecast_project,
        scenarios_project,
    )

    project_ids = [args.project, *(args.also or [])]
    chosen = resolve_sections(
        preset_id=args.template, sections=args.section or None
    )

    with session_scope() as session:
        bundle = analyze_project(
            session, project_id=args.project, also=args.also or []
        )
        explain = (
            explain_project(session, project_id=args.project, also=args.also or [])
            if "projection" in chosen
            else None
        )
        scenarios = (
            scenarios_project(session, project_id=args.project, also=args.also or [])
            if "scenarios" in chosen
            else None
        )
        forecast = (
            forecast_project(
                session, project_id=args.project, also=args.also or []
            )
            if "forecast" in chosen
            else None
        )
        risks = None
        if "risks" in chosen:
            from app.risks.service import list_risks

            risks = list_risks(session, project_ids=project_ids)

    if not bundle.findings and not bundle.context.get("task_count"):
        print(
            f"no data for project {args.project}. Build the timeline first:\n"
            "  python -m scripts.replay"
        )
        return

    doc = build_document(
        bundle,
        explain=explain,
        scenarios=scenarios,
        forecast=forecast,
        risks=risks,
        sections=chosen,
        project_name=args.name,
    )

    # The extension picks the renderer unless --format overrides it, so
    # `--out status.md` does the obvious thing.
    suffix = Path(args.out).suffix.lstrip(".").lower()
    fmt = args.format or (suffix if suffix in ("docx", "xlsx", "md") else "docx")

    if fmt == "md":
        from app.exports.markdown import markdown_bytes

        content = markdown_bytes(doc)
    elif fmt == "xlsx":
        from app.exports.workbook import workbook_bytes

        content = workbook_bytes(doc)
    else:
        from app.exports.report import ReportUnavailable, render_docx

        try:
            buffer = BytesIO()
            render_docx(doc).save(buffer)
            content = buffer.getvalue()
        except ReportUnavailable as exc:
            print(f"{exc}")
            return

    path = Path(args.out).with_suffix(f".{fmt}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print(
        f"{path}  ({len(content)} bytes)  "
        f"{len(bundle.findings)} finding(s), {len(doc.sections)} section(s)"
    )
    print(f"  sections: {', '.join(s.id for s in doc.sections) or '(none)'}")
    print(f"  narration: {bundle.narration_source}")
    print(f"  covers {len(project_ids)} source project id(s)")


def cmd_advise(args) -> None:
    """Show the advisory duration band for each task, if the model is present.

    Prints a band and never a number of days - see app/ml/duration.py. With no
    artefact on disk this says so and exits, which is the ordinary state.
    """
    from app.ml.duration import TaskFacts, try_load
    from app.models.domain import Task

    classifier, why = try_load(args.model or None)
    if classifier is None:
        # `why` distinguishes a missing file from one that will not unpickle.
        # Printing "no duration model" for the second sends a reader looking for
        # a download they have already done.
        print(f"duration advice unavailable: {why}")
        print(
            "  python -m scripts.fetch_model      # downloads the artefact\n"
            '  pip install -e ".[ml]"             # and its pinned dependencies'
        )
        return

    project_ids = [args.project, *(args.also or [])]
    with session_scope() as session:
        tasks = session.scalars(
            select(Task).where(Task.project_id.in_(project_ids)).order_by(Task.id)
        ).all()

    if not tasks:
        print("no tasks; run python -m scripts.replay first")
        return

    print(f"{len(tasks)} task(s). Advisory only - a band, never a number of days.\n")
    header = f"{'task':<26} {'band':<14} {'confidence':<11} basis"
    print(header)
    print("-" * len(header))
    for task in tasks:
        advice = classifier.advise(
            TaskFacts(
                title=task.title or "",
                project_key=task.project_id.split(":")[-1],
                created=task.start_date,
                has_assignee=bool(task.assignee),
            )
        )
        label = task.id.split(":", 3)[-1]
        if advice is None:
            print(f"{label:<26} {'-':<14} {'-':<11} the model declined")
            continue
        print(
            f"{label:<26} {advice.label:<14} {advice.confidence:<11} {advice.basis}"
        )


def cmd_insight(args) -> None:
    """The whole intelligence layer, printed.

    This is the command that shows what the product actually produces: what is at
    risk, why, what it will impact, and what to do - each finding with the rule
    that fired, the values it compared, and the source rows behind it.

    `--model` asks a language model to phrase the narrative instead of the
    deterministic template; `--provider` picks the vendor. It changes no finding
    and no figure whichever vendor answers: the draft is validated before any
    number is substituted into it, and a draft that fails is dropped in favour
    of the template with the reason printed. So the flag is safe to leave off,
    and safe to turn on.
    """
    from app.intelligence.pipeline import analyze_project

    narrator = None
    if args.model:
        from app.narration.providers import ModelConfig, drafter_for

        narrator = drafter_for(
            args.provider or settings.narration_provider,
            ModelConfig(model=args.llm_model or settings.narration_model),
        )

    with session_scope() as session:
        bundle = analyze_project(
            session,
            project_id=args.project,
            also=args.also or [],
            narrator=narrator,
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
            # Where the prose came from, before the prose itself. A reader who
            # cannot tell which they are looking at cannot judge either.
            print(f"narration: {bundle.narration_source}")
            if bundle.narration_fallback_reason:
                print(f"  fell back because: {bundle.narration_fallback_reason}")
            print()
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
    insight.add_argument(
        "--model",
        action="store_true",
        help="let a language model phrase the narrative; needs one of the llm "
             "extras and credentials, and falls back to the template if either "
             "is absent",
    )
    insight.add_argument(
        "--provider",
        choices=PROVIDERS,
        help="which vendor to ask (default: PULSE_NARRATION_PROVIDER, or "
             "anthropic). The gate around the answer is the same either way.",
    )
    insight.add_argument(
        "--llm-model",
        help="override the model id, e.g. claude-opus-5 (default: that "
             "provider's entry in narration.providers.DEFAULT_MODELS)",
    )
    insight.set_defaults(func=cmd_insight)

    template = sub.add_parser(
        "template", help="write the blank input workbooks a PM fills in"
    )
    template.add_argument("--out", default="templates", help="output directory")
    template.add_argument(
        "--kind", choices=("schedule", "worklog"), help="just one of them"
    )
    template.set_defaults(func=cmd_template)

    fc = sub.add_parser("forecast", help="a range of finish dates, from observed drift")
    fc.add_argument("--project", default="excel:Project:1:HRMS")
    fc.add_argument("--also", action="append")
    fc.set_defaults(func=cmd_forecast)

    report = sub.add_parser("report", help="write the status report")
    report.add_argument("--project", default="excel:Project:1:HRMS")
    report.add_argument("--also", action="append")
    report.add_argument("--out", default="delivery_status.docx")
    report.add_argument("--name", default="", help="project name for the title")
    report.add_argument(
        "--format",
        choices=("docx", "xlsx", "md"),
        help="output format (default: from the --out extension, else docx)",
    )
    report.add_argument(
        "--template",
        default=None,
        help="audience preset: weekly, steering or exec (default: weekly)",
    )
    report.add_argument(
        "--section",
        action="append",
        help="include only this section (repeatable); overrides --template",
    )
    report.set_defaults(func=cmd_report)

    advise = sub.add_parser(
        "advise", help="advisory duration band per task (needs the ml extra)"
    )
    advise.add_argument("--project", default="excel:Project:1:HRMS")
    advise.add_argument("--also", action="append")
    advise.add_argument("--model", default="", help="path to the joblib artefact")
    advise.set_defaults(func=cmd_advise)

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

    onedrive = sub.add_parser("onedrive", help="Microsoft Graph / OneDrive sign-in")
    onedrive_sub = onedrive.add_subparsers(dest="onedrive_command", required=True)
    onedrive_sub.add_parser("login").set_defaults(func=cmd_onedrive_login)
    onedrive_sub.add_parser("status").set_defaults(func=cmd_onedrive_status)
    onedrive_sub.add_parser("logout").set_defaults(func=cmd_onedrive_logout)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
