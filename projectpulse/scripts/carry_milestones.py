"""Carry a feature grouping from one source of a project onto another.

    python -m scripts.carry_milestones --from excel:Project:upload:cowork-local \
                                       --to jira:Project:1:COWORKLOCAL

Some things a spreadsheet carries are not in the tracker at all. CoWorkLocal
groups its work into 29 features - Core Platform, Cowork Chat, Knowledge &
RAG - and that grouping lives only in the sheet: every one of the 194 Jira
issues has an empty `components` field, so nothing downstream of Jira can
rebuild it. Dropping the spreadsheet source therefore loses the feature map
as a side effect nobody asked for.

The two sources describe the same work under the same titles, so the
grouping can be carried across on an exact title match before the old
source goes. Titles are matched exactly and case-sensitively: a fuzzy match
would attach work to the wrong feature, and a wrong grouping is worse than
an absent one because it still renders.

Rows that do not match are left alone and counted, never guessed at.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.domain import Milestone, Task  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", required=True,
                        help="source id that currently holds the grouping")
    parser.add_argument("--to", dest="target", required=True,
                        help="source id that should inherit it")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    with session_scope() as session:
        donors = session.scalars(
            select(Task).where(Task.project_id == args.source)).all()
        by_title = {
            (t.title or "").strip(): t.milestone_id
            for t in donors if t.milestone_id and (t.title or "").strip()
        }

        # The milestones themselves must belong to the surviving source, or
        # they are deleted with the old one and the tasks point at nothing.
        moved = 0
        for milestone in session.scalars(
            select(Milestone).where(Milestone.project_id == args.source)
        ).all():
            milestone.project_id = args.target
            moved += 1

        matched = unmatched = already = 0
        for task in session.scalars(
            select(Task).where(Task.project_id == args.target)
        ).all():
            if task.milestone_id:
                already += 1
                continue
            found = by_title.get((task.title or "").strip())
            if found:
                task.milestone_id = found
                matched += 1
            else:
                unmatched += 1

        print(f"milestones moved to {args.target}: {moved}")
        print(f"  tasks grouped     : {matched}")
        print(f"  no title match    : {unmatched}")
        print(f"  already grouped   : {already}")

        if args.dry_run:
            session.rollback()
            print("\ndry run - nothing was written")
            return 0

    print("\ncarried.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
