"""Guards for the risk register: CRUD, and the rating it derives rather than stores."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas.risk import RiskIn
from app.models.base import Base
from app.risks.service import build_matrix, create_risk, delete_risk, list_risks, update_risk

PROJECT = "excel:Project:1:HRMS"
OTHER_PROJECT = "excel:Project:1:SAIN"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def _risk(**overrides) -> RiskIn:
    fields = dict(
        project_id=PROJECT,
        title="Scope Creep",
        category="Financial/Fraud",
        pre_likelihood="Likely",
        pre_impact="Severe",
        pre_cost=120000,
        pre_delay_days=0,
    )
    fields.update(overrides)
    return RiskIn(**fields)


def test_creating_a_risk_derives_its_rating_rather_than_storing_one(session):
    out = create_risk(session, _risk())

    assert out.id is not None
    assert out.pre_rating == "Very High"  # Likely x Severe, per the matrix
    assert out.post_rating is None  # no post-treatment assessment given yet
    assert out.risk_no == "1"  # auto-numbered, first for this project


def test_project_id_is_required(session):
    with pytest.raises(ValueError):
        create_risk(session, _risk(project_id=None))


def test_title_is_required(session):
    with pytest.raises(ValueError):
        create_risk(session, _risk(title=None))


def test_risk_numbers_auto_increment_per_project(session):
    create_risk(session, _risk(risk_no=None))
    second = create_risk(session, _risk(title="Key Person Dependency", risk_no=None))

    assert second.risk_no == "2"


def test_risk_numbers_do_not_cross_projects(session):
    create_risk(session, _risk(risk_no=None))
    other = create_risk(session, _risk(project_id=OTHER_PROJECT, risk_no=None))

    assert other.risk_no == "1"


def test_an_explicit_risk_no_is_kept(session):
    out = create_risk(session, _risk(risk_no="4a"))
    assert out.risk_no == "4a"


def test_update_changes_only_the_fields_sent(session):
    created = create_risk(session, _risk())

    updated = update_risk(session, created.id, RiskIn(status="Closed"))

    assert updated is not None
    assert updated.status == "Closed"
    assert updated.title == "Scope Creep"  # untouched
    assert updated.pre_rating == "Very High"  # untouched


def test_updating_the_pair_changes_the_derived_rating(session):
    created = create_risk(session, _risk())

    updated = update_risk(
        session, created.id, RiskIn(post_likelihood="Possible", post_impact="Minor")
    )

    assert updated is not None
    assert updated.post_rating == "Low"


def test_updating_a_missing_risk_returns_none(session):
    assert update_risk(session, 999, RiskIn(status="Closed")) is None


def test_delete_removes_the_row(session):
    created = create_risk(session, _risk())

    assert delete_risk(session, created.id) is True
    assert list_risks(session).risks == []


def test_deleting_a_missing_risk_returns_false(session):
    assert delete_risk(session, 999) is False


def test_list_defaults_to_every_project(session):
    create_risk(session, _risk())
    create_risk(session, _risk(project_id=OTHER_PROJECT, risk_no=None))

    bundle = list_risks(session)

    assert {r.project_id for r in bundle.risks} == {PROJECT, OTHER_PROJECT}


def test_list_can_be_narrowed_to_one_project(session):
    create_risk(session, _risk())
    create_risk(session, _risk(project_id=OTHER_PROJECT, risk_no=None))

    bundle = list_risks(session, project_ids=[PROJECT])

    assert [r.project_id for r in bundle.risks] == [PROJECT]


def test_the_matrix_covers_every_cell_even_with_no_risks(session):
    matrix = build_matrix(session)
    assert len(matrix) == 25
    assert all(cell.risk_count == 0 for cell in matrix)
    assert all(cell.rating for cell in matrix)


def test_the_matrix_counts_risks_by_their_pre_treatment_cell(session):
    create_risk(session, _risk())  # Likely x Severe
    create_risk(session, _risk(title="Second", pre_likelihood="Likely", pre_impact="Severe"))
    create_risk(session, _risk(title="Third", pre_likelihood="Rare", pre_impact="Insignificant"))

    matrix = {(c.likelihood, c.impact): c.risk_count for c in build_matrix(session)}

    assert matrix[("Likely", "Severe")] == 2
    assert matrix[("Rare", "Insignificant")] == 1
    assert matrix[("Possible", "Moderate")] == 0


def test_the_bundle_carries_the_vocabulary_the_form_needs(session):
    bundle = list_risks(session)
    assert "Technology" in bundle.categories
    assert bundle.likelihoods
    assert bundle.impacts


# A risk is the one row in this product a person types rather than the engine
# derives, so `project_id` is the one place a typo can file something against a
# project that does not exist. These four are the guard.


def test_a_risk_cannot_be_filed_against_a_project_that_does_not_exist(session):
    """The reported bug: a risk added "to SAIN" landed nowhere.

    `project_id` was a free-text box, so a PM typing the project's *name*
    stored `"SAIN"` - accepted, visible in the flat register, and absent from
    every per-project surface that asks by id. Refusing is the honest answer;
    the id is not a detail the register can quietly guess at.
    """
    with pytest.raises(ValueError) as raised:
        create_risk(session, _risk(project_id="SAIN"))

    # The refusal has to say what *would* work, or it is the same dead end.
    assert "SAIN" in str(raised.value)
    assert OTHER_PROJECT in str(raised.value)


def test_a_risk_filed_under_a_paired_source_id_is_stored_against_the_project(session):
    """Invariant 7 reaching the register: one project, however it was named."""
    out = create_risk(session, _risk(project_id="jira:Project:1:HRMS"))

    assert out.project_id == PROJECT
    assert [r.title for r in list_risks(session, project_ids=[PROJECT]).risks] == [
        "Scope Creep"
    ]


def test_narrowing_by_a_paired_source_id_finds_the_project_s_risks(session):
    """The read is widened too - a tile scoped to the Jira side of HRMS is
    asking about the same project, and rows written before the rule above
    existed can carry either id."""
    create_risk(session, _risk())

    bundle = list_risks(session, project_ids=["jira:Project:1:HRMS"])

    assert [r.title for r in bundle.risks] == ["Scope Creep"]
    assert sum(c.risk_count for c in bundle.matrix) == 1


def test_an_edit_cannot_move_a_risk_onto_a_project_that_does_not_exist(session):
    created = create_risk(session, _risk())

    with pytest.raises(ValueError):
        update_risk(session, created.id, RiskIn(project_id="Example Project"))


def test_the_bundle_carries_the_projects_a_risk_can_belong_to(session):
    """Same reasoning as `categories`: the picker's vocabulary is served, not
    hard-coded in the front end - which is where the wrong default came from."""
    bundle = list_risks(session)

    by_id = {p.project_id: p.name for p in bundle.projects}
    assert by_id[OTHER_PROJECT] == "SAIN"
    assert by_id[PROJECT] == "HRMS Platform"
