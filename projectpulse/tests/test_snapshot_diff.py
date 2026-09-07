"""The differ manufactures history, so its failure modes are all "invented a change"."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.ingest.sources.excel.snapshot_diff import (
    FIELD_ROW_PRESENT,
    diff_snapshot,
    normalize_value,
    normalized_payload,
)

TRACKED = ("status", "planned_end", "progress")


def _rows(**overrides):
    base = {"status": "Open", "planned_end": "2026-03-04", "progress": 0}
    base.update(overrides)
    return {"WBS-1": base}


class TestNormalizeValue:
    @pytest.mark.parametrize("value", [None, "", "   ", "\n\t "])
    def test_blank_is_absent(self, value):
        assert normalize_value(value) is None

    @pytest.mark.parametrize("value", [3, 3.0, "3", " 3 ", 3.000])
    def test_the_same_number_typed_five_ways(self, value):
        """A PM typing 3 and Excel storing 3.0 is not a change."""
        assert normalize_value(value) == "3"

    def test_fractions_survive(self):
        assert normalize_value(2.5) == "2.5"

    def test_midnight_datetime_is_a_plain_date(self):
        """openpyxl returns date cells as datetimes; a typed date has no time."""
        assert normalize_value(datetime(2026, 3, 4)) == "2026-03-04"
        assert normalize_value(date(2026, 3, 4)) == "2026-03-04"

    def test_real_times_are_kept(self):
        assert normalize_value(datetime(2026, 3, 4, 9, 30)) == "2026-03-04T09:30:00"

    def test_internal_whitespace_collapses(self):
        assert normalize_value("In   Progress\n") == "In Progress"

    def test_is_idempotent(self):
        """Stored baselines are already normalized and get normalized again."""
        for value in [3.0, datetime(2026, 3, 4), "  In   Progress "]:
            once = normalize_value(value)
            assert normalize_value(once) == once


class TestBaseline:
    def test_no_previous_scan_means_no_changes(self):
        """The first sight of a sheet is history we did not witness.

        Getting this wrong makes a first import emit hundreds of fabricated
        changes, every one of which is a candidate cause.
        """
        result = diff_snapshot(None, _rows(), TRACKED)
        assert result.changes == []
        assert result.added_keys == ["WBS-1"]

    def test_empty_previous_scan_is_not_the_same_as_no_scan(self):
        """previous={} means the sheet was empty last time - a real observation."""
        result = diff_snapshot({}, _rows(), TRACKED)
        assert [c.field for c in result.changes] == [FIELD_ROW_PRESENT]


class TestChangeDetection:
    def test_detects_a_real_change(self):
        result = diff_snapshot(_rows(), _rows(status="Blocked"), TRACKED)
        assert len(result.changes) == 1
        change = result.changes[0]
        assert (change.field, change.old_value, change.new_value) == (
            "status",
            "Open",
            "Blocked",
        )

    def test_reformatting_is_not_a_change(self):
        """The single most important negative case.

        If retyping the same value emits a change, every scan floods the causal
        engine with noise and the feature is worse than absent.
        """
        before = {"WBS-1": {"status": "Open", "planned_end": datetime(2026, 3, 4),
                            "progress": 0}}
        after = {"WBS-1": {"status": " Open ", "planned_end": "2026-03-04",
                           "progress": 0.0}}
        assert diff_snapshot(before, after, TRACKED).changes == []

    def test_untracked_fields_are_ignored(self):
        """A Notes column changing is not a delivery event."""
        before = {"WBS-1": {"status": "Open", "notes": "call the vendor"}}
        after = {"WBS-1": {"status": "Open", "notes": "vendor called back"}}
        assert diff_snapshot(before, after, TRACKED).changes == []

    def test_added_and_removed_rows(self):
        result = diff_snapshot(_rows(), {}, TRACKED)
        assert result.removed_keys == ["WBS-1"]
        assert [c.field for c in result.changes] == [FIELD_ROW_PRESENT]

    def test_low_confidence_propagates_onto_every_change(self):
        """A row we are unsure of taints the changes it produces."""
        result = diff_snapshot(
            _rows(),
            _rows(status="Blocked"),
            TRACKED,
            confidences={"WBS-1": "low"},
        )
        assert all(c.identity_confidence == "low" for c in result.changes)


def test_normalized_payload_round_trips():
    payload = {"planned_end": datetime(2026, 3, 4), "progress": 75.0, "note": None}
    assert normalized_payload(payload) == {
        "planned_end": "2026-03-04",
        "progress": "75",
        "note": None,
    }
