"""A Jira issue export, converted into the schedule sheet this app reads.

Jira's "general_report" export is not a sheet `app/ingest/sources/excel` can
read, and the gap is not only the column names. Three things differ:

**Its table does not start at row 1.** There is a filter name, a "Displaying N
issues at ..." line, and then the header. `reader.find_sheet` already searches
for a header row rather than assuming one, so that half would have worked.

**One issue is not one row.** An export with a rich-text Description writes each
issue across a block of rows, the fields on the first and the wrapped text
below. Rows are therefore collected by "has a Key", never by position or stride.

**Four hundred columns, and the ones this app needs are mostly not there.** The
export carries every custom field the Jira instance defines - most empty,
several holding the page's own JavaScript rather than a value.

What this produces is the blank schedule template's own column list
(`SCHEDULE_CONTRACT.template_headers`), so the output is the same document a
person would get from `GET /api/template/schedule.xlsx` and fill in by hand -
which means the upload route, the differ, the identity resolver and every
downstream reader treat it exactly like any other schedule sheet, with no
second ingestion path to keep in step.

**It fills the columns the export actually has and leaves the rest empty rather
than inventing them**, and `coverage()` reports what did not come across. That
is the load-bearing part: a Jira export cannot light up most of this product,
and finding that out from a blank dashboard is worse than being told up front.

- **No baseline.** Jira has no "originally committed to" field, so recorded slip
  is 0. It is not knowable from *one* export - only from two taken at different
  times, which is precisely what the differ compares. So a second upload later
  genuinely does start producing it.
- **No dependency edges** unless `Linked Issues` / `Sub-Tasks` are populated.
  The projected finish, propagated slip, the driving path, `days_late` and
  milestones-at-risk are all forward-pass results over a DAG.
- **No effort** unless the estimate columns are filled, and those belong in a
  *worklog* sheet rather than this one.

Lives in `app/` rather than in `scripts/` because two entry points need it: the
CLI (`scripts.from_jira_export`) and `POST /api/sources/upload`, which is the
only one a person on the deployed app can reach.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT

#: How far down to look for the header row. Generous because the preamble is a
#: report title and a "Displaying N issues" line today, and nothing promises a
#: different filter will not add a third.
HEADER_SEARCH_ROWS = 25

#: Which Jira column feeds each column of the schedule template, most-preferred
#: first. Matched case-insensitively on the header text.
#:
#: Several entries list more than one name because "the" Jira column depends on
#: how the instance was configured: `Due Date` is the system field and `End
#: date` the custom one an Advanced-Roadmaps instance uses instead, and a given
#: export usually has exactly one of them filled.
SOURCES: dict[str, tuple[str, ...]] = {
    "Task ID": ("Key",),
    "Activity": ("Summary",),
    "Phase": ("Issue Type",),
    # A parent/epic is a *grouping*, and Milestone is this contract's grouping
    # column - so an epic becomes the band its children sit under on the Gantt,
    # which is what "NOT UNDER A MILESTONE" was showing before. It is emphatically
    # not a dependency: a parent says these belong together, never that one waits
    # for another, and putting it in `Predecessor` would fabricate a chain.
    #
    # `Product` is last because it is only a parent link on instances configured
    # that way; the standard fields win when present.
    "Milestone": ("Parent", "Parent Link", "Epic Link", "Epic Name", "Fix Version/s", "Product"),
    "Status": ("Status",),
    "Owner": ("Assignee",),
    "Start": ("Planned Start", "Start date", "Start Date"),
    # Deliberately fed by nothing - Jira has no baseline field. Listed anyway so
    # the coverage report names it rather than leaving a reader to notice.
    "Baseline Finish": (),
    "Planned Finish": ("Due Date", "End date"),
    "Progress": ("Task progress", "Progress"),
    # `Linked Issues` only. `Sub-Tasks` was here and was simply wrong: a
    # sub-task is a *child*, not a predecessor. Nothing showed it while the
    # column was empty on every row, and the day it filled in this would have
    # asserted that a parent waits for its children - a chain nobody stated,
    # feeding a critical path and a projected date.
    "Predecessor": ("Linked Issues",),
    "Last Updated": ("Updated", "Last Viewed"),
}

#: A Jira issue key, anywhere in a cell.
ISSUE_KEY = re.compile(r"\b([A-Z][A-Z0-9_]*-\d+)\b")

#: The phrases that mean "this issue comes *after* the one named".
#:
#: Direction is the whole problem with link columns. "blocks PROJ-3" and "is
#: blocked by PROJ-3" name the same pair and opposite edges, and a predecessor
#: column that takes both produces a graph with half its arrows reversed - which
#: does not fail, it just returns a confident and wrong critical path. So only
#: inbound phrasing is accepted, and a bare key with no direction word is
#: dropped rather than guessed at, the same way `dependencies.py` drops a
#: dangling reference instead of hedging it.
INBOUND_LINK = re.compile(
    r"\b(is\s+blocked\s+by|blocked\s+by|depends\s+on|is\s+depended\s+on\s+by"
    r"|follows|is\s+after|after)\b",
    re.I,
)

#: The opposite phrasings, recognised only so they can be reported as skipped
#: rather than silently ignored - a person who filled in a whole column of
#: "blocks" links deserves to be told why no edges came of it.
OUTBOUND_LINK = re.compile(r"\b(blocks|is\s+blocking|precedes|is\s+before|before)\b", re.I)

#: Jira renders some custom fields by shipping the page's own script, so the
#: cell holds a function body rather than a value. Any cell that looks like this
#: is dropped rather than written through - a task whose Owner is a jQuery call
#: is worse than a task with no Owner.
SCRIPT_SMELL = re.compile(r"\$\(|function\s*\(|setTimeout|document\.ready", re.I)

#: Same idea for Jira's own rendering failures, which arrive as prose.
ERROR_SMELL = re.compile(r"^Error rendering ", re.I)

DATE_COLUMNS = frozenset({"Start", "Planned Finish", "Baseline Finish", "Last Updated"})


class NotAJiraExport(ValueError):
    """The workbook has no Jira issue table in it.

    Its own exception so the API can answer 400 with the reason rather than
    500, and the CLI can exit with a message - the two disagreed when this was
    a bare `SystemExit` inside a script.
    """


#: Jira writes a parent as `Some Name [PROJ-123]`. The key is noise in a
#: grouping label - the band on a chart wants "Management", not
#: "Management [COWORKLOCAL-1]" - and the issue it names is in the export
#: anyway, under its own row.
PARENT_KEY = re.compile(r"\s*\[[A-Z][A-Z0-9_]*-\d+\]\s*$")


def _clean(value):
    """One cell, as a value or not at all."""
    if value is None:
        return None
    if isinstance(value, (int, float, date, datetime)):
        return value
    text = str(value).strip()
    if not text or text.casefold() in {"none", "unresolved", "n/a"}:
        return None
    if SCRIPT_SMELL.search(text) or ERROR_SMELL.match(text):
        return None
    return text


def _as_date(value):
    """A date, or None. Jira writes datetimes; a schedule column wants a day.

    The time is dropped rather than kept because this app compares dates, and
    carrying `19:22` into a planned-finish column would suggest the plan is
    precise to the minute when the PM chose a day.
    """
    value = _clean(value)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _as_percent(value):
    """Jira progress as the 0-100 the schedule sheet uses, or nothing.

    Accepts `60`, `60%`, `"60 %"`. Rejects anything outside 0-100 rather than
    rescaling it: Jira's own aggregate progress fields are in *seconds*, and a
    silently divided 43200 would render as a plausible completion figure.
    """
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace("%", "").strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 100 else None


def _predecessors(cells: list) -> str | None:
    """The issues this one comes *after*, as the schedule sheet writes them.

    Comma-separated keys, which is what `dependencies.py` parses. Only inbound
    phrasing contributes - see `INBOUND_LINK` for why a bare key is dropped
    instead of guessed at.
    """
    keys: list[str] = []
    for cell in cells:
        text = _clean(cell)
        if not isinstance(text, str) or not INBOUND_LINK.search(text):
            continue
        for key in ISSUE_KEY.findall(text):
            if key not in keys:
                keys.append(key)
    return ", ".join(keys) if keys else None


def _find_header(grid: list[tuple]) -> int | None:
    """The row holding the export's column names.

    Identified by containing `Key` *and* `Summary`, not by position and not by
    `Key` alone - a preamble line can easily contain one word.
    """
    for index, row in enumerate(grid):
        labels = {str(c).strip().casefold() for c in row if c is not None}
        if "key" in labels and "summary" in labels:
            return index
    return None


def read_export(source, sheet: str | None = None) -> tuple[list[str], list[tuple]]:
    """`(headers, issue_rows)` from a Jira export, whichever tab holds it.

    `source` is a path or a file-like object, so the API can convert an upload
    without writing it to disk first - nothing is stored until the document has
    been accepted.
    """
    workbook = load_workbook(source, data_only=True, read_only=True)
    try:
        names = ([sheet] if sheet else []) + [
            n for n in workbook.sheetnames if n != sheet
        ]
        for name in names:
            worksheet = workbook[name]
            grid = []
            for index, row in enumerate(worksheet.iter_rows(values_only=True)):
                if index >= HEADER_SEARCH_ROWS:
                    break
                grid.append(row)
            header_at = _find_header(grid)
            if header_at is None:
                continue

            headers = [("" if c is None else str(c).strip()) for c in grid[header_at]]
            key_at = [h.casefold() for h in headers].index("key")

            rows = []
            for index, row in enumerate(worksheet.iter_rows(values_only=True)):
                if index <= header_at:
                    continue
                # One issue is one row *with a Key*. Everything between is the
                # previous issue's wrapped rich text.
                if key_at < len(row) and _clean(row[key_at]):
                    rows.append(row)
            return headers, rows
    finally:
        workbook.close()
    raise NotAJiraExport(
        "no Jira issue table found - no header row with both 'Key' and 'Summary'. "
        "Export from Jira with the 'Excel (All fields)' option."
    )


def _index(headers: list[str]) -> dict[str, list[int]]:
    """Header -> every column position carrying it.

    Jira repeats a header rather than packing a list into one cell: an issue
    with four approvers produces four `Approver` columns. This export already
    has `Approver` x4 and `Watchers` x2, so a dict keeping one position per
    header silently reads the last one - and the day `Linked Issues` is filled
    in, an issue with three links would contribute one edge and lose two.
    """
    positions: dict[str, list[int]] = {}
    for at, header in enumerate(headers):
        if header:
            positions.setdefault(header.casefold(), []).append(at)
    return positions


def convert(headers: list[str], rows: list[tuple]) -> tuple[list[dict], dict[str, str]]:
    """`(records, chosen)` - the template rows, and which Jira column fed each."""
    lookup = _index(headers)

    def _cells(row: tuple, name: str) -> list:
        """Every value this row carries under `name`, across repeated columns."""
        return [
            row[at] for at in lookup[name.casefold()] if at < len(row) and _clean(row[at])
        ]

    def _populated(name: str) -> int:
        """How many issues carry a value in any column of this name."""
        return sum(1 for r in rows if _cells(r, name))

    #: The candidate with the most data wins, not the first one that exists.
    #:
    #: Presence order was wrong and quietly so. A Jira instance defines every
    #: standard field whether or not anybody fills it, so this export carries an
    #: empty `Parent Link` column *and* a populated `Product` one - and picking
    #: by presence chose the empty one, reporting "Milestone 0/17 from 'Parent
    #: Link'" as though the export had no parents. It has 16.
    #:
    #: Ties keep the declared order, so the preference in `SOURCES` still decides
    #: between two columns that are equally filled - which is the case it was
    #: written for (`Due Date` vs `End date`, where an instance uses one or the
    #: other).
    chosen: dict[str, str] = {}
    for column, candidates in SOURCES.items():
        present = [c for c in candidates if c.casefold() in lookup]
        if not present:
            continue
        best = max(present, key=lambda name: (_populated(name), -present.index(name)))
        if _populated(best) or not rows:
            chosen[column] = best
        else:
            # Every candidate is empty. Name the preferred one anyway so the
            # coverage report can say "0/17 from 'Due Date'" rather than going
            # silent about a column nobody filled.
            chosen[column] = present[0]

    records = []
    for row in rows:
        record = {}
        for column in SCHEDULE_CONTRACT.template_headers:
            source = chosen.get(column)
            if source is None:
                record[column] = None
                continue
            cells = _cells(row, source)
            if column in DATE_COLUMNS:
                record[column] = _as_date(cells[0]) if cells else None
            elif column == "Predecessor":
                record[column] = _predecessors(cells)
            elif column == "Progress":
                record[column] = _as_percent(cells[0]) if cells else None
            elif column == "Milestone":
                value = _clean(cells[0]) if cells else None
                record[column] = (
                    PARENT_KEY.sub("", value) or None if isinstance(value, str) else value
                )
            else:
                record[column] = _clean(cells[0]) if cells else None
        records.append(record)
    return records, chosen


def _build(records: list[dict]) -> Workbook:
    """The blank template's own shape, filled in.

    The tab is named `Activities` because that is what the contract prefers and
    `find_sheet` tries first - it would resolve a differently-named tab too, but
    only by searching, and there is no reason to make it search.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Activities"
    sheet.append(list(SCHEDULE_CONTRACT.template_headers))
    for record in records:
        sheet.append([record[h] for h in SCHEDULE_CONTRACT.template_headers])
    for index, header in enumerate(SCHEDULE_CONTRACT.template_headers, start=1):
        column = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[column].width = max(12, len(header) + 2)
    return workbook


