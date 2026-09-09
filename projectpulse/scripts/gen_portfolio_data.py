"""Write the two supporting delivery projects that make the portfolio real.

`gen_demo_data.py` writes HRMS - the project the causal-chain story is built
around, choreographed across four steps so a scan always separates cause from
effect. These two are not that: single-snapshot workbooks, no multi-step
timeline, because nothing here needs to *prove* an ordering. Their job is
narrower - give the Program-level rollups (portfolio, risk register, schedule
matrix) more than one row to roll up, using the same names the PM's own
mockups already show alongside HRMS ("Example Project, SAIN, HRMS" - see
`Layout_Program`, the Program Risk / Program Schedule screens).

Same discipline as the HRMS generator: this writes files a PM would have
produced, then the real reader/differ/identity-resolver run over them -
never DB rows directly (see invariant 4, CLAUDE.md section 4).

Task/QA ids are prefixed distinctly from HRMS's `WBS-`/`QA-` (SAIN uses
`SAIN-`/`SQA-`, Example Project uses `EXP-`/`EQA-`) because every Excel
project shares one `connection_id`, and `domain_id()` namespaces only by
that connection plus the row's own key - two projects both using `WBS-101`
would collide into one Task row.

    python -m scripts.gen_portfolio_data
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from app.config import settings
from app.ingest.sources.excel.reader import SCHEDULE_CONTRACT, WORKLOG_CONTRACT

SCHEDULE_HEADERS = list(SCHEDULE_CONTRACT.template_headers)
WORKLOG_HEADERS = list(WORKLOG_CONTRACT.template_headers)


def _write(path: Path, sheet_name: str, headers: list[str], rows: list[list],
           title: str) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet.append([title])
    worksheet.append([])
    worksheet.append(list(headers))
    for row in rows:
        worksheet.append(list(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def _sain_schedule_rows() -> list[list]:
    """SAIN: on track. The contrast case next to HRMS on a heatmap."""
    return [
        ["SAIN-201", "Discovery workshop", "Planning", "Project Charter",
         "Done", "Le Van C", date(2026, 1, 8), date(2026, 1, 20), date(2026, 1, 20), 100, None],
        ["SAIN-205", "Core module build", "Development", "Development",
         "Done", "Hoach Bach", date(2026, 1, 22), date(2026, 2, 25), date(2026, 2, 25), 100,
         "SAIN-201"],
        ["SAIN-208", "Regression suite", "Testing", "UAT",
         "In Progress", "My Nguyen", date(2026, 2, 26), date(2026, 3, 18), date(2026, 3, 18), 55,
         "SAIN-205"],
        ["SAIN-212", "UAT sign-off", "Testing", "UAT",
         "Not Started", "Le Van C", date(2026, 3, 19), date(2026, 3, 28), date(2026, 3, 28), 0,
         "SAIN-208"],
        ["SAIN-215", "Go-live", "Deployment", "Go-Live",
         "Not Started", "Hoach Bach", date(2026, 3, 29), date(2026, 4, 6), date(2026, 4, 6), 0,
         "SAIN-212"],
    ]


def _sain_worklog_rows() -> list[list]:
    rows = [
        ("SQA-001", "Checkout regression", "My Nguyen", 10, 6),
        ("SQA-002", "API contract tests", "My Nguyen", 8, 5),
    ]
    return [
        [task_id, summary, "Open", "No", owner, estimate, hours,
         date(2026, 3, 12) if hours else None]
        for task_id, summary, owner, estimate, hours in rows
    ]


def _example_schedule_rows() -> list[list]:
    """Example Project: a smaller, mid-flight project - the "watch" case."""
    return [
        ["EXP-301", "Vendor onboarding", "Planning", "Project Charter",
         "Done", "Tran Quoc B", date(2026, 1, 15), date(2026, 2, 2), date(2026, 2, 2), 100, None],
        ["EXP-305", "Environment provisioning", "Development", "Environment Setup",
         "In Progress", "Tran Quoc B", date(2026, 2, 3), date(2026, 3, 10), date(2026, 3, 10), 40,
         "EXP-301"],
        ["EXP-309", "Feature build", "Development", "Development",
         "Not Started", "Pham Hong D", date(2026, 3, 11), date(2026, 4, 2), date(2026, 4, 2), 0,
         "EXP-305"],
    ]


def _example_worklog_rows() -> list[list]:
    rows = [
        ("EQA-001", "Smoke suite", "Tung Nguyen", 6, 1),
    ]
    return [
        [task_id, summary, "Blocked" if hours == 0 else "Open",
         "Yes" if hours == 0 else "No", owner, estimate, hours,
         date(2026, 3, 12) if hours else None]
        for task_id, summary, owner, estimate, hours in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or settings.data_root

    _write(out / "sain_schedule.xlsx", "Activities", SCHEDULE_HEADERS,
           _sain_schedule_rows(), "SAIN - Delivery Schedule")
    _write(out / "sain_worklog.xlsx", "Worklog", WORKLOG_HEADERS,
           _sain_worklog_rows(), "SAIN - QA Worklog")
    _write(out / "example_project_schedule.xlsx", "Activities", SCHEDULE_HEADERS,
           _example_schedule_rows(), "Example Project - Delivery Schedule")
    _write(out / "example_project_worklog.xlsx", "Worklog", WORKLOG_HEADERS,
           _example_worklog_rows(), "Example Project - QA Worklog")

    print(f"wrote SAIN + Example Project workbooks to {out}")


if __name__ == "__main__":
    main()
