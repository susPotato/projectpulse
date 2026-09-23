"""Stage 2 and 3 contracts.

No network. The adjudicator is exercised through its cache and a fake
client, because the thing worth pinning is the prompt assembly, the cache
key and the accounting — not the model's opinion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adjudicate import (
    Adjudicator, PRICING, Usage, build_prompt, cache_key,
)
from tracelink.artifacts import (
    Candidate, Corpus, Description, Evidence, SourceFile, Symbol, Ticket,
    TicketCandidates, Verdict, VerdictEvidence, ROLE_CODE,
)
from tracelink.describe import Bundle, ExistingDocs, RawSource, _best_window
from tracelink.report import build as build_report


def _ev(anchor="chat"):
    return [Evidence("stem", anchor, 1, 2, True)]


# --------------------------------------------------------------------------
# Stage 2 — windowing is the whole point
# --------------------------------------------------------------------------

def test_window_finds_the_anchor_deep_in_a_file():
    """The demo showed lines[:220]; a symbol at line 400 was never seen."""
    lines = ["filler"] * 400 + ["def handle_toast():"] + ["filler"] * 400
    lo, hi = _best_window(lines, {"toast"}, size=50)
    assert lo <= 400 < hi


def test_window_returns_the_head_when_there_are_no_anchors():
    lines = ["a"] * 500
    assert _best_window(lines, set(), size=50) == (0, 50)


def test_window_returns_everything_for_a_short_file():
    lines = ["a"] * 10
    assert _best_window(lines, {"x"}, size=50) == (0, 10)


def _corpus(tmp_path: Path) -> Corpus:
    (tmp_path / "ui").mkdir(parents=True, exist_ok=True)
    body = ["# header"] * 30 + ["filler"] * 300 + ["class ToastBanner:  # target"] \
        + ["filler"] * 100
    (tmp_path / "ui" / "toast.py").write_text("\n".join(body), encoding="utf-8")
    return Corpus(
        files=[SourceFile("ui/toast.py", ROLE_CODE, 100, "python")],
        symbols=[Symbol("ui/toast.py::ToastBanner", "ui/toast.py", "ToastBanner", "class")],
        root=str(tmp_path), analyzer="test")


def test_raw_source_quotes_the_matched_region(tmp_path):
    corpus = _corpus(tmp_path)
    tc = TicketCandidates("t1", [Candidate("ui/toast.py", _ev("toast"))])
    d = RawSource(window=60).describe(tc, corpus)
    assert "ToastBanner" in d.text
    assert "lines omitted" in d.text, "elision must be visible, not silent"
    assert d.source == "raw-source"
    assert "ui/toast.py::ToastBanner" in d.anchors


def test_raw_source_names_symbols_in_the_header(tmp_path):
    corpus = _corpus(tmp_path)
    tc = TicketCandidates("t1", [Candidate("ui/toast.py", _ev("toast"))])
    d = RawSource().describe(tc, corpus)
    assert "1 symbols" in d.text or "ToastBanner" in d.text


def test_raw_source_survives_a_missing_file(tmp_path):
    corpus = Corpus(files=[], symbols=[], root=str(tmp_path), analyzer="test")
    tc = TicketCandidates("t1", [Candidate("gone.py", _ev())])
    d = RawSource().describe(tc, corpus)
    assert d.paths == []
    assert "no candidate files" in d.text


def test_existing_docs_returns_empty_when_nothing_covers_the_files(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("This document is about billing.py only.", encoding="utf-8")
    corpus = _corpus(tmp_path)
    tc = TicketCandidates("t1", [Candidate("ui/toast.py", _ev())])
    d = ExistingDocs(docs).describe(tc, corpus)
    assert d.text == "", "absence of coverage must not be disguised"


def test_existing_docs_excerpts_a_mentioning_doc(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(
        "intro\n" * 5 + "The toast.py module renders banners.\n" + "tail\n" * 5,
        encoding="utf-8")
    corpus = _corpus(tmp_path)
    tc = TicketCandidates("t1", [Candidate("ui/toast.py", _ev())])
    d = ExistingDocs(docs).describe(tc, corpus)
    assert "renders banners" in d.text


def test_bundle_drops_describers_that_produced_nothing(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    corpus = _corpus(tmp_path)
    tc = TicketCandidates("t1", [Candidate("ui/toast.py", _ev("toast"))])
    out = Bundle(ExistingDocs(docs), RawSource()).describe_all(tc, corpus)
    assert [d.source for d in out] == ["raw-source"]


# --------------------------------------------------------------------------
# Stage 3 — prompt, cache, accounting
# --------------------------------------------------------------------------

def test_prompt_carries_status_and_marks_retrieval_as_fallible():
    t = Ticket(uid="t1", summary="Toasts", description="d", status="To Do")
    p = build_prompt(t, [Description(["ui/toast.py"], "code here", "raw-source")])
    assert "**Status: To Do**" in p
    assert "may be wrong" in p


def test_prompt_says_so_when_retrieval_found_nothing():
    t = Ticket(uid="t1", summary="Toasts", status="Release it")
    p = build_prompt(t, [])
    assert "proposed nothing" in p


def test_cache_key_changes_with_content_model_and_system():
    a = cache_key("claude-opus-5", "sys", "one")
    assert a != cache_key("claude-opus-5", "sys", "two")
    assert a != cache_key("claude-sonnet-5", "sys", "one")
    assert a != cache_key("claude-opus-5", "other sys", "one"), \
        "editing the system prompt must invalidate the cache"
    assert a == cache_key("claude-opus-5", "sys", "one")


def test_cache_hit_costs_nothing_and_makes_no_call(tmp_path):
    from tracelink.adjudicate import SYSTEM

    adj = Adjudicator(model="claude-opus-5", cache_dir=tmp_path)
    t = Ticket(uid="t1", summary="Toasts", status="To Do")
    prompt = build_prompt(t, [])
    adj._store(cache_key(adj.model, SYSTEM, prompt), {
        "verdict": "corroborated", "confidence": "high",
        "reasoning": "seen it", "evidence": [{"file": "ui/toast.py", "why": "it is there"}],
        "status_conflict": True,
    })

    def explode(*a, **k):
        raise AssertionError("cache hit must not reach the API")

    adj._client = type("C", (), {"messages": type("M", (), {"parse": explode})()})()
    verdict, usage = adj.adjudicate(t, [])
    assert verdict.verdict == "corroborated"
    assert verdict.status_conflict is True
    assert usage.api_calls == 0 and usage.cached_calls == 1
    assert usage.cost(adj.model) == 0.0


def test_corrupt_cache_entry_is_ignored_not_fatal(tmp_path):
    adj = Adjudicator(cache_dir=tmp_path)
    (tmp_path / "abc.json").write_text("{not json", encoding="utf-8")
    assert adj._cached("abc") is None


def test_usage_cost_uses_the_model_price():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    pin, pout = PRICING["claude-opus-5"]
    assert u.cost("claude-opus-5") == pytest.approx(pin + pout)
    assert u.cost("claude-sonnet-5") < u.cost("claude-opus-5")


def test_unknown_model_prices_at_zero_rather_than_guessing():
    assert Usage(input_tokens=10**6).cost("some-future-model") == 0.0


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _v(uid, verdict="unverified", conflict=False, conf="low"):
    return Verdict(uid=uid, verdict=verdict, confidence=conf,
                   reasoning="because", status_conflict=conflict,
                   evidence=[VerdictEvidence("a.py", "found", "Sym")])


def test_report_leads_with_status_conflicts():
    tickets = [Ticket(uid="t1", summary="A", status="To Do"),
               Ticket(uid="t2", summary="B", status="Release it")]
    r = build_report(tickets, [_v("t1", "corroborated", conflict=True, conf="high"),
                               _v("t2")])
    assert len(r.status_conflicts) == 1
    text = r.render()
    assert text.index("STATUS CONFLICT") < text.index("VERDICT MIX")


def test_report_ranks_conflicts_by_confidence():
    tickets = [Ticket(uid=f"t{i}", summary=str(i)) for i in range(3)]
    r = build_report(tickets, [
        _v("t0", "corroborated", conflict=True, conf="low"),
        _v("t1", "corroborated", conflict=True, conf="high"),
        _v("t2", "corroborated", conflict=True, conf="medium"),
    ])
    assert [v.confidence for _, v in r.status_conflicts] == ["high", "medium", "low"]


def test_report_says_no_conflicts_rather_than_printing_nothing():
    r = build_report([Ticket(uid="t1", summary="A")], [_v("t1")])
    assert "none found" in r.render()


def test_report_keeps_the_corroborated_caveat():
    r = build_report([Ticket(uid="t1", summary="A")], [_v("t1", "corroborated")])
    assert "does not mean the feature works" in r.render()


def test_a_run_where_everything_failed_is_not_silent(tmp_path):
    """A total failure previously reported `0 api calls, $0.0000` — the same
    shape as a perfect cache run. The counter is what tells them apart."""
    from tracelink.adjudicate import Adjudicator, run as run_adj
    from tracelink.describe import Bundle, RawSource

    adj = Adjudicator(cache_dir=tmp_path)

    def explode(*a, **k):
        raise RuntimeError("credit balance is too low")

    adj._client = type("C", (), {"messages": type("M", (), {"parse": explode})()})()
    corpus = _corpus(tmp_path)
    tc = TicketCandidates("t1", [Candidate("ui/toast.py", _ev("toast"))])
    verdicts, usage = run_adj([Ticket(uid="t1", summary="Toasts")],
                              {"t1": tc}, corpus, Bundle(RawSource()), adj,
                              workers=1)
    assert verdicts == []
    assert usage.failures == 1
    assert "credit balance" in usage.first_error
