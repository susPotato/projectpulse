"""A small local console for watching the retriever work.

Not the product UI. This exists so a change to a spreadsheet can be made in Excel
and its consequences seen immediately: which rows were read, which were refused,
what state changes came out, how precisely they are dated, and how many of them
can actually be ordered against each other.

    python -m scripts.demo          # then open http://127.0.0.1:8000

The workflow it is built around is the real one: edit `data/demo/*.xlsx` in Excel,
or `data/demo/jira/*.json` in any editor, press Sync, and look at what changed.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from app.api.schemas.explain import ExplainBundle
from app.api.schemas.gantt import GanttBundle
from app.api.schemas.portfolio import PortfolioBundle
from app.api.schemas.program import ProgramBundle
from app.api.schemas.team import TeamBundle
from app.api.schemas.scenario import ScenarioBundle
from app.api.schemas.agent import ChatRequest, ChatResponse
from app.api.schemas.insight import InsightBundle
from app.api.schemas.risk import RiskBundle, RiskIn, RiskOut
from app import scope
from app.config import settings
from app.db import check_connection, create_all, drop_all, session_scope
from app.ingest.runner import run_sync
from app.intelligence.pipeline import (
    analyze_project,
    explain_project,
    gantt_project,
    portfolio,
    program_config,
    scenarios_project,
    team_project,
)
from app.ingest.sources.excel.reader import sha256_file
from app.ingest.sources.excel.source import WATCHED
from app.intelligence.temporal.ordering import OrderingBasis, ordering_basis
from app.models.domain import StateChange
from app.models.sync import RawReject, SheetScan, SyncRun

# Importing the source modules registers them with the runner.
import app.ingest.sources.excel.source  # noqa: F401
import app.ingest.sources.jira.source  # noqa: F401

log = logging.getLogger(__name__)

app = FastAPI(title="ProjectPulse retriever console")
STATIC = Path(__file__).parent / "static"

# Mounted so the three pages can share one stylesheet instead of each declaring
# its own `:root` - which is exactly how the six mockups ended up with two
# conflicting token families.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SyncRequest(BaseModel):
    source: str = "excel"
    #: Simulated scan time. Blank means now. It matters: scan times are what bound
    #: every spreadsheet change, so replaying a timeline needs control of them.
    now: str | None = None


class StepRequest(BaseModel):
    step: int = 0


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _short(entity_id: str) -> str:
    return entity_id.split(":", 3)[-1]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> dict:
    """Everything the console renders, in one round trip."""
    with session_scope() as session:
        changes = session.scalars(
            select(StateChange).order_by(
                StateChange.occurred_at.desc(), StateChange.entity_id
            )
        ).all()
        rejects = session.scalars(
            select(RawReject).order_by(RawReject.id.desc())
        ).all()
        runs = session.scalars(
            select(SyncRun).order_by(SyncRun.id.desc()).limit(12)
        ).all()
        scans = session.scalars(select(SheetScan)).all()

        last_scan: dict[str, SheetScan] = {}
        for scan in scans:
            prior = last_scan.get(scan.scope)
            if prior is None or scan.scanned_at > prior.scanned_at:
                last_scan[scan.scope] = scan

        # Ordering, computed live so the effect of each sync is visible.
        pairs = []
        for a in changes:
            for b in changes:
                if a is b:
                    continue
                basis = ordering_basis(a, b)
                if basis is not OrderingBasis.UNPROVABLE:
                    pairs.append((a, b, basis))

        cross = [
            {
                "earlier": f"{_short(a.entity_id)}.{a.field}",
                "later": f"{_short(b.entity_id)}.{b.field}",
                "basis": str(basis),
            }
            for a, b, basis in pairs
            if a.precision == "exact" and b.precision == "bounded"
        ]

        total_pairs = len(changes) * (len(changes) - 1)

        files = []
        for watched in WATCHED:
            path = Path(settings.data_root) / watched.file_name
            scope = f"{watched.file_name}#{watched.sheet_name}"
            scan = last_scan.get(scope)
            exists = path.exists()
            current_hash = sha256_file(path) if exists else None
            files.append(
                {
                    "kind": "excel",
                    "scope": scope,
                    "path": str(path),
                    "exists": exists,
                    "last_scan": scan.scanned_at.isoformat() if scan else None,
                    "rows": scan.row_count if scan else 0,
                    # The console's most useful single fact: is there anything
                    # to ingest, or would a sync be a no-op?
                    "dirty": bool(exists and scan and scan.sha256 != current_hash),
                    "never_scanned": exists and scan is None,
                }
            )

        jira_dir = Path(settings.data_root) / "jira"
        jira_files = sorted(jira_dir.glob("*.json")) if jira_dir.exists() else []
        jira_issues = 0
        jira_items = 0
        for path in jira_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            issues = payload.get("issues", [])
            jira_issues += len(issues)
            jira_items += sum(
                len(h.get("items", []))
                for issue in issues
                for h in (issue.get("changelog") or {}).get("histories", [])
            )

        return {
            "data_root": str(settings.data_root),
            "files": files,
            "jira": {
                "dir": str(jira_dir),
                "files": [p.name for p in jira_files],
                "issues": jira_issues,
                "field_changes": jira_items,
            },
            "counts": {
                "changes": len(changes),
                "exact": sum(1 for c in changes if c.precision == "exact"),
                "bounded": sum(1 for c in changes if c.precision == "bounded"),
                "low_confidence": sum(
                    1 for c in changes if c.identity_confidence == "low"
                ),
                "orderable": len(pairs),
                "total_pairs": total_pairs,
                "rejects": len(rejects),
            },
            "changes": [
                {
                    "entity": _short(c.entity_id),
                    "source": c.entity_id.split(":", 1)[0],
                    "field": c.field,
                    "old": c.old_value,
                    "new": c.new_value,
                    "precision": c.precision,
                    "confidence": c.identity_confidence,
                    "lower": c.occurred_at_lower.isoformat(),
                    "upper": c.occurred_at.isoformat(),
                }
                for c in changes[:200]
            ],
            "cross_pairs": cross[:40],
            "rejects": [
                {
                    "where": f"{r.sheet_name}!row{r.row_index}",
                    "reason": r.reason,
                }
                for r in rejects[:40]
            ],
            "runs": [
                {
                    "id": r.id,
                    "source": r.source,
                    "trigger": r.trigger,
                    "status": r.status,
                    "at": r.started_at.isoformat() if r.started_at else None,
                    "rows_ok": r.rows_ok,
                    "rejected": r.rows_rejected,
                    "changes": r.changes_emitted,
                }
                for r in runs
            ],
        }


@app.post("/api/sync")
def sync(request: SyncRequest) -> dict:
    """The same call the scheduler and the PM's button make."""
    now = _parse_now(request.now)
    try:
        with session_scope() as session:
            outcome = run_sync(session, request.source, "manual", now=now)
    except Exception as exc:  # noqa: BLE001 - surfaced in the console, not swallowed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "run_id": outcome.run_id,
        "status": outcome.status,
        "rows_ok": outcome.rows_ok,
        "rows_rejected": outcome.rows_rejected,
        "changes_emitted": outcome.changes_emitted,
        "notes": outcome.notes,
        "at": now.isoformat(),
    }


