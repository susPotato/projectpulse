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
#: check exists to break.
PAGES = (
    ("/portfolio", "program", 1200),
    ("/", "console", 1500),
    ("/gantt", "schedule", 1100),
    ("/insight", "insight", 2400),
    ("/risk", "risk", 1600),
    ("/team", "team", 1500),
    ("/explain", "calculation", 2400),
    ("/reports", "reports", 1800),
    ("/agent", "agent", 1100),
    ("/settings", "settings", 950),
)

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
        # The pages follow `prefers-color-scheme`, and headless defaults to
        # light. Without this the dark design is never what gets photographed.
        *(["--force-dark-mode"] if dark else []),
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
    wanted = (
        [(p, p.strip("/") or "console", DEFAULT_HEIGHT) for p in args.page]
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
                f"http://{HOST}:{PORT}{path}",
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
