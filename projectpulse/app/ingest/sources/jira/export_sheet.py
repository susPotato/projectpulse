"""A Jira issue export, converted into the schedule sheet this app reads.

Jira's "general_report" export is not a sheet `app/ingest/sources/excel` can
read, and the gap is not only the column names. Three things differ:

**Its table does not start at row 1.** There is a filter name, a "Displaying N
issues at ..." line, and then the header. `reader.find_sheet` already searches
for a header row rather than assuming one, so that half would have worked.

**One issue is not one row.** An export with a rich-text Description writes each
issue across a block of rows, the fields on the first and the wrapped text
below. Rows are therefore collected by what they carry, never by position or
stride: a Key, or - for a backlog somebody appended *below* the export, which
Jira never wrote and so never numbered - a Summary and a Status together, which
no wrapping produces. The second kind gets a derived `Task ID`; see
`_synthetic_id`, and `coverage()` says how many, because reading rows Jira did
not write changes what every count downstream is counting.

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
from hashlib import sha1
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT, WORKLOG_CONTRACT

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
    # The issue body. Carried for the advisory lane in `app/risks/drafts.py`
    # and for a reader; no rule reads it, and the differ does not track it.
    #
    # Worth having even though it is the column most likely to be long: on the
    # export this was written against it is the *only* field that says what a
    # task actually involves, and it was being discarded on 189 of 191 rows.
    "Description": ("Description",),
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

#: A link, which a grouping label never is.
URL = re.compile(r"^\s*(https?://|www\.)", re.I)


def _looks_like_parent(value) -> bool:
    """Does this value look like a reference to a parent issue?

    Jira writes one as `Some Name [PROJ-123]`, or occasionally as a bare key.
    Anything else - a URL, a person's name, free text - is some other field that
    happens to sit under a parent-ish header.
    """
    text = _clean(value)
    return isinstance(text, str) and bool(
        PARENT_KEY.search(text) or re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", text.strip())
    )


def _looks_like_link(value) -> bool:
    """Does this value name an issue at all? A predecessor column that does not
    is not one."""
    text = _clean(value)
    return isinstance(text, str) and bool(ISSUE_KEY.search(text))


#: How to recognise a column that means what its header claims, where "not
#: empty" is not enough to tell.
#:
#: Jira defines every standard field whether or not it holds anything useful, so
#: an export can carry a *populated* `Parent Link` holding a documentation URL
#: beside a *populated* `Product` holding the real `Management [COWORKLOCAL-1]`.
#: Ranking on non-emptiness alone picked the URL and made it a milestone name.
SHAPE = {
    "Milestone": _looks_like_parent,
    "Predecessor": _looks_like_link,
}

#: A candidate that is identical to this other column, on every row that has it,
#: is that column wearing a different name - and loses to any candidate that is
#: not.
#:
#: `Planned Start` is the case: on an instance that does not use it, Jira stamps
#: it at creation and never touches it, so it equals `Created` everywhere. The
#: coverage report already said so - and the conversion went on choosing it over
#: a populated `Start date` holding real, different dates, which meant warning
#: about the very value it had just used. Detecting it and then preferring it
#: anyway is worse than not detecting it.
DEMOTE_IF_EQUALS = {"Start": "Created"}

#: Where to look for a grouping label on a row whose chosen source has none.
#:
#: Deliberately a *fallback* rather than another entry in `SOURCES`. `SHAPE`
#: gates the Milestone candidates on looking like a parent reference, and that
#: gate is load-bearing - it is what stops a documentation URL in `Parent Link`
#: becoming a milestone name, and `Component/s` would never pass it. Widening
#: the gate to admit free text would give that bug its column back.
#:
#: So the ranking is untouched: a real parent still wins, and still names the
#: band for every row that has one. This only answers the row that has *none* -
#: an appended backlog row, which carries no parent and cannot, but does carry
#: the feature area it belongs to. The alternative is what it replaced: 174 of
#: 190 rows stacked under "NOT UNDER A MILESTONE", which is a grouping column
#: doing no grouping.
#:
#: A component is not a parent issue and the two do land in one column here.
#: They are the same *kind* of thing for the only purpose this column serves -
#: the band a row sits under on the Gantt - and nothing downstream reads a
#: milestone as an issue that could be opened.
GROUPING_FALLBACK: dict[str, tuple[str, ...]] = {
    "Milestone": ("Component/s", "Component", "Fix Version/s"),
}

#: The same export, read as a *worklog* instead of a schedule.
#:
#: Jira carries effort per issue - `Original Estimate` against `Time Spent` -
#: and that is exactly the pair `app/api/schemas/team.py` argues is the only
#: honest effort comparison available, because both are values a person entered
#: rather than a ratio derived from self-reported progress. Without this the
#: Jira path could describe a schedule and never say what it cost.
#:
#: One export therefore feeds two sheets. That is not duplication: an issue row
#: genuinely carries both a plan (dates, links) and a record of effort, and the
#: app keeps those in separate contracts because a spreadsheet-shop keeps them
#: in separate files.
WORKLOG_SOURCES: dict[str, tuple[str, ...]] = {
    "Task ID": ("Key",),
    "Summary": ("Summary",),
    "Status": ("Status",),
    # Jira has no single "blocked" column. `Flagged` is the closest thing that
    # means it unambiguously; a status *called* Blocked is handled by the
    # convertor's own vocabulary, so nothing is guessed at here.
    "Blocked": ("Flagged", "Impediment"),
    "Owner": ("Assignee",),
    "Estimate": ("Original Estimate", "Σ Original Estimate"),
    "Hours": ("Time Spent", "Σ Time Spent"),
    # When the work was recorded, as near as an export gets: Jira does not put
    # a worklog's own date in an issue export, and `Updated` is the last time
    # anything on the issue moved. Named honestly in the coverage report rather
    # than presented as a log date.
    "Date": ("Updated",),
}

#: Jira renders some custom fields by shipping the page's own script, so the
#: cell holds a function body rather than a value. Any cell that looks like this
#: is dropped rather than written through - a task whose Owner is a jQuery call
#: is worse than a task with no Owner.
SCRIPT_SMELL = re.compile(r"\$\(|function\s*\(|setTimeout|document\.ready", re.I)

#: Same idea for Jira's own rendering failures, which arrive as prose.
ERROR_SMELL = re.compile(r"^Error rendering ", re.I)

DATE_COLUMNS = frozenset(
    {"Start", "Planned Finish", "Baseline Finish", "Last Updated", "Date"}
)


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


def _as_hours(value):
    """Effort as hours, however this instance writes it.

    Jira reports effort in **seconds** through the API and as a formatted string
    ("3h 30m", "2d") in some exports, while a plain number in a spreadsheet is
    usually already hours. Each form is read for what it is; anything else is
    dropped rather than guessed, because an effort figure off by a factor of
    3600 is worse than an absent one.
    """
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        #: A bare number is taken as hours, and **never** rescaled.
        #:
        #: Some Jira configurations write effort in seconds, and a threshold
        #: that guessed which was which would be wrong by a factor of 3600 the
        #: day it guessed wrong - a confident wrong number, which is the one
        #: outcome this codebase will not trade for coverage. `coverage()`
        #: instead says when a column *looks* like seconds and lets a person
        #: decide, because they can see their own Jira and we cannot.
        return float(value)
    text = str(value).strip().lower()
    total = 0.0
    matched = False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([wdhm])", text):
        matched = True
        total += float(amount) * {"w": 40.0, "d": 8.0, "h": 1.0, "m": 1 / 60}[unit]
    if matched:
        return round(total, 2)
    try:
        return float(text)
    except ValueError:
        return None


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


def _is_keyless_record(row: tuple, summary_at: int, status_at: int | None) -> bool:
    """Is this a work item somebody appended, or the previous issue's rich text?

    A Jira export writes a wrapped Description across rows that carry *only*
    that one column - the key, the summary and the status stay on the first row
    of the block. So a row with no Key but a Summary **and** a Status is not
    spill: nothing in Jira's own wrapping produces that pair, and it is exactly
    the shape of a backlog pasted in below the export.

    Requiring both is the load-bearing part. Summary alone would swallow every
    continuation row in an export whose rich text happens to sit under a
    summary-ish column, turning one issue into nine tasks - which is the failure
    the key-only rule was written to avoid, and it must not come back.
    """
    if status_at is None:
        return False
    if summary_at >= len(row) or status_at >= len(row):
        return False
    return bool(_clean(row[summary_at])) and bool(_clean(row[status_at]))


#: Marks a Task ID this module derived rather than read. Deliberately not
#: shaped like a Jira key (`PROJ-12`), so nothing downstream - and nobody
#: reading a Gantt label - can mistake a synthesized id for one that exists in
#: the tracker and could be opened.
SYNTHETIC_PREFIX = "NOKEY-"


def _synthetic_id(summary, taken: set[str]) -> str | None:
    """A stable Task ID for a row the export gave no Key.

    Hashed from the summary rather than numbered by position, because `Task ID`
    is the schedule contract's key field: the differ matches this upload's rows
    against last upload's by it, so an id that moves when somebody sorts the
    sheet would report every task as deleted and re-added. The text is the only
    thing about these rows that is theirs.

    Collisions - two rows with the identical summary - get a suffix rather than
    being merged, because they are two items on the board however they are
    named.
    """
    text = _clean(summary)
    if not isinstance(text, str):
        return None
    digest = sha1(text.casefold().encode("utf-8")).hexdigest()[:8]
    candidate = f"{SYNTHETIC_PREFIX}{digest}"
    suffix = 2
    while candidate in taken:
        candidate = f"{SYNTHETIC_PREFIX}{digest}-{suffix}"
        suffix += 1
    return candidate


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

            labels = [h.casefold() for h in headers]
            summary_at = labels.index("summary")
            status_at = labels.index("status") if "status" in labels else None

            rows = []
            for index, row in enumerate(worksheet.iter_rows(values_only=True)):
                if index <= header_at:
                    continue
                if key_at < len(row) and _clean(row[key_at]):
                    rows.append(row)
                elif _is_keyless_record(row, summary_at, status_at):
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


def convert(
    headers: list[str],
    rows: list[tuple],
    *,
    sources: dict[str, tuple[str, ...]] | None = None,
    columns: tuple[str, ...] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """`(records, chosen)` - the template rows, and which Jira column fed each.

    Defaults to the schedule contract. Pass `sources`/`columns` to read the same
    export as a worklog instead - one function rather than two, because every
    rule below (shape ranking, repeated headers, dropped script cells) applies
    identically and a second copy would drift.
    """
    sources = sources if sources is not None else SOURCES
    columns = columns if columns is not None else SCHEDULE_CONTRACT.template_headers
    lookup = _index(headers)
    def _grouping_fallback(row: tuple, column: str) -> str | None:
        """A band for a row the chosen source gave none - see `GROUPING_FALLBACK`.

        Takes the first value that is not a link, in the declared order, so a
        component beats a fix version and neither is reached while a parent
        exists. Multi-valued cells (`UI/UX, Core Platform`) are left whole: the
        first is not more true than the second, and splitting one row across two
        bands would double-count it.
        """
        for name in GROUPING_FALLBACK.get(column, ()):
            if name.casefold() not in lookup:
                continue
            for cell in _cells(row, name):
                text = _clean(cell)
                if isinstance(text, str) and not URL.match(text):
                    return PARENT_KEY.sub("", text) or None
        return None

    #: Whichever of the two contracts calls the issue's own text its title -
    #: `Activity` on a schedule, `Summary` on a worklog. It is what a synthesized
    #: Task ID is hashed from, so it must not be hardcoded to one contract.
    title_column = "Activity" if "Activity" in columns else "Summary"

    def _cells(row: tuple, name: str) -> list:
        """Every value this row carries under `name`, across repeated columns."""
        return [
            row[at] for at in lookup[name.casefold()] if at < len(row) and _clean(row[at])
        ]

    def _populated(name: str) -> int:
        """How many issues carry a value in any column of this name."""
        return sum(1 for r in rows if _cells(r, name))

    def _plausible(column: str, name: str) -> int:
        """How many of those values look like what `column` actually means.

        Falls back to the plain count where no shape is declared, so a column
        with no way to recognise itself behaves exactly as before.
        """
        shape = SHAPE.get(column)
        if shape is None:
            return _populated(name)
        return sum(1 for r in rows if any(shape(c) for c in _cells(r, name)))

    def _is_a_copy_of_another_column(column: str, name: str) -> bool:
        """Is this candidate just some other column under a different header?"""
        twin = DEMOTE_IF_EQUALS.get(column)
        if twin is None or twin.casefold() not in lookup:
            return False
        pairs = [
            (_cells(r, name)[0], _cells(r, twin)[0])
            for r in rows
            if _cells(r, name) and _cells(r, twin)
        ]
        return bool(pairs) and all(a == b for a, b in pairs)

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
    for column, candidates in sources.items():
        present = [c for c in candidates if c.casefold() in lookup]
        if not present:
            continue
        #: Shape first, then how full, then the declared preference: a column
        #: that looks right beats one that is merely non-empty.
        best = max(
            present,
            key=lambda name: (
                not _is_a_copy_of_another_column(column, name),
                _plausible(column, name),
                _populated(name),
                -present.index(name),
            ),
        )
        if _populated(best) or not rows:
            chosen[column] = best
        else:
            # Every candidate is empty. Name the preferred one anyway so the
            # coverage report can say "0/17 from 'Due Date'" rather than going
            # silent about a column nobody filled.
            chosen[column] = present[0]

    records = []
    #: Every id already spoken for, so a synthesized one cannot land on a real
    #: Jira key or on another synthesized one. Seeded with the real keys before
    #: the loop rather than filled as it goes, because the rows arrive in sheet
    #: order and an appended backlog sits *below* the keyed issues - checking
    #: only what has been seen so far would miss a clash with a key further up.
    taken = {
        str(_clean(row[at]))
        for name in (chosen.get("Task ID"),)
        if name
        for at in lookup.get(name.casefold(), [])
        for row in rows
        if at < len(row) and _clean(row[at])
    }
    for row in rows:
        record = {}
        for column in columns:
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
            elif column in {"Estimate", "Hours"}:
                record[column] = _as_hours(cells[0]) if cells else None
            elif column == "Milestone":
                #: Prefer a cell that looks like a parent over merely the first
                #: non-empty one, and refuse a URL outright - that is a link to
                #: a thing, not the name of one.
                named = next((c for c in cells if _looks_like_parent(c)), None)
                value = _clean(named if named is not None else (cells[0] if cells else None))
                if isinstance(value, str):
                    value = None if URL.match(value) else (PARENT_KEY.sub("", value) or None)
                if value is None:
                    value = _grouping_fallback(row, column)
                record[column] = value
            else:
                record[column] = _clean(cells[0]) if cells else None
        #: A row the export gave no Key still has to carry one: `Task ID` is the
        #: schedule contract's key field, and a blank there is a row the reader
        #: rejects. Derived from the summary and marked as derived - see
        #: `_synthetic_id` - so the row reaches the board instead of being
        #: dropped, which is the whole point of reading it.
        if not record.get("Task ID"):
            made = _synthetic_id(record.get(title_column), taken)
            if made:
                taken.add(made)
                record["Task ID"] = made
        records.append(record)
    return records, chosen


def _build(records: list[dict], contract=None) -> Workbook:
    """The blank template's own shape, filled in.

    The tab is named `Activities` because that is what the contract prefers and
    `find_sheet` tries first - it would resolve a differently-named tab too, but
    only by searching, and there is no reason to make it search.
    """
    contract = contract if contract is not None else SCHEDULE_CONTRACT
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Worklog" if contract is WORKLOG_CONTRACT else "Activities"
    sheet.append(list(contract.template_headers))
    for record in records:
        sheet.append([record[h] for h in contract.template_headers])
    for index, header in enumerate(contract.template_headers, start=1):
        column = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[column].width = max(12, len(header) + 2)
    return workbook


def write_schedule(records: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    _build(records).save(out)


def to_bytes(records: list[dict], contract=None) -> bytes:
    """The same workbook as `write_schedule`, in memory.

    What the upload route needs: the converted sheet has to reach the existing
    ingestion path as bytes, exactly as if a person had picked a schedule
    workbook from their disk.
    """
    buffer = BytesIO()
    _build(records, contract).save(buffer)
    return buffer.getvalue()


def coverage(
    records: list[dict],
    chosen: dict[str, str],
    headers: list[str],
    rows: list[tuple],
    columns: tuple[str, ...] | None = None,
) -> dict:
    """What came across, what did not, and what that costs.

    Returned as data rather than printed so the CLI and the API can say the same
    thing in their own shapes. `notes` is the list a person needs to read: each
    entry is a limitation of the *export*, not of the conversion, and no better
    mapping removes any of them.
    """
    total = len(records)
    wanted = columns if columns is not None else SCHEDULE_CONTRACT.template_headers
    columns = []
    for column in wanted:
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

    #: Said first, because it changes what every count below is counting. A
    #: person who exported 17 issues and sees 191 tasks needs to know where the
    #: other 174 came from before they read a completion figure derived from
    #: them.
    synthesized = sum(
        1
        for r in records
        if isinstance(r.get("Task ID"), str)
        and r["Task ID"].startswith(SYNTHETIC_PREFIX)
    )
    if synthesized:
        notes.append(
            f"{synthesized} of {total} row(s) carry no Jira Key - they were "
            "appended to the sheet below the export rather than written by it. "
            "They were read, with a Task ID derived from their summary and "
            f"prefixed {SYNTHETIC_PREFIX!r} so it is not mistaken for an issue "
            "you can open. Re-word a summary and that row reads as a new task, "
            "because the text is the only stable thing about it - the fix is to "
            "raise them in Jira so they arrive with keys of their own."
        )

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

    #: Effort that is implausible as hours is usually seconds. Said, not fixed -
    #: see `_as_hours`. 2000 hours is a person-year on one issue, so anything
    #: past it is a unit problem rather than a workload.
    effort = [
        v
        for r in records
        for v in (r.get("Estimate"), r.get("Hours"))
        if isinstance(v, (int, float))
    ]
    if effort and max(effort) > 2000:
        notes.append(
            f"The largest effort value is {max(effort):,.0f}, which is implausible "
            "as hours - this Jira probably reports effort in seconds. Nothing was "
            "rescaled, because guessing wrong would be wrong by a factor of 3600. "
            "Divide the column by 3600 in the export, or re-export with effort "
            "formatted as '3h 30m'."
        )

    owners = {r["Owner"] for r in records if r["Owner"]}
    if len(owners) <= 1:
        notes.append(
            f"{len(owners)} distinct owner(s). Resource contention is computed "
            "across projects sharing a person, so this project contributes nothing "
            "to a program rollup until other projects name the same people."
        )

    return {"issues": total, "columns": columns, "notes": notes}


def convert_workbook(
    source, sheet: str | None = None, kind: str = "schedule"
) -> tuple[bytes, dict]:
    """A Jira export in, a schedule workbook and its coverage report out.

    The one call both entry points make, so the CLI and the upload route cannot
    convert the same file two different ways.
    """
    worklog = kind == "worklog"
    headers, rows = read_export(source, sheet)
    records, chosen = convert(
        headers,
        rows,
        sources=WORKLOG_SOURCES if worklog else SOURCES,
        columns=WORKLOG_CONTRACT.template_headers if worklog else None,
    )
    if not records:
        raise NotAJiraExport(
            "the export has a header row but no issues under it - nothing to load."
        )
    if worklog:
        if not any(r.get("Hours") or r.get("Estimate") for r in records):
            raise NotAJiraExport(
                "no issue in this export carries Original Estimate or Time Spent, so "
                "there is no effort to load. Export those fields, or import it as a "
                "schedule instead."
            )
        return to_bytes(records, WORKLOG_CONTRACT), coverage(
            records, chosen, headers, rows, columns=WORKLOG_CONTRACT.template_headers
        )
    return to_bytes(records), coverage(records, chosen, headers, rows)
