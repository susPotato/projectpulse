"""Row identity across scans - where a rename becomes a fabricated event."""

from __future__ import annotations

import pytest

from app.ingest.sources.excel.identity import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    MissingKeyColumn,
    resolve_identities,
)
from app.ingest.sources.excel.snapshot_diff import diff_snapshot

KWARGS = {"key_field": "task_id", "title_field": "title"}


def _resolve(rows, previous=None, has_key_column=True):
    return resolve_identities(
        list(enumerate(rows, start=1)),
        previous=previous,
        has_key_column=has_key_column,
        **KWARGS,
    )


class TestSheetLevelGuards:
    def test_sheet_without_a_key_column_is_refused(self):
        """Guessing identity for a whole sheet is worse than ingesting nothing."""
        with pytest.raises(MissingKeyColumn):
            _resolve([{"title": "Environment Setup"}], has_key_column=False)


class TestExplicitIds:
    def test_a_stable_id_is_high_confidence(self):
        resolved, rejected = _resolve([{"task_id": "WBS-1", "title": "Env Setup"}])
        assert rejected == []
        assert (resolved[0].row_key, resolved[0].confidence) == ("WBS-1", CONFIDENCE_HIGH)

    def test_duplicate_ids_are_rejected_not_merged(self):
        """Silently overwriting the earlier row would lose it without a trace."""
        resolved, rejected = _resolve(
            [
                {"task_id": "WBS-1", "title": "Integration build"},
                {"task_id": "WBS-1", "title": "Integration build (rework)"},
            ]
        )
        assert len(resolved) == 1
        assert len(rejected) == 1
        assert "duplicate" in rejected[0].reason


class TestFallbackMatching:
    def test_row_with_neither_id_nor_title_is_rejected(self):
        resolved, rejected = _resolve([{"task_id": None, "title": None, "status": "Open"}])
        assert resolved == []
        assert "cannot be identified" in rejected[0].reason

    def test_unmatched_row_gets_a_stable_synthetic_key(self):
        """The same unidentified row must land on the same key next scan."""
        first, _ = _resolve([{"task_id": None, "title": "Data migration dry run"}])
        second, _ = _resolve([{"task_id": None, "title": "Data migration dry run"}])
        assert first[0].row_key == second[0].row_key
        assert first[0].row_key.startswith("~anon-")
        assert first[0].confidence == CONFIDENCE_LOW

    def test_a_rename_is_matched_not_re_keyed(self):
        """Regression: a renamed row must not become a delete plus an insert.

        This is the bug the whole module exists to prevent. An earlier version
        excluded synthetic keys from the candidate pool, which meant an
        id-less row could never match itself after a rename - so every rename
        fabricated two row events, and two row events can carry a causal chain.
        """
        first, _ = _resolve([{"task_id": None, "title": "Data migration dry run"}])
        baseline = {r.row_key: r.payload for r in first}

        second, _ = _resolve(
            [{"task_id": None, "title": "Data migration dry-run"}],
            previous=baseline,
        )

        assert second[0].row_key == first[0].row_key, "rename lost the row's identity"
        assert second[0].confidence == CONFIDENCE_LOW

        # And end to end: the rename produces no delivery event at all.
        current = {r.row_key: r.payload for r in second}
        result = diff_snapshot(baseline, current, tracked_fields=("status",))
        assert result.changes == []
        assert result.added_keys == []
        assert result.removed_keys == []

    def test_a_genuinely_different_row_is_not_force_matched(self):
        first, _ = _resolve([{"task_id": None, "title": "Data migration dry run"}])
        baseline = {r.row_key: r.payload for r in first}

        second, _ = _resolve(
            [{"task_id": None, "title": "Payroll cutover rehearsal"}],
            previous=baseline,
        )
        assert second[0].row_key != first[0].row_key

    def test_one_previous_row_cannot_absorb_two_new_ones(self):
        first, _ = _resolve([{"task_id": None, "title": "Integration build"}])
        baseline = {r.row_key: r.payload for r in first}

        resolved, _ = _resolve(
            [
                {"task_id": None, "title": "Integration build"},
                {"task_id": None, "title": "Integration builds"},
            ],
            previous=baseline,
        )
        assert len({r.row_key for r in resolved}) == 2
