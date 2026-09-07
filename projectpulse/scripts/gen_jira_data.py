"""Generate Jira-shaped JSON for the demo, in place of a live Jira connection.

Faithful to what `GET /rest/api/3/search?expand=changelog` actually returns -
nested `fields`, `changelog.histories[].items[]`, Jira's `+0000` timestamp format -
because the replay collector must be the *same* code path a real connection uses.
The only thing being faked is the transport.

The timeline is the point. Excel snapshots can only ever say "sometime between
these two scans"; a Jira changelog says exactly when. Placing the environment slip
at 09:12 on 4 March - after the 2 March scan, before the 6 March scan - is what
makes it provably earlier than the QA backlog growth observed later, and so what
makes one causal chain in this dataset defensible rather than merely plausible.

    python -m scripts.gen_jira_data
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings

JIRA_TS = "%Y-%m-%dT%H:%M:%S.000+0000"


def _user(name: str) -> dict:
    return {"displayName": name, "accountId": name.lower().replace(" ", ".")}


def _issue(
    issue_id: str,
    key: str,
    summary: str,
    status: str,
    category: str,
    assignee: str,
    created: str,
    updated: str,
    duedate: str | None,
    histories: list[dict],
) -> dict:
    return {
        "id": issue_id,
        "key": key,
        "self": f"https://fpt-demo.atlassian.net/rest/api/3/issue/{issue_id}",
        "fields": {
            "summary": summary,
            "project": {"key": "HRMS", "name": "HRMS Portal V2"},
            "issuetype": {"name": "Task"},
            "status": {"name": status, "statusCategory": {"name": category}},
            "assignee": _user(assignee),
            "creator": _user("Nguyen Van A"),
            "created": created,
            "updated": updated,
            "duedate": duedate,
            "customfield_10016": None,  # story points, unset in this project
        },
        "changelog": {"startAt": 0, "maxResults": 100,
                      "total": len(histories), "histories": histories},
    }


def _history(history_id: str, author: str, created: str, items: list[dict]) -> dict:
    return {"id": history_id, "author": _user(author), "created": created, "items": items}


def _item(field: str, from_value: str | None, to_value: str | None) -> dict:
    return {
        "field": field,
        "fieldtype": "jira",
        "fromString": from_value,
        "toString": to_value,
    }


def build_search_response() -> dict:
    """The HRMS board, with the change history that makes ordering possible."""
    issues = [
        _issue(
            "10108", "HRMS-108", "Environment Setup", "Blocked", "In Progress",
            "Tran Quoc B",
            created="2026-02-16T09:00:00.000+0000",
            updated="2026-03-04T09:12:00.000+0000",
            duedate="2026-03-16",
            histories=[
                # 3 March: the environment goes down.
                _history("99881", "Tran Quoc B", "2026-03-03T16:40:00.000+0000",
                         [_item("status", "In Progress", "Blocked")]),
                # 4 March 09:12: the slip is committed. THE cause event - exact,
                # and after the 2 March scan but before the 6 March one.
                _history("99887", "Tran Quoc B", "2026-03-04T09:12:00.000+0000",
                         [_item("duedate", "2026-03-04", "2026-03-16"),
                          _item("description", "Provision UAT env", "Provision UAT env - vendor delay")]),
            ],
        ),
        _issue(
            "10114", "HRMS-114", "Integration build", "Blocked", "To Do",
            "Pham Hong D",
            created="2026-02-20T10:15:00.000+0000",
            updated="2026-03-05T11:20:00.000+0000",
            duedate="2026-04-01",
            histories=[
                _history("99902", "Pham Hong D", "2026-03-05T11:20:00.000+0000",
                         [_item("status", "To Do", "Blocked")]),
                _history("99903", "Pham Hong D", "2026-03-05T11:22:00.000+0000",
                         [_item("duedate", "2026-03-20", "2026-04-01")]),
            ],
        ),
        _issue(
            "10118", "HRMS-118", "UAT preparation", "To Do", "To Do",
            "Nguyen Van A",
            created="2026-02-20T10:20:00.000+0000",
            updated="2026-03-19T08:05:00.000+0000",
            duedate="2026-05-26",
            histories=[
                # 19 March: the consequence lands on the milestone.
                _history("99950", "Nguyen Van A", "2026-03-19T08:05:00.000+0000",
                         [_item("duedate", "2026-05-14", "2026-05-26")]),
            ],
        ),
        _issue(
            "10101", "HRMS-101", "Requirements sign-off", "Done", "Done",
            "Le Van C",
            created="2026-01-12T08:00:00.000+0000",
            updated="2026-01-30T17:00:00.000+0000",
            duedate="2026-01-30",
            histories=[
                _history("99801", "Le Van C", "2026-01-30T17:00:00.000+0000",
                         [_item("status", "In Progress", "Done")]),
            ],
        ),
    ]

    return {
        "expand": "schema,names,changelog",
        "startAt": 0,
        "maxResults": 50,
        "total": len(issues),
        "issues": issues,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out = (args.out or settings.data_root) / "jira"
    out.mkdir(parents=True, exist_ok=True)

    payload = build_search_response()
    path = out / "search_expand_changelog.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    changes = sum(
        len(h["items"]) for i in payload["issues"] for h in i["changelog"]["histories"]
    )
    print(f"wrote {len(payload['issues'])} issues and {changes} field changes to {path}")


if __name__ == "__main__":
    main()
