"""Turn a Jira issue export into the schedule workbook this app reads.

    python -m scripts.from_jira_export "Jira Cowork Local 1.xlsx"
    python -m scripts.from_jira_export export.xlsx --out data/coworklocal_schedule.xlsx

**You usually do not need this.** Settings > Sources accepts a Jira export
directly - pick "Jira issue export" as the kind and the server runs exactly the
conversion below, which is the only route available on a deployed instance
where there is no shell to run this in.

This stays for the cases the browser cannot cover: converting a batch of
exports, inspecting the sheet before anything is ingested, or keeping the
converted workbook as an artifact.

The conversion itself lives in `app/ingest/sources/jira/export_sheet.py`, not
here - two entry points must not convert the same file two different ways. See
that module for what a Jira export can and cannot tell this product, which is
the part worth reading.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

from app.ingest.sources.jira.export_sheet import (  # noqa: E402,F401
    HEADER_SEARCH_ROWS,
    SOURCES,
    NotAJiraExport,
    convert,
    coverage,
    read_export,
    write_schedule,
)


def report(cover: dict) -> None:
    """The coverage report, printed.

    Every run, rather than behind a flag: a conversion that "succeeded" into a
    schedule with no baseline and no edges is the case worth being loud about,
    and it is also the likely one.
    """
    total = cover["issues"]
    print(f"\n  {total} issue(s) -> {len(cover['columns'])} schedule columns\n")
    for entry in cover["columns"]:
        origin = (
            f"from {entry['source']!r}"
            if entry["source"]
            else "no Jira column feeds this"
        )
        flag = "  " if entry["filled"] else "! "
        print(
            f"  {flag}{entry['column']:16} {entry['filled']:>3}/{entry['of']}  {origin}"
        )
    for note in cover["notes"]:
        print(f"\n  ! {note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export", type=Path, help="the Jira .xlsx export")
    parser.add_argument(
        "--out", type=Path, default=None, help="where to write the schedule workbook"
    )
    parser.add_argument(
        "--sheet", default=None, help="which tab of the export holds the table"
    )
    args = parser.parse_args()

    if not args.export.exists():
        raise SystemExit(f"no such file: {args.export}")

    try:
        headers, rows = read_export(args.export, args.sheet)
        records, chosen = convert(headers, rows)
        if not records:
            raise NotAJiraExport(
                "the export has a header row but no issues under it."
            )
    except NotAJiraExport as exc:
        # Reported as a message, not a traceback: the reader is being told their
        # file is the wrong shape, which is not a crash.
        raise SystemExit(str(exc)) from exc

    out = args.out or args.export.with_name(args.export.stem + "_schedule.xlsx")
    write_schedule(records, out)
    print(f"wrote {out}")
    report(coverage(records, chosen, headers, rows))
    print(
        "\n  Load it: Settings > Sources > upload, kind 'schedule'. Or upload the\n"
        "  original export there as 'Jira issue export' and skip this step.\n"
    )


if __name__ == "__main__":
    main()
