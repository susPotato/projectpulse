"""Contract tests.

These pin the *generalisations*, not the demo's numbers. Anything asserting
"173 tickets" or "82.7%" would fail on the next document and would be
testing the fixture rather than the system.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adapters.tickets_tabular import find_shape, read_tickets
from tracelink.artifacts import (
    Candidate, Corpus, Evidence, Label, SourceFile, Symbol, Ticket,
    TicketCandidates, load_payload, rebuild_candidates, save,
)
from tracelink.config import PipelineConfig, corpus_bar
from tracelink.corpus import classify
from tracelink.artifacts import ROLE_CODE, ROLE_DATA, ROLE_DECLARATIVE, ROLE_TEST
from tracelink.evaluate import coverage, score
from tracelink.index import Index, build as build_index, split_identifier
from tracelink.retrieve import detect_hubdocs, retrieve, title_head


# --------------------------------------------------------------------------
# Thresholds scale with the corpus
# --------------------------------------------------------------------------

def test_rarity_bar_scales_with_corpus_size():
    """The demo's core lesson: df<=4 is not a statement about rarity."""
    small = corpus_bar(13, 0.035, 2, 12)
    large = corpus_bar(5000, 0.035, 2, 12)
    assert small < large, "bar must grow with the corpus"


def test_rarity_bar_respects_floor_and_ceiling():
    assert corpus_bar(1, 0.035, 2, 12) == 2      # tiny corpus gets the floor
    assert corpus_bar(10**6, 0.035, 2, 12) == 12  # huge corpus gets the ceiling


def test_symbol_bar_is_harder_than_stem_bar():
    """Symbol names are noisier than filenames and must be judged harder."""
    r = PipelineConfig().retrieval
    for n in (50, 500, 5000):
        assert r.sym_bar(n) <= r.stem_bar(n)


# --------------------------------------------------------------------------
# The ticket adapter finds the shape instead of assuming it
# --------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh).writerows(rows)
    return path


def test_header_is_found_below_preamble(tmp_path):
    p = _write_csv(tmp_path / "e.csv", [
        ["Exported by someone", "", ""],
        ["", "", ""],
        ["Key", "Summary", "Status"],
        ["ABC-1", "Do a thing", "To Do"],
    ])
    shape = find_shape(p)
    assert shape.header_row == 2
    assert {"key", "summary", "status"} <= set(shape.columns)


def test_column_aliases_are_matched(tmp_path):
    p = _write_csv(tmp_path / "e.csv", [
        ["Issue key", "Title", "Component/s", "State"],
        ["ABC-1", "Do a thing", "ui", "Done"],
    ])
    shape = find_shape(p)
    assert {"key", "summary", "component", "status"} <= set(shape.columns)


def test_flat_export_treats_keyed_rows_as_tickets(tmp_path):
    p = _write_csv(tmp_path / "e.csv",
                   [["Key", "Summary"]] + [[f"A-{i}", f"Thing {i}"] for i in range(20)])
    tickets, shape = read_tickets(p)
    assert shape.convention == "flat"
    assert len(tickets) == 20


def test_unkeyed_subrow_export_is_detected(tmp_path):
    """The demo's convention: real work is in the rows with no key."""
    rows = [["Key", "Summary"]]
    for parent in range(3):
        rows.append([f"P-{parent}", f"Umbrella {parent}"])
        for child in range(10):
            rows.append(["", f"Feature {parent}.{child}"])
    tickets, shape = read_tickets(_write_csv(tmp_path / "e.csv", rows))
    assert shape.convention == "unkeyed-subrows"
    assert len(tickets) == 30
    assert all(t.key is None for t in tickets)
    assert tickets[0].parent == "P-0" and tickets[-1].parent == "P-2"


def test_convention_can_be_overridden(tmp_path):
    rows = [["Key", "Summary"]]
    for parent in range(3):
        rows.append([f"P-{parent}", f"Umbrella {parent}"])
        for child in range(10):
            rows.append(["", f"Feature {parent}.{child}"])
    tickets, _ = read_tickets(_write_csv(tmp_path / "e.csv", rows), convention="flat")
    assert len(tickets) == 33


