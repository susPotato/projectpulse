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
