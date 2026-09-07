"""Collector: Jira JSON in, raw rows out.

A live connection would page `GET /rest/api/3/search?expand=changelog`; this reads
the same payload from disk. That substitution is deliberate and it is the *only*
thing faked - everything downstream of the raw table is the code a real connection
drives. Seeding domain rows directly would bypass the extractor, the convertor and
the precision model, and would prove nothing about the pipeline.

Like DevLake's `ResponseParser`, one HTTP response is split into many raw rows -
one per issue, one per changelog entry - rather than stored whole, so a domain row
can point at the exact fragment it came from rather than at a page of fifty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.models.raw import RawJiraChangelogs, RawJiraIssues

# What a live collector would have requested. Recorded on every raw row so the
# evidence panel can show a PM where a value came from.
SEARCH_URL = (
    "https://fpt-demo.atlassian.net/rest/api/3/search"
    "?jql=project%3DHRMS%20ORDER%20BY%20updated%20DESC&expand=changelog&startAt=0"
)


@dataclass
class CollectReport:
    issues: int = 0
    changelogs: int = 0
    source_files: int = 0


def collect(
    session,
    *,
    connection_id: int,
    board_key: str,
    data_dir: Path,
    now,
) -> CollectReport:
    """Read captured Jira payloads into `_raw_jira_*`."""
    report = CollectReport()
    params = json.dumps({"connection_id": connection_id, "board_key": board_key})

    for path in sorted(Path(data_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        report.source_files += 1

        for issue in payload.get("issues", []):
            changelog = issue.pop("changelog", {}) or {}

            session.add(
                RawJiraIssues(
                    params=params,
                    data=json.dumps(issue).encode("utf-8"),
                    url=SEARCH_URL,
                    input=None,
                    fetched_at=now,
                )
            )
            report.issues += 1

            # Each history entry is its own row. `input` carries the issue id: the
            # per-issue changelog endpoint does not repeat it in the payload, so
            # the extractor reads it back from here - the same arrangement DevLake
            # uses for its fan-out collectors.
            for history in changelog.get("histories", []):
                session.add(
                    RawJiraChangelogs(
                        params=params,
                        data=json.dumps(history).encode("utf-8"),
                        url=(
                            "https://fpt-demo.atlassian.net/rest/api/3/issue/"
                            f"{issue['id']}/changelog"
                        ),
                        input=json.dumps(
                            {"issue_id": issue["id"], "issue_key": issue["key"]}
                        ),
                        fetched_at=now,
                    )
                )
                report.changelogs += 1

    return report
