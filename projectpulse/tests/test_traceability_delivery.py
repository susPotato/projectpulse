"""The delivery half of the Traceability page.

A tracker is not the only record of what a team did. On the project this
was built against, the backlog holds 173 feature tickets and knows nothing
about the ten-EPIC refactor the same repository documents in 63 dated
tasks. `tracelink` reads those documents; these tests cover the reading of
its output, and in particular the two places where a tidier presentation
would overstate what is known.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.api import tracelink_view
from app.api.tracelink_view import collect


def _artifact(run: Path, name: str, payload) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / f"{name}.json").write_text(
        json.dumps({"schema_version": 1, "produced_by": name, "meta": {},
                    "payload": payload}), encoding="utf-8")


def _minimal(run: Path) -> None:
    """Enough for `collect` to work at all."""
    _artifact(run, "run", {"project_id": "p:1", "project_name": "P", "tickets": 0})
    _artifact(run, "tickets", [])
    _artifact(run, "candidates", [])
    _artifact(run, "corpus", {"files": [], "symbols": [], "root": "/repo"})


PROGRESS = {
    "root": "/docs",
    "items": [
        {"wid": "R01-T01", "title": "a", "group": "E / R01", "done": True,
         "started": "2026-03-01 09:00", "ended": "2026-03-01 10:00"},
        {"wid": "R02-T01", "title": "b", "group": "E / R02", "done": False,
         "started": "", "ended": ""},
        {"wid": "R02-T02", "title": "c", "group": "E / R02", "done": False,
         "started": "", "ended": ""},
    ],
    "schedule": [{"planned": "01/03", "started": "2026-03-04 09:00"}],
    "gates": [], "register": [],
    "unparsed": [{"reason": "no-id", "doc": "d.md", "line": 1, "text": "x"},
                 {"reason": "ragged-row", "doc": "d.md", "line": 2, "text": "y"}],
}

RECON = [
    {"wid": "R01-T01", "title": "a", "label": "agreed", "tracker": "done",
     "docs": "done", "code": "present", "joined_via": "claim",
     "missing": [], "anchors": ["f"], "doc": "d.md", "line": 1},
    {"wid": "R02-T01", "title": "b", "label": "both-sides-wrong",
     "tracker": "done", "docs": "done", "code": "absent",
     "joined_via": "section", "missing": ["gone.py"], "anchors": [],
     "doc": "d.md", "line": 2},
]

GATES = [
    {"name": "LOC", "command": "check_loc.py --max-lines 400", "status": "fail",
     "detail": "27 of 154 files exceed 400 lines", "signed_off": True,
     "offenders": [], "criterion": "", "check": "file_size", "doc": "d.md",
     "line": 3},
    {"name": "Layers", "command": "check_imports.py", "status": "unchecked",
     "detail": "no such directory in this repository: domain/",
     "signed_off": False, "offenders": [], "criterion": "",
     "check": "layer_purity", "doc": "d.md", "line": 4},
]


def test_a_project_without_a_docs_tree_has_no_delivery_block(tmp_path):
    """And reports no gap for it.

    Three "not written yet" notes on every run that will never have a
    documentation tree is noise, and a page that complains about absent
    optional stages teaches people to stop reading the list.
    """
    run = tmp_path / "run"
    _minimal(run)
    got = collect(run)
    assert got["delivery"] == {}
    assert not [g for g in got["gaps"]
                if g.startswith(("progress", "reconciliation", "gates"))]


def test_a_half_read_docs_tree_names_what_is_missing(tmp_path):
    """Once one of the three is present, the absence of the others is a gap."""
    run = tmp_path / "run"
    _minimal(run)
    _artifact(run, "progress", PROGRESS)
    got = collect(run)
    assert got["delivery"]["items"] == 3
    assert any("reconciliation.json not written yet" in g for g in got["gaps"])
    assert any("tracelink reconcile" in g for g in got["gaps"])


def test_counts_and_worst_group_first(tmp_path):
    run = tmp_path / "run"
    _minimal(run)
    _artifact(run, "progress", PROGRESS)
    d = collect(run)["delivery"]
    assert (d["items"], d["done"], d["open"], d["timed"]) == (3, 1, 2, 1)
    assert d["groups"][0]["group"] == "R02", "an epic wholly open sorts first"
    assert d["groups"][0]["open"] == 2


def test_a_declined_template_line_is_not_counted_as_unread(tmp_path):
    """`no-id` is the identifier rule working, not the parser failing.

    Collapsing the two made the real document look 47% unread when the
    figure that matters was 3%.
    """
    run = tmp_path / "run"
    _minimal(run)
    _artifact(run, "progress", PROGRESS)
    assert collect(run)["delivery"]["unread"] == 1


def test_direct_and_area_findings_are_counted_apart(tmp_path):
    """The split is structural and load-bearing.

    A task joins the tracker directly only when one of its deliverables
    resolves in the code, so every row whose code is absent is joined at
    area level — which is exactly the set a reader cares most about.
    Presented in one column they read as equally solid.
    """
    run = tmp_path / "run"
    _minimal(run)
    _artifact(run, "progress", PROGRESS)
    _artifact(run, "reconciliation", RECON)
    d = collect(run)["delivery"]
    assert d["confidence"]["direct"] == {"agreed": 1}
    assert d["confidence"]["area"] == {"both-sides-wrong": 1}


def test_agreed_rows_are_not_offered_as_findings(tmp_path):
    run = tmp_path / "run"
    _minimal(run)
    _artifact(run, "progress", PROGRESS)
    _artifact(run, "reconciliation", RECON)
    d = collect(run)["delivery"]
    assert [f["wid"] for f in d["findings"]] == ["R02-T01"]
    assert d["findings"][0]["missing"] == ["gone.py"]


def test_an_unchecked_gate_is_not_a_pass(tmp_path):
    """"0 PySide6 under domain/" holds trivially when there is no domain/.

    Counting that as a pass tells a reader the architecture holds when in
    fact it was never built.
    """
    run = tmp_path / "run"
    _minimal(run)
    _artifact(run, "gates", GATES)
    d = collect(run)["delivery"]
    assert d["gates_failing"] == 1
    assert d["gates_unchecked"] == 1
    assert d["gates_contradicting"] == 1, "signed off in the doc, failing here"


# --------------------------------------------------------------------------
# The digest: one ranked list across every stage
# --------------------------------------------------------------------------

VERDICTS = [
    {"uid": "T1", "verdict": "contradicted", "confidence": "high",
     "reasoning": "the code does something else", "status_conflict": False,
     "describer": "doc-feature+raw-source", "cost_usd": 0.05,
     "evidence": [{"file": "a.py", "symbol": "A", "why": "w"}]},
    {"uid": "T2", "verdict": "corroborated", "confidence": "high",
     "reasoning": "built", "status_conflict": True,
     "describer": "raw-source", "cost_usd": 0.05,
     "evidence": [{"file": "b.py", "symbol": "B", "why": "w"}]},
    {"uid": "T3", "verdict": "unverified", "confidence": "low",
     "reasoning": "cannot tell", "status_conflict": False,
     "describer": "raw-source", "cost_usd": 0.05,
     "evidence": [{"file": "guide.md", "symbol": "Thing", "why": "w"}]},
]

GROUNDING = [
    {"uid": "T1", "verdict": "contradicted", "status": "grounded",
     "checks": [{"file": "a.py", "symbol": "A", "line": 3, "status": "defined"}]},
    {"uid": "T3", "verdict": "unverified", "status": "ungrounded",
     "checks": [{"file": "guide.md", "symbol": "Thing", "line": None,
                 "status": "no-file"}]},
]

TICKETS3 = [
    {"uid": "T1", "summary": "holiday calendar", "status": "Release it",
     "description": "", "component": "", "key": None, "parent": None,
     "source_row": 1, "source_sheet": "s", "inline": {}, "lang": "en",
     "summary_en": "", "description_en": "", "extra": {}},
    {"uid": "T2", "summary": "file edit dialog", "status": "To Do",
     "description": "", "component": "", "key": None, "parent": None,
     "source_row": 2, "source_sheet": "s", "inline": {}, "lang": "en",
     "summary_en": "", "description_en": "", "extra": {}},
    {"uid": "T3", "summary": "something vague", "status": "To Do",
     "description": "", "component": "", "key": None, "parent": None,
     "source_row": 3, "source_sheet": "s", "inline": {}, "lang": "en",
     "summary_en": "", "description_en": "", "extra": {}},
]


def _full(run: Path) -> None:
    _artifact(run, "run", {"project_id": "p:1", "project_name": "P", "tickets": 3})
    _artifact(run, "tickets", TICKETS3)
    _artifact(run, "candidates", [
        {"uid": t["uid"], "head_collision": False, "candidates": []}
        for t in TICKETS3])
    _artifact(run, "corpus", {"files": [], "symbols": [], "root": "/repo"})
    _artifact(run, "verdicts", VERDICTS)
    _artifact(run, "grounding", GROUNDING)
    _artifact(run, "progress", PROGRESS)
    _artifact(run, "reconciliation", RECON)
    _artifact(run, "gates", GATES)


def test_the_digest_ranks_deterministic_checks_above_model_verdicts(tmp_path):
    """Volume earns nothing: a reader who starts with 35 never reaches the 2."""
    run = tmp_path / "run"
    _full(run)
    kinds = [f["kind"] for f in collect(run)["findings"]]
    assert kinds[0] == "contradicted"
    assert kinds.index("gate-failing") < kinds.index("documented-not-built")
    assert kinds.index("status-conflict") < kinds.index("unsupported-citation")


def test_every_finding_says_where_to_look_and_how_strong_it_is(tmp_path):
    run = tmp_path / "run"
    _full(run)
    for f in collect(run)["findings"]:
        assert f["kind"] and f["title"] and f["evidence"]


def test_a_citation_naming_a_document_is_called_out_as_such(tmp_path):
    """The failure mode the pipeline's own change introduced.

    Showing the adjudicator the team's documents taught it to cite one as
    though it were source. Every `no-file` in the real run is a markdown
    path, so this is not hypothetical.
    """
    run = tmp_path / "run"
    _full(run)
    bad = [f for f in collect(run)["findings"]
           if f["kind"] == "unsupported-citation"]
    assert bad and bad[0]["cited_a_document"] is True
    assert "documentation file" in bad[0]["detail"]


def test_a_call_site_citation_is_not_a_finding(tmp_path):
    """`no-symbol` is legitimate — a citation may name a use, not a definition."""
    run = tmp_path / "run"
    _full(run)
    _artifact(run, "grounding", [
        {"uid": "T1", "verdict": "contradicted", "status": "partly-grounded",
         "checks": [{"file": "a.py", "symbol": "A", "line": None,
                     "status": "no-symbol"}]}])
    assert not [f for f in collect(run)["findings"]
                if f["kind"] == "unsupported-citation"]


def test_a_run_with_nothing_wrong_produces_an_empty_digest(tmp_path):
    run = tmp_path / "run"
    _minimal(run)
    assert collect(run)["findings"] == []


# --- Ownership -------------------------------------------------------------


def test_a_field_most_rows_carry_is_treated_as_one_every_row_should():
    """The convention is measured, never declared.

    Nothing here may know that a team writes `Developer`. A key on at least
    half the rows is one the sheet expects, and the rows without it are the
    gap - which is what makes the finding survive a rename or a translation.
    """
    rows = [
        {"uid": "a", "status": "open", "inline": {"Reviewer": "x", "Odd": "y"}},
        {"uid": "b", "status": "open", "inline": {"Reviewer": "x"}},
        {"uid": "c", "status": "shipped", "inline": {}},
    ]
    fields = {f["field"]: f for f in tracelink_view._ownership(rows)["fields"]}

    #: On 2 of 3 rows, so expected; row c is the gap.
    assert fields["Reviewer"]["missing"] == 1
    assert fields["Reviewer"]["uids"] == ["c"]
    #: On 1 of 3. Too rare to call a convention, so it is not reported as one.
    assert "Odd" not in fields


def test_statuses_are_reported_verbatim_and_never_ranked():
    """This sheet's words do not map onto our done/open vocabulary.

    Deciding which of them means finished would turn a count into an opinion,
    so the breakdown names the status and lets the reader judge.
    """
    #: Two of four carry the field, which clears the bar for a convention -
    #: below it the field is not reported at all and there is nothing to break
    #: down, which is what the first draft of this test got wrong.
    rows = [
        {"uid": "a", "status": "Release it", "inline": {"Dev": "x"}},
        {"uid": "b", "status": "To Do", "inline": {"Dev": "y"}},
        {"uid": "c", "status": "Release it", "inline": {}},
        {"uid": "d", "status": "To Do", "inline": {}},
    ]
    field = tracelink_view._ownership(rows)["fields"][0]

    assert field["by_status"] == [
        {"status": "Release it", "n": 1},
        {"status": "To Do", "n": 1},
    ]


def test_whitespace_is_not_a_name():
    rows = [
        {"uid": "a", "status": "open", "inline": {"Dev": "x"}},
        {"uid": "b", "status": "open", "inline": {"Dev": "   "}},
    ]
    assert tracelink_view._ownership(rows)["fields"][0]["missing"] == 1


def test_an_empty_backlog_reports_no_fields_rather_than_dividing_by_zero():
    assert tracelink_view._ownership([]) == {"rows": 0, "fields": []}
