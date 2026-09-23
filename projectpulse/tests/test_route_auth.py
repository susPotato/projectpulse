"""Which routes a stranger may call.

Everything in this app was writable by anyone who could reach it. For most of
it that is a deliberate product choice - it is a demo, and a visitor
rearranging their own dashboard costs nothing. For two kinds of route it was
not a choice, just an omission:

**Routes that spend money.** Five of them call a language model on the
deployment's own API key. `/usage` exists so that spend is not invisible; it
should not also be anonymous.

**Routes that destroy a project.** `DELETE /api/projects/{id}` has no undo and
rebuilds nothing. "No undo" and "anybody may call it" do not belong together.

The test that matters most here is the last one: it reads the routing table, so
a route added later that spends money is caught by this file rather than by a
bill.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import admin
from app.api.main import app

#: Routes that reach a vendor, or destroy something with no undo. Each entry
#: carries a body that gets *past validation*, because a 422 would satisfy a
#: "not 200" assertion while proving nothing about authorisation - which is
#: exactly how the first version of this file passed two routes it had not
#: actually tested.
GUARDED = [
    ("POST", "/api/risks/drafts?project=excel:Project:1:HRMS", None),
    (
        "POST",
        "/api/dashboards/generate",
        {"scope_type": "project", "scope_id": "x", "prompt": "a board"},
    ),
    ("POST", "/api/custom-tiles/draft", {"raw_data": "a,b\n1,2"}),
    ("POST", "/api/custom-tiles/chat", {"messages": [{"role": "user", "content": "hi"}]}),
    ("POST", "/api/agent/chat", {"messages": [{"role": "user", "content": "hi"}]}),
    ("DELETE", "/api/projects/excel:Project:upload:whatever", None),
    # Makes this server clone a URL somebody typed, and the delete has
    # no undo. Same two reasons as the entries above.
    ("POST", "/api/repos", {"project_id": "x", "repo_url": ""}),
    ("DELETE", "/api/repos/excel:Project:upload:whatever", None),
]


@pytest.fixture(autouse=True)
def no_token(monkeypatch):
    """Default-deny is the state being tested, so start with no token set."""
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_a_stranger_is_refused(method, path, body):
    """No token and not loopback - and refused before anything runs."""
    response = TestClient(app).request(method, path, json=body)
    assert response.status_code == 403, f"{method} {path} answered {response.status_code}"


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_loopback_still_passes(method, path, body):
    """Local development must not need a token to use the app it is building.

    The call may still fail for its own reasons - no model configured, no such
    project - but it must get *past* the gate, which a 403 would disprove.
    """
    local = TestClient(app, client=("127.0.0.1", 5000))
    response = local.request(method, path, json=body)
    assert response.status_code != 403, f"{method} {path} refused loopback"


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_a_valid_token_passes(method, path, body, monkeypatch):
    monkeypatch.setenv(admin.TOKEN_ENV, "a-real-admin-token")
    response = TestClient(app).request(
        method, path, json=body, headers={admin.HEADER: "a-real-admin-token"}
    )
    assert response.status_code != 403


@pytest.mark.parametrize("method,path,body", GUARDED)
def test_a_wrong_token_is_still_refused(method, path, body, monkeypatch):
    monkeypatch.setenv(admin.TOKEN_ENV, "a-real-admin-token")
    response = TestClient(app).request(
        method, path, json=body, headers={admin.HEADER: "not-it"}
    )
    assert response.status_code == 403


#: Calling one of these means the handler can reach a vendor and spend money.
REACHES_A_MODEL = ("_narrator(", "attributed_drafter(", "drafter_for(", "run_chat(")

ROUTE = re.compile(r'@app\.(get|post|put|delete|patch)\("([^"]+)"')


def _handlers(source: str):
    """Yield `(method, path, body)` for each route, body scoped to that handler.

    Splitting on the next `@app.` instead is what the first version of this
    test did, and it was wrong: a plain module-level helper sitting between two
    routes gets swallowed into the one above it. That read `_draft_drafter()`'s
    body as part of `DELETE /api/risks/{risk_id}` and accused a route that
    touches nothing of spending money.

    So take the decorator, find its `def`, then consume every line that is
    blank or indented - which is exactly one function and nothing after it.
    """
    lines = source.splitlines()
    for i, line in enumerate(lines):
        head = ROUTE.match(line)
        if not head:
            continue

        j = i + 1
        while j < len(lines) and not lines[j].startswith("def "):
            j += 1
        j += 1

        body = []
        while j < len(lines) and (not lines[j].strip() or lines[j][:1].isspace()):
            body.append(lines[j])
            j += 1

        yield head.group(1).upper(), head.group(2), "\n".join(body)


def test_no_route_reaches_a_model_without_passing_the_gate():
    """The one that catches the *next* one.

    A route added later that calls a model is easy to write and impossible to
    notice: it works, it is not obviously public, and the only symptom is the
    bill.
    """
    source = Path("app/api/main.py").read_text(encoding="utf-8")

    offenders = [
        f"{method} {path}"
        for method, path, body in _handlers(source)
        if method != "GET"
        and any(marker in body for marker in REACHES_A_MODEL)
        and "admin.require" not in body
    ]

    assert not offenders, (
        "these mutating routes reach a language model without authorisation: "
        + ", ".join(offenders)
    )


def test_the_scanner_can_actually_see_the_routes_it_checks():
    """A scanner that matched nothing would pass forever and prove nothing."""
    source = Path("app/api/main.py").read_text(encoding="utf-8")
    seen = {
        f"{method} {path}"
        for method, path, body in _handlers(source)
        if any(marker in body for marker in REACHES_A_MODEL)
    }
    assert "POST /api/agent/chat" in seen
    assert "POST /api/custom-tiles/chat" in seen
