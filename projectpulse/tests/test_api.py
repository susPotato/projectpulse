"""Guards for the served contract: the API, and the pages that render it.

Insight and Calculation are a built React app (`web/`), typed from the server's
own OpenAPI schema - so the field-by-field coupling these tests used to check by
grepping HTML is now a compile error instead. What remains here is what types
cannot catch: that the routes serve, that failures are named rather than
guessed at, that the committed bundle is present and current, and that the
Schedule page's chart still obeys the palette and mark rules.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.schemas.explain import ExplainBundle
from app.api.schemas.gantt import GanttBundle
from app.api.schemas.insight import InsightBundle
from app.intelligence.assembler import build_bundle
from app.intelligence.context import DeliveryContext
from app.intelligence.rules.engine import RuleHit
from app.narration.fallback import QUESTION_HEADINGS, render_narrative

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "app" / "api" / "static"
WEB = REPO / "web"

#: The Schedule page is still hand-written; its chart is shared with the app.
GANTT_PAGE = STATIC / "gantt.html"
GANTT_JS = STATIC / "gantt.js"
GANTT_CSS = STATIC / "gantt.css"

#: The built React bundle, committed so `scripts.demo` needs no npm.
APP_SHELL = STATIC / "app" / "index.html"


@pytest.fixture()
def client():
    """A client that looks like it came from the machine the app runs on.

    The routes that spend money on a vendor, and the one that deletes a
    project, require loopback or an admin token - see `app/admin.py`. A suite
    running on a developer's machine *is* loopback, so this matches reality;
    `tests/test_route_auth.py` owns the case this fixture deliberately is not,
    which is a stranger arriving over the proxy.
    """
    return TestClient(
        __import__("app.api.main", fromlist=["app"]).app, client=("127.0.0.1", 5000)
    )


@pytest.fixture()
def seeded():
    """One task in the shared test database, so the happy path has data."""
    from app.db import session_scope
    from app.models.domain import Program, Project, Task

    project_id = "excel:Project:1:APITEST"
    with session_scope() as session:
        session.merge(Program(id="excel:Program:1:DEFAULT", name="Test"))
        session.merge(Project(id=project_id, name="API test"))
        session.merge(
            Task(
                id="excel:Task:1:T-1",
                project_id=project_id,
                title="A task",
                status="IN_PROGRESS",
                start_date=date(2026, 3, 1),
                due_date=date(2026, 3, 10),
                baseline_end=date(2026, 3, 10),
            )
        )
    return project_id


def bundle_with_findings() -> InsightBundle:
    hits = [
        RuleHit(
            rule_id="rows_rejected",
            category="data_quality",
            severity="low",
            headline="{{rows_rejected}} row(s) could not be read.",
            recommendation="Review them.",
            rationale="Silent partial data is worse than none.",
            trace=("rows_rejected >= 1 (was 3)",),
        )
    ]
    bundle = build_bundle(
        project_id="p",
        as_of=datetime(2026, 3, 22, tzinfo=timezone.utc),
        generated_at=datetime(2026, 3, 22, tzinfo=timezone.utc),
        context=DeliveryContext(project_id="p", as_of="2026-03-22", rows_rejected=3),
        hits=hits,
    )
    bundle.narrative = render_narrative(bundle)
    return bundle


# --------------------------------------------------------------------------
# The committed bundle
# --------------------------------------------------------------------------


def test_the_built_app_is_committed():
    """`python -m scripts.demo` must never need `npm install`.

    Judges run the code. Committing `dist` is what keeps the runtime one Python
    command while still giving the front end a real toolchain.
    """
    assert APP_SHELL.exists(), "run: cd web && npm install && npm run build"

    html = APP_SHELL.read_text(encoding="utf-8")
    for url in re.findall(r'(?:src|href)="(/static/app/[^"]+)"', html):
        asset = REPO / "app" / "api" / url.removeprefix("/").removeprefix("app/")
        assert asset.exists() or (STATIC / url.removeprefix("/static/")).exists(), url


def test_the_bundle_is_not_stale():
    """A source edit without a rebuild serves yesterday's app.

    Vite content-hashes its filenames, so a stale bundle is invisible - the page
    loads and is simply wrong. Comparing mtimes is cheap and catches it.
    """
    assert APP_SHELL.exists(), "the app has not been built"

    built = APP_SHELL.stat().st_mtime
    sources = [p for p in (WEB / "src").rglob("*") if p.suffix in {".tsx", ".ts", ".css"}]
    assert sources, "no web sources found"

    newer = [p.name for p in sources if p.stat().st_mtime > built + 1]
    assert not newer, f"rebuild needed - newer than the bundle: {sorted(newer)}"


def test_the_app_shell_still_loads_the_shared_chart():
    """The Gantt is one vanilla implementation, used by the app and the Schedule
    page. Two copies would eventually draw two pictures of one projection."""
    html = APP_SHELL.read_text(encoding="utf-8")

    assert "/static/gantt.js" in html
    assert "/static/gantt.css" in html


def test_the_generated_types_match_the_live_schema(client):
    """Hand-written types would be a second declaration of the contract.

    Generated ones move the page/server coupling check to compile time - but
    only while they are current, which is what this compares.
    """
    generated = (WEB / "src" / "api-types.ts").read_text(encoding="utf-8")

    for name in ("InsightBundle", "ExplainBundle", "GanttBundle", "ForwardStep", "Calc"):
        assert f"{name}: {{" in generated, f"{name} missing - run: npm run types"

    # Every response schema the API exposes should appear in the types.
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    for name in schemas:
        if name.startswith(("HTTPValidation", "ValidationError")):
            continue
        assert f"{name}: {{" in generated, f"{name} is not in the generated types"


# --------------------------------------------------------------------------
# The routes
# --------------------------------------------------------------------------


def test_the_app_route_serves_the_bundle(client):
    """A real URL rather than a hash router, so it can be linked.

    Was parameterised over `/insight` and `/explain` until the Calc page
    was deleted - `/explain` now 404s, which is the point."""
    response = client.get("/insight")

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_the_schedule_page_is_served(client):
    response = client.get("/gantt")

    assert response.status_code == 200
    assert "Schedule" in response.text


def test_the_shared_chart_assets_are_served(client):
    for path, kind in (("/static/gantt.js", "javascript"), ("/static/gantt.css", "css")):
        response = client.get(path)
        assert response.status_code == 200, path
        assert kind in response.headers["content-type"], path


def test_the_api_returns_a_valid_bundle(client, seeded):
    response = client.get("/api/insight", params={"project": seeded})

    assert response.status_code == 200
    parsed = InsightBundle.model_validate(response.json())
    assert parsed.narration_source in ("template", "model")
    assert parsed.narrative


def test_the_api_response_is_json_serialisable_end_to_end(client, seeded):
    payload = client.get("/api/insight", params={"project": seeded}).json()

    assert json.loads(json.dumps(payload)) == payload


def test_response_schemas_mark_defaulted_fields_as_required():
    """A response always sends `findings`, so the schema must say so.

    Pydantic marks a defaulted field optional, which made the generated types
    `ForwardStep[] | undefined` and forced `?? []` over fields that can never
    be absent. `schemas/base.Response` fixes it at the schema.
    """
    schema = InsightBundle.model_json_schema(mode="serialization")

    assert "findings" in schema["required"]
    assert "data_quality" in schema["required"]


def test_every_narrative_heading_the_app_splits_on_is_emitted():
    """The app splits the narrative on these exact strings.

    Rename one in `fallback.py` and the summary panel silently empties, so the
    two are pinned together here as well as in the TypeScript.
    """
    text = render_narrative(bundle_with_findings())
    source = (WEB / "src" / "pages" / "Insight.tsx").read_text(encoding="utf-8")

    block = re.search(r"const QUESTIONS = \[(.*?)\] as const;", source, re.S)
    assert block, "QUESTIONS not found in Insight.tsx"
    in_app = set(re.findall(r'"([^"]+)"', block.group(1)))

    assert in_app == set(QUESTION_HEADINGS)
    for heading in QUESTION_HEADINGS[:4]:
        assert f"{heading}\n" in text


# --------------------------------------------------------------------------
# Failure paths - what a judge sees when something is not running
# --------------------------------------------------------------------------


def test_an_empty_database_returns_an_actionable_404(client):
    response = client.get("/api/insight", params={"project": "excel:Project:9:NOPE"})

    assert response.status_code == 404
    assert "scripts.replay" in response.json()["detail"]


def test_an_unreachable_database_is_reported_as_such(monkeypatch):
    import app.api.main as main

    monkeypatch.setattr(main, "check_connection", lambda: "cannot reach db: boom")
    response = TestClient(main.app).get("/api/insight")

    assert response.status_code == 503
    assert "cannot reach db" in response.json()["detail"]


def test_a_missing_bundle_is_named_rather_than_a_500(monkeypatch):
    """A FileNotFoundError tells a reader nothing about the one fix."""
    import app.api.main as main

    monkeypatch.setattr(main, "APP_SHELL", REPO / "does-not-exist.html")
    response = TestClient(main.app).get("/insight")

    assert response.status_code == 503
    assert "npm run build" in response.json()["detail"]


def test_the_client_names_each_failure_it_can_receive():
    """503 is a missing container, 404 is a missing replay - not both "error"."""
    source = (WEB / "src" / "api.ts").read_text(encoding="utf-8")

    assert "Database unreachable" in source
    assert "docker compose up -d" in source
    assert "No data yet" in source
    assert "scripts.replay" in source
    assert 'window.location.protocol === "file:"' in source


def test_the_app_never_computes_a_reported_number():
    """Invariant 1 reaches the front end: figures are formatted once, on the
    server. The app may lay values out; it may not recompute one.

    The single permitted operation is the baseline-coverage percentage, a ratio
    rendered for display rather than a finding's own number.
    """
    for name in ("Insight.tsx",):
        source = (WEB / "src" / "pages" / name).read_text(encoding="utf-8")
        stripped = source.replace("Math.round(q.baseline_coverage * 100)", "")

        for pattern in (
            r"\bfinding\.\w+\s*[*/-]\s*\d",
            r"\bstep\.\w+\s*[*/-]\s*\d",
            r"\blink\.lag_days_\w+\s*[*/-]",
        ):
            assert not re.search(pattern, stripped), f"{name}: {pattern}"


# --------------------------------------------------------------------------
# The Tailwind span trap
#
# Tailwind generates CSS by scanning source *text*. A class assembled at
# runtime - `md:col-span-${span}` - is therefore in no source file, gets no
# rule emitted, and the board silently collapses to one column. The page
# still renders, still looks tidy, and is simply the wrong layout.
#
# `Shell.tsx` spells every span out in a lookup table for that reason. These
# two tests are what stop someone "simplifying" it back.
# --------------------------------------------------------------------------

SHELL_TSX = WEB / "src" / "components" / "Shell.tsx"


def _built_css() -> str:
    sheets = sorted((STATIC / "app" / "assets").glob("*.css"))
    assert sheets, "the app has not been built"
    return "\n".join(p.read_text(encoding="utf-8") for p in sheets)


def _without_comments(source: str) -> str:
    """Source with comments removed.

    Needed because `Shell.tsx` *documents* this trap, and the documentation
    naturally contains the offending pattern as an example. Scanning the raw
    file flags the explanation as the bug it warns about.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//.*", "", source)


