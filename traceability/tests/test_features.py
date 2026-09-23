"""Contract tests for Stage 0c — documentation as a checked feature map.

Pins the generalisations, not this project's numbers. The one thing these
tests exist above all to protect is the rule that a document measured as
not describing the corpus can never be presented as evidence that
something is implemented: that is the failure mode the stage was built to
prevent, and it is silent when it happens.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adapters.featuremap_markdown import (
    normalise_claim, parse_document, read_docs,
)
from tracelink.artifacts import (
    Corpus, Description, SourceFile, Symbol, Ticket, TicketCandidates,
    MODALITY_GROUNDED, MODALITY_PROCESS, MODALITY_UNGROUNDED,
    ROLE_CODE, rebuild_features,
)
from tracelink.config import PipelineConfig
from tracelink.describe import DocFeature, ExistingDocs
from tracelink.features import build, classify, drift, resolve, skew, summary
from tracelink.index import add_features, build as build_index
from tracelink.retrieve import retrieve


# --------------------------------------------------------------------------
# Fixtures: a tiny repository and two documents about it, one of which is
# about a version of it that does not exist.
# --------------------------------------------------------------------------

TRUTHFUL = """\
# Widget Service

## Billing

| # | Function | Notes |
|---|----------|-------|
| 1.1 | `charge_card()` | Take payment for an order |
| 1.2 | `refund()` | Give it back |

## Reporting

- `build_report()` renders the monthly figures
"""

PLAN = """\
# Target Architecture

## Billing, after the split

| # | Symbol | Moves to |
|---|--------|----------|
| 1 | `charge_card` | `presentation/billing/card_widget.py` |
| 2 | `refund` | `presentation/billing/refund_widget.py` |
| 3 | `settle_batch` | `application/billing/settlement.py` |
"""

POLICY = """\
# Review Policy

