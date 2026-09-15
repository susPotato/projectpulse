"""Fit scoring: turn a model's metadata + probe result into a 0..1 score.

The score blends three normalized terms — quality (from the judge), cost
(cheaper is better) and latency (faster is better) — weighted by the active
:class:`~cowork_local.core.routing.models.Policy`::

    fit = w_quality * quality
        + w_cost    * 1/(1 + cost)
        + w_latency * 1/(1 + latency_s)

Each term is in ``[0, 1]`` and the weights sum to 1, so ``fit`` is in ``[0, 1]``.
A probe that failed scores 0 outright — an unusable model must never win.
"""
from __future__ import annotations

from typing import Dict

from .models import ModelMetadata, Policy, ProbeResult

# Weights per policy: (quality, cost, latency). Each row sums to 1.0.
#   quality  — pick the smartest model, cost/speed barely matter.
#   cost     — pick the cheapest usable model.
#   latency  — pick the fastest usable model.
#   balanced — a sensible default that still leans on quality.
POLICY_WEIGHTS: Dict[Policy, tuple[float, float, float]] = {
    Policy.QUALITY: (0.80, 0.10, 0.10),
    Policy.COST: (0.20, 0.70, 0.10),
    Policy.LATENCY: (0.20, 0.10, 0.70),
    Policy.BALANCED: (0.50, 0.25, 0.25),
}

# When a model's price is unknown (metadata_incomplete), we cannot compute a
# real cost term. Rather than reward the gap (cost=0 → term=1.0, unfairly
# best) or nuke the model (term=0), we assume a neutral middling price so it
# competes on quality/latency without a fabricated cost advantage.
_UNKNOWN_COST_PER_1K = 0.01


def _cost_term(metadata: ModelMetadata) -> float:
    """Normalized cost term ``1/(1+cost)`` in ``(0, 1]`` — higher is cheaper."""
    cost = metadata.avg_cost_per_1k
    if cost is None:
        cost = _UNKNOWN_COST_PER_1K
    cost = max(0.0, float(cost))
    return 1.0 / (1.0 + cost)


def _latency_term(probe: ProbeResult) -> float:
    """Normalized latency term ``1/(1+latency_s)`` in ``(0, 1]`` — higher is faster."""
    latency_s = max(0.0, float(probe.latency_ms)) / 1000.0
    return 1.0 / (1.0 + latency_s)


def compute_fit_score(
    metadata: ModelMetadata,
    probe: ProbeResult,
    policy: Policy = Policy.BALANCED,
) -> float:
    """Fit score in ``[0, 1]`` for one model on one task, under ``policy``.

    Returns 0.0 immediately if the probe failed or the model is unavailable —
    an unusable model is never routable regardless of its price/speed.
    """
    if not probe.success or not metadata.available:
        return 0.0

    w_quality, w_cost, w_latency = POLICY_WEIGHTS.get(
        policy, POLICY_WEIGHTS[Policy.BALANCED]
    )

    quality = min(1.0, max(0.0, float(probe.quality_score)))
    cost_term = _cost_term(metadata)
    latency_term = _latency_term(probe)

    score = w_quality * quality + w_cost * cost_term + w_latency * latency_term
    # Clamp defensively against float drift; the math already bounds it to [0,1].
    return round(min(1.0, max(0.0, score)), 6)


__all__ = ["POLICY_WEIGHTS", "compute_fit_score"]
