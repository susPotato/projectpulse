"""Orchestrator tests — fully mocked client + judge, no real API calls."""
from __future__ import annotations

import pytest

from cowork_local.core.routing.clients import CompletionResult
from cowork_local.core.routing.models import Policy, TaskType
from cowork_local.core.routing.orchestrator import build_assessment, check_and_update
from cowork_local.core.routing.prober import BENCHMARK_TASKS
from cowork_local.core.routing.store import AssessmentStore


class FakeClient:
    """Deterministic ProbeClient. Per-(provider,model) canned answers + a
    scripted judge score; counts calls so we can assert probe vs judge volume.
    """

    def __init__(self, answers, quality):
        # answers: {(provider, model_id): "text" or Exception/None(error)}
        # quality: {model_id: score} used when this client acts as the judge
        self.answers = answers
        self.quality = quality
        self.calls = []

    def complete(self, provider, model_id, messages) -> CompletionResult:
        self.calls.append((provider, model_id))
        # Judge calls carry the rubric (which contains "JSON object").
        text = messages[0]["content"]
        is_judge = "ONLY a JSON object" in text or "grading an AI assistant" in text
        if is_judge:
            # The rubric embeds the answer being graded; score by which model's
            # canned answer text appears in it.
            score = 0.0
            for mid, q in self.quality.items():
                if self.answers.get((_prov_of(self, mid), mid), "") and \
                        self.answers.get((_prov_of(self, mid), mid), "") in text:
                    score = q
            return CompletionResult(text=f'{{"score": {score}}}')
        # Normal completion.
        val = self.answers.get((provider, model_id))
        if val is None:
            return CompletionResult(error="model unavailable")
        return CompletionResult(text=val, tokens_out=len(val) // 4)


def _prov_of(client, model_id):
    for (prov, mid) in client.answers:
        if mid == model_id:
            return prov
    return ""


def _judge(scores):
    """A direct JudgeFn (bypasses the LLM judge) returning scripted scores by
    matching the answer text — simplest for deterministic tests."""
    def judge(task_type, prompt, answer):
        return scores.get(answer, 0.0)
    return judge


def test_build_assessment_scores_all_tasks():
    from cowork_local.core.routing.models import ProbeResult
    probes = {
        tt.value: ProbeResult(latency_ms=200, success=True, quality_score=0.8, tokens_out=30)
        for tt in TaskType
    }
    a = build_assessment("anthropic", "claude-x", "fast", probes, Policy.BALANCED)
    assert set(a.fit_scores) == {tt.value for tt in TaskType}
    assert all(0 < s <= 1 for s in a.fit_scores.values())
    assert a.metadata.tier == "fast"


def test_build_assessment_all_failed_marks_unavailable():
    from cowork_local.core.routing.models import ProbeResult
    probes = {
        tt.value: ProbeResult(latency_ms=0, success=False, error="down")
        for tt in TaskType
    }
    a = build_assessment("anthropic", "dead", None, probes, Policy.BALANCED)
    assert a.metadata.available is False
    assert all(s == 0.0 for s in a.fit_scores.values())


def test_check_and_update_persists_and_scores(tmp_path):
    candidates = [("anthropic", "good", "powerful"), ("anthropic", "weak", "fast")]
    client = FakeClient(
        answers={("anthropic", "good"): "GOOD-ANSWER", ("anthropic", "weak"): "weak-answer"},
        quality={},
    )
    store = AssessmentStore(store_path=tmp_path / "a.json", history_dir=tmp_path / "h")

    result = check_and_update(
        candidates, client,
        judge=_judge({"GOOD-ANSWER": 0.9, "weak-answer": 0.4}),
        store=store, policy=Policy.QUALITY,
    )
    assert set(result) == {"anthropic/good", "anthropic/weak"}
    # Persisted and reloadable.
    reloaded = store.load()
    assert set(reloaded) == {"anthropic/good", "anthropic/weak"}
    # "good" should out-score "weak" on every task under QUALITY.
    for tt in TaskType:
        assert result["anthropic/good"].fit_for(tt) > result["anthropic/weak"].fit_for(tt)


def test_check_and_update_handles_dead_model(tmp_path):
    candidates = [("anthropic", "alive", None), ("anthropic", "dead", None)]
    client = FakeClient(
        answers={("anthropic", "alive"): "hello", ("anthropic", "dead"): None},  # dead → error
        quality={},
    )
    store = AssessmentStore(store_path=tmp_path / "a.json")
    result = check_and_update(
        candidates, client,
        judge=_judge({"hello": 0.7}),
        store=store, policy=Policy.BALANCED,
    )
    assert result["anthropic/dead"].metadata.available is False
    assert all(s == 0.0 for s in result["anthropic/dead"].fit_scores.values())
    assert result["anthropic/alive"].metadata.available is True


def test_idempotent_reassess_is_stable(tmp_path):
    """Running twice with the same deterministic client gives the same scores
    and backs up the previous version (history has one entry after 2nd run)."""
    candidates = [("anthropic", "m", None)]
    client = FakeClient(answers={("anthropic", "m"): "answer"}, quality={})
    store = AssessmentStore(store_path=tmp_path / "a.json", history_dir=tmp_path / "h")
    judge = _judge({"answer": 0.6})

    r1 = check_and_update(candidates, client, judge=judge, store=store, policy=Policy.BALANCED)
    r2 = check_and_update(candidates, client, judge=judge, store=store, policy=Policy.BALANCED)

    # Scores are STABLE across runs to within live-latency jitter — quality and
    # cost are deterministic; only the measured latency term moves by µs, which
    # is orders of magnitude below the routing min_score_gain (~0.05). Assert
    # approximate, not exact, equality (exact would test the wall clock, not us).
    s1, s2 = r1["anthropic/m"].fit_scores, r2["anthropic/m"].fit_scores
    assert set(s1) == set(s2)
    for tt in s1:
        assert s1[tt] == pytest.approx(s2[tt], abs=1e-3)
    assert len(store.history_files()) == 1  # first run backed up before second


def test_empty_candidates_returns_empty(tmp_path):
    store = AssessmentStore(store_path=tmp_path / "a.json")
    assert check_and_update([], FakeClient({}, {}), judge=_judge({}), store=store) == {}


def test_dry_run_does_not_persist(tmp_path):
    candidates = [("anthropic", "m", None)]
    client = FakeClient(answers={("anthropic", "m"): "answer"}, quality={})
    store = AssessmentStore(store_path=tmp_path / "a.json")
    check_and_update(candidates, client, judge=_judge({"answer": 0.6}),
                     store=store, persist=False)
    assert store.load() == {}  # nothing written


def test_probe_uses_all_benchmark_tasks(tmp_path):
    """Every TaskType is probed → one probe per (candidate, task)."""
    candidates = [("anthropic", "m", None)]
    client = FakeClient(answers={("anthropic", "m"): "answer"}, quality={})
    store = AssessmentStore(store_path=tmp_path / "a.json")
    result = check_and_update(candidates, client, judge=_judge({"answer": 0.5}),
                              store=store)
    assert set(result["anthropic/m"].probes) == {tt.value for tt in TaskType}
    assert len(BENCHMARK_TASKS) == len(TaskType)
