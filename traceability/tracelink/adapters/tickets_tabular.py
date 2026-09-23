"""Stage 0a — read a backlog out of a tabular tracker export (.xlsx / .csv).

Generalised from the demo export in three ways:

* **Columns are matched by alias, not position.** Trackers rename these
  constantly (`Component/s`, `Components`, `Component`).
* **The header row is found, not assumed.** Jira's "Excel (All fields)"
  export puts preamble rows above the header, and the sheet is often not
  the first one.
* **The parent/sub-row convention is detected, not hardcoded.** In the demo
  export the real feature rows are the ones with *no* key, nested under
  keyed PM tickets. That is a real convention, but it is not universal, so
  we measure which shape the sheet has and say which one we picked.
"""

from __future__ import annotations

import csv
import re
from datetime import date, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from tracelink.artifacts import Ticket

# Column aliases, lowercased and stripped of punctuation for matching.
ALIASES: dict[str, tuple[str, ...]] = {
    "key": ("key", "issue key", "id", "issue id", "task id", "item id",
            "ticket id", "work item id", "wbs", "wbs id"),
    "summary": ("summary", "title", "name", "subject", "activity", "task",
                "task name", "work item", "story", "feature", "requirement"),
    "description": ("description", "details", "body", "acceptance criteria",
                    "notes", "note"),
    "component": ("components", "component s", "component", "module", "area",
                  "phase", "workstream"),
    "status": ("status", "state", "workflow status", "progress status"),
    "project": ("project", "project name", "project key"),
    "issue_type": ("issue type", "type", "work item type"),
    "fix_version": ("fix version s", "fix versions", "fix version", "release",
                    "milestone", "sprint"),
    "assignee": ("assignee", "owner", "assigned to", "responsible", "pic"),
}

#: Without a title column there is nothing to trace, so this is the one
#: field the reader refuses to proceed without.
REQUIRED = ("summary",)

_PUNCT = re.compile(r"[^a-z0-9]+")

# `PO: alice`, `Ngày nhận: 46244` — a label then a value, one per line or
# separated by | or ;. Teams fall back to this whenever the tracker's own
# column for something is not on the screen they type into.
_INLINE = re.compile(r"^\s*([^\s:][^:\n]{0,30}?)\s*:\s*(.+?)\s*$")
# Spreadsheet date serials in the plausible range for a modern project.
_SERIAL = re.compile(r"^(4[0-9]{4}|5[0-9]{4})$")
_EXCEL_EPOCH = date(1899, 12, 30)


def repair(text: str) -> str:
    """Undo UTF-8 that was decoded as cp1252 on its way into the sheet.

    The export carries "environment,â€¦" and "[Project code] â€“ Request
    review" — an em dash and an ellipsis whose bytes were read as Latin-1.
    Repaired by the round trip that created them, and only kept when it
    succeeds, because text that was never mangled must come back untouched.
    """
    if not text or "Ã" not in text and "â" not in text:
        return text
    try:
        fixed = text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return fixed


def parse_inline_fields(text: str) -> dict[str, str]:
    """Pull `Label: value` pairs out of a free-text field.

    Values that are bare spreadsheet date serials are decoded, because a
    date stored as `46244` is unreadable to a person and invisible to every
    date filter in the tracker. The raw value is kept alongside so nothing
    is silently reinterpreted.
    """
    out: dict[str, str] = {}
    if not text:
        return out
    for line in re.split(r"[\n;|]", text):
        m = _INLINE.match(line)
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        if not label or not value:
            continue
        out[label] = value
        if _SERIAL.match(value):
            try:
                out[f"{label} (date)"] = str(
                    _EXCEL_EPOCH + timedelta(days=int(value)))
            except (ValueError, OverflowError):
                pass
    return out


def _canon(header: Any) -> str:
    return _PUNCT.sub(" ", str(header or "").strip().lower()).strip()


