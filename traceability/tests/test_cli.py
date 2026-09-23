"""The commands themselves, end to end, with no network.

`cli.py` is the largest module in the project and had no tests at all — which
is where the worst bug so far lived: a `--uid` run overwrote `verdicts.json`
with its own slice and silently discarded the other 149 verdicts. Every test
below exists because the stage boundary it covers can lose or corrupt data,
not because the line needed covering.

Nothing here reaches the API. The paid stages are exercised through
`--dry-run` and through a pre-seeded cache, which is exactly how a developer
should be able to work on them.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adjudicate import SYSTEM, build_prompt, cache_key
from tracelink.artifacts import load_payload
from tracelink.cli import main


@pytest.fixture
def workspace(tmp_path: Path):
    """A tiny backlog and a tiny repo — enough for every stage to be real."""
    export = tmp_path / "backlog.csv"
    with export.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows([
            ["Key", "Summary", "Status", "Component/s", "Description"],
            ["A-1", "Toast notifications", "Done", "UI", "PO: alice"],
            ["A-2", "Billing invoices", "To Do", "Billing", "PO: bob"],
            ["A-3", "Nothing matches this", "To Do", "Other", ""],
        ])

    repo = tmp_path / "repo"
    (repo / "ui").mkdir(parents=True)
    (repo / "core").mkdir()
    (repo / "ui" / "toast.py").write_text(
        "class ToastBanner:\n    def show(self):\n        return 1\n",
        encoding="utf-8")
    (repo / "core" / "billing.py").write_text(
        "class InvoiceEngine:\n    def render(self):\n        return 2\n",
        encoding="utf-8")

    run = tmp_path / "run"
    return {"export": export, "repo": repo, "run": run, "tmp": tmp_path}


def cli(run: Path, *args: str) -> int:
    return main(["--run", str(run), *args])


def test_tickets_writes_both_artifacts(workspace):
    w = workspace
    assert cli(w["run"], "tickets", str(w["export"]), "--project-id", "p:1") == 0
    tickets = load_payload(w["run"] / "tickets.json", "tickets")
    assert [t["summary"] for t in tickets] == [
        "Toast notifications", "Billing invoices", "Nothing matches this"]
    manifest = load_payload(w["run"] / "run.json", "run")
    assert manifest["project_id"] == "p:1"
    assert manifest["tickets"] == 3


def test_tickets_recovers_inline_fields(workspace):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    tickets = load_payload(w["run"] / "tickets.json", "tickets")
    assert tickets[0]["inline"]["PO"] == "alice"


def test_diagnose_writes_its_report(workspace):
    w = workspace
    assert cli(w["run"], "diagnose", str(w["export"]), "--rows", "all") == 0
    d = load_payload(w["run"] / "diagnosis.json", "diagnosis")
    assert d["rows"] == 3
    assert any(f["topic"] == "no recorded dependencies" for f in d["findings"])


def test_corpus_then_retrieve_produces_candidates(workspace):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    assert cli(w["run"], "corpus", str(w["repo"])) == 0
    assert cli(w["run"], "retrieve") == 0

    rows = {r["uid"]: r for r in
            load_payload(w["run"] / "candidates.json", "candidates")}
    def strong(uid):
        return {c["path"] for c in rows[uid]["candidates"]
                if any(e["strong"] for e in c["evidence"])}
    assert "ui/toast.py" in strong("T0000")
    assert "core/billing.py" in strong("T0001")
    assert not strong("T0002"), "a ticket matching nothing must claim nothing"


def test_retrieve_is_repeatable(workspace):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    first = (w["run"] / "candidates.json").read_text(encoding="utf-8")
    cli(w["run"], "retrieve")
    assert (w["run"] / "candidates.json").read_text(encoding="utf-8") == first


# --------------------------------------------------------------------------
# Spending is opt-in and partial runs are safe
# --------------------------------------------------------------------------

def test_adjudicate_dry_run_spends_nothing_and_writes_nothing(workspace):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    assert cli(w["run"], "adjudicate", "--all", "--dry-run") == 0
    assert not (w["run"] / "verdicts.json").exists()


def _seed_verdict(run: Path, uid: str, summary: str, verdict: str) -> None:
    """Put a response in the cache so `adjudicate` runs without a network."""
    from tracelink.artifacts import rebuild_tickets, rebuild_candidates, rebuild_corpus
    from tracelink.describe import Bundle, RawSource

    tickets = {t.uid: t for t in
               rebuild_tickets(load_payload(run / "tickets.json", "tickets"))}
    corpus = rebuild_corpus(load_payload(run / "corpus.json", "corpus"))
    cands = {c.uid: c for c in
             rebuild_candidates(load_payload(run / "candidates.json", "candidates"))}
    t = tickets[uid]
    tc = cands[uid]
    descs = Bundle(RawSource()).describe_all(tc, corpus) if tc.candidates else []
    key = cache_key("claude-opus-5", SYSTEM, build_prompt(t, descs))
    cache = run / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{key}.json").write_text(json.dumps({
        "verdict": verdict, "confidence": "high", "reasoning": summary,
        "evidence": [], "status_conflict": False,
    }), encoding="utf-8")


def test_a_partial_run_merges_instead_of_replacing(workspace):
    """The data-loss bug: `--uid` used to overwrite the whole file."""
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    for uid, verdict in (("T0000", "corroborated"), ("T0001", "unverified"),
                         ("T0002", "unverified")):
        _seed_verdict(w["run"], uid, "seeded", verdict)

    assert cli(w["run"], "adjudicate", "--all") == 0
    assert len(load_payload(w["run"] / "verdicts.json", "verdicts")) == 3

    # Re-run one ticket only. The other two must survive.
    assert cli(w["run"], "adjudicate", "--uid", "T0001") == 0
    after = load_payload(w["run"] / "verdicts.json", "verdicts")
    assert len(after) == 3, "a one-ticket run must not delete the other verdicts"
    assert {v["uid"] for v in after} == {"T0000", "T0001", "T0002"}


def test_merged_verdicts_keep_ticket_order(workspace):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    for uid in ("T0000", "T0001", "T0002"):
        _seed_verdict(w["run"], uid, "seeded", "unverified")
    cli(w["run"], "adjudicate", "--all")
    cli(w["run"], "adjudicate", "--uid", "T0000")
    after = load_payload(w["run"] / "verdicts.json", "verdicts")
    assert [v["uid"] for v in after] == ["T0000", "T0001", "T0002"], \
        "order must follow the tickets, not the order they were adjudicated"


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------

def test_score_refuses_without_labels(workspace, capsys):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    assert cli(w["run"], "score") == 1
    assert "not a score" in capsys.readouterr().err


def test_shadow_without_verdicts_says_so_but_still_runs(workspace, capsys):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    assert cli(w["run"], "shadow") == 0
    out = capsys.readouterr().out
    assert "no verdicts.json" in out
    assert load_payload(w["run"] / "shadow.json", "shadow")["counts"]


def test_couple_refuses_without_verdicts(workspace):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    assert cli(w["run"], "couple") == 1


def test_translate_on_an_english_backlog_costs_nothing(workspace, capsys):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    assert cli(w["run"], "translate") == 0
    assert "already English" in capsys.readouterr().out
    assert not (w["run"] / "translations.json").exists()


def test_a_stage_reading_a_missing_upstream_artifact_fails_clearly(workspace):
    w = workspace
    with pytest.raises(FileNotFoundError):
        cli(w["run"], "retrieve")


# --------------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------------

def test_a_cached_rerun_does_not_erase_what_a_verdict_cost(workspace):
    """The ledger used to drift to zero the more the pipeline was re-run."""
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    for uid in ("T0000", "T0001", "T0002"):
        _seed_verdict(w["run"], uid, "seeded", "unverified")
    cli(w["run"], "adjudicate", "--all")

    # Pretend the first run had recorded a cost, as a real one would.
    from tracelink.artifacts import save
    rows = load_payload(w["run"] / "verdicts.json", "verdicts")
    for r in rows:
        r["cost_usd"] = 0.04
    save(w["run"] / "verdicts.json", "verdicts", rows)

    cli(w["run"], "adjudicate", "--all")      # entirely cache hits
    after = load_payload(w["run"] / "verdicts.json", "verdicts")
    assert all(r["cost_usd"] == 0.04 for r in after), \
        "a free re-run must not rewrite the cost of producing the verdict"


def test_cost_counts_external_inputs_and_remembers_them(workspace, capsys):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    assert cli(w["run"], "cost", "--docs-cost", "12.0",
               "--docs-note", "generated elsewhere") == 0
    # Declared once, remembered after.
    capsys.readouterr()
    assert cli(w["run"], "cost") == 0
    out = capsys.readouterr().out
    assert "12.0000" in out and "external" in out


def test_a_declared_figure_is_labelled_as_declared(workspace, capsys):
    w = workspace
    cli(w["run"], "tickets", str(w["export"]))
    cli(w["run"], "corpus", str(w["repo"]))
    cli(w["run"], "retrieve")
    for uid in ("T0000", "T0001", "T0002"):
        _seed_verdict(w["run"], uid, "seeded", "corroborated")
    cli(w["run"], "adjudicate", "--all")
    capsys.readouterr()
    cli(w["run"], "cost", "--declare", "verdicts=8.75")
    out = capsys.readouterr().out
    assert "8.7500" in out
    assert "declared, not measured" in out
