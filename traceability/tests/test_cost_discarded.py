"""Spend on a run that was thrown away.

A ledger built only from surviving artifacts understates what left the
account, because a failed or superseded run still billed for what it did.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracelink import artifacts as A
from tracelink import cost as CO


def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    A.save(run / "explain.json", "explain",
           [{"component": "UI", "tickets": 2, "cost_usd": 0.30}])
    return run


def test_a_discarded_run_is_counted_in_the_total(tmp_path):
    run = _run(tmp_path)
    plain = CO.build(run)
    led = CO.build(run, discarded={"explain": 0.2162})
    assert led.wasted == 0.2162
    assert round(led.total - plain.total, 4) == 0.2162


def test_a_discarded_run_does_not_inflate_the_stage(tmp_path):
    """It bought nothing, so per-unit figures must not improve."""
    led = CO.build(_run(tmp_path), discarded={"explain": 0.2162})
    explain = [s for s in led.stages if s.stage == "explain"][0]
    assert explain.usd == 0.30


def test_discarded_spend_is_remembered_across_runs(tmp_path):
    run = _run(tmp_path)
    CO.build(run, discarded={"explain": 0.2162})
    assert CO.build(run).wasted == 0.2162


def test_discarded_spend_is_rendered_as_its_own_line(tmp_path):
    text = CO.render(CO.build(_run(tmp_path), discarded={"explain": 0.2162}))
    assert "discarded" in text
    assert "bought nothing that survives" in text


def test_a_ledger_with_nothing_discarded_is_unchanged(tmp_path):
    led = CO.build(_run(tmp_path))
    assert led.wasted == 0.0
    assert "discarded" not in CO.render(led)


def test_zero_is_not_recorded_as_a_discard(tmp_path):
    led = CO.build(_run(tmp_path), discarded={"explain": 0.0})
    assert led.discarded == {}


def test_the_artifact_records_the_discarded_spend(tmp_path):
    run = _run(tmp_path)
    led = CO.build(run, discarded={"explain": 0.2162})
    assert led.to_dict()["discarded_usd"] == {"explain": 0.2162}
    stored = json.loads((run / "costs.json").read_text(encoding="utf-8"))
    assert stored["payload"]["discarded"] == {"explain": 0.2162}
