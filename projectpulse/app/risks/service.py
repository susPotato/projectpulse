"""CRUD for the risk register, and the heat-map built from it.

The only module that touches a session for this feature - `main.py`'s routes
are thin wrappers around the four functions below, the same split the rest of
the API uses between `intelligence/pipeline.py` and its routes.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.api.schemas.risk import RiskBundle, RiskIn, RiskMatrixCell, RiskOut
from app.models.domain import Risk
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


def build_matrix(session, project_ids: list[str] | None = None) -> list[RiskMatrixCell]:
    """The 5x5 heat-map: every cell's rating, and how many risks (by their
    pre-treatment assessment - the register's convention, see the mockup)
    landed in it."""
    query = select(Risk.pre_likelihood, Risk.pre_impact, func.count()).group_by(
        Risk.pre_likelihood, Risk.pre_impact
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
    query = select(Risk).order_by(Risk.project_id, Risk.risk_no)
    if project_ids is not None:
        query = query.where(Risk.project_id.in_(project_ids))
    rows = session.scalars(query).all()

    return RiskBundle(
        risks=[_to_out(r) for r in rows],
        matrix=build_matrix(session, project_ids),
        likelihoods=list(LIKELIHOODS),
        impacts=list(IMPACTS),
        categories=list(CATEGORIES),
    )


def create_risk(session, data: RiskIn) -> RiskOut:
    if not data.project_id:
        raise ValueError("project_id is required")
    if not data.title:
        raise ValueError("title is required")

    risk = Risk(
        project_id=data.project_id,
        title=data.title,
        risk_no=data.risk_no or _next_risk_no(session, data.project_id),
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
