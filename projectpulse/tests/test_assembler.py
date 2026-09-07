"""Guards for the one place numbers are born.

Invariant 1 in executable form: rules and templates emit `{{tokens}}`, this module
substitutes them from the context record, and nothing else in the system formats a
number. These tests exist so that stays true as the rule table grows.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.intelligence.assembler import (
    build_bundle,
    entity_label,
    facts_for,
    format_fact,
    substitute,
)
from app.intelligence.context import DeliveryContext
from app.intelligence.rules.engine import RuleHit
from tests.test_chains import EDGE, exact
from app.intelligence.temporal.chains import find_chains

NOW = datetime(2026, 3, 22, tzinfo=timezone.utc)


def ctx(**kw) -> DeliveryContext:
    return DeliveryContext(project_id="p", as_of="2026-03-22", **kw)


def hit(headline, recommendation="Do something.", **kw) -> RuleHit:
    return RuleHit(
        rule_id=kw.pop("rule_id", "r"),
        category=kw.pop("category", "schedule_risk"),
        severity=kw.pop("severity", "high"),
        headline=headline,
        recommendation=recommendation,
        rationale="because",
        trace=("x > 1 (was 2)",),
    )


# --------------------------------------------------------------------------
# Formatting - one rule, applied everywhere
# --------------------------------------------------------------------------


def test_a_ratio_becomes_a_percentage():
    assert format_fact("qa_blocked_ratio", 0.8235) == "82%"
    assert format_fact("baseline_coverage", 1.0) == "100%"


def test_a_plain_float_keeps_one_decimal_and_drops_a_trailing_zero():
    assert format_fact("lag_min", 4.0) == "4"
    assert format_fact("lag_min", 4.5) == "4.5"


def test_a_boolean_reads_as_a_word():
    assert format_fact("depends_on_inferred_edges", True) == "yes"


def test_a_date_is_iso_and_never_locale_dependent():
    assert format_fact("as_of", date(2026, 3, 22)) == "2026-03-22"


def test_entity_ids_are_shortened_to_what_a_human_typed():
    assert entity_label("excel:Task:1:WBS-108") == "WBS-108"
    assert entity_label("WBS-108") == "WBS-108"


# --------------------------------------------------------------------------
# Substitution
# --------------------------------------------------------------------------


def test_substitution_replaces_every_token():
    text, missing = substitute("{{a}} and {{b}}", {"a": "1", "b": "2"})

    assert text == "1 and 2"
    assert missing == []


def test_an_unresolved_token_is_reported_not_silently_left():
    text, missing = substitute("{{a}} and {{ghost}}", {"a": "1"})

    assert missing == ["ghost"]
    assert "{{ghost}}" in text


def test_facts_are_taken_only_for_the_names_actually_used():
    record = ctx(rows_rejected=3, qa_blocked=9).as_record()

    assert facts_for(record, {"rows_rejected"}) == {"rows_rejected": "3"}


def test_the_same_number_formats_identically_wherever_it_appears():
    """A headline saying 82% and a panel saying 0.8235 is the bug this prevents."""
    record = ctx(qa_blocked_ratio=0.8235).as_record()
    facts = facts_for(record, {"qa_blocked_ratio"})

    a, _ = substitute("blocked {{qa_blocked_ratio}}", facts)
    b, _ = substitute("still {{qa_blocked_ratio}}", facts)

    assert a.endswith("82%") and b.endswith("82%")


# --------------------------------------------------------------------------
# Bundle assembly
# --------------------------------------------------------------------------


def test_a_finding_with_an_unresolvable_token_is_dropped_not_shipped():
    """A literal {{token}} on screen discredits every number on the page."""
    bundle = build_bundle(
        project_id="p",
        as_of=NOW,
        generated_at=NOW,
        context=ctx(rows_rejected=3),
        hits=[hit("{{no_such_field}} rows were rejected.")],
    )

    assert bundle.findings == []


def test_a_resolvable_finding_ships_with_its_facts_recorded():
    bundle = build_bundle(
        project_id="p",
        as_of=NOW,
        generated_at=NOW,
        context=ctx(rows_rejected=3),
        hits=[hit("{{rows_rejected}} rows were rejected.")],
    )

    finding = bundle.findings[0]
    assert finding.headline == "3 rows were rejected."
    assert finding.facts == {"rows_rejected": "3"}


def test_the_rule_trace_reaches_the_bundle():
    """A PM told something is at risk is entitled to see what was compared."""
    bundle = build_bundle(
        project_id="p",
        as_of=NOW,
        generated_at=NOW,
        context=ctx(rows_rejected=3),
        hits=[hit("{{rows_rejected}} rows were rejected.")],
    )

    trace = bundle.findings[0].rule_trace
    assert trace.rule_id == "r"
    assert trace.conditions == ["x > 1 (was 2)"]
    assert trace.rationale == "because"


def test_many_effects_from_one_cause_become_one_finding():
    """Eight identical sentences about one blocked environment is unreadable."""
    changes = [exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1)]
    changes += [
        exact(f"QA-{n:03d}", "status", "Open", "Blocked", 3, entity_type="qa_item")
        for n in range(1, 7)
    ]
    chains = find_chains(changes, EDGE)

    bundle = build_bundle(
        project_id="p",
        as_of=NOW,
        generated_at=NOW,
        context=ctx(),
        chains=chains,
    )
    root_cause = [f for f in bundle.findings if f.category == "root_cause"]

    assert len(chains) > 1
    assert len(root_cause) == 1
    assert "6 other items" in root_cause[0].headline


def test_chain_findings_are_capped_so_the_page_stays_readable():
    changes = []
    for i in range(12):
        changes.append(
            exact(f"T-{i}", "planned_end", "2026-03-04", "2026-03-16", i)
        )
    chains = find_chains(changes, EDGE)

    bundle = build_bundle(
        project_id="p",
        as_of=NOW,
        generated_at=NOW,
        context=ctx(),
        chains=chains,
        max_chain_findings=3,
    )

    assert len([f for f in bundle.findings if f.category == "root_cause"]) == 3


def test_the_context_record_is_shipped_for_the_rule_trace_ui():
    bundle = build_bundle(
        project_id="p",
        as_of=NOW,
        generated_at=NOW,
        context=ctx(rows_rejected=3, qa_blocked=2),
    )

    assert bundle.context["rows_rejected"] == 3
    assert "notes" not in bundle.context
