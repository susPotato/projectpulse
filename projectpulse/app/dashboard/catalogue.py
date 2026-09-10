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
    "insight", "portfolio", "programs", "risks", "gantt", "forecast", "team"
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
        "People allocated over 100% combined, across this program's projects.",
        "program", "programs", default_w=4, default_h=3,
    ),
    TileSpec(
        "ai_cross_project_brief", "AI Management Brief", "AI Intelligence",
        "The narrative for the program's highest-priority project.",
        "program", "insight", default_w=6, default_h=3, preview="brief",
    ),
    TileSpec(
        "ai_detected_risks_top", "AI Detected Risks", "AI Intelligence",
        "Top findings from the program's highest-priority project.",
        "program", "insight", default_w=6, default_h=3,
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
)

BY_KEY: dict[str, TileSpec] = {tile.key: tile for tile in CATALOGUE}


def for_scope(scope: Scope) -> tuple[TileSpec, ...]:
    return tuple(tile for tile in CATALOGUE if tile.scope == scope)


#: A couple of canned starter layouts for "Browse Templates" (Phase D) -
#: named subsets of `CATALOGUE`, not a separate feature.
TEMPLATES: dict[str, tuple[str, ...]] = {
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
