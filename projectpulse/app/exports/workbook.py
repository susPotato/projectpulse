"""A `ReportDoc` as a workbook, for the PM who wants to sort and filter it.

The point of this format is not that it is a report in Excel - it is that the
**tables become real tables**: their own sheet, a frozen header, an autofilter,
and columns wide enough to read. A projection of forty tasks pasted into Word is
an appendix nobody opens; the same rows in a sheet get sorted by implied slip in
one click, which is the actual question a delivery manager has.

So the prose goes on one sheet and every table block gets its own. The prose
sheet keeps the tables' names in reading order, so the document still has a
structure rather than being a pile of tabs.

Formats nothing - see `document.py`. Cells are written as the strings that
arrived, deliberately: re-parsing "2026-07-02" back into a date so Excel can
right-align it would mean this module deciding how a date looks, which is the
one thing it must not do. `format_fact` already decided.

openpyxl is a hard dependency of the ingester, so unlike the `.docx` path this
format needs no optional extra and cannot be missing at a demo.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.exports.document import Block, ReportDoc

#: Matches the template exporter, so the two files the product hands back look
#: like one product.
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_TITLE_FONT = Font(bold=True, size=14)
_H2_FONT = Font(bold=True, size=11)
_NOTE_FONT = Font(italic=True, color="595959")

#: Excel refuses a sheet name over 31 characters or containing these, and fails
#: at save time rather than at write time - which would surface as a corrupt
#: download rather than a traceback.
_FORBIDDEN = set(r"[]:*?/\'")
_MAX_SHEET_NAME = 31

#: Wide enough for a task label, narrow enough that five columns fit a screen.
_MIN_WIDTH, _MAX_WIDTH = 10, 52


def _safe_sheet_name(name: str, taken: set[str]) -> str:
    """A name Excel will accept, unique within the workbook.

    Truncation makes collisions likely rather than theoretical - two sections
    whose titles agree for 31 characters is unlikely, but a numeric suffix costs
    nothing and a duplicate name raises.
    """
    cleaned = "".join(" " if ch in _FORBIDDEN else ch for ch in name).strip()
    cleaned = (cleaned or "Sheet")[:_MAX_SHEET_NAME]

    candidate, suffix = cleaned, 2
    while candidate.casefold() in taken:
        tail = f" {suffix}"
        candidate = cleaned[: _MAX_SHEET_NAME - len(tail)] + tail
        suffix += 1
    taken.add(candidate.casefold())
    return candidate


def _autosize(sheet, columns: int) -> None:
    """Column widths from the longest cell, clamped at both ends.

    openpyxl has no measure-the-text facility, so this is a character count -
    fine for a proportional font because the clamp does the real work.
    """
    for index in range(1, columns + 1):
        longest = max(
            (len(str(cell.value)) for cell in sheet[get_column_letter(index)] if cell.value),
            default=0,
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(longest + 2, _MIN_WIDTH), _MAX_WIDTH
        )


def _write_table(workbook: Workbook, block: Block, title: str, taken: set[str]) -> str:
    """One table block as its own filterable sheet. Returns the sheet name."""
    sheet = workbook.create_sheet(_safe_sheet_name(title, taken))

    sheet.append(list(block.columns))
    for cell in sheet[1]:
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")

    for row in block.rows:
        sheet.append(list(row))

    if block.columns:
        # Freeze *and* filter. A frozen header without a filter is a table you
        # can scroll; the filter is what makes the format worth choosing.
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(block.columns))}{sheet.max_row}"
        )
        _autosize(sheet, len(block.columns))
    return sheet.title


def _write_prose(sheet, block: Block, row: int) -> int:
    """One non-table block down column A. Returns the next free row."""
    if block.kind == "heading":
        cell = sheet.cell(row=row, column=1, value=block.text)
        cell.font = _H2_FONT
        return row + 1

    if block.kind == "paragraph":
        text = f"{block.label}{block.text}".strip()
        if not text:
            return row
        cell = sheet.cell(row=row, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if block.label and not block.text:
            cell.font = _H2_FONT
        return row + 1

    if block.kind == "note":
        cell = sheet.cell(row=row, column=1, value=block.text)
        cell.font = _NOTE_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        return row + 1

    if block.kind == "bullets":
        for item in block.items:
            # A literal bullet character, not a list style: a sheet has none,
            # and "- " reads as a bullet in every locale.
            cell = sheet.cell(row=row, column=1, value=f"- {item}")
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            row += 1
        return row

    return row  # pragma: no cover - tables are handled by the caller


def build_workbook(doc: ReportDoc) -> Workbook:
    """The whole report as an openpyxl workbook."""
    workbook = Workbook()
    prose = workbook.active
    prose.title = "Report"
    prose.column_dimensions["A"].width = 110

    row = 1
    prose.cell(row=row, column=1, value=doc.title).font = _TITLE_FONT
    row += 2

    for block in doc.preamble:
        row = _write_prose(prose, block, row)
    row += 1

    taken: set[str] = {"report"}
    for section in doc.sections:
        prose.cell(row=row, column=1, value=section.title).font = _TITLE_FONT
        row += 1

        for block in section.blocks:
            if block.kind == "table":
                name = _write_table(workbook, block, section.title, taken)
                # The prose sheet keeps the document's order, so a reader who
                # started at the top knows the tab exists and where it belongs.
                pointer = prose.cell(
                    row=row, column=1, value=f"[ see the '{name}' sheet ]"
                )
                pointer.font = _NOTE_FONT
                row += 1
            else:
                row = _write_prose(prose, block, row)
        row += 1

    return workbook


def workbook_bytes(doc: ReportDoc) -> bytes:
    """The workbook as bytes, for an HTTP response or a file write."""
    buffer = BytesIO()
    build_workbook(doc).save(buffer)
    return buffer.getvalue()
