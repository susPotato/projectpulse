"""Guards for the scalars the rules compare against.

`DeliveryContext` field names are the rule tables' vocabulary. Renaming one does
not raise - it silently stops a rule from firing - so the aggregation is pinned
here and `validate_table` checks the names at engine construction.
"""

from __future__ import annotations

from datetime import date

from app.intelligence.context import DeliveryContext, build_context
from app.intelligence.schedule.graph import EdgeRecord, TaskNode, build_graph
from app.intelligence.schedule.impact import project_schedule

PROJECT = "excel:Project:1:HRMS"
AS_OF = date(2026, 3, 22)


class Row:
    """A QA item or task double - only `status` is read."""

    def __init__(self, status):
        self.status = status


class Change:
    def __init__(self, precision="bounded", identity_confidence="high"):
        self.precision = precision
        self.identity_confidence = identity_confidence


def task(entity_id, start, planned, baseline=None, **kw):
    return TaskNode(
        entity_id=entity_id,
        project_id=PROJECT,
        start_date=start,
        planned_end=planned,
        baseline_end=baseline,
        **kw,
    )


def context(tasks=(), edges=(), **kw):
    schedule = build_graph(tasks, edges)
    return build_context(
        project_id=PROJECT,
        as_of=AS_OF,
        schedule=schedule,
        impact=project_schedule(schedule),
        edges=edges,
        **kw,
    )


def test_an_empty_project_produces_zeros_not_errors():
    """A ratio of an empty set is 0.0 - rules must not special-case emptiness."""
    record = context().as_record()

    assert record["qa_blocked_ratio"] == 0.0
    assert record["baseline_coverage"] == 0.0
    assert record["task_count"] == 0


def test_the_record_is_flat_and_scalar():
    """ZEN evaluates one flat record; a nested value would not compare."""
    record = context().as_record()

    assert all(
        isinstance(v, (str, int, float, bool)) for v in record.values()
    ), {k: type(v) for k, v in record.items() if not isinstance(v, (str, int, float, bool))}


def test_notes_never_reach_the_rule_record():
    assert "notes" not in DeliveryContext(project_id="p", as_of="x").as_record()


def test_blocked_counts_accept_the_vocabularies_sheets_actually_use():
    record = context(
        qa_items=[Row("BLOCKED"), Row("blocked"), Row("Yes"), Row("Open")]
    ).as_record()

    assert record["qa_blocked"] == 3
    assert record["qa_blocked_ratio"] == 0.75


def test_baseline_coverage_reflects_what_can_be_measured():
    tasks = [
        task("A", date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 5)),
        task("B", date(2026, 1, 6), date(2026, 1, 9)),  # no baseline
    ]

    record = context(tasks).as_record()

    assert record["tasks_with_baseline"] == 1
    assert record["baseline_coverage"] == 0.5


def test_low_confidence_changes_are_counted_and_ratioed():
    changes = [Change(), Change(), Change(identity_confidence="low")]

    record = context(changes=changes).as_record()

    assert record["changes_low_confidence"] == 1
    assert record["low_confidence_ratio"] == round(1 / 3, 4)


def test_stated_and_inferred_edges_are_counted_apart():
    tasks = [
        task("A", date(2026, 1, 1), date(2026, 1, 5)),
        task("B", date(2026, 1, 6), date(2026, 1, 9)),
        task("C", date(2026, 1, 10), date(2026, 1, 12)),
    ]
    edges = [
        EdgeRecord("A", "B", source="excel_predecessor"),
        EdgeRecord("B", "C", source="wbs_implicit"),
    ]

    record = context(tasks, edges).as_record()

    assert record["edges_stated"] == 1
    assert record["edges_inferred"] == 1
    assert record["edges_total"] == 2


def test_dependence_on_inferred_edges_is_detected():
    """The flag that lets a finding admit its date rests on an inference."""
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20), date(2026, 3, 20)),
    ]
    edges = [EdgeRecord("A", "B", source="wbs_implicit")]
    schedule = build_graph(tasks, edges)

    record = build_context(
        project_id=PROJECT,
        as_of=AS_OF,
        schedule=schedule,
        impact=project_schedule(schedule),
        edges=edges,
        stated_only_impact=project_schedule(build_graph(tasks, edges, stated_only=True)),
    ).as_record()

    assert record["depends_on_inferred_edges"] is True


def test_without_a_stated_only_comparison_the_flag_stays_false():
    """Absence of the check is not evidence of dependence."""
    assert context().as_record()["depends_on_inferred_edges"] is False


def test_propagated_slip_reaches_the_rule_vocabulary():
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20), date(2026, 3, 20)),
    ]

    record = context(tasks, [EdgeRecord("A", "B")]).as_record()

    assert record["max_propagated_days"] == 11
    assert record["tasks_inconsistent"] == 1
    assert record["max_recorded_slip_days"] == 12