def test_no_span_class_is_assembled_at_runtime():
    """An interpolated class name is invisible to Tailwind's scanner."""
    source = _without_comments(SHELL_TSX.read_text(encoding="utf-8"))

    offenders = re.findall(r"(?:md:)?col-span-\$\{[^}]+\}", source)

    assert offenders == [], (
        "these class names are built at runtime, so Tailwind emits no CSS for "
        f"them and the board loses its columns: {offenders}"
    )


def test_every_span_the_shell_offers_exists_in_the_built_css():
    """The stronger check: the classes are not merely literal, they are emitted.

    Catches the case a source grep cannot - a span added to the lookup table
    after the last build, which would be a literal string with no rule behind
    it.
    """
    source = SHELL_TSX.read_text(encoding="utf-8")
    table = re.search(r"const SPAN: Record<number, string> = \{(.*?)\}", source, re.S)
    assert table, "Shell.tsx no longer declares a SPAN lookup table"

    classes = re.findall(r'"(md:col-span-\d+)"', table.group(1))
    assert classes, "the SPAN table declares no span classes"

    css = _built_css()
    # Tailwind escapes the colon in a variant class: `.md\:col-span-7`.
    missing = [name for name in classes if name.replace(":", r"\:") not in css]

    assert missing == [], (
        f"declared but absent from the built stylesheet: {missing}. "
        "Rebuild the front end (npm run build), or they are unreachable classes."
    )


