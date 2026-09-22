"""Rebuild the tool and domain layers from Jira rows already collected.

    python -m scripts.jira_rebuild --project COWORKLOCAL

**Why this exists.** Collection and conversion are one route today, but they
were not always: the 190 CoWorkLocal issues were fetched before the collect
route learned to carry on into the domain layer, so the raw and tool rows
were complete while `tasks` and `state_changes` held nothing from them. The
only way to fix that was to collect again - another few hundred requests
past a rate limiter that answers 429 to bursts, to re-fetch bytes already
on disk.

So this re-runs everything downstream of the fetch: extract, convert, and
the invariant-7 pairing. It never contacts Jira. That makes it the right
tool whenever the pipeline below the fetch changes - which it will again -
and it is safe to run repeatedly, because every stage merges on a
deterministic id rather than appending.

It is deliberately a script and not a button. Re-running a conversion is a
maintenance action with a blast radius of one project, and the person doing
it should see the counts.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.ingest.sources.jira import convertor  # noqa: E402
from app.ingest.sources.jira.extractor import (  # noqa: E402
    extract_changelogs,
    extract_issues,
)
from app.models.jira import JiraConnection  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True,
                        help="the Jira project key to rebuild, e.g. COWORKLOCAL")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the counts and write nothing")
    args = parser.parse_args(argv)

    key = args.project.strip().upper()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with session_scope() as session:
        row = session.scalar(
            select(JiraConnection).where(JiraConnection.project_key == key))
        if row is None:
            raise SystemExit(
                f"no Jira connection named {key!r}. Save one on Settings "
                f"first - the rebuild needs it to know which delivery "
                f"project these issues belong to."
            )

        issues = extract_issues(session, connection_id=row.id)
        changes = extract_changelogs(session, connection_id=row.id)
        session.flush()

        jira_project = convertor.ensure_project(
            session, connection_id=row.id, project_key=key, name=key)
        session.flush()
        tasks = convertor.convert_issues(
            session, connection_id=row.id, project_id=jira_project,
            project_key=key)
        state_changes = convertor.convert_changelogs(
            session, connection_id=row.id, project_key=key, now=now)
        session.flush()
        starts = convertor.derive_start_dates(session, project_id=jira_project)
        ends = convertor.derive_end_dates(session, project_id=jira_project)

        print(f"{key}  (connection {row.id})")
        print(f"  tool layer   : {issues} issue(s), {changes} change(s)")
        print(f"  domain layer : {tasks} task(s), {state_changes} state change(s)")
        print(f"  start dates  : {starts} derived from the changelog")
        print(f"  end dates    : {ends} derived from the changelog")
        print(f"  jira project : {jira_project}")

        if args.dry_run:
            session.rollback()
            print("\ndry run - nothing was written")
            return 0

        delivery_id = row.project_id

    # Pair the two source ids onto one delivery project - invariant 7. Imported
    # from the API rather than reimplemented, so a rebuild pairs by exactly the
    # same rule a collection does; two copies of this would be two chances to
    # disagree about which project the work belongs to.
    from app.api.main import _pair_source

    paired = _pair_source(delivery_id, jira_project)
    if paired:
        print(f"  paired onto  : {paired}")
    else:
        print(f"  not paired   : {delivery_id} is not a registered project, so "
              f"the Jira rows stand on their own")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
