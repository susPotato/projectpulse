"""The reconciliation truth table, one synthetic triple per outcome.

Eight labelled combinations plus the two cases that must *refuse* a label:
a work item naming no code, and a work item from a document measured as
describing a different build.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink import reconcile as R
from tracelink.artifacts import (
    Candidate, Claim, DocStat, Evidence, FeatureMap, ProgressReport, Ticket,
    TicketCandidates, WorkItem, MODALITY_GROUNDED, MODALITY_UNGROUNDED,
)

DONE_STATUS = {"release it"}


def _item(wid, done, names, doc="guide.md"):
    return WorkItem(wid=wid, title=wid, group="G", done=done, doc=doc, line=1,
                    deliverables=[Claim(text=n, name=n, kind="symbol") for n in names])


def _resolve(item, present: set[str]):
    """Stand in for `features.resolve_claims`, so the table tests the table."""
    for c in item.deliverables:
        if c.name in present:
            c.paths = [f"{c.name}.py"]
            c.sids = [f"{c.name}.py::{c.name}"]
    return item


def _ticket(uid, status):
    return Ticket(uid=uid, summary=uid, status=status)


def _cand(uid, paths):
    return TicketCandidates(uid=uid, candidates=[
        Candidate(path=p, evidence=[Evidence(matcher="doc", anchor="a", df=1,
                                             bar=1, strong=True)])
        for p in paths])


def _run(items, tickets, cands, fmap=None, done_status=DONE_STATUS):
    return R.reconcile(ProgressReport(root=".", items=items), tickets, cands,
                       fmap=fmap, done_status=done_status)


# --------------------------------------------------------------------------
# The eight
# --------------------------------------------------------------------------

def _sectioned(paths=("thing.py",)) -> FeatureMap:
    """A feature map whose one section pins `paths`.

    Every `absent` row in the table needs this. An item whose own code is
    missing resolves nothing and therefore joins to no ticket, so without a
    section to join through, half the table — and every label worth
    reading — is unreachable.
    """
    from tracelink.artifacts import Feature

    return FeatureMap(root=".", features=[
        Feature(fid="F0", doc="guide.md", heading=["G"], label="row",
                claims=[Claim(text=p, name=p, kind="path", paths=[p])])
        for p in paths
    ], docs=[DocStat(doc="guide.md", modality=MODALITY_GROUNDED, features=1,
                     claims=1, resolved=1)])


@pytest.mark.parametrize("ticket_status,item_done,code_present,expected", [
    ("Release it", True, True, "agreed"),
    ("Release it", True, False, "both-sides-wrong"),
    ("Release it", False, True, "undertracked-doc"),
    ("Release it", False, False, "tracker-optimistic"),
    ("In Progress", True, True, "untracked-delivery"),
    ("In Progress", True, False, "doc-optimistic"),
    ("In Progress", False, True, "unclaimed-code"),
    ("In Progress", False, False, "agreed-outstanding"),
])
def test_truth_table(ticket_status, item_done, code_present, expected):
    item = _resolve(_item("W-01", item_done, ["thing"]),
                    {"thing"} if code_present else set())
    rows = _run([item], [_ticket("T1", ticket_status)],
                [_cand("T1", ["thing.py"])], fmap=_sectioned())
    assert rows[0].label == expected
    assert rows[0].code == ("present" if code_present else "absent")
    assert rows[0].joined_via == ("claim" if code_present else "section")


def test_absent_code_still_joins_when_another_claim_resolved():
    """A partly-wrong item must not lose its tracker side."""
    item = _resolve(_item("W-02", True, ["here", "gone"]), {"here"})
    rows = _run([item], [_ticket("T1", "In Progress")], [_cand("T1", ["here.py"])])
    assert rows[0].code == "present"
    assert rows[0].missing == ["gone"]
    assert rows[0].label == "untracked-delivery"


# --------------------------------------------------------------------------
# The two refusals
# --------------------------------------------------------------------------

def test_naming_no_code_is_unknown_never_absent():
    """Rule 1. Absence of evidence is not evidence of absence."""
    rows = _run([_item("W-03", False, [])], [], [])
    assert rows[0].code == "unknown"
    assert rows[0].label == "unknown"


def _mixed_fmap() -> FeatureMap:
    return FeatureMap(root=".", docs=[
        DocStat(doc="plan.md", modality=MODALITY_UNGROUNDED, features=1,
                claims=1, resolved=0),
        DocStat(doc="guide.md", modality=MODALITY_GROUNDED, features=1,
                claims=1, resolved=1),
    ])


def test_a_checked_claim_stands_whatever_document_it_came_from():
    """Rule 2 is per item, not per document.

    An earlier version of this test asserted the opposite — that a done
    item in an ungrounded document always reports `claimed-done-elsewhere`.
    It was wrong, and wrong in a way that hid the findings: every document
    in the real set is ungrounded, so all 56 completed tasks were
    discounted and 57 of 63 rows came out `unknown`, 54 of them with a code
    state we had already established. Retrieval had the right rule all
    along: a claim we checked ourselves does not need the document to
    vouch for it.
    """
    grounded = _resolve(_item("W-04", True, ["thing"], doc="guide.md"), {"thing"})
    planned = _resolve(_item("W-05", True, ["thing"], doc="plan.md"), {"thing"})
    rows = {r.wid: r for r in _run([grounded, planned],
                                   [_ticket("T1", "Release it")],
                                   [_cand("T1", ["thing.py"])], fmap=_mixed_fmap())}
    assert rows["W-04"].label == "agreed"
    assert rows["W-05"].docs == "done", "its deliverable was verified directly"
    assert rows["W-05"].label == "agreed"


def test_an_unverifiable_claim_from_an_ungrounded_document_is_discounted():
    """What rule 2 is actually for: the cases we could not check.

    No deliverable to look up, and a document measured as describing some
    other build. That is the one situation where the document's reliability
    is the only evidence there is, and it is not enough.
    """
    planned = _item("W-06", True, [], doc="plan.md")
    grounded = _item("W-07", True, [], doc="guide.md")
    rows = {r.wid: r for r in _run([planned, grounded], [], [],
                                   fmap=_mixed_fmap())}
    assert rows["W-06"].docs == R.ELSEWHERE
    assert rows["W-06"].label == "unknown", "and so it cannot come out `agreed`"
    assert rows["W-07"].docs == "done", "a reliable document keeps its claim"


def test_without_a_status_mapping_the_tracker_axis_is_unknown():
    """Which status means finished is a decision, and an unmade one shows."""
    item = _resolve(_item("W-06", True, ["thing"]), {"thing"})
    rows = _run([item], [_ticket("T1", "Release it")],
                [_cand("T1", ["thing.py"])], done_status=set())
    assert rows[0].tracker == "unknown"
    assert rows[0].label == "unknown"


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def test_rows_are_ordered_worst_first():
    bad = _resolve(_item("W-07", True, ["gone"]), set())
    good = _resolve(_item("W-08", True, ["thing"]), {"thing"})
    rows = _run([good, bad], [_ticket("T1", "Release it")],
                [_cand("T1", ["thing.py"]), _cand("T1", ["gone.py"])])
    assert rows[0].severity <= rows[-1].severity


def test_an_absent_row_is_always_area_level():
    """Structural, not incidental, and the reason it has to be reported.

    An item joins directly only when one of its deliverables resolved, and
    `absent` means none did. So every `both-sides-wrong`,
    `tracker-optimistic`, `doc-optimistic` and `agreed-outstanding` row
    rests on area-level tracker evidence, always. Measured on the real
    documents: 30 absent rows, 30 of them section-joined.
    """
    absent = _resolve(_item("W-10", True, ["gone"]), set())
    present = _resolve(_item("W-11", True, ["thing"]), {"thing"})
    rows = {r.wid: r for r in _run([absent, present],
                                   [_ticket("T1", "Release it")],
                                   [_cand("T1", ["thing.py"])], fmap=_sectioned())}
    assert rows["W-10"].code == "absent" and rows["W-10"].confidence == "area"
    assert rows["W-11"].code == "present" and rows["W-11"].confidence == "direct"


def test_summary_can_be_split_by_confidence():
    absent = _resolve(_item("W-12", True, ["gone"]), set())
    present = _resolve(_item("W-13", True, ["thing"]), {"thing"})
    rows = _run([absent, present], [_ticket("T1", "Release it")],
                [_cand("T1", ["thing.py"])], fmap=_sectioned())
    assert R.summary(rows, "direct") == {"agreed": 1}
    assert R.summary(rows, "area") == {"both-sides-wrong": 1}
    assert R.summary(rows) == {"both-sides-wrong": 1, "agreed": 1}


def test_summary_counts_only_labels_present():
    item = _resolve(_item("W-09", False, ["thing"]), {"thing"})
    s = R.summary(_run([item], [_ticket("T1", "In Progress")],
                       [_cand("T1", ["thing.py"])]))
    assert s == {"unclaimed-code": 1}


# An axis that came out the same on every row decided nothing


def test_a_constant_axis_is_reported():
    """`both-sides-wrong` reads as two sources agreeing. On this data the
    tracker is `done` on every row, so it is really one source."""
    from tracelink import reconcile as RC

    rows = [RC.Row(wid=f"R{i}", title="t", group="g", tracker="done",
                   docs="done" if i else "open", code="absent",
                   label="both-sides-wrong", tickets=["T1"])
            for i in range(4)]
    flat = RC.degenerate_axes(rows)
    assert flat["tracker"] == "done"
    assert flat["code"] == "absent"
    assert "docs" not in flat


def test_a_varying_axis_is_not_reported():
    from tracelink import reconcile as RC

    rows = [RC.Row(wid="A", title="t", group="g", tracker="done", docs="done",
                   code="present", label="agreed"),
            RC.Row(wid="B", title="t", group="g", tracker="open", docs="done",
                   code="absent", label="doc-optimistic")]
    assert "tracker" not in RC.degenerate_axes(rows)


def test_no_rows_means_no_claim_about_axes():
    from tracelink import reconcile as RC

    assert RC.degenerate_axes([]) == {}


def test_join_width_reports_how_many_tickets_a_row_reached():
    """A row joined to 100 tickets is not evidence about one task."""
    from tracelink import reconcile as RC

    rows = [RC.Row(wid=str(i), title="t", group="g", tickets=["T"] * n)
            for i, n in enumerate((3, 31, 104))]
    w = RC.join_width(rows)
    assert w["min"] == 3 and w["median"] == 31 and w["max"] == 104
    assert w["rows"] == 3
