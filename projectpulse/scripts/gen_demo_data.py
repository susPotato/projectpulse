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

The steps are deliberately spread apart in time. A cause and its effect observed
in the same scan share a time window and can never be ordered, so a demo that
moves everything at once proves nothing at all.

Step 2 deliberately includes the messy cases, because a demo where every row is
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
    # The column that closes the dependency-edge gap (architecture section 5.4). We own
    # this template, so this is the cheapest real source of DAG edges available.
    "Predecessor",
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
         "Done", "Le Van C", date(2026, 1, 12), date(2026, 1, 30), date(2026, 1, 30), 100,
         None],
        ["WBS-108", "Environment Setup", "Development", "Environment Setup",
         "In Progress", "Tran Quoc B", date(2026, 2, 16), date(2026, 3, 4), date(2026, 3, 4), 60,
         "WBS-101"],
        ["WBS-114", "Integration build", "Development", "Development",
         "Not Started", "Pham Hong D", date(2026, 3, 5), date(2026, 3, 20), date(2026, 3, 20), 0,
         # The headline edge of the demo chain, stated outright rather than
         # inferred: the environment slip reaches UAT through this row.
         "WBS-108"],
        ["WBS-118", "UAT preparation", "Testing", "UAT",
         "Not Started", "Nguyen Van A", date(2026, 3, 23), date(2026, 5, 14), date(2026, 5, 14), 0,
         # MS-Project lag notation, because a PM pasting from Project will type it.
         "WBS-114FS+2d"],
        # No Task ID from the outset - someone added this by hand. It is the row
        # that will exercise title matching when it gets renamed in step 1.
        [None, "Data migration dry run", "Development", "Development",
         "Not Started", "Tran Quoc B", date(2026, 3, 10), date(2026, 3, 27), date(2026, 3, 27), 0,
         # Deliberately stated. The row's identity is only 'low' confidence, so the
         # edge is dropped with a reason - an edge is only as good as the identity
         # of the rows it joins.
         "WBS-108"],
        # Nobody wrote a predecessor here, but the dates say it cannot start until
        # UAT prep is finished. This is the row that exercises 'wbs_implicit'.
        ["WBS-121", "Cutover rehearsal", "Deployment", "Go-Live",
         "Not Started", "Nguyen Van A", date(2026, 5, 18), date(2026, 5, 29), date(2026, 5, 29), 0,
         None],
    ]

    if step >= 1:
        # The environment slips 12 days. Nothing downstream has reacted yet - that
        # is the point of a separate step. A cause and its effect landing in one
        # scan window are not orderable, so a demo that moves both at once cannot
        # prove causation no matter how obvious the story looks to a human.
        rows[1][8] = date(2026, 3, 16)   # WBS-108 planned finish
        rows[1][9] = 75                  # progress crept up

    if step >= 2:
        # Now the downstream reacts, one scan later - so the environment slip is
        # provably earlier than the integration slip rather than merely adjacent.
        rows[2][4] = "Blocked"           # WBS-114 status
        rows[2][8] = date(2026, 4, 1)
        rows[3][8] = date(2026, 5, 26)   # UAT slides
        # Renamed by hand, still no id: matched on title similarity, low confidence.
        rows[4][1] = "Data migration dry-run"

        # A second predecessor typed by hand, one of the two a typo for WBS-118.
        # The good half still produces its edge; the typo is quarantined with a
        # reason the sheet's owner can act on.
        rows[3][10] = "WBS-114FS+2d, WBS-117"

        # A duplicate id. Without the guard this would silently overwrite WBS-114.
        rows.append(
            ["WBS-114", "Integration build (rework)", "Development", "Development",
             "Not Started", "Pham Hong D", date(2026, 4, 2), date(2026, 4, 18),
             date(2026, 4, 18), 0, "WBS-108"]
        )

    return rows


def _worklog_rows(step: int) -> list[list]:
    """QA worklog. The blocked count is the signal that matters.

    Note this moves at step **3**, two steps after the environment slip. The
    schedule reflects the slip first, the downstream tasks react next, and only
    then does the QA backlog grow. Each gap puts a scan between cause and effect,
    and a scan between them is exactly what makes the ordering provable rather
    than merely plausible - without it the whole cascade lands in one window and
    the causal engine can prove nothing.
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

    if step >= 3:
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
                        help="0 = baseline, 1 = the environment slips, "
                             "2 = downstream tasks react, 3 = QA backlog grows")
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
        extra_header="Comments" if args.step >= 2 else None,
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
