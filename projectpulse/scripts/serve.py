"""Start the app the way a server starts it: schema, seed if empty, then serve.

    python -m scripts.serve

This is the container entry point. Written in Python rather than as a shell
script for two reasons: it can be run and verified on the machine it was written
on, which a Linux entrypoint.sh cannot be, and the "is the database empty" test
is a query rather than a guess.

The seeding rule is the part worth reading. **It seeds only an empty database.**
`scripts.replay` replays a timeline of edits and syncs; running it against a
database that already holds that timeline would append a second copy, and the
precision model would then see two overlapping histories of the same project -
which is not a crash, but it is a demo quietly showing twice as many state
changes as it should. So the check is for emptiness, and a database with
anything in it is left exactly as it is.

NOTE: a server has no OneDrive folder. The Sync button and `sync run` need
`data_root`, which on a laptop is a synced folder. In a container the demo
timeline is *generated* by `scripts.replay` from code, so the deployed app is
complete and real - findings, evidence, chains, the Gantt, the working - and
only live re-ingestion is absent until Graph API consent lands. That is the
trade recorded in CLAUDE.md, not a gap discovered here.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def database_url() -> str:
    from app.config import settings

    return settings.database_url


def is_empty() -> bool:
    """Whether this database has no delivery data yet.

    Counts tasks rather than any table: `sync_runs` gets a row from a failed
    boot, so a run-count test would decide a database was seeded when it holds
    nothing a page could render.
    """
    from sqlalchemy import func, select

    from app.db import session_scope
    from app.models.domain import Task

    with session_scope() as session:
        return not session.scalar(select(func.count()).select_from(Task))


def seed() -> None:
    """Replay the demo timeline into an empty database."""
    print("[serve] database is empty; replaying the demo timeline")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.replay"],
        cwd=REPO,
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Loudly, and then carry on. An app that serves "no data for project"
        # is diagnosable from a browser; an app that exits on boot is a crash
        # loop and a red dashboard, and the failure is usually the database URL
        # rather than anything this code can fix by retrying.
        sys.stderr.write(result.stdout + result.stderr)
        print("[serve] seeding failed; starting anyway", file=sys.stderr)
        return
    print("[serve] seeded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", default=os.getenv("HOST", "0.0.0.0"),
        help="interface to bind (default 0.0.0.0, which is what a container needs)",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("PORT", "8080")),
        help="port to bind (default $PORT, or 8080)",
    )
    parser.add_argument(
        "--no-seed", action="store_true",
        help="skip the emptiness check and never replay the demo timeline",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="do the schema and seed steps, report, and exit without serving",
    )
    args = parser.parse_args(argv)

    url = database_url()
    if url.startswith("sqlite"):
        # Worth saying out loud on a server: a container filesystem does not
        # survive a restart, so this is a database that silently empties.
        print(
            "[serve] WARNING: using SQLite. Fine locally; on a server the file "
            "goes away with the container and the app comes back empty. Set "
            "DATABASE_URL to a managed Postgres.",
            file=sys.stderr,
        )
    print(f"[serve] database: {url}")

    from app.db import create_all

    create_all()
    print("[serve] schema ready")

    if args.no_seed:
        print("[serve] seeding skipped")
    elif is_empty():
        seed()
    else:
        print("[serve] database already holds data; not seeding")

    if args.check:
        print("[serve] check complete; not serving")
        return 0

    import uvicorn

    print(f"[serve] listening on {args.host}:{args.port}")
    uvicorn.run("app.api.main:app", host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
