"""The program view: every delivery project, ranked, with bands not scores.

The design's Program screen. One deliberate departure from the mockup, and it
is the same one the rest of the product makes: the mockup shows a health
**score** of 68, and this reports a **band** - `critical` / `watch` / `healthy`
/ `no_data`.

A score has to come from weighting things that are not comparable (a blocked QA
queue against a slipped milestone) with coefficients nobody can defend, and it
would be computed on partial data besides. A band is a statement the rules
already make. `worst_severity` says which rule set it, so a reader can ask.

`no_data` is its own band and never `healthy`: a project with no ingested sheet
is unknown, and colouring unknown green is the failure this whole product
argues against.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field

from app.api.schemas.base import Response

Band = Literal["critical", "watch", "healthy", "no_data"]

#: The dimensions the design's heatmap shows, in its order.
DIMENSIONS = ("schedule", "quality", "qa", "evidence")


class ProjectRow(Response):
    """One delivery project, whatever number of sources fed it."""

    project_id: str
    name: str
    #: Every source id folded into this row - the reason it appears once.
    source_ids: list[str] = Field(default_factory=list)

    band: Band = "no_data"
    #: `high`, `medium`, ... - which finding set the band, so it can be checked.
    worst_severity: str | None = None
    findings: int = 0

    task_count: int = 0
    committed_end: date | None = None
    projected_end: date | None = None
    #: Slip the dependencies imply and the sheet does not show. The one number
    #: on this screen, because it is a subtraction over two dates.
    days_late: int = 0

    milestones_at_risk: int = 0
    qa_blocked: int = 0
    qa_count: int = 0

    #: Band per dimension, keyed by `DIMENSIONS`.
    bands: dict[str, Band] = Field(default_factory=dict)

    #: The single most important thing about this project, already substituted.
    headline: str | None = None
    depends_on_inferred_edges: bool = False


class PortfolioBundle(Response):
    """Everything the program screen renders."""

    program_name: str = ""
    generated_at: date | None = None
    projects: list[ProjectRow] = Field(default_factory=list)

    @property
    def band(self) -> Band:
        """The program's band: the worst of its projects.

        Deliberately not an average. A program with one critical project and
        nine healthy ones is not 90% healthy; it has a critical project.
        """
        for level in ("critical", "watch", "healthy"):
            if any(row.band == level for row in self.projects):
                return level  # type: ignore[return-value]
        return "no_data"

    @property
    def worst_slip(self) -> int:
        return max((row.days_late for row in self.projects), default=0)
