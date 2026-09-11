"""Every function that produces a number, exercised at its edges.

The rest of the suite tests behaviour: does a chain form, does a rule fire. This
file tests *arithmetic*, because these are the functions whose output a PM takes
to a steering committee. A wrong boolean shows up as a missing finding; a wrong
number shows up as a date someone commits to.

Each function is pushed at the places arithmetic actually breaks: empty inputs,
`None`, zero, negative, and the boundary either side of a threshold. Where a
function must refuse to answer rather than guess, that refusal is asserted -
returning `0` where the honest answer is "unknown" is the single most dangerous
thing any of this code could do.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.intelligence.assembler import entity_label, format_fact
from app.intelligence.context import _ratio, build_context
from app.intelligence.rules.tables import Condition
from app.intelligence.schedule.graph import (
    EdgeRecord,
    TaskNode,
    build_graph,
    descendants,
    driving_predecessors,
)
from app.intelligence.schedule.impact import (
    TaskProjection,
    _duration_days,
    project_schedule,
)
from app.intelligence.temporal.chains import find_chains, group_chains
from app.intelligence.temporal.ordering import provably_before
from app.intelligence.temporal.templates import (
    LinkBasis,
    link_rank,
    value_delta,
)
from tests.test_chains import EDGE, PROJECT as PROJECT_LINKS, exact

PROJECT = "excel:Project:1:HRMS"


def task(entity_id, start=None, planned=None, baseline=None, **kw):
    return TaskNode(
        entity_id=entity_id,
        project_id=kw.pop("project_id", PROJECT),
        start_date=start,
        planned_end=planned,
        baseline_end=baseline,
        **kw,
    )


# ==========================================================================
# _ratio — the denominator guard
# ==========================================================================


@pytest.mark.parametrize(
    "part,whole,expected",
    [
        (0, 0, 0.0),  # empty set, not a ZeroDivisionError
        (1, 0, 0.0),  # nonsense input still must not raise
        (0, 10, 0.0),
        (10, 10, 1.0),
        (1, 3, 0.3333),  # rounded to 4dp, so a rule threshold is stable
        (2, 3, 0.6667),
    ],
)
def test_ratio_edges(part, whole, expected):
    assert _ratio(part, whole) == expected


def test_a_ratio_is_rounded_tightly_enough_to_compare_against_a_threshold():
    """`qa_blocked_ratio > 0.6` must not flip on floating-point noise."""
    assert _ratio(3, 5) == 0.6
    assert not _ratio(3, 5) > 0.6
    assert _ratio(4, 5) > 0.6


# ==========================================================================
# value_delta — dates and numbers, or an honest None
# ==========================================================================


@pytest.mark.parametrize(
    "old,new,expected",
    [
        ("2026-03-04", "2026-03-16", 12.0),
        ("2026-03-16", "2026-03-04", -12.0),  # a pull-in is negative
        ("2026-03-04", "2026-03-04", 0.0),
        ("2026-02-28", "2026-03-01", 1.0),  # 2026 is not a leap year
        ("2024-02-28", "2024-03-01", 2.0),  # 2024 is
        ("60", "75", 15.0),
        ("75", "60", -15.0),
        ("0", "0", 0.0),
        ("-5", "5", 10.0),
        ("1.5", "2.25", 0.75),
    ],
)
def test_value_delta_computes(old, new, expected):
    assert value_delta(old, new) == pytest.approx(expected)


@pytest.mark.parametrize(
    "old,new",
    [
        ("Not Started", "Blocked"),  # a status has direction, not magnitude
        (None, "2026-03-16"),  # a value appearing is not a delta
        ("2026-03-04", None),
        (None, None),
        ("", ""),
        ("2026-03-04", "75"),  # a date and a number are not comparable
        ("TBD", "2026-03-16"),
        ("03/04/2026", "16/03/2026"),  # locale-ambiguous: refused, not guessed
    ],
)
def test_value_delta_refuses_rather_than_guessing(old, new):
    """`None` means no magnitude. A caller treating it as 0 would let every
    status edit satisfy a `min_magnitude` threshold."""
    assert value_delta(old, new) is None


# ==========================================================================
# _duration_days — never negative
# ==========================================================================


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (date(2026, 3, 1), date(2026, 3, 10), 9),
        (date(2026, 3, 1), date(2026, 3, 1), 0),
        # An end before its start is a typo in the sheet. Clamped to zero rather
        # than propagated as a negative duration, which would pull every
        # downstream date *earlier* and understate the slip.
        (date(2026, 3, 10), date(2026, 3, 1), 0),
        (None, date(2026, 3, 10), None),
        (date(2026, 3, 1), None, None),
        (None, None, None),
    ],
)
def test_duration_days(start, end, expected):
    assert _duration_days(start, end) == expected


# ==========================================================================
# TaskProjection — the three slip numbers, and when they must be None
# ==========================================================================


def projection(**kw) -> TaskProjection:
    base = dict(
        entity_id="A",
        title="A",
        planned_end=date(2026, 3, 10),
        baseline_end=date(2026, 3, 10),
        projected_end=date(2026, 3, 10),
    )
    base.update(kw)
    return TaskProjection(**base)


def test_a_consistent_projection_reports_zero_everywhere():
    p = projection()

    assert p.propagated_days == 0
    assert p.variance_days == 0
    assert p.recorded_slip_days == 0
    assert not p.is_inconsistent


def test_the_three_numbers_measure_three_different_things():
    """Recorded slip is what a human typed; propagated is what nobody typed."""
    p = projection(
        baseline_end=date(2026, 3, 10),
        planned_end=date(2026, 3, 16),  # +6 typed into the sheet
        projected_end=date(2026, 3, 20),  # +4 more the chain implies
    )

    assert p.recorded_slip_days == 6
    assert p.propagated_days == 4
    assert p.variance_days == 10  # the two together
    assert p.is_inconsistent


def test_a_projection_earlier_than_the_plan_reports_a_negative_not_zero():
    """Clamping here would hide a plan that is internally inconsistent the other
    way, which is a data-quality signal worth seeing."""
    p = projection(planned_end=date(2026, 3, 16), projected_end=date(2026, 3, 10))

    assert p.propagated_days == -6
    assert not p.is_inconsistent


@pytest.mark.parametrize(
    "kwargs,field",
    [
        ({"projected_end": None}, "propagated_days"),
        ({"planned_end": None}, "propagated_days"),
        ({"projected_end": None}, "variance_days"),
        ({"baseline_end": None}, "variance_days"),
        ({"planned_end": None}, "recorded_slip_days"),
        ({"baseline_end": None}, "recorded_slip_days"),
    ],
)
def test_a_missing_date_yields_none_never_zero(kwargs, field):
    """Zero means "no slip"; None means "cannot say". Conflating them reports a
    project with no baseline as permanently on time."""
    assert getattr(projection(**kwargs), field) is None


def test_is_inconsistent_treats_unknown_as_not_a_problem():
    """An unmeasurable task must not be reported as a breach."""
    assert not projection(baseline_end=None, projected_end=None).is_inconsistent


# ==========================================================================
# project_schedule — the forward pass
# ==========================================================================


def test_lag_accumulates_along_a_chain():
    tasks = [
        task("A", date(2026, 3, 1), date(2026, 3, 5), date(2026, 3, 5)),
        task("B", date(2026, 3, 6), date(2026, 3, 10), date(2026, 3, 10)),
        task("C", date(2026, 3, 11), date(2026, 3, 15), date(2026, 3, 15)),
    ]
    edges = [EdgeRecord("A", "B", lag_days=3), EdgeRecord("B", "C", lag_days=2)]

    report = project_schedule(build_graph(tasks, edges))

    # A finishes 3/5. B starts 3/8 (+3 lag), lasts 4 days -> 3/12.
    assert report.projections["B"].projected_end == date(2026, 3, 12)
    # C starts 3/14 (+2 lag), lasts 4 days -> 3/18.
    assert report.projections["C"].projected_end == date(2026, 3, 18)
    assert report.projections["C"].propagated_days == 3


def test_a_negative_lag_pulls_the_successor_in_but_not_past_its_own_plan():
    tasks = [
        task("A", date(2026, 3, 1), date(2026, 3, 10), date(2026, 3, 10)),
        task("B", date(2026, 3, 11), date(2026, 3, 15), date(2026, 3, 15)),
    ]

    report = project_schedule(build_graph(tasks, [EdgeRecord("A", "B", lag_days=-5)]))

    assert report.projections["B"].projected_end == date(2026, 3, 15)


def test_the_latest_of_several_predecessors_wins():
    tasks = [
        task("A", date(2026, 3, 1), date(2026, 3, 5), date(2026, 3, 5)),
        task("B", date(2026, 3, 1), date(2026, 3, 20), date(2026, 3, 20)),
        task("C", date(2026, 3, 6), date(2026, 3, 10), date(2026, 3, 10)),
    ]
    edges = [EdgeRecord("A", "C"), EdgeRecord("B", "C")]

    report = project_schedule(build_graph(tasks, edges))

    assert report.projections["C"].driving_predecessor == "B"
    assert report.projections["C"].projected_end == date(2026, 3, 24)


def test_a_task_with_no_dates_does_not_poison_its_successors():
    tasks = [
        task("A"),  # nothing known
        task("B", date(2026, 3, 6), date(2026, 3, 10), date(2026, 3, 10)),
    ]

    report = project_schedule(build_graph(tasks, [EdgeRecord("A", "B")]))

    assert report.projections["A"].projected_end is None
    assert report.projections["B"].projected_end == date(2026, 3, 10)


def test_project_slip_is_none_when_nothing_can_be_dated():
    report = project_schedule(build_graph([task("A")], []))

    assert report.project_end_projected is None
    assert report.project_slip_days is None


def test_inconsistent_tasks_come_back_worst_first():
    tasks = [
        task("A", date(2026, 3, 1), date(2026, 3, 20), date(2026, 3, 5)),
        task("B", date(2026, 3, 6), date(2026, 3, 10), date(2026, 3, 10)),
        task("C", date(2026, 3, 6), date(2026, 3, 8), date(2026, 3, 8)),
    ]
    edges = [EdgeRecord("A", "B"), EdgeRecord("A", "C")]

    worst = project_schedule(build_graph(tasks, edges)).inconsistent()

    assert [p.entity_id for p in worst] == ["B", "C"]
    assert worst[0].propagated_days >= worst[1].propagated_days


# ==========================================================================
# Graph traversal
# ==========================================================================


def test_descendants_is_transitive_and_excludes_self():
    tasks = [task(k) for k in "ABCD"]
    edges = [EdgeRecord("A", "B"), EdgeRecord("B", "C")]
    schedule = build_graph(tasks, edges)

    assert descendants(schedule, "A") == {"B", "C"}
    assert descendants(schedule, "C") == set()
    assert descendants(schedule, "D") == set()
    assert descendants(schedule, "GHOST") == set()


def test_driving_predecessors_names_only_the_ones_that_actually_constrain():
    """The answer to "what is holding this up" - not every predecessor."""
    tasks = [
        task("EARLY", date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 2)),
        task("LATE", date(2026, 3, 1), date(2026, 3, 20), date(2026, 3, 20)),
        task("TARGET", date(2026, 3, 10), date(2026, 3, 15), date(2026, 3, 15)),
    ]
    edges = [EdgeRecord("EARLY", "TARGET"), EdgeRecord("LATE", "TARGET")]
    schedule = build_graph(tasks, edges)
    projections = {"EARLY": date(2026, 3, 2), "LATE": date(2026, 3, 20)}

    driving = driving_predecessors(schedule, "TARGET", projections)

    assert driving == ["LATE"]


def test_driving_predecessors_on_an_unknown_task_is_empty_not_an_error():
    schedule = build_graph([task("A")], [])

    assert driving_predecessors(schedule, "GHOST", {}) == []


# ==========================================================================
# Lag intervals on chains
# ==========================================================================


def test_the_lag_interval_widens_with_the_effect_s_uncertainty():
    """Both bounds come from interval arithmetic, never a midpoint."""
    chains = find_chains(
        [
            exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 0),
            exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 5),
        ],
        EDGE,
    )

    assert chains[0].lag_days_min == 5.0
    assert chains[0].lag_days_max == 5.0
    assert chains[0].lag_is_certain


def test_a_sub_day_lag_is_reported_as_a_fraction_not_rounded_to_zero():
    """Jira changelogs are timestamped to the minute; rounding to whole days
    would report a real ordering as simultaneous."""
    base = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    cause = exact("A", "planned_end", "2026-03-04", "2026-03-16", 0)
    effect = exact("B", "planned_end", "2026-03-20", "2026-04-01", 0)
    object.__setattr__(effect, "occurred_at", base + timedelta(hours=12))
    object.__setattr__(effect, "occurred_at_lower", base + timedelta(hours=12))

    chains = find_chains([cause, effect], EDGE)

    assert chains[0].lag_days_min == pytest.approx(0.5)


def test_group_counts_distinct_effects_not_chains():
    """The same effect explained twice must not inflate the count."""
    changes = [exact("CAUSE", "status", "Open", "Blocked", 0)]
    changes += [
        exact(f"QA-{n:03d}", "status", "Open", "Blocked", 5, entity_type="qa_item")
        for n in range(1, 4)
    ]

    groups = group_chains(find_chains(changes, PROJECT_LINKS))

    assert groups[0].effect_count == 3
    assert len(groups[0].chains) == 3


def test_link_rank_orders_strongest_first():
    ranks = [
        link_rank(b)
        for b in (
            LinkBasis.SAME_ENTITY,
            LinkBasis.DEPENDENCY_EDGE,
            LinkBasis.DEPENDENCY_PATH,
            LinkBasis.SAME_PROJECT,
        )
    ]

    assert ranks == sorted(ranks)
    assert len(set(ranks)) == 4


# ==========================================================================
# Ordering arithmetic — the base every claim rests on
# ==========================================================================


def test_provably_before_is_strict_at_the_boundary():
    """Adjacent scan windows share an instant, so neither ordering is provable.

    This is why the demo timeline needs a scan between cause and effect. If this
    ever becomes `<=`, every consecutive pair becomes falsely orderable.
    """
    t = datetime(2026, 3, 6, 9, 0, tzinfo=timezone.utc)
    a = exact("A", "f", None, None, 0)
    b = exact("B", "f", None, None, 0)
    object.__setattr__(a, "occurred_at", t)
    object.__setattr__(a, "occurred_at_lower", t - timedelta(days=4))
    object.__setattr__(b, "occurred_at", t + timedelta(days=12))
    object.__setattr__(b, "occurred_at_lower", t)

    assert not provably_before(a, b)
    assert not provably_before(b, a)


# ==========================================================================
# format_fact — one number, one rendering
# ==========================================================================


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("qa_blocked_ratio", 0.0, "0%"),
        ("qa_blocked_ratio", 1.0, "100%"),
        # A non-zero ratio must never print as 0%: a reader takes that to mean
        # none. Same the other way - 199 of 200 is not "100%".
        ("qa_blocked_ratio", 0.005, "<1%"),
        ("qa_blocked_ratio", 0.001, "<1%"),
        ("qa_blocked_ratio", 0.995, ">99%"),
        ("qa_blocked_ratio", 0.5, "50%"),
        ("baseline_coverage", 0.5, "50%"),
        ("lag_min", 0.0, "0"),
        ("lag_min", 4.0, "4"),
        ("lag_min", 4.25, "4.2"),  # one decimal is the documented precision
        ("lag_min", -3.0, "-3"),
        ("max_propagated_days", 34, "34"),
        ("max_propagated_days", 0, "0"),
        ("depends_on_inferred_edges", True, "yes"),
        ("depends_on_inferred_edges", False, "no"),
        ("as_of", date(2026, 3, 22), "2026-03-22"),
        ("as_of", datetime(2026, 3, 22, 9, 30), "2026-03-22"),
        ("strongest_chain_template", "", ""),
    ],
)
def test_format_fact(name, value, expected):
    assert format_fact(name, value) == expected


def test_a_bool_is_formatted_as_a_word_not_as_the_integer_it_also_is():
    """`isinstance(True, int)` is True in Python; the bool branch must come first
    or every flag would render as 1 or 0."""
    assert format_fact("flag", True) == "yes"
    assert format_fact("count", 1) == "1"


@pytest.mark.parametrize(
    "entity_id,expected",
    [
        ("excel:Task:1:WBS-108", "WBS-108"),
        ("jira:Task:1:10108", "10108"),
        ("WBS-108", "WBS-108"),
        ("excel:Task:1:WBS%3A108", "WBS%3A108"),  # an escaped colon survives
        # An Excel entity is namespaced by project as well as by row key, so
        # the label is the *last* component and not everything after the
        # structure - otherwise the encoded project id is printed as the name.
        ("excel:Task:1:excel%3AProject%3A1%3AHRMS:WBS-108", "WBS-108"),
        ("a:b:c:d:e", "e"),
    ],
)
def test_entity_label(entity_id, expected):
    assert entity_label(entity_id) == expected


# ==========================================================================
# Rule comparison
# ==========================================================================


@pytest.mark.parametrize(
    "op,threshold,value,holds",
    [
        (">", 5, 6, True),
        (">", 5, 5, False),
        (">=", 5, 5, True),
        ("<", 5, 4, True),
        ("<", 5, 5, False),
        ("<=", 5, 5, True),
        ("==", True, True, True),
        ("!=", True, False, True),
        (">", 0.6, 0.6, False),
        (">", 0.6, 0.6001, True),
    ],
)
def test_condition_boundaries(op, threshold, value, holds):
    assert Condition("x", op, threshold).holds({"x": value}) is holds


def test_a_condition_on_an_absent_field_raises_rather_than_silently_failing():
    """A rule that never fires is worse than one that errors at startup."""
    with pytest.raises(KeyError):
        Condition("missing", ">", 1).holds({"x": 1})


def test_a_condition_describes_the_value_it_actually_saw():
    described = Condition("max_propagated_days", ">=", 5).describe(
        {"max_propagated_days": 34}
    )

    assert "max_propagated_days >= 5" in described
    assert "was 34" in described


# ==========================================================================
# Aggregation end to end
# ==========================================================================


def test_context_aggregates_are_internally_consistent():
    tasks = [
        task("A", date(2026, 2, 16), date(2026, 3, 16), date(2026, 3, 4)),
        task("B", date(2026, 3, 5), date(2026, 3, 20), date(2026, 3, 20)),
    ]
    edges = [EdgeRecord("A", "B", source="excel_predecessor")]
    schedule = build_graph(tasks, edges)
    impact = project_schedule(schedule)

    record = build_context(
        project_id=PROJECT,
        as_of=date(2026, 3, 22),
        schedule=schedule,
        impact=impact,
        edges=edges,
    ).as_record()

    assert record["task_count"] == 2
    assert record["edges_stated"] + record["edges_inferred"] == record["edges_total"]
    assert record["tasks_with_baseline"] == 2
    assert record["baseline_coverage"] == 1.0
    # The worst propagated figure must match the worst projection.
    assert record["max_propagated_days"] == max(
        p.propagated_days or 0 for p in impact.projections.values()
    )
