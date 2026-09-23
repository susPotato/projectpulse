"""Screenshot every page, so "does it still look right?" is a command.

    python -m scripts.shots                  # all pages, dark, into shots/
    python -m scripts.shots --light
    python -m scripts.shots --page /insight

On Git Bash, prefix that last one with MSYS_NO_PATHCONV=1 - MSYS rewrites a
leading-slash argument into a Windows path, so `--page /insight` arrives as
`C:/.../Git/insight` and the picture lands somewhere surprising.

Why this exists: the front end was being checked by starting the server, poking
one URL with curl, and reading the HTML. That proves a page answers; it proves
nothing about whether it looks like the design. A layout that has collapsed to
one column, a panel that renders white-on-white, a hero that lost its figure -
all of those return 200 with the right content type.

Headless Chrome or Edge, whichever is installed. No new dependency: this drives
the browser already on the machine through its command line, so there is no
Playwright to install and nothing to keep in sync.

It starts the app itself if nothing is listening, and stops it again. If a
server is already up it leaves it alone - and if that server is stale it will
happily photograph the old build, which is the trap `scripts.demo` warns about,
so it prints which it did.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.request import urlopen  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
HOST, PORT = "127.0.0.1", 8000

#: Every page, the name its picture gets, and how tall to make the window.
#:
#: The height is per page and not one generous value for all of them, because
#: photographing a SHORT page in a very tall window makes Chrome repeat the
#: paint - the settings page came back with its header appearing twice, which
#: reads exactly like a duplicate-render bug and is not one. Each entry is
#: sized to its own content: enough to avoid cutting a panel in half, not so
#: much that the capture invents things.
#:
#: Every entry in `Shell.tsx`'s TABS belongs here. `/risk` and `/agent` were
#: added to the app and not to this list, so the one check that looks at the
#: rendered page had stopped covering them - which is the same silence the
#: check exists to break. `/console` and `/explain` came off when those two
#: pages were deleted.
PAGES = (
    ("/portfolio", "program", 1200),
    ("/programs", "programs", 900),
    ("/projects", "projects", 900),
    #: Programs are keyed `program:Program:0:<KEY>` - source-neutral and
    #: connection-neutral. This entry still named `excel:Program:1:DEFAULT`,
    #: which `scripts.migrate_programs` re-keyed away, so the one check that
    #: looks at a rendered page was photographing "No program selected" and
    #: reporting success. The id a URL carries is part of what this script
    #: covers, and a stale one here is a silent hole exactly like the missing
    #: `/risk` entry above.
    ("/programs/dashboard?program=program:Program:0:DEFAULT", "program-dashboard", 1800),
    #: Taller than the rest: "Default setup" places thirteen tiles over six
    #: grid rows, and the previous 1400 cut the board off mid-Gantt.
    ("/project/dashboard?project=excel:Project:1:HRMS", "project-dashboard", 2600),
    ("/gantt", "schedule", 1100),
    ("/insight", "insight", 2500),
    ("/risk", "risk", 1600),
    ("/team", "team", 1500),
    ("/reports", "reports", 1800),
    ("/agent", "agent", 1100),
    #: Taller than it was: 950 predated the Jira connection list and the
    #: code repository card, and cut the page off above both - so the one
    #: check that looks at Settings was photographing the upload form and
    #: reporting success.
    ("/settings", "settings", 3600),
    #: The sign-in screen, and the one entry deliberately photographed with no
    #: credential - see SIGNED_OUT below. Every other page in this list only
    #: exists behind it now, so a login form that has collapsed would be a
    #: picture of a broken product on every shot in the directory and no
    #: picture of the thing that broke.
    ("/?screen=login", "login", 900),
)

#: Paths photographed as a visitor who is NOT signed in. Everything else gets
#: `?signin=` appended - see `sign_in_url`.
#:
#: Matched on the whole entry path rather than on the bare route, because "/"
#: signed in is the Programs list and "/" signed out is the login form: the
#: same URL is two screens and this list is what tells them apart. The
#: `screen=login` parameter is inert - nothing reads it - and is here so the
#: two entries are distinguishable to a human reading the list, and to
#: `--page`.
SIGNED_OUT = frozenset({"/?screen=login"})

#: The demo credential, the same pair `web/src/auth.ts` checks.
#:
#: Headless Chrome gets a throwaway profile per shot (see `shoot`), so there is
#: no `localStorage` to seed and without this every picture below would be of
#: the login form. This is not a bypass: the bundle runs the same check on it
#: that the form runs, and strips it from the address bar before painting, so
#: it does not appear in the screenshot either.
CREDENTIAL = "admin:1234"


def sign_in_url(path: str) -> str:
    """`path` with the demo credential attached, unless it is a signed-out shot."""
    if path in SIGNED_OUT:
        return path
    return f"{path}{'&' if '?' in path else '?'}signin={CREDENTIAL}"


WIDTH = 1400
DEFAULT_HEIGHT = 1400

CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str | None:
    for path in CANDIDATES:
        if Path(path).exists():
            return path
    return None


def listening() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((HOST, PORT)) == 0


def wait_for_server(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(f"http://{HOST}:{PORT}/api/settings", timeout=2) as answer:
                if answer.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - it is simply not up yet
            time.sleep(0.5)
    return False


def status_of(url: str) -> int:
    """The HTTP status for a page, so a 404 is not photographed as a design.

    This exists because the stale-server trap bit three times in one session:
    a `scripts.demo` that fails to bind leaves an OLDER build answering on the
    port, and every screenshot is then of a page that no longer exists. The
    warning printed above was accurate and easy to ignore; a failed check is
    not.
    """
    from urllib.error import HTTPError

    try:
        with urlopen(url, timeout=10) as answer:
            return answer.status
    except HTTPError as exc:
        return exc.code
    except Exception:  # noqa: BLE001 - unreachable is its own answer
        return 0


def shoot(browser: str, url: str, out: Path, *, dark: bool, height: int) -> bool:
    """One page, one PNG. Returns whether a file appeared.

    Each call gets its own throwaway profile. Without one, six launches in a
    row share the default user-data-dir and contend for it: the Schedule page
    came back as its own "chart component did not load" error page - a real
    error, correctly reported by the page, caused entirely by the screenshot
    tool. The same shot taken alone was fine, which is exactly how a flaky
    check teaches you to ignore it.
    """
    profile = tempfile.mkdtemp(prefix="pulse-shot-")
    args = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # The pages follow `prefers-color-scheme`. Headless used to default to
        # light, which is why only the dark case carried a flag - but on a
        # host whose OS theme is dark, `--headless=new` (unlike the old
        # headless) inherits that, and "light" silently photographed dark
        # instead. `--blink-settings=preferredColorScheme=1` pins it back to
        # light regardless of the OS. Verified empirically, not from docs:
        # passing this alongside `--force-dark-mode` for the dark case wins
        # over it and photographs light, so the two flags are exclusive.
        *(["--force-dark-mode"] if dark else ["--blink-settings=preferredColorScheme=1"]),
        # The React pages fetch before they render, so a screenshot taken at
        # load is a picture of "Loading...". This lets virtual time run on.
        "--virtual-time-budget=6000",
        f"--window-size={WIDTH},{height}",
        f"--screenshot={out}",
        url,
    ]
    try:
        subprocess.run(args, capture_output=True, text=True, timeout=120)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return out.exists() and out.stat().st_size > 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="shots", help="directory for the PNGs")
    parser.add_argument("--page", action="append", help="just this path, repeatable")
    parser.add_argument(
        "--light", action="store_true", help="photograph the light theme instead"
    )
    parser.add_argument(
        "--height", type=int, default=0,
        help=f"window height for every page (default: per page, else {DEFAULT_HEIGHT})",
    )
    args = parser.parse_args(argv)

    browser = find_browser()
    if browser is None:
        print(
            "no Chrome or Edge found. Install one, or check the pages by hand:\n"
            f"  python -m scripts.demo   then open http://{HOST}:{PORT}/insight",
            file=sys.stderr,
        )
        return 2

    started = None
    if listening():
        print(f"[shots] using the server already on :{PORT} - if it is stale, the")
        print("[shots] pictures will be of the previous build. Restart it if unsure.")
    else:
        print(f"[shots] starting the app on :{PORT}")
        env = dict(os.environ)
        env.setdefault("DATABASE_URL", "sqlite:///pulse.db")
        started = subprocess.Popen(
            [sys.executable, "-m", "scripts.demo"],
            cwd=REPO,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_for_server():
            started.terminate()
            print("[shots] the app did not come up", file=sys.stderr)
            return 1

    out_dir = REPO / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    # `--page` looks the path up in PAGES rather than inventing an entry, so
    # re-shooting one page gives the SAME picture the full run gives. It used
    # to fall straight to DEFAULT_HEIGHT, which quietly cropped a tall page
    # differently from the full run - and re-shooting a page alone is exactly
    # what the flaky-Schedule note above tells you to do, so the one workflow
    # meant to confirm a doubt was the one that changed the evidence.
    known = {path: entry for entry in PAGES for path in (entry[0],)}
    wanted = (
        [known.get(p, (p, p.strip("/") or "console", DEFAULT_HEIGHT)) for p in args.page]
        if args.page
        else list(PAGES)
    )
    theme = "light" if args.light else "dark"

    failed = []
    try:
        for path, name, page_height in wanted:
            target = out_dir / f"{name}-{theme}.png"

            code = status_of(f"http://{HOST}:{PORT}{path}")
            if code != 200:
                print(f"  {path:<12} HTTP {code} - not photographed")
                failed.append(f"{path} (HTTP {code})")
                continue

            ok = shoot(
                browser,
                f"http://{HOST}:{PORT}{sign_in_url(path)}",
                target,
                dark=not args.light,
                height=args.height or page_height,
            )
            size = target.stat().st_size if ok else 0
            print(f"  {path:<12} {'ok ' if ok else 'FAILED'} {size:>8} bytes  {target}")
            if not ok:
                failed.append(path)
    finally:
        if started is not None:
            started.terminate()
            print("[shots] stopped the app")

    if failed:
        print(f"[shots] no picture for: {', '.join(failed)}", file=sys.stderr)
        if any("HTTP 404" in item for item in failed):
            print(
                "[shots] a 404 on a route that exists in the code means a STALE "
                "SERVER is holding the port - `scripts.demo` prints a bind error "
                "and keeps going. Kill it and retry:",
                file=sys.stderr,
            )
            print(
                "  Get-NetTCPConnection -LocalPort 8000 -State Listen | "
                "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }",
                file=sys.stderr,
            )
        return 1

    print(f"\n[shots] {len(wanted)} page(s) in {out_dir}")
    print("[shots] Open them and compare against design/ - a page can answer 200")
    print("[shots] with a collapsed layout, so this is the check that catches it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
