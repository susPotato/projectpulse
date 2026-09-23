"""The hand-written pages have no build step, so nothing else checks them.

`/traceability`, `/gantt` and `/settings` are plain HTML with inline script,
served straight from `app/api/static`. That is deliberate — they read data
the React bundle must not depend on — but it means a class name that does
not exist, or a helper defined twice, ships silently and looks like a
styling bug on production.

Both of those happened: a view was written with `.panel`, `.row` and
`.grid`, none of which the page defines, so it rendered as unstyled divs;
and a second copy of `esc()` was added below the first. Neither is visible
to any other test in this suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "app" / "api" / "static"
PAGES = sorted(STATIC.glob("*.html"))

#: Defined in the shared stylesheet rather than in any one page.
SHELL = {
    "shell", "rail", "brand", "theme-toggle", "wrap", "row", "col", "grid",
    "btn", "card", "panel", "bar", "field", "hint", "pill", "badge",
}


def _defined_classes(text: str) -> set[str]:
    style = "\n".join(re.findall(r"<style>(.*?)</style>", text, re.S))
    return set(re.findall(r"\.([a-zA-Z][\w-]*)", style))


def _used_classes(text: str) -> set[str]:
    """Class names in the markup, including markup built inside JS strings.

    The attribute often ends mid-expression — `'<span class="tag ' + (TAG[k])`
    — so the capture stops at the first quote, backslash or newline and then
    keeps only identifier-shaped tokens. Without that this reads `+`, `===`
    and `(TAG[x.evidence]` as class names and the test is pure noise.
    """
    body = text.split("</style>", 1)[-1]
    used: set[str] = set()
    for m in re.finditer(r"""class=\\?"([^"'`\\\n]*)""", body):
        used.update(m.group(1).split())
    return {c for c in used if re.fullmatch(r"[a-zA-Z][\w-]*", c)}


def _js_hooks(text: str) -> set[str]:
    """Classes the script looks elements up by rather than styles.

    `row.querySelector(".k-input")` is a perfectly good reason for a class
    to carry no styling, and a test that cannot tell that apart from a
    mistake is one people learn to skip.
    """
    body = text.split("</style>", 1)[-1]
    hooks: set[str] = set()
    # Any class inside a selector string, not only one at its start:
    # `querySelectorAll("tr[data-file] .del")` is the common shape here.
    for sel in re.findall(r"""[Ss]elector(?:All)?\(\s*['"]([^'"]+)['"]""", body):
        hooks.update(re.findall(r"\.([a-zA-Z][\w-]*)", sel))
    hooks.update(re.findall(r"""classList\.\w+\(\s*['"]([\w-]+)""", body))
    hooks.update(re.findall(r"""getElementsByClassName\(\s*['"]([\w-]+)""", body))
    return hooks


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_class_the_page_uses_is_one_it_or_the_shell_defines(page: Path):
    """A class nobody defines renders as an unstyled div on production.

    Two exemptions, both real in this codebase. A token ending in `-` is a
    prefix being concatenated — `class="tag t-" + v.verdict` — so the class
    only exists at runtime. And a class the script selects on is a hook, not
    a style.
    """
    text = page.read_text(encoding="utf-8")
    shell = (STATIC / "shell.css")
    shell_defined = set(re.findall(r"\.([a-zA-Z][\w-]*)",
                                   shell.read_text(encoding="utf-8"))) if shell.exists() else set()
    used = {c for c in _used_classes(text) if not c.endswith("-")}
    unknown = used - _defined_classes(text) - shell_defined - SHELL - _js_hooks(text)
    assert not unknown, f"{page.name} styles nothing for: {sorted(unknown)}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_helper_is_defined_twice(page: Path):
    """Two `esc()` definitions is one too many, and the second one wins."""
    body = page.read_text(encoding="utf-8").split("</style>", 1)[-1]
    names = re.findall(r"\bfunction\s+(\w+)\s*\(", body)
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"{page.name} defines twice: {sorted(dupes)}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_tab_the_page_offers_has_somewhere_to_go(page: Path):
    """A tab whose view nothing renders shows the previous tab's content."""
    text = page.read_text(encoding="utf-8")
    offered = set(re.findall(r'data-view="([\w-]+)"', text))
    if not offered:
        pytest.skip("not a tabbed page")
    handled = set(re.findall(r'view === "([\w-]+)"', text))
    default = set(re.findall(r'view\s*=\s*"([\w-]+)"', text))
    # The fall-through case needs no branch of its own.
    assert offered - handled - default == set() or len(offered - handled) <= 1, \
        f"{page.name} offers tabs nothing renders: {sorted(offered - handled - default)}"


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_branch_is_left_behind_a_tab_that_was_removed(page: Path):
    """Dead code that used to be reachable is worse than no code."""
    text = page.read_text(encoding="utf-8")
    offered = set(re.findall(r'data-view="([\w-]+)"', text))
    if not offered:
        pytest.skip("not a tabbed page")
    handled = set(re.findall(r'view === "([\w-]+)"', text))
    assert not (handled - offered), \
        f"{page.name} still branches on removed tabs: {sorted(handled - offered)}"


