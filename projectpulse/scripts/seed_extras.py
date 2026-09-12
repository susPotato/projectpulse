"""Seed the things no source system produces: a second Program, resource
allocation, and a starting dashboard layout.

    python -m scripts.seed_extras

Both are hand-authored rather than read from a fake collector - deliberately,
because there is no collector to fake. `Resource` has been an empty table
since the schema was written (CLAUDE.md section 8: "resources stays empty on
purpose - the template has no allocation data"); this is the same call the
`Risk` register already made (`app/risks/`) - a PM/portfolio-owner's own
judgement is the data here, not something derived from an ingested sheet, so
it does not need the differ or the precision model. Every row is idempotent
(`session.merge` on a `domain_id("manual", ...)` id), so re-running this is a
no-op.

Must run after the excel syncs that create HRMS/SAIN/Example Project's
`Project` rows (see `scripts/replay.py`) - it looks them up, it does not
create them.
"""

from __future__ import annotations

from datetime import date

from scripts._bootstrap import bootstrap

bootstrap()

from app.dashboard.service import seed_default_dashboard  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.ids import domain_id  # noqa: E402
from app.models.domain import Program, Project, Resource  # noqa: E402
from app.scope import program_domain_id  # noqa: E402

#: A second, empty Program - so `/api/programs` is a real list, not a
#: single hardcoded row. Name matches the PM's own Programs-list mockup
#: (`Layout_Program`, image12: "Cloud-First Initiative - 2 Projects" was
#: their example; this one starts empty rather than inventing two more
#: fabricated projects nobody asked to see).
#: Keyed through `scope.program_domain_id` rather than `domain_id("manual", ...)`
#: so every program in the database shares one source-neutral namespace. A
#: program is not an artifact of the system its documents arrived from, and
#: putting a source name in the id is what made one program exist twice.
EXTRA_PROGRAM_ID = program_domain_id("CLOUD")
EXTRA_PROGRAM_NAME = "Cloud-First Initiative"

#: (resource_name, role, project canonical id, allocation_percent, window).
#:
#: **The windows are the point, not decoration.** Contention is the excess of
#: demand over capacity *in a period*, and these four people are each a
#: different case of it - together they are the regression test for the three
#: ways the old "sum the percentages, flag over 100" check was wrong:
#:
#: * **Tran Quoc B** - 80% + 50% over the same three months. A real conflict,
#:   caught before and caught now. The 130% figure the CLAUDE.md demo notes
#:   quote is this one, unchanged.
#: * **Pham Hong D** - 60% then 50%, in windows that do not overlap. 110%
#:   nominal, and *not* a conflict: nobody is being asked to do two things at
#:   once. The old check had no dates and would have flagged it.
#: * **My Nguyen** - 50% + 40% over the same window. 90% nominal, so the old
#:   check called it clean; but a person is not 100% available to project work,
#:   and against a supply discounted for non-project load it is a genuine, small
#:   shortfall. The case the old check missed.
#: * **Hoach Bach** - one project. Never a conflict, and it should cost nothing
#:   to say so.
ALLOCATIONS: tuple[tuple[str, str, str, float, str, str], ...] = (
    ("Tran Quoc B", "Environment Engineer", "excel:Project:1:HRMS", 80.0, "2026-02-01", "2026-04-30"),
    ("Tran Quoc B", "Environment Engineer", "excel:Project:1:EXPROJ", 50.0, "2026-02-01", "2026-04-30"),
    ("Pham Hong D", "Developer", "excel:Project:1:HRMS", 60.0, "2026-01-01", "2026-02-28"),
    ("Pham Hong D", "Developer", "excel:Project:1:EXPROJ", 50.0, "2026-03-01", "2026-04-30"),
    ("My Nguyen", "QA Lead", "excel:Project:1:HRMS", 50.0, "2026-02-01", "2026-04-30"),
    ("My Nguyen", "QA Lead", "excel:Project:1:SAIN", 40.0, "2026-02-01", "2026-04-30"),
    ("Hoach Bach", "Delivery Lead", "excel:Project:1:SAIN", 70.0, "2026-01-01", "2026-04-30"),
)


#: The canvas each demo scope opens on. Without this a fresh `scripts.replay`
#: leaves both dashboards blank - `get_dashboard` auto-creates an *empty* one
#: on first look, which is correct for a new scope in a real deployment and
#: means the biggest feature in the app opens as "Click Add Tiles" for anyone
#: who just built the demo database. Only applied to a dashboard that has no
#: tiles, so this never overwrites a layout somebody arranged.
DASHBOARDS: tuple[tuple[str, str, str], ...] = (
    ("program", program_domain_id("DEFAULT"), "it_portfolio_dashboard"),
    ("project", "excel:Project:1:HRMS", "project_delivery_review"),
)


def main() -> None:
    with session_scope() as session:
        if session.get(Program, EXTRA_PROGRAM_ID) is None:
            session.add(Program(id=EXTRA_PROGRAM_ID, name=EXTRA_PROGRAM_NAME, status="Active"))

        written = 0
        skipped = []
        for name, role, project_id, allocation, start, end in ALLOCATIONS:
            if session.get(Project, project_id) is None:
                # The excel sync that creates this project hasn't run yet -
                # report it rather than writing a Resource row with a
                # dangling project_id.
                skipped.append(project_id)
                continue
            resource_id = domain_id("manual", "Resource", 0, project_id, name)
            session.merge(
                Resource(
                    id=resource_id,
                    project_id=project_id,
                    resource_name=name,
                    role=role,
                    allocation_percent=allocation,
                    period_start=date.fromisoformat(start),
                    period_end=date.fromisoformat(end),
                )
            )
            written += 1

        boards = []
        for scope_type, scope_id, template in DASHBOARDS:
            if scope_type == "project" and session.get(Project, scope_id) is None:
                skipped.append(scope_id)
                continue
            if seed_default_dashboard(session, scope_type, scope_id, template):
                boards.append(f"{scope_type} {template}")

    print(f"seeded program {EXTRA_PROGRAM_NAME!r} and {written} resource allocation(s)")
    for board in boards:
        print(f"  dashboard: {board}")
    if skipped:
        print(f"  skipped (project not synced yet): {sorted(set(skipped))}")


if __name__ == "__main__":
    main()
