"""The risk register contract.

Genuinely unlike every other bundle in this package: nothing here is derived
from a sheet, and there is no evidence pointer, because a PM's own judgement
is the data - see `app/models/domain.py`'s `Risk` docstring. The one
exception is `pre_rating` / `post_rating`: a pure lookup of the likelihood and
impact a PM chose (`app/risks/matrix.py`), never a field either request body
can set directly, so a client cannot post a rating that disagrees with its
own pair.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.api.schemas.base import Response

RiskStatus = Literal["Active", "Closed", "Retired"]
Likelihood = Literal["Almost Certain", "Likely", "Possible", "Unlikely", "Rare"]
Impact = Literal["Insignificant", "Minor", "Moderate", "Major", "Severe"]


class RiskIn(Response):
    """What a PM may set when creating or editing a risk.

    Every field optional: `POST` fills in defaults for what is missing,
    `PUT` changes only the fields a request body actually names, the same
    "send only what changed" shape `NarrationSettingsIn` uses for settings.
    """

    project_id: str | None = None
    title: str | None = None
    risk_no: str | None = None
    status: RiskStatus | None = None
    key_risk: bool | None = None
    description: str | None = None
    category: str | None = None
    secondary_categories: str | None = None
    review_date: date | None = None
    possible_realise_date: date | None = None
    retired_date: date | None = None
    cause_title: str | None = None
    cause_description: str | None = None
    pre_likelihood: Likelihood | None = None
    pre_impact: Impact | None = None
    pre_cost: float | None = None
    pre_delay_days: int | None = None
    post_likelihood: Likelihood | None = None
    post_impact: Impact | None = None
    post_cost: float | None = None
    post_delay_days: int | None = None
    responsible: str | None = None


class RiskOut(Response):
    """One risk, as served. `pre_rating` / `post_rating` are computed, never stored."""

    id: int
    project_id: str
    title: str
    risk_no: str | None = None
    status: RiskStatus = "Active"
    key_risk: bool = False
    description: str | None = None
    category: str | None = None
    secondary_categories: str | None = None
    review_date: date | None = None
    possible_realise_date: date | None = None
    retired_date: date | None = None
    cause_title: str | None = None
    cause_description: str | None = None
    pre_likelihood: Likelihood | None = None
    pre_impact: Impact | None = None
    pre_rating: str | None = None
    pre_cost: float | None = None
    pre_delay_days: int | None = None
    post_likelihood: Likelihood | None = None
    post_impact: Impact | None = None
    post_rating: str | None = None
    post_cost: float | None = None
    post_delay_days: int | None = None
    responsible: str | None = None
    created_at: datetime
    updated_at: datetime


class RiskMatrixCell(Response):
    """One cell of the 5x5 heat-map: its rating, and how many risks sit in it."""

    likelihood: Likelihood
    impact: Impact
    rating: str
    risk_count: int = 0


class ProjectOption(Response):
    """One project a risk may be filed against, for the form's picker.

    `project_id` is the canonical id from `app/scope.py`, `name` is what a
    person calls it. Both halves matter: a picker showing only names cannot
    post, and one showing only ids asks a PM to know that "SAIN" is spelled
    `excel:Project:1:SAIN` - which is exactly what a free-text box asked, and
    exactly how risks ended up filed against a project that does not exist.
    """

    project_id: str
    name: str


class RiskBundle(Response):
    """Everything the risk register screen renders."""

    risks: list[RiskOut] = Field(default_factory=list)
    matrix: list[RiskMatrixCell] = Field(default_factory=list)
    likelihoods: list[str] = Field(default_factory=list)
    impacts: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    #: Every project a risk can belong to - the vocabulary of the one field a
    #: person used to have to type from memory.
    projects: list[ProjectOption] = Field(default_factory=list)
