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

import importlib.util
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

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
from app.api.schemas.risk import RiskBundle, RiskIn, RiskOut
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
        sections=chosen,
        project_name=found.name if found else "",
    )



@app.get("/api/explain", response_model=ExplainBundle)
def api_explain(
    project: str = "excel:Project:1:HRMS",
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
    project: str = "excel:Project:1:HRMS",
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
def report_options(project: str = "excel:Project:1:HRMS") -> ReportOptions:
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

    def available(requires: str) -> bool:
        # `explain` and `scenarios` are computed from the schedule, which any
        # analysable project has; `risks` is a register a person fills in, and
        # is genuinely empty until they do.
        return risk_count > 0 if requires == "risks" else True

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
    project: str = "excel:Project:1:HRMS",
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
    project: str = "excel:Project:1:HRMS",
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
    project: str = "excel:Project:1:HRMS",
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
    project: str = "excel:Project:1:HRMS",
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
    file_name = f"upload_{id_slug}_{kind}.xlsx"

    payload = await file.read()

    #: The conversion, before the workbook is inspected or stored. What gets
    #: registered and re-synced is the *converted* schedule, which is what makes
    #: a later export of the same project a genuine second observation: the
    #: differ compares it against this one, and the baseline Jira cannot give us
    #: starts existing the moment somebody uploads twice.
    jira_coverage: dict | None = None
    if from_jira:
        from app.ingest.sources.jira.export_sheet import (
            NotAJiraExport,
            convert_workbook,
        )

        try:
            payload, jira_coverage = convert_workbook(BytesIO(payload))
        except NotAJiraExport as exc:
            # 400 with the reason. The person picked the wrong kind for their
            # file, or exported without the fields - neither is a server fault,
            # and a 500 here would read as "the product is broken".
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Which tab holds the table, decided from the workbook's own contents.
    # Resolved here, once, rather than per scan: the answer is half the scope
    # key, so re-deriving it would let a tab rename lose the baseline.
    #
    # Inspected from a temp file, not from `data_root`: nothing is stored until
    # the workbook has been accepted, so a document we are going to refuse
    # cannot displace the copy of this sheet we are already syncing.
    conventional, contract = SHEET_KINDS[kind]
    staged = Path(tempfile.gettempdir()) / f"pulse-incoming-{os.getpid()}-{file_name}"
    staged.write_bytes(payload)
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
                    f"no sheet in this workbook looks like a {kind}. Tabs found: "
                    f"{tabs}. One of them needs a header row with columns like "
                    f"{wanted} - a '{contract.key_field}' column is required, "
                    "because it is what identifies a row across imports."
                ),
            )
    finally:
        staged.unlink(missing_ok=True)

    # Accepted - so the project may exist now.
    if register_new is not None:
        scope.register(project_id, register_new, program_id=program_id or None)

    # The bytes go into the database with the registration, not onto the
    # container's disk. A Fly machine's filesystem does not survive a deploy,
    # so a document written there had to be re-uploaded after every release -
    # and the sheet went on being watched with nothing behind it.
    register_watched(
        file_name,
        kind,
        project_id,
        sheet_name=sheet_name,
        content=payload,
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


@app.post("/api/dashboards/generate", response_model=DashboardOut)
def generate_dashboard_api(body: GenerateRequest) -> DashboardOut:
    """Create with AI: the model picks tiles from the catalogue, never data.

    Reuses whichever provider narration is already configured with (`_narrator`,
    same as `/api/agent/chat`) - no separate credential for this feature."""
    from app.dashboard.service import generate

    with session_scope() as session:
        return generate(session, body.scope_type, body.scope_id, body.prompt, _narrator())


@app.post("/api/custom-tiles/draft", response_model=CustomChartDraft)
def draft_custom_tile_api(body: CustomTileDraftRequest) -> CustomChartDraft:
    """Parse pasted data into a chart - not saved yet. Reuses whichever
    provider narration is already configured with; falls back to a plain
    two-column CSV/TSV parse when there's no model or its output doesn't
    validate, so 'paste a label,value table' still works with narration off."""
    from app.dashboard.custom import draft_chart

    try:
        return draft_chart(body.raw_data, body.hint, drafter=_narrator())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/custom-tiles/chat", response_model=TileChatResponse)
def chat_custom_tile_api(body: TileChatRequest) -> TileChatResponse:
    """The tile builder, one turn at a time: describe a chart, look at it, say
    what is wrong, look again.

    Stateless - the conversation and the draft on screen both arrive in the
    request, the same as `/api/agent/chat`. Unlike that route this one is not
    free-form: the model only ever returns a `{title, chart_type, labels,
    values}` object, validated before it reaches the response, and what the
    transcript says about a turn is computed by diffing the two drafts rather
    than taken from the model's own account of what it did."""
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
    from app.narration.providers import DEFAULT_MODELS, ModelConfig
    from app.narration.store import load as load_narration

    current = load_narration()
    agent_cfg = (
        ModelConfig(
            model=current.model or DEFAULT_MODELS["anthropic"],
            api_key=current.api_key,
            base_url=current.base_url,
        )
        if current.enabled and current.provider == "anthropic"
        else None
    )

    with session_scope() as session:
        try:
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
    from app.agent.chat import ChatTurn, ChatUnavailable, chat as run_chat
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
        reply = run_chat(
            turns,
            provider=settings_.provider,
            model=settings_.model,
            api_key=settings_.api_key or None,
            base_url=settings_.base_url or None,
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
    return bundle
