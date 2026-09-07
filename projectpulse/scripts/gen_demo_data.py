"""Write the demo spreadsheets at a given point in their history.

Deliberately writes *files a PM would actually have produced*, then lets the real
reader, identity resolver and differ run over them. Seeding domain rows directly
would demo nothing: the differ, the hash skip, the identity resolver and the whole
bounded-precision path would all be bypassed, and the result would be a screenshot
rather than a system.

Each step overwrites the same workbook, exactly as a person editing their sheet
would. Run step 0, sync, run step 1, sync - and the second sync sees change.

    python -m scripts.gen_demo_data --step 0
    python -m scripts.gen_demo_data --step 1

Step 1 deliberately includes the messy cases, because a demo where every row is
clean proves nothing about a hand-maintained sheet:

* a row that was renamed and has no Task ID  -> matched by title, low confidence
* a duplicated Task ID                       -> rejected, visibly
* a column nobody told us about              -> reported as an unknown header
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from app.config import settings

SCHEDULE_HEADERS = [
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
]

WORKLOG_HEADERS = [
    "Task ID",
    "Summary",
    "Status",
    "Blocked",
    "Owner",
    "Hours",
]


def _schedule_rows(step: int) -> list[list]:
    """The HRMS delivery schedule, before and after the environment slip."""
    rows = [
        ["WBS-101", "Requirements sign-off", "Planning", "Project Charter",
         "Done", "Le Van C", date(2026, 1, 12), date(2026, 1, 30), date(2026, 1, 30), 100],
        ["WBS-108", "Environment Setup", "Development", "Environment Setup",
         "In Progress", "Tran Quoc B", date(2026, 2, 16), date(2026, 3, 4), date(2026, 3, 4), 60],
        ["WBS-114", "Integration build", "Development", "Development",
         "Not Started", "Pham Hong D", date(2026, 3, 5), date(2026, 3, 20), date(2026, 3, 20), 0],
        ["WBS-118", "UAT preparation", "Testing", "UAT",
         "Not Started", "Nguyen Van A", date(2026, 3, 23), date(2026, 5, 14), date(2026, 5, 14), 0],
        # No Task ID from the outset - someone added this by hand. It is the row
        # that will exercise title matching when it gets renamed in step 1.
        [None, "Data migration dry run", "Development", "Development",
         "Not Started", "Tran Quoc B", date(2026, 3, 10), date(2026, 3, 27), date(2026, 3, 27), 0],
    ]

    if step >= 1:
        # The environment slips 12 days, and it pushes everything behind it.
        rows[1][8] = date(2026, 3, 16)   # WBS-108 planned finish
        rows[1][9] = 75                  # progress crept up
        rows[2][4] = "Blocked"           # WBS-114 status
        rows[2][8] = date(2026, 4, 1)
        rows[3][8] = date(2026, 5, 26)   # UAT slides
        # Renamed by hand, still no id: matched on title similarity, low confidence.
        rows[4][1] = "Data migration dry-run"

        # A duplicate id. Without the guard this would silently overwrite WBS-114.
        rows.append(
            ["WBS-114", "Integration build (rework)", "Development", "Development",
             "Not Started", "Pham Hong D", date(2026, 4, 2), date(2026, 4, 18),
             date(2026, 4, 18), 0]
        )

    return rows


def _worklog_rows(step: int) -> list[list]:
    """QA worklog. The blocked count is the signal that matters.

    Note this moves at step **2**, not step 1. The schedule sheet reflects the
    environment slip first; the QA backlog only grows afterwards. That gap is what
    puts a scan between cause and effect, and a scan between them is exactly what
    makes the ordering provable rather than merely plausible.
    """
    rows = [
        ["QA-001", "Login regression suite", "Open", "No", "My Nguyen", 6],
        ["QA-002", "Payroll calculation suite", "Blocked", "Yes", "My Nguyen", 2],
        ["QA-003", "Leave approval flow", "Blocked", "Yes", "Hoach Bach", 1],
        ["QA-004", "Timesheet import", "Blocked", "Yes", "Hoach Bach", 0],
        ["QA-005", "Org chart sync", "Blocked", "Yes", "My Nguyen", 0],
        ["QA-006", "Payslip PDF render", "Open", "No", "Tung Nguyen", 4],
        ["QA-007", "Role permissions matrix", "Open", "No", "Tung Nguyen", 3],
    ]

    if step >= 2:
        # Everything that needed the environment is now blocked behind it.
        for row in rows:
            if row[0] in {"QA-001", "QA-006", "QA-007"}:
                row[2], row[3] = "Blocked", "Yes"
        rows.extend(
            [
                [f"QA-{n:03d}", f"Integration case {n - 7}", "Blocked", "Yes",
                 "My Nguyen", 0]
                for n in range(8, 18)
            ]
        )

    return rows


def _write(path: Path, sheet_name: str, headers: list[str], rows: list[list],
           title: str, extra_header: str | None = None) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name

    # A title line and a blank row above the table, the way real sheets are laid
    # out. The reader has to find the header row rather than assume row 1.
    worksheet.append([title])
    worksheet.append([])

    header_row = list(headers)
    if extra_header:
        header_row.append(extra_header)
    worksheet.append(header_row)

    for row in rows:
        worksheet.append(list(row) + ([None] if extra_header else []))

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", type=int, default=0,
                        help="0 = baseline, 1 = environment slip lands in the "
                             "schedule, 2 = QA backlog grows behind it")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out = args.out or settings.data_root

    _write(
        out / "hrms_schedule.xlsx",
        "Activities",
        SCHEDULE_HEADERS,
        _schedule_rows(args.step),
        "HRMS Portal V2 - Delivery Schedule",
        # Appears in step 1 only: somebody added a column.
        extra_header="Comments" if args.step >= 1 else None,
    )
    _write(
        out / "hrms_worklog.xlsx",
        "Worklog",
        WORKLOG_HEADERS,
        _worklog_rows(args.step),
        "HRMS Portal V2 - QA Worklog",
    )

    print(f"wrote step {args.step} workbooks to {out}")


if __name__ == "__main__":
    main()