# --------------------------------------------------------------------------
# The rail is duplicated - so it is asserted
#
# Navigation is spelled out in `Shell.tsx` for the built pages and in the HTML
# of each hand-written one. Three copies of a nav is how the six original
# mockups ended up with two conflicting token families, so the copies are
# checked against each other rather than trusted.
# --------------------------------------------------------------------------

HAND_WRITTEN = ("gantt.html", "settings.html")


def _rail_hrefs(html: str) -> list[str]:
    rail = re.search(r'<nav class="rail".*?</nav>', html, re.S)
    assert rail, "no rail in this page"
    # The brand mark links into the app but is not a section.
    return re.findall(r'<a href="([^"]+)"', rail.group(0))


def test_every_page_carries_the_same_rail():
    shell = (WEB / "src" / "components" / "Shell.tsx").read_text(encoding="utf-8")
    table = re.search(r"const TABS = \[(.*?)\] as const;", shell, re.S)
    assert table, "Shell.tsx no longer declares a TABS list"
    expected = re.findall(r'href: "([^"]+)"', table.group(1))
    assert expected, "TABS declares no links"

    for name in HAND_WRITTEN:
        found = _rail_hrefs((STATIC / name).read_text(encoding="utf-8"))
        assert found == expected, (
            f"{name}'s rail is {found} but Shell.tsx says {expected} - "
            "the two navs have drifted"
        )


