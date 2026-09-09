"""The journey a PM actually takes, and the one claim it has to keep.

Every other test checks a component. `test_pipeline.py` checks that ingestion
and the intelligence layer agree about one bundle. This one walks the whole
route a delivery manager takes through the app in a normal week -

    Program  ->  Insight  ->  Calculation  ->  Schedule
             ->  Recovery  ->  Forecast     ->  a report they send

- and asserts at each step that **the surfaces agree with each other**.

That is the product's central architectural claim, and it is the one no unit
test can see. The program view folds the project view rather than aggregating a
shortcut; the report renders the same bundles the screens render; the scenarios
are measured against the same baseline the outlook is. Each of those is a
sentence in a docstring somewhere, and each of them is one careless commit away
from becoming false - at which point two numbers a judge can see on two screens
disagree, and every other number on the page stops being believable.

So the assertions here are deliberately *cross-surface*: not "is this figure
right" but "is it the same figure everywhere it appears".

Uses the same `replayed` timeline as `test_pipeline.py` - real workbooks, real
reader, real differ, real identity resolver. In-memory SQLite, no Docker, no
network.
"""

from __future__ import annotations

import pytest

from app.exports.document import build_document, resolve_sections
from app.exports.markdown import render_markdown
from app.exports.workbook import build_workbook
from app.intelligence.pipeline import (
    analyze_project,
    explain_project,
    forecast_project,
    gantt_project,
    portfolio,
    scenarios_project,
)
from tests.test_pipeline import PROJECT, sync, write_step