def test_source_row_matches_the_spreadsheet(tmp_path):
    """A keyless row's only locator, so an off-by-one makes it useless."""
    p = _write_csv(tmp_path / "e.csv", [
        ["preamble", ""],            # sheet row 1
        ["Key", "Summary"],          # sheet row 2  <- header
        ["A-1", "first"],            # sheet row 3
        ["A-2", "second"],           # sheet row 4
    ])
    tickets, shape = read_tickets(p)
    assert shape.header_row == 1
    assert [t.source_row for t in tickets] == [3, 4]


def test_source_row_survives_blank_rows(tmp_path):
    """Blank rows are skipped as tickets but must not shift the count."""
    p = _write_csv(tmp_path / "e.csv", [
        ["Key", "Summary"],          # 1
        ["A-1", "first"],            # 2
        ["", ""],                    # 3 — skipped
        ["A-2", "second"],           # 4
    ])
    tickets, _ = read_tickets(p)
    assert [t.source_row for t in tickets] == [2, 4]


def test_source_sheet_is_recorded(tmp_path):
    p = _write_csv(tmp_path / "export.csv", [["Key", "Summary"], ["A-1", "x"]])
    tickets, _ = read_tickets(p)
    assert tickets[0].source_sheet == "export"


def test_project_filter(tmp_path):
    p = _write_csv(tmp_path / "e.csv", [
        ["Project", "Key", "Summary"],
        ["Alpha", "A-1", "one"],
        ["Beta", "B-1", "two"],
    ])
    tickets, _ = read_tickets(p, project="Alpha")
    assert [t.summary for t in tickets] == ["one"]


def test_missing_header_fails_loudly(tmp_path):
    p = _write_csv(tmp_path / "e.csv", [["a", "b"], ["1", "2"]])
    with pytest.raises(ValueError, match="no header row"):
        find_shape(p)


# --------------------------------------------------------------------------
# Corpus roles are structural, never filename-specific
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel,size,role", [
    ("core/accounts.py", 4_000, ROLE_CODE),
    ("src/Main.java", 4_000, ROLE_CODE),
    ("app/service.cs", 4_000, ROLE_CODE),
    ("tests/test_thing.py", 4_000, ROLE_TEST),
    ("ui/__tests__/widget.js", 4_000, ROLE_TEST),
    ("config/rules.yaml", 900, ROLE_DECLARATIVE),
    ("skills/one.skill", 900, ROLE_DECLARATIVE),
    ("i18n.py", 400_000, ROLE_DATA),        # large => generated, by size
    ("assets/d3.min.js", 9_000, ROLE_DATA),  # minified, by shape
])
def test_classify_is_structural(rel, size, role):
    assert classify(rel, size, PipelineConfig().corpus)[0] == role


def test_classify_knows_languages():
    cfg = PipelineConfig().corpus
    assert classify("A.java", 100, cfg)[1] == "java"
    assert classify("a.ts", 100, cfg)[1] == "typescript"
    assert classify("a.rb", 100, cfg)[1] == "ruby"


def test_split_identifier_handles_camel_and_snake():
    stop = frozenset()
    assert split_identifier("MainWindow", stop, 4) == ["main", "window"]
    assert split_identifier("model_pricing", stop, 4) == ["model", "pricing"]
    assert "network" in split_identifier("block-network", stop, 4)


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------

def _corpus(tmp_path: Path) -> Corpus:
    files = [
        SourceFile("ui/chat_panel.py", ROLE_CODE, 100, "python"),
        SourceFile("ui/chat_view.py", ROLE_CODE, 100, "python"),
        SourceFile("core/billing.py", ROLE_CODE, 100, "python"),
    ]
    symbols = [
        Symbol("ui/chat_panel.py::ChatPanel", "ui/chat_panel.py", "ChatPanel", "class"),
        Symbol("core/billing.py::InvoiceEngine", "core/billing.py", "InvoiceEngine", "class"),
    ]
    return Corpus(files=files, symbols=symbols, root=str(tmp_path), analyzer="test")


def test_title_head_splits_on_conventional_separators():
    assert title_head("Cowork Chat — Chat panel with history") == "Cowork Chat"
    assert title_head("Dashboard: stat cards") == "Dashboard"
    assert title_head("Plain title") == "Plain title"


