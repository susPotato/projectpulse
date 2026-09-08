"""What the schedule would do if one thing changed, ready to serve.

Shaped like `ExplainBundle`: typed values, not formatted prose. Each figure is
one subtraction over two forward passes, so the page renders `days_earlier` and
`projected_end` directly rather than being handed a sentence with a number
baked into it.

`summary` and the move descriptions carry **no digits** on purpose - the
quantity is always a field beside them. `tests/test_whatif.py` asserts it.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.api.schemas.base import Response


class ScenarioMove(Response):
    """One change to the plan, in terms a PM would recognise."""

    #: `compress` shortens a task; `overlap` lets one start before another ends.
    kind: str
    entity_id: str
    label: str
    days: int
    #: `overlap` only: the predecessor being overlapped.
    against_id: str | None = None
    against_label: str | None = None


class Scenario(Response):
    """One re-run of the forward pass, and what it produced."""

    id: str
    #: Digit-free description. The figures are the fields below.
    summary: str
    moves: list[ScenarioMove] = Field(default_factory=list)
    projected_end: date | None = None
    #: How much sooner delivery lands than doing nothing.
    days_earlier: int = 0
    #: Where that leaves the *original* commitment. Negative means ahead of it.
    #: Measured against today's plan, never against the scenario's own shifted
    #: plan - a scenario compared against its own plan flatters itself.
    days_late: int = 0


class ScenarioBundle(Response):
    """Every scenario for one project, plus what doing nothing costs."""

    project_id: str
    #: The commitment as the sheet states it.
    committed_end: date | None = None
    #: Where the dependencies say it actually lands today.
    projected_end: date | None = None
    days_late: int = 0
    scenarios: list[Scenario] = Field(default_factory=list)

    #: True when the projection this rests on used an inferred dependency. Every
    #: scenario below then rests on the same inference, so it is stated once
    #: here rather than repeated on each row.
    depends_on_inferred_edges: bool = False

    @property
    def best(self) -> Scenario | None:
        return self.scenarios[0] if self.scenarios else None
