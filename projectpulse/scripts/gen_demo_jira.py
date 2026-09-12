"""Write the same three-project demo as Jira issue exports.

    python -m scripts.gen_demo_jira                # into data/upload_demo_jira/
    python -m scripts.gen_demo_jira --out somewhere

The counterpart of `scripts.gen_demo_upload`, in the shape Jira's **Excel (All
fields)** export actually produces: a filter name, a "Displaying N issues" line,
a header row well down the sheet, and a column per custom field - including the
same header repeated once per value, which is how Jira writes a list.

Upload each file at Settings > Sources as kind **Jira issue export**. The server
converts it, so this also exercises `app/ingest/sources/jira/export_sheet.py`
end to end rather than only the reader.

**What a Jira export can and cannot demonstrate.** More than it first appears,
and the difference is worth being exact about because it is the honest limit of
the whole Jira story:

*Available* - issue links in the inbound direction become real dependency edges,
so the forward pass runs: a projected finish, a driving path, propagated slip,
milestones at risk, and tasks **dated earlier than their own dependencies
allow**. That last one is the headline finding on the Excel demo too, and it
needs edges and dates, not a baseline.

*Not available, ever* - **a baseline**. Jira has no "originally committed to"
field, so slip a person recorded and the delivery forecast (which resamples
observed drift against a committed date) cannot appear from one export. They
appear from *two*, taken at different times, because then the differ has
something to compare - which is the whole reason the second export matters.

So the chaos this set produces is the *implied* kind - what the chain says will
happen and the board does not show - rather than the recorded kind. Arguably the
better half to demonstrate: it is the half a PM cannot get from their own tool.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

from openpyxl import Workbook  # noqa: E402

from scripts.gen_demo_upload import PROJECTS, TODAY  # noqa: E402

#: The export's own columns, in the order a real one carries them. `Linked
#: Issues` appears twice because Jira writes one column per value rather than
#: packing a list into a cell - an issue blocked by two others produces two of
#: them, and reading only the last is a bug this shape exists to keep pinned.
HEADERS = [
    "Project", "Key", "Summary", "Issue Type", "Status", "Priority", "Resolution",
    "Assignee", "Reporter", "Created", "Updated", "Due Date", "Start date",
    "Original Estimate", "Time Spent", "Progress", "Sub-Tasks",
    "Linked Issues", "Linked Issues", "Parent Link", "Product",
]

#: Jira's own lifecycle words, not this app's normalised ones - the point is to
#: exercise the mapping. `Resolved` in particular is the state that used to be
#: read as open.
STATUS = {
    "Done": "Resolved",
    "In Progress": "In Progress",
    "Not started": "To Do",
}


def d(offset: int) -> date:
    return TODAY + timedelta(days=offset)


def _stamp(value: date) -> datetime:
    """Jira writes datetimes, and the converter narrows them back to days."""
    return datetime(value.year, value.month, value.day, 9, 0)


def write_export(key: str, out: Path) -> Path:
    spec = PROJECTS[key]
    workbook = Workbook()
    sheet = workbook.active
    #: Not `general_report`, deliberately: `read_export` finds the table by
    #: looking for a header row with Key and Summary, and a demo that only ever
    #: used the conventional tab name would never show that.
    sheet.title = "Sheet1"
    sheet.append([f"{spec['name']} - all issues"])
    sheet.append([f"Displaying {len(spec['schedule'])} issues at {TODAY:%d/%b/%y} 9:00 AM."])
    sheet.append(HEADERS)

    #: Effort comes off the *worklog* half of the same project, keyed by the
    #: task the QA item belongs to - which is how a real Jira holds it: one
    #: issue, one estimate, one time-spent, rather than a separate file.
    effort = {}
    for index, (_qa, _summary, _status, _blocked, _owner, estimate, hours, _logged) in enumerate(
        spec["worklog"]
    ):
        if index < len(spec["schedule"]):
            effort[spec["schedule"][index][0]] = (estimate, hours)

    for row in spec["schedule"]:
        task, activity, phase, milestone, status, owner, start, _base, plan, prog, pred = row
        estimate, spent = effort.get(task, (None, None))
        # Inbound phrasing, because that is the only direction a predecessor
        # column can honestly take - see `export_sheet.INBOUND_LINK`.
        links = [f"is blocked by {p.strip()}" for p in pred.split(",") if p.strip()]
        # An issue everyone has finished is not stale; the rest were last
        # touched well over a week ago, which is what `work_not_moving` reads.
        updated = _stamp(d(-2 if status == "Done" else -12))
        sheet.append([
            key, task, activity, phase, STATUS[status], None,
            "Fixed" if status == "Done" else "Unresolved",
            owner, owner, _stamp(d(-45)), updated,
            _stamp(d(plan)), _stamp(d(start)),
            estimate, spent, prog, None,
            links[0] if links else None,
            links[1] if len(links) > 1 else None,
            None, f"{milestone} [{key}-EPIC]",
        ])

    path = out / f"{key.lower()}_jira_export.xlsx"
    workbook.save(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path("data") / "upload_demo_jira",
        help="where to write the exports",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    written = [(PROJECTS[k]["name"], write_export(k, args.out)) for k in PROJECTS]

    print(f"wrote {len(written)} Jira export(s) to {args.out}\n")
    for name, path in written:
        print(f"  {name:18} {path.name}")
    print(
        "\n  Upload each file TWICE at Settings > Sources - one export carries\n"
        "  both halves - same project name each time, same program for all\n"
        "  three:\n\n"
        "    Jira issue export - schedule   the plan: dates, links, milestones\n"
        "    Jira issue export - effort     estimate against time spent\n\n"
        "  Expect a projected finish, a driving path, milestones at risk and an\n"
        "  effort overrun - and no recorded slip or forecast, because Jira\n"
        "  carries no baseline. Those arrive on the *second* export of the same\n"
        "  project, when the differ has two observations to compare.\n"
    )


if __name__ == "__main__":
    main()