def test_shared_head_tickets_get_distinct_candidate_sets(tmp_path):
    """The collapse that made 143 retrievals only 96 real decisions."""
    cfg = PipelineConfig()
    idx = build_index(_corpus(tmp_path), cfg)
    tickets = [
        Ticket(uid="1", summary="Area — chat panel behaviour"),
        Ticket(uid="2", summary="Area — billing invoice totals"),
        Ticket(uid="3", summary="Area — something else entirely"),
    ]
    out = {r.uid: r.strong_paths() for r in retrieve(tickets, idx, cfg)}
    assert all(r.head_collision for r in retrieve(tickets, idx, cfg))
    assert out["1"] != out["2"], "shared head must not produce identical sets"
    assert "ui/chat_panel.py" in out["1"]
    assert "core/billing.py" in out["2"]


def test_unshared_head_uses_the_head_only(tmp_path):
    cfg = PipelineConfig()
    idx = build_index(_corpus(tmp_path), cfg)
    tickets = [Ticket(uid="1", summary="chat — anything at all about billing")]
    r = retrieve(tickets, idx, cfg)[0]
    assert not r.head_collision
    assert "core/billing.py" not in r.strong_paths()


def test_evidence_is_always_inspectable(tmp_path):
    cfg = PipelineConfig()
    idx = build_index(_corpus(tmp_path), cfg)
    r = retrieve([Ticket(uid="1", summary="chat")], idx, cfg)[0]
    for c in r.candidates:
        for e in c.evidence:
            assert e.matcher and e.anchor and e.bar >= 1


def test_hubdoc_detection_is_measured_not_named():
    """A file matching most of the backlog describes everything."""
    ev = [Evidence("stem", "x", 1, 2, True)]
    rows = [
        TicketCandidates(uid=str(i), candidates=[
            Candidate("docs/EVERYTHING.md", list(ev)),
            Candidate(f"src/f{i}.py", list(ev)),
        ])
        for i in range(10)
    ]
    assert detect_hubdocs(rows) == {"docs/EVERYTHING.md"}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_score_rewards_precision_and_recall():
    labels = {"a": Label("a", ["x.py", "y.py"])}
    results = {"a": TicketCandidates("a", [
        Candidate("x.py", [Evidence("stem", "x", 1, 2, True)]),
        Candidate("z.py", [Evidence("stem", "z", 1, 2, True)]),
    ])}
    s = score(labels, results)
    assert s.precision == pytest.approx(0.5)
    assert s.recall == pytest.approx(0.5)
    assert s.hit_rate == 1.0


def test_score_counts_a_correct_refusal():
    labels = {"a": Label("a", [])}
    results = {"a": TicketCandidates("a", [])}
    s = score(labels, results)
    assert s.empty_correct == 1 and s.false_positive_only == 0


def test_score_counts_an_all_wrong_set():
    labels = {"a": Label("a", ["x.py"])}
    results = {"a": TicketCandidates("a", [
        Candidate("q.py", [Evidence("stem", "q", 1, 2, True)])])}
    s = score(labels, results)
    assert s.false_positive_only == 1 and s.hit_rate == 0.0


def test_coverage_reports_set_collapse():
    ev = [Evidence("stem", "x", 1, 2, True)]
    same = [TicketCandidates(str(i), [Candidate("a.py", list(ev))]) for i in range(4)]
    cov = coverage(same)
    assert cov["distinct_sets"] == 1
    assert cov["collapsed_onto_shared_set"] == 3


# --------------------------------------------------------------------------
# Artifact envelope
# --------------------------------------------------------------------------

def test_artifacts_round_trip(tmp_path):
    rows = [TicketCandidates("a", [Candidate("x.py", [Evidence("stem", "x", 1, 2, True)])],
                             head_collision=True)]
    p = save(tmp_path / "c.json", "candidates", rows)
    back = rebuild_candidates(load_payload(p, "candidates"))
    assert back[0].uid == "a"
    assert back[0].head_collision is True
    assert back[0].candidates[0].evidence[0].anchor == "x"


def test_wrong_stage_is_rejected(tmp_path):
    p = save(tmp_path / "c.json", "candidates", [])
    with pytest.raises(ValueError, match="expected 'tickets'"):
        load_payload(p, "tickets")


def test_schema_version_mismatch_is_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"schema_version": 999, "produced_by": "x", "payload": []}',
                 encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_payload(p)


# --------------------------------------------------------------------------
# Fields the export buried in free text
# --------------------------------------------------------------------------

