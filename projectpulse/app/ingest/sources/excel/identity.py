"""Decide which spreadsheet row is which, across scans.

This is the risk in Excel ingestion, and it is not the diffing.

Hand-maintained sheets have no stable key unless you insist on one. Rows get
reordered, inserted, and renamed - and **a renamed task looks like a delete plus
an insert**, which manufactures two state changes that never happened, which can
manufacture a causal chain that never happened. That single bug would discredit
the whole root-cause feature, so identity is resolved explicitly and every weak
match is labelled as weak.

The policy, in order of preference:

1. A stable ``Task ID`` from the template we control. Confidence ``high``.
2. No id on the row, but a close title match against a row we saw last scan.
   Confidence ``low`` - the change is recorded and shown on the timeline, but the
   causal engine will not build on it.
3. Neither: the row cannot be identified at all and is rejected, visibly.

A sheet with no id *column* is rejected outright rather than silently
fallback-matched - see :class:`MissingKeyColumn`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.ingest.sources.excel.snapshot_diff import normalize_value

#: Above this title similarity we accept a match, at low confidence. Set strictly:
#: a false match invents history, while a miss merely produces an add plus a
#: remove, which is visible and recoverable.
TITLE_MATCH_THRESHOLD = 0.88

CONFIDENCE_HIGH = "high"
CONFIDENCE_LOW = "low"


class MissingKeyColumn(Exception):
    """The sheet has no identity column, so nothing about it can be trusted."""


@dataclass(frozen=True)
class ResolvedRow:
    row_key: str
    confidence: str
    payload: dict[str, Any]
    row_index: int


@dataclass(frozen=True)
class Rejection:
    row_index: int
    raw_row: dict[str, Any]
    reason: str


def _title_key(value: Any) -> str:
    return (normalize_value(value) or "").casefold()


def _synthetic_key(title: str) -> str:
    """A stable key for a row we could not otherwise identify.

    Derived from the title so the same unidentified row lands on the same key next
    scan; prefixed so it is obvious in the data that this was not a real id.
    """
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    return f"~anon-{digest}"


def resolve_identities(
    rows: Sequence[tuple[int, dict[str, Any]]],
    *,
    key_field: str,
    title_field: str,
    previous: Mapping[str, Mapping[str, Any]] | None,
    has_key_column: bool,
) -> tuple[list[ResolvedRow], list[Rejection]]:
    """Assign a stable key and a confidence to every row.

    Args:
        rows: (row_index, payload) pairs, in sheet order.
        key_field: canonical name of the identity column.
        title_field: canonical name of the human-readable column used for
            fallback matching.
        previous: row_key -> payload from the last scan, or None if there was none.
        has_key_column: whether the sheet declared the identity column at all.

    Raises:
        MissingKeyColumn: if the sheet has no identity column.
    """
    if not has_key_column:
        raise MissingKeyColumn(
            f"sheet has no {key_field!r} column; refusing to guess row identity. "
            "Add the column to the template, or fix the column map for this scope."
        )

    resolved: list[ResolvedRow] = []
    rejected: list[Rejection] = []
    seen_keys: dict[str, int] = {}

    # Candidates for fallback matching: last scan's rows, each usable once.
    # Every row we saw last scan is a match candidate, synthetic keys included.
    # Excluding them would defeat the whole fallback: a row with no id is given a
    # synthetic key on its first scan, so if it is later renamed it can only be
    # recognised by matching against that synthetic-keyed row. Skip them and a
    # rename becomes a delete plus an insert - the exact fabrication this module
    # exists to prevent.
    unmatched_previous: dict[str, str] = {}
    if previous:
        for prev_key, prev_payload in previous.items():
            unmatched_previous[prev_key] = _title_key(prev_payload.get(title_field))

    for row_index, payload in rows:
        explicit = normalize_value(payload.get(key_field))

        if explicit is not None:
            if explicit in seen_keys:
                rejected.append(
                    Rejection(
                        row_index=row_index,
                        raw_row=dict(payload),
                        reason=(
                            f"duplicate {key_field} {explicit!r}, already used by row "
                            f"{seen_keys[explicit]}; the later row would silently "
                            "overwrite the earlier one"
                        ),
                    )
                )
                continue
            seen_keys[explicit] = row_index
            unmatched_previous.pop(explicit, None)
            resolved.append(
                ResolvedRow(explicit, CONFIDENCE_HIGH, dict(payload), row_index)
            )
            continue

        title = _title_key(payload.get(title_field))
        if not title:
            rejected.append(
                Rejection(
                    row_index=row_index,
                    raw_row=dict(payload),
                    reason=(
                        f"row has neither {key_field} nor {title_field}; "
                        "it cannot be identified across scans"
                    ),
                )
            )
            continue

        best_key, best_ratio = None, 0.0
        for prev_key, prev_title in unmatched_previous.items():
            if not prev_title:
                continue
            ratio = SequenceMatcher(None, title, prev_title).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = prev_key, ratio

        if best_key is not None and best_ratio >= TITLE_MATCH_THRESHOLD:
            del unmatched_previous[best_key]
            key = best_key
        else:
            key = _synthetic_key(title)

        if key in seen_keys:
            rejected.append(
                Rejection(
                    row_index=row_index,
                    raw_row=dict(payload),
                    reason=(
                        f"row resolves to key {key!r}, already claimed by row "
                        f"{seen_keys[key]}"
                    ),
                )
            )
            continue

        seen_keys[key] = row_index
        # Never 'high': this row was matched on a title a human can retype.
        resolved.append(ResolvedRow(key, CONFIDENCE_LOW, dict(payload), row_index))

    return resolved, rejected
