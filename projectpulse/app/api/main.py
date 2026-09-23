"""The whole server: the JSON API, the built web app, and the static pages.

One FastAPI app, run by one uvicorn process, serving everything from one origin -
so there is no reverse proxy, no separate static host and no CORS anywhere.

    python -m scripts.demo          # local, http://127.0.0.1:8000
    python -m scripts.serve         # the container entry point, $PORT

Three kinds of front end share the one `/static` mount, which is history rather
than design - the hand-written pages came first - but the split is stable:

* the built React bundle (`static/app/`), served by every page route below,
* two hand-written pages, `/gantt` and `/settings`,
* the shared assets both use - `shell.css`, `gantt.css`, `gantt.js`, `theme.js`.

`shell.css` and `gantt.js` are shared on purpose. Six mockups with their own
`:root` blocks is how this project ended up with two conflicting token families,
and porting the Gantt into React would create a second renderer - two renderers
eventually draw two different pictures of one projection.

**Every screen has a real route.** `/insight`, `/team`, `/risk` and the rest each
return the same `index.html` and the app picks the page from the path, rather
than a hash router, so the URLs are linkable. There is deliberately no catch-all:
an unknown path 404s here instead of booting the app into a client-side "not
found", so a typo in an API path cannot render a dashboard.

This module is routes only. Every `/api/*` handler is a thin wrapper that checks
the connection, resolves the project id (invariant 7 - one delivery project, two
source ids), opens a session and calls `intelligence/pipeline.py`. No figure is
computed here; `response_model=` is what validates one on the way out.

Two failure shapes are deliberate and are what the pages rely on:

* **503** - the database is unreachable, and the detail says so. Paired with
  psycopg's 5-second connect timeout, a missing container reads as a missing
  container instead of a hang.
* **404** - there is no data, and the detail carries the command that fixes it
  (`python -m scripts.replay`).

So the three ways a screen can fail are distinguishable from the browser alone:
503 is the database, 404 is the seed, and no JSON at all means the HTML was
opened from disk, where a relative fetch has no server to reach.
"""

from __future__ import annotations

from dataclasses import dataclass

import importlib.util
import json
import logging
import os
import hashlib
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api import tracelink_view
from pydantic import BaseModel
from sqlalchemy import bindparam, func, select, text

from app.api.schemas.explain import ExplainBundle
from app.api.schemas.gantt import GanttBundle
from app.api.schemas.portfolio import PortfolioBundle
from app.api.schemas.program import ProgramBundle
from app.api.schemas.programs import (
    CreatedProgram,
    CreatedProject,
    ProgramIn,
    ProgramListBundle,
    ProgramRollupBundle,
    ProjectIn,
)
from app.api.schemas.dashboard import (
    CatalogueBundle,
    CustomChartDraft,
    CustomTileDraftRequest,
    CustomTileIn,
    CustomTileListBundle,
    CustomTileOut,
    DashboardOut,
    GenerateRequest,
    TileIn,
    TileOut,
    TileChatRequest,
    TileChatResponse,
)
from app.api.schemas.team import TeamBundle
from app.api.schemas.scenario import ScenarioBundle
from app.api.schemas.forecast import ForecastBundle
from app.api.schemas.agent import ChatRequest, ChatResponse
from app.api.schemas.insight import InsightBundle
from app.api.schemas.risk import (
    CitedTask,
    RiskBundle,
    RiskDraftBundle,
    RiskIn,
    RiskOut,
)
from app.api.schemas.report import (
    ReportBlock,
    ReportFormatOption,
    ReportOptions,
    ReportPresetOption,
    ReportPreview,
    ReportSection,
    ReportSectionOption,
)
from app import scope
from app.config import settings
from app.db import check_connection, session_scope
from app.ingest.runner import run_sync
from app.intelligence.pipeline import (
    analyze_project,
    explain_project,
    gantt_project,
    forecast_project,
    list_programs,
    portfolio,
    program_config,
    program_rollup,
    scenarios_project,
    team_project,
)

# Importing the source modules registers them with the runner.
import app.ingest.sources.excel.source  # noqa: F401
import app.ingest.sources.jira.source  # noqa: F401

log = logging.getLogger(__name__)

app = FastAPI(title="ProjectPulseAI")
STATIC = Path(__file__).parent / "static"

# Mounted so the three pages can share one stylesheet instead of each declaring
# its own `:root` - which is exactly how the six mockups ended up with two
# conflicting token families.
#: Hand-written assets whose filename never changes. A build-time hash would
#: be better, but these are not built - they are edited in place, which is the
#: whole reason a browser goes on serving a stale copy of them.
_VERSIONED = re.compile(
    r'(?P<attr>href|src)="(?P<path>/static/[^"?]+\.(?:js|css))"')


def _page(name: str) -> HTMLResponse:
    """A hand-written page, with its own assets fingerprinted in the URL.

    `no-cache` on the asset makes a browser *revalidate*; it does not help
    the copy it cached before that header existed, and it still costs a
    round trip per file per page load. Putting the file's content hash in
    the query string is the stronger and cheaper answer: a changed file is a
    changed URL, so the browser fetches it because it has never seen it
    before, and an unchanged one is served from cache with no request at
    all. This has now been mistaken for "the deploy did not work" twice.
    """
    html = (STATIC / name).read_text(encoding="utf-8")

    def stamp(match: re.Match[str]) -> str:
        target = STATIC / match.group("path")[len("/static/"):]
        if not target.exists():
            return match.group(0)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:10]
        return f'{match.group("attr")}="{match.group("path")}?v={digest}"'

    return HTMLResponse(_VERSIONED.sub(stamp, html))


class _Revalidating(StaticFiles):
    """Static files that a browser must re-check before reusing.

    Two kinds of file live under this one mount and they want opposite
    caching. Vite's output is content-hashed - `index-Dh28aHl8.js` - so a
    changed bundle is a changed URL and the old one can be cached forever.
    The hand-written pages are not: `gantt.js` keeps its name through every
    edit, so a browser that cached it once keeps showing the old chart. That
    is not hypothetical - a legend fix looked like it had not deployed,
    because the server was serving the new file and the browser was not
    asking for it.

    `no-cache` does not mean "do not store": it means revalidate first. The
    ETag `StaticFiles` already sends turns that into a 304 on the common
    path, so the cost of correctness here is one conditional request.
    """

    #: Content-hashed by the build, so the name changes when the bytes do.
    IMMUTABLE = "/app/assets/"

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        path = str(kwargs.get("full_path") or (args[0] if args else ""))
        fingerprinted = self.IMMUTABLE.strip("/") in path.replace("\\", "/")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if fingerprinted
            else "no-cache"
        )
        return response


app.mount("/static", _Revalidating(directory=STATIC), name="static")


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
    """The landing page: the Program board, worst project first.

    Used to be the raw ingestion console - moved to `/console` so a PM opening
    the app sees which projects are in trouble instead of a retriever debug
    log. `main.tsx` picks Portfolio for this path the same way it does for
    every other built-bundle route.
    """
    return _spa()


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


@app.get("/traceability")
def traceability_page() -> FileResponse:
    """Jira-to-code traceability: which tickets the code backs up, and what it
    does that no ticket claims.

    Hand-written like /gantt and /settings rather than part of the React
    bundle: the data comes from a *different* repository's run directory, so
    the page must not make the bundle depend on that pipeline existing.
    """
    return _page("traceability.html")


@dataclass(frozen=True)
class _Traceability:
    """A run's findings, in the shape the report exporter expects.

    A small object rather than the whole `collect()` dict, because the
    exporter is pure and should not be handed a payload shaped by a page's
    needs. It is `None` when the project has no run, and the section is
    then skipped rather than printed empty.
    """

    findings: list[dict]
    totals: dict


def _traceability_for(project: str | None) -> _Traceability | None:
    """The run for one project, or None when it has not been traced.

    Never falls back to whatever run happens to exist. A report that
    silently carried another project's contradictions would be the worst
    document this product can produce.
    """
    if not project:
        return None
    run, _ = tracelink_view.run_for_project(project)
    if run is None:
        return None
    payload = tracelink_view.collect(run)
    if payload.get("project_id") != project:
        return None
    return _Traceability(findings=payload.get("findings") or [],
                         totals=payload.get("totals") or {})


def _code_check(project: str | None) -> dict | None:
    """The traceability run for one project, summarised for the insight screen.

    A summary, not the payload: `/api/traceability` already serves the whole
    run to the page built for it, and the insight screen wants the four numbers
    a delivery manager would ask for before deciding whether to open it.

    Returns `None` for a project with no run, which is most of them - the
    screen then shows nothing rather than an empty panel explaining that it is
    empty.
    """
    if not project:
        return None
    run, _ = tracelink_view.run_for_project(project)
    if run is None:
        return None
    payload = tracelink_view.collect(run)
    if payload.get("project_id") != project:
        return None

    totals = payload.get("totals") or {}
    ownership = payload.get("ownership") or {}
    # Only the fields with a gap. A field every row carries is a fact about the
    # sheet, not a finding, and listing it would bury the two that matter.
    gaps = [f for f in (ownership.get("fields") or []) if f.get("missing")]
    return {
        "rows": totals.get("tickets", 0),
        "corroborated": totals.get("corroborated", 0),
        "contradicted": totals.get("contradicted", 0),
        "unverified": totals.get("unverified", 0),
        "conflicts": totals.get("conflicts", 0),
        "ownership_gaps": gaps,
        "files": (payload.get("corpus") or {}).get("files", 0),
    }


def default_project_id() -> str:
    """Which project a request that names none is about.

    Eleven routes used to carry `project: str = "excel:Project:1:HRMS"` as a
    literal default. Two things were wrong with that. The demo project's id was
    compiled into the API, so deleting it would have left every unparameterised
    route answering about a project that no longer exists - with a 200. And the
    rail's picker independently fell back to the *first row of the portfolio*
    when nothing was stored, which is a different rule: the two agreed only
    because the seed project happens to be first in both orders. A picker that
    names one project while the numbers describe another is the worst failure
    this screen has, because nothing about it looks wrong.

    So there is now one rule, written once. It is deliberately not clever -
    first registered project - because a default that ranks or scores would
    move under the reader as the data changed, and "why did my dashboard open
    on a different project today" is a worse question than "why this one".
    """
    projects = scope.all_projects()
    if not projects:
        raise HTTPException(
            status_code=404,
            detail="No project has been imported yet. Add a source in Settings.",
        )
    return projects[0].canonical_id


def project_param(project: str | None = None) -> str:
    """`?project=`, or the default above - never a hard-coded id."""
    return project or default_project_id()


@app.get("/api/traceability")
def api_traceability(project: str | None = None) -> dict:
    """The traceability run for one delivery project.

    Scoped by canonical project id like every other screen (invariant 7): a
    run declares the project it is about, and asking for a project that has
    no run gets an empty answer naming the projects that do - not another
    project's findings, which is the one wrong answer available here.

    200 with `run: null` rather than 404 when a project has no run: the page
    needs the project list either way to draw its picker, and "this project
    has not been traced" is an answer, not a failure.
    """
    run, runs = tracelink_view.run_for_project(project)
    if run is None:
        return {
            "run": None,
            "project_id": project,
            "available": runs,
            "detail": (
                f"no traceability run is attached to project {project!r}"
                if project else
                "no traceability runs found. Set TRACELINK_RUNS to the directory "
                "holding them, e.g. TRACELINK_RUNS=../../traceability/runs, and "
                "give each run a --project-id when reading its backlog."
                if not runs else
                "several runs are available - ask for one by project."
            ),
        }
    return {**tracelink_view.collect(run), "available": runs}


@app.get("/api/traceability/rollup")
def api_traceability_rollup(project: str | None = None) -> dict:
    """Traceability folded onto the tracker keys the Schedule page draws.

    Separate from `/api/traceability` because the Schedule page needs only
    this summary - sending it 173 tickets and their evidence to render a
    per-bar count would be most of a megabyte for a dozen numbers.
    """
    run, runs = tracelink_view.run_for_project(project)
    if run is None:
        return {"parents": {}, "run": None,
                "detail": f"no traceability run for project {project!r}"}
    return {**tracelink_view.rollup_by_parent(run), "run": str(run)}


@app.get("/api/traceability/rows")
def api_traceability_rows(project: str | None = None) -> dict:
    """The feature rows, flat, for a table somebody filters.

    Between `/rollup` (a dozen numbers) and `/api/traceability` (most of a
    megabyte). The Schedule page listed every row it could not match as one
    semicolon-joined paragraph, which read as a sentence while the project had
    seventeen rows in it and as a wall of text the moment the real export went
    in at a hundred and ninety. A list that long is a table.
    """
    run, _ = tracelink_view.run_for_project(project)
    if run is None:
        return {"rows": [], "run": None,
                "detail": f"no traceability run for project {project!r}"}
    return {"rows": tracelink_view.feature_rows(run), "run": str(run)}


