"""Guards for the narration layer: the validator gate and the fallback prose.

The validator tests are adversarial on purpose. They are written as the failures
a careless model actually produces - a number rewritten as a word, a task id that
looks plausible, a "because" attached to two facts nobody linked - rather than as
coverage of the code that catches them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.schemas.insight import (
    CausalLink,
    ChainStep,
    DataQuality,
    Finding,
    InsightBundle,
    TimeInterval,
)
from app.narration.fallback import render_narrative
from app.narration.validator import validate_draft

FACTS = {"days": "12", "count": "6", "task": "WBS-108"}
ENTITIES = {"WBS-108", "WBS-114", "QA-001"}

GOOD = (
    "Environment Setup slipped by {{days}} days. {{count}} downstream items "
    "moved in step. The team should re-baseline before the next review."
)


def validate(draft, **kw):
    kw.setdefault("facts", FACTS)
    kw.setdefault("allowed_entities", ENTITIES)
    return validate_draft(draft, **kw)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_a_clean_draft_passes_all_eight_stages():
    result = validate(GOOD)

    assert result.ok, result.summary
    assert len(result.stages_run) == 8


def test_a_literal_number_is_rejected():
    """The load-bearing rule: a model may never write a digit."""
    result = validate("Environment Setup slipped by 12 days and nothing else moved.")

    assert not result.ok
    assert any(i.stage == "no_literal_digits" for i in result.issues)


def test_a_number_hidden_in_prose_is_still_rejected():
    result = validate(
        "Environment Setup slipped and 6 downstream items moved with it, roughly."
    )

    assert any(i.stage == "no_literal_digits" for i in result.issues)


def test_an_invented_token_is_rejected():
    """A model inventing a figure has to invent a token name to carry it."""
    result = validate(
        "Environment Setup slipped by {{made_up_number}} days, which is a problem."
    )

    assert any(i.stage == "known_tokens" for i in result.issues)


def test_dropping_a_required_number_is_rejected():
    """Quietly omitting a figure is a silent downgrade of the finding."""
    result = validate(
        "Environment Setup slipped and several downstream items moved with it.",
        required_tokens={"days"},
    )

    assert any(i.stage == "required_tokens" for i in result.issues)


def test_an_invented_task_id_is_rejected():
    result = validate(
        "Environment Setup slipped, and WBS-999 moved with it as a consequence here."
    )

    issue = next(i for i in result.issues if i.stage == "known_entities")
    assert "WBS-999" in issue.detail


def test_a_real_task_id_is_accepted():
    assert validate(
        "Environment Setup slipped by {{days}} days, and WBS-114 moved with it."
    ).ok


def test_asserting_a_cause_without_a_chain_is_rejected():
    """The one claim the whole system exists to make carefully."""
    result = validate(
        "The QA queue has stalled because Environment Setup slipped by {{days}} days."
    )

    assert any(i.stage == "no_unsupported_causation" for i in result.issues)


def test_the_same_sentence_passes_when_a_chain_exists():
    assert validate(
        "The QA queue has stalled because Environment Setup slipped by {{days}} days.",
        has_causal_link=True,
    ).ok


def test_stating_a_projection_as_a_certainty_is_rejected():
    result = validate(
        "The UAT milestone will definitely be missed given the {{days}} day slip.",
        has_causal_link=True,
    )

    assert any(i.stage == "no_overclaimed_certainty" for i in result.issues)


def test_model_meta_commentary_is_rejected():
    result = validate(
        "As an AI, I cannot be certain, but the schedule slipped by {{days}} days."
    )

    assert any(i.stage == "no_model_artifacts" for i in result.issues)


def test_a_code_fence_is_rejected():
    result = validate(GOOD + "\n```json\n{}\n```")

    assert any(i.stage == "no_model_artifacts" for i in result.issues)


def test_an_empty_draft_is_rejected():
    assert any(i.stage == "shape" for i in validate("   ").issues)


def test_every_stage_runs_even_after_one_fails():
    """A prompt is easier to fix when every objection is visible at once."""
    result = validate("As an AI I must say 12 things happened because of WBS-999.")

    assert len(result.stages_run) == 8
    assert len({i.stage for i in result.issues}) >= 3


def test_the_summary_is_one_line_suitable_for_a_fallback_reason():
    summary = validate("only 3 words").summary

    assert "\n" not in summary
    assert summary != "passed"


# --------------------------------------------------------------------------
# The fallback
# --------------------------------------------------------------------------


def _bundle(**kw) -> InsightBundle:
    return InsightBundle(
        project_id="p",
        generated_at=datetime(2026, 3, 22, tzinfo=timezone.utc),
        as_of=datetime(2026, 3, 22, tzinfo=timezone.utc),
        **kw,
    )


def _finding(**kw) -> Finding:
    base = dict(
        id="f",
        category="schedule_risk",
        severity="high",
        headline="The plan cannot hold.",
        recommendation="Re-baseline.",
    )
    base.update(kw)
    return Finding(**base)


def _link() -> CausalLink:
    step = ChainStep(
        entity_id="excel:Task:1:WBS-108",
        entity_label="WBS-108",
        field="planned_end",
        occurred=TimeInterval(
            lower=datetime(2026, 3, 2, tzinfo=timezone.utc),
            upper=datetime(2026, 3, 6, tzinfo=timezone.utc),
            precision="bounded",
        ),
    )
    return CausalLink(
        template_id="dependency_slip_hits_successor",
        template_name="Dependency slip hit its successor",
        question="Which of my tasks slipped because something upstream slipped?",
        cause=step,
        effect=step,
        evidence_basis="dependency_edge",
        lag_days_min=4.0,
        lag_days_max=12.0,
        ordering_basis="bounded_disjoint",
    )


def test_an_empty_bundle_still_produces_a_complete_narrative():
    """The offline safety net must never yield a blank page."""
    text = render_narrative(_bundle())

    for heading in ("What is at risk", "Why it is happening", "What to do next"):
        assert heading in text


def test_with_no_chain_the_narrative_refuses_to_explain():
    text = render_narrative(_bundle(findings=[_finding()]))

    assert "No cause can be established" in text


def test_a_chain_produces_an_explanation_naming_its_basis():
    text = render_narrative(
        _bundle(findings=[_finding(category="root_cause", causal_link=_link())])
    )

    assert "dependency stated in the schedule sheet" in text


def test_a_bounded_ordering_is_explained_rather_than_hidden():
    text = render_narrative(
        _bundle(findings=[_finding(category="root_cause", causal_link=_link())])
    )

    assert "range rather than a point" in text


def test_an_inferred_edge_caveat_reaches_the_narrative():
    text = render_narrative(
        _bundle(
            findings=[_finding()],
            data_quality=DataQuality(depends_on_inferred_edges=True),
        )
    )

    assert "inferred from" in text


def test_the_narrative_never_invents_a_number():
    """It selects sentences; it does not compute. Every digit comes from a finding."""
    text = render_narrative(_bundle(findings=[_finding(headline="Slipped by 12 days.")]))

    assert "12" in text
    assert text.count("12") == 1


def test_the_explanation_leads_with_the_chain_not_the_summary_about_chains():
    """An informational finding can carry a chain to illustrate itself.

    Leading with "2 of 50 findings trace to a dependency" tells a PM how well
    evidenced the answer is without ever giving them the answer.
    """
    summary = _finding(
        id="root_cause_available",
        category="root_cause",
        severity="info",
        headline="2 of 50 findings trace to a stated dependency.",
        causal_link=_link(),
        evidence_basis="dependency_edge",
    )
    real = _finding(
        id="chain:dependency_slip_hits_successor:0",
        category="root_cause",
        severity="high",
        headline="WBS-108 moved later; WBS-114 moved with it.",
        causal_link=_link(),
        evidence_basis="dependency_edge",
    )

    text = render_narrative(_bundle(findings=[summary, real]))
    why = text.split("Why it is happening\n")[1].split("\n\n")[0]

    assert why.startswith("WBS-108 moved later")


def test_a_better_evidenced_chain_outranks_a_weaker_one_in_the_explanation():
    weak = _finding(
        id="chain:blocked_work_stalls_qa:0",
        category="root_cause",
        severity="medium",
        headline="Something and something else moved together.",
        causal_link=_link(),
        evidence_basis="same_project",
    )
    strong = _finding(
        id="chain:dependency_slip_hits_successor:1",
        category="root_cause",
        severity="medium",
        headline="WBS-108 moved later; WBS-114 moved with it.",
        causal_link=_link(),
        evidence_basis="dependency_edge",
    )

    text = render_narrative(_bundle(findings=[weak, strong]))
    why = text.split("Why it is happening\n")[1].split("\n\n")[0]

    assert why.startswith("WBS-108 moved later")
