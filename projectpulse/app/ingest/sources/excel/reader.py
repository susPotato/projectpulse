"""Read a spreadsheet against a declared contract.

Two decisions worth stating, because both are load-bearing:

**Parse by header name, never by column index.** Someone will insert a column, and
positional parsing would then silently shift every field by one - producing data
that is wrong rather than absent, which is far harder to notice.

**openpyxl, not pandas.** pandas infers dtypes per column and would coerce values
on its own schedule, so a column that is mostly numbers turns a stray ``"TBD"``
into NaN and a date column round-trips through Timestamp. We do our own
normalization in :func:`~app.ingest.sources.excel.snapshot_diff.normalize_value`
precisely so that comparison is predictable; a second, invisible coercion layer
underneath it would undo that.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from app.ingest.sources.excel.identity import Rejection

_WS = re.compile(r"\s+")

#: How many contract columns a row must contain to be taken for the header row.
#: Real sheets carry a title and a blank line above the table.
_HEADER_MIN_HITS = 2
#: How far down to look before giving up.
_HEADER_SEARCH_ROWS = 12


def normalize_header(text: Any) -> str:
    if text is None:
        return ""
    return _WS.sub(" ", str(text)).strip().rstrip("*:").strip().casefold()


@dataclass(frozen=True)
class SheetContract:
    """What we require of a sheet, and what we will read from it.

    ``columns`` maps normalized header text to our canonical field name, so the
    template can say "Planned Finish" while the code says ``planned_end``, and a
    later rename is a one-line change here rather than a migration.

    ``template_headers`` is the other direction: the exact headers, in order,
    that a blank template we hand a PM should carry. It lives here rather than
    in `exports/` because a sheet we ask someone to fill in and a sheet we know
    how to read have to be the same sheet. Several headers in ``columns`` are
    accepted aliases; this picks the one to write.
    """

    name: str
    key_field: str
    title_field: str
    tracked_fields: tuple[str, ...]
    columns: dict[str, str]
    entity_type: str = "task"
    template_headers: tuple[str, ...] = ()

    def canonical_fields(self) -> set[str]:
        return set(self.columns.values())

    def template_fields(self) -> set[str]:
        """The canonical fields a blank template actually offers a column for.

        Compared against `canonical_fields` by a test: adding a column to the
        contract and forgetting the template would otherwise ship a template
        that cannot express the thing we just started reading.
        """
        return {
            self.columns[normalize_header(h)]
            for h in self.template_headers
            if normalize_header(h) in self.columns
        }


@dataclass
class SheetRead:
    rows: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    rejects: list[Rejection] = field(default_factory=list)
    has_key_column: bool = False
    header_row: int | None = None
    unknown_headers: list[str] = field(default_factory=list)
    sha256: str = ""


def sha256_file(path: str | Path) -> str:
    """Content hash, used to skip a sheet that has not changed since last scan.

    This is what makes two-hourly polling effectively free: most scans find the
    same bytes and stop before parsing.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_header_row(
    grid: list[tuple], contract: SheetContract
) -> tuple[int | None, dict[int, str]]:
    """Locate the header row and map column position -> canonical field.

    Searched rather than assumed, because real sheets carry a title line and a
    blank row above the table.
    """
    wanted = set(contract.columns)

    for row_index, row in enumerate(grid[:_HEADER_SEARCH_ROWS]):
        mapping: dict[int, str] = {}
        for position, value in enumerate(row):
            header = normalize_header(value)
            if header in wanted:
                mapping[position] = contract.columns[header]
        if len(mapping) >= _HEADER_MIN_HITS:
            return row_index, mapping

    return None, {}


def find_sheet(
    path: str | Path,
    contract: SheetContract,
    *,
    preferred: str | None = None,
) -> str | None:
    """Which tab in this workbook is the sheet `contract` describes.

    The header *row* has always been searched for rather than assumed, because
    real sheets carry a title line above the table. Demanding an exact tab
    name was the matching rigidity nobody had removed: our own generated
    workbooks say `Activities`, and a workbook a PM already had says `Sheet1`
    or `Schedule` or `WBS`, so importing a real document ingested zero rows.

    This is not a guess. A tab qualifies only if the contract can find its
    header row *and* the identity column is in it - the same two conditions
    `read_sheet` imposes before it will trust a row - so a workbook of notes
    and a pivot table qualifies nothing and the caller is told so.

    `preferred` is tried first, so a workbook that does use the conventional
    name resolves to it whatever else is in the file, and nothing about an
    existing sheet's behaviour changes.

    ⚠️ The answer is half the scope key (`ingest_sheet` builds
    `"<logical name>#<sheet>"`), so resolve **once, when a sheet is
    registered**, and store it. Re-resolving on every scan would let a tab
    rename silently change the scope, which loses the baseline and fabricates
    a change per row with no lower bound - the same trap `transport.py`
    documents for `logical_name`.
    """
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        names = list(workbook.sheetnames)
        order = ([preferred] if preferred in names else []) + [
            n for n in names if n != preferred
        ]
        for name in order:
            grid = list(
                islice(workbook[name].iter_rows(values_only=True), _HEADER_SEARCH_ROWS)
            )
            header_row, columns = _find_header_row(grid, contract)
            if header_row is not None and contract.key_field in set(columns.values()):
                return name
    finally:
        workbook.close()
    return None


