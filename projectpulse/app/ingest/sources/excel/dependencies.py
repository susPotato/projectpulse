"""Turn a schedule sheet into the edges of a DAG.

This module exists because **no source we ingest states dependencies natively**.
The Excel template has a ``Predecessor`` column only because we own the template;
Jira ``issuelinks`` is sparse in practice. Without edges, "impact analysis"
collapses to *"this task is late, therefore its milestone is late"* - true, but
thin, and it hollows out one of the four product pillars.

So edges come from two places, and the difference between them is recorded rather
than smoothed over:

``excel_predecessor``
    A human wrote it down. This is a *stated* fact about the project.

``wbs_implicit``
    Nobody wrote it down, but the sheet's own dates say row B could not start
    until row A finished. This is an *inferred* fact, and it is asserted only
    when the dates actually support it - see :func:`_infer_wbs_edges`.

``Dependency.source`` carries that distinction all the way to the evidence panel,
because a critical path computed partly from inferred edges has to be able to say
so. Downstream code that needs certainty can filter to stated edges alone.

Four things are dropped rather than hedged, consistent with the ordering rule in
:mod:`app.intelligence.temporal.ordering`:

* a predecessor naming a row that is not in the sheet - a dangling edge would
  make the graph assert a path that does not exist,
* a row pointing at itself,
* an edge touching a row whose identity is only ``low`` confidence - it may not be
  the row we think it is, so an edge to it could attach to the wrong task,
* an edge that closes a cycle. A hand-typed predecessor column will eventually
  contain one, and a cyclic graph has no critical path at all: NetworkX would
  raise rather than return a wrong number. Better to drop the single edge that
  closes the loop, loudly, than to lose the whole schedule engine.

The module is pure - no session, no ORM, no clock - so all of the above is
testable in milliseconds. It speaks in *row keys*; mapping those onto domain ids
is the caller's job.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.ingest.sources.excel.identity import (
    CONFIDENCE_LOW,
    Rejection,
    ResolvedRow,
)

SOURCE_STATED = "excel_predecessor"
SOURCE_IMPLICIT = "wbs_implicit"

#: The four finish/start relations of classical scheduling. All are parsed so a
#: PM's existing MS-Project habits survive the copy-paste, even though the
#: schedule engine only reasons about FS today.
DEP_TYPES = ("FS", "SS", "FF", "SF")
DEFAULT_DEP_TYPE = "FS"

#: What separates two predecessors inside one cell. Newline is included because a
#: wrapped cell is how people fit three ids into one column.
_SPLIT = re.compile(r"[,;\n]+")

#: MS-Project suffix notation: ``WBS-108FS+2d``, ``WBS-108SS``, ``WBS-108-1d``.
#: Applied only as a *fallback* - see :func:`_match_key` for why.
_SUFFIX = re.compile(
    r"""^\s*
    (?P<key>.*?)\s*
    (?:(?P<type>FS|SS|FF|SF)\b)?\s*
    (?:(?P<sign>[+-])\s*(?P<lag>\d+)\s*(?:d|days?)?)?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Edge:
    """One directed edge, keyed by spreadsheet row key rather than domain id."""

    predecessor_key: str
    successor_key: str
    dep_type: str = DEFAULT_DEP_TYPE
    lag_days: int = 0
    source: str = SOURCE_STATED
    #: The row that carried this edge - the successor, in both the stated and the
    #: inferred case. Its raw record is what the evidence panel should show.
    stated_by_key: str = ""

    @property
    def pair(self) -> tuple[str, str]:
        return (self.predecessor_key, self.successor_key)


@dataclass
class EdgeResolution:
    edges: list[Edge] = field(default_factory=list)
    rejects: list[Rejection] = field(default_factory=list)

    @property
    def stated(self) -> list[Edge]:
        return [e for e in self.edges if e.source == SOURCE_STATED]

    @property
    def inferred(self) -> list[Edge]:
        return [e for e in self.edges if e.source == SOURCE_IMPLICIT]


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------


def _clean(value: Any) -> str:
    return _WS.sub(" ", str(value)).strip() if value is not None else ""


def _key_index(rows: Iterable[ResolvedRow]) -> dict[str, list[str]]:
    """Casefolded key -> the real row keys that fold onto it.

    A list rather than a single value because two rows differing only in case are
    genuinely ambiguous, and guessing between them is how an edge lands on the
    wrong task.
    """
    index: dict[str, list[str]] = {}
    for row in rows:
        index.setdefault(row.row_key.casefold(), []).append(row.row_key)
    return index


