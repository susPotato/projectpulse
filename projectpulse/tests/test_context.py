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


# --- Ownership -------------------------------------------------------------
#
# `owners` used to be a positional list of assignees, which could be counted
# but not joined. These pin the mapping form, and the one question the list
# could never answer: does *this* overdue task have a name on it.


def owned(entity_id, planned, owner, status="in progress"):
    return task(entity_id, date(2026, 3, 1), planned, status=status), owner


def test_an_overdue_task_with_nobody_on_it_is_counted_apart_from_both_halves():
    late, on_time = date(2026, 3, 1), date(2026, 4, 30)
    rows = [
        owned("t1", late, "Alice"),
        owned("t2", late, None),
        owned("t3", late, "   "),  # whitespace is not an owner
        owned("t4", on_time, None),
    ]
    record = context(
        tasks=[t for t, _ in rows],
        owners={t.entity_id: o for t, o in rows},
    ).as_record()

    assert record["tasks_overdue"] == 3
    #: t2 and t3 - late and unowned. t4 is unowned but not yet due, t1 is late
    #: but has someone to ask.
    assert record["tasks_overdue_unowned"] == 2
    assert record["tasks_unowned"] == 3
    assert record["distinct_owners"] == 1


def test_a_finished_task_needs_no_owner():
    """Counted over open tasks only, or every delivered backlog reads as a gap."""
    rows = [
        owned("t1", date(2026, 3, 1), None, status="done"),
        owned("t2", date(2026, 3, 1), None),
    ]
    record = context(
        tasks=[t for t, _ in rows],
        owners={t.entity_id: o for t, o in rows},
    ).as_record()

    assert record["tasks_unowned"] == 1
    assert record["tasks_overdue_unowned"] == 1


def test_a_backlog_with_no_dates_reports_zero_rather_than_clear():
    """The conjunction needs both halves; missing one is unmeasurable, not safe.

    This is the real shape of the project the rule was written for: 173 rows,
    every one carrying an owner in prose and four carrying a date. Nothing can
    honestly be said about overdue-and-unowned there, and 0 is how the context
    says so - which is why the rule's own rationale spells that out rather than
    letting a reader take silence for a clean result.
    """
    rows = [owned("t1", None, None), owned("t2", None, None)]
    record = context(
        tasks=[t for t, _ in rows],
        owners={t.entity_id: o for t, o in rows},
    ).as_record()

    assert record["tasks_unowned"] == 2
    assert record["tasks_overdue"] == 0
    assert record["tasks_overdue_unowned"] == 0


def test_owners_naming_a_task_the_schedule_does_not_have_are_ignored():
    """The mapping is a lookup, not a second source of tasks.

    The list form was zipped by position, so a stray row shifted every
    assignment after it. A mapping simply misses.
    """
    rows = [owned("t1", date(2026, 3, 1), None)]
    record = context(
        tasks=[t for t, _ in rows],
        owners={"t1": None, "ghost": "Alice"},
    ).as_record()

    assert record["tasks_unowned"] == 1
    assert record["distinct_owners"] == 1


# --- What a PM asks: who is late, how late, what is next -------------------


def test_the_person_with_most_overdue_work_is_named_with_their_load():
    """CoWorkLocal's shape: one person holds most of the project and every one
    of their open tasks is late; another holds a few, also all late."""
    rows = [
        owned("q1", date(2026, 3, 1), "Quan"),
        owned("q2", date(2026, 2, 20), "Quan"),
        owned("q3", date(2026, 3, 10), "Quan"),
        owned("q4", date(2026, 1, 1), "Quan", status="done"),
        owned("k1", date(2026, 3, 5), "Kien"),
        owned("k2", date(2026, 3, 6), "Kien"),
        owned("k3", date(2026, 3, 7), "Kien"),
        owned("h1", date(2026, 4, 1), "Hoach", status="todo"),
    ]
    record = context(
        tasks=[t for t, _ in rows], owners={t.entity_id: o for t, o in rows},
    ).as_record()

    assert record["top_owner"] == "Quan"
    assert record["top_owner_open"] == 3
    assert record["top_owner_overdue"] == 3
    assert record["busiest_owner"] == "Quan"
    assert record["busiest_owner_tasks"] == 4
    assert record["busiest_owner_share"] == 0.5
    # Quan and Kien: three open each, every one past due. Hoach is on time.
    assert record["owners_underwater"] == 2
    # q2, due 2026-02-20, against AS_OF 2026-03-22.
    assert record["worst_overdue_days"] == 30


def test_due_soon_not_started_leaves_out_work_already_underway():
    rows = [
        owned("a", date(2026, 3, 25), "X", status="todo"),
        owned("b", date(2026, 3, 30), "X", status="in progress"),
        owned("c", date(2026, 5, 1), "X", status="todo"),
    ]
    record = context(
        tasks=[t for t, _ in rows], owners={t.entity_id: o for t, o in rows},
    ).as_record()

    assert record["tasks_due_soon"] == 2
    assert record["tasks_due_soon_not_started"] == 1


def test_a_task_dated_backwards_is_not_dependency_slip():
    """Start after its own due date, no edges: inconsistent with itself only."""
    record = context([task("A", date(2026, 3, 20), date(2026, 3, 1))]).as_record()

    assert record["tasks_dated_backwards"] == 1
    assert record["worst_dated_backwards_days"] == 19
    assert record["tasks_dependency_inconsistent"] == 0
    assert record["max_dependency_slip_days"] == 0


def test_dependency_slip_is_still_counted_as_dependency_slip():
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20), date(2026, 3, 20)),
    ]
    record = context(tasks, [EdgeRecord("A", "B")]).as_record()

    assert record["max_dependency_slip_days"] == 11
    assert record["tasks_dependency_inconsistent"] == 1
    assert record["tasks_dated_backwards"] == 0


def test_without_a_trace_the_code_facts_stay_silent_not_zero_clean():
    record = context().as_record()
    assert record["trace_available"] is False
    record = context(trace={"available": True, "done_contradicted": 2,
                            "worst_area": "core/chat",
                            "worst_area_overdue": 4}).as_record()
    assert record["trace_available"] is True
    assert record["trace_done_contradicted"] == 2
    assert record["worst_area"] == "core/chat"
