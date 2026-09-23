"""Contract tests for the structured half of the docs tree.

A team generates as much documentation as it writes. These read the
generated kind — a control inventory, a screenshot manifest, a symbol
table — with the same claim test the markdown adapter uses, so a path is a
path and an identifier is an identifier by one rule, not two.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adapters.featuremap_json import parse_document, read_json_docs
from tracelink.artifacts import Corpus, SourceFile, Symbol, ROLE_CODE
from tracelink.features import build

FLAT = [
    {"slug": "dashboard", "title": "Dashboard", "note": "cards.py:35",
     "file": "screens/dashboard.png"},
    {"slug": "billing", "title": "Billing", "note": "billing.py:12",
     "file": "screens/billing.png"},
]

WRAPPED = {
    "generated_from": 363,
    "rows": [
        {"symbol": "Card.render", "target_file": "presentation/card.py"},
        {"symbol": "Card.total", "target_file": "presentation/card.py"},
    ],
}

NESTED = [
    {"file": "cards.py",
     "controls": [{"var": "self.ok_button", "type": "QPushButton", "line": 108},
                  {"var": "self.name_edit", "type": "QLineEdit", "line": 111}]},
]


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    return Corpus(
        root=str(tmp_path),
        files=[SourceFile(path="cards.py", role=ROLE_CODE, size=900,
                          language="python"),
               SourceFile(path="billing.py", role=ROLE_CODE, size=900,
                          language="python")],
        symbols=[Symbol(sid="cards.py::render", path="cards.py", name="render",
                        kind="function", line=10, end_line=40)])


def _tree(tmp_path: Path, **docs) -> Path:
    d = tmp_path / "docs"
    d.mkdir(parents=True, exist_ok=True)
    for name, payload in docs.items():
        (d / name.replace("__", ".")).write_text(
            json.dumps(payload), encoding="utf-8")
    return d


# --------------------------------------------------------------------------
# J1 — finding the records
# --------------------------------------------------------------------------

def test_a_top_level_list_is_the_records():
    feats = parse_document(FLAT, "manifest.json")
    assert len(feats) == 2


def test_a_wrapped_list_is_found_without_being_named():
    feats = parse_document(WRAPPED, "split-map.json")
    assert len(feats) == 2
    assert "rows" in feats[0].heading


def test_a_document_that_is_not_records_yields_nothing():
    assert parse_document({"version": 3, "ok": True}, "meta.json") == []
    assert parse_document([1, 2, 3], "numbers.json") == []


# --------------------------------------------------------------------------
# J2 — values become claims by the same test as markdown
# --------------------------------------------------------------------------

def test_a_path_with_a_line_locator_still_resolves(corpus, tmp_path):
    """`cards.py:35` is one file and a locator, not an unknown filename.

    Every row of the real screenshot manifest is anchored that way, and
    with the locator attached the path matched nothing: 54 rows, 0 claims.
    """
    fmap = build(_tree(tmp_path, manifest__json=FLAT), corpus)
    names = {c.name: c for f in fmap.features for c in f.claims}
    assert names["cards.py"].resolved
    assert names["cards.py"].paths == ["cards.py"]


def test_a_sibling_artifact_is_not_missing_code(corpus, tmp_path):
    """`screens/dashboard.png` lives in the docs tree, not the repository.

    Counted as code it is a capability the repository failed to deliver,
    which is how a screenshot manifest turns into 54 phantom findings.
    """
    d = _tree(tmp_path, manifest__json=FLAT)
    (d / "screens").mkdir()
    (d / "screens" / "dashboard.png").write_bytes(b"\x89PNG")
    fmap = build(d, corpus)
    kinds = {c.name: c.kind for f in fmap.features for c in f.claims}
    assert kinds.get("screens/dashboard.png") == "docref"
    # The one that is *not* in the docs tree stays a code claim, and fails.
    assert kinds.get("screens/billing.png") == "path"


def test_the_longest_non_claim_string_becomes_the_label():
    feats = parse_document(FLAT, "manifest.json")
    assert feats[0].label in ("dashboard", "Dashboard")


# --------------------------------------------------------------------------
# J3 — nesting
# --------------------------------------------------------------------------

def test_a_nested_record_inherits_the_file_its_parent_names():
    """Without inheritance every control is an orphan.

    A control at line 108 belongs to the file named once on the record
    above it; on its own, `self.ok_button` anchors nothing.
    """
    feats = parse_document(NESTED, "controls.json")
    assert len(feats) == 2
    names = {c.name for c in feats[0].claims}
    assert "cards.py" in names
    assert "self.ok_button" in names


def test_the_parent_of_a_nested_list_is_not_emitted_twice():
    feats = parse_document(NESTED, "controls.json")
    assert all("controls" in f.heading for f in feats)


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------

def test_json_and_markdown_land_in_one_map(corpus, tmp_path):
    d = _tree(tmp_path, manifest__json=FLAT)
    (d / "guide.md").write_text("# G\n\n- see `render()`\n", encoding="utf-8")
    fmap = build(d, corpus)
    docs = {f.doc for f in fmap.features}
    assert {"guide.md", "manifest.json"} <= docs


def test_the_json_half_can_be_ablated(corpus, tmp_path):
    d = _tree(tmp_path, manifest__json=FLAT)
    (d / "guide.md").write_text("# G\n\n- see `render()`\n", encoding="utf-8")
    assert not any(f.doc.endswith(".json")
                   for f in build(d, corpus, json_docs=False).features)


def test_a_malformed_json_file_is_skipped_not_fatal(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "broken.json").write_text("{not json", encoding="utf-8")
    assert read_json_docs(d) == []


def test_ids_do_not_collide_with_the_markdown_half(corpus, tmp_path):
    d = _tree(tmp_path, manifest__json=FLAT)
    (d / "guide.md").write_text("# G\n\n- see `render()`\n", encoding="utf-8")
    fids = [f.fid for f in build(d, corpus).features]
    assert len(fids) == len(set(fids))