Two approvals before merge. Nobody reviews their own change.
"""


@pytest.fixture
def repo(tmp_path: Path) -> Corpus:
    return Corpus(
        root=str(tmp_path),
        files=[SourceFile(path="billing.py", role=ROLE_CODE, size=100,
                          language="python"),
               SourceFile(path="reporting.py", role=ROLE_CODE, size=100,
                          language="python")],
        symbols=[
            Symbol(sid="billing.py::charge_card", path="billing.py",
                   name="charge_card", kind="function"),
            Symbol(sid="billing.py::refund", path="billing.py",
                   name="refund", kind="function"),
            Symbol(sid="reporting.py::build_report", path="reporting.py",
                   name="build_report", kind="function"),
        ],
    )


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    (d / "architecture").mkdir(parents=True)
    (d / "guide.md").write_text(TRUTHFUL, encoding="utf-8")
    (d / "architecture" / "target.md").write_text(PLAN, encoding="utf-8")
    (d / "architecture" / "policy.md").write_text(POLICY, encoding="utf-8")
    return d


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def test_docs_are_read_recursively(docs: Path):
    """One level of globbing hid every ADR and every refactor report."""
    fmap = read_docs(docs)
    found = {f.doc for f in fmap.features}
    assert "guide.md" in found
    assert "architecture/target.md" in found, "nested documents must be read"


def test_table_row_becomes_a_feature_with_its_heading_path(docs: Path):
    fmap = read_docs(docs)
    row = next(f for f in fmap.features if f.doc == "guide.md"
               and any(c.name == "charge_card" for c in f.claims))
    assert "Billing" in row.heading
    assert row.line > 0, "a row must be locatable in the document again"


def test_bullet_lists_yield_features_too(docs: Path):
    fmap = read_docs(docs)
    assert any(c.name == "build_report"
               for f in fmap.features for c in f.claims)


@pytest.mark.parametrize("span,expected", [
    ("charge_card()", ("charge_card", "symbol")),
    ("_ai_analyze()", ("_ai_analyze", "symbol")),
    ("DashboardTab._apply_budget", ("DashboardTab._apply_budget", "symbol")),
    ("ui/chat_panel.py", ("ui/chat_panel.py", "path")),
    ("chat_panel.py", ("chat_panel.py", "path")),
])
def test_identifier_shaped_spans_are_claims(span, expected):
    assert normalise_claim(span) == expected


@pytest.mark.parametrize("span", ["week", "cost", "the total", "OK", "x"])
def test_prose_in_backticks_is_not_a_claim(span):
    """A loose filter would inflate the drift count with English words."""
    assert normalise_claim(span) is None


def test_fenced_code_is_not_parsed_as_features():
    doc = "# T\n\n```python\n| a | `b()` |\n```\n\n- real `thing_one()`\n"
    feats = parse_document(doc, "t.md")
    names = {c.name for f in feats for c in f.claims}
    assert names == {"thing_one"}


# --------------------------------------------------------------------------
# Resolution and modality
# --------------------------------------------------------------------------

def test_claims_resolve_to_real_symbols(repo, docs):
    fmap = build(docs, repo)
    charge = next(c for f in fmap.features for c in f.claims
                  if c.name == "charge_card" and f.doc == "guide.md")
    assert charge.resolved
    assert charge.paths == ["billing.py"]


def test_document_describing_the_code_is_grounded(repo, docs):
    fmap = build(docs, repo)
    stat = next(d for d in fmap.docs if d.doc == "guide.md")
    assert stat.modality == MODALITY_GROUNDED
    assert stat.signals, "a classification must carry its own evidence"


def test_plan_naming_absent_files_is_ungrounded_even_when_symbols_resolve(repo, docs):
    """The case a single averaged rate gets wrong.

    The plan names `charge_card` and `refund`, which both exist — a naive
    resolution rate makes it look like a description of the build. What it
    actually says is where those two should *move*, to files that do not
    exist. Averaged, it reads as grounded and becomes an argument that a
    refactor has already happened.
    """
    fmap = build(docs, repo)
    stat = next(d for d in fmap.docs if d.doc == "architecture/target.md")
    assert stat.modality == MODALITY_UNGROUNDED
    assert any("presentation/" in s for s in stat.signals)


def test_document_about_process_is_neither(repo, docs):
    fmap = build(docs, repo)
    stat = next(d for d in fmap.docs if d.doc == "architecture/policy.md")
    assert stat.modality == MODALITY_PROCESS


def test_modality_is_never_decided_by_filename(repo, docs, tmp_path):
    """Rename the plan to something reassuring; it stays ungrounded."""
    src = docs / "architecture" / "target.md"
    src.rename(docs / "architecture" / "current-implementation.md")
    fmap = build(docs, repo)
    stat = next(d for d in fmap.docs
                if d.doc.endswith("current-implementation.md"))
    assert stat.modality == MODALITY_UNGROUNDED


def test_near_match_distinguishes_a_rename_from_an_absence(repo, docs):
    (docs / "renamed.md").write_text(
        "# R\n\n- `charge_cards()` takes payment\n- `nothing_like_this()`\n",
        encoding="utf-8")
    fmap = build(docs, repo)
    claims = {c.name: c for f in fmap.features if f.doc == "renamed.md"
              for c in f.claims}
    assert claims["charge_cards"].near.endswith("charge_card")
    assert claims["nothing_like_this"].near == ""


def test_doc_to_doc_references_are_not_missing_code(repo, docs):
    (docs / "xref.md").write_text(
        "# X\n\nSee `guide.md` and `architecture/policy.md`.\n", encoding="utf-8")
    fmap = build(docs, repo)
    names = {c.name for r in drift(fmap, (MODALITY_GROUNDED, MODALITY_UNGROUNDED))
             for c in [] } or {r["name"] for r in
                               drift(fmap, (MODALITY_GROUNDED, MODALITY_UNGROUNDED))}
    assert "guide.md" not in names


# --------------------------------------------------------------------------
# Drift
# --------------------------------------------------------------------------

def test_drift_reports_documented_code_that_does_not_exist(repo, docs):
    (docs / "guide.md").write_text(
        TRUTHFUL + "\n- `reconcile_ledger()` closes the month\n", encoding="utf-8")
    fmap = build(docs, repo)
    names = {r["name"] for r in drift(fmap)}
    assert "reconcile_ledger" in names


def test_drift_ignores_designs_by_default(repo, docs):
    """An unresolved claim in a plan is the plan, not a defect."""
    fmap = build(docs, repo)
    names = {r["name"] for r in drift(fmap)}
    assert "settle_batch" not in names
    assert "settle_batch" in {r["name"] for r in
                              drift(fmap, (MODALITY_GROUNDED, MODALITY_UNGROUNDED))}


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

def _linked(repo, docs, summary_text):
    cfg = PipelineConfig()
    fmap = build(docs, repo)
    idx = add_features(build_index(repo, cfg), fmap)
    t = Ticket(uid="T1", summary=summary_text)
    return retrieve([t], idx, cfg, fmap=fmap)[0], fmap


def test_doc_matcher_reaches_code_through_the_documented_feature(repo, docs):
    tc, _ = _linked(repo, docs, "Billing — refund a customer order")
    doc_ev = [e for c in tc.candidates for e in c.evidence if e.matcher == "doc"]
    assert doc_ev, "a ticket matching a documented feature must produce doc evidence"
    assert all(e.feature for e in doc_ev), "doc evidence must name its feature"


def test_doc_evidence_is_strong_only_when_the_claim_resolved(repo, docs):
    tc, fmap = _linked(repo, docs, "Billing settle batch after the split")
    for c in tc.candidates:
        for e in c.evidence:
            if e.matcher == "doc" and e.strong:
                assert c.path in {f.path for f in repo.files}


def test_retrieval_without_a_feature_map_is_unchanged(repo, docs):
    cfg = PipelineConfig()
    t = Ticket(uid="T1", summary="Billing — refund a customer order")
    plain = retrieve([t], build_index(repo, cfg), cfg)[0]
    assert all(e.matcher != "doc" for c in plain.candidates for e in c.evidence)


# --------------------------------------------------------------------------
# What reaches the model
# --------------------------------------------------------------------------

def test_describer_shows_the_lookup_result_not_the_claim(repo, docs):
    tc, fmap = _linked(repo, docs, "Billing — refund a customer order")
    text = DocFeature(fmap).describe(tc, repo).text
    assert "found:" in text
    assert "billing.py::refund" in text


def test_ungrounded_documents_arrive_labelled(repo, docs):
    """The whole point of the stage, in one assertion.

    If this ever stops holding, a refactor plan is being shown to the
    adjudicator as a description of the shipped system, and every verdict
    it touches is corroborated for the wrong reason.
    """
    tc, fmap = _linked(repo, docs, "Billing settle batch presentation split")
    desc = DocFeature(fmap).describe(tc, repo)
    if "target.md" in desc.text:
        assert "does NOT describe the repository" in desc.text


def test_describer_is_silent_when_no_feature_matched(repo, docs):
    fmap = build(docs, repo)
    empty = TicketCandidates(uid="T9", candidates=[])
    assert DocFeature(fmap).describe(empty, repo).text == ""


def test_resolved_path_claims_are_shown(repo, docs):
    """They have no symbol ids; reading only those printed an empty list."""
    (docs / "files.md").write_text(
        "# F\n\n- payment lives in `billing.py`\n- and `reporting.py`\n",
        encoding="utf-8")
    tc, fmap = _linked(repo, docs, "payment billing reporting files")
    text = DocFeature(fmap).describe(tc, repo).text
    assert "files.md" in text, "a matched path-only row must reach the describer"
    assert "-> found: `" in text, "a resolved path claim must print its file"


def test_existing_docs_can_withhold_documents(repo, docs):
    d = ExistingDocs(docs, skip_docs={"architecture/target.md"})
    assert "architecture/target.md" not in d._load()
    assert "guide.md" in d._load()


# --------------------------------------------------------------------------
# Round-trip
# --------------------------------------------------------------------------

def test_feature_map_survives_save_and_reload(repo, docs, tmp_path):
    from tracelink.artifacts import load_payload, save

    fmap = build(docs, repo)
    p = save(tmp_path / "features.json", "features",
             {"root": fmap.root, "features": fmap.features, "docs": fmap.docs})
    back = rebuild_features(load_payload(p, "features"))
    assert len(back.features) == len(fmap.features)
    assert {d.modality for d in back.docs} == {d.modality for d in fmap.docs}
    original = next(c for f in fmap.features for c in f.claims
                    if c.name == "charge_card")
    restored = next(c for f in back.features for c in f.claims
                    if c.name == "charge_card")
    assert restored.paths == original.paths


def test_summary_and_skew_are_reportable(repo, docs):
    fmap = build(docs, repo)
    s = summary(fmap)
    assert s["features"] > 0 and s["claims"] >= s["claims_resolved"]
    assert all("near_fraction" in r for r in skew(fmap))
