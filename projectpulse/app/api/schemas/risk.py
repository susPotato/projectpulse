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

#: `Draft` is the state a model-proposed risk sits in until a person accepts
#: it - see `app/risks/drafts.py`. In the same Literal as the register's own
#: states rather than a separate field, because it *is* this row's status and a
#: parallel boolean would let a row be both accepted and a draft.
RiskStatus = Literal["Active", "Closed", "Retired", "Draft"]
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
    #: `None` for a risk a person typed, which is every row the register had
    #: before drafts existed. The only other value is `ai_draft`, and it
    #: survives acceptance - see `Risk.origin`.
    origin: str | None = None
    #: The `Task ID`s a proposal was read from, comma-separated. Empty for a
    #: typed risk, and never empty for a draft: one that cites nothing is
    #: discarded rather than stored.
    cited_task_ids: str | None = None
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


class CitedTask(Response):
    """One task a draft was read from, as the Evidence tab shows it.

    Served beside the drafts rather than joined into them because several
    proposals routinely cite the same task, and repeating a description under
    each would make the panel unreadable and the payload several times larger
    than it needs to be.
    """

    task_id: str
    title: str | None = None
    status: str | None = None
    #: The text the model was actually given, already excerpted to the same
    #: length. Serving the full body would show a reader more than the model
    #: saw, which makes checking the claim against it misleading in the one
    #: direction that matters.
    text: str | None = None


class RiskDraftBundle(Response):
    """The proposals waiting on a person, and everything needed to check them.

    `enabled` and `reason` are why this is a bundle rather than a bare list.
    "The feature is off", "there is no text to read" and "nobody has generated
    any yet" are three different states that all render as zero drafts, and a
    panel that cannot tell them apart teaches people the feature is broken.
    """

    drafts: list[RiskOut] = Field(default_factory=list)
    #: Keyed by `task_id`, covering every id named in every draft's
    #: `cited_task_ids`.
    cited_tasks: list[CitedTask] = Field(default_factory=list)
    #: Whether a model may be asked at all here - `settings.risk_drafts_enabled`.
    enabled: bool = False
    #: Why there is nothing to show, in words fit for the page. `None` when
    #: there is something to show.
    reason: str | None = None
    #: How many tasks on this project carry text a model could read. Shown so
    #: "generate" is not a button that might do nothing without saying why.
    readable_tasks: int = 0