@app.post("/api/write-step")
def write_step(request: StepRequest) -> dict:
    """Overwrite the demo workbooks at a given point in their story.

    A shortcut for the guided tour. Editing the files in Excel by hand does
    exactly the same thing and is the more convincing demonstration.
    """
    result = subprocess.run(
        [sys.executable, "-m", "scripts.gen_demo_data", "--step", str(request.step)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    return {
        "ok": result.returncode == 0,
        "output": (result.stdout or result.stderr).strip(),
    }


@app.post("/api/write-jira")
def write_jira() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.gen_jira_data"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    return {
        "ok": result.returncode == 0,
        "output": (result.stdout or result.stderr).strip(),
    }


@app.post("/api/reset")
def reset() -> dict:
    """Drop and recreate the schema, so a timeline can be replayed from scratch."""
    drop_all()
    create_all()
    return {"ok": True}


#: The built React bundle. Committed to the repo, so `python -m scripts.demo`
#: never needs `npm install` - see `web/vite.config.ts` for why that matters.
APP_SHELL = STATIC / "app" / "index.html"


def _spa() -> FileResponse:
    """Serve the built app, or say plainly that it has not been built.

    A missing bundle would otherwise 500 with a FileNotFoundError, which tells a
    reader nothing about the one command that fixes it.
    """
    if not APP_SHELL.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "the web bundle is missing. Build it with: "
                "cd web && npm install && npm run build"
            ),
        )
    return FileResponse(APP_SHELL)


@app.get("/insight")
def insight_page() -> FileResponse:
    """The insight screen.

    Both this and /explain serve the same bundle; the app picks the page from
    the path. Two routes rather than a hash router so the URLs are real and a
    judge can link to either.
    """
    return _spa()


@app.get("/gantt")
def gantt_page() -> FileResponse:
    """The schedule view.

    Read-only by design - see `api/schemas/gantt.py` for why making it editable
    would cost the precision model.
    """
    return FileResponse(STATIC / "gantt.html")


