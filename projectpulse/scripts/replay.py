"""Build the whole demo from nothing, in one command.

    python -m scripts.replay

Exists because the March timeline is eight interdependent commands and getting one
wrong produces a system that looks broken rather than one that errors. In
particular the two syncs with no edit before them are easy to drop, and dropping
them silently removes every causal chain - the data is fine, the ordering just
becomes unprovable, and the insight page fills with schedule findings and no
"why". That is the worst possible failure for a demo: plausible and wrong.

Defaults to SQLite so it needs no Docker. Pass `--postgres` to use the container,
or set DATABASE_URL yourself and it is respected.
"""

from __future__ import annotations

from scripts._bootstrap import ensure_venv

ensure_venv()

import argparse  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: SQLAlchemy's SQLite URL prefix. Anything else is a server we must not touch.
_SQLITE_PREFIX = "sqlite:///"

#: The timeline. `None` means "sync without editing the sheets first" - those
#: entries are what put a scan between cause and effect, and the chains do not
#: exist without them. See CLAUDE.md section 5.
TIMELINE: tuple[tuple[int | None, str, str], ...] = (
    (0, "excel", "2026-03-02T09:00"),
    (None, "jira_replay", "2026-03-04T12:00"),
    (1, "excel", "2026-03-06T09:00"),  # the environment slips
    (None, "excel", "2026-03-10T09:00"),  # nothing changed - bounds the next change
    (2, "excel", "2026-03-14T09:00"),  # downstream reacts
    (None, "excel", "2026-03-18T09:00"),  # nothing changed
    (3, "excel", "2026-03-22T09:00"),  # the QA backlog grows
)


def sqlite_file_to_remove(target: str, *, postgres: bool) -> Path | None:
    """The SQLite file this run is about to rebuild, if any.

    Extracted so it can be tested, because getting it wrong destroys data.

    It used to be `REPO / args.db` - default "pulse.db" - whatever DATABASE_URL
    actually said, so building any *other* SQLite database silently deleted the
    default one. It ate a populated pulse.db the first time `scripts.serve`
    seeded a different file, and it would have taken a judge's demo data just as
    readily.

    Returns None for anything that is not a local SQLite file we are targeting.
    A Postgres URL must never map to a filesystem path here.
    """
    if postgres or not target.startswith(_SQLITE_PREFIX):
        return None

    path = Path(target[len(_SQLITE_PREFIX) :])
    if not path.name:
        return None  # `sqlite://` with no file is an in-memory database
    return path if path.is_absolute() else REPO / path


def run(args: list[str], env: dict) -> None:
    result = subprocess.run(
        [sys.executable, "-m", *args], cwd=REPO, env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"failed: {' '.join(args)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postgres",
        action="store_true",
        help="use the docker-compose Postgres instead of SQLite",
    )
    parser.add_argument("--db", default="pulse.db", help="SQLite file to build")
    args = parser.parse_args()

    env = dict(os.environ)
    if args.postgres:
        env.pop("DATABASE_URL", None)  # fall back to the Postgres default
    elif "DATABASE_URL" not in env:
        env["DATABASE_URL"] = f"sqlite:///{args.db}"

    target = env.get("DATABASE_URL", "the default Postgres URL")

    # Deliberately no connection check here. `app.config` freezes DATABASE_URL when
    # it is imported, so a check in *this* process would test the parent's URL, not
    # the one being handed to the subprocesses - and report a missing Postgres while
    # happily building SQLite. Each `run()` below raises on failure instead, so
    # reaching the end means every step actually worked.
    print(f"building {target}")

    doomed = sqlite_file_to_remove(target, postgres=args.postgres)
    if doomed is not None and doomed.exists():
        try:
            doomed.unlink()
        except PermissionError:
            # Windows will not delete a file another process holds open, and
            # that process is almost always a `scripts.demo` still serving
            # :8000. The raw traceback reads like a corrupt database, which
            # sends you looking for the wrong thing entirely.
            sys.exit(
                f"cannot rebuild: {doomed.name} is open in another process.\n"
                "A running server holds the database file. Stop it and retry:\n"
                "  Get-NetTCPConnection -LocalPort 8000 -State Listen |"
                " ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }"
            )
        print(f"  removed existing {doomed.name}")

    run(["scripts.sync", "init"], env)
    run(["scripts.gen_jira_data"], env)

    for step, source, when in TIMELINE:
        if step is not None:
            run(["scripts.gen_demo_data", "--step", str(step)], env)
            label = f"step {step}"
        else:
            label = "no edit"
        run(["scripts.sync", "run", "--source", source, "--now", when], env)
        print(f"  {when}  {source:<12} {label}")

    # No DATABASE_URL prefix in these hints: every entry point bootstraps to the
    # same SQLite default, so telling people to set it again would imply the
    # commands need it and reintroduce the trap this script exists to remove.
    print(
        f"\ndone - {target}\n\n"
        "  python -m scripts.demo\n"
        "  then open http://127.0.0.1:8000/insight\n\n"
        "or print it in the terminal:\n"
        "  python -m scripts.sync insight --also jira:Project:1:HRMS --narrative"
    )


if __name__ == "__main__":
    main()
