"""Guards for the commands a judge actually types.

Every failure in this file has already happened once for real:

* `python -m scripts.replay` under the system Python died on a missing
  `sqlalchemy`, because the dependencies live in `.venv`;
* the re-exec that fixed that then broke `import scripts`, because after `execv`
  `sys.path[0]` is the script's directory rather than the repo root;
* `scripts/demo.py` chose its database *after* importing `app.db`, which reads
  `DATABASE_URL` at import - so the console silently talked to a Postgres nobody
  had started;
* the replay timeline lost one of its two no-edit syncs, which removes every
  causal chain while leaving the page looking populated.

None of those are caught by testing the library. They are caught here.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import _bootstrap
from scripts.replay import TIMELINE

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

#: Entry points a human is documented to run.
ENTRY_POINTS = ("demo", "sync", "replay", "gen_demo_data", "gen_jira_data", "index_code")


# --------------------------------------------------------------------------
# Import order — the trap that made `scripts.demo` use the wrong database
# --------------------------------------------------------------------------


def _first_import_lines(path: Path) -> list[tuple[int, str]]:
    """Top-level imports in source order, as (line, dotted name)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.lineno, node.module))
    return found


@pytest.mark.parametrize("name", ["demo", "sync"])
def test_bootstrap_is_imported_before_any_app_module(name):
    """`app.config` freezes DATABASE_URL when it is imported.

    Any database choice made after the first `app.*` import is dead code. This is
    why the import order in those files looks wrong - it is deliberate, and this
    test is what stops someone tidying it back.
    """
    imports = _first_import_lines(SCRIPTS / f"{name}.py")

    bootstrap_line = min(
        (line for line, mod in imports if mod.startswith("scripts._bootstrap")),
        default=None,
    )
    first_app_line = min(
        (line for line, mod in imports if mod == "app" or mod.startswith("app.")),
        default=None,
    )

    assert bootstrap_line is not None, f"{name}.py does not bootstrap"
    if first_app_line is not None:
        assert bootstrap_line < first_app_line


def test_demo_no_longer_sets_the_database_after_importing_app():
    """The original bug, pinned so it cannot come back."""
    source = (SCRIPTS / "demo.py").read_text(encoding="utf-8")

    assert 'os.environ.setdefault("DATABASE_URL"' not in source


# --------------------------------------------------------------------------
# _bootstrap
# --------------------------------------------------------------------------


def test_the_venv_interpreter_is_found():
    interpreter = _bootstrap._venv_python()

    assert interpreter is not None, "no .venv found; the re-exec cannot work"
    assert interpreter.exists()