def _body_rule(text: str) -> str:
    style = "\n".join(re.findall(r"<style>(.*?)</style>", text, re.S))
    match = re.search(r"\bbody\s*\{([^}]*)\}", style)
    return match.group(1) if match else ""


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_page_puts_padding_between_the_viewport_and_the_rail(page: Path):
    """`body { padding }` insets the whole shell, and the shell is the rail.

    One of the six carried `padding: 20px` and the other five did not, so the
    navigation on that page sat 20px in from the top and left edge while every
    other tab ran flush against them - and the rail visibly moved when you
    switched between them. It reads as a bug in the rail, which is why it
    survived three attempts to fix it there.

    A page's own gutter belongs on `.wrap`, which is inside the shell.
    """
    body = _body_rule(page.read_text(encoding="utf-8"))
    assert "padding" not in body, (
        f"{page.name} pads <body>, which moves the rail away from the "
        f"viewport edge on this page only: {body.strip()!r}"
    )


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_with_a_rail_keeps_the_project_in_its_links(page: Path):
    """The hand-written rail is literal markup, so its hrefs are bare.

    `Shell.tsx` passes every rail href through `withProject`; these pages had
    no equivalent, so clicking through one dropped `?project=` from the
    address bar. The selection survives in storage - the data stayed right -
    but a link copied from here opens on whatever the server defaults to.
    """
    text = page.read_text(encoding="utf-8")
    if 'class="rail"' not in text:
        pytest.skip("no rail on this page")
    assert "/static/rail.js" in text, (
        f"{page.name} renders a rail but never rewrites its links"
    )


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_the_inline_script_parses(page: Path):
    """These pages carry their behaviour inline, and a syntax error in it
    is invisible to every other check here: the HTML is still well-formed,
    the classes are still defined, the route still returns 200, and the
    page loads to a spinner that never resolves.

    That is not hypothetical - `settings.html` shipped with
    `class=\\"hint\\"` inside a double-quoted string, which ends the string
    early and breaks the whole script. Caught by a person looking at it,
    which is the expensive way.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available to parse the script")

    text = page.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", text, re.S)
    if not blocks:
        pytest.skip(f"{page.name} has no inline script")

    for index, block in enumerate(blocks):
        if not block.strip():
            continue
        with tempfile.NamedTemporaryFile(
            "w", suffix=".js", delete=False, encoding="utf-8"
        ) as handle:
            handle.write(block)
            path = handle.name
        try:
            done = subprocess.run([node, "--check", path],
                                  capture_output=True, text=True)
        finally:
            Path(path).unlink(missing_ok=True)
        assert done.returncode == 0, (
            f"{page.name} script #{index} does not parse:\n{done.stderr}"
        )


def _rail_hrefs(text: str) -> list[str]:
    """The destinations one rail offers, in order."""
    nav = re.search(r'<nav class="rail".*?</nav>', text, re.S)
    if not nav:
        return []
    return re.findall(r'<a href="(/[^"?#]*)', nav.group(0))


RAILED = [p for p in PAGES if 'class="rail"' in p.read_text(encoding="utf-8")]


@pytest.mark.parametrize("page", RAILED, ids=lambda p: p.name)
def test_every_rail_offers_the_same_destinations(page: Path):
    """The rail is literal markup on each hand-written page and an array in
    `Shell.tsx`, so there are eight copies of one list and nothing made
    them agree. Adding `/jira` reached exactly one of them, and the page
    was live, tested and completely unreachable - which is the state
    `Shell.tsx`'s own comment warns about: "a page reachable only by
    typing its URL is a page nobody opens."
    """
    shell = (Path(__file__).resolve().parent.parent
             / "web" / "src" / "components" / "Shell.tsx")
    if not shell.is_file():
        pytest.skip("the built app's rail is not in this checkout")

    expected = re.findall(r'\{ href: "(/[^"]+)", label:', shell.read_text(encoding="utf-8"))
    got = _rail_hrefs(page.read_text(encoding="utf-8"))
    # `/insight` is the brand link as well as a rail entry on some pages,
    # so compare sets rather than counts.
    assert set(expected) <= set(got), (
        f"{page.name} is missing rail entries: {sorted(set(expected) - set(got))}"
    )


@pytest.mark.parametrize("page", RAILED, ids=lambda p: p.name)
def test_a_rail_does_not_offer_a_page_that_is_not_served(page: Path):
    """The mirror of the above: a link to a route nobody registered is a
    404 somebody finds by clicking."""
    from app.api.main import app

    served = {r.path for r in app.routes}
    for href in _rail_hrefs(page.read_text(encoding="utf-8")):
        assert href in served, f"{page.name} links to {href}, which is not a route"


def test_the_sync_tool_template_parses_as_powershell():
    """It is downloaded and run by somebody who cannot debug it - the PM,
    on a laptop, during a demo. A syntax error there is discovered in the
    worst possible room.

    Only the PowerShell half is parsed. The file is a `.cmd` now, because a
    default Windows install refuses to run a downloaded `.ps1` at all and
    closes the window before the reason can be read; the head is batch and
    `@echo off` is a parse error to PowerShell. Everything after the marker
    is PowerShell, and it is exactly the substring the batch header hands to
    `Invoke-Expression` - so parsing that is parsing what actually runs.
    """
    import shutil
    import subprocess
    import tempfile

    template = Path(__file__).resolve().parent.parent / "app" / "api" / "static" / "sync-tool.ps1.tmpl"
    assert template.is_file()

    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("no PowerShell to parse with")

    text = template.read_text(encoding="utf-8")
    marker = "#__PS" + "__"          # spelled in halves, as the file does
    assert text.count(marker) == 1, "the PowerShell marker is not unique"
    filled = (text[text.index(marker):]
              .replace("__SERVER__", "https://example.test")
              .replace("__JIRA_SITE__", "https://jira.example.test/jiradc")
              .replace("__PROJECT_KEY__", "ABC")
              .replace("__PUSH_TOKEN__", "t0ken"))
    # The marker itself is shaped like a placeholder and is not one.
    leftover = [m for m in re.findall(r"__[A-Z_]+__", filled) if m != "__PS__"]
    assert not leftover, f"a placeholder went unreplaced: {leftover}"

    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                     encoding="utf-8") as handle:
        handle.write(filled)
        path = handle.name
    try:
        done = subprocess.run(
            [powershell, "-NoProfile", "-Command",
             "$e=$null;"
             f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$e);"
             "if($e.Count -gt 0){$e[0].Message; exit 1}"],
            capture_output=True, text=True)
    finally:
        Path(path).unlink(missing_ok=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_sync_tool_has_a_placeholder_for_everything_it_needs():
    """Every field somebody has to fill in by hand is a field they can get
    wrong while a demo waits."""
    template = (Path(__file__).resolve().parent.parent / "app" / "api"
                / "static" / "sync-tool.ps1.tmpl").read_text(encoding="utf-8")
    for name in ("__SERVER__", "__JIRA_SITE__", "__PROJECT_KEY__", "__PUSH_TOKEN__"):
        assert name in template, name


def _client():
    from fastapi.testclient import TestClient

    from app.api import main

    return TestClient(main.app)


def test_a_hand_written_asset_must_be_revalidated():
    """`gantt.js` keeps its name through every edit, so a browser that
    cached it once will keep showing the old chart. A legend fix looked
    like it had not deployed for exactly this reason."""
    reply = _client().get("/static/gantt.js")
    assert reply.status_code == 200
    assert reply.headers["cache-control"] == "no-cache"


def test_a_fingerprinted_bundle_may_be_cached_forever():
    """Vite hashes the name, so changed bytes are a changed URL."""
    assets = STATIC / "app" / "assets"
    bundle = next(assets.glob("index-*.js"))
    reply = _client().get(f"/static/app/assets/{bundle.name}")
    assert reply.status_code == 200
    assert "immutable" in reply.headers["cache-control"]


def test_a_page_fingerprints_the_assets_it_loads():
    """`no-cache` only helps a browser that asks. A content hash in the URL
    means it never has to: a changed file is a URL it has not seen. This was
    mistaken for a failed deploy twice."""
    body = _client().get("/gantt").text
    assert 'src="/static/gantt.js?v=' in body
    assert 'href="/static/shell.css?v=' in body


def test_the_fingerprint_follows_the_file():
    """A stamp that never changes is decoration, not cache busting."""
    import hashlib
    import re

    body = _client().get("/gantt").text
    stamp = re.search(r'/static/gantt\.js\?v=([0-9a-f]+)', body).group(1)
    expected = hashlib.sha256((STATIC / "gantt.js").read_bytes()).hexdigest()
    assert expected.startswith(stamp)


def test_a_flat_backlog_folds_onto_its_own_keys(tmp_path):
    """`parent` is a property of the spreadsheet's nesting. Read the same
    board from Jira and every row is its own keyed issue, so folding on
    `parent` put all 190 into one `(none)` bucket - which the Schedule page
    reported as "190 of 190 feature rows are not on the chart"."""
    import json

    from app.api import tracelink_view

    run = tmp_path / "run"
    run.mkdir()
    (run / "tickets.json").write_text(json.dumps({
        "payload": [
            {"uid": "T1", "key": "AB-1", "parent": None, "status": "Release"},
            {"uid": "T2", "key": "AB-2", "parent": None, "status": "Release"},
        ]
    }), encoding="utf-8")

    rollup = tracelink_view.rollup_by_parent(run)
    assert set(rollup["parents"]) == {"AB-1", "AB-2"}
    assert "(none)" not in rollup["parents"]


def test_a_nested_backlog_still_folds_onto_its_parent(tmp_path):
    """The spreadsheet shape must keep working: unkeyed feature rows roll up
    to the keyed issue they sit under."""
    import json

    from app.api import tracelink_view

    run = tmp_path / "run"
    run.mkdir()
    (run / "tickets.json").write_text(json.dumps({
        "payload": [
            {"uid": "T1", "key": None, "parent": "AB-1", "status": "Release"},
            {"uid": "T2", "key": None, "parent": "AB-1", "status": "To Do"},
        ]
    }), encoding="utf-8")

    rollup = tracelink_view.rollup_by_parent(run)
    assert set(rollup["parents"]) == {"AB-1"}
    assert rollup["parents"]["AB-1"]["features"] == 2


def test_an_email_subject_is_not_a_person():
    """`parse_inline_fields` pulls every Label: value pair out of a
    description. "Email subject: [Project code] - Request review CM Plan"
    is one, and it was being printed in the "named on the row" column."""
    from app.api.tracelink_view import _people

    got = _people({
        "BA": "QuanDh14",
        "Developer": "FNS",
        "Email subject": "[Project code] - Request review CM Plan",
        "Review Email": "CMP.Review@fpt.com",
        "Note": "1 document thiet ke + 1 JSON schema + 2 sample manifests",
    })
    assert got == {"BA": "QuanDh14", "Developer": "FNS"}


def test_a_short_unfamiliar_name_is_still_a_person():
    """`FSG` and `TaiPH9,LocLP3,HieuHV1` are real answers on this board.
    The filter is about the shape of the value, not a roster."""
    from app.api.tracelink_view import _people

    got = _people({"BA": "FSG", "Ghi chú": "TaiPH9,LocLP3,HieuHV1"})
    assert got["BA"] == "FSG"
    assert got["Ghi chú"] == "TaiPH9,LocLP3,HieuHV1"