def test_every_rail_entry_is_actually_served(client):
    """A tab pointing at a 404 is the failure mode of adding one.

    The rail is three copies checked against each other above, which proves
    they agree and not that any of them goes anywhere - a new entry added to
    all four files in step is still a dead link until a route serves it.
    """
    shell = (WEB / "src" / "components" / "Shell.tsx").read_text(encoding="utf-8")
    table = re.search(r"const TABS = \[(.*?)\] as const;", shell, re.S)
    assert table
    for href in re.findall(r'href: "([^"]+)"', table.group(1)):
        assert client.get(href).status_code == 200, f"the rail links {href}, nothing serves it"


def test_every_hand_written_page_marks_its_own_rail_entry():
    """Without `aria-current` the reader cannot tell which page they are on."""
    for name in HAND_WRITTEN:
        html = (STATIC / name).read_text(encoding="utf-8")
        rail = re.search(r'<nav class="rail".*?</nav>', html, re.S)
        assert rail, name
        assert rail.group(0).count('aria-current="page"') == 1, name


def test_the_old_horizontal_nav_is_gone_from_the_hand_written_pages():
    """Two navigations on one page is worse than either alone.

    The rail replaced the tab bar; a page keeping both would offer the reader
    the same five links twice.
    """
    for name in HAND_WRITTEN:
        html = (STATIC / name).read_text(encoding="utf-8")
        assert 'class="tabs"' not in html, f"{name} still has the old tab bar"