def _match_key(
    token: str, index: dict[str, list[str]]
) -> tuple[str | None, str, int, str | None]:
    """Resolve one predecessor token to ``(row_key, dep_type, lag_days, error)``.

    The whole token is tried against the sheet's keys **before** the MS-Project
    suffix is parsed off it. Otherwise a legitimate id ending in a relation code -
    ``WBS-FS``, or a stream code like ``DEV-SS`` - would be silently truncated to
    ``WBS-`` and then either fail to resolve or, worse, resolve to something else.
    """
    whole = index.get(token.casefold())
    if whole is not None:
        if len(whole) > 1:
            return None, DEFAULT_DEP_TYPE, 0, (
                f"predecessor {token!r} matches {len(whole)} rows whose ids differ "
                "only in capitalisation"
            )
        return whole[0], DEFAULT_DEP_TYPE, 0, None

    match = _SUFFIX.match(token)
    if match is None or not _clean(match.group("key")):
        return None, DEFAULT_DEP_TYPE, 0, f"predecessor {token!r} is not a task id"

    bare = _clean(match.group("key"))
    candidates = index.get(bare.casefold())
    if candidates is None:
        return None, DEFAULT_DEP_TYPE, 0, (
            f"predecessor {token!r} does not match any row in this sheet"
        )
    if len(candidates) > 1:
        return None, DEFAULT_DEP_TYPE, 0, (
            f"predecessor {token!r} matches {len(candidates)} rows whose ids differ "
            "only in capitalisation"
        )

    dep_type = (match.group("type") or DEFAULT_DEP_TYPE).upper()
    lag = int(match.group("lag") or 0)
    if match.group("sign") == "-":
        lag = -lag
    return candidates[0], dep_type, lag, None


def parse_predecessor_cell(
    value: Any, index: dict[str, list[str]]
) -> tuple[list[tuple[str, str, int]], list[str]]:
    """Split one ``Predecessor`` cell into resolved refs plus per-token errors.

    Returns ``([(row_key, dep_type, lag_days), ...], [error, ...])``. A cell with
    three predecessors of which one is a typo yields two good edges and one
    error: a single bad token must not cost the other two.
    """
    text = _clean(value)
    if not text:
        return [], []

    refs: list[tuple[str, str, int]] = []
    errors: list[str] = []
    for raw_token in _SPLIT.split(text):
        token = _clean(raw_token)
        if not token:
            continue
        row_key, dep_type, lag, error = _match_key(token, index)
        if error is not None or row_key is None:
            errors.append(error or f"predecessor {token!r} could not be resolved")
            continue
        refs.append((row_key, dep_type, lag))
    return refs, errors


# --------------------------------------------------------------------------
# WBS-implicit inference
# --------------------------------------------------------------------------


