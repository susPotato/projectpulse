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

MARKER = "#__PS__"


def _powershell_section() -> str:
    """Everything the batch header hands to PowerShell.

    The file is a `.cmd` first: the head is batch, and `@echo off` is a parse
    error to PowerShell. Only the part after the marker is PowerShell, and it
    is the only part worth parsing - it is also exactly the substring the
    header's `Invoke-Expression` receives.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    index = text.index(MARKER)
    return text[index:]


def test_the_powershell_section_is_valid_powershell(tmp_path):
    """Parsed, not eyeballed. Nobody runs this file in CI and a syntax error
    would reach the person it was generated for."""
    import shutil
    import subprocess

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("no PowerShell on this machine")

    section = tmp_path / "section.ps1"
    section.write_text(_powershell_section(), encoding="utf-8")

    check = (
        "$e=$null;$t=$null;"
        f"[System.Management.Automation.Language.Parser]::ParseFile('{section}',"
        "[ref]$t,[ref]$e)|Out-Null;"
        "if($e){$e|%{Write-Output $_.Message};exit 1}else{exit 0}"
    )
    done = subprocess.run([powershell, "-NoProfile", "-Command", check],
                          capture_output=True, text=True)
    assert done.returncode == 0, (
        f"the PowerShell section does not parse:\n{done.stdout}"
    )


def test_the_file_starts_as_a_batch_script():
    """Why it is a `.cmd` at all.

    A default Windows install refuses a downloaded `.ps1` twice over - the
    execution policy, then the mark of the web - and the window closes before
    either message can be read, so the script cannot report its own failure.
    Reproduced: `cannot be loaded because running scripts is disabled on this
    system`. A `.cmd` double-clicks, and hands the PowerShell to an
    interpreter allowed to run it.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.startswith("@echo off"), (
        "the batch header is gone, so this is a .ps1 again and will not run "
        "by double-click on a default Windows install"
    )
    assert "-ExecutionPolicy Bypass" in text
    assert MARKER in text


def test_the_marker_appears_exactly_once():
    """`IndexOf` takes the first hit, so a second spelling anywhere breaks it.

    Caught exactly this: a `rem` line added to the header to explain the
    marker wrote it out whole, so the header found its own comment and handed
    PowerShell the tail of the batch script - `if errorlevel 1 pause`,
    `endlocal`, `exit /b` - which is a syntax error with no obvious source.

    Counting is the test rather than "not in the head", because slicing at
    the first hit is the same flawed lookup and passes vacuously.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.count(MARKER) == 1, (
        f"the marker {MARKER!r} appears {text.count(MARKER)} times. It must "
        f"appear once - the batch header's IndexOf takes the first, and "
        f"anything before the real marker sends PowerShell batch commands. "
        f"Spell it in two halves anywhere it has to be mentioned."
    )


def test_the_script_finds_itself_without_psscriptroot():
    """`$PSScriptRoot` is empty under `Invoke-Expression`.

    Which is how the header runs it. Without the environment variable the
    saved-token path becomes `Join-Path $null` - a terminating error under
    `ErrorActionPreference = Stop`, before the first pause, so the window
    would close instantly with nothing on it.
    """
    section = _powershell_section()
    assert "$env:PULSE_TOOL_PATH" in section
    assert "Join-Path $Here" in section
    assert "Join-Path $PSScriptRoot" not in section


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


def test_a_403_from_jira_is_not_retried_as_a_rate_limit():
    """403 is two failures wearing one number, and `connect.py` says so.

    Jira's own 403 is "authenticated, but refused" - a permission on the
    account or a restriction on the site. Waiting does not fix it, so
    retrying spends two minutes arriving at the same answer and then reports
    a rate limit, sending somebody to look for a queue that is not there. A
    bot filter in front of Jira also answers 403, but with an HTML page
    instead of Jira's JSON, and that one is worth waiting out.
    """
    section = _powershell_section()
    assert re.search(r"\$code\s+-eq\s+403", section), "403 is not handled apart"
    assert "!doctype" in section.lower(), (
        "nothing tells Jira's own 403 from a filter's HTML challenge page, so "
        "a permission problem is retried as though it were a rate limit"
    )
    # And the non-HTML case must stop rather than fall into the backoff.
    stop = section.index("Jira refused this account (403)")
    backoff = section.index("$code -eq 429 -or $code -eq 403")
    assert stop < backoff


def test_the_script_backs_off_rather_than_giving_up_on_one_refusal():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "Retry-After" in text, "Jira says how long to wait; ask it"
    assert "$MaxRetries" in text
    assert "429" in text


def test_a_refusal_with_no_retry_after_is_waited_out_once_and_then_left():
    """Retrying into a throttle is how a short block becomes a long one.

    A `Retry-After` is a limit with a stated end and is worth waiting out.
    A WAF throttling an address usually sends none and usually does not mean
    seconds - and requests made into it can restart the window. The old
    behaviour spent four attempts over two minutes there and reported a
    gateway problem; measured against a stand-in that always answers 429
    with no header, the tool now makes exactly two.

    Stopping is free: what was read is already sent, and the next run
    resumes from the new watermark.
    """
    section = _powershell_section()
    assert "$PatientWait" in section
    assert "$BlindWaited" in section, "nothing caps the blind waits"
    # The budget is the run, not the page - the next page is throttled too.
    assert re.search(r"\$BlindWaited\s*=\s*\$false", section)
    # And the stated-length path must still retry, since that limit ends.
    stated = section.index("Jira asked for $wait s, waiting")
    blind = section.index("no wait time given")
    assert stated < blind


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


def test_the_field_list_is_allowed_to_fail():
    """The regression that broke the tool on a real Data Center instance.

    `customfield_10016` is story points *on Jira Cloud*. Data Center numbers
    its custom fields differently, and Jira rejects the **whole request** with
    a 400 when `fields` names one that does not exist - it does not just omit
    the field. So naming a field list turned a working sync into one that
    failed on page one, on the only instance this is pointed at. (194 of 194
    issues already had an empty `story_points`, which is what that field id
    not existing looks like from the other end.)

    The list is an optimisation. It has to degrade to what the tool did
    before it existed: read every field, slower and larger and correct.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "$UseFields" in text, "the field list cannot be turned off"
    assert re.search(r"\$code\s+-eq\s+400\s+-and\s+\$UseFields", text), (
        "nothing catches a 400 caused by the field list, so an instance "
        "without one of these fields cannot sync at all"
    )
    # And the fallback must actually change the request, not just log.
    assert re.search(r"if\s*\(\$UseFields\)\s*\{\s*\$url\s*\+=", text), (
        "`fields` is still always appended, so turning it off changes nothing"
    )


