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
