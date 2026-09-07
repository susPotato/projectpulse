"""Match provably-ordered pairs against the named hypotheses.

This is the module that is allowed to say "because". Everything it needs has
already been established elsewhere and is only combined here:

* `ordering.py` proved the pair happened in that order,
* `templates.py` says the pair is a shape a PM asked about,
* `dependencies` says the two entities are connected at all.

A chain is emitted only when **all three** hold. Any one of them alone produces
the failure the design keeps warning about: ordering alone gives coincidences,
patterns alone give claims about events that may not have happened in that order,
and links alone give a graph with no time in it.

Four rules, each dropping something rather than hedging it:

1. **Low-confidence identity is excluded outright.** A row matched by title might
   not be the row we think it is; a chain built on one would be fiction with a
   citation. This is the single most important filter here.
2. **The lag is an interval, not a number.** Bounded changes make the gap between
   cause and effect a range, and it is reported as one. Collapsing it to a
   midpoint would invent precision the snapshot data never had.
3. **The link recorded is the one that was actually found**, not the one the
   template asked for. A template may accept "same project or better"; if a
   dependency edge was what connected them, the chain says so.
4. **One chain per cause/effect pair.** When several templates match, the one
   demanding the strongest link wins, so a direct dependency is never reported as
   a vague project-level coincidence.

Pure - no session, no ORM, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.intelligence.temporal.ordering import (
    OrderingBasis,
    ordering_basis,
)
from app.intelligence.temporal.templates import (
    TEMPLATES,
    CausalTemplate,
    HasChange,
    LinkBasis,
    link_rank,
    value_delta,
)

#: Identity confidence that disqualifies a change from any chain.
IDENTITY_LOW = "low"

SECONDS_PER_DAY = 86400.0


class LinkResolver(Protocol):
    """Answers what connects two entities, if anything.

    Implemented over the dependency graph in `intelligence/schedule/graph.py`.
    A protocol so this module stays pure and testable without NetworkX.
    """

    def link_between(self, cause_entity: str, effect_entity: str) -> LinkBasis | None:
        """The **strongest** link between two entities, or None if unrelated.

        None means no chain may be built at all: two events in the right order
        with nothing connecting them are a coincidence.
        """
        ...


@dataclass(frozen=True)
class CausalChain:
    """One defensible "A caused B", with everything needed to defend it."""

    template_id: str
    template_name: str
    question: str
    cause: HasChange
    effect: HasChange
    #: How the ordering was established - exact points, or disjoint intervals.
    basis: OrderingBasis
    #: What actually connected the two entities.
    link: LinkBasis
    #: Smallest gap the data permits, in days.
    lag_days_min: float
    #: Largest gap the data permits. Equal to `lag_days_min` only when both ends
    #: are exact.
    lag_days_max: float
    #: Magnitude of the effect, where it has one - days of slip, items added.
    effect_magnitude: float | None

    @property
    def is_exact(self) -> bool:
        return self.basis is OrderingBasis.EXACT

    @property
    def lag_is_certain(self) -> bool:
        return self.lag_days_min == self.lag_days_max

    @property
    def rank_key(self) -> tuple:
        """Sort key - strongest evidence first.

        Link strength dominates: a dependency-backed chain of two days beats a
        project-level coincidence of twenty, because the question a PM asks of a
        finding is "how do you know", not "how big".
        """
        return (
            link_rank(self.link),
            0 if self.is_exact else 1,
            0 if self.lag_is_certain else 1,
            -abs(self.effect_magnitude or 0.0),
            self.lag_days_min,
        )

    def evidence_ids(self) -> list[int]:
        """`_raw_data_id` of both ends, for the evidence panel.

        Copied, never re-derived - the chain raw -> tool -> domain -> finding is
        only intact if the same id survives every hop.
        """
        return [
            raw_id
            for raw_id in (
                getattr(self.cause, "raw_data_id", None),
                getattr(self.effect, "raw_data_id", None),
            )
            if raw_id is not None
        ]


def _days_between(later, earlier) -> float:
    return (later - earlier).total_seconds() / SECONDS_PER_DAY


def _eligible(change: HasChange) -> bool:
    """Whether a change may take part in a chain at all.

    The identity filter is here rather than at the call site so that no future
    caller can forget it.
    """
    return change.identity_confidence != IDENTITY_LOW


def find_chains(
    changes: Sequence[HasChange],
    resolver: LinkResolver,
    templates: Iterable[CausalTemplate] = TEMPLATES,
) -> list[CausalChain]:
    """Every chain the data actually supports, strongest evidence first.

    Quadratic in the number of changes, deliberately: the candidate set is the
    changes touching one project in one window, not the portfolio.
    """
    candidates = [c for c in changes if _eligible(c)]
    templates = list(templates)

    #: (cause_id, effect_id) -> the best chain found for that pair.
    best: dict[tuple[int, int], CausalChain] = {}

    for cause in candidates:
        for effect in candidates:
            if cause is effect:
                continue

            basis = ordering_basis(cause, effect)
            if basis is OrderingBasis.UNPROVABLE:
                continue

            link = resolver.link_between(cause.entity_id, effect.entity_id)
            if link is None:
                continue

            # Smallest and largest gaps the two intervals permit. `provably_before`
            # guarantees cause.occurred_at < effect.occurred_at_lower, so the
            # minimum is positive.
            lag_min = _days_between(effect.occurred_at_lower, cause.occurred_at)
            lag_max = _days_between(effect.occurred_at, cause.occurred_at_lower)

            for template in templates:
                # The template names the weakest link it will accept; anything
                # stronger also qualifies.
                if link_rank(link) > link_rank(template.link):
                    continue
                if not template.matches(cause, effect):
                    continue
                # Reject only when the gap is *certainly* too large. If any lag the
                # data permits is plausible, the chain stands and reports its own
                # interval rather than being silently dropped on a midpoint.
                if lag_min > template.max_lag_days:
                    continue

                chain = CausalChain(
                    template_id=template.id,
                    template_name=template.name,
                    question=template.question,
                    cause=cause,
                    effect=effect,
                    basis=basis,
                    link=link,
                    lag_days_min=lag_min,
                    lag_days_max=lag_max,
                    effect_magnitude=value_delta(effect.old_value, effect.new_value),
                )

                key = (id(cause), id(effect))
                incumbent = best.get(key)
                # Strongest *required* link wins, so a direct dependency is never
                # reported as a project-level coincidence.
                if incumbent is None or link_rank(template.link) < link_rank(
                    _required_link(incumbent, templates)
                ):
                    best[key] = chain

    return sorted(best.values(), key=lambda c: c.rank_key)


def _required_link(chain: CausalChain, templates: Sequence[CausalTemplate]) -> LinkBasis:
    for template in templates:
        if template.id == chain.template_id:
            return template.link
    return chain.link


def group_by_template(chains: Sequence[CausalChain]) -> dict[str, list[CausalChain]]:
    """Chains bucketed by hypothesis.

    The aggregate is often the finding: one blocked QA item is noise, nine behind
    the same cause is what a PM escalates.
    """
    grouped: dict[str, list[CausalChain]] = {}
    for chain in chains:
        grouped.setdefault(chain.template_id, []).append(chain)
    return grouped


def strongest_per_effect(chains: Sequence[CausalChain]) -> list[CausalChain]:
    """One chain per effect - the best-evidenced explanation for each outcome.

    A single slip can match several causes. Showing a PM five competing reasons
    for one date move is worse than showing the one we can defend best.
    """
    best: dict[str, CausalChain] = {}
    for chain in sorted(chains, key=lambda c: c.rank_key):
        key = f"{chain.effect.entity_id}.{chain.effect.field}"
        best.setdefault(key, chain)
    return sorted(best.values(), key=lambda c: c.rank_key)


@dataclass(frozen=True)
class ChainGroup:
    """One cause and everything it explains.

    Emitting a finding per cause/effect pair is technically correct and useless:
    one delivery task slipping ahead of eight blocked tests produces eight
    identical sentences, and a PM cannot tell that it is one problem. The queue is
    the finding, not any single item in it.

    `representative` is the best-evidenced pair in the group and carries the
    evidence a reader clicks through to; `effect_count` is what makes the
    aggregate legible.
    """

    template_id: str
    template_name: str
    question: str
    cause: HasChange
    chains: tuple[CausalChain, ...]

    @property
    def representative(self) -> CausalChain:
        return self.chains[0]

    @property
    def effect_count(self) -> int:
        return len({(c.effect.entity_id, c.effect.field) for c in self.chains})

    @property
    def link(self) -> LinkBasis:
        """The strongest link anywhere in the group."""
        return min((c.link for c in self.chains), key=link_rank)

    @property
    def rank_key(self) -> tuple:
        # A cause explaining more effects outranks one explaining fewer, but only
        # after evidence strength - breadth is not evidence.
        best = self.representative.rank_key
        return (best[0], best[1], -self.effect_count, *best[2:])


def group_chains(
    chains: Sequence[CausalChain], *, limit: int | None = None
) -> list[ChainGroup]:
    """Collapse pairs into one group per cause, strongest first.

    Effects are deduplicated first, so a single outcome contributes to exactly one
    group - the one that explains it best. Without that a slip explained by three
    upstream causes would inflate all three groups and none of the counts would
    mean anything.
    """
    buckets: dict[tuple[str, str, str], list[CausalChain]] = {}
    for chain in strongest_per_effect(chains):
        key = (chain.template_id, chain.cause.entity_id, chain.cause.field)
        buckets.setdefault(key, []).append(chain)

    groups = [
        ChainGroup(
            template_id=members[0].template_id,
            template_name=members[0].template_name,
            question=members[0].question,
            cause=members[0].cause,
            chains=tuple(sorted(members, key=lambda c: c.rank_key)),
        )
        for members in buckets.values()
    ]
    groups.sort(key=lambda g: g.rank_key)
    return groups[:limit] if limit else groups
