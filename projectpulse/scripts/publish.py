"""Bake a static snapshot of the app into `site/`.

    python -m scripts.publish

**Why a snapshot rather than the app.** Cloudflare Pages serves static assets;
the FastAPI app needs Python and a database, and Workers cannot run `psycopg`.
So the live product cannot be hosted there - but the screens only *fetch* JSON,
so freezing the JSON alongside them gives a public, clickable page with the real
numbers on it. It is the demo, not the service.

That distinction is stated on the page itself, next to the date it was frozen.
Presenting a snapshot as a live system would be the same class of dishonesty the
rest of this codebase works to avoid.

**Two page shapes.** Insight and Calculation are one built React bundle served at
two routes, so the same shell is written to both `insight.html` and
`explain.html` - Pages serves those at `/insight` and `/explain`. Schedule is
still a hand-written page.

**Asset paths are derived, never declared.** The pages ask for
`/static/app/assets/index-<hash>.js`; an earlier version wrote assets to the site
root and Pages answered the missing path with `index.html` and status **200**, so
every check passed while the browser silently refused HTML as a stylesheet.
`_referenced_assets` reads the URLs out of the pages and copies each file to that
exact path, and `_verify` fails the build if one is missing.

**`site/index.html` is never touched.** It is the architecture page and the one
URL people already have.
"""

from __future__ import annotations

from scripts._bootstrap import bootstrap

DATABASE_URL = bootstrap()

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path, PurePosixPath  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
#: The Pages project root is the repo's parent - `wrangler.toml` lives there.
SITE = REPO.parent / "site"
STATIC = REPO / "app" / "api" / "static"

#: Routes served by the built React bundle. Each gets its own copy of the shell,
#: because Pages routes `/insight` to `insight.html`.
APP_ROUTES = ("insight", "explain")
#: Pages that are still hand-written HTML.
STATIC_PAGES = ("gantt.html",)

APP_SHELL = STATIC / "app" / "index.html"

#: Matches `href="/static/..."` / `src="/static/..."`, with or without a query.
ASSET_REF = re.compile(r'(?:href|src)="(/static/[^"?]+)(\?[^"]*)?"')

#: Content types Pages will not infer, because the JSON files are extensionless -
#: they have to be, since the pages fetch `/api/insight` verbatim.
#:
#: `immutable` on /static/* is safe for two reasons: Vite content-hashes the
#: bundle's filenames, and `_version_assets` appends a hash to everything else.
#: A changed file always gets a changed URL, so a long cache can never pin a
#: stale asset. Without that it would be the opposite of safe - it is what held
#: a broken stylesheet for four hours.
HEADERS = """/api/*
  Content-Type: application/json; charset=utf-8
  Cache-Control: public, max-age=300

/static/*
  Cache-Control: public, max-age=31536000, immutable
"""


def _published_pages() -> dict[str, Path]:
    """Output filename -> the source HTML it is copied from."""
    pages: dict[str, Path] = {f"{route}.html": APP_SHELL for route in APP_ROUTES}
    pages.update({name: STATIC / name for name in STATIC_PAGES})
    return pages


def _referenced_assets() -> list[str]:
    """Every `/static/...` URL the published pages ask for.

    Read out of the pages rather than hardcoded, so adding a `<link>` - or Vite
    re-hashing a filename on the next build - cannot leave a live page unstyled.
    """
    urls: list[str] = []
    for source in _published_pages().values():
        if not source.exists():
            continue
        for url, _query in ASSET_REF.findall(source.read_text(encoding="utf-8")):
            if url not in urls:
                urls.append(url)
    return urls


def _source_for(url: str) -> Path:
    """`/static/app/assets/x.js` -> `app/api/static/app/assets/x.js`."""
    return STATIC / PurePosixPath(url).relative_to("/static")


