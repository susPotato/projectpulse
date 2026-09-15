"""Tests for the selector: ranking, filtering, capability gating, policy re-rank."""
from __future__ import annotations

import pytest

from cowork_local.core.routing.models import (
    ModelAssessment,
    ModelMetadata,
    Policy,
    ProbeResult,
    TaskType,
)
from cowork_local.core.routing.selector import best_model, rank_models


def _assessment(
    model_id,
    *,
    provider="anthropic",
    quality=0.8,
    latency_ms=500,
    cost_in=0.001,
    cost_out=0.003,
    available=True,
    caps=None,
    task=TaskType.CODING,
    probe_success=True,
) -> ModelAssessment:
    meta = ModelMetadata(
        provider=provider,
        model_id=model_id,
        cost_per_1k_input=cost_in,
        cost_per_1k_output=cost_out,
        max_context=100000,
        capabilities=set(caps or []),
        available=available,
    )
    probe = ProbeResult(
        latency_ms=latency_ms, success=probe_success,
        quality_score=quality, tokens_out=50,
    )
    return ModelAssessment(metadata=meta, probes={task.value: probe})


def test_empty_returns_no_best():
    assert best_model([], TaskType.CODING) is None


def test_best_is_highest_quality_under_quality_policy():
    weak = _assessment("weak", quality=0.3)
    strong = _assessment("strong", quality=0.95)
    best = best_model([weak, strong], TaskType.CODING, Policy.QUALITY)
    assert best is not None
    assert best.assessment.metadata.model_id == "strong"


def test_unavailable_excluded():
    down = _assessment("down", quality=0.99, available=False)
    up = _assessment("up", quality=0.5)
    ranking = rank_models([down, up], TaskType.CODING)
    keys = [c.assessment.metadata.model_id for c in ranking.ranked]
    assert "down" not in keys
    assert ranking.best.assessment.metadata.model_id == "up"


def test_failed_probe_excluded():
    broken = _assessment("broken", quality=0.99, probe_success=False)
    ok = _assessment("ok", quality=0.4)
    best = best_model([broken, ok], TaskType.CODING)
    assert best.assessment.metadata.model_id == "ok"


def test_missing_probe_for_task_excluded():
    # Only has a CODING probe; asking for REASONING must exclude it.
    coding_only = _assessment("c", task=TaskType.CODING)
    assert best_model([coding_only], TaskType.REASONING) is None


def test_required_capability_filters_out_incapable():
    no_vision = _assessment("text", quality=0.95, caps=[])
    vision = _assessment("vision", quality=0.6, caps=["vision"])
    best = best_model(
        [no_vision, vision], TaskType.CODING, required_capabilities=["vision"]
    )
    assert best.assessment.metadata.model_id == "vision"


def test_policy_change_reranks_without_reprobe():
    """Same assessments, different policy → different winner, no re-probing."""
    smart_pricey_slow = _assessment(
        "opus", quality=0.95, latency_ms=6000, cost_in=0.015, cost_out=0.075
    )
    cheap_fast_ok = _assessment(
        "haiku", quality=0.7, latency_ms=200, cost_in=0.0002, cost_out=0.0004
    )
    candidates = [smart_pricey_slow, cheap_fast_ok]

    q_best = best_model(candidates, TaskType.CODING, Policy.QUALITY)
    c_best = best_model(candidates, TaskType.CODING, Policy.COST)
    l_best = best_model(candidates, TaskType.CODING, Policy.LATENCY)

    assert q_best.assessment.metadata.model_id == "opus"    # quality wins
    assert c_best.assessment.metadata.model_id == "haiku"   # cost wins
    assert l_best.assessment.metadata.model_id == "haiku"   # latency wins


def test_ranking_is_descending_and_stable():
    a = _assessment("a", quality=0.9)
    b = _assessment("b", quality=0.6)
    c = _assessment("c", quality=0.3)
    ranking = rank_models([b, c, a], TaskType.CODING, Policy.QUALITY)
    scores = [rc.score for rc in ranking.ranked]
    assert scores == sorted(scores, reverse=True)
    assert [rc.assessment.metadata.model_id for rc in ranking.ranked] == ["a", "b", "c"]


def test_score_of_returns_zero_for_unranked():
    a = _assessment("a", quality=0.9)
    ranking = rank_models([a], TaskType.CODING)
    assert ranking.score_of("anthropic/a") > 0
    assert ranking.score_of("anthropic/missing") == 0.0
