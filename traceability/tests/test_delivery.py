"""Contract tests for Stage 4e — effort and output, from the work record.

The module's whole risk is overclaiming, so most of these pin what it
declines to say: a short interval is reported and not judged, an open
task's missing output is not a finding, and the one impossibility it does
assert — two intervals overlapping for a single owner — is a statement
about the record rather than about anybody's day.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink import delivery as DV
from tracelink.artifacts import Claim, ProgressReport, ScheduleEntry, WorkItem


def _item(wid, start="", end="", owner="Alice", done=True, deliverables=()):
    return WorkItem(wid=wid, title=wid, group=f"Epic / {wid[:3]}", owner=owner,
                    done=done, started=start, ended=end, doc="plan.md", line=1,
                    deliverables=[Claim(text=n, name=n, kind=k, paths=list(p))
                                  for n, k, p in deliverables])


def _report(items, schedule=()):
    return ProgressReport(root=".", items=list(items), schedule=list(schedule))


# --------------------------------------------------------------------------
# Durations
# --------------------------------------------------------------------------

def test_a_duration_is_the_gap_between_two_recorded_stamps():
    d = DV.build(_report([_item("A-01", "2026-03-02 09:00", "2026-03-02 10:30")]))
    assert d.tasks[0].minutes == 90


def test_a_task_missing_either_stamp_is_not_timed():
    d = DV.build(_report([_item("A-01", "2026-03-02 09:00", ""),
                          _item("A-02", "", "2026-03-02 09:00"),
                          _item("A-03", "2026-03-02 09:00", "2026-03-02 09:10")]))
    assert [t.wid for t in d.tasks] == ["A-03"]
    assert DV.summary(d)["timed_tasks"] == 1


def test_a_one_minute_task_is_reported_and_not_judged():
    """The module counts them; it does not call them wrong.

    Nothing here can separate a fast team from a backfilled sheet, so the
    distribution is the output and the reader decides.
    """
    d = DV.build(_report([_item("A-01", "2026-03-02 09:00", "2026-03-02 09:01")]))
    assert d.tasks[0].minutes == 1
    assert d.tasks[0].flags == []
    assert DV.summary(d)["under_five_minutes"] == 1


def test_an_end_before_its_start_is_flagged():
    d = DV.build(_report([_item("A-01", "2026-03-02 10:00", "2026-03-02 09:00")]))
    assert "ends-before-start" in d.tasks[0].flags


# --------------------------------------------------------------------------
# The one impossibility
# --------------------------------------------------------------------------

def test_one_owner_cannot_spend_the_same_minute_twice():
    d = DV.build(_report([
        _item("A-01", "2026-03-02 09:00", "2026-03-02 10:00"),
        _item("A-02", "2026-03-02 09:30", "2026-03-02 11:00"),
    ]))
    assert len(d.overlaps) == 1
    assert d.overlaps[0].minutes == 30
    assert all("overlaps" in t.flags[0] for t in d.tasks)


def test_two_owners_working_at_once_is_not_an_overlap():
    d = DV.build(_report([
        _item("A-01", "2026-03-02 09:00", "2026-03-02 10:00", owner="Alice"),
        _item("A-02", "2026-03-02 09:30", "2026-03-02 11:00", owner="Bob"),
    ]))
    assert d.overlaps == []


def test_an_unowned_task_cannot_overlap_anything():
    """Two tasks with no owner are not evidence about one person."""
    d = DV.build(_report([
        _item("A-01", "2026-03-02 09:00", "2026-03-02 10:00", owner=""),
        _item("A-02", "2026-03-02 09:30", "2026-03-02 11:00", owner=""),
    ]))
    assert d.overlaps == []


def test_touching_intervals_do_not_overlap():
    d = DV.build(_report([
        _item("A-01", "2026-03-02 09:00", "2026-03-02 10:00"),
        _item("A-02", "2026-03-02 10:00", "2026-03-02 11:00"),
    ]))
    assert d.overlaps == []


# --------------------------------------------------------------------------
# Calendar
# --------------------------------------------------------------------------

def test_a_day_inside_the_span_with_nothing_on_it_is_reported():
    d = DV.build(_report([
        _item("A-01", "2026-03-02 09:00", "2026-03-02 10:00"),
        _item("A-02", "2026-03-05 09:00", "2026-03-05 10:00"),
    ]))
    assert d.idle_days == ["2026-03-03", "2026-03-04"]
    assert set(d.by_day) == {"2026-03-02", "2026-03-05"}


def test_days_outside_the_span_are_not_idle():
    d = DV.build(_report([_item("A-01", "2026-03-02 09:00", "2026-03-02 10:00")]))
    assert d.idle_days == []


# --------------------------------------------------------------------------
# Planned against actual
# --------------------------------------------------------------------------

@pytest.mark.parametrize("planned,started,days", [
    ("24/03 (T2)", "2026-03-27 16:05", 3),      # late
    ("28/03", "2026-03-22 18:57", -6),          # started before it was planned
])
def test_slip_is_measured_in_both_directions(planned, started, days):
    d = DV.build(_report([], [ScheduleEntry(group="T / Team", title="x",
                                            planned=planned, started=started)]))
    assert d.slips[0].days == days


def test_a_row_on_its_planned_day_is_not_a_slip():
    d = DV.build(_report([], [ScheduleEntry(group="g", title="x",
                                            planned="24/03 (T2)",
                                            started="2026-03-24 09:00")]))
    assert d.slips == []


def test_an_unparseable_plan_cell_is_skipped_not_guessed():
    d = DV.build(_report([], [ScheduleEntry(group="g", title="x", planned="soon",
                                            started="2026-03-24 09:00")]))
    assert d.slips == []


# --------------------------------------------------------------------------
# What a finished task says it produced
# --------------------------------------------------------------------------

def test_only_a_completed_task_can_have_failed_to_produce_something():
    """An open task's missing output is the plan, not a finding."""
    d = DV.build(_report([
        _item("A-01", done=True, deliverables=[("gone.py", "path", [])]),
        _item("A-02", done=False, deliverables=[("later.py", "path", [])]),
    ]))
    assert [u.wid for u in d.unmet] == ["A-01"]