@app.get("/api/traceability/export")
def list_ticket_exports(project: str | None = None) -> dict:
    """Every stored backlog export, or one project's. Never the bytes.

    The size, never the blob - the same rule `app/imports.py` states for
    uploaded sheets. This runs on a page load, and selecting the entity would
    pull every workbook into memory to render a table showing how big they are.
    """
    from sqlalchemy import func, select

    from app.models.traceability import TicketExport

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    query = select(
        TicketExport.project_id,
        TicketExport.original_filename,
        func.length(TicketExport.content),
        TicketExport.project_filter,
        TicketExport.done_status,
        TicketExport.updated_at,
    ).order_by(TicketExport.project_id)
    if project:
        query = query.where(TicketExport.project_id == project)

    with session_scope() as session:
        rows = session.execute(query).all()

    return {"exports": [
        {
            "project_id": row[0],
            "original_filename": row[1] or "",
            "size": int(row[2] or 0),
            "project_filter": row[3] or "",
            "done_status": row[4] or "",
            "updated_at": row[5].isoformat() if row[5] else None,
        }
        for row in rows
    ]}


@app.post("/api/traceability/export")
async def save_ticket_export(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    project_filter: str = Form(""),
    done_status: str = Form(""),
) -> dict:
    """Store the backlog export a run for this project will be built from.

    Separate from `POST /api/sources/upload`, which takes the same file for a
    different purpose and *throws the original away*: it converts a Jira
    export into the schedule and worklog contracts and stores those. That is
    right for the dashboard, and useless to the pipeline - `governance` exists
    because the keyed PM rows carry prose the converted sheets do not keep.

    So this is the export as uploaded, byte for byte, and nothing here parses
    it. Validating it would mean reading it with the pipeline's own adapter,
    which is exactly what `tickets` does two minutes later with better error
    messages than this route could invent.
    """
    from app import admin, scope
    from app.models.traceability import TicketExport

    actor = admin.require(request)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    project_id = (project_id or "").strip()
    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="Choose which delivery project this backlog is for.",
        )
    if scope.find(project_id) is None:
        raise HTTPException(status_code=400, detail=f"unknown project {project_id!r}")

    if file.filename and not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail=f"{file.filename!r} is not an Excel workbook (.xlsx)",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="that file is empty")

    with session_scope() as session:
        session.merge(TicketExport(
            project_id=project_id,
            original_filename=file.filename or "",
            content=payload,
            project_filter=(project_filter or "").strip(),
            done_status=(done_status or "").strip(),
            updated_by=actor,
        ))

    return {
        "ok": True,
        "project_id": project_id,
        "original_filename": file.filename or "",
        "size": len(payload),
    }


@app.get("/api/traceability/run")
def traceability_run_status() -> dict:
    """What the pipeline is doing, or what it last did.

    Reports the analyser the corpus was built with, because a server missing
    CodeWiki produces a *complete* run with no dependency edges and no
    non-Python symbols, and nothing else on the page would say so - see
    `tracelink/corpus.py`.
    """
    from app import traceability_run

    payload = traceability_run.state()
    ok, why = _pipeline_ready()
    payload["available"] = ok
    payload["detail"] = why
    return payload


def _pipeline_ready() -> tuple[bool, str]:
    """Whether this server can run the pipeline at all, and why not.

    Both halves are checked - the pipeline and git - for the reason
    `app/ingest/sources/git/source.available` gives: they fail for unrelated
    reasons, and reporting only the first sends somebody to install the wrong
    thing.
    """
    from app.ingest.sources.git import source

    return source.available()


class TraceabilityRunIn(BaseModel):
    """Which delivery project to trace.

    Only the project: the repository, the branch and the documentation tree
    come from its registration, and the export's own options are stored with
    the export. Accepting them here too would give one run two sources of
    truth about what it was built from, and the run's manifest would record
    whichever this route happened to prefer.
    """

    project_id: str = ""


@app.post("/api/traceability/run")
def start_traceability_run(request: Request, body: TraceabilityRunIn) -> dict:
    """Clone, read the backlog, and produce a run the Traceability page reads.

    The order matters and is the same one `POST /api/repos` settled on: fetch
    the repository first, enforce its documentation tree, and only then start
    anything. A run begun against a repository nobody could reach would write
    a half directory the page then reports as a set of named gaps, which reads
    as "the pipeline is broken" rather than "the clone failed".

    Returns as soon as the run starts. It continues in a background thread;
    poll `GET /api/traceability/run` for progress. Only the *free* stages run
    - `translate`, `adjudicate` and `explain` cost money per ticket and are
    deliberately not wired to a button.
    """
    from app import admin, traceability_run
    from app.ingest.sources.git import source, store
    from app.llm.keys import KeyStoreUnavailable
    from app.models.traceability import TicketExport

    actor = admin.require(request)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    project_id = (body.project_id or "").strip()
    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="Choose which delivery project to trace.",
        )

    ready, why = _pipeline_ready()
    if not ready:
        raise HTTPException(status_code=503, detail=why)

    with session_scope() as session:
        row = store.for_project(session, project_id)
        if row is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"no repository is registered for {project_id!r}. The "
                    f"pipeline reads the code and the documents from one, so "
                    f"register it on Settings > Sources first."
                ),
            )
        registration = {
            "repo_url": row.repo_url, "ref": row.ref, "docs_path": row.docs_path,
        }
        try:
            token = store.token_for(row)
        except KeyStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

        export = session.get(TicketExport, project_id)
        if export is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"no backlog export is stored for {project_id!r}. The "
                    f"`tickets` and `governance` stages read the tracker "
                    f"export as a workbook - upload it first."
                ),
            )
        export_bytes = export.content
        export_name = export.original_filename
        project_filter = export.project_filter
        done_status = export.done_status

    # Fetched here rather than inside the run so a failure is *this* request's
    # 400, with the message `tracelink.source` wrote, instead of a line buried
    # in a log the caller has to go and poll for.
    try:
        fetched = source.fetch(
            repo_url=registration["repo_url"], ref=registration["ref"],
            docs_path=registration["docs_path"], token=token,
        )
    except source.PipelineMissing as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except (source.DocsMissing, source.RepoUnavailable) as exc:
        with session_scope() as session:
            store.record_failure(session, project_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from None

    try:
        started = traceability_run.start(
            project_id=project_id,
            repo_tree=fetched["tree"],
            docs_dir=fetched["docs_dir"],
            export_bytes=export_bytes,
            export_name=export_name,
            project_filter=project_filter,
            done_status=done_status,
            actor=actor,
        )
    except traceability_run.RunBusy as exc:
        # 409, not 400: the request is fine and will succeed later, which is a
        # different thing for a page to say than "you asked for the wrong one".
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except traceability_run.RunUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    return {"ok": True, "run": started, "commit": fetched["commit"],
            "doc_count": fetched["doc_count"]}


@app.get("/gantt")
def gantt_page() -> FileResponse:
    """The schedule view.

    Read-only by design - see `api/schemas/gantt.py` for why making it editable
    would cost the precision model.
    """
    return _page("gantt.html")


@app.get("/api/gantt", response_model=GanttBundle)
def api_gantt(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
) -> GanttBundle:
    """Tasks, milestones and dependency edges on one shared time window."""
    if also is None:
        # Whichever of this project's source ids the caller holds, resolved to
        # the canonical one plus the rest (invariant 7). Asking by the Jira id
        # used to analyse the Jira rows alone and present that as the project.
        project, also = scope.canonical_pairing(project)

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


#: The three downloadable formats. Markdown is `text/markdown` with an explicit
#: charset: task labels and owner names are not ASCII, and a browser that
#: guesses latin-1 renders them as mojibake in the preview tab.
MD_TYPE = "text/markdown; charset=utf-8"


def _as_block(block) -> ReportBlock:
    """An `exports.document.Block` as its wire form.

    A hand-written mapping rather than `dataclasses.asdict`: the schema is a
    contract the front end is generated from, and a field silently appearing on
    it because someone added one to the dataclass is how a contract stops being
    reviewed.
    """
    return ReportBlock(
        kind=block.kind,
        text=block.text,
        level=block.level,
        items=list(block.items),
        columns=list(block.columns),
        rows=[list(row) for row in block.rows],
        label=block.label,
    )


def _risk_count(session, project: str) -> int:
    """How many risks this project has, for the section's `available` flag."""
    from app.risks.service import list_risks

    return len(list_risks(session, project_ids=[project, *scope.also_for(project)]).risks)


def _report_doc(
    project: str,
    also: list[str] | None,
    preset: str | None,
    sections: list[str] | None,
):
    """Build the document once, for whichever renderer asked for it.

    Every format route and the preview go through here, so a `.docx` and the
    `.xlsx` beside it are the same document by construction rather than by two
    call sites being kept in step.

    Loading is lazy per section: a preview of the executive brief must not pay
    for the scenario search or a risk query it will not render.
    """
    from app.exports.document import build_document, resolve_sections

    if also is None:
        # Invariant 7 lives in `app.scope`, not in a literal here - a second
        # copy of the pairing is how the portfolio came to list HRMS twice.
        project, also = scope.canonical_pairing(project)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    chosen = resolve_sections(preset_id=preset, sections=sections)
    found = scope.find(project)

    with session_scope() as session:
        bundle = analyze_project(
            session, project_id=project, also=list(also), narrator=_narrator()
        )
        explain = (
            explain_project(session, project_id=project, also=list(also))
            if "projection" in chosen
            else None
        )
        scenarios = (
            scenarios_project(session, project_id=project, also=list(also))
            if "scenarios" in chosen
            else None
        )
        forecast = (
            forecast_project(session, project_id=project, also=list(also))
            if "forecast" in chosen
            else None
        )
        risks = None
        if "risks" in chosen:
            from app.risks.service import list_risks

            risks = list_risks(session, project_ids=[project, *also])
        drafts = None
        if "model_read" in chosen:
            #: A read, never a generation. Exporting a report must not call a
            #: model - it would make a download slow, cost money per click, and
            #: produce a document whose contents differ from the page the person
            #: was just looking at. What is in the report is what somebody has
            #: already generated on the Risk page.
            drafts = _draft_bundle(session, found.canonical_id if found else project)

    # The traceability run, when this project has one. Read outside the
    # session because it is files on disk, not rows - and read here rather
    # than inside `build_document` so the exporter keeps its promise of
    # touching no I/O and staying testable without any of this.
    traceability = None
    if "traceability" in chosen:
        traceability = _traceability_for(found.canonical_id if found else project)

    if not bundle.findings and not bundle.context.get("task_count"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"no data for project {project!r}. Build the demo timeline first: "
                "python -m scripts.replay"
            ),
        )

    return build_document(
        bundle,
        explain=explain,
        scenarios=scenarios,
        forecast=forecast,
        risks=risks,
        drafts=drafts,
        traceability=traceability,
        sections=chosen,
        project_name=found.name if found else "",
    )



@app.get("/api/explain", response_model=ExplainBundle)
def api_explain(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
) -> ExplainBundle:
    """The forward pass, with the working, for one project.

    The `/explain` *page* was deleted; this is not part of it. The Insight
    page's driving-path panel reads this bundle rather than recomputing the
    chain, and the report's projection section reads the same function - so
    removing it takes a panel off Insight silently, because that fetch
    degrades to `null` rather than erroring.
    """
    if also is None:
        project, also = scope.canonical_pairing(project)

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


@app.get("/api/forecast", response_model=ForecastBundle)
def forecast(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
) -> ForecastBundle:
    """A range of finish dates, resampled from this project's observed drift.

    Answers the question the forward pass deliberately does not: that one says
    where the chain lands if nothing else moves, and nothing else moving is the
    single assumption a delivery plan has never satisfied.

    **A refusal is a 200, not an error.** When the data cannot support a range
    the bundle comes back with `available: false` and a reason - too few
    baselined tasks, no variation between them, or nothing left to move. A 4xx
    would make the page render an error where the honest answer belongs.
    """
    if also is None:
        # Whichever of this project's source ids the caller holds, resolved to
        # the canonical one plus the rest (invariant 7). Asking by the Jira id
        # used to analyse the Jira rows alone and present that as the project.
        project, also = scope.canonical_pairing(project)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return forecast_project(session, project_id=project, also=list(also))

