"""Tests for the fit-score formula and policy weights."""
from __future__ import annotations

import pytest

from cowork_local.core.routing.models import ModelMetadata, Policy, ProbeResult
from cowork_local.core.routing.scorer import POLICY_WEIGHTS, compute_fit_score


def _meta(**kw) -> ModelMetadata:
    base = dict(
        provider="anthropic",
        model_id="claude-x",
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.003,
        max_context=200000,
    )
    base.update(kw)
    return ModelMetadata(**base)


def _probe(**kw) -> ProbeResult:
    base = dict(latency_ms=500.0, success=True, quality_score=0.8, tokens_out=100)
    base.update(kw)
    return ProbeResult(**base)


# --------------------------------------------------------------------------- #
# Failure / availability short-circuits
# --------------------------------------------------------------------------- #
def test_failed_probe_scores_zero():
    probe = _probe(success=False, quality_score=0.9, error="boom")
    for policy in Policy:
        assert compute_fit_score(_meta(), probe, policy) == 0.0


def test_unavailable_model_scores_zero():
    meta = _meta(available=False)
    for policy in Policy:
        assert compute_fit_score(meta, _probe(), policy) == 0.0


# --------------------------------------------------------------------------- #
# Range + monotonicity
# --------------------------------------------------------------------------- #
def test_score_within_unit_interval():
    for policy in Policy:
        s = compute_fit_score(_meta(), _probe(), policy)
        assert 0.0 <= s <= 1.0


def test_higher_quality_scores_higher():
    lo = compute_fit_score(_meta(), _probe(quality_score=0.2), Policy.QUALITY)
    hi = compute_fit_score(_meta(), _probe(quality_score=0.9), Policy.QUALITY)
    assert hi > lo


def test_lower_latency_scores_higher_under_latency_policy():
    slow = compute_fit_score(_meta(), _probe(latency_ms=5000), Policy.LATENCY)
    fast = compute_fit_score(_meta(), _probe(latency_ms=100), Policy.LATENCY)
    assert fast > slow


def test_cheaper_scores_higher_under_cost_policy():
    cheap = compute_fit_score(
        _meta(cost_per_1k_input=0.0001, cost_per_1k_output=0.0002),
        _probe(),
        Policy.COST,
    )
    pricey = compute_fit_score(
        _meta(cost_per_1k_input=0.05, cost_per_1k_output=0.15),
        _probe(),
        Policy.COST,
    )
    assert cheap > pricey


# --------------------------------------------------------------------------- #
# Policy weighting behaviour
# --------------------------------------------------------------------------- #
def test_all_policy_rows_sum_to_one():
    for policy, weights in POLICY_WEIGHTS.items():
        assert abs(sum(weights) - 1.0) < 1e-9, policy


def test_quality_policy_favors_smart_slow_model_over_fast_dumb():
    """Under QUALITY, a smart-but-slow model beats a fast-but-weak one."""
    smart_slow = compute_fit_score(
        _meta(), _probe(quality_score=0.95, latency_ms=4000), Policy.QUALITY
    )
    fast_dumb = compute_fit_score(
        _meta(), _probe(quality_score=0.3, latency_ms=100), Policy.QUALITY
    )
    assert smart_slow > fast_dumb


def test_latency_policy_favors_fast_dumb_over_smart_slow():
    """Under LATENCY, the ordering flips — speed dominates."""
    smart_slow = compute_fit_score(
        _meta(), _probe(quality_score=0.95, latency_ms=8000), Policy.LATENCY
    )
    fast_dumb = compute_fit_score(
        _meta(), _probe(quality_score=0.5, latency_ms=50), Policy.LATENCY
    )
    assert fast_dumb > smart_slow


# --------------------------------------------------------------------------- #
# Unknown-cost handling
# --------------------------------------------------------------------------- #
def test_unknown_cost_does_not_beat_known_cheap_model_under_cost_policy():
    """A model with unknown price must not be handed a free cost advantage."""
    known_cheap = compute_fit_score(
        _meta(cost_per_1k_input=0.0001, cost_per_1k_output=0.0001),
        _probe(),
        Policy.COST,
    )
    unknown = compute_fit_score(
        _meta(cost_per_1k_input=None, cost_per_1k_output=None,
              metadata_incomplete=True),
        _probe(),
        Policy.COST,
    )
    # Both are usable; the genuinely-cheap known model should not score below
    # the unknown-price one (no fabricated cost=0 advantage).
    assert known_cheap >= unknown


def test_quality_score_clamped():
    """A judge returning >1 or <0 must not push fit outside [0,1]."""
    over = compute_fit_score(_meta(), _probe(quality_score=5.0), Policy.QUALITY)
    under = compute_fit_score(_meta(), _probe(quality_score=-3.0), Policy.QUALITY)
    assert 0.0 <= over <= 1.0
    assert 0.0 <= under <= 1.0