def column_named(headers: Iterable[Any], field: str) -> str | None:
    """The header that means `field`, or None if the export has no such column.

    Aliases are tried in the order `ALIASES` lists them, most specific
    first, because a real export is full of near-misses: this project's
    spreadsheet carries twenty-odd headers containing the word "Type"
    (`Risk Type`, `Enabler Type`, a bare `Type`, all empty) alongside the
    `Issue Type` that actually holds the value. Scanning headers in sheet
    order would return whichever came first; scanning aliases in order
    returns the one that was named precisely.
    """
    seen: dict[str, Any] = {}
    for header in headers:
        seen.setdefault(_canon(header), header)
    for alias in ALIASES.get(field, ()):
        if alias in seen:
            return seen[alias]
    return None


@dataclass
class SheetShape:
    """What we concluded about the export's layout — reported, not hidden."""

    header_row: int
    sheet: str
    columns: dict[str, int]
    convention: str          # "unkeyed-subrows" | "flat"
    n_keyed: int
    n_unkeyed: int
    projects: dict[str, int]

    def describe(self) -> str:
        cols = ", ".join(sorted(self.columns))
        return (
            f"sheet={self.sheet!r} header_row={self.header_row} "
            f"convention={self.convention} (keyed={self.n_keyed}, "
            f"unkeyed={self.n_unkeyed})\n  columns matched: {cols}"
        )


