"""End-to-end tests for RoutingService with a fake client + temp store.

No real API calls: the fake client answers both benchmark probes and judge
calls deterministically, so reassess → score → route → confirm all run offline.
"""
from __future__ import annotations

import copy

import pytest

from cowork_local.config import DEFAULT_CONFIG, AppConfig
from cowork_local.core.routing.clients import CompletionResult
from cowork_local.core.routing.models import SwitchMode, TaskType, candidate_key
from cowork_local.core.routing.service import RoutingService
from cowork_local.core.routing.store import AssessmentStore
from cowork_local.state import AppContext


class FakeClient:
    """Answers probes per model and grades via an embedded-answer lookup."""

    def __init__(self, answers, quality):
        self.answers = answers      # {(provider, model_id): "answer text"}
        self.quality = quality      # {"answer text": score}

    def complete(self, provider, model_id, messages) -> CompletionResult:
        text = messages[0]["content"]
        if "grading an AI assistant" in text:  # judge rubric
            score = 0.0
            for answer, q in self.quality.items():
                if answer and answer in text:
                    score = q
                    break
            return CompletionResult(text=f'{{"score": {score}}}')
        answer = self.answers.get((provider, model_id))
        if answer is None:
            return CompletionResult(error="unavailable")
        return CompletionResult(text=answer, tokens_out=len(answer) // 4)


@pytest.fixture()
def ctx(tmp_path):
    data = copy.deepcopy(DEFAULT_CONFIG)
    # Two candidates on one provider; pin a judge model that's NOT a candidate.
    data["providers"] = {
        "anthropic": {"base_url": "x", "api_key": "x", "model": "strong-model"},
    }
    data["routing"]["candidates"] = [
        {"provider": "anthropic", "model_id": "strong-model", "tier": "powerful"},
        {"provider": "anthropic", "model_id": "weak-model", "tier": "fast"},
    ]
    data["routing"]["judge_provider"] = "anthropic"
    data["routing"]["judge_model"] = "judge-model"
    data["routing"]["policy"] = "quality"
    data["routing"]["min_score_gain"] = 0.05
    cfg = AppConfig(data=data, path=tmp_path / "config.json")
    return AppContext(cfg)


@pytest.fixture()
def service(ctx, tmp_path):
    client = FakeClient(
        answers={
            ("anthropic", "strong-model"): "STRONG-DETAILED-CORRECT-ANSWER",
            ("anthropic", "weak-model"): "weak",
        },
        quality={"STRONG-DETAILED-CORRECT-ANSWER": 0.95, "weak": 0.35},
    )
    store = AssessmentStore(store_path=tmp_path / "assess.json", history_dir=tmp_path / "hist")
    return RoutingService(ctx, store=store, client=client)


def test_reassess_scores_and_persists(service):
    result = service.reassess()
    assert set(result) == {"anthropic/strong-model", "anthropic/weak-model"}
    # strong beats weak on coding under quality policy
    strong = result["anthropic/strong-model"].fit_for(TaskType.CODING)
    weak = result["anthropic/weak-model"].fit_for(TaskType.CODING)
    assert strong > weak
    assert service.status()["count"] == 2


def test_best_for_returns_strong(service):
    service.reassess()
    ranking = service.best_for(TaskType.CODING)
    assert ranking.best is not None
    assert ranking.best.assessment.metadata.model_id == "strong-model"


def test_route_off_never_switches(service):
    service.reassess()
    service.ctx.config.data["routing"]["switch_mode"] = "off"
    r = service.route("cowork", "Write a Python function", "anthropic", "weak-model")
    assert r.mode == SwitchMode.OFF
    assert r.should_switch is False


def test_route_auto_switches_to_strong(service):
    service.reassess()
    service.ctx.config.data["routing"]["switch_mode"] = "auto"
    r = service.route("cowork", "Write a Python function to sort a list",
                      "anthropic", "weak-model")
    assert r.mode == SwitchMode.AUTO
    assert r.should_switch is True
    assert r.target() == ("anthropic", "strong-model")
    assert r.task_type == TaskType.CODING


def test_route_manual_needs_confirmation(service):
    service.reassess()
    service.ctx.config.data["routing"]["switch_mode"] = "manual"
    r = service.route("cowork", "Write a Python function", "anthropic", "weak-model")
    assert r.mode == SwitchMode.MANUAL
    assert r.needs_confirmation is True


def test_manual_confirm_flow_idempotent(service):
    service.reassess()
    service.ctx.config.data["routing"]["switch_mode"] = "manual"
    r = service.route("cowork", "Write a Python function", "anthropic", "weak-model")
    pending = service.create_pending(r.decision, {"prompt": "Write a Python function"})

    runs = {"n": 0}

    def run(model_key, switched):
        runs["n"] += 1
        return {"model_key": model_key, "switched": switched}

    out1 = service.resolve_pending(pending.request_id, approve=True, run=run)
    out2 = service.resolve_pending(pending.request_id, approve=True, run=run)
    assert out1["model_key"] == "anthropic/strong-model"
    assert out1["switched"] is True
    assert runs["n"] == 1        # idempotent — executed once
    assert out1 == out2


def test_route_never_raises_on_broken_store(ctx, tmp_path):
    # Point the store at a corrupt file; route must still return a safe result.
    store = AssessmentStore(store_path=tmp_path / "bad.json")
    store.store_path.write_text("{{ not json", encoding="utf-8")
    svc = RoutingService(ctx, store=store, client=FakeClient({}, {}))
    ctx.config.data["routing"]["switch_mode"] = "auto"
    r = svc.route("cowork", "hello", "anthropic", "strong-model")
    assert r.should_switch is False  # nothing assessed → nothing to switch to


def test_per_surface_mode_override(service):
    service.reassess()
    service.ctx.config.data["routing"]["switch_mode"] = "off"
    service.ctx.config.data["routing"]["surface_modes"]["co4e"] = "auto"
    # cowork follows global (off); co4e overridden to auto
    r_cowork = service.route("cowork", "Write a Python function", "anthropic", "weak-model")
    r_co4e = service.route("co4e", "Write a Python function", "anthropic", "weak-model")
    assert r_cowork.mode == SwitchMode.OFF
    assert r_co4e.mode == SwitchMode.AUTO
    assert r_co4e.should_switch is True


def test_add_candidate_appends_without_reassess(service):
    added = service.add_candidate("anthropic", "new-model", "fast", reassess=False)
    assert added is True
    cands = service.candidates()
    assert any(m == "new-model" for _, m, _ in cands)
    # Adding the same one again is a no-op.
    assert service.add_candidate("anthropic", "new-model", "fast", reassess=False) is False
