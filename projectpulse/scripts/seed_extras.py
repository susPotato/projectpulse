"""Seed the two things no source system produces: a second Program, and
resource allocation.

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

from scripts._bootstrap import bootstrap

bootstrap()

from app.db import session_scope  # noqa: E402
from app.ids import domain_id  # noqa: E402
from app.models.domain import Program, Project, Resource  # noqa: E402

#: A second, empty Program - so `/api/programs` is a real list, not a
#: single hardcoded row. Name matches the PM's own Programs-list mockup
#: (`Layout_Program`, image12: "Cloud-First Initiative - 2 Projects" was
#: their example; this one starts empty rather than inventing two more
#: fabricated projects nobody asked to see).
EXTRA_PROGRAM_ID = domain_id("manual", "Program", 0, "CLOUD")
EXTRA_PROGRAM_NAME = "Cloud-First Initiative"

#: (resource_name, role, project canonical id, allocation_percent). Tran Quoc
#: B is deliberately allocated on both HRMS and Example Project at a combined
#: 130% - the overallocation the "Resource Conflict" cross-project tile
#: exists to catch, and the same name already carries schedule rows on both
#: projects' sheets, so the story is at least internally consistent.
ALLOCATIONS: tuple[tuple[str, str, str, float], ...] = (
    ("Tran Quoc B", "Environment Engineer", "excel:Project:1:HRMS", 80.0),
    ("Tran Quoc B", "Environment Engineer", "excel:Project:1:EXPROJ", 50.0),
    ("Pham Hong D", "Developer", "excel:Project:1:HRMS", 60.0),
    ("Pham Hong D", "Developer", "excel:Project:1:EXPROJ", 40.0),
    ("My Nguyen", "QA Lead", "excel:Project:1:HRMS", 50.0),
    ("My Nguyen", "QA Lead", "excel:Project:1:SAIN", 40.0),
    ("Hoach Bach", "Delivery Lead", "excel:Project:1:SAIN", 70.0),
)


def main() -> None:
    with session_scope() as session:
        if session.get(Program, EXTRA_PROGRAM_ID) is None:
            session.add(Program(id=EXTRA_PROGRAM_ID, name=EXTRA_PROGRAM_NAME, status="Active"))

        written = 0
        skipped = []
        for name, role, project_id, allocation in ALLOCATIONS:
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
                )
            )
            written += 1

    print(f"seeded program {EXTRA_PROGRAM_NAME!r} and {written} resource allocation(s)")
    if skipped:
        print(f"  skipped (project not synced yet): {sorted(set(skipped))}")


if __name__ == "__main__":
    main()
