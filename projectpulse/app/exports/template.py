"""The blank workbook we ask a project manager to fill in.

Generated from `SheetContract.template_headers` rather than written by hand, so
the file we hand out and the file we know how to read are the same file by
construction. The alternative - a template maintained beside the reader - drifts
the first time a column is renamed, and it drifts silently: the sheet still
opens, the column is simply ignored, and a PM's data quietly stops arriving.

Two workbooks, not one with two tabs, because that is what the watcher watches:
a schedule file and a worklog file, each with the sheet name the contract
expects. A single combined workbook would look tidier and would not ingest.

Each file carries a title row, a blank row, then the headers - the same shape as
a real sheet, and the shape the reader's header search is built for. It also
carries a `Notes` sheet with the three rules a filled-in sheet has to obey. The
reader only ever opens the named sheet, so extra tabs are free.

⚠️ **No example rows.** An example row in a template comes back as a real task
with a real id, and the identity resolver has no way to know it was decorative.
The guidance goes on the Notes tab instead.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.ingest.sources.excel.reader import (
    SCHEDULE_CONTRACT,
    WORKLOG_CONTRACT,
    SheetContract,
)

#: The two files, keyed by the name used in a URL and on the command line.
#: `sheet_name` must match what `source.WATCHED` opens, or the file is
#: unreadable to the very pipeline it was generated for.
KINDS: dict[str, tuple[SheetContract, str, str]] = {
    "schedule": (SCHEDULE_CONTRACT, "Activities", "Delivery schedule"),
    "worklog": (WORKLOG_CONTRACT, "Worklog", "QA / worklog"),
}

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF")

#: What a filled-in sheet has to obey, and why. Phrased as consequences rather
#: than rules - "do not change Task ID" is ignored; "renaming it makes the row
#: look like a new task" is not.
_NOTES: dict[str, tuple[str, ...]] = {
    "schedule": (
        "Task ID must be stable. It is how a row is recognised between one "
        "scan and the next. Change it and the old row reads as deleted and the "
        "new one as freshly created, which breaks the history for that task.",
        "Leave Task ID blank only if you must. Rows without one are matched by "
        "title instead, marked low confidence, and excluded from root-cause "
        "analysis - their history is not trustworthy enough to build on.",
        "Predecessor accepts MS-Project notation: WBS-114, WBS-114FS+2d, or "
        "several separated by commas. This is where dependency edges come "
        "from, and a stated edge always outranks one we infer from dates.",
        "Dates go in real date cells, not text. Baseline Finish is the date "
        "agreed at plan time; Planned Finish is the date believed now. "
        "Variance is the gap between them, so a sheet with no baseline can "
        "show slip but not variance.",
        "Progress is a percentage: 0 to 100.",
        "Add columns if you need them - unknown columns are reported, not "
        "rejected. Do not delete Task ID.",
    ),
    "worklog": (
        "Task ID must be stable, for the same reason as on the schedule sheet.",
        "Blocked marks an item that cannot proceed. A queue where most items "
        "are blocked is what the QA rules look at, so this column is what "
        "makes a stalled test queue visible.",
        "Estimate is planned effort in hours and Hours is what has actually "
        "been spent. Both as plain numbers. The pair is what a burn chart is "
        "drawn from - Hours on its own is a total with nothing to burn "
        "against, so leaving Estimate blank turns that chart off.",
        "Date is when those hours were last logged. A total with no date "
        "cannot be placed on a time axis, and we will not guess one for you: "
        "when we notice an edit is not when the work happened.",
        "Add columns if you need them - unknown columns are reported, not "
        "rejected.",
    ),
}


def template_workbook(kind: str) -> Workbook:
    """One blank workbook, ready to ingest once someone types in it."""
    if kind not in KINDS:
        raise ValueError(
            f"unknown template {kind!r}; expected one of {', '.join(sorted(KINDS))}"
        )

    contract, sheet_name, title = KINDS[kind]
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name

    # Title, blank, headers - matching a real sheet, and matching what the
    # reader's header search expects to have to skip.
    sheet.append([title])
    sheet["A1"].font = Font(bold=True, size=14)
    sheet.append([])
    sheet.append(list(contract.template_headers))

    header_row = 3
    for index, header in enumerate(contract.template_headers, start=1):
        cell = sheet.cell(row=header_row, column=index)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        # Wide enough to read the header, which is the only content there is.
        sheet.column_dimensions[get_column_letter(index)].width = max(
            14, len(header) + 4
        )

    # So typing starts in the right place rather than on the header.
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)

    _notes_sheet(workbook, kind, contract)
    return workbook


def _notes_sheet(workbook: Workbook, kind: str, contract: SheetContract) -> None:
    """How to fill it in, on its own tab.

    A separate sheet rather than comments in the header row: the reader opens
    only the named sheet, so anything here is guaranteed not to reach the
    pipeline, and a PM can read it without unhiding anything.
    """
    notes = workbook.create_sheet("Notes")
    notes.column_dimensions["A"].width = 4
    notes.column_dimensions["B"].width = 96

    notes["A1"] = "How to fill in this sheet"
    notes["A1"].font = Font(bold=True, size=14)

    row = 3
    for index, note in enumerate(_NOTES.get(kind, ()), start=1):
        notes.cell(row=row, column=1, value=f"{index}.")
        cell = notes.cell(row=row, column=2, value=note)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        row += 2

    notes.cell(row=row, column=2, value="Columns this sheet is read for:")
    notes.cell(row=row, column=2).font = Font(bold=True)
    row += 1
    # Listed from the contract, so this cannot describe a column that is not
    # actually read.
    for header in contract.template_headers:
        notes.cell(row=row, column=2, value=f"  {header}")
        row += 1


def template_bytes(kind: str) -> bytes:
    """The workbook as bytes, for an HTTP response or a file write."""
    buffer = BytesIO()
    template_workbook(kind).save(buffer)
    return buffer.getvalue()