@app.get("/reports")
def reports_page() -> FileResponse:
    """The report builder: choose an audience, see it, download it."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/report/options", response_model=ReportOptions)
def report_options(project: str = Depends(project_param)) -> ReportOptions:
    """What the builder screen may offer, straight from the exporter.

    Served rather than hardcoded in the front end so that a section added to
    `exports/document.py` appears in the UI without a front-end change - and so
    the UI can never offer one the exporter does not know how to build.

    `available` is computed per project: a tick box for a section this project
    has no data for would produce a silently empty download, which reads as a
    broken feature rather than an empty register.
    """
    from app.exports.document import DEFAULT_PRESET, PRESETS, SECTIONS

    docx_available = importlib.util.find_spec("docx") is not None

    with session_scope() as session:
        risk_count = _risk_count(session, project)
        #: Drafts, like the register, exist only once somebody has made them -
        #: generation is a button on the Risk page, never automatic. Offering
        #: the tick box before then would download a heading over a sentence
        #: explaining that nothing has been proposed.
        from app.risks.service import list_drafts

        draft_count = len(list_drafts(session, project_ids=[project]))

    def available(requires: str) -> bool:
        # `explain` and `scenarios` are computed from the schedule, which any
        # analysable project has; `risks` is a register a person fills in, and
        # is genuinely empty until they do.
        if requires == "risks":
            return risk_count > 0
        if requires == "drafts":
            return draft_count > 0
        return True

    return ReportOptions(
        project_id=project,
        default_preset=DEFAULT_PRESET,
        presets=[
            ReportPresetOption(
                id=preset.id,
                label=preset.label,
                description=preset.description,
                sections=list(preset.sections),
            )
            for preset in PRESETS
        ],
        sections=[
            ReportSectionOption(
                id=spec.id,
                title=spec.title,
                description=spec.description,
                requires=spec.requires,
                available=available(spec.requires),
            )
            for spec in SECTIONS
        ],
        formats=[
            ReportFormatOption(
                id="md",
                label="Markdown",
                extension="md",
                description="Paste into an email, a wiki or a chat. Needs nothing to open.",
            ),
            ReportFormatOption(
                id="xlsx",
                label="Excel",
                extension="xlsx",
                description="Every table on its own sheet, filterable and sortable.",
            ),
            ReportFormatOption(
                id="docx",
                label="Word",
                extension="docx",
                description="The document to attach to an email.",
                available=docx_available,
                unavailable_reason=(
                    ""
                    if docx_available
                    else 'python-docx is not installed; pip install -e ".[report]"'
                ),
            ),
        ],
    )


@app.get("/api/report/preview", response_model=ReportPreview)
def report_preview(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
    template: str | None = None,
    section: list[str] | None = Query(default=None),
) -> ReportPreview:
    """The document as blocks - what every download will contain.

    Not a summary of the report and not a second rendering of the bundles: it
    is the same `ReportDoc` the file renderers walk. A preview built any other
    way is a preview that eventually disagrees with the file.
    """
    doc = _report_doc(project, also, template, section)
    return ReportPreview(
        title=doc.title,
        preamble=[_as_block(b) for b in doc.preamble],
        sections=[
            ReportSection(
                id=s.id, title=s.title, blocks=[_as_block(b) for b in s.blocks]
            )
            for s in doc.sections
        ],
        resolved_sections=[s.id for s in doc.sections],
    )


@app.get("/api/report.md")
def report_md(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
    template: str | None = None,
    section: list[str] | None = Query(default=None),
) -> Response:
    """The report as Markdown."""
    from app.exports.markdown import markdown_bytes

    return Response(
        content=markdown_bytes(_report_doc(project, also, template, section)),
        media_type=MD_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="delivery_status.md"'
        },
    )


@app.get("/api/report.xlsx")
def report_xlsx(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
    template: str | None = None,
    section: list[str] | None = Query(default=None),
) -> Response:
    """The report as a workbook, every table on its own filterable sheet."""
    from app.exports.workbook import workbook_bytes

    return Response(
        content=workbook_bytes(_report_doc(project, also, template, section)),
        media_type=XLSX_TYPE,
        headers={
            "Content-Disposition": 'attachment; filename="delivery_status.xlsx"'
        },
    )


@app.get("/api/report.docx")
def report(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
    template: str | None = None,
    section: list[str] | None = Query(default=None),
) -> Response:
    """The status report, as a .docx a PM can attach to an email.

    The same findings as `/api/insight` and the same projection as
    `/api/explain`, so the document cannot disagree with either screen - it
    renders their bundles rather than recomputing anything.
    """
    from app.exports.report import ReportUnavailable, render_docx

    doc = _report_doc(project, also, template, section)

    try:
        buffer = BytesIO()
        render_docx(doc).save(buffer)
        content = buffer.getvalue()
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


@app.get("/programs")
def programs_page() -> FileResponse:
    """The Programs list. Same shell as every other page; the app picks by path."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/projects")
def projects_page() -> FileResponse:
    """Every project, flat and searchable - pick one and open it.

    Distinct from `/programs`, which is the portfolio by program and reaches a
    project only through the program that owns it. This one exists for the
    other question: open the project I work on. It reads `/api/portfolio`, so
    a project no program claims - an uploaded one - is still reachable.
    """
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/programs", response_model=ProgramListBundle)
def programs_api() -> ProgramListBundle:
    """Every Program, with a project count and its worst project's band."""
    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return list_programs(session)


@app.get("/api/programs/{program_id}", response_model=ProgramRollupBundle)
def program_detail_api(program_id: str) -> ProgramRollupBundle:
    """One program's cross-project rollup: ranked projects, resources, and
    resource conflicts - the data behind the Program dashboard's tiles."""
    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        bundle = program_rollup(session, program_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"no program {program_id!r}")
        return bundle


@app.post("/api/programs", response_model=CreatedProgram, status_code=201)
def create_program_route(body: ProgramIn) -> CreatedProgram:
    """Create a program from the Programs tab.

    Until now a program could only arrive from the built-in seed or be invented
    by a collector mid-ingest, which is how one program came to exist under two
    ids. Creating one is a deliberate act with a name attached, so it belongs on
    a form rather than as a side effect of parsing somebody's spreadsheet.

    The id is derived from the name, never accepted from the caller - see
    `ProgramIn`. Re-posting a name that maps to an existing program renames it
    in place and reports `existed: true`, rather than creating a near-duplicate
    a person cannot tell apart on the list.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="a program needs a name")

    program_id = scope.program_domain_id(scope.slugify(name).upper())
    existed = scope.find_program(program_id) is not None

    entry = scope.register_program(
        name, owner=(body.owner or "").strip() or None, status=body.status or "Active"
    )
    return CreatedProgram(
        program_id=entry.program_id,
        name=entry.name,
        owner=entry.owner,
        status=entry.status,
        existed=existed,
    )


@app.post("/api/projects", response_model=CreatedProject, status_code=201)
def create_project_route(body: ProjectIn) -> CreatedProject:
    """Create a project from the Projects tab, with no document yet.

    A project used to exist only as a side effect of uploading a sheet for it,
    so a PM could not set the portfolio up before the documents arrived. One
    registered here and never ingested is correct and shows as `no_data` - it is
    a project somebody has told us about, not one we have seen a sheet for.

    The canonical id uses the same `scope.slugify` the upload route uses, on
    purpose: uploading a schedule later for the same name fills *this* project
    in rather than creating a second one beside it.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="a project needs a name")

    program_id = (body.program_id or "").strip() or None
    # An unknown program is refused, never created. A typo that silently invents
    # a program is how a portfolio grows rows nobody meant.
    if program_id is not None and scope.find_program(program_id) is None:
        raise HTTPException(status_code=400, detail=f"unknown program {program_id!r}")

    canonical_id = f"excel:Project:upload:{scope.slugify(name)}"
    found = scope.find(canonical_id)
    existed = found is not None

    # Keep whatever pairing the project already had: a re-post that dropped
    # `also` would split a two-source project back into two (invariant 7).
    entry = scope.register(
        canonical_id,
        name,
        also=found.also if found is not None else (),
        program_id=program_id,
    )
    return CreatedProject(
        canonical_id=entry.canonical_id,
        name=entry.name,
        program_id=entry.program_id,
        existed=existed,
    )


@app.get("/api/projects/{project_id}/removal")
def preview_project_removal(project_id: str) -> dict:
    """What removing this project would destroy, without removing anything.

    Its own request so a confirmation can name real numbers rather than a
    generic warning. "This deletes 17 tasks and 2 risks you typed by hand" is a
    decision; "are you sure?" is a reflex.
    """
    from app.projects import plan

    with session_scope() as session:
        outcome = plan(session, project_id)
        return {
            "project_id": outcome.project_id,
            "name": outcome.name,
            "removable": outcome.removable,
            "reason": outcome.reason,
            "source_ids": list(outcome.source_ids),
            "counts": outcome.counts,
            "irreplaceable": outcome.irreplaceable,
        }


@app.delete("/api/projects/{project_id}")
def remove_project_route(request: Request, project_id: str) -> dict:
    """Remove a project and everything that was only ever about it.

    There is no undo and nothing rebuilds a risk somebody typed or a board
    somebody arranged, so the reason to refuse is served as a sentence rather
    than a status code alone - see `app/projects.py` for what is deliberately
    left behind.

    Gated for that same reason. "No undo" and "anybody on the internet may
    call it" are not two facts that belong in one route: until this, a stranger
    could empty the deployment a project at a time. Loopback still passes with
    no token, so local development is unchanged.
    """
    from app import admin
    from app.projects import remove

    admin.require(request)

    with session_scope() as session:
        try:
            outcome = remove(session, project_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "project_id": outcome.project_id,
        "name": outcome.name,
        "removed": outcome.counts,
    }