def _as_date(value: Any) -> date | None:
    """Coerce a spreadsheet date cell, giving up rather than guessing.

    Only ISO-ish text is accepted. A locale-ambiguous ``03/04/2026`` is *not*
    parsed: choosing between March 4th and April 3rd on the reader's behalf would
    silently move a schedule by a month.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _infer_wbs_edges(
    rows: Sequence[ResolvedRow],
    *,
    start_field: str,
    end_fields: Sequence[str],
    already_stated: set[str],
    existing_pairs: set[tuple[str, str]],
) -> list[Edge]:
    """Derive finish-to-start edges from the sheet's own sequence.

    The rule, from architecture §5.4: the schedule mockup encodes a chain in which
    each activity's start equals the previous activity's baseline end. So order
    the rows by start date and, for a row nobody wrote a predecessor for, look
    backwards for the nearest row that actually *finishes* by the time this one
    starts.

    Two guards keep this from becoming fiction:

    * A row that already carries a stated predecessor is left alone. A human's
      answer always beats ours.
    * The edge is asserted only where ``predecessor.end <= successor.start``
      genuinely holds. Two rows running in parallel produce no edge at all, which
      is the correct answer - and it is why this returns fewer edges than there
      are rows.
    """

    def end_of(row: ResolvedRow) -> date | None:
        for name in end_fields:
            value = _as_date(row.payload.get(name))
            if value is not None:
                return value
        return None

    # Low-confidence rows are excluded from inference entirely: we are not sure
    # which task they are, so we are certainly not sure what they follow.
    ordered: list[tuple[date, ResolvedRow]] = []
    for row in rows:
        if row.confidence == CONFIDENCE_LOW:
            continue
        start = _as_date(row.payload.get(start_field))
        if start is None:
            continue
        ordered.append((start, row))
    ordered.sort(key=lambda pair: (pair[0], pair[1].row_index))

    edges: list[Edge] = []
    for position, (start, row) in enumerate(ordered):
        if row.row_key in already_stated:
            continue
        # Nearest earlier row that has finished by the time this one starts.
        for _, candidate in reversed(ordered[:position]):
            finish = end_of(candidate)
            if finish is None or finish > start:
                continue
            pair = (candidate.row_key, row.row_key)
            if pair in existing_pairs:
                break
            edges.append(
                Edge(
                    predecessor_key=candidate.row_key,
                    successor_key=row.row_key,
                    dep_type=DEFAULT_DEP_TYPE,
                    lag_days=(start - finish).days,
                    source=SOURCE_IMPLICIT,
                    stated_by_key=row.row_key,
                )
            )
            existing_pairs.add(pair)
            break
    return edges


# --------------------------------------------------------------------------
# Cycle guard
# --------------------------------------------------------------------------


def _drop_cycles(edges: Sequence[Edge]) -> tuple[list[Edge], list[Edge]]:
    """Keep the largest acyclic prefix, in the order given.

    Edges are offered in priority order - stated before inferred - so when a loop
    closes, the edge dropped is the least authoritative one that completes it.
    Incremental rather than "detect, then remove": after refusing one edge the
    rest of the graph is usually fine, and we want to keep as much of it as we can.
    """
    kept: list[Edge] = []
    dropped: list[Edge] = []
    successors: dict[str, list[str]] = {}

    def reaches(origin: str, target: str) -> bool:
        """Is `target` already downstream of `origin`?"""
        seen = {origin}
        stack = [origin]
        while stack:
            for nxt in successors.get(stack.pop(), ()):
                if nxt == target:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    for edge in edges:
        # Adding pred -> succ closes a cycle iff pred is already reachable from succ.
        if reaches(edge.successor_key, edge.predecessor_key):
            dropped.append(edge)
            continue
        kept.append(edge)
        successors.setdefault(edge.predecessor_key, []).append(edge.successor_key)

    return kept, dropped


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def resolve_edges(
    rows: Sequence[ResolvedRow],
    *,
    predecessor_field: str = "predecessor",
    start_field: str = "start_date",
    end_fields: Sequence[str] = ("baseline_end", "planned_end"),
    infer_wbs: bool = True,
) -> EdgeResolution:
    """Build the edge set for one schedule sheet.

    Every rejection carries the offending row so it lands in ``raw_rejects`` and
    becomes visible to the PM. A predecessor typo is a data-quality problem the
    person who owns the sheet can fix in ten seconds - but only if we tell them.
    """
    result = EdgeResolution()
    index = _key_index(rows)
    by_key = {row.row_key: row for row in rows}

    stated: list[Edge] = []
    have_stated: set[str] = set()
    pairs: set[tuple[str, str]] = set()

    for row in rows:
        refs, errors = parse_predecessor_cell(row.payload.get(predecessor_field), index)

        for error in errors:
            result.rejects.append(
                Rejection(
                    row_index=row.row_index, raw_row=dict(row.payload), reason=error
                )
            )

        for predecessor_key, dep_type, lag in refs:
            if predecessor_key == row.row_key:
                result.rejects.append(
                    Rejection(
                        row_index=row.row_index,
                        raw_row=dict(row.payload),
                        reason=(
                            f"row {row.row_key!r} lists itself as its own predecessor"
                        ),
                    )
                )
                continue

            # An edge is only as trustworthy as the identity of the rows it joins.
            weak = [
                key
                for key in (predecessor_key, row.row_key)
                if by_key[key].confidence == CONFIDENCE_LOW
            ]
            if weak:
                result.rejects.append(
                    Rejection(
                        row_index=row.row_index,
                        raw_row=dict(row.payload),
                        reason=(
                            f"dependency {predecessor_key} -> {row.row_key} dropped: "
                            f"identity of {', '.join(weak)} is low confidence"
                        ),
                    )
                )
                continue

            # The sheet says the same thing twice. Harmless; take the first.
            if (predecessor_key, row.row_key) in pairs:
                continue

            pairs.add((predecessor_key, row.row_key))
            have_stated.add(row.row_key)
            stated.append(
                Edge(
                    predecessor_key=predecessor_key,
                    successor_key=row.row_key,
                    dep_type=dep_type,
                    lag_days=lag,
                    source=SOURCE_STATED,
                    stated_by_key=row.row_key,
                )
            )

    inferred: list[Edge] = []
    if infer_wbs:
        inferred = _infer_wbs_edges(
            rows,
            start_field=start_field,
            end_fields=end_fields,
            already_stated=have_stated,
            existing_pairs=pairs,
        )

    # Stated first, so a cycle costs an inferred edge before a human-written one.
    kept, dropped = _drop_cycles([*stated, *inferred])
    result.edges = kept

    for edge in dropped:
        row = by_key[edge.successor_key]
        result.rejects.append(
            Rejection(
                row_index=row.row_index,
                raw_row=dict(row.payload),
                reason=(
                    f"dependency {edge.predecessor_key} -> {edge.successor_key} "
                    f"({edge.source}) dropped: it would create a cycle, and a "
                    "cyclic schedule has no critical path"
                ),
            )
        )

    return result