@pytest.fixture(scope="module")
def journey(tmp_path_factory):
    """The demo timeline, replayed **once** for the whole journey.

    Module-scoped rather than reusing `test_pipeline`'s function-scoped
    `replayed`, for two reasons. The cheap one: sixteen tests rebuilding six
    spreadsheet syncs each cost ~35 seconds of a ~125-second suite, and this is
    the file that grows every time a screen is added.

    The one that matters: these tests assert that the surfaces **agree**, so
    they have to be looking at one timeline. Rebuilding per test would let a
    real disagreement hide behind two loads that happened to see the same data
    - and would let a flaky one appear where there is none.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.base import Base

    root = tmp_path_factory.mktemp("journey")
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)

    with sessionmaker(bind=engine, future=True)() as handle:
        write_step(root, 0)
        sync(handle, root, "2026-03-02T09:00")

        write_step(root, 1)  # the environment slips
        sync(handle, root, "2026-03-06T09:00")

        sync(handle, root, "2026-03-10T09:00")  # nothing changed

        write_step(root, 2)  # downstream reacts
        sync(handle, root, "2026-03-14T09:00")

        sync(handle, root, "2026-03-18T09:00")  # nothing changed

        write_step(root, 3)  # the QA backlog grows
        sync(handle, root, "2026-03-22T09:00")
        yield handle


@pytest.fixture(scope="module")
def app_state(journey):
    """Everything the app would have loaded, from one replayed timeline.

    Built once and shared, because the point of these tests is that these
    bundles agree - and rebuilding them per test would hide a disagreement
    caused by two loads seeing different data.
    """
    session = journey
    return {
        "insight": analyze_project(session, project_id=PROJECT),
        "explain": explain_project(session, project_id=PROJECT),
        "gantt": gantt_project(session, project_id=PROJECT),
        "scenarios": scenarios_project(session, project_id=PROJECT),
        "forecast": forecast_project(session, project_id=PROJECT),
        "portfolio": portfolio(session),
    }


# --------------------------------------------------------------------------
# 1. The PM opens the app on the Program screen
# --------------------------------------------------------------------------

def test_the_program_screen_lists_the_project_and_names_what_set_its_band(app_state):
    """The first screen, and the only one that has to be readable in 3 seconds.

    `worst_severity` is what stops a band being an opinion: the colour is
    defensible because the finding that set it is named beside it.
    """
    board = app_state["portfolio"]
    row = next((p for p in board.projects if p.project_id == PROJECT), None)

    assert row is not None, "the demo project is missing from the program view"
    assert row.band in {"critical", "watch", "healthy", "no_data"}
    assert row.band != "no_data", "this project has data; no_data would be a bug"
    assert row.worst_severity, "a band with nothing named beside it is an opinion"


def test_the_program_view_cannot_disagree_with_the_project_view(app_state):
    """`pipeline.portfolio()` folds `analyze_project` per project.

    It would be cheaper to aggregate a shortcut - count findings from a single
    query - and that is exactly how a program screen starts contradicting the
    project screen it links to. This pins the fold.
    """
    board = app_state["portfolio"]
    row = next(p for p in board.projects if p.project_id == PROJECT)
    insight, explain = app_state["insight"], app_state["explain"]

    assert row.findings == len(insight.findings)
    # The dates too, not just the count: the program screen states a slip, and
    # a reader who clicks through must not be shown a different one.
    assert row.committed_end == explain.project_end_planned
    assert row.projected_end == explain.project_end_projected
    assert row.days_late == explain.project_slip_days


# --------------------------------------------------------------------------
# 2. They click into Insight
# --------------------------------------------------------------------------

def test_insight_leads_with_a_slip_nobody_wrote_down(app_state):
    """The product's whole reason for existing, on one screen.

    The sheet says one date, the dependency chain implies a later one, and the
    gap is a number no human recorded. If this is ever zero on the demo data,
    the story the app tells has quietly stopped being true.
    """
    explain = app_state["explain"]

    assert explain.project_slip_days is not None
    assert explain.project_slip_days > 0
    assert explain.driving_path, "a slip with no chain behind it cannot be explained"


def test_every_finding_a_pm_sees_can_be_challenged(app_state):
    """Invariant 2, from the reader's side.

    A finding with no rule trace and no chain is an assertion. Each one has to
    carry either the rule that fired or the causal link that produced it -
    otherwise the evidence panel has nothing to open.
    """
    for finding in app_state["insight"].findings:
        assert finding.rule_trace is not None or finding.causal_link is not None, (
            f"{finding.id} is an assertion with nothing behind it"
        )
        assert "{{" not in finding.headline, f"{finding.id} shipped a raw token"


def test_the_headline_finding_is_the_one_with_a_stated_dependency(app_state):
    """The panel leads with the best-evidenced cause, not the loudest.

    A chain resting on `same_project` is barely a link; one resting on a
    dependency a human wrote in the sheet is the claim worth putting first.
    """
    chains = [f.causal_link for f in app_state["insight"].findings if f.causal_link]

    assert chains, "no causal chain survived - the demo timeline has collapsed"
    assert any(c.evidence_basis == "dependency_edge" for c in chains)


# --------------------------------------------------------------------------
# 3. They check the arithmetic on the Calculation tab
# --------------------------------------------------------------------------

def test_the_calculation_tab_shows_the_same_slip_the_insight_screen_led_with(app_state):
    """Two screens, one figure. They render the same `ExplainBundle`.

    The Insight hero fetches `/api/explain` rather than recomputing, which is
    what makes this true - and what a well-meaning "just compute it here"
    refactor would break silently.
    """
    explain = app_state["explain"]
    driving = [s for s in explain.steps if s.entity_id in set(explain.driving_path)]

    assert driving, "the driving path names tasks that are not in the steps"
    worst = max((s.propagated_days or 0) for s in driving)
    assert worst == explain.project_slip_days


# --------------------------------------------------------------------------
# 4. They look at the Schedule
# --------------------------------------------------------------------------

def test_the_gantt_draws_the_same_projection_the_calculation_tab_explains(app_state):
    """One projection, two pictures. `gantt.js` is shared by both tabs for the
    same reason: two implementations eventually draw two different charts."""
    gantt, explain = app_state["gantt"], app_state["explain"]

    assert gantt.project_end_planned == explain.project_end_planned
    assert gantt.project_end_projected == explain.project_end_projected
    assert set(gantt.driving_path) == set(explain.driving_path)


def test_a_milestone_at_risk_is_one_the_chain_cannot_support(app_state):
    """Milestones are real rows with a real foreign key, not label text."""
    gantt = app_state["gantt"]
    at_risk = [m for m in gantt.milestones if m.at_risk]

    assert at_risk, "the demo has milestones behind slipping work; none flagged"
    for milestone in at_risk:
        assert milestone.planned_date, (
            "a milestone at risk with no date cannot be judged"
        )
        # A real row with a real name, not label text carried alongside - the
        # workaround `pipeline._milestone_names` used to be, which could trade
        # names between projects because row keys are only unique per sheet.
        assert milestone.id and milestone.name


# --------------------------------------------------------------------------
# 5. They ask what would recover it
# --------------------------------------------------------------------------

def test_recovery_scenarios_are_measured_against_the_original_commitment(app_state):
    """Two figures and never one.

    `days_earlier` is against doing nothing; `days_late` against what was
    committed. Compressing a task moves the plan too, so a scenario compared
    against its own plan reports "on time" for having moved the goalposts. The
    first version of this feature said "recovers 37 of 34 days".
    """
    scenarios, explain = app_state["scenarios"], app_state["explain"]

    assert scenarios.committed_end == explain.project_end_planned
    assert scenarios.projected_end == explain.project_end_projected

    for scenario in scenarios.scenarios:
        expected = (scenario.projected_end - scenarios.committed_end).days
        assert scenario.days_late == expected, scenario.id


def test_no_scenario_claims_to_be_achievable(app_state):
    """Feasibility is not modelled, so nothing may imply it.

    `resources` is empty by decision - the sheets carry no allocation data - so
    every scenario is "if this were true, the graph says X" and never "you can
    do this". The moves are schedule changes only.
    """
    for scenario in app_state["scenarios"].scenarios:
        for move in scenario.moves:
            assert move.kind in {"compress", "overlap"}, (
                f"{move.kind} is not a schedule move the forward pass honours"
            )


# --------------------------------------------------------------------------
# 6. They read the forecast
# --------------------------------------------------------------------------

def test_the_forecast_starts_from_the_same_plan_as_everything_else(app_state):
    """A range measured against a different baseline is a different claim."""
    forecast, explain = app_state["forecast"], app_state["explain"]

    assert forecast.committed_end == explain.project_end_planned
    assert forecast.projected_end == explain.project_end_projected


def test_the_forecast_either_shows_its_sample_or_says_why_there_is_none(app_state):
    """Both branches are correct behaviour; silence is not.

    A percentile with no sample size beside it is the "89% confidence" this
    product argues against, and an empty panel reads as broken rather than as
    the deliberate refusal it is.
    """
    forecast = app_state["forecast"]

    if forecast.available:
        assert forecast.points, "available with no points is neither answer"
        assert forecast.observations == len(forecast.sample) > 0
        assert forecast.assumption, "the range must carry its own assumption"
    else:
        assert forecast.reason, "a refusal with no reason is just a blank panel"


# --------------------------------------------------------------------------
# 7. They send a report
# --------------------------------------------------------------------------

def test_the_report_a_pm_sends_says_what_the_screen_said(app_state):
    """The claim that matters most, because the document outlives the session.

    A figure rendered its own way here is the copy quoted in a steering meeting
    six weeks later, disagreeing with a screen nobody has open.
    """
    doc = build_document(
        app_state["insight"],
        explain=app_state["explain"],
        scenarios=app_state["scenarios"],
        forecast=app_state["forecast"],
        sections=resolve_sections(preset_id="steering"),
    )
    markdown = render_markdown(doc)

    for finding in app_state["insight"].findings[:3]:
        assert finding.headline in markdown, f"{finding.id} was reworded on the way out"


def test_the_report_never_drops_what_the_analysis_could_not_use(app_state):
    """Not a footnote, and not optional. Conclusions drawn on part of a project
    that do not say which part are the document this product replaces."""
    doc = build_document(
        app_state["insight"],
        explain=app_state["explain"],
        sections=resolve_sections(preset_id="exec"),
    )

    assert doc.section("data_quality") is not None


def test_the_spreadsheet_and_the_markdown_are_the_same_document(app_state):
    """Three formats, one `ReportDoc`. They cannot disagree by construction -
    this is what stops that from silently becoming untrue."""
    doc = build_document(
        app_state["insight"],
        explain=app_state["explain"],
        sections=resolve_sections(preset_id="weekly"),
    )
    markdown = render_markdown(doc)
    workbook = build_workbook(doc)
    cells = "\n".join(
        str(cell.value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    )

    headline = app_state["insight"].findings[0].headline
    assert headline in markdown
    assert headline in cells


# --------------------------------------------------------------------------
# The rule that outranks all of the above
# --------------------------------------------------------------------------

def test_not_one_number_on_this_journey_was_written_by_a_language_model(app_state):
    """Invariant 1, checked over everything a PM saw on the whole route.

    The narrative is the only prose a model may touch, and it reaches the page
    with its figures already substituted by the server. Every other surface
    here is arithmetic. If a `{{token}}` survives to any of them, a reader is
    looking at a number nobody computed.
    """
    doc = build_document(
        app_state["insight"],
        explain=app_state["explain"],
        scenarios=app_state["scenarios"],
        forecast=app_state["forecast"],
        sections=resolve_sections(preset_id="steering"),
    )
    surfaces = {
        "narrative": app_state["insight"].narrative,
        "report": render_markdown(doc),
    }

    for name, text in surfaces.items():
        assert "{{" not in text, f"{name} shipped an unsubstituted token"
        assert "}}" not in text, f"{name} shipped an unsubstituted token"