@app.get("/team")
def team_page() -> FileResponse:
    """Who is carrying what, and what moved."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/team", response_model=TeamBundle)
def team(
    project: str = Depends(project_param),
    also: list[str] | None = Query(default=None),
) -> TeamBundle:
    """Workload, effort and activity, from columns the sheets actually carry.

    `api/schemas/team.py` records what is served and what is deliberately
    absent. The effort burn is reconstructed from observed `hours_spent`
    changes rather than read off the final sheet, which is what lets a flat
    stretch in it mean "nothing was logged" instead of "we stopped looking".
    """
    if also is None:
        # Whichever of this project's source ids the caller holds, resolved to
        # the canonical one plus the rest (invariant 7). Asking by the Jira id
        # used to analyse the Jira rows alone and present that as the project.
        project, also = scope.canonical_pairing(project)

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


@app.get("/imports")
def imports_page() -> FileResponse:
    """What has been imported, what it feeds, and what reads it."""
    return _page("imports.html")


@app.get("/api/imports")
def read_imports(request: Request) -> dict:
    """Every import with its project, its availability and its consumers.

    Readable without a token - it discloses no credential and no content, only
    what exists and what uses it. The actions below are gated.
    """
    from app import admin
    from app.imports import inventory, orphan_projects

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        rows = inventory(session)
        orphans = orphan_projects(session)

    return {
        "imports": [
            {
                "file_name": r.file_name,
                "origin": r.origin,
                "kind": r.kind,
                "sheet_name": r.sheet_name,
                "project_id": r.project_id,
                "project_name": r.project_name,
                "program_id": r.program_id,
                "program_name": r.program_name,
                "original_filename": r.original_filename,
                "last_scan": r.last_scan.isoformat() if r.last_scan else None,
                "rows": r.rows,
                "size_bytes": r.size_bytes,
                "available": r.available,
                "problem": r.problem,
                "ingested": r.ingested,
                "task_count": r.task_count,
                "risk_count": r.risk_count,
                "consumers": [{"page": c.page, "uses": c.uses} for c in r.consumers],
            }
            for r in rows
        ],
        "orphan_projects": orphans,
        "writable": admin.may_write(request),
        "auth": admin.status(),
    }


@app.delete("/api/imports/{file_name}")
def delete_import(request: Request, file_name: str) -> dict:
    """Forget one import.

    Deleting the `uploaded_sheets` row removes the workbook *and* the watch in
    one step, because that row is both - `source._load_registered()` builds the
    watch list from this table. There is no second registry to fall out of step
    with it, which is why this is one delete rather than two.

    **What it leaves alone is the point.** The tasks it produced stay, so
    removing a superseded upload does not empty the pages built from it. To
    remove those too, delete the project - a separate decision, and one the
    page states separately.
    """
    from app import admin
    from app.models.uploads import UploadedSheet

    admin.require(request)

    with session_scope() as session:
        row = session.get(UploadedSheet, file_name)
        if row is None:
            # A demo-seed sheet is compiled into `source._SEED`, not stored, so
            # there is nothing to delete and saying "not found" would be a
            # worse answer than saying why.
            raise HTTPException(
                status_code=404,
                detail=(
                    f"{file_name!r} is not an upload. Sheets that ship with the "
                    "demo are part of the image and cannot be removed here."
                ),
            )
        session.delete(row)

    return {"deleted": file_name}


class JiraProbeIn(BaseModel):
    """Credentials for one connection test. Never persisted."""

    site: str = ""
    email: str = ""
    token: str = ""


@app.post("/api/jira/test")
def test_jira_connection(request: Request, body: JiraProbeIn) -> dict:
    """Ask one Jira whether this credential works, and store nothing.

    Admin-gated for two reasons, and the second is the real one. It spends
    an outbound request, and more importantly it makes *this server* fetch
    a URL somebody typed - `connect.normalise_site` refuses anything that
    resolves onto our own network, but the ability to aim the server at all
    is not something to hand out unauthenticated.

    The token is used to build one header and is never written down: not to
    the database, not to the log, and not into any message this returns.
    """
    from app import admin
    from app.ingest.sources.jira import connect

    admin.require(request)

    try:
        result = connect.probe(body.site, body.email, body.token)
    except connect.ConnectionRefused as exc:
        # A refusal to attempt is a 400 about the input, not a failed test -
        # the page shows them differently because they need different fixes.
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return result.as_dict()


class JiraPreviewIn(JiraProbeIn):
    """The same credentials, plus the project to look at."""

    project: str = ""
    sample: int = 25


@app.post("/api/jira/preview")
def preview_jira_project(request: Request, body: JiraPreviewIn) -> dict:
    """What this Jira would give us for one project, before collecting any.

    Same gate and same no-storage rule as the connection test. Reports
    field coverage over a sample rather than a yes/no: "Jira has a created
    date" and "this instance fills it in" are different claims, and only
    the second one decides whether a live collector is worth building.
    """
    from app import admin
    from app.ingest.sources.jira import connect

    admin.require(request)

    try:
        return connect.preview(body.site, body.email, body.token,
                               body.project, min(int(body.sample or 25), 50))
    except connect.ConnectionRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


class JiraLinkIn(BaseModel):
    """One link between a delivery project and a Jira project."""

    project_id: str = ""
    site: str = ""
    email: str = ""
    project_key: str = ""
    #: Blank on an edit keeps the stored credential.
    token: str = ""


@app.get("/api/jira/connections")
def list_jira_connections(project: str | None = None) -> dict:
    """Every Jira link, or one project's. Never returns a credential.

    Readable without a token: it discloses a site, a project key and a
    masked hint, which is what the Settings list needs to render. The
    writes below are gated.
    """
    from app.ingest.sources.jira import store

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        return {"connections": store.listing(session, project)}


@app.post("/api/jira/connections")
def save_jira_connection(request: Request, body: JiraLinkIn) -> dict:
    """Link a delivery project to a Jira project, credential and all."""
    from app import admin
    from app.ingest.sources.jira import connect, store

    actor = admin.require(request)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    from app.llm.keys import KeyStoreUnavailable

    try:
        with session_scope() as session:
            row = store.save(
                session, project_id=body.project_id, site=body.site,
                email=body.email, project_key=body.project_key,
                token=body.token, actor=actor,
            )
            session.flush()
            saved = {"id": row.id, "project_id": row.project_id,
                     "site": row.site, "project_key": row.project_key,
                     "hint": row.hint}
    except KeyStoreUnavailable as exc:
        # A deployment that has not opted in, not a bad request. The
        # message already names the command that fixes it, and losing it
        # to a 500 is how this first failed: the page showed a JSON parse
        # error about the words "Internal Server Error".
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except connect.ConnectionRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, **saved}


@app.delete("/api/jira/connections/{connection_id}")
def delete_jira_connection(request: Request, connection_id: int) -> dict:
    """Forget one link.

    The rows it already collected stay, the same rule `delete_import`
    keeps: removing a source does not empty the pages built from it.
    """
    from app import admin
    from app.ingest.sources.jira import store

    admin.require(request)

    with session_scope() as session:
        if not store.delete(session, connection_id):
            raise HTTPException(status_code=404, detail="no such connection")
    return {"deleted": connection_id}


class ProjectRepoIn(BaseModel):
    """Where one delivery project's code and documents come from."""

    project_id: str = ""
    #: A clone URL, or a path on this host for a local run.
    repo_url: str = ""
    #: Branch or tag. Blank takes the remote's default.
    ref: str = ""
    #: The documentation tree, relative to the repository root. The lock -
    #: see `app/ingest/sources/git/source.py`.
    docs_path: str = "docs"
    #: Blank on an edit keeps the stored credential, as `JiraLinkIn` does.
    token: str = ""
    #: Explicitly drop the stored credential. Needed because blank means
    #: keep: without this there is no way to say "this repository is public
    #: now" and stop sending a token to a host that no longer needs one.
    clear_token: bool = False


@app.get("/api/repos")
def list_project_repos(project: str | None = None) -> dict:
    """Every registered repository, or one project's. Never a credential.

    Reports whether this server can fetch at all alongside the rows, so a
    deployment missing git or the pipeline says so on the page instead of
    only when somebody presses the button.
    """
    from app.ingest.sources.git import source, store

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    ok, reason = source.available()
    with session_scope() as session:
        return {
            "repos": store.listing(session, project),
            "can_fetch": ok,
            "reason": reason,
        }


@app.post("/api/repos")
def save_project_repo(request: Request, body: ProjectRepoIn) -> dict:
    """Register a repository against a project, after actually reading it.

    The order is the whole point: clone, enforce the documentation tree,
    and only then write the row. A registration therefore always names a
    commit this server read and a documentation tree it walked - and a
    repository that cannot be reached, or has no documents, leaves nothing
    behind. That is the fifth defect in CLAUDE.md section 0a, which was a
    refused import still leaving a project on the portfolio.
    """
    from app import admin
    from app.ingest.sources.git import source, store
    from app.llm.keys import KeyStoreUnavailable

    actor = admin.require(request)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    project_id = (body.project_id or "").strip()
    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="Choose which delivery project this repository is for.",
        )
    if not (body.repo_url or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Give a clone URL, such as https://github.com/org/repo.git",
        )

    docs_path = store.normalise_docs_path(body.docs_path)

    # Blank keeps, `clear_token` drops. See `ProjectRepoIn`.
    if body.clear_token:
        token: str | None = ""
    else:
        token = body.token.strip() or None

    # Which credential the fetch runs with: the one being saved, or the one
    # already stored when this is an edit that left the field alone.
    with session_scope() as session:
        existing = store.for_project(session, project_id)
        try:
            if token:
                secret = token
            elif token == "" or existing is None:
                secret = ""
            else:
                secret = store.token_for(existing)
        except KeyStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    try:
        fetched = source.fetch(
            repo_url=body.repo_url.strip(), ref=body.ref.strip(),
            docs_path=docs_path, token=secret,
        )
    except source.PipelineMissing as exc:
        # A deployment that cannot do this at all, not a bad request.
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except source.DocsMissing as exc:
        # The lock. Names what it looked for and what is there.
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except source.RepoUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    try:
        with session_scope() as session:
            row = store.save(
                session, project_id=project_id,
                repo_url=body.repo_url.strip(), ref=body.ref.strip(),
                docs_path=docs_path, token=token, actor=actor,
                fetched=fetched,
            )
            session.flush()
            saved = {
                "project_id": row.project_id,
                "repo_url": row.repo_url,
                "ref": row.ref,
                "docs_path": row.docs_path,
                "commit": row.commit,
                "short_commit": row.commit[:12] if row.commit else "",
                "dirty": row.dirty,
                "doc_count": row.doc_count,
                "private": bool(row.ciphertext),
                "hint": row.hint,
            }
    except KeyStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, **saved}


@app.post("/api/repos/{project_id:path}/refresh")
def refresh_project_repo(request: Request, project_id: str) -> dict:
    """Re-read a registered repository at its ref.

    A fetch rather than a clone - `tracelink.source.resolve` moves the
    cached checkout instead of downloading it again. The interesting
    answer is `moved`: whether the commit changed since the last read,
    which is what tells somebody a run built from this repository is now
    describing code that is no longer there.
    """
    from app import admin
    from app.ingest.sources.git import source, store
    from app.llm.keys import KeyStoreUnavailable

    admin.require(request)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        row = store.for_project(session, project_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"no repository is registered for {project_id!r}",
            )
        before = row.commit
        spec = {"repo_url": row.repo_url, "ref": row.ref,
                "docs_path": row.docs_path}
        try:
            secret = store.token_for(row)
        except KeyStoreUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    try:
        fetched = source.fetch(token=secret, **spec)
    except (source.PipelineMissing, source.DocsMissing,
            source.RepoUnavailable) as exc:
        # The row survives a failed refresh and remembers why, which is
        # the case `record_failure` exists for: the refresh that fails is
        # usually the one nobody was watching.
        with session_scope() as session:
            store.record_failure(session, project_id, str(exc))
        status = 503 if isinstance(exc, source.PipelineMissing) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from None

    with session_scope() as session:
        row = store.save(
            session, project_id=project_id, repo_url=spec["repo_url"],
            ref=spec["ref"], docs_path=spec["docs_path"], token=None,
            fetched=fetched,
        )
        session.flush()
        now = row.commit
        docs = row.doc_count

    return {
        "ok": True,
        "project_id": project_id,
        "commit": now,
        "short_commit": now[:12] if now else "",
        "moved": bool(before) and before != now,
        "previous_commit": before,
        "doc_count": docs,
    }


@app.delete("/api/repos/{project_id:path}")
def delete_project_repo(request: Request, project_id: str) -> dict:
    """Forget one repository registration.

    Anything already derived from it stays, the same rule `delete_import`
    keeps: removing a source does not empty the pages built from it.
    """
    from app import admin
    from app.ingest.sources.git import store

    admin.require(request)

    with session_scope() as session:
        if not store.delete(session, project_id):
            raise HTTPException(
                status_code=404,
                detail=f"no repository is registered for {project_id!r}",
            )
    return {"deleted": project_id}



@app.get("/jira")
def jira_page() -> FileResponse:
    """What has been collected from Jira, as rows."""
    return _page("jira.html")


@app.get("/api/jira/issues")
def read_jira_issues(project_key: str | None = None, limit: int = 500) -> dict:
    """Collected Jira issues, straight from the tool layer.

    Deliberately the tool layer and not the domain: this screen exists to
    show what Jira actually said, before any of this product's naming or
    status mapping is applied. A row here disagreeing with the Schedule
    page is a finding about the mapping, and flattening the two would hide
    exactly that.

    Read-only and unauthenticated: it discloses issue summaries, which the
    tracker already shows everyone who can open it.
    """
    from app.models.tool import ToolJiraIssue

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    def note_of(raw: bytes | str | None) -> str | None:
        """The issue's description, out of the raw payload we already kept.

        `ToolJiraIssue` has no description column, and adding one would mean a
        migration plus a re-collection from a Jira this server cannot reach.
        It does not need one: `_raw_jira_issues.data` is the response body
        exactly as Jira sent it, and `_raw_data_id` on the tool row is the
        pointer to it - the same pointer the evidence panel follows. Reading
        it here costs one join and invents nothing.

        Jira returns this field as a plain string on some instances and as an
        Atlassian Document Format tree on others. Only the string case is
        rendered; an ADF body is reported as present rather than flattened,
        because a lossy flatten printed in a table reads as the note itself.
        """
        if raw is None:
            return None
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            return None
        description = (body.get("fields") or {}).get("description")
        if isinstance(description, str):
            text = description.strip()
            return text or None
        if isinstance(description, dict):
            return "(rich text - open in Jira)"
        return None

    with session_scope() as session:
        query = select(ToolJiraIssue)
        if project_key:
            query = query.where(ToolJiraIssue.project_key == project_key.strip().upper())
        query = query.order_by(ToolJiraIssue.updated_at_src.desc()).limit(
            max(1, min(int(limit), 2000))
        )
        rows = session.scalars(query).all()
        keys = sorted({r.project_key for r in session.scalars(
            select(ToolJiraIssue)).all() if r.project_key})

        # One query for the payloads these rows point at, rather than one per
        # row. The table is the raw response bodies and they are large.
        raw_ids = [r.raw_data_id for r in rows if r.raw_data_id is not None]
        notes: dict[int, str | None] = {}
        if raw_ids:
            found = session.execute(
                text(
                    "SELECT id, data FROM _raw_jira_issues WHERE id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": raw_ids},
            ).all()
            notes = {int(rid): note_of(data) for rid, data in found}

        return {
            "project_keys": keys,
            "issues": [
                {
                    "issue_key": r.issue_key,
                    "project_key": r.project_key,
                    "summary": r.summary,
                    "issue_type": r.issue_type,
                    "status": r.status,
                    "assignee": r.assignee,
                    "created": r.created_at_src.isoformat() if r.created_at_src else None,
                    "updated": r.updated_at_src.isoformat() if r.updated_at_src else None,
                    "due_date": r.due_date,
                    "story_points": r.story_points,
                    # What the team wrote on the ticket. Empty on most rows in
                    # a backlog imported from a spreadsheet, which is itself
                    # worth seeing: the page says how many carry one.
                    "note": notes.get(r.raw_data_id) if r.raw_data_id else None,
                }
                for r in rows
            ],
        }