@app.get("/api/gantt", response_model=GanttBundle)
def api_gantt(
    project: str = "excel:Project:1:HRMS",
    also: list[str] | None = Query(default=None),
) -> GanttBundle:
    """Tasks, milestones and dependency edges on one shared time window."""
    if also is None:
        also = ["jira:Project:1:HRMS"]

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        bundle = gantt_project(session, project_id=project, also=list(also))

    if not bundle.rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no tasks for project {project!r}. Build the demo timeline first: "
                "python -m scripts.replay"
            ),
        )
    return bundle


@app.get("/explain")
def explain_page() -> FileResponse:
    """The arithmetic behind every number.

    A sibling of `/insight` rather than a panel inside it: a PM opens this only
    when they want to check a figure, and burying it would make the insight
    screen heavier for everyone else.
    """
    return _spa()


@app.get("/api/explain", response_model=ExplainBundle)
def api_explain(
    project: str = "excel:Project:1:HRMS",
    also: list[str] | None = Query(default=None),
) -> ExplainBundle:
    """The forward pass, with the working, for one project."""
    if also is None:
        also = ["jira:Project:1:HRMS"]

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        bundle = explain_project(session, project_id=project, also=list(also))

    if not bundle.steps:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no tasks for project {project!r}. Build the demo timeline first: "
                "python -m scripts.replay"
            ),
        )
    return bundle


#: Content types for the two generated files. Set explicitly because a browser
#: offered `application/octet-stream` saves a file Excel and Word will open only
#: after a warning, which on a demo reads as a broken download.
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.get("/api/template/{kind}.xlsx")
def template(kind: str) -> Response:
    """A blank input workbook, generated from the sheet contract itself.

    Two files rather than one with two tabs, because that is what the watcher
    watches. `kind` is `schedule` or `worklog`.
    """
    from app.exports.template import KINDS, template_bytes

    if kind not in KINDS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown template {kind!r}; expected one of {sorted(KINDS)}",
        )

    return Response(
        content=template_bytes(kind),
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="projectpulse_{kind}.xlsx"'
        },
    )


@app.get("/api/report.docx")
def report(
    project: str = "excel:Project:1:HRMS",
    also: list[str] | None = Query(default=None),
) -> Response:
    """The status report, as a .docx a PM can attach to an email.

    The same findings as `/api/insight` and the same projection as
    `/api/explain`, so the document cannot disagree with either screen - it
    renders their bundles rather than recomputing anything.
    """
    if also is None:
        also = ["jira:Project:1:HRMS"]

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    from app.exports.report import ReportUnavailable, report_bytes

    with session_scope() as session:
        bundle = analyze_project(
            session, project_id=project, also=list(also), narrator=_narrator()
        )
        explain = explain_project(session, project_id=project, also=list(also))

    if not bundle.findings and not bundle.context.get("task_count"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"no data for project {project!r}. Build the demo timeline first: "
                "python -m scripts.replay"
            ),
        )

    try:
        content = report_bytes(bundle, explain=explain)
    except ReportUnavailable as exc:
        # 501 rather than 500: the server is working, this optional extra is
        # simply not installed, and the message says which.
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=DOCX_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="delivery_status.docx"'
        },
    )


@app.get("/portfolio")
def portfolio_page() -> FileResponse:
    """The program screen. Same bundle as /insight; the app picks by path."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/portfolio", response_model=PortfolioBundle)
def portfolio_api() -> PortfolioBundle:
    """Every delivery project in the program, ranked worst first.

    Folded from `analyze_project` per project rather than a separate
    aggregation, so the program view cannot disagree with the project view.
    """
    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return portfolio(session)


@app.get("/team")
def team_page() -> FileResponse:
    """Who is carrying what, and what moved."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/team", response_model=TeamBundle)
def team(
    project: str = "excel:Project:1:HRMS",
    also: list[str] | None = Query(default=None),
) -> TeamBundle:
    """Workload, effort and activity, from columns the sheets actually carry.

    `api/schemas/team.py` records what is served and what is deliberately
    absent. The effort burn is reconstructed from observed `hours_spent`
    changes rather than read off the final sheet, which is what lets a flat
    stretch in it mean "nothing was logged" instead of "we stopped looking".
    """
    if also is None:
        also = scope.also_for(project)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return team_project(session, project_id=project, also=list(also))


@app.get("/api/program", response_model=ProgramBundle)
def program() -> ProgramBundle:
    """Program configuration: watched sources, project pairing, the rule table.

    Read-only. A rule table edited in a browser has no review and no history,
    and every finding here is defended by pointing at these thresholds.
    """
    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return program_config(session)


