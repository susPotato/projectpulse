"""Characterization tests against the real CoWorkLocal documents.

Separated from the contract tests on purpose. `test_structure.py` opens with
the rule: anything asserting "173 tickets" is testing the fixture, not the
system. These assertions *are* about one document set, so they live apart,
assert invariants wherever an invariant will do, and mark the one place a
count is pinned as a snapshot that is expected to move.

Skipped entirely when the documents are not present, so the suite still runs
on a clean checkout.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adapters.progress_markdown import read_progress

DOCS = Path(__file__).resolve().parents[2] / "hackathon" / "docs"
REPO = Path(__file__).resolve().parents[2] / "pimsathon-main"

pytestmark = pytest.mark.skipif(
    not DOCS.is_dir(), reason="the CoWorkLocal documents are not in this checkout")


@pytest.fixture(scope="module")
def report():
    return read_progress(DOCS)


# --------------------------------------------------------------------------
# Invariants — these should hold on any document set
# --------------------------------------------------------------------------

def test_every_extracted_timestamp_parses(report):
    shape = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?$")
    for item in report.items:
        for value in (item.started, item.ended):
            assert value == "" or shape.match(value), f"{item.wid}: {value!r}"


def test_an_end_never_precedes_its_start(report):
    for item in report.items:
        if item.started and item.ended:
            assert item.ended >= item.started, item.wid


def test_identifiers_are_unique(report):
    ids = [i.wid for i in report.items]
    assert len(ids) == len(set(ids))


def test_every_item_still_points_at_its_own_line(report):
    """A locator that has drifted is worse than no locator."""
    cache: dict[str, list[str]] = {}
    for item in report.items:
        lines = cache.setdefault(
            item.doc, (DOCS / item.doc).read_text(encoding="utf-8").splitlines())
        assert item.wid in lines[item.line - 1], f"{item.doc}:{item.line}"


def test_failures_to_read_stay_rare(report):
    """Only the reasons that mean "the parser could not cope" are capped.

    The design document proposed `len(unparsed) < 0.2 * records` and that
    was the wrong measure: 64 of the 68 entries here are `no-id`, which is
    rule R2 working exactly as intended on checklist templates. Counting a
    deliberate exclusion as a loss would have pushed the threshold up until
    it stopped catching anything.
    """
    records = len(report.items) + len(report.schedule) + len(report.register)
    broke = [u for u in report.unparsed if u["reason"] != "no-id"]
    assert len(broke) < 0.1 * records, \
        f"{len(broke)} lines could not be read: {broke[:3]}"


def test_nothing_with_a_real_identifier_was_declined(report):
    """The other half of the above, and the one that catches a real loss.

    `no-id` is only safe if it never fires on something that *does* carry
    the document's identifier convention. If it did, work would vanish
    quietly under a reason that reads like success.
    """
    prefixes = {re.split(r"[-_]", i.wid, maxsplit=1)[0] for i in report.items}
    for u in report.unparsed:
        if u["reason"] != "no-id":
            continue
        for p in prefixes:
            assert not re.search(rf"\b{re.escape(p)}[-_]\d", u["text"]), \
                f"dropped something that looks like a real item: {u}"


def test_every_unread_line_says_why(report):
    for u in report.unparsed:
        assert u["reason"] and u["doc"] and u["line"] > 0


def test_plan_rows_carry_both_a_plan_and_an_actual(report):
    """Otherwise there is no slip to measure and the stage is decorative."""
    usable = [s for s in report.schedule if s.planned and s.started]
    assert usable, "no plan row has both a planned day and a real start"


# --------------------------------------------------------------------------
# The end-to-end case, where all three witnesses are checkable by hand
# --------------------------------------------------------------------------

def test_the_secrets_epic_is_open_and_its_credential_is_still_in_the_code():
    """R02 is the case the design document named, and it holds.

    Three independent witnesses, each verifiable by opening a file:

    * the checklist leaves every R02 task unchecked;
    * it blames R02 for two failing tests, naming `config.py`;
    * `config.py` still contains the plaintext credential.

    The design document predicted this would reconcile to
    `agreed-outstanding`. Measured, it comes out `tracker-optimistic`,
    because the tracker has no concept of R02 at all: the tickets that
    reach the same files are feature tickets marked done. That is the more
    interesting answer and the prediction was simply wrong, so the
    prediction is what changed.
    """
    report = read_progress(DOCS)
    r02 = [i for i in report.items if i.wid.startswith("R02-")]
    assert len(r02) >= 5, "R02 should be a whole epic"
    assert all(not i.done for i in r02), "every R02 task is unchecked"

    if REPO.is_dir():
        config = (REPO / "config.py").read_text(encoding="utf-8", errors="ignore")
        assert "sandbox_pw" in config
        assert re.search(r'"sandbox_pw"\s*:\s*"[^"]+"', config), \
            "the plaintext credential R02 exists to remove is still there"


def test_quality_gates_are_unsigned_while_the_daily_tables_claim_they_ran(report):
    """The document contradicts itself, and that is an output, not a bug."""
    gates = [g for g in report.gates if g.command]
    assert gates, "the gates carry their own verification commands"
    assert all(not g.signed_off for g in gates), \
        "no gate is signed off, though the per-team tables say they ran"
    assert all(g.started == "" for g in gates), \
        "a blank placeholder must never be read as a date"


# --------------------------------------------------------------------------
# Snapshot — expected to change; here so a change is noticed, not prevented
# --------------------------------------------------------------------------

def test_counts_snapshot(report):
    got = {
        "items": len(report.items),
        "done": sum(1 for i in report.items if i.done),
        "open": sum(1 for i in report.items if not i.done),
        "schedule": len(report.schedule),
        "gates": len(report.gates),
    }
    assert got == {"items": 63, "done": 56, "open": 7,
                   "schedule": 30, "gates": 6}, (
        f"snapshot moved to {got}. That is allowed — update it and say in the "
        f"WORKLOG what changed and why.")
