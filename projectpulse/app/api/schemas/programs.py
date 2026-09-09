"""The Program level: the list of programs, and one program's rollup.

Deliberately a different module and route prefix (`/api/programs`, plural)
from `app/api/schemas/program.py` / `GET /api/program` - that one is an
unrelated screen (watched sources, project pairing, the rule table), named
before this product had a real `Program` entity with more than one row.
Reusing its name here would collide two different bundles under one symbol.

Bands, not scores - same reasoning as `portfolio.py`: `ProjectRow`/`Band` are
imported from there rather than redefined, so a project can never disagree
with itself between the Program rollup and the Portfolio screen.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.api.schemas.base import Response
from app.api.schemas.portfolio import Band, ProjectRow


class ProgramSummary(Response):
    """One row on the Programs list (`Layout_Program` image12)."""

    id: str
    name: str
    owner: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    project_count: int = 0
    #: The worst of its projects' bands - same rule as `PortfolioBundle.band`.
    band: Band = "no_data"


class ProgramListBundle(Response):
    programs: list[ProgramSummary] = Field(default_factory=list)
    generated_at: date | None = None


class ResourceRow(Response):
    """One person's allocation on one project within the program."""

    resource_name: str
    role: str | None = None
    project_id: str
    project_name: str
    allocation_percent: float | None = None


class ResourceConflict(Response):
    """One person allocated over 100% combined, across this program's projects.

    This is the "cross-project resource control" a delivery manager cannot see
    from any single project's own page - the whole reason it is a Program-level
    tile rather than a per-project one.
    """

    resource_name: str
    total_allocation_percent: float
    projects: list[str] = Field(default_factory=list)


class ProgramRollupBundle(Response):
    """Everything the Program dashboard's cross-project tiles read from."""

    program: ProgramSummary
    projects: list[ProjectRow] = Field(default_factory=list)
    resources: list[ResourceRow] = Field(default_factory=list)
    resource_conflicts: list[ResourceConflict] = Field(default_factory=list)
    generated_at: date | None = None
