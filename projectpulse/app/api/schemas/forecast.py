"""The delivery forecast contract: a range, and everything needed to doubt it.

Three fields on this bundle are not decoration and must reach every surface
that shows `points`:

- `observations` - how many measured drifts the range rests on. A percentile
  without its sample size is the "89% confidence" the pitch deck has to lose.
- `sample` - the drifts themselves, so a reader can check the basis rather than
  trust it.
- `assumption` - the one modelling choice the module makes, in a sentence,
  served rather than written into the front end so the page cannot quietly stop
  saying it.

When `available` is false there are no points and `reason` says why. The reason
is the product working, not an error: see `intelligence/schedule/forecast.py`.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.api.schemas.base import Response


class ForecastObservation(Response):
    """One measured drift, so the sample can be read rather than trusted."""

    entity_id: str
    label: str
    committed: date
    planned: date
    days: int


class ForecastPoint(Response):
    """One percentile of the resampled finish date."""

    percentile: int
    finish: date
    #: Days past the **committed** finish, never past the current plan.
    days_late: int


class ForecastBundle(Response):
    """The delivery forecast for one project."""

    project_id: str
    available: bool = False
    #: Why there is no range. Rendered verbatim when `available` is false.
    reason: str = ""

    #: What was committed, and where the forward pass alone lands. Both are
    #: served so a surface can show the range against the deterministic figure
    #: rather than instead of it - they answer different questions.
    committed_end: date | None = None
    projected_end: date | None = None

    points: list[ForecastPoint] = Field(default_factory=list)
    observations: int = 0
    sample: list[ForecastObservation] = Field(default_factory=list)
    open_tasks: int = 0
    trials: int = 0

    #: The method, in one sentence, and the assumption it rests on. Served so
    #: that every surface says the same thing and none of them can drop it.
    method: str = ""
    assumption: str = ""
