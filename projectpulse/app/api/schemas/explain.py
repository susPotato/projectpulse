"""The contract for "show me the arithmetic".

A sibling of `InsightBundle`, and deliberately a separate one. The insight bundle
answers *what should I do*; this answers *how did you get that number*, and a PM
opens it only when they want to check. Keeping them apart means the insight
screen stays small and this one can be as detailed as it needs to be.

Nothing here is computed by the API. Every field is a value from the same
`ImpactReport` the findings were built from, so the two views cannot disagree
about a date - if they ever did, the bug would be in serialisation, not in two
rival calculations.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.api.schemas.base import Response


class Operand(Response):
    """One labelled value, on the way in or out."""

    label: str
    value: str
    note: str = ""


class Calc(Response):
    """One arithmetic step, written so a reader can redo it on paper.

    Four fields rather than one sentence, because a reader needs to see the
    operation and the substituted values separately - a formula alone is abstract
    and numbers alone are unverifiable.
    """

    number: int
    question: str
    formula: str
    substituted: str
    result: str
    note: str = ""


class ForwardStep(Response):
    """One task's forward pass, derived as input -> algorithm -> output."""

    entity_id: str
    label: str
    title: str | None = None

    start: date | None = None
    duration_days: int | None = None

    #: Which predecessor set this task's start, if any. `None` means the task's
    #: own plan is the constraint.
    driver: str | None = None
    driver_label: str | None = None
    lag_days: int | None = None
    earliest_start: date | None = None

    projected_end: date | None = None
    planned_end: date | None = None
    baseline_end: date | None = None

    #: **The headline.** Slip the dependency chain implies that the sheet does
    #: not show. `None` means it cannot be computed, which is not the same as 0.
    propagated_days: int | None = None
    variance_days: int | None = None
    recorded_slip_days: int | None = None
    is_inconsistent: bool = False

    #: What the spreadsheet supplied. Anything absent here did not come from it.
    inputs: list[Operand] = Field(default_factory=list)
    #: The operations, in order.
    calc: list[Calc] = Field(default_factory=list)
    #: The figures the findings quote.
    outputs: list[Operand] = Field(default_factory=list)


class RefusedEdge(Response):
    """A dependency the graph would not accept, and why.

    Surfaced rather than swallowed: an edge silently dropped is a path a PM
    thinks exists and the projection does not use.
    """

    predecessor: str
    successor: str
    reason: str


class ExplainBundle(Response):
    """Everything needed to check the schedule arithmetic by hand."""

    project_id: str
    steps: list[ForwardStep] = Field(default_factory=list)

    #: The chain that determines the project's finish - the only sequence a PM
    #: can shorten to pull the date in.
    driving_path: list[str] = Field(default_factory=list)
    project_end_planned: date | None = None
    project_end_projected: date | None = None
    project_slip_days: int | None = None

    #: The same projection using only dependencies a human stated. When it
    #: differs, the headline date rests on an inference and should say so.
    stated_only_end: date | None = None
    depends_on_inferred_edges: bool = False

    edges_stated: int = 0
    edges_inferred: int = 0
    refused_edges: list[RefusedEdge] = Field(default_factory=list)

    #: Every scalar the rule table compares against, so a rule trace can be read
    #: against real values.
    scalars: dict = Field(default_factory=dict)

    @property
    def inconsistent_count(self) -> int:
        return sum(1 for step in self.steps if step.is_inconsistent)