def test_a_refusal_reports_what_jira_said():
    """"Jira answered 400" is a message nobody can act on.

    The reason is in the body, and it is this site's own words about this
    site's own fields - which is exactly what the reader needs and exactly
    what the first version threw away.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "ErrorDetails" in text
    assert "errorMessages" in text


def test_every_placeholder_the_route_fills_is_present():
    """A renamed placeholder leaves the literal `__SERVER__` in a script
    somebody then runs, and the failure is a DNS error about a hostname
    nobody typed."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for token in ("__SERVER__", "__JIRA_SITE__", "__PROJECT_KEY__",
                  "__PUSH_TOKEN__"):
        assert token in text, f"{token} is no longer in the template"


def test_the_page_and_the_route_agree_on_the_extension():
    """The page's `download` attribute overrides `Content-Disposition`.

    So the server can serve `sync-x.cmd` and the browser still save
    `sync-x.ps1`, which Windows will not double-click - the exact failure
    this change exists to remove, reintroduced by a one-word mismatch in a
    different file.
    """
    page = (Path(__file__).resolve().parent.parent / "app" / "api" / "static"
            / "settings.html").read_text(encoding="utf-8")
    route = (Path(__file__).resolve().parent.parent / "app" / "api"
             / "main.py").read_text(encoding="utf-8")

    assert 'a.download = "sync-" + key.toLowerCase() + ".cmd"' in page, (
        "the page still saves the download as .ps1"
    )
    assert 'filename="sync-{key.lower()}.cmd"' in route, (
        "the route still names the attachment .ps1"
    )


def test_the_download_is_served_with_crlf(client, monkeypatch):
    """`cmd.exe` parses batch with carriage returns in mind.

    A LF-only `.cmd` ranges from working to silently skipping lines, and the
    template's endings depend on whichever machine last edited it - the
    measured download had LF throughout before this was normalised.
    """
    from app import admin
    from app.models.jira import JiraConnection

    monkeypatch.setenv(admin.TOKEN_ENV, "test-admin-token")
    with session_scope() as session:
        session.merge(JiraConnection(
            id=9992, project_id="excel:Project:1:CRLF",
            site="https://jira.example.com", email="", project_key="CRLFTEST",
            ciphertext="", hint="",
        ))

    raw = client.get("/api/jira/sync-tool",
                     params={"project_key": "CRLFTEST"}).content
    assert raw.count(b"\r\n") > 100
    assert raw.count(b"\n") == raw.count(b"\r\n"), "a bare LF survived"
    assert raw.startswith(b"@echo off")

    with session_scope() as session:
        row = session.get(JiraConnection, 9992)
        if row is not None:
            session.delete(row)


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
