"""Guards for the causal engine.

The rule these all serve: **orderable is not causal.** Most of these tests assert
that a chain is *not* built - from a coincidence, from an unprovable ordering,
from a row we cannot identify, or from two events with nothing connecting them.
A causal engine that emits too much is worse than one that emits nothing, because
a PM cannot tell which half to trust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.intelligence.temporal.chains import (
    find_chains,
    group_by_template,
    strongest_per_effect,
)
from app.intelligence.temporal.ordering import OrderingBasis
from app.intelligence.temporal.templates import (
    Direction,
    ChangePattern,
    LinkBasis,
    value_delta,
)

DAY = timedelta(days=1)
T0 = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


@dataclass
class Change:
    """A test double for `StateChange`."""

    entity_id: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    entity_type: str = "task"
    occurred_at: datetime = T0
    occurred_at_lower: datetime = T0
    precision: str = "exact"
    identity_confidence: str = "high"
    raw_data_id: int | None = None


def exact(entity_id, field, old, new, day, **kw):
    when = T0 + day * DAY
    return Change(
        entity_id=entity_id,
        field=field,
        old_value=old,
        new_value=new,
        occurred_at=when,
        occurred_at_lower=when,
        precision="exact",
        **kw,
    )


def bounded(entity_id, field, old, new, lower_day, upper_day, **kw):
    return Change(
        entity_id=entity_id,
        field=field,
        old_value=old,
        new_value=new,
        occurred_at=T0 + upper_day * DAY,
        occurred_at_lower=T0 + lower_day * DAY,
        precision="bounded",
        **kw,
    )


class Links:
    """A stub `LinkResolver` - explicit, so tests state their own topology."""

    def __init__(self, mapping=None, default=None):
        self.mapping = mapping or {}
        self.default = default

    def link_between(self, cause, effect):
        if cause == effect:
            return LinkBasis.SAME_ENTITY
        return self.mapping.get((cause, effect), self.default)


EDGE = Links(default=LinkBasis.DEPENDENCY_EDGE)
PROJECT = Links(default=LinkBasis.SAME_PROJECT)
UNRELATED = Links(default=None)


# --------------------------------------------------------------------------
# value_delta - the quantity comparison every pattern rests on
# --------------------------------------------------------------------------


def test_value_delta_reads_dates_as_days():
    assert value_delta("2026-03-04", "2026-03-16") == 12.0


def test_value_delta_reads_numbers():
    assert value_delta("60", "75") == 15.0


def test_value_delta_is_none_for_things_with_no_magnitude():
    """A status change has a direction a human understands and no magnitude."""
    assert value_delta("Not Started", "Blocked") is None


def test_a_direction_pattern_never_matches_a_non_quantity():
    """Otherwise every status edit would masquerade as a schedule slip."""
    pattern = ChangePattern(label="slip", direction=Direction.INCREASED)
    assert not pattern.matches(exact("A", "status", "Open", "Blocked", 1))


def test_a_pull_in_is_not_a_slip():
    pattern = ChangePattern(label="slip", direction=Direction.INCREASED)
    assert not pattern.matches(exact("A", "planned_end", "2026-03-16", "2026-03-04", 1))


def test_a_small_correction_is_below_the_slip_threshold():
    """A one-day edit is housekeeping, not a delivery event."""
    changes = [
        exact("A", "planned_end", "2026-03-04", "2026-03-05", 1),
        exact("B", "planned_end", "2026-03-20", "2026-03-21", 3),
    ]
    assert find_chains(changes, EDGE) == []


# --------------------------------------------------------------------------
# The headline chain
# --------------------------------------------------------------------------


def test_a_dependency_backed_slip_produces_a_chain():
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 3),
    ]

    chains = find_chains(changes, EDGE)

    assert len(chains) == 1
    chain = chains[0]
    assert chain.template_id == "dependency_slip_hits_successor"
    assert chain.link is LinkBasis.DEPENDENCY_EDGE
    assert chain.basis is OrderingBasis.EXACT
    assert chain.lag_days_min == 2.0
    assert chain.lag_is_certain
    assert chain.effect_magnitude == 12.0


def test_the_same_pair_in_reverse_order_is_not_also_a_chain():
    """Causation has a direction; the resolver must not link both ways."""
    links = Links(mapping={("A", "B"): LinkBasis.DEPENDENCY_EDGE})
    changes = [
        exact("A", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("B", "planned_end", "2026-03-20", "2026-04-01", 3),
    ]

    chains = find_chains(changes, links)

    assert [(c.cause.entity_id, c.effect.entity_id) for c in chains] == [("A", "B")]


# --------------------------------------------------------------------------
# What must never produce a chain
# --------------------------------------------------------------------------


def test_an_unprovable_ordering_produces_nothing():
    """Overlapping intervals permit both orderings, so neither may be claimed."""
    changes = [
        bounded("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 0, 4),
        bounded("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 2, 6),
    ]

    assert find_chains(changes, EDGE) == []


def test_a_low_confidence_change_is_excluded_even_when_everything_else_fits():
    """A row matched by title may not be the row we think it is.

    This is the most important filter in the module: a chain built on a mistaken
    identity is fiction with a citation attached.
    """
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact(
            "~anon-abc",
            "planned_end",
            "2026-03-20",
            "2026-04-01",
            3,
            identity_confidence="low",
        ),
    ]

    assert find_chains(changes, EDGE) == []


def test_unrelated_entities_produce_no_chain_however_well_ordered():
    """Two events in the right order with nothing connecting them is a coincidence."""
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("OTHER-1", "planned_end", "2026-03-20", "2026-04-01", 3),
    ]

    assert find_chains(changes, UNRELATED) == []


def test_a_cause_far_outside_the_template_window_is_dropped():
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 0),
        exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 200),
    ]

    assert find_chains(changes, EDGE) == []


def test_a_pattern_nobody_asked_about_produces_no_chain():
    """Ordered, linked, and still not a hypothesis on the list."""
    changes = [
        exact("WBS-108", "progress", "10", "20", 1),
        exact("WBS-114", "progress", "0", "5", 3),
    ]

    assert find_chains(changes, EDGE) == []


# --------------------------------------------------------------------------
# Honest lag reporting
# --------------------------------------------------------------------------


def test_a_bounded_effect_reports_the_lag_as_an_interval():
    """Snapshot data cannot produce a single lag, so it must not report one."""
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        bounded("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 3, 7),
    ]

    chain = find_chains(changes, EDGE)[0]

    assert chain.lag_days_min == 2.0
    assert chain.lag_days_max == 6.0
    assert not chain.lag_is_certain
    assert chain.basis is OrderingBasis.BOUNDED_DISJOINT


# --------------------------------------------------------------------------
# Link strength and ranking
# --------------------------------------------------------------------------


def test_an_edge_backed_chain_is_never_reported_as_a_project_coincidence():
    """When several templates match, the strongest-linked one wins."""
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 3),
    ]

    chain = find_chains(changes, EDGE)[0]

    assert chain.template_id == "dependency_slip_hits_successor"


def test_a_path_linked_slip_uses_the_cascade_template():
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("WBS-121", "planned_end", "2026-05-14", "2026-05-26", 3),
    ]

    chain = find_chains(changes, Links(default=LinkBasis.DEPENDENCY_PATH))[0]

    assert chain.template_id == "slip_cascades_downstream"
    assert chain.link is LinkBasis.DEPENDENCY_PATH


def test_stronger_evidence_sorts_first():
    """A PM's first question of a finding is 'how do you know', not 'how big'."""
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 3),
        exact("QA-1", "status", "Open", "Blocked", 5, entity_type="qa_item"),
    ]
    links = Links(
        mapping={("WBS-108", "WBS-114"): LinkBasis.DEPENDENCY_EDGE},
        default=LinkBasis.SAME_PROJECT,
    )

    chains = find_chains(changes, links)

    assert chains[0].link is LinkBasis.DEPENDENCY_EDGE
    assert all(
        chains[i].rank_key <= chains[i + 1].rank_key for i in range(len(chains) - 1)
    )


