"""The synced Jira issues, as the backlog CSV the traceability pipeline reads.

The pipeline's `tickets` and `governance` stages read a tracker export - a
workbook or CSV - and until this module the only way to give them one was to
upload it on Settings > Sources. A project whose Jira link had already synced
every issue still had to have the same issues exported and uploaded by hand.
Now a trace with no uploaded export builds one from the synced issues; an
uploaded export still wins, because somebody chose it.

Generalised from the scratchpad exporter the first Jira-based run was built
with (2026-09, CoWorkLocal), keeping the two things it learned by looking at
the payload rather than assuming:

* **The prose is often not in `description`.** On CoWorkLocal 19 of 190 issues
  filled it; the roles and the acceptance form lived in custom fields. Which
  custom fields matter differs per Jira instance, so they are chosen by
  measurement, not by id: a text field is kept when its values *vary* across
  issues. Template boilerplate is the same on every issue it appears on, and
  carrying 190 copies of one placeholder would be carrying noise.
* **`components` may be empty on every issue.** The component column then
  falls back to the issue type - the pipeline groups by it, and grouping by
  something real beats grouping by a column of blanks.

Reads `raw_jira_issues`, the append-only log the sync writes, taking the
newest copy of each issue.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from typing import Any

from sqlalchemy import select

#: A template slot nobody replaced. Whole-field markers only, so a field is
#: dropped when it is *entirely* boilerplate, never trimmed.
BOILERPLATE = re.compile(r"^\s*(<[^>]*>|Insert .*here if required)\s*$", re.I)

#: A custom field whose single commonest value covers more than this share of
#: the issues that fill it is template text, not something written per issue.
TEMPLATE_SHARE = 0.5

COLUMNS = ("Issue key", "Summary", "Description", "Status", "Issue Type",
           "Assignee", "Component", "Project")


def _text(value: Any) -> str:
    """Jira Cloud sends ADF documents; Data Center sends a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [value["text"]] if value.get("text") else []
        parts += [_text(c) for c in value.get("content") or []]
        joiner = "\n" if value.get("type") in ("paragraph", "listItem") else ""
        return joiner.join(p for p in parts if p)
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    return str(value)


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name")
                   or value.get("value") or "")
    return str(value or "")


def _useful(text: str) -> str:
    text = (text or "").strip()
    return "" if not text or BOILERPLATE.match(text) else text


def _is_prose(value: Any) -> bool:
    """A custom field value a person typed: a string, or an ADF document."""
    if isinstance(value, str):
        return any(c.isalpha() for c in value) and len(value.strip()) >= 3
    return isinstance(value, dict) and value.get("type") == "doc"


def issues_for(session, project_key: str) -> list[dict]:
    """The newest synced copy of every issue in `project_key`."""
    from app.models.raw import RawJiraIssues

    prefix = f"{project_key.strip().upper()}-"
    newest: dict[str, dict] = {}
    for raw in session.scalars(select(RawJiraIssues).order_by(RawJiraIssues.id)):
        try:
            issue = json.loads(raw.data)
        except (TypeError, ValueError):
            continue
        if str(issue.get("key", "")).upper().startswith(prefix):
            newest[str(issue.get("id") or issue.get("key"))] = issue
    return list(newest.values())


def _prose_fields(issues: list[dict]) -> list[str]:
    """Custom fields that carry per-issue prose rather than template text."""
    values: dict[str, list[str]] = {}
    for issue in issues:
        for name, value in (issue.get("fields") or {}).items():
            if name.startswith("customfield_") and _is_prose(value):
                text = _useful(_text(value))
                if text:
                    values.setdefault(name, []).append(text)
    kept = []
    for name, texts in values.items():
        top = Counter(texts).most_common(1)[0][1]
        if len(texts) > 1 and top / len(texts) <= TEMPLATE_SHARE:
            kept.append(name)
    return sorted(kept)


def rows_for(issues: list[dict], project_key: str) -> list[dict[str, str]]:
    extra = _prose_fields(issues)
    rows = []
    for issue in issues:
        f = issue.get("fields") or {}
        body = "\n".join(p for p in (
            _useful(_text(f.get("description"))),
            *(_useful(_text(f.get(name))) for name in extra),
        ) if p)
        components = ", ".join(_name(c) for c in f.get("components") or [] if _name(c))
        rows.append({
            "Issue key": issue.get("key", ""),
            "Summary": f.get("summary") or "",
            "Description": body,
            "Status": _name(f.get("status")),
            "Issue Type": _name(f.get("issuetype")),
            "Assignee": _name(f.get("assignee")),
            "Component": components or _name(f.get("issuetype")),
            "Project": project_key,
        })

    def order(row: dict) -> tuple:
        tail = row["Issue key"].rsplit("-", 1)[-1]
        return (0, int(tail)) if tail.isdigit() else (1, row["Issue key"])

    return sorted(rows, key=order)


#: A status in Jira's "done" category that means the work was dropped, not
#: delivered. CoWorkLocal files `Cancelled` under done, as Jira's defaults do;
#: counting it would ask the code to prove features nobody built.
NOT_DELIVERED = re.compile(r"cancel|reject|won'?t|abandon|duplicate|invalid|obsolete",
                           re.I)


def done_statuses(issues: list[dict]) -> list[str]:
    """The statuses that mean delivered, from Jira's own status categories.

    What the pipeline's `--done-status` wants, and what an uploaded export
    has to be told by hand. Jira already knows: every status carries a
    category, and `done` is the one that means finished.
    """
    names = set()
    for issue in issues:
        status = (issue.get("fields") or {}).get("status") or {}
        if ((status.get("statusCategory") or {}).get("key") == "done"
                and status.get("name") and not NOT_DELIVERED.search(status["name"])):
            names.add(status["name"])
    return sorted(names)


def to_csv(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS))
    writer.writeheader()
    writer.writerows(rows)
    # BOM so Excel opens the Vietnamese text as UTF-8 if somebody downloads it.
    return buffer.getvalue().encode("utf-8-sig")


def backlog_for_project(session, project_id: str) -> dict[str, Any] | None:
    """The CSV for a project's Jira link, or None when there is nothing synced.

    Returns `{"content", "name", "project_key", "issues", "done_status"}`. A project with
    several links (rare) gets every linked key's issues in one backlog.
    """
    from app.models.jira import JiraConnection

    links = session.scalars(
        select(JiraConnection).where(JiraConnection.project_id == project_id)
    ).all()
    keys = sorted({row.project_key.strip().upper() for row in links if row.project_key})
    rows: list[dict[str, str]] = []
    done: set[str] = set()
    for key in keys:
        issues = issues_for(session, key)
        rows += rows_for(issues, key)
        done.update(done_statuses(issues))
    if not rows:
        return None
    return {
        "content": to_csv(rows),
        "name": f"jira-{'-'.join(keys)}.csv",
        "project_key": ",".join(keys),
        "issues": len(rows),
        "done_status": ",".join(sorted(done)),
    }
