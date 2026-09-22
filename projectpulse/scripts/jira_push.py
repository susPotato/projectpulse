"""Send locally collected Jira issues to a server that cannot collect them.

    python -m scripts.jira_push --to https://projectpulse.fly.dev --project COWORKLOCAL

**Why this exists is a network fact, not a preference.** The Jira this was
built against sits behind bot scoring that lets an ordinary laptop through
and refuses a data centre. Measured both ways: `/rest/api/2/myself` answers
401 from a laptop and 403 with a challenge page from the hosted app. So the
server cannot fetch, and the only thing that has to move is the fetch.

It reads what `live.collect` already wrote into this machine's raw tables
and posts it. Nothing is re-fetched, so running this does not spend another
trip past the rate limiter, and the payload is the same JSON Jira sent.

The receiving end runs the ordinary extractor, convertor and pairing, so
what lands there is indistinguishable from a collection that happened on
that machine. When the block is lifted, the Collect button works and this
script stops being needed rather than becoming load-bearing.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.jira import JiraConnection  # noqa: E402
from app.models.raw import RawJiraChangelogs, RawJiraIssues  # noqa: E402

#: Issues per request. The receiving server does real work per batch -
#: extract, convert, pair - so a single post of two thousand issues is a
#: request that times out halfway through having written half of them.
BATCH = 50


def _local(project_key: str) -> tuple[list[dict], str]:
    """Rebuild the issues this machine collected, changelogs reattached.

    The collector splits an issue from its history deliberately, so they
    are separate evidence. Putting them back together is only so the wire
    format matches what Jira itself returns, which is what the receiving
    end knows how to read.
    """
    with session_scope() as session:
        row = session.scalar(
            select(JiraConnection).where(
                JiraConnection.project_key == project_key.upper())
        )
        if row is None:
            raise SystemExit(
                f"no local Jira connection for {project_key!r}. Save one on "
                f"Settings and collect before pushing."
            )
        params = json.dumps({"connection_id": row.id,
                             "board_key": row.project_key})

        histories: dict[str, list[dict]] = {}
        for change in session.scalars(
            select(RawJiraChangelogs).where(RawJiraChangelogs.params == params)
        ).all():
            issue_id = (json.loads(change.input or "{}") or {}).get("issue_id")
            if issue_id:
                histories.setdefault(str(issue_id), []).append(
                    json.loads(change.data))

        # Latest row per issue id. A second collection appends rather than
        # replaces, so without this a re-pushed project carries every issue
        # once per collection it has ever had.
        newest: dict[str, dict] = {}
        for raw in session.scalars(
            select(RawJiraIssues).where(RawJiraIssues.params == params)
            .order_by(RawJiraIssues.id)
        ).all():
            issue = json.loads(raw.data)
            newest[str(issue.get("id"))] = issue

        issues = []
        for issue_id, issue in newest.items():
            if issue_id in histories:
                issue["changelog"] = {"histories": histories[issue_id]}
            issues.append(issue)
        return issues, row.site


def _post(target: str, token: str, payload: dict) -> dict:
    request = urllib.request.Request(
        target.rstrip("/") + "/api/jira/ingest",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "X-Pulse-Admin-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail", body)
        except json.JSONDecodeError:
            detail = body[:300]
        raise SystemExit(f"{target} answered {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"could not reach {target}: {exc.reason}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", required=True,
                        help="the server to push to, e.g. https://projectpulse.fly.dev")
    parser.add_argument("--project", required=True, help="Jira project key")
    parser.add_argument("--token", default=os.environ.get("PULSE_ADMIN_TOKEN", ""),
                        help="admin token for the target (or PULSE_ADMIN_TOKEN)")
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be sent and send nothing")
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        raise SystemExit(
            "no admin token. Pass --token or set PULSE_ADMIN_TOKEN - it is "
            "the target server's token, not this machine's."
        )

    issues, site = _local(args.project)
    changes = sum(len((i.get("changelog") or {}).get("histories", []))
                  for i in issues)
    print(f"{len(issues)} issue(s) and {changes} change(s) collected locally "
          f"from {site}")
    if args.dry_run:
        print(f"dry run - would post to {args.to} in batches of {args.batch}")
        return 0

    sent = tasks = 0
    for start in range(0, len(issues), args.batch):
        batch = issues[start:start + args.batch]
        result = _post(args.to, args.token, {
            "project_key": args.project.upper(),
            "issues": batch,
            "url": f"{site}/rest/api/2/search",
        })
        sent += result.get("received", 0)
        tasks = result.get("tasks", tasks)
        print(f"  sent {sent}/{len(issues)}  ->  {result.get('extracted_issues')} "
              f"extracted, {result.get('tasks')} task(s)")

    print(f"\ndone. {tasks} task(s) on {args.to}, paired onto the delivery "
          f"project the connection there names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