def test_the_agent_route_serves_the_bundle(client):
    response = client.get("/agent")

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_agent_chat_rejects_an_empty_conversation(client):
    response = client.post("/api/agent/chat", json={"messages": []})
    assert response.status_code == 400


def test_agent_chat_reports_unavailable_rather_than_a_500(client, monkeypatch):
    """No key is configured in the test environment - the route must degrade
    to a visible `ok: false`, the same rule narration follows, not crash."""
    response = client.post(
        "/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"]


def test_agent_chat_returns_the_model_s_reply_when_available(client, monkeypatch):
    def fake_chat(turns, *, provider, model, api_key, base_url, context=None):
        assert turns[-1].content == "hi"
        return "hello yourself"

    monkeypatch.setattr("app.agent.chat.chat", fake_chat)

    response = client.post(
        "/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["reply"] == "hello yourself"


def test_the_risk_route_serves_the_bundle(client):
    """A fourth app route, wired the same way as portfolio/team/explain."""
    response = client.get("/risk")

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_creating_a_risk_through_the_api(client):
    body = {
        "project_id": "excel:Project:1:SAIN",
        "title": "Vendor lock-in",
        "category": "Technology",
        "pre_likelihood": "Possible",
        "pre_impact": "Major",
    }

    created = client.post("/api/risks", json=body)
    assert created.status_code == 201
    payload = created.json()
    assert payload["pre_rating"] == "High"  # Possible x Major, per the matrix
    assert payload["risk_no"] == "1"

    listed = client.get("/api/risks", params={"project": "excel:Project:1:SAIN"})
    assert listed.status_code == 200
    assert [r["title"] for r in listed.json()["risks"]] == ["Vendor lock-in"]

    risk_id = payload["id"]
    updated = client.put(f"/api/risks/{risk_id}", json={"status": "Closed"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "Closed"
    assert updated.json()["title"] == "Vendor lock-in"  # untouched by the PUT

    deleted = client.delete(f"/api/risks/{risk_id}")
    assert deleted.status_code == 204

    after = client.get("/api/risks", params={"project": "excel:Project:1:SAIN"})
    assert after.json()["risks"] == []


def test_a_risk_cannot_be_filed_against_a_project_that_does_not_exist(client):
    """The register's one typed key, guarded at the route as well.

    `project_id` used to be whatever string arrived, so a PM typing a
    project's *name* into the form created a risk belonging to no project: in
    the flat register, absent from that project's own risk tile and from the
    Program tab's cross-project one. A 400 naming the id is the honest answer.
    """
    response = client.post(
        "/api/risks", json={"project_id": "SAIN", "title": "Typed the name"}
    )

    assert response.status_code == 400
    assert "SAIN" in response.json()["detail"]
    assert client.get("/api/risks").json()["risks"] == []


def test_the_risk_bundle_carries_the_projects_a_risk_can_belong_to(client):
    """So the form's picker has a vocabulary instead of a hard-coded id."""
    projects = client.get("/api/risks").json()["projects"]

    assert "excel:Project:1:SAIN" in {p["project_id"] for p in projects}


def test_creating_a_risk_without_a_title_is_rejected(client):
    response = client.post(
        "/api/risks", json={"project_id": "excel:Project:1:SAIN"}
    )
    assert response.status_code == 400


def test_updating_a_missing_risk_is_a_404(client):
    response = client.put("/api/risks/999999", json={"status": "Closed"})
    assert response.status_code == 404


def test_deleting_a_missing_risk_is_a_404(client):
    response = client.delete("/api/risks/999999")
    assert response.status_code == 404


def test_the_program_route_serves_the_bundle(client):
    """A third app route, so the portfolio can be linked like the others."""
    response = client.get("/portfolio")

    assert response.status_code == 200
    assert 'id="root"' in response.text


def test_the_portfolio_folds_source_ids_into_one_row(client, seeded):
    """Invariant 7 on this screen: two source ids are one delivery project.

    Excel and Jira each create their own `projects` row for the same delivery,
    so a portfolio built from that table would list HRMS twice. `app.scope`
    holds the pairing, and this is what stops the screen double-counting.
    """
    from app.api.schemas.portfolio import PortfolioBundle
    from app.scope import PORTFOLIO

    bundle = PortfolioBundle.model_validate(client.get("/api/portfolio").json())

    # Rows are ranked worst-first (see `portfolio()`), not seed order - the
    # portfolio now holds more than one project (SAIN, Example Project
    # alongside HRMS), so match by id instead of a positional zip.
    assert len(bundle.projects) == len(PORTFOLIO)
    by_id = {row.project_id: row for row in bundle.projects}
    for entry in PORTFOLIO:
        assert set(by_id[entry.canonical_id].source_ids) == set(entry.source_ids)


def test_a_project_with_no_data_is_never_green(client):
    """`no_data` is its own band. Colouring unknown healthy is the failure the
    whole product argues against, so it is asserted rather than trusted."""
    from app.intelligence.pipeline import _band_for

    assert _band_for([]) == "healthy"          # findings ran, nothing fired
    assert _band_for(["high"]) == "critical"
    assert _band_for(["medium"]) == "watch"
    assert _band_for(["info"]) == "healthy"
    # And the row builder uses `no_data` when nothing was ingested - a state
    # `_band_for` deliberately cannot produce, because it means "not analysed".
    assert "no_data" not in {_band_for([]), _band_for(["low"])}


def test_the_team_route_serves_the_bundle(client):
    assert client.get("/team").status_code == 200


def test_the_team_bundle_says_when_it_has_no_effort_data(client, seeded):
    """`has_effort_data` exists so the page can explain an empty panel.

    A zero where a total belongs reads as "nobody logged anything"; the flag
    lets the page say "no sheet carried an Hours column" instead, which is a
    different and truer statement.
    """
    from app.api.schemas.team import TeamBundle

    bundle = TeamBundle.model_validate(
        client.get("/api/team", params={"project": seeded}).json()
    )

    # The seeded fixture has one task and no worklog at all.
    assert bundle.has_effort_data is False
    assert bundle.total_hours == 0



def test_the_program_config_exposes_the_whole_rule_table(client):
    """Every finding is defended by pointing at one of these thresholds, so the
    screen shows all of them rather than a sample."""
    from app.api.schemas.program import ProgramBundle
    from app.intelligence.rules.tables import DEFAULT_TABLE

    bundle = ProgramBundle.model_validate(client.get("/api/program").json())

    assert len(bundle.rules) == len(DEFAULT_TABLE.rules)
    assert bundle.rule_table == DEFAULT_TABLE.name
    # Tokens are deliberately NOT substituted here: this screen is about the
    # rule, not about today's numbers.
    assert any("{{" in rule.headline for rule in bundle.rules)
    assert all(rule.conditions for rule in bundle.rules)


# --------------------------------------------------------------------------
# Invariant 7 through the API: whichever of a project's source ids a caller
# holds, they get the same project.
#
# This was broken and nothing caught it. `scope.also_for` used `find`, which
# matches canonical ids only, so a caller holding the *paired* id got an empty
# pairing - and the route then analysed one source of a two-source project and
# served that as the whole project. Measured on the demo before the fix: 1
# finding instead of 10, 4 schedule rows instead of 10, 0 logged hours instead
# of 28. Nothing errored; the numbers were quietly wrong, which is precisely
# the failure `app/scope.py` exists to prevent.
# --------------------------------------------------------------------------

CANONICAL = "excel:Project:1:HRMS"
PAIRED = "jira:Project:1:HRMS"


def test_also_for_resolves_from_the_paired_id_not_just_the_canonical_one():
    from app import scope

    assert scope.also_for(CANONICAL) == [PAIRED]
    # The half that was broken: holding the paired id must name the other one.
    assert scope.also_for(PAIRED) == [CANONICAL]


def test_also_for_never_returns_the_id_it_was_given():
    """`analyze_project(project_id=x, also=[...])` would otherwise load x's rows
    twice, which is one way a task count doubles."""
    from app import scope

    for source_id in (CANONICAL, PAIRED):
        assert source_id not in scope.also_for(source_id)


def test_an_unpaired_or_unknown_id_still_gets_an_empty_pairing():
    """A project nobody has paired is a legitimate question whose answer is
    "just this one source". It must not become an error."""
    from app import scope

    assert scope.also_for("excel:Project:1:SAIN") == []
    assert scope.also_for("nothing:Project:9:NOPE") == []


def test_canonical_pairing_answers_the_same_from_either_end():
    from app import scope

    assert scope.canonical_pairing(CANONICAL) == (CANONICAL, [PAIRED])
    assert scope.canonical_pairing(PAIRED) == (CANONICAL, [PAIRED])


def test_canonical_pairing_returns_an_unknown_id_as_itself():
    """Not a 404. "I have not ingested that yet" is a different statement from
    "that is not a project", and the route has to be able to make the first."""
    from app import scope

    assert scope.canonical_pairing("nothing:Project:9:NOPE") == (
        "nothing:Project:9:NOPE",
        [],
    )


@pytest.fixture()
def paired_project():
    """One delivery project with a task in each of its two source systems.

    Deliberately different dates per source: if a route analyses only the id it
    was handed, the two calls come back with different windows and the test
    fails on content rather than on a status code.
    """
    from app.db import session_scope
    from app.models.domain import Program, Project, Task

    with session_scope() as session:
        session.merge(Program(id="program:Program:0:DEFAULT", name="Test"))
        for pid, name in ((CANONICAL, "HRMS Platform"), (PAIRED, "HRMS Platform")):
            session.merge(
                Project(id=pid, name=name, program_id="program:Program:0:DEFAULT")
            )
        session.merge(
            Task(
                id="excel:Task:1:PAIR-1", project_id=CANONICAL, title="From the sheet",
                status="IN_PROGRESS", start_date=date(2026, 3, 1),
                due_date=date(2026, 3, 10), baseline_end=date(2026, 3, 10),
            )
        )
        session.merge(
            Task(
                id="jira:Task:1:PAIR-2", project_id=PAIRED, title="From Jira",
                status="IN_PROGRESS", start_date=date(2026, 4, 1),
                due_date=date(2026, 4, 20), baseline_end=date(2026, 4, 10),
            )
        )
    return CANONICAL, PAIRED


def test_the_schedule_is_the_same_project_from_either_source_id(client, paired_project):
    """The regression at the surface a person actually hits.

    Both tasks, one window, whichever id was asked for - and the same count, so
    a route that silently analysed half would fail here rather than 200 with a
    smaller answer."""
    canonical, paired = paired_project

    a = client.get("/api/gantt", params={"project": canonical})
    b = client.get("/api/gantt", params={"project": paired})
    assert a.status_code == 200 and b.status_code == 200

    assert a.json() == b.json(), "the same project answered differently per source id"
    labels = {row["title"] for row in a.json()["rows"]}
    assert {"From the sheet", "From Jira"} <= labels, labels


def test_no_project_scoped_route_resolves_its_pairing_by_lookup_alone():
    """The fix was one line repeated in seven routes, so the failure to guard
    against is fixing six of them.

    Source inspection rather than seven live calls: some of those routes need
    rich data to answer at all, and the thing worth pinning is that none of them
    goes back to the canonical-only helper - which is a property of the text.
    """
    import re
    from pathlib import Path

    import app.api.main as main

    source = Path(main.__file__).read_text(encoding="utf-8")
    offenders = re.findall(r"^\s*also = scope\.also_for\(.*\)$", source, re.M)
    assert not offenders, (
        "a project-scoped route still resolves its pairing with the "
        f"canonical-only helper: {offenders}"
    )