@app.get("/api/scenarios", response_model=ScenarioBundle)
def scenarios(
    project: str = "excel:Project:1:HRMS",
    also: list[str] | None = Query(default=None),
) -> ScenarioBundle:
    """What the schedule would do if one thing changed.

    Every figure is the forward pass re-run over modified rows - a simulation,
    never a mutation: nothing is written to a task, an edge or a spreadsheet,
    which is what lets the app answer "what if" while staying read-only.

    Returns an empty list when the plan is not late. There is nothing to
    recover then, and a list of zero-day scenarios reads as a broken feature.
    """
    if also is None:
        also = ["jira:Project:1:HRMS"]

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return scenarios_project(session, project_id=project, also=list(also))


def _narrator():
    """The narration drafter, if narration is switched on.

    Returns None when it is not, which is the default: the served narrative is
    then the deterministic template - complete prose, no key, no network - and
    a request cannot be made slower or more fragile by a feature nobody turned
    on.

    Read per request from `narration.store`, so the settings page takes effect
    on the next reload rather than the next restart. `app.config` is still the
    floor: it supplies the defaults the store starts from.
    """
    from app.narration.store import load

    current = load()
    if not current.enabled:
        return None

    from app.narration.providers import ModelConfig, drafter_for

    try:
        return drafter_for(
            current.provider,
            ModelConfig(
                model=current.model,
                api_key=current.api_key,
                base_url=current.base_url,
            ),
        )
    except ValueError:
        # An unknown provider in the stored file. Narration is optional, so a
        # bad setting costs the model and not the page.
        log.warning("unknown narration provider %r; serving the template", current.provider)
        return None


def _is_local(request: Request) -> bool:
    """Whether the caller is on this machine.

    The settings endpoints accept an API key, so writing them is restricted to
    loopback. That is not a permission system - it is the smallest honest
    boundary: `scripts.demo` binds 127.0.0.1, so it always passes, and a
    deployment behind a proxy always fails and becomes read-only with no
    configuration to forget.
    """
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"}


class NarrationSettingsIn(BaseModel):
    """What the settings page may change.

    `api_key` omitted (or null) keeps the stored key - the page never receives
    it, so it cannot send it back, and a save that did not retype it must not
    wipe it. An empty string is an explicit clear.
    """

    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None


@app.get("/settings")
def settings_page() -> FileResponse:
    """Where a person turns narration on and pastes a key."""
    return FileResponse(STATIC / "settings.html")


@app.get("/api/settings")
def read_settings() -> dict:
    """Current narration settings. Never includes the key itself."""
    from app.narration.store import public_view

    view = public_view()
    view["writable"] = True
    return view


@app.put("/api/settings")
def write_settings(request: Request, body: NarrationSettingsIn) -> dict:
    from app.narration.providers import PROVIDERS
    from app.narration.store import public_view, update

    if not _is_local(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "settings can only be changed from the machine the app runs on. "
                "Set PULSE_NARRATION, PULSE_NARRATION_PROVIDER and the vendor's "
                "API key as environment variables instead."
            ),
        )

    if body.provider is not None and body.provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider {body.provider!r}; expected one of {list(PROVIDERS)}",
        )

    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    update(**changes)
    return read_settings()


