"""Tests for the assessment store: round-trip, atomic write, history backup."""
from __future__ import annotations

import json

import pytest

from cowork_local.core.routing.models import (
    ModelAssessment,
    ModelMetadata,
    Policy,
    ProbeResult,
    TaskType,
)
from cowork_local.core.routing.store import AssessmentStore


def _assessment(provider="anthropic", model_id="claude-x", quality=0.8) -> ModelAssessment:
    meta = ModelMetadata(
        provider=provider,
        model_id=model_id,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.003,
        max_context=200000,
        capabilities={"tools"},
    )
    probe = ProbeResult(latency_ms=400, success=True, quality_score=quality, tokens_out=50)
    return ModelAssessment(
        metadata=meta,
        probes={TaskType.CODING.value: probe},
        fit_scores={TaskType.CODING.value: 0.75},
        assessed_at="2026-07-22T00:00:00+00:00",
    )


@pytest.fixture()
def store(tmp_path):
    return AssessmentStore(
        store_path=tmp_path / "assessments.json",
        history_dir=tmp_path / "history",
    )


def test_load_missing_returns_empty(store):
    assert store.load() == {}
    assert store.last_updated() is None


def test_save_then_load_roundtrip(store):
    a = _assessment()
    store.save({a.key: a}, Policy.BALANCED)

    loaded = store.load()
    assert set(loaded) == {a.key}
    got = loaded[a.key]
    assert got.metadata.model_id == "claude-x"
    assert got.metadata.capabilities == {"tools"}
    assert got.fit_scores[TaskType.CODING.value] == 0.75
    assert got.probes[TaskType.CODING.value].quality_score == 0.8


def test_save_writes_last_updated_and_policy(store):
    a = _assessment()
    store.save({a.key: a}, Policy.QUALITY, last_updated="2026-07-22T09:00:00+00:00")
    assert store.last_updated() == "2026-07-22T09:00:00+00:00"
    assert store.policy() == "quality"


def test_overwrite_backs_up_previous_to_history(store):
    first = _assessment(quality=0.5)
    store.save({first.key: first}, Policy.BALANCED)
    assert store.history_files() == []  # nothing existed before the first write

    second = _assessment(quality=0.9)
    store.save({second.key: second}, Policy.BALANCED)

    history = store.history_files()
    assert len(history) == 1  # the first write got backed up before the second
    backed_up = json.loads(history[0].read_text(encoding="utf-8"))
    key = first.key
    assert backed_up["results"][key]["probes"][TaskType.CODING.value]["quality_score"] == 0.5

    # Live store now holds the second (degraded/improved) version.
    assert store.load()[second.key].probes[TaskType.CODING.value].quality_score == 0.9


def test_corrupt_store_loads_as_empty(store):
    store.store_path.parent.mkdir(parents=True, exist_ok=True)
    store.store_path.write_text("{ this is not valid json ", encoding="utf-8")
    assert store.load() == {}  # corrupt file must not crash


def test_atomic_write_leaves_no_temp_files(store):
    a = _assessment()
    store.save({a.key: a}, Policy.BALANCED)
    leftovers = list(store.store_path.parent.glob(".assessments-*.tmp"))
    assert leftovers == []


def test_one_bad_entry_does_not_hide_good_ones(store):
    a = _assessment(model_id="good")
    store.save({a.key: a}, Policy.BALANCED)
    # Inject a malformed sibling entry directly into the JSON.
    raw = json.loads(store.store_path.read_text(encoding="utf-8"))
    raw["results"]["anthropic/bad"] = {"metadata": {"oops": True}}  # missing required fields
    store.store_path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = store.load()
    assert a.key in loaded
    assert "anthropic/bad" not in loaded


def test_prune_history_keeps_newest(store):
    a = _assessment()
    # 5 overwrites → 4 history snapshots.
    for i in range(5):
        store.save({a.key: a}, Policy.BALANCED)
    assert len(store.history_files()) == 4
    store.prune_history(keep=2)
    assert len(store.history_files()) == 2