def test_inline_fields_are_recovered_from_description():
    from tracelink.adapters.tickets_tabular import parse_inline_fields
    got = parse_inline_fields("PO: alice\nBA: bob\nDeveloper: carol")
    assert got == {"PO": "alice", "BA": "bob", "Developer": "carol"}


def test_inline_date_serials_are_decoded_and_the_raw_kept():
    from tracelink.adapters.tickets_tabular import parse_inline_fields
    got = parse_inline_fields("Ngay nhan: 46244")
    assert got["Ngay nhan"] == "46244"
    assert got["Ngay nhan (date)"] == "2026-08-10"


def test_a_number_that_is_not_a_date_serial_is_left_alone():
    from tracelink.adapters.tickets_tabular import parse_inline_fields
    got = parse_inline_fields("Count: 12")
    assert got == {"Count": "12"}


def test_prose_without_labels_yields_nothing():
    from tracelink.adapters.tickets_tabular import parse_inline_fields
    assert parse_inline_fields("Just a sentence about the feature.") == {}


def test_inline_fields_reach_the_ticket(tmp_path):
    p = _write_csv(tmp_path / "e.csv", [
        ["Key", "Summary", "Description"],
        ["A-1", "thing", "PO: alice | Developer: bob"],
    ])
    tickets, _ = read_tickets(p)
    assert tickets[0].inline["PO"] == "alice"
    assert tickets[0].inline["Developer"] == "bob"


# --------------------------------------------------------------------------
# Generality: a header that matches but is unusable
# --------------------------------------------------------------------------

def test_a_schedule_style_header_is_understood(tmp_path):
    """Not every backlog is a Jira export. This shape is a plan workbook."""
    p = _write_csv(tmp_path / "s.csv", [
        ["Task ID", "Activity", "Phase", "Status", "Owner", "Predecessor"],
        ["MIG-1", "Source profiling", "Requirement", "Done", "alice", ""],
        ["MIG-2", "Mapping spec", "Design", "Done", "bob", "MIG-1"],
    ])
    tickets, shape = read_tickets(p)
    assert {"key", "summary", "component", "status"} <= set(shape.columns)
    assert [t.summary for t in tickets] == ["Source profiling", "Mapping spec"]
    assert tickets[0].key == "MIG-1"


def test_a_header_without_a_title_column_fails_with_the_fix(tmp_path):
    """Two alias hits is enough to look like a header and still be unusable.
    The old behaviour returned zero rows and blamed the wrong thing."""
    p = _write_csv(tmp_path / "s.csv", [
        ["Status", "Owner", "Widgets"],
        ["Done", "alice", "3"],
    ])
    with pytest.raises(ValueError) as exc:
        find_shape(p)
    msg = str(exc.value)
    assert "no summary column" in msg
    assert "Widgets" in msg, "must show the headers it actually saw"
    assert "ALIASES" in msg, "must say where to add the missing name"


# --------------------------------------------------------------------------
# Translation: match on English, never lose the original
# --------------------------------------------------------------------------

def test_script_detection_is_by_character_not_wordlist():
    from tracelink.translate import detect
    assert detect("Thêm chức năng") == "vi"
    assert detect("新しい機能") == "ja"
    assert detect("Add a feature") == "en"


def test_translation_never_replaces_the_original():
    t = Ticket(uid="1", summary="Thêm chức năng", description="mô tả")
    t.summary_en, t.description_en = "Add a feature", "description"
    assert t.summary == "Thêm chức năng", "the tracker's text must survive"
    assert t.text == "Thêm chức năng\nmô tả"


def test_match_text_prefers_english_but_keeps_the_original():
    t = Ticket(uid="1", summary="Thêm RULEBASE", description="")
    t.summary_en = "Add RULEBASE"
    assert "Add RULEBASE" in t.match_text
    assert "Thêm RULEBASE" in t.match_text, "a lost product name must still match"
    assert t.match_summary == "Add RULEBASE"


def test_untranslated_tickets_match_on_their_own_text():
    t = Ticket(uid="1", summary="Add a feature")
    assert t.match_text == t.text
    assert t.match_summary == "Add a feature"


def test_an_all_english_backlog_needs_no_translation():
    from tracelink.translate import census, needs_translation
    rows = [Ticket(uid=str(i), summary=f"Feature {i}") for i in range(5)]
    assert census(rows) == {"en": 5}
    assert not any(needs_translation(t) for t in rows)
