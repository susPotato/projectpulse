"""How much to trust the delivery-outlook figure.

Design of record: `ProjectPulseAI_Architecture.md` §8.6 — coverage × freshness ×
precedent, rendered as a **band**, never a percentage. The scenarios panel
already rejected a mocked "delivery confidence 61% -> 91%" as a number nothing
could defend (`web/src/pages/Insight.tsx`); this is the same discipline applied
to the one figure the insight screen leads with.

**Precedent is not in the formula.** It needs retrieval (pgvector cosine over
`historical_cases`), which is not built - see `README.md`'s Next section. §8.4
is explicit about the alternative to faking it: drop the input rather than
fill it with a constant, and say so. `precedent_available` carries that fact
onto the bundle instead of a silent `* 1.0`.

Pure like the rest of `intelligence/`: two numbers in, a dataclass out. No
session, no ORM, no clock - `hours_since_sync` is computed by the caller, which
is the one place that knows *now*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Band = Literal["high", "medium", "low"]

#: Below this age the sync is as fresh as polling gets - two cycles of
#: `PULSE_SYNC_INTERVAL` (default 120 min), so one missed poll does not read as
#: stale.
FRESH_HOURS = 4.0
#: Beyond this, the data behind the projection is a week old and freshness has
#: fully decayed to zero. Linear in between, deliberately: legible as "loses a
#: fifth of its weight every 33 hours" rather than an exponential nobody can
#: eyeball on a settings page.
STALE_HOURS = 168.0

#: score = coverage * freshness, both already in [0, 1]. Thresholds split that
#: product into thirds - the same three-band shape as the portfolio's
#: critical/watch/healthy, so a reader learns the vocabulary once.
_HIGH = 0.66
_MEDIUM = 0.33


@dataclass(frozen=True)
class DeliveryConfidence:
    """How much of the outlook figure rests on known, recent data.

    `score` and `band` are derived from `coverage` and `freshness` alone -
    nothing here is a model output, and every field is either an input or
    arithmetic on inputs, so a reader can redo the line by hand the same way
    `intelligence/explain.py` lets them redo the schedule projection.
    """

    band: Band
    score: float
    #: Fraction of tasks the forward pass could actually date - `context.
    #: baseline_coverage`, unchanged. A projection resting on few dated tasks
    #: is a projection over mostly-missing input, whatever the graph says.
    coverage: float
    #: 0..1, decayed from `data_age_hours`. How long ago the data behind this
    #: projection was last synced - not how old the *events* it describes
    #: are, which is a property of the demo timeline, not of the sync.
    freshness: float
    data_age_hours: float | None
    #: Always False until retrieval lands. Shown on the panel as "no precedent
    #: data yet" rather than omitted, so its absence reads as a known gap and
    #: not as a fact nobody thought to check.
    precedent_available: bool = False


def _freshness(hours: float | None) -> float:
    """1.0 at `FRESH_HOURS` or newer, 0.0 at `STALE_HOURS` or older.

    `None` - nothing has ever synced successfully - is the same as fully
    stale: there is no sync to be fresh about.
    """
    if hours is None:
        return 0.0
    if hours <= FRESH_HOURS:
        return 1.0
    if hours >= STALE_HOURS:
        return 0.0
    span = STALE_HOURS - FRESH_HOURS
    return round(1.0 - (hours - FRESH_HOURS) / span, 4)


def _band(score: float) -> Band:
    if score >= _HIGH:
        return "high"
    if score >= _MEDIUM:
        return "medium"
    return "low"


def compute(*, coverage: float, hours_since_sync: float | None) -> DeliveryConfidence:
    """The confidence band for one project's outlook figure.

    `coverage` is `context.baseline_coverage`, already a ratio. `hours_since_
    sync` is `generated_at` minus the most recent successful `SyncRun.
    finished_at`, or `None` when nothing has synced yet.
    """
    coverage = max(0.0, min(1.0, coverage))
    freshness = _freshness(hours_since_sync)
    score = round(coverage * freshness, 4)

    return DeliveryConfidence(
        band=_band(score),
        score=score,
        coverage=coverage,
        freshness=freshness,
        data_age_hours=None if hours_since_sync is None else round(hours_since_sync, 1),
    )