# --------------------------------------------------------------------------
# QA patterns
# --------------------------------------------------------------------------


def test_blocked_delivery_work_stalling_qa_is_recognised():
    changes = [
        exact("WBS-108", "status", "In Progress", "Blocked", 1),
        exact("QA-002", "status", "Open", "Blocked", 3, entity_type="qa_item"),
    ]

    chain = find_chains(changes, PROJECT)[0]

    assert chain.template_id == "blocked_work_stalls_qa"
    assert chain.link is LinkBasis.SAME_PROJECT


def test_a_compounding_qa_backlog_is_aggregated():
    """One blocked test is noise; a queue behind one cause is the finding."""
    changes = [exact("QA-001", "status", "Open", "Blocked", 1, entity_type="qa_item")]
    changes += [
        exact(f"QA-{n:03d}", "status", "Open", "Blocked", 3, entity_type="qa_item")
        for n in range(2, 8)
    ]

    grouped = group_by_template(find_chains(changes, PROJECT))

    assert len(grouped["qa_backlog_compounds"]) == 6


def test_one_effect_keeps_only_its_best_explanation():
    """Five competing reasons for one date move is worse than one defensible one."""
    changes = [
        exact("WBS-101", "planned_end", "2026-01-30", "2026-02-10", 0),
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1),
        exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 3),
    ]
    links = Links(
        mapping={("WBS-108", "WBS-114"): LinkBasis.DEPENDENCY_EDGE},
        default=LinkBasis.SAME_PROJECT,
    )

    best = strongest_per_effect(find_chains(changes, links))
    for_114 = [c for c in best if c.effect.entity_id == "WBS-114"]

    assert len(for_114) == 1
    assert for_114[0].cause.entity_id == "WBS-108"


def test_evidence_ids_carry_both_ends():
    """The chain raw -> tool -> domain -> finding must stay intact."""
    changes = [
        exact("WBS-108", "planned_end", "2026-03-04", "2026-03-16", 1, raw_data_id=11),
        exact("WBS-114", "planned_end", "2026-03-20", "2026-04-01", 3, raw_data_id=22),
    ]

    assert find_chains(changes, EDGE)[0].evidence_ids() == [11, 22]
