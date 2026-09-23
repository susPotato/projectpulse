"""The PowerShell sync tool, and the watermark that makes it incremental.

The tool exists because Jira's bot scoring lets a laptop through and refuses
the server, so the fetch happens on somebody's desk. What it did **not** do
was ask what the server already held: the JQL was `project = X ORDER BY
updated ASC` with no bound, so every run re-read every issue with its full
changelog, and every page was buffered in memory and posted only at the end.

Those two together made a large project impossible rather than slow. Read
seven pages, get refused on the eighth, exit - and the six hundred issues
already read were discarded, because nothing had been sent. The next run
asked for exactly the same volume and was refused in exactly the same place.
No amount of retrying could ever get through.

So: a watermark endpoint, a JQL bound by it, and a push per page. These
cover the server half and the substitution; the script itself is checked by
parsing it, which `test_the_template_is_valid_powershell` does.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db import session_scope
from app.models.tool import ToolJiraIssue

TEMPLATE = (Path(__file__).resolve().parent.parent
            / "app" / "api" / "static" / "sync-tool.ps1.tmpl")
KEY = "SYNCTOOLTEST"


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 5000))


@pytest.fixture(autouse=True)
def _clean():
    def drop():
        with session_scope() as session:
            for row in session.query(ToolJiraIssue).filter(
                    ToolJiraIssue.project_key == KEY).all():
                session.delete(row)

    drop()
    yield
    drop()


def _issue(session, issue_id: str, updated: datetime):
    session.merge(ToolJiraIssue(
        connection_id=9991, issue_id=issue_id, project_key=KEY,
        updated_at_src=updated,
    ))


# --------------------------------------------------------------------------
# The watermark
# --------------------------------------------------------------------------

def test_a_project_with_nothing_held_has_no_watermark(client):
    """An empty answer means "read everything", which is the first run."""
    body = client.get("/api/jira/watermark", params={"project_key": KEY}).json()
    assert body["since"] == ""
    assert body["issues_held"] == 0


def test_the_watermark_is_the_newest_issue_update(client):
    with session_scope() as session:
        _issue(session, "1", datetime(2026, 9, 1, 8, 30))
        _issue(session, "2", datetime(2026, 9, 20, 14, 5))
        _issue(session, "3", datetime(2026, 9, 11, 9, 0))

    body = client.get("/api/jira/watermark", params={"project_key": KEY}).json()
    assert body["since"] == "2026-09-20 14:05"
    assert body["issues_held"] == 3


def test_the_watermark_is_minute_precision(client):
    """`updated >=` is inclusive, so seconds would re-read the boundary issue
    on every single run - `live._jql` formats it the same way."""
    with session_scope() as session:
        _issue(session, "1", datetime(2026, 9, 20, 14, 5, 47))

    since = client.get("/api/jira/watermark",
                       params={"project_key": KEY}).json()["since"]
    assert since == "2026-09-20 14:05"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", since)


def test_the_project_key_is_matched_without_case(client):
    with session_scope() as session:
        _issue(session, "1", datetime(2026, 9, 20, 14, 5))

    body = client.get("/api/jira/watermark",
                      params={"project_key": KEY.lower()}).json()
    assert body["issues_held"] == 1


def test_a_blank_project_key_is_refused(client):
    assert client.get("/api/jira/watermark",
                      params={"project_key": "  "}).status_code == 400


def test_the_watermark_needs_no_admin_token(client):
    """The tool asks before it has any reason to hold a credential, and the
    answer is one timestamp about a project the caller already named."""
    outside = TestClient(app, client=("203.0.113.9", 5000))
    assert outside.get("/api/jira/watermark",
                       params={"project_key": KEY}).status_code == 200


# --------------------------------------------------------------------------
# The script
# --------------------------------------------------------------------------

def test_the_template_is_valid_powershell():
    """Parsed, not eyeballed. Nobody runs this file in CI and a syntax error
    would reach the person it was generated for."""
    import shutil
    import subprocess

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("no PowerShell on this machine")

    check = (
        "$e=$null;$t=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{TEMPLATE}',"
        "[ref]$t,[ref]$e)|Out-Null;"
        "if($e){$e|%{Write-Output $_.Message};exit 1}else{exit 0}"
    )
    done = subprocess.run([powershell, "-NoProfile", "-Command", check],
                          capture_output=True, text=True)
    assert done.returncode == 0, f"the template does not parse:\n{done.stdout}"


def test_the_script_asks_for_the_watermark_and_bounds_its_jql():
    """The whole point. Without the bound it re-reads the project every run,
    which is both the slow path and the one the gateway refuses."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "/api/jira/watermark" in text
    assert "updated >=" in text, (
        "the JQL carries no lower bound, so every run re-reads every issue"
    )


def test_the_script_sends_each_page_as_it_reads_it():
    """Buffering the project and posting at the end means a refusal on the
    last page discards every page before it - and the next run, starting from
    the same watermark, is refused in the same place. That loop never ends.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    send = text.index("Send-Page $issues")
    loop = text.index("while ($pages -lt $MaxPages)")
    assert send > loop, "the push must happen inside the paging loop"


def test_the_script_backs_off_rather_than_giving_up_on_one_refusal():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "Retry-After" in text, "Jira says how long to wait; ask it"
    assert "$MaxRetries" in text
    assert "429" in text


def test_the_script_caps_its_paging():
    """A project far larger than expected must not walk for hours."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "$MaxPages" in text


def test_the_script_names_a_field_list():
    """`fields=` unset returns every custom field the site defines, which on
    a corporate Jira is most of the weight of the request that gets refused."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "fields=$Fields" in text
    assert "customfield_10016" in text, (
        "story points feed the extractor; dropping them from `fields` would "
        "silently empty a column"
    )


def test_every_placeholder_the_route_fills_is_present():
    """A renamed placeholder leaves the literal `__SERVER__` in a script
    somebody then runs, and the failure is a DNS error about a hostname
    nobody typed."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for token in ("__SERVER__", "__JIRA_SITE__", "__PROJECT_KEY__",
                  "__PUSH_TOKEN__"):
        assert token in text, f"{token} is no longer in the template"


def test_the_generated_script_has_no_placeholders_left(client, monkeypatch):
    """What the browser actually downloads."""
    from app import admin
    from app.models.jira import JiraConnection

    monkeypatch.setenv(admin.TOKEN_ENV, "test-admin-token")
    with session_scope() as session:
        session.merge(JiraConnection(
            id=9991, project_id="excel:Project:1:SYNCTOOL",
            site="https://jira.example.com", email="", project_key=KEY,
            ciphertext="", hint="",
        ))

    response = client.get("/api/jira/sync-tool", params={"project_key": KEY})
    assert response.status_code == 200, response.text
    script = response.text

    assert "__SERVER__" not in script
    assert "__JIRA_SITE__" not in script
    assert "__PROJECT_KEY__" not in script
    assert "__PUSH_TOKEN__" not in script
    assert KEY in script
    assert "https://jira.example.com" in script

    with session_scope() as session:
        row = session.get(JiraConnection, 9991)
        if row is not None:
            session.delete(row)
