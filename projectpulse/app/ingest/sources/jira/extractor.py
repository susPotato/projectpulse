"""Extractor: raw Jira JSON -> tool-layer rows, still in Jira's own shape.

No normalization of meaning happens here - `In Progress` stays `In Progress`.
The only job is to turn stored JSON into typed columns, stamping each row with
the raw fragment it came from so provenance survives the hop.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select

from app.models.raw import RawJiraChangelogs, RawJiraIssues
from app.models.tool import ToolJiraChangelog, ToolJiraIssue

JIRA_TS = "%Y-%m-%dT%H:%M:%S.%f%z"


def parse_jira_datetime(value: str | None) -> datetime | None:
    """Jira sends `2026-03-04T09:12:00.000+0000`, which %z accepts."""
    if not value:
        return None
    try:
        return datetime.strptime(value, JIRA_TS)
    except ValueError:
        # Some deployments emit `+00:00`. Accept both rather than lose the row.
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def _display_name(node: dict | None) -> str | None:
    return (node or {}).get("displayName")


def extract_issues(session, *, connection_id: int, since: datetime | None = None) -> int:
    """`_raw_jira_issues` -> `_tool_jira_issues`."""
    query = select(RawJiraIssues)
    if since is not None:
        query = query.where(RawJiraIssues.fetched_at >= since)

    count = 0
    for raw in session.scalars(query).all():
        issue = json.loads(raw.data)
        fields = issue.get("fields", {}) or {}
        status = fields.get("status") or {}

        tool = ToolJiraIssue(
            connection_id=connection_id,
            issue_id=str(issue["id"]),
            issue_key=issue.get("key"),
            project_key=(fields.get("project") or {}).get("key"),
            summary=fields.get("summary"),
            issue_type=(fields.get("issuetype") or {}).get("name"),
            status=status.get("name"),
            status_category=(status.get("statusCategory") or {}).get("name"),
            assignee=_display_name(fields.get("assignee")),
            created_at_src=parse_jira_datetime(fields.get("created")),
            updated_at_src=parse_jira_datetime(fields.get("updated")),
            due_date=fields.get("duedate"),
            story_points=fields.get("customfield_10016"),
        )
        tool.set_raw_origin(raw)
        session.merge(tool)
        count += 1

    return count


def extract_changelogs(
    session, *, connection_id: int, since: datetime | None = None
) -> int:
    """`_raw_jira_changelogs` -> `_tool_jira_changelogs`.

    One history entry can move several fields at once, and each becomes its own
    row: a state change is per field, not per edit.
    """
    query = select(RawJiraChangelogs)
    if since is not None:
        query = query.where(RawJiraChangelogs.fetched_at >= since)

    count = 0
    for raw in session.scalars(query).all():
        history = json.loads(raw.data)
        context = json.loads(raw.input) if raw.input else {}
        issue_id = str(context.get("issue_id") or "")
        if not issue_id:
            continue  # cannot attach the change to anything; skip rather than guess

        created = parse_jira_datetime(history.get("created"))
        if created is None:
            continue  # a change with no timestamp cannot carry an ordering claim

        for item in history.get("items", []) or []:
            tool = ToolJiraChangelog(
                connection_id=connection_id,
                changelog_id=str(history["id"]),
                field=item.get("field", "unknown"),
                issue_id=issue_id,
                author=_display_name(history.get("author")),
                from_value=item.get("fromString"),
                to_value=item.get("toString"),
                created_at_src=created,
            )
            tool.set_raw_origin(raw)
            session.merge(tool)
            count += 1

    return count