def test_a_resolved_deliverable_is_not_unmet():
    d = DV.build(_report([
        _item("A-01", deliverables=[("here.py", "path", ["here.py"])]),
    ]))
    assert d.unmet == []


def test_missing_output_is_split_by_what_kind_of_thing_it_is():
    """A missing test and a missing source file are different findings."""
    d = DV.build(_report([_item("A-01", deliverables=[
        ("tests/fakes/fake_provider.py", "path", []),
        ("domain/thing.py", "path", []),
        ("README.md", "path", []),
        ("SomeClass", "symbol", []),
    ])]))
    kinds = {u.name: u.kind for u in d.unmet}
    assert kinds["tests/fakes/fake_provider.py"] == DV.KIND_TEST
    assert kinds["domain/thing.py"] == DV.KIND_CODE
    assert kinds["README.md"] == DV.KIND_DOC
    assert kinds["SomeClass"] == DV.KIND_SYMBOL


def test_tests_sort_first_because_a_definition_of_done_depends_on_them():
    d = DV.build(_report([_item("A-01", deliverables=[
        ("domain/thing.py", "path", []),
        ("tests/test_thing.py", "path", []),
    ])]))
    assert d.unmet[0].kind == DV.KIND_TEST


def test_a_cross_reference_to_another_document_is_not_missing_output():
    d = DV.build(_report([_item("A-01", deliverables=[("guide.md", "docref", [])])]))
    assert d.unmet == []


def test_a_named_document_is_recognised_when_the_tree_is_known():
    d = DV.build(_report([_item("A-01", deliverables=[("NOTES", "path", [])])]),
                 known_docs={"NOTES"})
    assert d.unmet[0].kind == DV.KIND_DOC


# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------

def test_summary_reports_the_distribution_not_a_single_number():
    d = DV.build(_report([
        _item(f"A-{i:02d}", "2026-03-02 09:00", f"2026-03-02 09:{i:02d}")
        for i in range(10, 60, 10)
    ]))
    s = DV.summary(d)
    assert s["p25_minutes"] <= s["median_minutes"] <= s["p75_minutes"]
    assert s["total_hours"] > 0


def test_an_empty_report_summarises_to_zero_rather_than_raising():
    s = DV.summary(DV.build(_report([])))
    assert s["timed_tasks"] == 0 and s["median_minutes"] == 0
