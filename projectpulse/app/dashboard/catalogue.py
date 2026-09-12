"""The tile catalogue: what a Program or Project dashboard can be built from.

P0 only - the PM's own priority triage in `PiMSatho_Overview.xlsx`'s
`(Program)Tiles List` / `(Project)Tiles List` sorts 66 program tiles and ~90
project tiles into P0 ("bat buoc Phase 1") / P1 / Phase 2. This is the P0
slice, and every entry is a slice of a bundle that **already exists**
(`/api/insight`, `/api/programs/{id}`, `/api/risks`) - nothing here triggers
new backend computation. `data_source` documents which bundle a tile reads;
the frontend `tileRegistry` is the thing that actually renders one.

This module is also the allow-list the AI dashboard generator (Phase D)
validates its output against - a tile key the model invents that is not in
`CATALOGUE` is dropped, the same way `narration/validator.py`'s
`known_entities` stage drops an id the bundle never supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Scope = Literal["program", "project"]
DataSource = Literal[
    "insight",
    "portfolio",
    "programs",
    #: `GET /api/program`, singular - the program *configuration* bundle
    #: (watched sources, the declared project->program pairing, the rule
    #: table), which is a different bundle from `programs`' rollup. The two
    #: names are one letter apart because the two routes are, and
    #: `app/api/schemas/programs.py` explains why that split is deliberate.
    "program",
    "risks",
    "gantt",
    "forecast",
    "team",
]
#: What shape of content this tile renders - purely for the Add Tiles
#: picker's preview swatch (`AddTilesModal`), so a person can tell a stat
#: tile from a list from a heat-map before adding it, without the picker
#: fetching every tile's real data just to draw a thumbnail.
Preview = Literal["stat", "list", "heatmap", "brief", "chart"]


@dataclass(frozen=True)
class TileSpec:
    key: str
    label: str
    category: str
    description: str
    scope: Scope
    data_source: DataSource
    default_w: int = 4
    default_h: int = 3
    preview: Preview = "list"


CATALOGUE: tuple[TileSpec, ...] = (
    # --- Program scope --------------------------------------------------
    TileSpec(
        "program_health", "Program Health", "Program Information",
        "The program's worst project band, and how many projects feed it.",
        "program", "programs", default_w=4, default_h=2, preview="stat",
    ),
    TileSpec(
        "project_portfolio", "Project Portfolio", "Portfolio",
        "Every project in the program, ranked worst first.",
        "program", "programs", default_w=8, default_h=4,
    ),
    TileSpec(
        "project_health_heatmap", "Project Health Heatmap", "Portfolio",
        "Schedule / quality / QA / evidence bands, one row per project.",
        "program", "programs", default_w=8, default_h=4, preview="heatmap",
    ),
    TileSpec(
        "cross_project_risk", "AI Cross-Project Risk", "New",
        "The risk register rolled up across every project in the program.",
        "program", "risks", default_w=8, default_h=4,
    ),
    TileSpec(
        "resource_conflict", "AI Resource Conflict", "New",
        "Where demand on a shared person exceeds their capacity in a month, in "
        "effort-days, apportioned across the projects that lose out.",
        "program", "programs", default_w=4, default_h=3,
    ),
    TileSpec(
        "ai_cross_project_brief", "AI Management Brief", "AI Intelligence",
        "The narrative for the program's highest-priority project.",
        # 4 rows for the same reason `ai_management_brief` is 4: it renders the
        # same narrative, and at h=3 it clipped its own last sentence.
        "program", "insight", default_w=6, default_h=4, preview="brief",
    ),
    TileSpec(
        "ai_detected_risks_top", "AI Detected Risks", "AI Intelligence",
        "Top findings from the program's highest-priority project.",
        "program", "insight", default_w=6, default_h=3,
    ),
    # Added from the PM's own `(Program)Tiles List` - all Phase-1, all a slice
    # of the rollup `/api/programs/{id}` already computes.
    TileSpec(
        "projects_needing_attention", "Projects Needing Attention", "Portfolio",
        "Every project in the program that is not healthy, with the one "
        "sentence that says why - the rollup's own `headline`, not a summary "
        "of it.",
        "program", "programs", default_w=8, default_h=2,
    ),
    TileSpec(
        "top_delayed_projects", "Top Delayed Projects", "Portfolio",
        "The program's projects ranked by the slip their dependencies imply "
        "and their sheets do not show.",
        "program", "programs", default_w=4, default_h=3,
    ),
    TileSpec(
        "resource_contention_split", "Resource Contention Split", "Resource",
        "One shared person's excess demand as it lands on each project - the "
        "apportionment itself, per project rather than per person.",
        # 6 rows: a bar plus two lines per project, and then the footnote that
        # says the column sums to the program's total excess. That footnote is
        # the claim the tile is making - conservation is asserted in
        # `app/intelligence/contention.py`, not hoped for - so it is the last
        # thing that may be allowed to fall off the bottom.
        "program", "programs", default_w=4, default_h=6,
    ),
    TileSpec(
        "team_allocation", "Team Allocation", "Resource",
        "Who is committed to more than one project in this program, and at "
        "what stated percentage on each.",
        # Two lines per person plus the caveat about nominal percentages, which
        # is the line that stops the number being misread - so it must not be
        # the one that falls off the bottom.
        "program", "programs", default_w=6, default_h=6,
    ),
    TileSpec(
        "program_timeline", "Program Timeline", "Schedule",
        "Every project's committed finish on one shared window, and how far "
        "past it the dependency chain implies it will land.",
        # 5 rows: the date axis, a row per project, and the line saying what the
        # tick and the bar mean. At h=4 that last line fell below the fold, and
        # an unlabelled tick on a shared axis is exactly the thing a reader
        # guesses wrong about.
        "program", "programs", default_w=8, default_h=5, preview="chart",
    ),
    # --- Project scope ---------------------------------------------------
    TileSpec(
        "milestones_at_risk", "Milestones at Risk", "Schedule",
        "Milestones whose projected date has slipped past its baseline.",
        "project", "insight", default_w=4, default_h=2, preview="stat",
    ),
    TileSpec(
        "blocking_qa", "Blocking QA", "Requirement/QA",
        "Open QA items marked blocked, and how long they have been aging.",
        "project", "insight", default_w=4, default_h=2, preview="stat",
    ),
    TileSpec(
        "quality_health", "Quality Health", "Quality",
        "The project's quality band and the findings behind it.",
        "project", "insight", default_w=4, default_h=3,
    ),
    TileSpec(
        "risk_matrix", "Risk Matrix", "Risk",
        "The 5x5 likelihood x impact heat-map for this project's risk register.",
        "project", "risks", default_w=6, default_h=4, preview="heatmap",
    ),
    TileSpec(
        "ai_management_brief", "AI Management Brief", "AI Intelligence",
        "The narrated summary: what is at risk, why, and what to do next.",
        # 4 rows, not 3: the brief runs to several sentences and at h=3 the
        # tile clipped its own last one. The PM's triage calls this the entry
        # point, so a truncated first impression is the one place not to save
        # 32px.
        "project", "insight", default_w=6, default_h=4, preview="brief",
    ),
    TileSpec(
        "ai_detected_risks", "AI Detected Risks", "AI Intelligence",
        "Every finding the rule engine raised, worst first, with evidence.",
        "project", "insight", default_w=6, default_h=4,
    ),
    TileSpec(
        "ai_root_cause_impact", "AI Root Cause & Impact", "AI Intelligence",
        "The top finding's causal chain, its downstream impact, and the "
        "recommended action.",
        "project", "insight", default_w=6, default_h=3, preview="brief",
    ),
    TileSpec(
        "schedule_gantt", "Gantt Chart", "Schedule",
        "The dependency-graphed schedule on one shared timeline.",
        "project", "gantt", default_w=8, default_h=6, preview="chart",
    ),
    TileSpec(
        "delivery_forecast", "Delivery Forecast", "Schedule",
        "A range of finish dates resampled from observed drift, by percentile.",
        "project", "forecast", default_w=5, default_h=4, preview="chart",
    ),
    TileSpec(
        "effort_burn", "Effort Burn", "Effort",
        "Cumulative hours logged over time, against the planned total.",
        "project", "team", default_w=5, default_h=4, preview="chart",
    ),
    TileSpec(
        "team_effort", "Team Effort by Person", "Effort",
        "Hours logged against hours planned, one bar per person - real "
        "logged time, never a productivity figure nobody measured.",
        "project", "team", default_w=6, default_h=4, preview="chart",
    ),
    # Added from the PM's own `(Project)Tiles List`: every one of these is
    # marked Phase 1 / Must or Should there, and every one is a slice of a
    # bundle that already exists. Two are deliberately NOT here, and the
    # reason is the same both times - the data to do them honestly is absent:
    #
    #   * "Progress vs Effort" and "Productivity Trend" (both Must) need an
    #     output measure, and `progress` is a self-reported percentage. A
    #     ratio built on it would inherit that and present it as measurement -
    #     see `app/api/schemas/team.py`, which already refuses the same tile
    #     for the same reason.
    #   * "Current Critical Path" needs float per task to be worth reading, and
    #     the forward-pass CPM step that produces float is not built (CLAUDE.md
    #     s0d, "Not done, deliberately"). `gantt.on_driving_path` names the
    #     chain that sets the finish, and `delayed_tasks` flags it as such -
    #     which is the honest half of that tile.
    TileSpec(
        "project_summary", "Project Summary", "Project Information",
        "The context a person needs on opening a project: band, what set it, "
        "committed against projected finish, and task / QA counts.",
        "project", "portfolio", default_w=4, default_h=6,
    ),
    TileSpec(
        "program_context", "Program Context", "Program Information",
        "Which program this project is declared under, which projects it "
        "shares that program with, and what those siblings are taking from "
        "it - the one thing a project cannot see from its own data.",
        "project", "program", default_w=4, default_h=6,
    ),
    TileSpec(
        "schedule_variance", "Schedule Variance", "Schedule",
        "Slip somebody already recorded against the baseline, beside slip the "
        "dependency chain implies and nobody has written down.",
        "project", "gantt", default_w=4, default_h=5, preview="stat",
    ),
    TileSpec(
        "delayed_tasks", "Delayed Tasks", "Schedule",
        "The tasks carrying that slip, worst first, flagged when they sit on "
        "the chain that sets the project's finish.",
        "project", "gantt", default_w=6, default_h=4,
    ),
    TileSpec(
        "upcoming_milestones", "Upcoming Milestones", "Milestone",
        "Each milestone's planned date against the baseline it was committed "
        "to, soonest first.",
        "project", "gantt", default_w=6, default_h=5,
    ),
    TileSpec(
        "risk_register", "Risk Register", "Risk",
        "The registered risks as a table - rating, status, owner - which is "
        "what a PM reads next to the matrix, not instead of it.",
        "project", "risks", default_w=6, default_h=4,
    ),
    TileSpec(
        "mitigation_effect", "Risk Before / After Mitigation", "Risk",
        "Each risk's pre- and post-treatment rating side by side: whether the "
        "mitigation somebody wrote down actually moves the assessment.",
        "project", "risks", default_w=6, default_h=4,
    ),
    TileSpec(
        "ai_recommended_actions", "AI Recommended Actions", "AI Intelligence",
        "Every finding's recommended action, worst first - the decision half "
        "of the insight, on its own so it can be worked through.",
        "project", "insight", default_w=6, default_h=4,
    ),
)

BY_KEY: dict[str, TileSpec] = {tile.key: tile for tile in CATALOGUE}


def for_scope(scope: Scope) -> tuple[TileSpec, ...]:
    return tuple(tile for tile in CATALOGUE if tile.scope == scope)


#: A couple of canned starter layouts for "Browse Templates" (Phase D) -
#: named subsets of `CATALOGUE`, not a separate feature.
#:
#: The first two entries are the matched pair behind "Default setup", and they
#: are designed against each other rather than each being "the good tiles for
#: this level". What makes them a pair is the shape of the model underneath
#: (`app/scope.py`): a program is the thing projects are declared under and the
#: boundary that unit factors and the working-day calendar are scoped to, and
#: one `ProgramContext` is folded into the same `analyze_project()` every
#: project page calls - so a rollup *is* the project view folded up and cannot
#: disagree with it. Two consequences decide the layouts:
#:
#: * **Only the program level can see contention.** Effort excess on a shared
#:   person is apportioned across the projects that lose out (`Sum s_i = E`), so
#:   `resource` is the one band whose cause lives outside the project it marks.
#:   The program default therefore leads with who-is-short and where it lands;
#:   the project default carries `program_context`, which is the same
#:   apportionment read from below - this project's share, named.
#: * **Membership is declared, and may be absent.** A project with
#:   `program_id = NULL` is a real state, never filed under an invented
#:   program. `program_context` says so out loud rather than showing a blank.
#:
#: So the program board answers "which project do I open, and what do they
#: cost each other", and the project board answers "what is wrong here, why,
#: what it costs, and what the program is taking from me" - the second being
#: the first at one altitude down, which is exactly the relationship the
#: `program:Program:0:<KEY>` re-keying made expressible.
TEMPLATES: dict[str, tuple[str, ...]] = {
    #: Ordered for `auto_layout`, which packs left to right and wraps at 12
    #: columns: 4+8 / 8+4 / 4+8 / 6+6 fills four exact rows with no gaps.
    "program_delivery_control": (
        "program_health", "projects_needing_attention",
        "project_health_heatmap", "top_delayed_projects",
        "resource_contention_split", "cross_project_risk",
        "team_allocation", "ai_cross_project_brief",
    ),
    #: 4+4+4 / 6+6 / 4+4+4 / 6+6 / 6+6 / 8 - five exact rows, then the Gantt,
    #: which wants the width it has rather than a partner beside it.
    "project_delivery_control": (
        "project_summary", "program_context", "schedule_variance",
        "ai_management_brief", "ai_recommended_actions",
        "milestones_at_risk", "blocking_qa", "quality_health",
        "delayed_tasks", "upcoming_milestones",
        "risk_register", "mitigation_effect",
        "schedule_gantt",
    ),
    "it_portfolio_dashboard": (
        "program_health", "project_portfolio", "project_health_heatmap",
        "cross_project_risk", "resource_conflict",
    ),
    "sprint_delivery_report": (
        "milestones_at_risk", "blocking_qa", "quality_health",
        "ai_management_brief",
    ),
    #: The four questions this product answers, in order, on one canvas:
    #: what is wrong (brief), why (root cause), what it will cost (Gantt,
    #: forecast), and what corroborates it (burn). Ordered for `auto_layout`
    #: too - it packs left to right and wraps at 12 columns, so 6+6 / 4+4+4 /
    #: 8 / 5+5 fills four tidy rows rather than leaving gaps.
    "project_delivery_review": (
        "ai_management_brief", "ai_root_cause_impact",
        "milestones_at_risk", "blocking_qa", "quality_health",
        "schedule_gantt",
        "delivery_forecast", "effort_burn",
    ),
}


#: What "Default setup" places, per scope. A named entry in `TEMPLATES` rather
#: than a list of its own, so the one-click button and "Browse Templates" can
#: never drift into offering two different things under one idea of "default".
DEFAULT_TEMPLATE: dict[Scope, str] = {
    "program": "program_delivery_control",
    "project": "project_delivery_control",
}
