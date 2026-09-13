"""CRUD for the risk register, and the heat-map built from it.

The only module that touches a session for this feature - `main.py`'s routes
are thin wrappers around the four functions below, the same split the rest of
the API uses between `intelligence/pipeline.py` and its routes.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app import scope
from app.api.schemas.risk import (
    ProjectOption,
    RiskBundle,
    RiskIn,
    RiskMatrixCell,
    RiskOut,
)
from app.models.domain import Risk
from app.risks.drafts import DRAFT_STATUS
from app.risks.matrix import IMPACTS, LIKELIHOODS, rating_for

#: The org's standard risk categories, from `Layout/fpt-pm-risk.html`. Served
#: on the bundle so the create/edit form's dropdown has one source rather
#: than a copy hand-kept in the front end.
CATEGORIES = (
    "Technology",
    "Financial/Fraud",
    "Delivery of Services",
    "Resource",
    "Security",
    "Operational",
    "Legal/Compliance",
)


def _canonical_project(project_id: str) -> str:
    """The delivery project this risk belongs to, or a refusal naming the id.

    A risk is the one thing in this app a person types rather than the engine
    derives, and `project_id` used to be whatever string arrived. A risk filed
    against `"SAIN"` - the project's *name*, which is what a PM types - was
    accepted, listed in the flat register, and then invisible everywhere that
    asks per project: the project dashboard's risk tile, the Program tab's
    cross-project risk tile, and the `risks` report section's own gate. It
    read as "my risk was not saved" while the row sat in the table.

    So: refuse an id no delivery project claims, and store the canonical id
    for one that is paired (invariant 7), so a risk logged from the Jira side
    of a project is not a second pile from the Excel side.
    """
    project = scope.resolve(project_id)
    if project is None:
        known = ", ".join(p.canonical_id for p in scope.all_projects())
        raise ValueError(
            f"unknown project {project_id!r}; a risk must belong to one of: {known}"
        )
    return project.canonical_id


def _to_out(risk: Risk) -> RiskOut:
    return RiskOut(
        id=risk.id,
        project_id=risk.project_id,
        title=risk.title,
        risk_no=risk.risk_no,
        status=risk.status,  # type: ignore[arg-type]
        key_risk=risk.key_risk,
        description=risk.description,
        category=risk.category,
        secondary_categories=risk.secondary_categories,
        origin=risk.origin,
        cited_task_ids=risk.cited_task_ids,
        review_date=risk.review_date,
        possible_realise_date=risk.possible_realise_date,
        retired_date=risk.retired_date,
        cause_title=risk.cause_title,
        cause_description=risk.cause_description,
        pre_likelihood=risk.pre_likelihood,  # type: ignore[arg-type]
        pre_impact=risk.pre_impact,  # type: ignore[arg-type]
        pre_rating=rating_for(risk.pre_likelihood, risk.pre_impact),
        pre_cost=float(risk.pre_cost) if risk.pre_cost is not None else None,
        pre_delay_days=risk.pre_delay_days,
        post_likelihood=risk.post_likelihood,  # type: ignore[arg-type]
        post_impact=risk.post_impact,  # type: ignore[arg-type]
        post_rating=rating_for(risk.post_likelihood, risk.post_impact),
        post_cost=float(risk.post_cost) if risk.post_cost is not None else None,
        post_delay_days=risk.post_delay_days,
        responsible=risk.responsible,
        created_at=risk.created_at,
        updated_at=risk.updated_at,
    )


def _next_risk_no(session, project_id: str) -> str:
    """The next per-project sequence number, for a risk created without one.

    Numeric risk numbers only - a PM who has typed "4a" is not competing with
    the auto-numberer, and this just continues from the highest integer seen.
    """
    existing = session.scalars(
        select(Risk.risk_no).where(Risk.project_id == project_id)
    ).all()
    highest = 0
    for value in existing:
        if value and value.isdigit():
            highest = max(highest, int(value))
    return str(highest + 1)


def _widen(project_ids: list[str] | None) -> list[str] | None:
    """Every id each requested project may have been filed under.

    Writes store the canonical id, but a caller narrowing to one project may
    hold a paired one (a tile scoped to the Jira side of HRMS), and rows
    written before that rule existed can carry either. Reading by one id and
    finding nothing is precisely the symptom this whole change is about, so
    the read is widened rather than trusting the write to have been tidy.
    """
    if project_ids is None:
        return None
    widened: list[str] = []
    for project_id in project_ids:
        for source_id in scope.source_ids_for(project_id):
            if source_id not in widened:
                widened.append(source_id)
    return widened


def build_matrix(session, project_ids: list[str] | None = None) -> list[RiskMatrixCell]:
    """The 5x5 heat-map: every cell's rating, and how many risks (by their
    pre-treatment assessment - the register's convention, see the mockup)
    landed in it."""
    project_ids = _widen(project_ids)
    query = (
        select(Risk.pre_likelihood, Risk.pre_impact, func.count())
        #: A proposal is not an assessment. A model's guess at a likelihood
        #: would otherwise land in a heat-map cell and read as a judgement
        #: somebody made, which is the one thing the matrix is for.
        .where(Risk.status != DRAFT_STATUS)
        .group_by(Risk.pre_likelihood, Risk.pre_impact)
    )
    if project_ids is not None:
        query = query.where(Risk.project_id.in_(project_ids))
    counts = {(lik, imp): n for lik, imp, n in session.execute(query)}

    return [
        RiskMatrixCell(
            likelihood=likelihood,  # type: ignore[arg-type]
            impact=impact,  # type: ignore[arg-type]
            rating=rating_for(likelihood, impact) or "",
            risk_count=counts.get((likelihood, impact), 0),
        )
        for likelihood in LIKELIHOODS
        for impact in IMPACTS
    ]


def list_risks(session, project_ids: list[str] | None = None) -> RiskBundle:
    """Every risk, program-wide by default - the register is a program-level
    view (`Layout/fpt-pm-risk.html` lists several projects in one table),
    with `project_ids` narrowing it when a caller wants one project."""
    widened = _widen(project_ids)
    #: Drafts are excluded here, not filtered in the front end. The register is
    #: the record of what a person decided; a row nobody has accepted yet has
    #: not been decided, and one query that forgot the filter would put a
    #: model's suggestion into a document that gets sent to a customer.
    query = (
        select(Risk)
        .where(Risk.status != DRAFT_STATUS)
        .order_by(Risk.project_id, Risk.risk_no)
    )
    if widened is not None:
        query = query.where(Risk.project_id.in_(widened))
    rows = session.scalars(query).all()

    return RiskBundle(
        risks=[_to_out(r) for r in rows],
        matrix=build_matrix(session, project_ids),
        likelihoods=list(LIKELIHOODS),
        impacts=list(IMPACTS),
        categories=list(CATEGORIES),
        #: Served with the register for the same reason `categories` is: the
        #: form's project picker has one source of truth, instead of the
        #: front end hard-coding an id and a PM typing over it.
        projects=[
            ProjectOption(project_id=p.canonical_id, name=p.name)
            for p in scope.all_projects()
        ],
    )


def create_risk(session, data: RiskIn) -> RiskOut:
    if not data.project_id:
        raise ValueError("project_id is required")
    if not data.title:
        raise ValueError("title is required")

    project_id = _canonical_project(data.project_id)

    risk = Risk(
        project_id=project_id,
        title=data.title,
        risk_no=data.risk_no or _next_risk_no(session, project_id),
        status=data.status or "Active",
        key_risk=bool(data.key_risk),
        description=data.description,
        category=data.category,
        secondary_categories=data.secondary_categories,
        review_date=data.review_date,
        possible_realise_date=data.possible_realise_date,
        retired_date=data.retired_date,
        cause_title=data.cause_title,
        cause_description=data.cause_description,
        pre_likelihood=data.pre_likelihood,
        pre_impact=data.pre_impact,
        pre_cost=data.pre_cost,
        pre_delay_days=data.pre_delay_days,
        post_likelihood=data.post_likelihood,
        post_impact=data.post_impact,
        post_cost=data.post_cost,
        post_delay_days=data.post_delay_days,
        responsible=data.responsible,
    )
    session.add(risk)
    session.flush()
    return _to_out(risk)


def update_risk(session, risk_id: int, data: RiskIn) -> RiskOut | None:
    """Merge only the fields the request actually set. `None` on the model
    already returns `None` here - the caller turns that into a 404."""
    risk = session.get(Risk, risk_id)
    if risk is None:
        return None

    changes = data.model_dump(exclude_unset=True)
    if changes.get("project_id"):
        changes["project_id"] = _canonical_project(changes["project_id"])
    for field, value in changes.items():
        setattr(risk, field, value)

    session.flush()
    return _to_out(risk)


def delete_risk(session, risk_id: int) -> bool:
    risk = session.get(Risk, risk_id)
    if risk is None:
        return False
    session.delete(risk)
    return True


def list_drafts(session, project_ids: list[str] | None = None) -> list[RiskOut]:
    """The proposals waiting on somebody, newest first.

    Its own function rather than a flag on `list_risks`, so that a caller has
    to *ask* for drafts. The register's readers - the report section, the
    dashboard tile, the program rollup - keep the shape they had and cannot
    acquire a model's suggestion by forgetting a parameter.
    """
    widened = _widen(project_ids)
    query = (
        select(Risk)
        .where(Risk.status == DRAFT_STATUS)
        .order_by(Risk.created_at.desc(), Risk.id.desc())
    )
    if widened is not None:
        query = query.where(Risk.project_id.in_(widened))
    return [_to_out(r) for r in session.scalars(query).all()]


def accept_draft(session, risk_id: int) -> RiskOut | None:
    """Promote one proposal into the register, as the person who read it.

    Three things change and one deliberately does not. It leaves `Draft` for
    `Active`, so every reader of the register now sees it; it is given the next
    sequence number, because until now it had none and a register numbers its
    rows; and `updated_at` moves. `origin` stays `ai_draft` - see the column's
    own note: agreeing with a sentence does not change where it came from, and
    a reader later is entitled to know a model wrote the first version.

    `None` for a risk that does not exist or is not a draft, which the route
    turns into a 404 - accepting an already-accepted risk is a stale page
    pressing a button twice, not an error worth a 500.
    """
    risk = session.get(Risk, risk_id)
    if risk is None or risk.status != DRAFT_STATUS:
        return None
    risk.status = "Active"
    risk.risk_no = risk.risk_no or _next_risk_no(session, risk.project_id)
    session.flush()
    return _to_out(risk)


def dismiss_draft(session, risk_id: int) -> bool:
    """Throw one proposal away. Deleted rather than marked, because a rejected
    suggestion is not a record of anything - nobody asked for it, and keeping a
    pile of them would make the drafts list a place where the same bad idea
    reappears every time somebody looks."""
    risk = session.get(Risk, risk_id)
    if risk is None or risk.status != DRAFT_STATUS:
        return False
    session.delete(risk)
    return True
