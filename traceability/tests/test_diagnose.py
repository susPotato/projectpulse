"""Export diagnostics.

Two classes of bug matter here and both were real during development:
saying a field is a dependency when it is not (Jira's `Rank`, "Detection (D)
Ranking"), and failing to say a planning field is really a timestamp because
one row had a gap. Everything below pins one of those.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.diagnose import _matches, diagnose, title_convention

HEADERS = ["Key", "Summary", "Status", "Linked Issues", "Epic Link",
           "Due Date", "Created", "Planned Start", "Component/s",
           "Detection (D) Ranking", "Rank", "Apparent Cause"]


def _rows(**cols):
    n = max(len(v) for v in cols.values())
    return [{k: v[i] for k, v in cols.items()} for i in range(n)]


def _levels(d, topic):
    return [f for f in d.findings if f.topic == topic]


# --------------------------------------------------------------------------
# Field matching
# --------------------------------------------------------------------------

def test_rank_lookalikes_are_not_relationships():
    assert not _matches("detection (d) ranking", ("rank",))
    assert not _matches("occurrence (o) ranking", ("rank",))
    assert _matches("rank", ("rank",))


def test_parent_lookalikes_are_not_parents():
    assert not _matches("apparent cause", ("parent",))
    assert _matches("parent link", ("parent link",))


def test_multiword_keys_match_in_order():
    assert _matches("baseline start date", ("baseline start date",))
    assert not _matches("start date baseline", ("baseline start date",))


# --------------------------------------------------------------------------
# Relationships
# --------------------------------------------------------------------------

def test_empty_relationship_fields_are_a_blocker():
    d = diagnose(_rows(Summary=["a", "b", "c"], **{"Linked Issues": ["", "", ""]}),
                 ["Summary", "Linked Issues"])
    f = _levels(d, "no recorded dependencies")
    assert f and f[0].level == "blocker"


def test_populated_relationship_fields_are_reported_not_flagged():
    d = diagnose(_rows(Summary=["a", "b"], **{"Linked Issues": ["blocks X", ""]}),
                 ["Summary", "Linked Issues"])
    assert not _levels(d, "no recorded dependencies")
    assert _levels(d, "recorded dependencies")


def test_jira_rank_alone_does_not_count_as_a_dependency():
    """Rank is board ordering. Counting it would hide the blocker on every
    Jira export ever produced."""
    d = diagnose(_rows(Summary=["a", "b"], Rank=["0|i0001:", "0|i0002:"],
                       **{"Linked Issues": ["", ""]}),
                 ["Summary", "Rank", "Linked Issues"])
    assert "Rank" not in d.relationships
    assert _levels(d, "no recorded dependencies")


# --------------------------------------------------------------------------
# Schedule vs bookkeeping
# --------------------------------------------------------------------------

def test_planning_field_equal_to_a_timestamp_is_flagged():
    d = diagnose(_rows(Summary=["a", "b", "c"],
                       **{"Planned Start": ["2026-01-01", "2026-01-01", "2026-01-01"],
                          "Created": ["2026-01-01", "2026-01-01", "2026-01-01"]}),
                 ["Summary", "Planned Start", "Created"])
    f = _levels(d, "planning field is a timestamp")
    assert f and f[0].level == "warning"


def test_a_single_gap_does_not_hide_the_timestamp_finding():
    """The demo export has exactly this shape: one row lacks Planned Start."""
    d = diagnose(_rows(Summary=["a", "b", "c", "d"],
                       **{"Planned Start": ["2026-01-01", "2026-01-01",
                                            "2026-01-01", ""],
                          "Created": ["2026-01-01", "2026-01-01",
                                      "2026-01-01", "2026-01-01"]}),
                 ["Summary", "Planned Start", "Created"])
    assert _levels(d, "planning field is a timestamp")


def test_a_genuine_plan_is_not_flagged():
    d = diagnose(_rows(Summary=["a", "b", "c"],
                       **{"Planned Start": ["2026-02-01", "2026-03-01", "2026-04-01"],
                          "Created": ["2026-01-01", "2026-01-01", "2026-01-01"]}),
                 ["Summary", "Planned Start", "Created"])
    assert not _levels(d, "planning field is a timestamp")


def test_no_schedule_data_is_a_blocker():
    d = diagnose(_rows(Summary=["a", "b"], **{"Due Date": ["", ""]}),
                 ["Summary", "Due Date"])
    f = _levels(d, "no schedule data")
    assert f and f[0].level == "blocker"


# --------------------------------------------------------------------------
# Shape of the sheet
# --------------------------------------------------------------------------

def test_constant_columns_are_identified():
    d = diagnose(_rows(Summary=["a", "b", "c"], Project=["X", "X", "X"]),
                 ["Summary", "Project"])
    assert ("Project", "X") in d.constant


def test_grouped_row_order_is_reported():
    comp = ["ui"] * 5 + ["core"] * 5
    d = diagnose(_rows(Summary=[str(i) for i in range(10)],
                       **{"Component/s": comp}),
                 ["Summary", "Component/s"])
    assert d.order_runs["Component/s"][0] == 2
    assert _levels(d, "row order is structure")


def test_scattered_order_is_not_reported_as_structure():
    comp = ["ui", "core"] * 8
    d = diagnose(_rows(Summary=[str(i) for i in range(16)],
                       **{"Component/s": comp}),
                 ["Summary", "Component/s"])
    assert d.order_runs["Component/s"][0] == 16
    assert not _levels(d, "row order is structure")


def test_date_serials_buried_in_text_are_decoded():
    d = diagnose(
        _rows(Summary=["a", "b"], Description=[
            "PO: alice | Ngay nhan: 46244 | note here to make it long enough",
            "PO: bob | Ngay nhan: 46246 | another note long enough to qualify",
        ]),
        ["Summary", "Description"])
    f = _levels(d, "dates buried in text")
    assert f and "2026-08-10" in f[0].detail


def test_inline_key_value_fields_are_surfaced():
    d = diagnose(
        _rows(Summary=["a", "b"], Description=[
            "PO: alice\nBA: bob\nDeveloper: carol — padding to exceed the bar",
            "PO: dave\nBA: erin\nDeveloper: frank — padding to exceed the bar",
        ]),
        ["Summary", "Description"])
    f = _levels(d, "fields inlined in text")
    assert f and "PO" in f[0].detail


def test_a_constant_text_column_is_not_scanned_for_structure():
    """A column with one value everywhere carries no per-row structure, so
    scanning it would only add noise."""
    text = "PO: alice | Ngay nhan: 46244 | note here to make it long enough"
    d = diagnose(_rows(Summary=["a", "b"], Description=[text, text]),
                 ["Summary", "Description"])
    assert not _levels(d, "dates buried in text")
    assert ("Description", text[:40]) in d.constant


def test_empty_input_is_reported_not_crashed():
    d = diagnose([], ["Summary"])
    assert _levels(d, "empty")


# --------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------

def test_title_convention_counts_shared_heads():
    t = title_convention(["Chat — one", "Chat — two", "Billing — three", "Plain"])
    assert t["with_separator"] == 3
    assert t["shared_heads"] == 1
    assert t["rows_under_a_shared_head"] == 2


def test_a_flat_keyed_backlog_is_profiled_rather_than_matching_nothing(tmp_path):
    """A Jira backlog: every issue has a key. The old default profiled only the
    unkeyed sub-rows of the nested demo export, matched nothing here, and the
    stage exited 1 - failing the whole pipeline run over a report."""
    from tracelink.cli import main

    rows = ["Issue key,Summary,Description,Status,Issue Type,Project"]
    rows += [f"CW-{n},Feature number {n},Does thing {n},Done,Task,CW" for n in range(1, 13)]
    export = tmp_path / "jira.csv"
    export.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert main(["--run", str(tmp_path / "run"), "diagnose", str(export)]) == 0
    assert (tmp_path / "run" / "diagnosis.json").is_file()