@app.post("/api/jira/connections/{connection_id}/collect")
def collect_jira_connection(request: Request, connection_id: int) -> dict:
    """Pull one linked Jira project into the raw tables, then extract it.

    Incremental by construction: `live.collect` reads its watermark from
    the rows it wrote last time, so the first call collects the project
    and every later one collects the changes.

    Synchronous on purpose for now. A first collection of a large project
    is slow - pages are paced to stay under the rate limiter - and a
    background job that fails silently is worse than a request that takes
    a minute and says what happened.
    """
    from datetime import datetime, timezone

    from app import admin
    from app.ingest.sources.jira import convertor, live, store
    from app.ingest.sources.jira.extractor import extract_changelogs, extract_issues
    from app.models.jira import JiraConnection

    admin.require(request)

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    with session_scope() as session:
        row = session.get(JiraConnection, connection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such connection")
        try:
            token = store.token_for(row)
        except store.NoCredential as exc:
            # A link with no token is a deliberate state, not a fault.
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - a sealed row we cannot read
            raise HTTPException(
                status_code=503,
                detail=("this connection was sealed with a different "
                        f"PULSE_SECRET_KEY and cannot be read: {exc}"),
            ) from None

        try:
            report = live.collect(
                session, connection_id=row.id, project_key=row.project_key,
                site=row.site, email=row.email, token=token,
                now=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        except ConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        session.flush()

        # Straight on into the tool layer, so the Jira page has rows to
        # show rather than raw JSON nobody can read.
        issues = extract_issues(session, connection_id=row.id)
        changes = extract_changelogs(session, connection_id=row.id)
        session.flush()

        # ...and on into the domain, which is what every other page reads.
        # Stopping at the tool layer is why the first collection filled the
        # Jira tab and changed nothing anywhere else.
        jira_project = convertor.ensure_project(
            session, connection_id=row.id, project_key=row.project_key,
            name=row.project_key,
        )
        session.flush()
        tasks = convertor.convert_issues(
            session, connection_id=row.id, project_id=jira_project,
            project_key=row.project_key)
        state_changes = convertor.convert_changelogs(
            session, connection_id=row.id, project_key=row.project_key,
            now=datetime.now(timezone.utc).replace(tzinfo=None))
        session.flush()
        # Jira has no start date, so the workload window and the Gantt had
        # nothing to draw. The changelog does know - see `derive_start_dates`.
        convertor.derive_start_dates(session, project_id=jira_project)
        convertor.derive_end_dates(session, project_id=jira_project)
        delivery_id, jira_source_id = row.project_id, jira_project

    # Pair the two source ids onto one delivery project - invariant 7. The
    # convertor mints its own `jira:Project:...` id, so without this the
    # portfolio grows a second project with the same work in it, and the
    # spreadsheet's rows and Jira's rows describe one project from two
    # places that never meet.
    paired = _pair_source(delivery_id, jira_source_id)

    return {"ok": True, "project_key": row.project_key,
            "extracted_issues": issues, "extracted_changes": changes,
            "tasks": tasks, "state_changes": state_changes,
            "jira_project_id": jira_source_id, "paired_with": paired,
            **report.as_dict()}


def _pair_source(delivery_id: str, source_id: str) -> str | None:
    """Add `source_id` to the delivery project's `also` list, idempotently.

    Returns the delivery project it was paired onto, or None when that
    project is not registered - which is not a failure: an unregistered
    project simply has no pairing row to extend, and the Jira rows stand
    on their own until somebody registers one.
    """
    from app import scope

    known = scope.find(delivery_id)
    if known is None:
        return None
    also = list(known.also)
    if source_id not in also and source_id != delivery_id:
        also.append(source_id)
        scope.register(delivery_id, known.name, tuple(also), known.program_id)
    return delivery_id


class JiraIngestIn(BaseModel):
    """Issues collected elsewhere, for a server that cannot collect them."""

    project_key: str = ""
    #: Whole Jira issue objects, `changelog` still attached, exactly as the
    #: search endpoint returned them.
    issues: list[dict] = []
    #: What the collector recorded as the source URL, kept for provenance.
    url: str = ""


@app.post("/api/jira/ingest")
def ingest_jira_issues(request: Request, body: JiraIngestIn) -> dict:
    """Accept issues somebody else fetched, then run the ordinary pipeline.

    This exists because of a network fact, not a design preference: the
    Jira this was built against sits behind bot scoring that lets an
    ordinary laptop through and refuses a request from a data centre, so
    a hosted server cannot collect for itself. Measured both ways -
    `/myself` answers 401 from a laptop and 403 with a challenge page
    from Fly.

    Only the *fetch* moves. The issues are written into the same raw
    tables `live.collect` writes, and extract, convert and pairing run
    here exactly as they would have - so the evidence trail, the
    tool-layer rows and the exact-precision state changes are identical
    to a collection that happened on this machine.

    When the block is lifted, the Collect button starts working and this
    becomes unnecessary rather than becoming load-bearing.
    """
    import json as _json
    from datetime import datetime, timezone

    from app import admin
    from app.ingest.sources.jira import convertor
    from app.ingest.sources.jira.extractor import extract_changelogs, extract_issues
    from app.models.jira import JiraConnection
    from app.models.raw import RawJiraChangelogs, RawJiraIssues

    admin.require(request)

    key = (body.project_key or "").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="project_key is required")
    if not body.issues:
        raise HTTPException(status_code=400, detail="no issues in the payload")

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with session_scope() as session:
        # The connection is what binds these rows to a delivery project.
        # Without one the issues would land with no way to pair them, so
        # this refuses rather than importing rows nothing can reach.
        row = session.scalar(
            select(JiraConnection).where(JiraConnection.project_key == key)
        )
        if row is None:
            raise HTTPException(
                status_code=400,
                detail=(f"no Jira connection on this server names {key!r}. "
                        f"Save one on Settings first - it is what binds "
                        f"these issues to a delivery project."),
            )

        params = _json.dumps({"connection_id": row.id, "board_key": key})
        url = body.url or f"{row.site}/rest/api/2/search"
        issues = changelogs = 0
        for issue in body.issues:
            changelog = issue.pop("changelog", {}) or {}
            session.add(RawJiraIssues(
                params=params, data=_json.dumps(issue).encode("utf-8"),
                url=url, input=None, fetched_at=now))
            issues += 1
            for history in changelog.get("histories", []):
                session.add(RawJiraChangelogs(
                    params=params,
                    data=_json.dumps(history).encode("utf-8"),
                    url=f"{row.site}/rest/api/2/issue/{issue.get('id')}/changelog",
                    input=_json.dumps({"issue_id": issue.get("id"),
                                       "issue_key": issue.get("key")}),
                    fetched_at=now))
                changelogs += 1
        session.flush()

        extracted = extract_issues(session, connection_id=row.id)
        changes_extracted = extract_changelogs(session, connection_id=row.id)
        session.flush()

        jira_project = convertor.ensure_project(
            session, connection_id=row.id, project_key=key, name=key)
        session.flush()
        tasks = convertor.convert_issues(
            session, connection_id=row.id, project_id=jira_project,
            project_key=key)
        state_changes = convertor.convert_changelogs(
            session, connection_id=row.id, project_key=key, now=now)
        session.flush()
        convertor.derive_start_dates(session, project_id=jira_project)
        convertor.derive_end_dates(session, project_id=jira_project)
        delivery_id = row.project_id

    paired = _pair_source(delivery_id, jira_project)

    return {"ok": True, "project_key": key, "received": issues,
            "changelogs": changelogs, "extracted_issues": extracted,
            "extracted_changes": changes_extracted, "tasks": tasks,
            "state_changes": state_changes, "paired_with": paired}


@app.get("/api/jira/watermark")
def jira_watermark(project_key: str) -> dict:
    """The newest `updated` this server already holds for one project.

    What the sync tool needs to stop re-downloading a project it has
    already sent. `live.collect` has had this since it was written - it
    reads its own watermark from the raw table - and the PowerShell tool
    never did, so every run fetched every issue with its full changelog
    and a project large enough to trip Jira's rate limiter could not be
    collected at all: each attempt re-read everything and banked none of
    it.

    Read from `updated_at_src` - the timestamp *Jira* put on the issue -
    and not from `fetched_at`, because the JQL this feeds is
    `updated >= …`. Those two answer different questions, and using the
    collection time would skip anything edited while a slow collection
    was running.

    Ungated on purpose: it is one timestamp about a project the caller
    has to name, it decloses nothing an issue list would not, and the tool
    asks for it before it has any reason to hold a credential.
    """
    from sqlalchemy import func, select as _select

    from app.models.tool import ToolJiraIssue

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    key = (project_key or "").strip().upper()
    if not key:
        raise HTTPException(status_code=400, detail="name a project_key")

    mine = func.upper(ToolJiraIssue.project_key) == key
    with session_scope() as session:
        newest = session.scalar(
            _select(func.max(ToolJiraIssue.updated_at_src)).where(mine)
        )
        # Reported so the tool can say "42 already there" - a person seeing
        # an incremental run finish in two seconds needs to know it added to
        # a set rather than replaced one with nothing.
        held = int(session.scalar(
            _select(func.count()).select_from(ToolJiraIssue).where(mine)
        ) or 0)

    return {
        "project_key": key,
        # Jira's JQL wants minute precision: `updated >=` is inclusive, so a
        # second-precision bound re-collects the boundary issue every run.
        # `live._jql` uses the same format for the same reason.
        "since": newest.strftime("%Y-%m-%d %H:%M") if newest else "",
        "issues_held": held,
    }


@app.get("/api/jira/sync-tool")
def download_jira_sync_tool(request: Request, project_key: str) -> Response:
    """A one-file PowerShell script that syncs one project, pre-filled.

    The person who runs this is not the person who built the app. They
    have a browser and a work laptop, so the tool has to be something
    Windows can already run - no Python, no install, no build step - and
    it has to arrive knowing the server, the Jira site and the project,
    because every field somebody has to fill in is a field they can get
    wrong while a demo waits.

    **It carries this server's admin token**, which is what lets it post
    what it collected. That is a real disclosure: whoever holds the file
    can write to this instance until the token is rotated. Gated behind
    the same token so only somebody who already has it can mint one, and
    worth rotating after a demo rather than leaving it in a Downloads
    folder forever.
    """
    from app import admin
    from app.models.jira import JiraConnection

    admin.require(request)

    key = (project_key or "").strip().upper()
    with session_scope() as session:
        row = session.scalar(
            select(JiraConnection).where(JiraConnection.project_key == key)
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=(f"no Jira connection names {key!r}. Save one on "
                        f"Settings first - the tool needs to know which "
                        f"delivery project these issues belong to."),
            )
        site = row.site

    template = (STATIC / "sync-tool.ps1.tmpl").read_text(encoding="utf-8")
    script = (template
              .replace("__SERVER__", str(request.base_url).rstrip("/"))
              .replace("__JIRA_SITE__", site)
              .replace("__PROJECT_KEY__", key)
              .replace("__PUSH_TOKEN__", os.environ.get(admin.TOKEN_ENV, "").strip()))

    # CRLF, normalised here rather than trusted from the file on disk.
    #
    # This is a batch file before it is anything else, and `cmd.exe` parses
    # batch line by line with carriage returns in mind: a LF-only `.cmd` is
    # read in ways that range from working to silently skipping lines. The
    # template is edited on whichever machine last touched it and served from
    # an image built on Linux, so its endings are not something to assume -
    # the measured download had LF throughout.
    script = script.replace("\r\n", "\n").replace("\n", "\r\n")

    # `.cmd`, not `.ps1`. Windows refuses to run a downloaded `.ps1` on a
    # default install - twice over, for the execution policy and for the mark
    # of the web - and the window closes before either message can be read.
    # The person this is built for has a browser and a work laptop, and may
    # not be allowed to change the policy on it. A `.cmd` double-clicks.
    return Response(
        content=script,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="sync-{key.lower()}.cmd"'},
    )


@app.post("/api/imports/resync")
def resync_imports(request: Request) -> dict:
    """Re-read every watched sheet through the ordinary reader, differ and rules.

    All of them rather than one: `run_sync` scans the whole source, and there
    is no per-sheet entry point to pretend otherwise with. A sheet whose last
    scan failed, or one whose rules have changed since, is re-run by this.
    """
    from app import admin
    from app.ingest.runner import run_sync

    admin.require(request)

    try:
        with session_scope() as session:
            outcome = run_sync(session, "excel", "manual")
    except Exception as exc:  # noqa: BLE001 - report it rather than 500
        raise HTTPException(
            status_code=400, detail=f"{type(exc).__name__}: {exc}"
        ) from None

    return {"resynced": True, "outcome": str(outcome)[:400]}


@app.post("/api/sources/upload")
async def upload_source(
    file: UploadFile = File(...),
    sheet_kind: str = Form(...),
    project_name: str = Form(""),
    project_id: str = Form(""),
    program_id: str = Form(""),
) -> dict:
    """Ingest one Excel sheet for a project, from a file picked in the browser.

    The alternative to the OneDrive-synced folder `scripts.sync` reads - and
    the only ingestion path that works on a deployed host, which has no such
    folder. Saves the file into `settings.data_root` under a name derived
    from the project rather than the upload's own filename (stable across
    re-uploads, which is what a `WatchedSheet.file_name` needs to be for the
    differ to find last time's baseline - see `transport.py`), registers it,
    and runs the same sync a PM's button does.

    ⚠️ Ephemeral on a host with no attached volume: the file and the
    registration live on local disk and do not survive a redeploy to a new
    machine, only a restart of the same one. What gets ingested into the
    database from it does survive - only *re*-ingesting a later edit to the
    same document would need another upload.
    """
    from app.ingest.sources.excel.reader import find_sheet
    from app.ingest.sources.excel.source import SHEET_KINDS, register_watched
    from app.models.tool import ToolExcelRow

    kind = sheet_kind.strip().lower()
    #: A Jira export is not a sheet kind - it is a *format* that becomes the
    #: schedule kind. Converted below, before anything else looks at the bytes,
    #: so everything downstream (the contract, the differ, the identity
    #: resolver, the watched-sheet registration) handles one shape and there is
    #: no second ingestion path to keep in step.
    #: One Jira export becomes *both* sheets, not one the person has to pick.
    #:
    #: An issue row genuinely holds a plan (dates, links) and a record of effort,
    #: and this app keeps those in separate contracts the way a spreadsheet shop
    #: keeps them in separate files. That is an internal arrangement, though, and
    #: asking somebody which half of their own file to read leaks it onto the
    #: form - they have one file. So the schedule always goes in, and the worklog
    #: follows whenever the export carries effort at all.
    from_jira = kind == "jira_export"
    if from_jira:
        kind = "schedule"
    if kind not in SHEET_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown sheet_kind {sheet_kind!r}; expected one of "
                f"{[*SHEET_KINDS, 'jira_export']}"
            ),
        )

    if file.filename and not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(
            status_code=400,
            detail=f"{file.filename!r} is not an Excel workbook (.xlsx)",
        )

    project_id = project_id.strip()
    project_name = project_name.strip()
    program_id = program_id.strip()

    # Which program a new project belongs to is stated here or nowhere. Deriving
    # it downstream from whichever collector ran is what let one program exist
    # under two ids, so the choice is taken at the moment a person makes it -
    # and an unknown program is refused rather than created, because a typo that
    # silently invents a program is how a portfolio grows rows nobody meant.
    if program_id:
        if scope.find_program(program_id) is None:
            raise HTTPException(
                status_code=400, detail=f"unknown program {program_id!r}"
            )

    #: Set for a project this upload would be the first sight of. Registered
    #: only once the workbook has been *accepted* - registering up here left a
    #: project behind every time a document was refused, and it then sat on
    #: the portfolio as `no_data`, indistinguishable from the silent-failure
    #: bug this whole route was fixed for.
    register_new: str | None = None

    if project_id:
        found = scope.find(project_id)
        if found is None:
            raise HTTPException(status_code=400, detail=f"unknown project {project_id!r}")
        project_name = found.name
    else:
        if not project_name:
            raise HTTPException(
                status_code=400,
                detail="project_name is required when project_id is not given",
            )
        # `scope.slugify`, not a second regex. `POST /api/projects` derives the
        # same id from the same name, and if the two spellings drift then
        # uploading a sheet for a project somebody already added by name creates
        # a *second* project beside it rather than filling that one in.
        # `slugify` also handles a name with no ASCII in it - "工数管理" - which
        # the regex here collapsed to the constant "project", mapping every
        # Japanese-named project onto one id.
        project_id = f"excel:Project:upload:{scope.slugify(project_name)}"
        register_new = project_name

    id_slug = re.sub(r"[^a-z0-9]+", "-", project_id.lower()).strip("-")

    payload = await file.read()

    #: The sheets this one upload becomes: `(kind, bytes)`.
    #:
    #: Conversion happens before the workbook is inspected or stored, and what
    #: gets registered and re-synced is the *converted* sheet - which is what
    #: makes a later export of the same project a genuine second observation:
    #: the differ compares it against this one, and the baseline Jira cannot
    #: give us starts existing the moment somebody uploads twice.
    sheets: list[tuple[str, bytes]] = []
    jira_coverage: dict | None = None

    if from_jira:
        from app.ingest.sources.jira.export_sheet import (
            NotAJiraExport,
            convert_workbook,
        )

        try:
            schedule_bytes, jira_coverage = convert_workbook(
                BytesIO(payload), kind="schedule"
            )
        except NotAJiraExport as exc:
            # 400 with the reason. The file is the wrong shape, or was exported
            # without the fields - neither is a server fault, and a 500 here
            # would read as "the product is broken".
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sheets.append(("schedule", schedule_bytes))

        #: The effort half, when there is one. Its absence is **not** an error:
        #: plenty of Jira projects never fill in an estimate, and refusing the
        #: whole upload for that would reject a perfectly good schedule.
        try:
            worklog_bytes, _ = convert_workbook(BytesIO(payload), kind="worklog")
        except NotAJiraExport:
            worklog_bytes = None
        if worklog_bytes is not None:
            sheets.append(("worklog", worklog_bytes))
    else:
        sheets.append((kind, payload))

    # Which tab holds each table, decided from the workbook's own contents.
    # Resolved here, once, rather than per scan: the answer is half the scope
    # key, so re-deriving it would let a tab rename lose the baseline.
    #
    # Inspected from a temp file, not from `data_root`: nothing is stored until
    # the workbook has been accepted, so a document we are going to refuse
    # cannot displace the copy of this sheet we are already syncing. Every sheet
    # is validated before *any* is registered, so a Jira export whose second
    # half is malformed does not leave the first half half-imported.
    resolved: list[tuple[str, str, str, bytes]] = []
    for this_kind, this_payload in sheets:
        this_name = f"upload_{id_slug}_{this_kind}.xlsx"
        conventional, contract = SHEET_KINDS[this_kind]
        staged = Path(tempfile.gettempdir()) / f"pulse-incoming-{os.getpid()}-{this_name}"
        staged.write_bytes(this_payload)
        try:
            sheet_name = find_sheet(staged, contract, preferred=conventional)
            if sheet_name is None:
                from openpyxl import load_workbook

                book = load_workbook(staged, read_only=True)
                tabs = list(book.sheetnames)
                book.close()
                # Name the tabs we saw and the columns we need. "Could not read
                # this workbook" sends a person back to a file with nothing to
                # change; this tells them which sheet to fix and what is missing.
                wanted = [h for h in contract.template_headers[:5]]
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"no sheet in this workbook looks like a {this_kind}. Tabs "
                        f"found: {tabs}. One of them needs a header row with columns "
                        f"like {wanted} - a '{contract.key_field}' column is required, "
                        "because it is what identifies a row across imports."
                    ),
                )
        finally:
            staged.unlink(missing_ok=True)
        resolved.append((this_kind, this_name, sheet_name, this_payload))

    #: What the response reports on: the schedule, which is always first. The
    #: worklog rides along and is named in the notes rather than counted here -
    #: "17 rows" meaning the plan is the number a person is looking for.
    kind, file_name, sheet_name, payload = resolved[0]

    # Accepted - so the project may exist now.
    if register_new is not None:
        scope.register(project_id, register_new, program_id=program_id or None)

    # The bytes go into the database with the registration, not onto the
    # container's disk. A Fly machine's filesystem does not survive a deploy,
    # so a document written there had to be re-uploaded after every release -
    # and the sheet went on being watched with nothing behind it.
    for this_kind, this_name, this_sheet, this_payload in resolved:
        register_watched(
            this_name,
            this_kind,
            project_id,
            sheet_name=this_sheet,
            content=this_payload,
            original_filename=file.filename,
        )

    try:
        with session_scope() as session:
            outcome = run_sync(session, "excel", "manual")
    except Exception as exc:  # noqa: BLE001 - surfaced on the page, not swallowed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "project_id": project_id}

    # This sheet's own outcome, not the whole run's. `run_sync` scans every
    # watched sheet, so `outcome.rows_ok` counts other projects' rows too - an
    # import that ingested nothing would report whatever else happened to move
    # and read as a success.
    scope_key = f"{file_name}#{sheet_name}"
    mine = next((n for n in outcome.notes if n.startswith(f"{scope_key}:")), None)
    rejected = "REJECTED" in (mine or "")
    with session_scope() as session:
        rows = session.scalar(
            select(func.count()).select_from(ToolExcelRow).where(
                ToolExcelRow.scope == scope_key
            )
        ) or 0

    return {
        # An import that landed no rows is not a success, whatever the run did.
        "ok": rows > 0 and not rejected,
        "project_id": project_id,
        "project_name": project_name,
        "file_name": file_name,
        "sheet_name": sheet_name,
        "rows_ok": rows,
        "run_id": outcome.run_id,
        "status": outcome.status,
        "rows_rejected": outcome.rows_rejected,
        "changes_emitted": outcome.changes_emitted,
        # This sheet's line first - the rest of the run is context, and a
        # previous upload's failure showing up first reads as this one's.
        "notes": ([mine] if mine else []) + [n for n in outcome.notes if n != mine],
        # What a Jira export could NOT tell us, when that is what was uploaded.
        # Returned rather than logged because it is the difference between a
        # person thinking the import half-failed and knowing their export has no
        # baseline column - and the page has no other way to find out.
        "conversion": jira_coverage,
        "error": (mine or "this workbook produced no rows") if (rejected or rows == 0) else None,
    }


