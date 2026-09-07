"""Turn two spreadsheet snapshots into state changes.

A hand-maintained sheet only ever shows the present. Everything we know about
*change* in it is inferred by comparing what we see now against what we stored
last time - which means this module manufactures history, and every bug in it
manufactures history that did not happen.

Three rules keep it honest, and each exists because of a specific failure:

1. **No baseline, no changes.** The first time we see a sheet there is nothing to
   compare against, so nothing changed. Passing ``previous=None`` says "no prior
   scan"; passing ``previous={}`` says "the prior scan had no rows", and those are
   different claims. Conflating them makes a first import emit hundreds of
   fabricated changes.

2. **Normalize before comparing.** ``3``, ``3.0`` and ``" 3 "`` are the same value
   typed by three different people. Without normalization every scan emits
   spurious changes, the causal engine drowns, and the feature is worse than
   useless because it is confidently wrong.

3. **Only tracked fields.** A free-text Notes column changing is not a delivery
   event. Emitting it would put noise into causal chains.

Deliberately pure: no session, no ORM, no clock. Persisting the result and
stamping it with scan bounds is :mod:`app.ingest.sources.excel.ingest`'s job.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_WS = re.compile(r"\s+")

# Row-level events, recorded as changes to a synthetic field so they sit on the
# same timeline as ordinary field edits.
FIELD_ROW_PRESENT = "__row__"


def normalize_value(value: Any) -> str | None:
    """Reduce a cell to a comparable string, or None for "no value".

    The equivalences are the ones a spreadsheet actually produces:
    blank/whitespace is absent; an integral float is its integer; a datetime at
    midnight is a plain date (openpyxl returns dates as datetimes).
    """
    if value is None:
        return None

    if isinstance(value, str):
        text = _WS.sub(" ", value).strip()
        return text or None

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, datetime):
        # openpyxl hands back datetimes for date-formatted cells; a date typed by
        # a human has no meaningful time component.
        if (value.hour, value.minute, value.second, value.microsecond) == (0, 0, 0, 0):
            return value.date().isoformat()
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if number == number.to_integral_value():
            return str(int(number))
        return format(number.normalize(), "f")

    return _WS.sub(" ", str(value)).strip() or None


def normalized_payload(payload: Mapping[str, Any]) -> dict[str, str | None]:
    """Normalize every cell in a row.

    What gets stored as the baseline must be what the next scan is compared
    against. Storing raw cell objects and normalizing only at comparison time
    would work until an openpyxl upgrade changed how a cell type is returned, at
    which point every row would appear to change at once.

    :func:`normalize_value` is idempotent, so re-normalizing a stored value is
    safe.
    """
    return {key: normalize_value(value) for key, value in payload.items()}


@dataclass(frozen=True)
class Change:
    """One field of one row moving between two values."""

    row_key: str
    field: str
    old_value: str | None
    new_value: str | None
    identity_confidence: str = "high"


@dataclass
class DiffResult:
    changes: list[Change] = field(default_factory=list)
    added_keys: list[str] = field(default_factory=list)
    removed_keys: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.changes


def diff_snapshot(
    previous: Mapping[str, Mapping[str, Any]] | None,
    current: Mapping[str, Mapping[str, Any]],
    tracked_fields: Sequence[str],
    confidences: Mapping[str, str] | None = None,
    *,
    emit_row_events: bool = True,
) -> DiffResult:
    """Compare two keyed snapshots.

    Args:
        previous: row_key -> payload as last observed, or **None** if this sheet
            has never been scanned. None yields no changes at all.
        current: row_key -> payload as observed now.
        tracked_fields: which payload keys can produce a change.
        confidences: row_key -> 'high' | 'low', from the identity resolver. A row
            we are not sure is the same row carries that doubt onto every change
            it produces, and the causal engine drops what it cannot trust.
        emit_row_events: record rows appearing and disappearing as changes to
            :data:`FIELD_ROW_PRESENT`.

    Returns:
        The changes, plus which keys were added and removed.
    """
    result = DiffResult()

    if previous is None:
        # First sight of this sheet: everything is baseline, nothing is news.
        result.added_keys = list(current)
        return result

    confidences = confidences or {}

    previous_keys = set(previous)
    current_keys = set(current)

    result.added_keys = sorted(current_keys - previous_keys)
    result.removed_keys = sorted(previous_keys - current_keys)

    for key in sorted(current_keys & previous_keys):
        before = previous[key]
        after = current[key]
        confidence = confidences.get(key, "high")

        for name in tracked_fields:
            old = normalize_value(before.get(name))
            new = normalize_value(after.get(name))
            if old != new:
                result.changes.append(
                    Change(
                        row_key=key,
                        field=name,
                        old_value=old,
                        new_value=new,
                        identity_confidence=confidence,
                    )
                )

    if emit_row_events:
        for key in result.added_keys:
            result.changes.append(
                Change(
                    row_key=key,
                    field=FIELD_ROW_PRESENT,
                    old_value=None,
                    new_value="present",
                    identity_confidence=confidences.get(key, "high"),
                )
            )
        for key in result.removed_keys:
            result.changes.append(
                Change(
                    row_key=key,
                    field=FIELD_ROW_PRESENT,
                    old_value="present",
                    new_value=None,
                    identity_confidence=confidences.get(key, "high"),
                )
            )

    return result
