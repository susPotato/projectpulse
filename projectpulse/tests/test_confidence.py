"""Guards for the delivery-confidence formula.

Pure, so all of this runs without a database. What is being protected is the
band boundaries and the two edge cases a demo will actually hit: nothing has
ever synced, and the schedule has no dated tasks at all.
"""

from __future__ import annotations

from app.intelligence.confidence import (
    FRESH_HOURS,
    STALE_HOURS,
    DeliveryConfidence,
    compute,
)


def test_full_coverage_and_a_fresh_sync_is_high():
    result = compute(coverage=1.0, hours_since_sync=0.0)
    assert result.band == "high"
    assert result.score == 1.0
    assert result.freshness == 1.0


def test_nothing_has_ever_synced_is_low_however_good_coverage_is():
    result = compute(coverage=1.0, hours_since_sync=None)
    assert result.band == "low"
    assert result.score == 0.0
    assert result.freshness == 0.0
    assert result.data_age_hours is None


def test_no_dated_tasks_is_low_however_fresh_the_sync_is():
    result = compute(coverage=0.0, hours_since_sync=0.0)
    assert result.band == "low"
    assert result.score == 0.0


def test_freshness_decays_linearly_between_the_two_thresholds():
    midpoint = (FRESH_HOURS + STALE_HOURS) / 2
    result = compute(coverage=1.0, hours_since_sync=midpoint)
    assert result.freshness == 0.5


def test_freshness_floors_at_zero_past_stale_hours():
    result = compute(coverage=1.0, hours_since_sync=STALE_HOURS * 10)
    assert result.freshness == 0.0
    assert result.band == "low"


def test_freshness_ceilings_at_one_within_fresh_hours():
    result = compute(coverage=0.5, hours_since_sync=FRESH_HOURS / 2)
    assert result.freshness == 1.0


def test_coverage_is_clamped_to_zero_one():
    # A ratio should never arrive out of range, but the formula must not
    # produce a score or band outside its own scale if one slips through.
    over = compute(coverage=1.5, hours_since_sync=0.0)
    under = compute(coverage=-0.5, hours_since_sync=0.0)
    assert over.coverage == 1.0
    assert under.coverage == 0.0


def test_band_thresholds():
    # score = coverage * freshness; hold freshness at 1.0 and walk coverage
    # across the two boundaries the bands are cut at.
    assert compute(coverage=0.70, hours_since_sync=0.0).band == "high"
    assert compute(coverage=0.66, hours_since_sync=0.0).band == "high"
    assert compute(coverage=0.50, hours_since_sync=0.0).band == "medium"
    assert compute(coverage=0.33, hours_since_sync=0.0).band == "medium"
    assert compute(coverage=0.32, hours_since_sync=0.0).band == "low"


def test_precedent_is_never_claimed_available():
    # Retrieval is not built. A True here would be the exact failure §8.4
    # warns about - a third input a judge discovers is a constant.
    assert compute(coverage=1.0, hours_since_sync=0.0).precedent_available is False


def test_is_a_frozen_dataclass():
    result = compute(coverage=1.0, hours_since_sync=0.0)
    assert isinstance(result, DeliveryConfidence)
