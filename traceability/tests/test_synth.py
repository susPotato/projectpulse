"""The synthetic corpus, and the eval it makes possible.

Hand labels remain the only evidence about *real* data. These exist so the
matcher can be scored at all — and so a regression in it fails a test
instead of waiting for somebody to notice a number moved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.artifacts import load_payload
from tracelink.cli import main
from tracelink.config import PipelineConfig
from tracelink.index import split_identifier, tokenize_query, tokenize_text
from tracelink.label import load_labels
from tracelink.synth import build_plan, write


def test_the_plan_is_deterministic():
    a, b = build_plan(seed=3), build_plan(seed=3)
    assert [c.summary for c in a.cases] == [c.summary for c in b.cases]
    assert build_plan(seed=4).cases[0].summary != a.cases[0].summary


def test_every_difficulty_tier_is_present():
    tiers = {c.difficulty for c in build_plan().cases}
    assert tiers == {"named", "symbol", "prose", "shared", "foreign", "decoy"}


def test_decoys_have_no_truth_and_others_do():
    for c in build_plan().cases:
        if c.difficulty == "decoy":
            assert c.truth == [], "a decoy's right answer is nothing"
        else:
            assert c.truth, f"{c.uid_hint} must name the file that implements it"


def test_labels_line_up_with_reader_uids(tmp_path):
    """Ground truth is keyed on the uid the reader will assign, in row order.
    An off-by-one here would score every ticket against its neighbour."""
    plan = build_plan()
    out = write(plan, tmp_path)
    run = tmp_path / "run"
    assert main(["--run", str(run), "tickets", str(out["export"])]) == 0
    tickets = load_payload(run / "tickets.json", "tickets")
    labels = load_payload(out["labels"], "label")
    assert len(tickets) == len(labels)
    for t, l in zip(tickets, labels):
        assert t["uid"] == l["uid"]


# --------------------------------------------------------------------------
# The bug the synthetic data found
# --------------------------------------------------------------------------

def test_a_camelcase_name_in_a_ticket_splits_like_the_index_does():
    """`InvoiceEngine` lowercases to one token that matches nothing, while
    the index stores the class as {invoice, engine}. 0 of 12 such tickets
    were retrieved before this."""
    stop = frozenset()
    assert split_identifier("InvoiceEngine", stop, 4) == ["invoice", "engine"]
    assert "invoiceengine" in tokenize_text("InvoiceEngine lifecycle", stop, 4)
    got = tokenize_query("InvoiceEngine lifecycle", stop, 4)
    assert {"invoice", "engine"} <= got
    assert "invoiceengine" in got, "the whole word must still match a literal name"


def test_snake_case_also_splits():
    got = tokenize_query("fix task_scheduler retries", frozenset(), 4)
    assert {"task", "scheduler"} <= got


def test_ordinary_words_are_not_mangled():
    stop = frozenset()
    assert tokenize_query("improve the billing report", stop, 4) == \
           tokenize_text("improve the billing report", stop, 4)


# --------------------------------------------------------------------------
# End to end: the score must not regress
# --------------------------------------------------------------------------

def _run_pipeline(tmp_path: Path):
    plan = build_plan()
    out = write(plan, tmp_path)
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "labels.json").write_text(out["labels"].read_text(encoding="utf-8"),
                                     encoding="utf-8")
    for args in (["tickets", str(out["export"])],
                 ["corpus", str(out["repo"])],
                 ["retrieve"]):
        assert main(["--run", str(run), *args]) == 0
    return run, plan


def test_retrieval_scores_above_the_recorded_baseline(tmp_path):
    from tracelink.artifacts import rebuild_candidates
    from tracelink.evaluate import score

    run, _ = _run_pipeline(tmp_path)
    results = {r.uid: r for r in
               rebuild_candidates(load_payload(run / "candidates.json", "candidates"))}
    s = score(load_labels(run / "labels.json"), results)
    # Recorded when the CamelCase fix landed: P .833 R .714 F1 .769.
    # A floor, not a target — raise it when something genuinely improves.
    assert s.precision >= 0.80, f"precision regressed to {s.precision}"
    assert s.recall >= 0.68, f"recall regressed to {s.recall}"
    assert s.f1 >= 0.74, f"F1 regressed to {s.f1}"


def test_the_matcher_never_answers_a_decoy(tmp_path):
    """The one failure that would be worse than missing: confident nonsense."""
    from tracelink.artifacts import rebuild_candidates

    run, plan = _run_pipeline(tmp_path)
    results = {r.uid: r for r in
               rebuild_candidates(load_payload(run / "candidates.json", "candidates"))}
    labels = load_labels(run / "labels.json")
    for uid, lab in labels.items():
        if lab.note == "decoy":
            assert not results[uid].strong_paths(), \
                f"{uid} is a decoy; proposing files for it is confabulation"


def test_hub_file_is_not_proposed_for_everything(tmp_path):
    from tracelink.artifacts import rebuild_candidates

    run, plan = _run_pipeline(tmp_path)
    results = rebuild_candidates(load_payload(run / "candidates.json", "candidates"))
    hits = sum(1 for r in results if plan.hub_path in r.strong_paths())
    assert hits <= len(results) * 0.2, \
        f"{plan.hub_path} was proposed for {hits} of {len(results)} tickets"