def _fingerprint(path: Path) -> str:
    """First eight hex of the file's sha256.

    Content-addressed rather than date-stamped: a same-day republish of a fixed
    file has to change the URL, or the browser keeps serving the broken one it
    already has.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def _version_assets(html: str) -> str:
    """Give each asset reference a content hash.

    Vite already hashes the bundle's filenames; this covers the hand-written
    assets (`gantt.js`, `gantt.css`) so every URL under /static is safe to cache
    for a year.
    """

    def replace(match: re.Match[str]) -> str:
        url = match.group(1)
        source = _source_for(url)
        if not source.exists():
            return match.group(0)
        attribute = match.group(0).split("=", 1)[0]
        return f'{attribute}="{url}?v={_fingerprint(source)}"'

    return ASSET_REF.sub(replace, html)


def _snapshot_nav(html: str, stamp: str) -> str:
    """Point the tab bar at what actually exists on the static site.

    Locally `/` is the retriever console; on Pages it is the architecture page,
    and the console cannot work at all without a server to POST to. Rewriting
    the label is more honest than leaving a tab that does nothing.

    The React pages build their tab bar in JavaScript, so the label swap lands
    in the bundle rather than here - `Shell.tsx` reads it from the page. What
    this still does for them is stamp the freeze date.
    """
    html = html.replace(">Retriever console<", ">Architecture<")

    note = (
        f'  <span class="tab-note">static snapshot, data frozen {stamp} '
        "- the live app runs locally</span>\n"
    )
    if '<span class="tab-note">' not in html and "</nav>" in html:
        html = html.replace("</nav>", note + "</nav>", 1)
    return html


def _verify() -> None:
    """Fail the build if anything a page asks for is not where it will look.

    Cloudflare Pages answers an unmatched path with `index.html` and status
    **200**, so a missing asset produces a page that loads and is silently
    broken. Checking the filesystem before deploying is the only cheap way to
    catch that - a status code will not.
    """
    missing = [name for name in _published_pages() if not (SITE / name).exists()]
    missing += [
        url for url in _referenced_assets() if not (SITE / url.lstrip("/")).exists()
    ]

    if missing:
        raise SystemExit(
            "snapshot is incomplete - these would 200 with index.html on Pages:\n  "
            + "\n  ".join(missing)
        )


def build(*, project: str, also: list[str], stamp: str | None = None) -> dict:
    """Write the snapshot. Returns what was produced, for printing."""
    from app.db import check_connection, session_scope
    from app.intelligence.pipeline import (
        analyze_project,
        explain_project,
        gantt_project,
    )

    problem = check_connection()
    if problem is not None:
        raise SystemExit(f"cannot build a snapshot: {problem}")

    if not APP_SHELL.exists():
        raise SystemExit(
            "the web bundle is missing. Build it with: "
            "cd web && npm install && npm run build"
        )

    stamp = stamp or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    with session_scope() as session:
        bundles = {
            "insight": analyze_project(session, project_id=project, also=also),
            "explain": explain_project(session, project_id=project, also=also),
            "gantt": gantt_project(session, project_id=project, also=also),
        }

    if not bundles["insight"].findings:
        raise SystemExit(
            "the database has no findings, so the snapshot would be empty. "
            "Run: python -m scripts.replay"
        )

    written: list[str] = []

    api = SITE / "api"
    api.mkdir(parents=True, exist_ok=True)

    # Extensionless on purpose: the pages fetch `/api/insight`, and rewriting
    # the fetch paths for the snapshot would mean publishing a page that is not
    # the page the tests exercise.
    for name, bundle in bundles.items():
        target = api / name
        target.write_text(
            json.dumps(bundle.model_dump(mode="json"), indent=1), encoding="utf-8"
        )
        written.append(str(target.relative_to(SITE.parent)))

    for name, source in _published_pages().items():
        html = _version_assets(_snapshot_nav(source.read_text(encoding="utf-8"), stamp))
        (SITE / name).write_text(html, encoding="utf-8")
        written.append(str((SITE / name).relative_to(SITE.parent)))

    for url in _referenced_assets():
        source = _source_for(url)
        if not source.exists():
            raise SystemExit(f"{url} is referenced but {source} does not exist")
        target = SITE / url.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        written.append(str(target.relative_to(SITE.parent)))

    (SITE / "_headers").write_text(HEADERS, encoding="utf-8")
    written.append(str((SITE / "_headers").relative_to(SITE.parent)))

    _verify()

    return {
        "written": written,
        "findings": len(bundles["insight"].findings),
        "steps": len(bundles["explain"].steps),
        "stamp": stamp,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="excel:Project:1:HRMS")
    parser.add_argument("--also", action="append", default=None)
    parser.add_argument(
        "--stamp", default=None, help="override the frozen-on date (for tests)"
    )
    args = parser.parse_args()

    also = args.also if args.also is not None else ["jira:Project:1:HRMS"]
    result = build(project=args.project, also=also, stamp=args.stamp)

    print(f"snapshot built from {DATABASE_URL}")
    for path in result["written"]:
        print(f"  {path}")
    print(
        f"\n{result['findings']} finding(s), {result['steps']} task(s), "
        f"frozen {result['stamp']}"
    )
    print(
        "\nThe architecture page at site/index.html is untouched.\n"
        "Preview locally:\n"
        "  python -m http.server -d ../site 8080\n"
        "  then open http://127.0.0.1:8080/insight.html\n"
        "\nDeploy (publishes publicly):\n"
        "  cd .. && npx wrangler pages deploy"
    )


if __name__ == "__main__":
    main()
