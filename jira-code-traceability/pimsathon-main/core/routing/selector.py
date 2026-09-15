"""Select the best-fit model for a task type under a policy.

The selector is deliberately *stateless and pure*: given a set of assessments,
a task type and a policy, it recomputes each candidate's fit score from its
stored probe + metadata (via :func:`scorer.compute_fit_score`) and ranks them.

Recomputing (rather than trusting the ``fit_scores`` cached at assess time)
means changing the routing **policy** — quality → cost, say — re-ranks instantly
from existing measurements, with **no** expensive re-probing. Probes are the raw
truth; fit is a pure function of ``(probe, metadata, policy)``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .models import ModelAssessment, Policy, TaskType
from .scorer import compute_fit_score


@dataclass
class RankedCandidate:
    """One candidate's standing for a given task type + policy."""

    assessment: ModelAssessment
    score: float

    @property
    def key(self) -> str:
        return self.assessment.key


@dataclass
class Ranking:
    """Full ordering of candidates for a task type, best first."""

    task_type: TaskType
    policy: Policy
    ranked: List[RankedCandidate] = field(default_factory=list)

    @property
    def best(self) -> Optional[RankedCandidate]:
        return self.ranked[0] if self.ranked else None

    def score_of(self, key: str) -> float:
        """Score of a specific candidate key, or 0.0 if it isn't ranked
        (unavailable / filtered out / failed probe)."""
        for c in self.ranked:
            if c.key == key:
                return c.score
        return 0.0

    def as_dicts(self) -> List[Dict]:
        """JSON-friendly ranking for API responses / the UI."""
        return [
            {
                "key": c.key,
                "provider": c.assessment.metadata.provider,
                "model_id": c.assessment.metadata.model_id,
                "score": c.score,
                "tier": c.assessment.metadata.tier,
                "metadata_incomplete": c.assessment.metadata.metadata_incomplete,
            }
            for c in self.ranked
        ]


def _has_capabilities(assessment: ModelAssessment, required: Set[str]) -> bool:
    return required.issubset(assessment.metadata.capabilities)


def rank_models(
    assessments: Iterable[ModelAssessment],
    task_type: TaskType,
    policy: Policy = Policy.BALANCED,
    *,
    required_capabilities: Optional[Iterable[str]] = None,
) -> Ranking:
    """Rank candidates for ``task_type`` under ``policy``, best first.

    A candidate is excluded when it is unavailable, lacks a probe for this task
    type, fails the required-capability filter, or scores 0 (failed probe).
    Ties break by lower average cost, then by model id, for stable ordering.
    """
    required: Set[str] = set(required_capabilities or ())
    scored: List[RankedCandidate] = []

    for a in assessments:
        if not a.metadata.available:
            continue
        if required and not _has_capabilities(a, required):
            continue
        probe = a.probes.get(task_type.value)
        if probe is None:
            continue
        score = compute_fit_score(a.metadata, probe, policy)
        if score <= 0.0:
            continue
        scored.append(RankedCandidate(assessment=a, score=score))

    def _sort_key(c: RankedCandidate):
        cost = c.assessment.metadata.avg_cost_per_1k
        cost = cost if cost is not None else float("inf")
        # score desc, then cheaper, then model id for determinism.
        return (-c.score, cost, c.assessment.metadata.model_id)

    scored.sort(key=_sort_key)
    return Ranking(task_type=task_type, policy=policy, ranked=scored)


def best_model(
    assessments: Iterable[ModelAssessment],
    task_type: TaskType,
    policy: Policy = Policy.BALANCED,
    *,
    required_capabilities: Optional[Iterable[str]] = None,
) -> Optional[RankedCandidate]:
    """The single best-fit candidate for ``task_type``, or ``None`` if none
    qualify (all unavailable / filtered / failed)."""
    return rank_models(
        assessments,
        task_type,
        policy,
        required_capabilities=required_capabilities,
    ).best


__all__ = ["Ranking", "RankedCandidate", "rank_models", "best_model"]