@app.post("/api/settings/test")
def test_settings(request: Request) -> dict:
    """Ask the configured model for a narrative, and report exactly what happened.

    A real end-to-end call rather than a credential ping: it builds the same
    brief, runs the same eight-stage gate and substitutes the same way, so a
    pass here means the feature works and not merely that the key is valid.
    """
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="only available locally")

    from app.narration.client import narrate
    from app.narration.providers import ModelConfig, drafter_for
    from app.narration.store import load

    current = load()
    try:
        drafter = drafter_for(
            current.provider,
            ModelConfig(
                model=current.model,
                api_key=current.api_key,
                base_url=current.base_url,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        bundle = analyze_project(
            session,
            project_id="excel:Project:1:HRMS",
            also=["jira:Project:1:HRMS"],
        )

    if not bundle.findings:
        raise HTTPException(
            status_code=404,
            detail="no findings to narrate. Build the demo timeline: python -m scripts.replay",
        )

    outcome = narrate(bundle, drafter=drafter)
    return {
        "source": outcome.source,
        "attempts": outcome.attempts,
        "fallback_reason": outcome.fallback_reason,
        "narrative": outcome.narrative,
        "provider": current.provider,
        "model": current.model or "(provider default)",
    }


@app.get("/risk")
def risk_page() -> FileResponse:
    """The risk register: what a PM tracks by hand, not what the engine finds."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/risks", response_model=RiskBundle)
def read_risks(project: list[str] | None = Query(default=None)) -> RiskBundle:
    """Every risk, program-wide by default - `Layout/fpt-pm-risk.html` lists
    several projects in one table. Pass `?project=` (repeatable) to narrow it.

    Unlike every other route in this file, this one needs no `check_connection`
    guard against an empty analysis: an empty risk register is not an error,
    it is a program nobody has logged a risk against yet.
    """
    from app.risks.service import list_risks

    with session_scope() as session:
        return list_risks(session, project_ids=project)


@app.post("/api/risks", response_model=RiskOut, status_code=201)
def create_risk_route(body: RiskIn) -> RiskOut:
    from app.risks.service import create_risk

    with session_scope() as session:
        try:
            return create_risk(session, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/risks/{risk_id}", response_model=RiskOut)
def update_risk_route(risk_id: int, body: RiskIn) -> RiskOut:
    from app.risks.service import update_risk

    with session_scope() as session:
        updated = update_risk(session, risk_id, body)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"no risk with id {risk_id}")
        return updated


@app.delete("/api/risks/{risk_id}", status_code=204)
def delete_risk_route(risk_id: int) -> Response:
    from app.risks.service import delete_risk

    with session_scope() as session:
        if not delete_risk(session, risk_id):
            raise HTTPException(status_code=404, detail=f"no risk with id {risk_id}")
    return Response(status_code=204)


@app.get("/agent")
def agent_page() -> FileResponse:
    """Free-form chat - the one page with no deterministic engine behind it."""
    return FileResponse(STATIC / "app" / "index.html")


@app.post("/api/agent/chat", response_model=ChatResponse)
def agent_chat(body: ChatRequest) -> ChatResponse:
    """One reply, given the whole conversation so far.

    Stateless: nothing is persisted server-side, so the client resends the
    transcript each turn - see `app/agent/chat.py`. Reuses whichever key
    narration is already configured with, so a working `/insight` narrative
    means this works too, with no second setup.

    A link in the *latest* user turn is fetched and its text folded into
    that turn before the model sees it - see `link_fetch.py`. Only the
    latest turn, not the whole history: re-fetching every link on every
    reply would repeat both the latency and the token cost for no new
    information.
    """
    from app.agent.chat import ChatTurn, ChatUnavailable, gemini_chat
    from app.agent.link_fetch import fetch_and_extract, find_first_url
    from app.narration.store import load as load_narration_settings

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    settings_ = load_narration_settings()
    turns = [ChatTurn(role=m.role, content=m.content) for m in body.messages]

    last = turns[-1]
    if last.role == "user":
        url = find_first_url(last.content)
        if url:
            fetched = fetch_and_extract(url)
            turns[-1] = ChatTurn(
                role="user",
                content=(
                    f"{last.content}\n\n"
                    f"[Content fetched from {url}]\n{fetched}\n[end of fetched content]"
                ),
            )

    try:
        reply = gemini_chat(
            turns,
            model=settings_.model or "gemini-3.8-flash",
            api_key=settings_.api_key or None,
        )
    except ChatUnavailable as exc:
        return ChatResponse(reply="", ok=False, error=str(exc))

    return ChatResponse(reply=reply, ok=True)


@app.get("/api/insight", response_model=InsightBundle)
def insight(
    project: str = "excel:Project:1:HRMS",
    also: list[str] | None = Query(default=None),
) -> InsightBundle:
    """The whole intelligence layer for one project, as one object.

    `also` names the same delivery project as other source systems call it - one
    project tracked in both Jira and a spreadsheet produces two `projects` rows,
    and analysing them separately would throw away every cross-source claim.

    This is the endpoint the React insight route renders. It is served here rather
    than assembled in the client so that numbers stay born in exactly one place.
    """
    # The demo portfolio is the same project in both sources; a real deployment
    # reads this pairing from scope config alongside the watched sheets.
    if also is None:
        also = ["jira:Project:1:HRMS"]

    # Report an unreachable database as an unreachable database. Letting the
    # driver error propagate gives a 500 with a stack trace in the log and a bare
    # failure on the page, which reads as "the product is broken" rather than
    # "the container is not running".
    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        bundle = analyze_project(
            session, project_id=project, also=list(also), narrator=_narrator()
        )

    if not bundle.findings and not bundle.context.get("task_count"):
        # An empty database is not an error, but it is indistinguishable from a
        # healthy project on the page unless we say so.
        raise HTTPException(
            status_code=404,
            detail=(
                f"no data for project {project!r}. Build the demo timeline first: "
                "python -m scripts.replay"
            ),
        )
    return bundle