def write_schedule(records: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    _build(records).save(out)


def to_bytes(records: list[dict]) -> bytes:
    """The same workbook as `write_schedule`, in memory.

    What the upload route needs: the converted sheet has to reach the existing
    ingestion path as bytes, exactly as if a person had picked a schedule
    workbook from their disk.
    """
    buffer = BytesIO()
    _build(records).save(buffer)
    return buffer.getvalue()


def coverage(
    records: list[dict], chosen: dict[str, str], headers: list[str], rows: list[tuple]
) -> dict:
    """What came across, what did not, and what that costs.

    Returned as data rather than printed so the CLI and the API can say the same
    thing in their own shapes. `notes` is the list a person needs to read: each
    entry is a limitation of the *export*, not of the conversion, and no better
    mapping removes any of them.
    """
    total = len(records)
    columns = []
    for column in SCHEDULE_CONTRACT.template_headers:
        filled = sum(1 for r in records if r[column] not in (None, ""))
        columns.append(
            {
                "column": column,
                "filled": filled,
                "of": total,
                "source": chosen.get(column),
            }
        )

    missing = {c["column"] for c in columns if not c["filled"]}
    notes: list[str] = []

    if "Baseline Finish" in missing:
        notes.append(
            "No baseline: Jira has no 'originally committed to' field, so recorded "
            "slip will read 0. It is only recoverable from two exports taken at "
            "different times - upload a later one and the differ will observe it."
        )
    if "Predecessor" in missing:
        #: Distinguish "no links at all" from "links that name the wrong
        #: direction", because the second is fixable in Jira in a minute and the
        #: first is not, and a single note for both would send a person looking
        #: in the wrong place.
        linked = chosen.get("Predecessor")
        outbound = 0
        if linked and linked.casefold() in {h.casefold() for h in headers if h}:
            positions = _index(headers).get(linked.casefold(), [])
            outbound = sum(
                1
                for r in rows
                for at in positions
                if at < len(r)
                and isinstance(_clean(r[at]), str)
                and OUTBOUND_LINK.search(str(r[at]))
                and not INBOUND_LINK.search(str(r[at]))
            )
        if outbound:
            notes.append(
                f"{outbound} issue link(s) name the outbound direction only "
                "(\"blocks\", \"precedes\"). A predecessor column has to say what "
                "an issue comes *after*, and taking both directions would reverse "
                "half the arrows and produce a confident, wrong critical path - so "
                "they were skipped. Adding the matching \"is blocked by\" link in "
                "Jira turns them into edges."
            )
        else:
            notes.append(
                "No dependency edges: no issue carries an inbound link (\"is "
                "blocked by\", \"depends on\"). The projected finish, propagated "
                "slip, the driving path, days-late and milestones-at-risk are all "
                "forward-pass results over a DAG, so without them every task's "
                "projected date equals its planned one."
            )

    start_source = chosen.get("Start")
    lookup = {h.casefold(): i for i, h in enumerate(headers) if h}
    if start_source and "created" in lookup:
        s, c = lookup[start_source.casefold()], lookup["created"]
        pairs = [
            (r[s], r[c])
            for r in rows
            if s < len(r) and c < len(r) and r[s] is not None and r[c] is not None
        ]
        if pairs and all(a == b for a, b in pairs):
            notes.append(
                f"{start_source!r} equals 'Created' on all {len(pairs)} rows that "
                "have it. That is a field default, not a planned start - it was "
                "written when the issue was raised. Carried across because it is "
                "what the export says; do not read Start as a plan."
            )

    owners = {r["Owner"] for r in records if r["Owner"]}
    if len(owners) <= 1:
        notes.append(
            f"{len(owners)} distinct owner(s). Resource contention is computed "
            "across projects sharing a person, so this project contributes nothing "
            "to a program rollup until other projects name the same people."
        )

    return {"issues": total, "columns": columns, "notes": notes}


def convert_workbook(source, sheet: str | None = None) -> tuple[bytes, dict]:
    """A Jira export in, a schedule workbook and its coverage report out.

    The one call both entry points make, so the CLI and the upload route cannot
    convert the same file two different ways.
    """
    headers, rows = read_export(source, sheet)
    records, chosen = convert(headers, rows)
    if not records:
        raise NotAJiraExport(
            "the export has a header row but no issues under it - nothing to load."
        )
    return to_bytes(records), coverage(records, chosen, headers, rows)