def read_sheet(
    path: str | Path,
    sheet_name: str,
    contract: SheetContract,
) -> SheetRead:
    """Read one sheet into canonical row dicts.

    Never raises on a bad row. A single unparseable row must not cost us the other
    four hundred - it is quarantined with a reason, counted, and surfaced, because
    silent partial data is how a PM ends up trusting a health score computed on
    60% of their project.
    """
    result = SheetRead(sha256=sha256_file(path))

    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            result.rejects.append(
                Rejection(
                    row_index=0,
                    raw_row={},
                    reason=(
                        f"sheet {sheet_name!r} not found; "
                        f"workbook has {workbook.sheetnames}"
                    ),
                )
            )
            return result

        # Read once into plain value tuples. In read-only mode a blank cell comes
        # back as EmptyCell, which carries no position of its own, so working from
        # tuples keeps column alignment coming from one place: the tuple index.
        grid: list[tuple] = list(
            workbook[sheet_name].iter_rows(values_only=True)
        )
    finally:
        workbook.close()

    header_row, columns = _find_header_row(grid, contract)

    if header_row is None:
        result.rejects.append(
            Rejection(
                row_index=0,
                raw_row={},
                reason=(
                    "no header row found in the first "
                    f"{_HEADER_SEARCH_ROWS} rows; expected columns like "
                    f"{sorted(contract.columns)[:4]}"
                ),
            )
        )
        return result

    # Report in 1-based sheet coordinates, so a reject message points at the row
    # a PM can actually find in Excel.
    result.header_row = header_row + 1
    result.has_key_column = contract.key_field in set(columns.values())

    # Headers present in the sheet that the contract does not know about.
    # Reported rather than ignored: an unknown header is usually a rename we need
    # to hear about while it is still one sheet and not twenty.
    for value in grid[header_row]:
        header = normalize_header(value)
        if header and header not in contract.columns:
            result.unknown_headers.append(header)

    for offset, row in enumerate(grid[header_row + 1 :], start=header_row + 2):
        payload: dict[str, Any] = {}
        for position, value in enumerate(row):
            canonical = columns.get(position)
            if canonical:
                payload[canonical] = value

        if not any(v is not None and str(v).strip() for v in payload.values()):
            continue  # blank spacer row

        result.rows.append((offset, payload))

    return result


# --------------------------------------------------------------------------
# The contracts. These describe the templates we control.
# --------------------------------------------------------------------------

SCHEDULE_CONTRACT = SheetContract(
    name="schedule",
    key_field="task_id",
    title_field="title",
    # Only fields whose movement is a delivery event. A Notes column changing is
    # not something a causal chain should ever be built on.
    tracked_fields=("status", "planned_end", "progress", "assignee", "milestone"),
    entity_type="task",
    # What a blank schedule template carries, in this order. `gen_demo_data`
    # writes the same list, so the demo file and the template a judge downloads
    # cannot describe two different sheets.
    template_headers=(
        "Task ID",
        "Activity",
        "Phase",
        "Milestone",
        "Status",
        "Owner",
        "Start",
        "Baseline Finish",
        "Planned Finish",
        "Progress",
        # The column that closes the dependency-edge gap (architecture 5.4). We
        # own this template, so it is the cheapest real source of DAG edges.
        "Predecessor",
        # What the source system says about when this row last moved. Optional
        # and usually blank on a hand-kept sheet - a person editing a workbook
        # is not going to maintain it - but every issue tracker has one, and it
        # is the only movement signal a *single* export carries: with no earlier
        # scan there is nothing for the differ to compare against yet.
        "Last Updated",
    ),
    columns={
        "task id": "task_id",
        "wbs": "task_id",
        "activity": "title",
        "activity name": "title",
        "title": "title",
        "phase": "phase",
        "milestone": "milestone",
        "status": "status",
        "owner": "assignee",
        "assignee": "assignee",
        "start": "start_date",
        "start date": "start_date",
        "last updated": "source_updated_at",
        "updated": "source_updated_at",
        "baseline finish": "baseline_end",
        "baseline completion": "baseline_end",
        "planned finish": "planned_end",
        "planned completion": "planned_end",
        "progress": "progress",
        "% complete": "progress",
        "predecessor": "predecessor",
        "predecessors": "predecessor",
    },
)

WORKLOG_CONTRACT = SheetContract(
    name="worklog",
    key_field="task_id",
    title_field="title",
    tracked_fields=("status", "blocked", "hours_spent", "assignee"),
    entity_type="qa_item",
    template_headers=(
        "Task ID",
        "Summary",
        "Status",
        "Blocked",
        "Owner",
        # Planned effort beside actual, at the same grain, plus the date the
        # hours were logged. A burn chart needs all three: a total with no
        # baseline and no time axis cannot be drawn honestly.
        "Estimate",
        "Hours",
        "Date",
    ),
    columns={
        "task id": "task_id",
        "ticket": "task_id",
        "summary": "title",
        "title": "title",
        "status": "status",
        "blocked": "blocked",
        "blocked by": "blocked_by",
        "owner": "assignee",
        "assignee": "assignee",
        "hours": "hours_spent",
        "hours spent": "hours_spent",
        "estimate": "estimate_hours",
        "estimated hours": "estimate_hours",
        "date": "log_date",
    },
)

CONTRACTS = {c.name: c for c in (SCHEDULE_CONTRACT, WORKLOG_CONTRACT)}
