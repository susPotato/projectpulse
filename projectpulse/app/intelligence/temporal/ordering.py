"""Can we prove one event happened before another?

The single most defensible piece of engineering in this product, and the smallest.

Every state change carries an interval `[occurred_at_lower, occurred_at]` rather
than a timestamp. Jira changelogs collapse it to a point; Excel snapshots leave it
as wide as the gap between two scans. One event precedes another only when its
*latest* possible time is before the other's *earliest* - anything less and the
data permits both orderings, so neither may be claimed.

Two traps, both easy to fall into and both silently wrong:

* **Never compare `scan_id`.** Sync windows deliberately overlap, so two bounded
  events from different scans can still have overlapping intervals. Same-scan is
  sufficient for unorderable, not necessary.
* **`precision='exact'` gets no shortcut.** A Jira event at 14:00 on day 2 and an
  Excel change bounded across days 1-3 are not orderable either. The interval
  arithmetic already handles this; a special case for exact events reintroduces
  the bug it was meant to avoid.

Unprovable orderings are **dropped**, never downgraded to a hedge. A caveat is
something a reader can skip; an absent claim is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class HasInterval(Protocol):
    """Anything carrying a time interval - a StateChange row, or a test double."""

    occurred_at: datetime
    occurred_at_lower: datetime


class OrderingBasis(StrEnum):
    #: Both events are points in time; the ordering is direct.
    EXACT = "exact"
    #: At least one is an interval, but the intervals do not overlap.
    BOUNDED_DISJOINT = "bounded_disjoint"
    #: The intervals overlap. No ordering may be claimed in either direction.
    UNPROVABLE = "unprovable"


def provably_before(a: HasInterval, b: HasInterval) -> bool:
    """True when *a* is certainly earlier than *b*.

    `a`'s latest possible time must fall before `b`'s earliest possible time.
    """
    return a.occurred_at < b.occurred_at_lower


def ordering_basis(a: HasInterval, b: HasInterval) -> OrderingBasis:
    """How - or whether - `a -> b` can be established."""
    if not provably_before(a, b):
        return OrderingBasis.UNPROVABLE
    if a.occurred_at == a.occurred_at_lower and b.occurred_at == b.occurred_at_lower:
        return OrderingBasis.EXACT
    return OrderingBasis.BOUNDED_DISJOINT


def is_orderable(a: HasInterval, b: HasInterval) -> bool:
    """Whether *either* direction can be proven. Symmetric."""
    return provably_before(a, b) or provably_before(b, a)


@dataclass(frozen=True)
class OrderedPair:
    earlier: HasInterval
    later: HasInterval
    basis: OrderingBasis


def ordered_pairs(events: list[HasInterval]) -> list[OrderedPair]:
    """Every pair whose ordering the data actually supports.

    Quadratic, and deliberately so at this scale: a candidate set is the handful
    of changes touching one project around one finding, not the whole portfolio.
    """
    result: list[OrderedPair] = []
    for a in events:
        for b in events:
            if a is b:
                continue
            basis = ordering_basis(a, b)
            if basis is not OrderingBasis.UNPROVABLE:
                result.append(OrderedPair(a, b, basis))
    return result
