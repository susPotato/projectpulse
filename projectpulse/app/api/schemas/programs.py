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
    """One row on the Programs list (`Layout_Program` image12).

    Carries its own `projects`, not just a count - the Programs list is the
    single entry point into the Program -> Project hierarchy (there is no
    separate "Project" tab, deliberately), so picking a project has to be
    possible from the same page as picking a program, without a click into
    the program's own dashboard first.
    """

    id: str
    name: str
    owner: str | None = None
    status: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    project_count: int = 0
    #: The worst of its projects' bands - same rule as `PortfolioBundle.band`.
    band: Band = "no_data"
    projects: list[ProjectRow] = Field(default_factory=list)


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


class ProjectShortfall(Response):
    """One project's apportioned share of a person's excess demand.

    `effort_days` is the conserved quantity and the only one safe to add up.
    `delay_days` is a scenario: it holds only if the shortfall lands in a later
    window that has room, and summing it across projects re-creates exactly the
    replication error the apportionment exists to remove - so it is rendered per
    row, never totalled.
    """

    project_id: str
    project_name: str
    effort_days: float
    delay_days: float


class ResourceConflict(Response):
    """One person whose committed demand exceeds their capacity in a window.

    This is the "cross-project resource control" a delivery manager cannot see
    from any single project's own page - the whole reason it is a Program-level
    tile rather than a per-project one.

    **It is no longer "allocated over 100%".** That test ignored the dates, so
    two 60% allocations in non-overlapping quarters read as a 120% conflict that
    did not exist, while a simultaneous 50% and 40% read as clean even though a
    person is not 100% available to project work. What is computed now is the
    excess of windowed demand over discounted supply, apportioned across the
    projects that lose out. `total_allocation_percent` is kept because it is the
    number a PM recognises from their own resource plan, but it is a label now,
    not the test.
    """

    resource_name: str
    total_allocation_percent: float
    projects: list[str] = Field(default_factory=list)

    #: Demand, supply and excess in effort-days over `window_label`. The excess
    #: is the finding; the other two are what make it arguable.
    demand_days: float = 0.0
    supply_days: float = 0.0
    excess_days: float = 0.0
    window_label: str = ""
    working_days: int = 0
    #: 'proportional' (nobody protected - the default) or 'priority'.
    mode: str = ""
    shortfalls: list[ProjectShortfall] = Field(default_factory=list)
    #: How the overload would be absorbed, in words, and what it costs in
    #: overtime hours. A delay figure must never appear without this beside it.
    absorption: str = ""
    overtime_hours: float = 0.0
    breaches_overtime_limit: bool = False
    #: Caveats that belong next to the number - a derived rather than stated
    #: allocation window, the availability factor applied to supply.
    notes: list[str] = Field(default_factory=list)


class ProgramRollupBundle(Response):
    """Everything the Program dashboard's cross-project tiles read from."""

    program: ProgramSummary
    projects: list[ProjectRow] = Field(default_factory=list)
    resources: list[ResourceRow] = Field(default_factory=list)
    resource_conflicts: list[ResourceConflict] = Field(default_factory=list)
    generated_at: date | None = None