def test_default_database_does_not_override_an_explicit_choice(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///chosen.db")

    assert _bootstrap.default_database() == "sqlite:///chosen.db"


def test_default_database_falls_back_to_sqlite(monkeypatch):
    """The CLI must run with nothing installed and no container."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert _bootstrap.default_database() == _bootstrap.DEFAULT_SQLITE
    assert "sqlite" in os.environ["DATABASE_URL"]


def test_ensure_venv_is_a_noop_once_guarded(monkeypatch):
    """The guard is what stops an exec loop if a venv lacks the dependencies."""
    monkeypatch.setenv(_bootstrap._GUARD, "1")
    called = []
    monkeypatch.setattr(os, "execv", lambda *a: called.append(a))

    _bootstrap.ensure_venv()

    assert called == []


def test_ensure_venv_does_not_relaunch_when_already_in_the_venv(monkeypatch):
    monkeypatch.delenv(_bootstrap._GUARD, raising=False)
    interpreter = _bootstrap._venv_python()
    monkeypatch.setattr(sys, "executable", str(interpreter))
    called = []
    monkeypatch.setattr(os, "execv", lambda *a: called.append(a))

    _bootstrap.ensure_venv()

    assert called == []


def test_a_relaunch_keeps_the_repo_importable(monkeypatch):
    """After execv, `sys.path[0]` is the script's directory.

    Without PYTHONPATH the relaunched process cannot import `scripts` or `app` -
    which is exactly how the first version of this failed.
    """
    monkeypatch.delenv(_bootstrap._GUARD, raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\some\other\python.exe")
    captured: list[tuple] = []
    monkeypatch.setattr(os, "execv", lambda *a: captured.append(a))

    _bootstrap.ensure_venv()

    assert captured, "expected a relaunch"
    assert str(REPO) in os.environ["PYTHONPATH"]
    # And it relaunches via `-m`, not by path, for the same reason.
    argv = captured[0][1]
    assert "-m" in argv


# --------------------------------------------------------------------------
# The replay timeline
# --------------------------------------------------------------------------


def test_the_timeline_keeps_its_no_edit_syncs():
    """Dropping one removes every causal chain, silently.

    The data would still look fine: the page fills with schedule findings and no
    "why", which is the worst failure mode available - plausible and wrong.
    """
    no_edit = [entry for entry in TIMELINE if entry[0] is None and entry[1] == "excel"]

    assert len(no_edit) == 2


def test_the_timeline_is_chronological():
    times = [when for _, _, when in TIMELINE]

    assert times == sorted(times)


def test_every_demo_step_appears_exactly_once_and_in_order():
    steps = [step for step, _, _ in TIMELINE if step is not None]

    assert steps == [0, 1, 2, 3]


def test_a_scan_falls_between_each_edit_and_the_next():
    """The property the whole precision model needs.

    Two bounded changes in adjacent windows share a boundary instant and can
    never be ordered, so every edit must be followed by a sync before the next
    edit lands.
    """
    edits = [i for i, (step, _, _) in enumerate(TIMELINE) if step is not None]
    for first, second in zip(edits, edits[1:]):
        between = TIMELINE[first + 1 : second]
        assert between, f"no sync between step {TIMELINE[first][0]} and the next"


# --------------------------------------------------------------------------
# The entry points actually start
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_every_entry_point_is_importable(name):
    """A syntax error or bad import in a script is invisible until someone runs it."""
    module = __import__(f"scripts.{name}", fromlist=["main"])

    assert module is not None


@pytest.mark.parametrize("name", ["sync", "replay", "gen_demo_data", "index_code"])
def test_every_entry_point_offers_help_without_touching_a_database(name, tmp_path):
    """`--help` is the cheapest possible smoke test.

    It proves the module imports, its arguments parse, and nothing at import time
    needs a database - the failure that made every command hang for minutes.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'x.db').as_posix()}"
    env["PULSE_BOOTSTRAPPED"] = "1"  # already the right interpreter

    result = subprocess.run(
        [sys.executable, "-m", f"scripts.{name}", "--help"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode == 0, result.stderr
    assert "usage" in (result.stdout + result.stderr).lower()


def test_the_index_generator_produces_a_marker_and_real_entries():
    """WORKLOG.md's index is generated; a broken generator makes it stale silently."""
    from scripts.index_code import MARKER, render

    output = render()

    assert output.startswith(MARKER)
    assert "app/intelligence/assembler.py" in output
    assert "build_bundle(" in output


def test_the_index_reads_source_rather_than_importing_it():
    """It has to work mid-refactor, when the package does not import."""
    source = (SCRIPTS / "index_code.py").read_text(encoding="utf-8")

    assert "import ast" in source
    assert "importlib" not in source


def test_the_index_generator_can_report_staleness_without_writing():
    """`--check` exists so the index cannot rot unnoticed.

    It also means `--help` and unknown flags behave, rather than the generator
    ignoring its arguments and rewriting the file regardless - which is what it
    did before, so running `--help` had a side effect.
    """
    result = subprocess.run(
        [sys.executable, "-m", "scripts.index_code", "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode in (0, 1)
    assert "index is" in (result.stdout + result.stderr)


# --------------------------------------------------------------------------
# Console encoding — a Windows console is not always UTF-8
# --------------------------------------------------------------------------

CLI_SOURCES = [p for p in SCRIPTS.rglob("*.py") if "__pycache__" not in str(p)] + [
    REPO / "app" / "intelligence" / "explain.py"
]


@pytest.mark.parametrize("path", CLI_SOURCES, ids=lambda p: p.name)
def test_no_cli_source_contains_a_character_the_console_cannot_print(path):
    """An em dash crashed `scripts.sync explain` outright.

    The development console here is cp932, and a judge's may be cp1252 or
    anything else. `argparse(description=__doc__)` also prints module docstrings
    as --help text, so a docstring is just as exposed as a print(). ASCII-only is
    the only rule that holds on every machine.
    """
    text = path.read_text(encoding="utf-8")
    offenders = sorted({c for c in text if ord(c) > 127})

    assert not offenders, f"{path.name} contains {offenders}"


@pytest.mark.parametrize("name", ["sync", "replay", "gen_demo_data", "index_code"])
def test_help_text_survives_a_legacy_codepage(name):
    """`--help` renders the module docstring; it must encode on cp932 and cp1252."""
    module = __import__(f"scripts.{name}", fromlist=["main"])

    for codec in ("cp932", "cp1252", "ascii"):
        (module.__doc__ or "").encode(codec)


# --------------------------------------------------------------------------
# explain — the arithmetic, shown
# --------------------------------------------------------------------------


def test_explain_renders_a_checkable_table_and_working():
    from datetime import date

    from app.intelligence import explain
    from app.intelligence.schedule.graph import EdgeRecord, TaskNode, build_graph
    from app.intelligence.schedule.impact import project_schedule

    tasks = [
        TaskNode("A", "p", title="Env", start_date=date(2026, 2, 16),
                 planned_end=date(2026, 3, 16), baseline_end=date(2026, 3, 4)),
        TaskNode("B", "p", title="Build", start_date=date(2026, 3, 5),
                 planned_end=date(2026, 4, 1), baseline_end=date(2026, 3, 20)),
    ]
    schedule = build_graph(tasks, [EdgeRecord("A", "B")])
    impact = project_schedule(schedule)

    table = explain.table(schedule, impact)
    working = explain.working(schedule, impact, "B")

    # Every column header present, and one row per task.
    for header, _ in explain.HEADERS:
        assert header in table
    assert table.count("\n") == 3  # header, rule, two rows

    # The terminal rendering is the same three-part shape as the page, because a
    # reader following along on paper needs the same structure either way.
    for section in ("INPUT", "ALGORITHM", "OUTPUT"):
        assert section in working
    for field in ("formula", "values", "result"):
        assert field in working


def test_explain_truncates_a_synthetic_key_rather_than_breaking_alignment():
    """A `~anon-<hex>` key is far wider than any real Task ID."""
    from datetime import date

    from app.intelligence import explain
    from app.intelligence.schedule.graph import TaskNode, build_graph
    from app.intelligence.schedule.impact import project_schedule

    long_key = "~anon-ef0576ffa2b2a0f2"
    tasks = [
        TaskNode(long_key, "p", start_date=date(2026, 3, 1),
                 planned_end=date(2026, 3, 5), baseline_end=date(2026, 3, 5))
    ]
    schedule = build_graph(tasks, [])

    lines = explain.table(schedule, project_schedule(schedule)).splitlines()

    assert len(lines[0]) == len(lines[2]), "row width does not match the header"
    assert long_key not in lines[2]


def test_explain_shows_a_dash_where_a_number_is_unknown():
    """Printing 0 for "no baseline" reads as "on time", which is a lie."""
    from datetime import date

    from app.intelligence import explain
    from app.intelligence.schedule.graph import TaskNode, build_graph
    from app.intelligence.schedule.impact import project_schedule

    tasks = [TaskNode("A", "p", start_date=date(2026, 3, 1),
                      planned_end=date(2026, 3, 5))]  # no baseline
    schedule = build_graph(tasks, [])

    impact = project_schedule(schedule)
    row = explain.rows(schedule, impact)[0]
    derived = explain.derive(schedule, impact, "A")

    # The overview shows a dash; the derivation says so in words.
    assert row.propagated == "0"
    assert any(o.value == explain.ABSENT for o in derived.inputs)
    assert any(o.value == "-" for o in derived.outputs)


# --------------------------------------------------------------------------
# publish — the static snapshot
# --------------------------------------------------------------------------


def test_the_snapshot_never_overwrites_the_architecture_page():
    """`site/index.html` is the one live URL people already have.

    A publisher that clobbered it would take down something that works in order
    to add something new.
    """
    from scripts.publish import APP_ROUTES, STATIC_PAGES, _published_pages

    assert "index.html" not in _published_pages()
    assert "index" not in APP_ROUTES
    assert "index.html" not in STATIC_PAGES


def test_the_snapshot_publishes_assets_at_the_url_the_pages_request():
    """The failure this replaces was invisible to every status check.

    The pages linked `/static/shell.css`; the publisher wrote `site/shell.css`.
    Cloudflare Pages answers an unmatched path with `index.html` and status
    **200**, so `curl -w %{http_code}` said 200 while the browser refused to
    apply HTML as a stylesheet - a whole site of unstyled pages, no chart, and
    white-on-white text where an inline rule set `color:#fff` against a token
    that no longer resolved.

    Destinations are now derived from the reference, so they cannot disagree.
    """
    from scripts.publish import _referenced_assets, _source_for

    referenced = _referenced_assets()
    assert referenced, "no assets found - has the app been built?"

    for url in referenced:
        assert url.startswith("/static/"), url
        assert _source_for(url).exists(), f"{url} -> {_source_for(url)} missing"


def test_the_snapshot_covers_the_hashed_bundle_paths():
    """Vite re-hashes filenames on every content change.

    A hardcoded asset list would go stale on the next build and publish a page
    pointing at a file that is no longer there.
    """
    from scripts.publish import _referenced_assets

    bundle_assets = [u for u in _referenced_assets() if u.startswith("/static/app/")]

    assert bundle_assets, "the built bundle is not referenced by any page"
    assert any(u.endswith(".js") for u in bundle_assets)
    assert any(u.endswith(".css") for u in bundle_assets)


def test_both_app_routes_get_their_own_shell():
    """Pages routes `/insight` to `insight.html`, so each needs a copy."""
    from scripts.publish import APP_SHELL, _published_pages

    pages = _published_pages()

    assert pages["insight.html"] == APP_SHELL
    assert pages["explain.html"] == APP_SHELL


def test_the_publisher_refuses_an_incomplete_snapshot():
    """A build-time check, because a silent 200 is not caught by looking."""
    source = (SCRIPTS / "publish.py").read_text(encoding="utf-8")

    assert "def _verify(" in source
    assert "_verify()" in source
    assert "index.html" in source and "200" in source


def test_the_snapshot_relabels_the_tab_that_cannot_work_statically():
    """The retriever console POSTs; there is no server on Pages to POST to."""
    from scripts.publish import _snapshot_nav

    source = '<nav class="tabs">\n<a href="/">Retriever console</a>\n</nav>'

    rewritten = _snapshot_nav(source, "2026-09-07")

    assert ">Architecture<" in rewritten
    assert "Retriever console" not in rewritten


def test_the_snapshot_says_it_is_a_snapshot_and_when():
    """Presenting frozen data as a live system is the dishonesty this avoids."""
    from scripts.publish import _snapshot_nav

    rewritten = _snapshot_nav('<nav class="rail"><a href="/">Console</a></nav>\\n<div class="appbar">\\n  <h1>Schedule</h1>\\n  <span class="spacer"></span>\\n</div>', "2026-09-07")

    assert "static snapshot" in rewritten
    assert "2026-09-07" in rewritten
    assert "runs locally" in rewritten
    # In the app bar, not the rail: navigation is a 64px column now, and a
    # sentence appended inside it would be squeezed to nothing.
    assert rewritten.index("static snapshot") > rewritten.index('appbar')


def test_the_snapshot_note_is_not_added_twice():
    """Publishing twice must not stack banners."""
    from scripts.publish import _snapshot_nav

    once = _snapshot_nav('<nav class="rail"><a href="/">Console</a></nav>\\n<div class="appbar">\\n  <h1>Schedule</h1>\\n  <span class="spacer"></span>\\n</div>', "2026-09-07")
    twice = _snapshot_nav(once, "2026-09-08")

    assert twice.count("static snapshot") == 1


def test_published_asset_urls_carry_a_content_hash():
    """A deploy that fixes a broken asset must be visible immediately.

    Pages caches assets for hours. When `/static/shell.css` briefly returned
    `index.html`, every visitor held that HTML and the fixed deploy changed
    nothing they could see - "hard-reload" is an apology, not a fix. Hashing the
    URL makes a republish self-invalidating.
    """
    from scripts.publish import _fingerprint, _version_assets

    static = SCRIPTS.parent / "app" / "api" / "static"
    versioned = _version_assets('<link rel="stylesheet" href="/static/shell.css">')

    digest = _fingerprint(static / "shell.css")
    assert f"/static/shell.css?v={digest}" in versioned
    assert len(digest) == 8


def test_the_hash_changes_only_when_the_bytes_do(tmp_path):
    """Content-addressed, not date-stamped.

    A date stamp would not change on a same-day republish - exactly when a fix
    needs to invalidate.
    """
    from scripts.publish import _fingerprint

    target = tmp_path / "a.css"
    target.write_text("body { color: red }", encoding="utf-8")
    before = _fingerprint(target)

    target.write_text("body { color: red }", encoding="utf-8")
    assert _fingerprint(target) == before

    target.write_text("body { color: blue }", encoding="utf-8")
    assert _fingerprint(target) != before


def test_immutable_caching_is_only_claimed_for_hashed_paths():
    """`immutable` is safe with a content hash and dangerous without one."""
    from scripts.publish import HEADERS

    assert "/static/*" in HEADERS
    assert "immutable" in HEADERS

    source = (SCRIPTS / "publish.py").read_text(encoding="utf-8")
    assert "_version_assets(_snapshot_nav(" in source


# --------------------------------------------------------------------------
# `scripts.replay` deleting a database it was not asked to build
# --------------------------------------------------------------------------


def test_replay_only_deletes_the_database_it_builds():
    """A regression test for a bug that destroyed data.

    `replay` removed `--db` (default `pulse.db`) whatever `DATABASE_URL` said,
    so building any other SQLite database silently deleted the default one. It
    ate a populated `pulse.db` the first time `scripts.serve` seeded a different
    file, and it would have taken a judge's demo data just as readily.
    """
    from pathlib import Path

    from scripts.replay import REPO, sqlite_file_to_remove

    # The case that caused the loss: a different SQLite target.
    assert sqlite_file_to_remove("sqlite:///fresh.db", postgres=False) == (
        REPO / "fresh.db"
    )

    # A server is never a file. This one mapping to a path would be catastrophic
    # rather than merely annoying.
    assert (
        sqlite_file_to_remove("postgresql+psycopg://u:p@host/db", postgres=False)
        is None
    )
    assert sqlite_file_to_remove("sqlite:///pulse.db", postgres=True) is None

    # `sqlite://` with no path is in-memory - there is nothing to unlink.
    assert sqlite_file_to_remove("sqlite://", postgres=False) is None

    # An absolute path is respected rather than re-rooted under the repo.
    absolute = Path("C:/tmp/x.db") if REPO.drive else Path("/tmp/x.db")
    assert sqlite_file_to_remove(
        f"sqlite:///{absolute}", postgres=False
    ) == absolute


def test_the_relabel_is_matched_by_href_not_by_label_text():
    """The trap this closes.

    The rewrite used to be a literal `.replace(">Retriever console<", ...)`.
    Renaming that tab turned it into a silent no-op - the snapshot shipped with
    a console link that cannot work on Pages - and the test above went on
    passing, because "the old label is absent" is trivially true once the old
    label is gone. Matching the `href` makes the label free to change.
    """
    from scripts.publish import _snapshot_nav

    renamed = '<nav class="tabs">\n<a href="/">Console</a>\n</nav>'

    rewritten = _snapshot_nav(renamed, "2026-09-07")

    assert ">Architecture<" in rewritten
    assert ">Console<" not in rewritten


def test_a_tab_bar_with_tabs_but_no_root_link_fails_the_build():
    """Loudly, rather than publishing a page whose first tab is dead."""
    import pytest

    from scripts.publish import _snapshot_nav

    with pytest.raises(SystemExit) as caught:
        _snapshot_nav(
            '<nav class="tabs">\n<a href="/insight">Insight</a>\n</nav>', "2026-09-07"
        )

    assert "none link to" in str(caught.value)


def test_the_react_pages_carry_no_static_nav_and_pass_through():
    """They build their tab bar in JavaScript, so there is nothing to rewrite.

    Asserted because the guard above must not start failing the build for the
    two pages that legitimately have no `<nav class="tabs">`.
    """
    from scripts.publish import _snapshot_nav

    react_page = '<div id="root"></div><script src="/static/app/x.js"></script>'

    assert "Architecture" not in _snapshot_nav(react_page, "2026-09-07")


def test_an_empty_tab_bar_is_not_treated_as_a_broken_one():
    """A nav with no tabs has no dead console link to worry about.

    This is the distinction the first version of the guard got wrong: it fired
    on any static nav, which broke the two tests that check the freeze stamp.
    """
    from scripts.publish import _snapshot_nav

    assert _snapshot_nav('<nav class="tabs">\n</nav>', "2026-09-07")


def test_a_page_with_no_server_rendered_bar_is_left_alone():
    """The React pages build their chrome in the bundle.

    There is nothing to stamp, so the publisher must say nothing rather than
    inject stray markup into a page it does not understand.
    """
    from scripts.publish import _snapshot_nav

    react_page = '<div id="root"></div>'

    assert _snapshot_nav(react_page, "2026-09-07") == react_page
