"""The tracker's done-status against what the code shows.

The count this file guards is the one a completion figure has to answer for.
A ticket marked Done that the code contradicts is counted as delivered by
every percentage in this product, and reads on a dashboard as progress. The
tag is a second axis over the same verdicts, not a recolouring of them, so
the verdict palette keeps its own meaning - including its deliberate refusal
to colour `unverified`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.tracelink_view import (DELIVERY_WORD, STATUS_CLASS_WORD, collect,
                                    delivery_tag, status_class)


def _artifact(run: Path, name: str, payload) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / f"{name}.json").write_text(
        json.dumps({"schema_version": 1, "produced_by": name, "meta": {},
                    "payload": payload}), encoding="utf-8")


def _ticket(uid: str, status: str) -> dict:
    return {"uid": uid, "summary": uid, "status": status, "description": "",
            "component": "", "key": uid, "parent": None, "source_row": 1,
            "source_sheet": "s", "inline": {}, "lang": "en",
            "summary_en": "", "description_en": "", "extra": {}}


def _verdict(uid: str, call: str, conflict: bool = False) -> dict:
    return {"uid": uid, "verdict": call, "confidence": "high",
            "reasoning": "r", "status_conflict": conflict,
            "describer": "raw-source", "cost_usd": 0.0, "evidence": []}


# --------------------------------------------------------------------------
# The rule itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status,call,conflict,expected", [
    # Marked finished: the question is whether the code agrees.
    ("Done", "corroborated", False, "confirmed"),
    ("Closed", "corroborated", False, "confirmed"),
    ("Done", "contradicted", False, "conflict"),
    ("Completed", "unverified", False, "review"),
    # Still open: only the adjudicator saying the status is wrong counts.
    ("To Do", "corroborated", True, "conflict"),
    ("In Progress", "corroborated", False, ""),
    ("To Do", "unverified", False, ""),
])
def test_the_tag_reads_status_against_the_code(status, call, conflict, expected):
    assert delivery_tag(status, _verdict("T", call, conflict)) == expected


def test_a_ticket_nobody_adjudicated_gets_no_tag():
    """Absence of evidence is not a finding. It must not land in any bucket."""
    assert delivery_tag("Done", None) == ""


def test_dropped_work_is_neither_delivered_nor_outstanding():
    """`context.py` excludes dropped work from both counts; so does this.

    Counting it as a conflict would report work somebody deliberately
    stopped as a delivery problem, forever.
    """
    assert delivery_tag("Dropped", _verdict("T", "contradicted")) == ""


@pytest.mark.parametrize("status", ["Cancelled", "Rejected", "Duplicate"])
def test_cancelled_is_not_in_this_products_dropped_vocabulary(status):
    """Documents a real gap rather than pretending it is closed.

    `context.py` describes DROPPED_STATES as "cancelled, rejected, a
    duplicate" and the set contains only `dropped`, so a Jira export using
    the ordinary word lands in `open`. That is the safe direction - it is
    never counted as delivered - but it does mean cancelled work shows as
    outstanding. Widening the set changes CLOSED_STATES, and with it overdue,
    due-soon and in-progress across the whole product, so it is a deliberate
    decision rather than a typo to fix here.
    """
    assert status_class(status) == "open"


def test_every_tag_the_rule_emits_has_a_word_for_it():
    """A tag with no label renders as a blank chip on the page."""
    emitted = {delivery_tag(s, _verdict("T", c, x))
               for s, c, x in [("Done", "corroborated", False),
                               ("Done", "contradicted", False),
                               ("Done", "unverified", False),
                               ("To Do", "corroborated", True)]}
    assert emitted - {""} == set(DELIVERY_WORD)


# --------------------------------------------------------------------------
# The counts, through the reader the page actually uses
# --------------------------------------------------------------------------

def _run(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    tickets = [
        _ticket("T1", "Done"),        # corroborated -> confirmed
        _ticket("T2", "Done"),        # contradicted -> conflict (done, not built)
        _ticket("T3", "Done"),        # unverified   -> review
        _ticket("T4", "To Do"),       # status_conflict -> conflict (built, open)
        _ticket("T5", "In Progress"), # ordinary work in progress -> no tag
        _ticket("T6", "Dropped"),     # dropped -> no tag
    ]
    _artifact(run, "run", {"project_id": "p:1", "project_name": "P",
                           "tickets": len(tickets)})
    _artifact(run, "tickets", tickets)
    _artifact(run, "candidates", [{"uid": t["uid"], "head_collision": False,
                                   "candidates": []} for t in tickets])
    _artifact(run, "corpus", {"files": [], "symbols": [], "root": "/repo"})
    _artifact(run, "verdicts", [
        _verdict("T1", "corroborated"),
        _verdict("T2", "contradicted"),
        _verdict("T3", "unverified"),
        _verdict("T4", "corroborated", conflict=True),
        _verdict("T5", "corroborated"),
        _verdict("T6", "contradicted"),
    ])
    return run


def test_the_counts_split_the_tickets_the_way_the_tag_does(tmp_path):
    t = collect(_run(tmp_path))["totals"]
    assert t["delivery_confirmed"] == 1
    assert t["delivery_conflict"] == 2
    assert t["delivery_review"] == 1


def test_a_done_ticket_the_code_contradicts_is_not_counted_as_confirmed(tmp_path):
    """The whole point. It used to be indistinguishable from delivered work."""
    t = collect(_run(tmp_path))["totals"]
    assert t["delivery_confirmed"] == 1, "only T1 is actually backed by code"
    assert t["done_not_built"] == 1, "T2 is reported finished and is not there"


def test_both_directions_of_disagreement_are_reported_apart(tmp_path):
    """One number to act on, and the breakdown so nobody has to guess."""
    t = collect(_run(tmp_path))["totals"]
    assert t["done_not_built"] == 1
    assert t["built_not_done"] == 1
    assert t["done_not_built"] + t["built_not_done"] == t["delivery_conflict"]


def test_the_tag_rides_on_every_row_so_the_page_never_recomputes_it(tmp_path):
    """Two implementations of this rule would eventually disagree."""
    rows = {r["uid"]: r["delivery_tag"] for r in collect(_run(tmp_path))["tickets"]}
    assert rows == {"T1": "confirmed", "T2": "conflict", "T3": "review",
                    "T4": "conflict", "T5": "", "T6": ""}


# --------------------------------------------------------------------------
# The tracker's own claim, normalised
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("Done", "done"),
    ("done", "done"),
    ("Closed", "done"),
    ("Completed", "done"),
    ("To Do", "open"),
    ("In Progress", "open"),
    ("Blocked", "open"),
    ("Dropped", "dropped"),
])
def test_a_tracker_status_is_classified_the_way_the_counts_classify_it(
        status, expected):
    assert status_class(status) == expected


@pytest.mark.parametrize("status", ["Release it", "Ready for UAT", "", "Foo"])
def test_an_unrecognised_status_is_outstanding_not_finished(status):
    """The conservative direction.

    Calling unknown work finished is the error this whole feature exists to
    catch; calling finished work unknown only ever asks someone to look.
    """
    assert status_class(status) == "open"


def test_every_class_has_a_word_for_it():
    assert set(STATUS_CLASS_WORD) == {"done", "open", "dropped"}


def test_the_class_rides_on_every_row(tmp_path):
    rows = {r["uid"]: r["status_class"] for r in collect(_run(tmp_path))["tickets"]}
    assert rows == {"T1": "done", "T2": "done", "T3": "done",
                    "T4": "open", "T5": "open", "T6": "dropped"}


def test_the_class_agrees_with_the_tag_about_what_finished_means(tmp_path):
    """Two vocabularies for "done" would eventually disagree, and the counts
    would stop matching the chips a reader sees beside them."""
    for r in collect(_run(tmp_path))["tickets"]:
        if r["delivery_tag"] in ("confirmed", "review"):
            assert r["status_class"] == "done", r["uid"]
