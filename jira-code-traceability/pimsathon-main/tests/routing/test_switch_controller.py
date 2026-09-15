"""Tests for switch decisions and the pending-switch registry.

Covers: Auto vs Manual vs Off, the min-score-gain threshold, confirm/reject,
timeout → keep current, and idempotent confirm (task runs exactly once).
"""
from __future__ import annotations

import pytest

from cowork_local.core.routing.models import (
    ModelAssessment,
    ModelMetadata,
    ProbeResult,
    SwitchMode,
    SwitchStatus,
    TaskType,
)
from cowork_local.core.routing.selector import rank_models
from cowork_local.core.routing.switch_controller import (
    PendingSwitchRegistry,
    decide,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _assessment(model_id, quality, *, provider="anthropic") -> ModelAssessment:
    meta = ModelMetadata(
        provider=provider, model_id=model_id,
        cost_per_1k_input=0.001, cost_per_1k_output=0.003, max_context=100000,
    )
    probe = ProbeResult(latency_ms=300, success=True, quality_score=quality, tokens_out=40)
    return ModelAssessment(metadata=meta, probes={TaskType.CODING.value: probe})


def _ranking(*assessments):
    return rank_models(assessments, TaskType.CODING, task_type_policy())


def task_type_policy():
    from cowork_local.core.routing.models import Policy
    return Policy.QUALITY


# --------------------------------------------------------------------------- #
# decide() — pure decision logic
# --------------------------------------------------------------------------- #
def test_off_never_switches():
    weak = _assessment("weak", 0.3)
    strong = _assessment("strong", 0.95)
    ranking = _ranking(weak, strong)
    d = decide("anthropic/weak", ranking, SwitchMode.OFF, 0.05)
    assert d.should_switch is False
    assert "off" in d.reason.lower()


def test_auto_switches_when_gain_clears_threshold():
    weak = _assessment("weak", 0.3)
    strong = _assessment("strong", 0.95)
    ranking = _ranking(weak, strong)
    d = decide("anthropic/weak", ranking, SwitchMode.AUTO, 0.05)
    assert d.should_switch is True
    assert d.to_model == "anthropic/strong"
    assert d.score_gain > 0.05


def test_no_switch_when_gain_below_threshold():
    a = _assessment("a", 0.80)
    b = _assessment("b", 0.82)  # only marginally better
    ranking = _ranking(a, b)
    d = decide("anthropic/a", ranking, SwitchMode.AUTO, 0.20)  # demand a big gain
    assert d.should_switch is False
    assert "keeping current" in d.reason.lower()


def test_no_switch_when_current_is_already_best():
    a = _assessment("a", 0.95)
    b = _assessment("b", 0.5)
    ranking = _ranking(a, b)
    d = decide("anthropic/a", ranking, SwitchMode.AUTO, 0.05)
    assert d.should_switch is False
    assert "already best-fit" in d.reason.lower()


def test_manual_decision_marks_mode_manual():
    weak = _assessment("weak", 0.3)
    strong = _assessment("strong", 0.95)
    ranking = _ranking(weak, strong)
    d = decide("anthropic/weak", ranking, SwitchMode.MANUAL, 0.05)
    assert d.should_switch is True
    assert d.mode == SwitchMode.MANUAL


def test_no_current_model_adopts_best():
    strong = _assessment("strong", 0.9)
    ranking = _ranking(strong)
    d = decide(None, ranking, SwitchMode.AUTO, 0.05)
    assert d.should_switch is True
    assert d.to_model == "anthropic/strong"


def test_no_candidate_available():
    ranking = rank_models([], TaskType.CODING)
    d = decide("anthropic/x", ranking, SwitchMode.AUTO, 0.05)
    assert d.should_switch is False


def test_reason_contains_scores_and_gain():
    weak = _assessment("weak", 0.5)
    strong = _assessment("strong", 0.9)
    ranking = _ranking(weak, strong)
    d = decide("anthropic/weak", ranking, SwitchMode.AUTO, 0.05)
    # e.g. "coding fit 0.xx > current 0.yy, gain 0.zz — switch to strong"
    assert "fit" in d.reason and "gain" in d.reason


# --------------------------------------------------------------------------- #
# PendingSwitchRegistry — manual confirm/reject/timeout/idempotency
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _decision():
    weak = _assessment("weak", 0.5)
    strong = _assessment("strong", 0.9)
    ranking = _ranking(weak, strong)
    return decide("anthropic/weak", ranking, SwitchMode.MANUAL, 0.05)


def test_confirm_runs_with_new_model():
    reg = PendingSwitchRegistry()
    ps = reg.create(_decision(), {"prompt": "hi"}, timeout_sec=60)
    calls = []

    def run(model_key, switched):
        calls.append((model_key, switched))
        return {"model": model_key, "switched": switched, "text": "done"}

    result = reg.resolve(ps.request_id, approve=True, run=run)
    assert result["model"] == "anthropic/strong"
    assert result["switched"] is True
    assert calls == [("anthropic/strong", True)]
    assert reg.get(ps.request_id).status == SwitchStatus.CONFIRMED


def test_reject_runs_with_current_model():
    reg = PendingSwitchRegistry()
    ps = reg.create(_decision(), {"prompt": "hi"}, timeout_sec=60)

    def run(model_key, switched):
        return {"model": model_key, "switched": switched}

    result = reg.resolve(ps.request_id, approve=False, run=run)
    assert result["model"] == "anthropic/weak"  # stayed on current
    assert result["switched"] is False
    assert reg.get(ps.request_id).status == SwitchStatus.REJECTED


def test_confirm_is_idempotent_runs_once():
    reg = PendingSwitchRegistry()
    ps = reg.create(_decision(), {"prompt": "hi"}, timeout_sec=60)
    count = {"n": 0}

    def run(model_key, switched):
        count["n"] += 1
        return {"run_number": count["n"], "model": model_key}

    r1 = reg.resolve(ps.request_id, approve=True, run=run)
    r2 = reg.resolve(ps.request_id, approve=True, run=run)
    r3 = reg.resolve(ps.request_id, approve=True, run=run)
    assert count["n"] == 1           # task executed exactly once
    assert r1 == r2 == r3            # cached result replayed


def test_timeout_forces_current_model_on_resolve():
    clock = FakeClock()
    reg = PendingSwitchRegistry(clock=clock)
    ps = reg.create(_decision(), {"prompt": "hi"}, timeout_sec=60)

    clock.advance(120)  # blow past the confirm window
    assert reg.get(ps.request_id).status == SwitchStatus.EXPIRED

    # Even an approve after expiry must run with the CURRENT model.
    def run(model_key, switched):
        return {"model": model_key, "switched": switched}

    result = reg.resolve(ps.request_id, approve=True, run=run)
    assert result["model"] == "anthropic/weak"
    assert result["switched"] is False


def test_sweep_expired_marks_overdue():
    clock = FakeClock()
    reg = PendingSwitchRegistry(clock=clock)
    ps = reg.create(_decision(), {}, timeout_sec=30)
    assert reg.sweep_expired() == []
    clock.advance(31)
    assert reg.sweep_expired() == [ps.request_id]
    assert reg.get(ps.request_id).status == SwitchStatus.EXPIRED


def test_resolve_unknown_id_returns_none():
    reg = PendingSwitchRegistry()
    assert reg.resolve("does-not-exist", approve=True, run=lambda k, s: {}) is None


def test_purge_removes_terminal_entries():
    reg = PendingSwitchRegistry()
    ps = reg.create(_decision(), {}, timeout_sec=60)
    reg.resolve(ps.request_id, approve=False, run=lambda k, s: {"ok": True})
    # keep_resolved=True retains entries that cached a result (idempotency).
    assert reg.purge(keep_resolved=True) == 0
    assert reg.purge(keep_resolved=False) == 1
    assert reg.get(ps.request_id) is None
