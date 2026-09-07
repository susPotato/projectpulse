"""The ordering guard - the rule every causal claim rests on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.intelligence.temporal.ordering import (
    OrderingBasis,
    is_orderable,
    ordered_pairs,
    ordering_basis,
    provably_before,
)

MAR = lambda d, h=0: datetime(2026, 3, d, h, tzinfo=timezone.utc)  # noqa: E731


@dataclass(frozen=True)
class Event:
    """Stands in for a StateChange row - the guard needs no database."""

    name: str
    occurred_at_lower: datetime
    occurred_at: datetime


def exact(name: str, when: datetime) -> Event:
    return Event(name, when, when)


def bounded(name: str, lower: datetime, upper: datetime) -> Event:
    return Event(name, lower, upper)


class TestExactEvents:
    def test_earlier_precedes_later(self):
        assert provably_before(exact("a", MAR(4)), exact("b", MAR(6)))

    def test_ordering_is_asymmetric(self):
        a, b = exact("a", MAR(4)), exact("b", MAR(6))
        assert provably_before(a, b)
        assert not provably_before(b, a)

    def test_simultaneous_events_are_not_ordered(self):
        a, b = exact("a", MAR(4)), exact("b", MAR(4))
        assert not provably_before(a, b)
        assert not provably_before(b, a)

    def test_basis_is_exact(self):
        assert ordering_basis(exact("a", MAR(4)), exact("b", MAR(6))) is (
            OrderingBasis.EXACT
        )


class TestBoundedEvents:
    def test_disjoint_intervals_are_ordered(self):
        a = bounded("a", MAR(2), MAR(4))
        b = bounded("b", MAR(6), MAR(18))
        assert provably_before(a, b)
        assert ordering_basis(a, b) is OrderingBasis.BOUNDED_DISJOINT

    def test_overlapping_intervals_are_not_ordered_either_way(self):
        """The core rule. Overlap means the data permits both orderings."""
        a = bounded("a", MAR(2), MAR(10))
        b = bounded("b", MAR(6), MAR(18))
        assert not provably_before(a, b)
        assert not provably_before(b, a)
        assert ordering_basis(a, b) is OrderingBasis.UNPROVABLE

    def test_touching_intervals_are_not_ordered(self):
        """a ends exactly when b begins: b could have happened first."""
        a = bounded("a", MAR(2), MAR(6))
        b = bounded("b", MAR(6), MAR(18))
        assert not provably_before(a, b)


class TestMixedPrecision:
    def test_exact_before_a_later_interval_is_provable(self):
        """The demo's actual chain: a Jira slip preceding an Excel observation."""
        cause = exact("jira: env slipped", MAR(4, 9))
        effect = bounded("excel: qa blocked", MAR(6, 9), MAR(18, 9))
        assert provably_before(cause, effect)
        assert ordering_basis(cause, effect) is OrderingBasis.BOUNDED_DISJOINT
        assert not provably_before(effect, cause)

    def test_an_exact_event_inside_an_interval_is_not_ordered(self):
        """Regression guard against "exact must be comparable" reasoning.

        A Jira event at 14:00 on day 2 and an Excel change bounded across days
        1-3 are genuinely unorderable. Special-casing precision='exact' here is
        the tempting optimisation that reintroduces the bug.
        """
        jira = exact("jira", MAR(2, 14))
        excel = bounded("excel", MAR(1), MAR(3))
        assert not provably_before(jira, excel)
        assert not provably_before(excel, jira)
        assert not is_orderable(jira, excel)


class TestSameScanWindow:
    def test_two_changes_from_one_scan_are_never_ordered(self):
        """Everything an Excel scan detects shares one window, so nothing in it
        can be ordered against anything else in it - in either direction."""
        window = (MAR(6), MAR(18))
        events = [bounded(f"e{i}", *window) for i in range(6)]
        for a in events:
            for b in events:
                if a is not b:
                    assert not provably_before(a, b), f"{a.name} vs {b.name}"

    @pytest.mark.parametrize("offset_hours", [0, 1, 24, 24 * 11])
    def test_no_overlapping_pair_is_ever_ordered(self, offset_hours):
        """Property: overlap in any amount defeats ordering."""
        a = bounded("a", MAR(6), MAR(18))
        shift = timedelta(hours=offset_hours)
        b = bounded("b", MAR(6) + shift, MAR(18) + shift)
        overlaps = a.occurred_at_lower < b.occurred_at and b.occurred_at_lower < a.occurred_at
        if overlaps:
            assert not is_orderable(a, b)


def test_ordered_pairs_finds_only_provable_ones():
    events = [
        exact("cause", MAR(4)),
        bounded("effect", MAR(6), MAR(18)),
        bounded("sibling", MAR(6), MAR(18)),
    ]
    pairs = ordered_pairs(events)
    names = {(p.earlier.name, p.later.name) for p in pairs}

    assert ("cause", "effect") in names
    assert ("cause", "sibling") in names
    # The two same-window observations must not be ordered against each other.
    assert ("effect", "sibling") not in names
    assert ("sibling", "effect") not in names