def _rows_from_xlsx(path: Path) -> Iterator[tuple[str, list[list[Any]]]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        yield ws.title, [list(r) for r in ws.iter_rows(values_only=True)]


def _rows_from_csv(path: Path) -> Iterator[tuple[str, list[list[Any]]]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        yield path.stem, [list(r) for r in csv.reader(fh)]


def _score_header(row: Sequence[Any]) -> tuple[int, dict[str, int]]:
    """How many of our known fields this row looks like a header for."""
    found: dict[str, int] = {}
    for i, cell in enumerate(row):
        c = _canon(cell)
        if not c:
            continue
        for field, names in ALIASES.items():
            if c in names and field not in found:
                found[field] = i
    return len(found), found


def find_shape(path: Path, min_fields: int = 2) -> SheetShape:
    """Locate the sheet and header row, and work out the row convention."""
    reader = _rows_from_xlsx if path.suffix.lower() in (".xlsx", ".xlsm") else _rows_from_csv

    best: tuple[int, str, int, dict[str, int], list[list[Any]]] | None = None
    for sheet, rows in reader(path):
        for idx, row in enumerate(rows[:50]):   # headers live near the top
            n, cols = _score_header(row)
            if n >= min_fields and (best is None or n > best[0]):
                best = (n, sheet, idx, cols, rows)
    if best is None:
        raise ValueError(
            f"{path}: no header row found. Expected a row naming at least "
            f"{min_fields} of: {', '.join(sorted(ALIASES))}"
        )

    _, sheet, header_row, cols, rows = best

    # A header can match two aliases and still be unusable. A schedule export
    # headed `Task ID | Activity | Phase | Status | Owner` once matched only
    # `status` and `assignee`, so every row was skipped for having no title
    # and the reader reported "no rows matched" - true, useless, and it
    # pointed at the wrong thing. Name the missing field and show what was
    # actually on the header row.
    missing = [f for f in REQUIRED if f not in cols]
    if missing:
        seen = [str(h) for h in rows[header_row] if h not in (None, "")]
        raise ValueError(
            f"{path}: found a header row on sheet {sheet!r} but no "
            f"{'/'.join(missing)} column. Headers seen: "
            f"{', '.join(seen[:12])}"
            f"{' …' if len(seen) > 12 else ''}. "
            f"Add the column's name to ALIASES[{missing[0]!r}] in "
            f"adapters/tickets_tabular.py."
        )
    body = rows[header_row + 1:]

    def cell(row: Sequence[Any], field: str) -> str:
        i = cols.get(field)
        if i is None or i >= len(row):
            return ""
        return str(row[i]).strip() if row[i] is not None else ""

    keyed = unkeyed = 0
    projects: dict[str, int] = {}
    for row in body:
        if not any(c not in (None, "") for c in row):
            continue
        if not cell(row, "summary"):
            continue
        if cell(row, "key"):
            keyed += 1
        else:
            unkeyed += 1
        p = cell(row, "project")
        if p:
            projects[p] = projects.get(p, 0) + 1

    # The demo's shape: substantive work described in rows that carry no key,
    # grouped under keyed umbrella rows. Only believe it when the unkeyed
    # rows genuinely dominate — otherwise a few blank keys are just gaps.
    convention = "unkeyed-subrows" if unkeyed > keyed * 2 and unkeyed >= 10 else "flat"

    return SheetShape(header_row=header_row, sheet=sheet, columns=cols,
                      convention=convention, n_keyed=keyed, n_unkeyed=unkeyed,
                      projects=projects)


def read_raw(
    path: str | Path,
    project: str | None = None,
    keyed: bool | None = None,
) -> tuple[list[dict[str, Any]], list[str], SheetShape]:
    """Every column of every substantive row, untouched.

    `read_tickets` deliberately keeps only the fields the pipeline models.
    The diagnostic needs the opposite: the whole sheet, so it can report
    what is in the 400 columns nobody mapped.
    """
    path = Path(path)
    shape = find_shape(path)
    reader = _rows_from_xlsx if path.suffix.lower() in (".xlsx", ".xlsm") else _rows_from_csv

    header: list[str] = []
    body: list[list[Any]] = []
    for sheet, rs in reader(path):
        if sheet != shape.sheet:
            continue
        header = [str(h).strip() if h is not None else "" for h in rs[shape.header_row]]
        body = rs[shape.header_row + 1:]
        break

    names = [h for h in header if h]
    key_i = shape.columns.get("key")
    sum_i = shape.columns.get("summary")
    proj_i = shape.columns.get("project")

    out: list[dict[str, Any]] = []
    for row in body:
        if not any(c not in (None, "") for c in row):
            continue
        if sum_i is None or sum_i >= len(row) or row[sum_i] in (None, ""):
            continue
        if project and proj_i is not None and proj_i < len(row):
            if row[proj_i] and str(row[proj_i]).strip() != project:
                continue
        has_key = bool(key_i is not None and key_i < len(row)
                       and row[key_i] not in (None, ""))
        if keyed is not None and has_key != keyed:
            continue
        out.append({header[i]: row[i] for i in range(len(header))
                    if header[i] and i < len(row)})
    return out, names, shape


def read_tickets(
    path: str | Path,
    project: str | None = None,
    convention: str | None = None,
) -> tuple[list[Ticket], SheetShape]:
    """Read a backlog. `project` filters; `convention` overrides detection."""
    path = Path(path)
    shape = find_shape(path)
    if convention:
        shape.convention = convention

    reader = _rows_from_xlsx if path.suffix.lower() in (".xlsx", ".xlsm") else _rows_from_csv
    rows: list[list[Any]] = []
    for sheet, rs in reader(path):
        if sheet == shape.sheet:
            rows = rs[shape.header_row + 1:]
            break

    cols = shape.columns

    def cell(row: Sequence[Any], field: str) -> str:
        i = cols.get(field)
        if i is None or i >= len(row):
            return ""
        return str(row[i]).strip() if row[i] is not None else ""

    tickets: list[Ticket] = []
    parent: str | None = None
    # `offset` converts an index into `rows` (the body) back to the row number
    # a spreadsheet shows: header row is 0-based here, 1-based there, and the
    # body starts one row after it.
    offset = shape.header_row + 2
    for index, row in enumerate(rows):
        summary = repair(cell(row, "summary"))
        key = cell(row, "key") or None
        proj = cell(row, "project")

        if project and proj and proj != project:
            continue
        if not summary:
            # A keyed row with no summary still sets the parent context.
            if key:
                parent = key
            continue

        if shape.convention == "unkeyed-subrows" and key:
            parent = key
            continue   # umbrella row, not a feature claim

        tickets.append(Ticket(
            uid=f"T{len(tickets):04d}",
            key=key,
            parent=parent,
            summary=summary,
            description=repair(cell(row, "description")),
            component=cell(row, "component") or "None",
            status=cell(row, "status"),
            source_row=index + offset,
            source_sheet=shape.sheet,
            inline=parse_inline_fields(repair(cell(row, "description"))),
            extra={k: cell(row, k) for k in ("issue_type", "fix_version", "assignee")
                   if cols.get(k) is not None},
        ))

    return tickets, shape