@app.get("/api/scenarios", response_model=ScenarioBundle)
def scenarios(
    project: str = Depends(project_param),
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
        # Whichever of this project's source ids the caller holds, resolved to
        # the canonical one plus the rest (invariant 7). Asking by the Jira id
        # used to analyse the Jira rows alone and present that as the project.
        project, also = scope.canonical_pairing(project)

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

    Read per request, so the settings page takes effect on the next reload
    rather than the next restart. `app.config` is still the floor: it supplies
    the defaults the resolution starts from.
    """
    from app.llm.features import attributed_drafter, resolve

    # Per-feature now: narration, risk drafts, the tile agent and chat each
    # resolve their own vendor and model, instead of all four reading one
    # global setting. `resolve` falls back to exactly that setting when nobody
    # has overridden the feature, so an existing deployment is unaffected.
    chosen = resolve("narration")

    # `chosen.enabled`, not the global flag this used to read: the resolution
    # already folds `PULSE_NARRATION` together with a per-feature switch, and
    # checking the global one separately meant turning narration off on the
    # settings page changed nothing here.
    if not chosen.enabled:
        return None

    try:
        return attributed_drafter("narration")
    except ValueError:
        # An unknown provider in the stored settings. Narration is optional,
        # so a bad setting costs the model and not the page.
        log.warning("unknown narration provider %r; serving the template", chosen.provider)
        return None


class NarrationSettingsIn(BaseModel):
    """The global model default that features fall back to.

    **No `api_key`.** Keys are set on `/llm` and sealed in `llm_credentials`;
    this row is plaintext JSON and accepting one here would quietly reopen the
    hole that store was built to close. A client that still sends the field
    has it ignored rather than rejected - see `narration.store.update`.
    """

    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None


@app.get("/settings")
def settings_page() -> FileResponse:
    """Where a person turns narration on and pastes a key."""
    return _page("settings.html")


@app.get("/api/settings")
def read_settings(request: Request) -> dict:
    """Current narration settings. Never includes the key itself.

    `writable` used to be hardcoded true, which told every deployed browser it
    could save when the write would 403 - and contradicted DEPLOY.md's own
    "read-only when deployed". It now answers the question it claims to.
    """
    from app import admin
    from app.narration.store import public_view

    view = public_view()
    view["writable"] = admin.may_write(request)
    view["auth"] = admin.status()
    return view


@app.put("/api/settings")
def write_settings(request: Request, body: NarrationSettingsIn) -> dict:
    from app import admin
    from app.narration.providers import PROVIDERS
    from app.narration.store import public_view, update

    admin.require(request)

    if body.provider is not None and body.provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider {body.provider!r}; expected one of {list(PROVIDERS)}",
        )

    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    update(**changes)
    return read_settings(request)


# --------------------------------------------------------------------------
# Models, keys and spend.
#
# Three questions that used to have one answer between them - the single
# global narration setting - and no answer at all for the third. See
# `app/llm/` for the modules behind these routes.
# --------------------------------------------------------------------------


class FeatureOverrideIn(BaseModel):
    """One feature's model settings. Every field is optional on purpose.

    An omitted field is left alone; an explicitly empty one is cleared back to
    the global default. That distinction is what lets the page offer "follow
    the default" as a real choice rather than a string somebody has to guess.
    """

    provider: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    base_url: str | None = None
    enabled: bool | None = None


class ApiKeyIn(BaseModel):
    """A vendor credential on its way to the encrypted store.

    An empty string is an explicit clear, matching the settings page's existing
    convention - the page never holds the real key, so it cannot send one back
    by accident.
    """

    api_key: str = ""


class RateIn(BaseModel):
    """US dollars per million tokens. Zero input and output removes the rate."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


@app.get("/llm")
def llm_page() -> FileResponse:
    """Where each feature's model is chosen and keys are managed."""
    return _page("llm.html")


@app.get("/usage")
def usage_page() -> FileResponse:
    """What the models have cost."""
    return _page("usage.html")


@app.get("/api/llm/features")
def llm_features(request: Request) -> dict:
    """Every feature, the model it resolves to, and where that came from."""
    from app import admin
    from app.llm import keys
    from app.llm.features import public_view

    view = public_view()
    view["writable"] = admin.may_write(request)
    view["auth"] = admin.status()
    view["key_store"] = {
        "available": keys.available(),
        "reason": keys.unavailable_reason(),
        "secret_env": keys.SECRET_ENV,
    }
    return view


@app.put("/api/llm/features/{feature}")
def set_llm_feature(request: Request, feature: str, body: FeatureOverrideIn) -> dict:
    from app import admin
    from app.llm.features import set_override

    admin.require(request)

    # `exclude_unset` rather than a None filter: it is what distinguishes "do
    # not touch this field" from "clear it", and `enabled=False` from absent.
    changes = body.model_dump(exclude_unset=True)
    try:
        set_override(feature, **changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return llm_features(request)


@app.post("/api/llm/features/{feature}/test")
def test_llm_feature(request: Request, feature: str) -> dict:
    """Ask this feature's configured model to answer, and report what happened.

    For `narration` this runs the full end-to-end test - the real brief, the
    real eight-stage validator - because that path exists and proves far more
    than a ping. For the others it is a ping: the smallest real call through
    the same adapter, which is what actually establishes that the vendor, the
    model id and the credential agree.

    Either way it spends money, so it needs the same authorisation a write
    does, and it records a usage row against the feature like any other call.
    """
    from app import admin
    from app.llm import usage
    from app.llm.features import FEATURES, resolve
    from app.narration.providers import drafter_for

    admin.require(request)

    if feature not in FEATURES:
        raise HTTPException(status_code=400, detail=f"unknown feature {feature!r}")

    if feature == "narration":
        return test_settings(request)

    chosen = resolve(feature)
    if not chosen.has_credential:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no credential for {chosen.provider}. Save a key below, or set "
                "the vendor's environment variable."
            ),
        )

    try:
        drafter = drafter_for(chosen.provider, chosen.model_config())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    started = time.monotonic()
    try:
        with usage.for_feature(feature):
            reply = drafter(
                "Reply with the single word: ready.",
                "Are you reachable?",
            )
    except Exception as exc:  # noqa: BLE001 - the answer is what went wrong
        return {
            "ok": False,
            "provider": chosen.provider,
            "model": chosen.model,
            "error": f"{type(exc).__name__}: {exc}"[:400],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    return {
        "ok": True,
        "provider": chosen.provider,
        "model": chosen.model,
        "source_of_choice": chosen.source,
        "reply": (reply or "").strip()[:200],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


@app.get("/api/llm/keys")
def llm_keys(request: Request) -> dict:
    """Which vendors have a key, and where each one lives. Never a key."""
    from app import admin
    from app.llm import keys

    return {
        "keys": keys.status(),
        "writable": admin.may_write(request),
        "auth": admin.status(),
        "store": {
            "available": keys.available(),
            "reason": keys.unavailable_reason(),
            "secret_env": keys.SECRET_ENV,
            "encrypted": True,
        },
    }


@app.put("/api/llm/keys/{provider}")
def save_llm_key(request: Request, provider: str, body: ApiKeyIn) -> dict:
    from app import admin
    from app.llm import keys
    from app.narration.providers import PROVIDERS

    actor = admin.require(request)

    if provider not in PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown provider {provider!r}; expected one of {list(PROVIDERS)}",
        )

    try:
        keys.save(provider, body.api_key.strip(), actor=actor)
    except keys.KeyStoreUnavailable as exc:
        # 503 rather than 400: the request was fine, the server cannot store
        # it safely yet. The message names the variable to set.
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return llm_keys(request)


@app.delete("/api/llm/keys/{provider}")
def delete_llm_key(request: Request, provider: str) -> dict:
    from app import admin
    from app.llm import keys

    admin.require(request)
    keys.delete(provider)
    return llm_keys(request)


@app.get("/api/llm/usage")
def llm_usage(days: int = 30) -> dict:
    """Tokens and cost, grouped by feature, model and day.

    Readable without an admin token: it discloses no credential and no prompt,
    and a spend figure nobody can see is a spend figure nobody controls.
    """
    from app.llm.usage import summary

    return summary(days=max(1, min(days, 365)))


@app.get("/api/llm/pricing")
def llm_pricing(request: Request) -> dict:
    from app import admin
    from app.llm.pricing import table

    return {"rates": table(), "writable": admin.may_write(request)}


@app.put("/api/llm/pricing/{model:path}")
def set_llm_rate(request: Request, model: str, body: RateIn) -> dict:
    from app import admin
    from app.llm.pricing import set_rate

    admin.require(request)
    try:
        set_rate(model, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return llm_pricing(request)


@app.post("/api/llm/usage/purge")
def purge_llm_usage(request: Request, older_than_days: int = 90) -> dict:
    """Trim the usage log. Retention is a decision, so nothing does this itself."""
    from app import admin
    from app.llm.usage import purge

    admin.require(request)
    return {"deleted": purge(max(0, older_than_days))}


@app.post("/api/settings/test")
def test_settings(request: Request) -> dict:
    """Ask the configured model for a narrative, and report exactly what happened.

    A real end-to-end call rather than a credential ping: it builds the same
    brief, runs the same eight-stage gate and substitutes the same way, so a
    pass here means the feature works and not merely that the key is valid.
    """
    # A real call to the vendor, so it spends money - the same gate as a
    # write rather than the softer one a read gets.
    from app import admin

    admin.require(request)

    from app.llm.features import attributed_drafter, resolve
    from app.narration.client import narrate

    # The test exercises what narration itself would run, so it resolves the
    # same feature rather than the global default - otherwise a per-feature
    # override would be exactly the thing this never tested.
    chosen = resolve("narration")
    try:
        drafter = attributed_drafter("narration")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    problem = check_connection()
    if problem is not None:
        raise HTTPException(status_code=503, detail=problem)

    # The smoke test narrates a real project so a key that authenticates but
    # cannot complete still fails here rather than on someone's report. Which
    # project is the same question every other route asks, so it is the same
    # answer - naming the demo id here made "test your key" fail on any install
    # that had deleted it.
    _default, _also = scope.canonical_pairing(default_project_id())
    with session_scope() as session:
        bundle = analyze_project(session, project_id=_default, also=list(_also))

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
        # What actually ran, which is the point of a test: with per-feature
        # settings these are narration's own resolved values, not the global
        # default they used to be read from.
        "provider": chosen.provider,
        "model": chosen.model or "(provider default)",
        "source_of_choice": chosen.source,
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


def _draft_drafter():
    """The model used to propose risks, or a reason it cannot be.

    Separate from `_narrator()` on purpose, and the difference is the first
    line: this checks `risk_drafts_enabled`, not narration's own switch. The
    two are different permissions - see `app/config.py` - and a deployment that
    wants prose without claims has to be able to have exactly that.

    Credentials are narration's, because they are the same account. Only the
    permission is separate.
    """
    from app.config import settings as live_settings

    if not live_settings.risk_drafts_enabled:
        return None, (
            "Reading task text for risks is switched off. Set PULSE_RISK_DRAFTS=1 "
            "to turn it on. It is separate from narration deliberately: this lets "
            "a model make a claim, which the narration fence exists to forbid."
        )

    from app.llm.features import attributed_drafter, resolve

    # The timeout and token ceiling that used to live here are now this
    # feature's defaults in `app/llm/features.py`, with the measurements that
    # produced them - so an override can raise them without editing a route.
    chosen = resolve("risk_drafts")
    try:
        drafter = attributed_drafter("risk_drafts")
    except ValueError:
        return None, f"unknown model provider {chosen.provider!r} in settings."
    return drafter, None


def _draft_bundle(session, project: str, reason: str | None = None) -> RiskDraftBundle:
    """The drafts panel for one project, whatever state it is in.

    One builder for both the read and the generate route, so a freshly
    generated panel and a reloaded one cannot disagree about what they show.
    """
    from app.config import settings as live_settings
    from app.risks.drafts import readable_tasks
    from app.risks.service import list_drafts

    drafts = list_drafts(session, project_ids=[project])

    wanted: list[str] = []
    for draft in drafts:
        for task_id in (draft.cited_task_ids or "").split(","):
            task_id = task_id.strip()
            if task_id and task_id not in wanted:
                wanted.append(task_id)

    cited: list[CitedTask] = []
    if wanted:
        from app.models.domain import Task
        from app.risks.drafts import MAX_DESCRIPTION_CHARS, _clean_text, _task_key

        for task in session.scalars(select(Task).where(Task.id.in_(wanted))).all():
            cited.append(
                CitedTask(
                    task_id=task.id,
                    label=_task_key(task),
                    title=task.title,
                    status=task.original_status or task.status,
                    text=_clean_text(task.description, MAX_DESCRIPTION_CHARS) or None,
                )
            )

    readable = len(readable_tasks(session, scope.source_ids_for(project)))
    if reason is None and not drafts:
        if not live_settings.risk_drafts_enabled:
            reason = (
                "Reading task text for risks is switched off (PULSE_RISK_DRAFTS)."
            )
        elif not readable:
            reason = (
                "No task on this project carries a description, so there is no "
                "text to read."
            )
        else:
            reason = f"Nothing proposed yet. {readable} task(s) carry text to read."

    return RiskDraftBundle(
        drafts=drafts,
        cited_tasks=cited,
        enabled=live_settings.risk_drafts_enabled,
        reason=reason,
        readable_tasks=readable,
    )


@app.get("/api/risks/drafts", response_model=RiskDraftBundle)
def read_risk_drafts(project: str) -> RiskDraftBundle:
    """Proposals waiting on a person for one project, with the rows they cite.

    A plain read: it never asks a model, so opening the page costs nothing and
    a reload does not quietly spend money. Generating is a POST, because it is
    an action somebody takes.
    """
    if scope.resolve(project) is None:
        raise HTTPException(status_code=400, detail=f"unknown project {project!r}")
    with session_scope() as session:
        return _draft_bundle(session, project)


@app.post("/api/risks/drafts", response_model=RiskDraftBundle)
def generate_risk_drafts(request: Request, project: str) -> RiskDraftBundle:
    """Read this project's task text and propose risks from it.

    Answers 200 with a `reason` rather than an error when the model cannot be
    reached or finds nothing worth proposing. That is the same bargain
    `narrate` makes: an optional feature being absent is a state to describe,
    not a failure to raise, and a 500 here would make a page that works look
    broken.
    """
    # Gated because it spends money. Anyone who could reach this route could
    # run up the bill on the deployment's own key, and `/usage` exists
    # precisely so that spend is not invisible - it should not also be
    # anonymous. Loopback still passes with no token, so local development is
    # unchanged; see `app/admin.py`.
    from app import admin

    admin.require(request)

    from app.risks.drafts import DraftsUnavailable, propose
    from app.risks.service import CATEGORIES

    if scope.resolve(project) is None:
        raise HTTPException(status_code=400, detail=f"unknown project {project!r}")

    drafter, refusal = _draft_drafter()
    with session_scope() as session:
        if drafter is None:
            return _draft_bundle(session, project, reason=refusal)
        try:
            propose(session, project, drafter, list(CATEGORIES))
        except DraftsUnavailable as exc:
            return _draft_bundle(session, project, reason=str(exc))
        return _draft_bundle(session, project)


@app.post("/api/risks/drafts/{risk_id}/accept", response_model=RiskOut)
def accept_risk_draft(risk_id: int) -> RiskOut:
    """Promote one proposal into the register. The act that makes it a risk."""
    from app.risks.service import accept_draft

    with session_scope() as session:
        accepted = accept_draft(session, risk_id)
        if accepted is None:
            raise HTTPException(
                status_code=404, detail=f"no draft risk with id {risk_id}"
            )
        return accepted


@app.delete("/api/risks/drafts/{risk_id}", status_code=204)
def dismiss_risk_draft(risk_id: int) -> Response:
    from app.risks.service import dismiss_draft

    with session_scope() as session:
        if not dismiss_draft(session, risk_id):
            raise HTTPException(
                status_code=404, detail=f"no draft risk with id {risk_id}"
            )
    return Response(status_code=204)


@app.get("/programs/dashboard")
def program_dashboard_page() -> FileResponse:
    """The Program canvas. Same shell as every other page; picked by path."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/project/dashboard")
def project_dashboard_page() -> FileResponse:
    """The Project canvas. Same shell as every other page; picked by path."""
    return FileResponse(STATIC / "app" / "index.html")


@app.get("/api/dashboard/catalogue", response_model=CatalogueBundle)
def dashboard_catalogue_api() -> CatalogueBundle:
    from app.dashboard.service import get_catalogue

    return get_catalogue()


@app.get("/api/dashboards", response_model=DashboardOut)
def get_dashboard_api(scope_type: str, scope_id: str) -> DashboardOut:
    from app.dashboard.service import get_dashboard

    with session_scope() as session:
        return get_dashboard(session, scope_type, scope_id)


@app.post("/api/dashboards/blank", response_model=DashboardOut)
def reset_dashboard_api(scope_type: str, scope_id: str) -> DashboardOut:
    from app.dashboard.service import reset_blank

    with session_scope() as session:
        return reset_blank(session, scope_type, scope_id)


@app.post("/api/dashboards/apply-template", response_model=DashboardOut)
def apply_template_api(scope_type: str, scope_id: str, template: str) -> DashboardOut:
    from app.dashboard.service import apply_template

    with session_scope() as session:
        bundle = apply_template(session, scope_type, scope_id, template)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"no template {template!r}")
        return bundle


@app.post("/api/dashboards/default", response_model=DashboardOut)
def apply_default_dashboard_api(scope_type: str, scope_id: str) -> DashboardOut:
    """Default setup: this scope's starting board, without picking anything.

    Its own route rather than the caller naming a template, because *which*
    template is the default is a product decision and belongs beside the
    catalogue (`catalogue.DEFAULT_TEMPLATE`), not in a query string a browser
    can get wrong. 400, not 404, for an unknown `scope_type`: the template
    exists, the scope does not.
    """
    from app.dashboard.service import apply_default

    with session_scope() as session:
        bundle = apply_default(session, scope_type, scope_id)
        if bundle is None:
            raise HTTPException(
                status_code=400, detail=f"no default dashboard for scope {scope_type!r}"
            )
        return bundle


@app.post("/api/dashboards/fit", response_model=DashboardOut)
def fit_dashboard_api(scope_type: str, scope_id: str) -> DashboardOut:
    """Build a board from the signals this scope's data actually carries.

    Its own route rather than a template name, because the answer depends on the
    database rather than on a list somebody wrote: the same call against two
    projects legitimately produces two different boards.
    """
    from app.dashboard.service import apply_fitted

    with session_scope() as session:
        return apply_fitted(session, scope_type, scope_id)


@app.post("/api/dashboards/generate", response_model=DashboardOut)
def generate_dashboard_api(request: Request, body: GenerateRequest) -> DashboardOut:
    """Create with AI: the model picks tiles from the catalogue, never data.

    Reuses whichever provider narration is already configured with (`_narrator`,
    same as `/api/agent/chat`) - no separate credential for this feature."""
    # Gated because it spends money. Anyone who could reach this route could
    # run up the bill on the deployment's own key, and `/usage` exists
    # precisely so that spend is not invisible - it should not also be
    # anonymous. Loopback still passes with no token, so local development is
    # unchanged; see `app/admin.py`.
    from app import admin

    admin.require(request)

    from app.dashboard.service import generate

    with session_scope() as session:
        return generate(session, body.scope_type, body.scope_id, body.prompt, _narrator())


@app.post("/api/custom-tiles/draft", response_model=CustomChartDraft)
def draft_custom_tile_api(request: Request, body: CustomTileDraftRequest) -> CustomChartDraft:
    """Parse pasted data into a chart - not saved yet. Reuses whichever
    provider narration is already configured with; falls back to a plain
    two-column CSV/TSV parse when there's no model or its output doesn't
    validate, so 'paste a label,value table' still works with narration off."""
    # Gated because it spends money. Anyone who could reach this route could
    # run up the bill on the deployment's own key, and `/usage` exists
    # precisely so that spend is not invisible - it should not also be
    # anonymous. Loopback still passes with no token, so local development is
    # unchanged; see `app/admin.py`.
    from app import admin

    admin.require(request)

    from app.dashboard.custom import draft_chart

    try:
        return draft_chart(body.raw_data, body.hint, drafter=_narrator())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/custom-tiles/chat", response_model=TileChatResponse)
def chat_custom_tile_api(request: Request, body: TileChatRequest) -> TileChatResponse:
    """The tile builder, one turn at a time: describe a chart, look at it, say
    what is wrong, look again.

    Stateless - the conversation and the draft on screen both arrive in the
    request, the same as `/api/agent/chat`. Unlike that route this one is not
    free-form: the model only ever returns a `{title, chart_type, labels,
    values}` object, validated before it reaches the response, and what the
    transcript says about a turn is computed by diffing the two drafts rather
    than taken from the model's own account of what it did."""
    # Gated because it spends money. Anyone who could reach this route could
    # run up the bill on the deployment's own key, and `/usage` exists
    # precisely so that spend is not invisible - it should not also be
    # anonymous. Loopback still passes with no token, so local development is
    # unchanged; see `app/admin.py`.
    from app import admin

    admin.require(request)

    from app.api.schemas.dashboard import CustomChartDraft
    from app.dashboard.custom import chat_turn

    on_screen = (
        CustomChartDraft(**body.draft.model_dump()) if body.draft is not None else None
    )

    # The tool-use path (app/dashboard/agent.py) needs a raw ModelConfig, not
    # just the Drafter closure _narrator() returns - and it is Anthropic-only,
    # so it is only built when that is the configured provider. Any other
    # provider, or no key at all, leaves agent_cfg None and chat_turn falls
    # straight through to its existing plain-parse path. Model default
    # resolved the same way drafter_for() resolves it - an empty cfg.model
    # would otherwise reach the Anthropic SDK as model="".
    from app.llm import usage
    from app.llm.features import resolve

    # `resolve` already pins this feature to Anthropic and fills in the model,
    # so the "is it anthropic, and what is the default model" dance this used
    # to do by hand is gone. It stays None when the feature has no credential,
    # which is what makes `chat_turn` fall through to its plain-parse path.
    chosen = resolve("tile_agent")
    agent_cfg = chosen.model_config() if chosen.has_credential else None

    with session_scope() as session:
        try:
            # The tool loop inside records a row per round; this names the
            # feature those rounds belong to.
            with usage.for_feature("tile_agent"):
                return chat_turn(
                    body.messages,
                    on_screen,
                    body.raw_data,
                    drafter=_narrator(),
                    session=session,
                    project_id=body.scope_id,
                    agent_cfg=agent_cfg,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/custom-tiles", response_model=CustomTileListBundle)
def list_custom_tiles_api() -> CustomTileListBundle:
    from app.dashboard.custom import list_custom_tiles

    with session_scope() as session:
        return CustomTileListBundle(tiles=list_custom_tiles(session))


@app.post("/api/custom-tiles", response_model=CustomTileOut)
def create_custom_tile_api(body: CustomTileIn) -> CustomTileOut:
    from app.dashboard.custom import create_custom_tile

    with session_scope() as session:
        try:
            return create_custom_tile(session, body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/custom-tiles/{tile_id}", response_model=CustomTileOut)
def get_custom_tile_api(tile_id: int) -> CustomTileOut:
    from app.dashboard.custom import get_custom_tile

    with session_scope() as session:
        tile = get_custom_tile(session, tile_id)
        if tile is None:
            raise HTTPException(status_code=404, detail=f"no custom tile with id {tile_id}")
        return tile


@app.delete("/api/custom-tiles/{tile_id}", status_code=204)
def delete_custom_tile_api(tile_id: int) -> Response:
    from app.dashboard.custom import delete_custom_tile

    with session_scope() as session:
        if not delete_custom_tile(session, tile_id):
            raise HTTPException(status_code=404, detail=f"no custom tile with id {tile_id}")
    return Response(status_code=204)


@app.post("/api/dashboards/tiles", response_model=TileOut)
def add_tile_api(scope_type: str, scope_id: str, data: TileIn) -> TileOut:
    from app.dashboard.service import add_tile

    with session_scope() as session:
        try:
            tile = add_tile(session, scope_type, scope_id, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return tile


@app.patch("/api/dashboards/tiles/{tile_id}", response_model=TileOut)
def update_tile_api(tile_id: int, data: TileIn) -> TileOut:
    from app.dashboard.service import update_tile

    with session_scope() as session:
        tile = update_tile(session, tile_id, data)
        if tile is None:
            raise HTTPException(status_code=404, detail=f"no tile with id {tile_id}")
        return tile


@app.post("/api/dashboards/tiles/{tile_id}/duplicate", response_model=TileOut)
def duplicate_tile_api(tile_id: int) -> TileOut:
    from app.dashboard.service import duplicate_tile

    with session_scope() as session:
        tile = duplicate_tile(session, tile_id)
        if tile is None:
            raise HTTPException(status_code=404, detail=f"no tile with id {tile_id}")
        return tile


@app.delete("/api/dashboards/tiles/{tile_id}", status_code=204)
def delete_tile_api(tile_id: int) -> Response:
    from app.dashboard.service import delete_tile

    with session_scope() as session:
        if not delete_tile(session, tile_id):
            raise HTTPException(status_code=404, detail=f"no tile with id {tile_id}")
    return Response(status_code=204)


@app.get("/agent")
def agent_page() -> FileResponse:
    """Free-form chat - the one page with no deterministic engine behind it."""
    return FileResponse(STATIC / "app" / "index.html")


@app.post("/api/agent/chat", response_model=ChatResponse)
def agent_chat(request: Request, body: ChatRequest) -> ChatResponse:
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
    # Gated because it spends money. Anyone who could reach this route could
    # run up the bill on the deployment's own key, and `/usage` exists
    # precisely so that spend is not invisible - it should not also be
    # anonymous. Loopback still passes with no token, so local development is
    # unchanged; see `app/admin.py`.
    from app import admin

    admin.require(request)

    from app.agent.brief import build_brief
    from app.agent.chat import ChatTurn, ChatUnavailable, chat as run_chat
    from app.agent.link_fetch import fetch_and_extract, find_first_url
    from app.llm import usage
    from app.llm.features import resolve

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")

    # Its own feature, so the Agent tab can sit on a cheaper model than
    # narration does - many short turns is a different shape of bill from a
    # few long ones, and they used to be forced to share a setting.
    chosen = resolve("chat")
    turns = [ChatTurn(role=m.role, content=m.content) for m in body.messages]

    #: Rebuilt every turn rather than once per conversation. The snapshot moves
    #: under a chat - an upload lands, a draft is accepted - and a brief pinned
    #: at the first question would have the agent answering from data the pages
    #: beside it stopped showing.
    context = None
    grounded_in = ""
    if body.project.strip():
        found = scope.resolve(body.project.strip())
        if found is not None:
            with session_scope() as session:
                context = build_brief(session, found.canonical_id)
            grounded_in = found.name or found.canonical_id

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
        with usage.for_feature("chat"):
            reply = run_chat(
                turns,
                provider=chosen.provider,
                model=chosen.model,
                api_key=chosen.api_key or None,
                base_url=chosen.base_url or None,
                context=context,
            )
    except ChatUnavailable as exc:
        return ChatResponse(reply="", ok=False, error=str(exc))

    return ChatResponse(reply=reply, ok=True, grounded_in=grounded_in)


@app.get("/api/insight", response_model=InsightBundle)
def insight(
    project: str = Depends(project_param),
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
        # Whichever of this project's source ids the caller holds, resolved to
        # the canonical one plus the rest (invariant 7). Asking by the Jira id
        # used to analyse the Jira rows alone and present that as the project.
        project, also = scope.canonical_pairing(project)

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

    # Attached after the analysis rather than inside it, and left `None` when
    # the project has no run. `analyze_project` reads the database and nothing
    # else; giving it a directory of another repository's artifacts to read
    # would make the intelligence layer depend on a filesystem it has no
    # business knowing about, and every test of it would need one.
    bundle.code_check = _code_check(project)
    return bundle
