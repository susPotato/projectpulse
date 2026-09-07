"""Guards for the rule layer.

The most valuable test here is `test_both_backends_agree`. ZEN is the editable,
fast path; the built-in evaluator is the definition of correct. If they ever
disagree, a rule table means two different things depending on which wheel
installed - so they are checked against each other on every record the other
tests use.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from app.intelligence.context import DeliveryContext
from app.intelligence.rules.engine import RulesEngine, RuleTableError, to_jdm
from app.intelligence.rules.tables import (
    DEFAULT_TABLE,
    Condition,
    Rule,
    RuleTable,
    validate_table,
)

KNOWN = {f.name for f in fields(DeliveryContext)} - {"notes"}


def ctx(**kw) -> dict:
    return DeliveryContext(
        project_id=kw.pop("project_id", "p"),
        as_of=kw.pop("as_of", "2026-03-18"),
        **kw,
    ).as_record()


RECORDS = [
    ctx(),
    ctx(max_propagated_days=11, tasks_inconsistent=2),
    ctx(max_propagated_days=3, tasks_inconsistent=1),
    ctx(qa_count=17, qa_blocked=14, qa_blocked_ratio=0.82),
    ctx(qa_count=10, qa_blocked=4, qa_blocked_ratio=0.4),
    ctx(milestones_at_risk=2),
    ctx(depends_on_inferred_edges=True, edges_inferred=1, edges_total=4),
    ctx(changes_total=10, changes_low_confidence=4, low_confidence_ratio=0.4),
    ctx(rows_rejected=7),
    ctx(task_count=6, tasks_with_baseline=1, baseline_coverage=0.16),
    ctx(chain_count=50, chains_dependency_backed=2),
]


# --------------------------------------------------------------------------
# Table health - caught at construction, never at evaluation
# --------------------------------------------------------------------------


def test_the_default_table_is_valid():
    assert validate_table(DEFAULT_TABLE, KNOWN) == []


def test_a_rule_on_an_unknown_field_is_rejected():
    """Otherwise the rule never fires and nobody finds out."""
    table = RuleTable(
        "bad",
        (
            Rule(
                id="r",
                when=(Condition("no_such_field", ">", 1),),
                category="c",
                severity="high",
                headline="something happened",
                recommendation="do something",
                rationale="because",
            ),
        ),
    )

    assert "unknown context field" in " ".join(validate_table(table, KNOWN))
    with pytest.raises(RuleTableError):
        RulesEngine(table, known_fields=KNOWN)


def test_a_headline_with_a_literal_digit_is_rejected():
    """Invariant 1: every number is a token so it cannot drift from the data."""
    table = RuleTable(
        "bad",
        (
            Rule(
                id="r",
                when=(Condition("rows_rejected", ">=", 1),),
                category="c",
                severity="high",
                headline="exactly 3 rows were rejected",
                recommendation="check them",
                rationale="because",
            ),
        ),
    )

    assert "literal digit" in " ".join(validate_table(table, KNOWN))


def test_a_token_with_no_matching_field_is_rejected():
    table = RuleTable(
        "bad",
        (
            Rule(
                id="r",
                when=(Condition("rows_rejected", ">=", 1),),
                category="c",
                severity="high",
                headline="{{nonexistent}} rows were rejected",
                recommendation="check them",
                rationale="because",
            ),
        ),
    )

    assert "could never be substituted" in " ".join(validate_table(table, KNOWN))


def test_an_unknown_operator_is_refused_at_definition():
    with pytest.raises(ValueError):
        Condition("rows_rejected", "~=", 1)


def test_duplicate_rule_ids_are_rejected():
    rule = Rule(
        id="dupe",
        when=(Condition("rows_rejected", ">=", 1),),
        category="c",
        severity="high",
        headline="something",
        recommendation="do",
        rationale="because",
    )

    assert "duplicate rule id" in " ".join(validate_table(RuleTable("t", (rule, rule)), KNOWN))


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("record", RECORDS)
def test_both_backends_agree(record):
    """ZEN and the built-in evaluator must mean the same thing.

    If this ever fails, a rule table means one thing on a machine where the ZEN
    wheel built and another where it did not - which is worse than not having
    ZEN at all.
    """
    zen_engine = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN, backend="zen")
    py_engine = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN, backend="python")

    assert zen_engine.backend_name == "zen"
    assert py_engine.backend_name == "python"
    assert [h.rule_id for h in zen_engine.evaluate(record)] == [
        h.rule_id for h in py_engine.evaluate(record)
    ]


def test_a_healthy_project_fires_nothing():
    assert RulesEngine(DEFAULT_TABLE, known_fields=KNOWN).evaluate(ctx()) == []


def test_several_problems_all_fire():
    """Collect, not first-match: a project can be in trouble several ways."""
    hits = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN).evaluate(
        ctx(
            max_propagated_days=11,
            tasks_inconsistent=2,
            milestones_at_risk=1,
            rows_rejected=3,
        )
    )

    assert {h.rule_id for h in hits} >= {
        "schedule_inconsistent_major",
        "milestone_at_risk",
        "rows_rejected",
    }


def test_the_severity_bands_do_not_overlap():
    """A 3-day inconsistency is minor; an 11-day one is not both."""
    engine = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN)

    minor = {h.rule_id for h in engine.evaluate(ctx(max_propagated_days=3))}
    major = {h.rule_id for h in engine.evaluate(ctx(max_propagated_days=11))}

    assert "schedule_inconsistent_minor" in minor
    assert "schedule_inconsistent_major" not in minor
    assert "schedule_inconsistent_major" in major
    assert "schedule_inconsistent_minor" not in major


def test_every_hit_carries_a_trace_showing_real_values():
    """A PM told their milestone is at risk may ask what the rule compared."""
    hits = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN).evaluate(
        ctx(milestones_at_risk=2)
    )

    trace = next(h for h in hits if h.rule_id == "milestone_at_risk").trace
    assert any("milestones_at_risk" in line and "was 2" in line for line in trace)


def test_headlines_still_carry_tokens_after_evaluation():
    """Substitution is the assembler's job and happens exactly once."""
    hits = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN).evaluate(ctx(rows_rejected=3))

    assert "{{rows_rejected}}" in hits[0].headline


def test_the_qa_rules_ignore_a_tiny_sample():
    """One blocked item out of two is not a stalled queue."""
    hits = RulesEngine(DEFAULT_TABLE, known_fields=KNOWN).evaluate(
        ctx(qa_count=2, qa_blocked=2, qa_blocked_ratio=1.0)
    )

    assert not any(h.category == "quality_risk" for h in hits)


# --------------------------------------------------------------------------
# JDM compilation
# --------------------------------------------------------------------------


def test_jdm_has_one_column_per_field_and_one_row_per_rule():
    jdm = to_jdm(DEFAULT_TABLE)
    table = next(n for n in jdm["nodes"] if n["type"] == "decisionTableNode")

    assert table["content"]["hitPolicy"] == "collect"
    assert len(table["content"]["rules"]) == len(DEFAULT_TABLE.rules)
    assert {i["field"] for i in table["content"]["inputs"]} <= KNOWN


def test_a_banded_rule_joins_its_conditions_in_one_cell():
    """1 <= x < 5 is two conditions on one field, so one cell has to hold both."""
    jdm = to_jdm(DEFAULT_TABLE)
    table = next(n for n in jdm["nodes"] if n["type"] == "decisionTableNode")
    row = next(r for r in table["content"]["rules"] if r["_id"] == "schedule_inconsistent_minor")

    assert " and " in row["i_max_propagated_days"]
