"""Make every `python -m scripts.*` command work, whatever Python you typed.

Two traps this removes, both of which cost a judge the whole demo rather than one
feature.

**The wrong interpreter.** `python -m scripts.replay` with the system Python dies on
`ModuleNotFoundError: No module named 'sqlalchemy'` - the dependencies are in
`.venv`. Telling people to activate it first is a documentation fix for a problem
that keeps happening, so instead we re-exec into the venv's interpreter and print
a line saying we did.

**Configuration read too late.** `app.config.Settings` captures `DATABASE_URL`
when its class body executes, i.e. during `import app.config`. So an
`os.environ.setdefault` written *after* an `app.*` import - which is what
`scripts/demo.py` had - is dead code: the URL is already frozen at the Postgres
default, and the console then silently talks to a database nobody started.

That is why this module must be imported and called **before any `app.*` import**,
which makes the import order in the entry points look wrong. It is not; there is a
comment at each call site.

The library default stays Postgres, because that is the deployment target. Only
the command-line entry points default to SQLite, so that reading and running the
code needs nothing installed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Set once we have re-exec'd, so a venv that is somehow still missing the
#: dependencies fails with the real ImportError instead of looping.
_GUARD = "PULSE_BOOTSTRAPPED"

#: The database the CLI uses when the developer has not chosen one.
DEFAULT_SQLITE = "sqlite:///pulse.db"


def _venv_python() -> Path | None:
    for candidate in (
        REPO / ".venv" / "Scripts" / "python.exe",  # Windows
        REPO / ".venv" / "bin" / "python",  # POSIX
    ):
        if candidate.exists():
            return candidate
    return None


def _already_in_venv(interpreter: Path) -> bool:
    try:
        return Path(sys.executable).resolve() == interpreter.resolve()
    except OSError:  # pragma: no cover - unusual filesystem
        return False


def ensure_venv() -> None:
    """Re-exec under `.venv` if we are not already there.

    Deliberately loud: swapping interpreter underneath someone silently would be
    worse than the error it prevents.
    """
    if os.environ.get(_GUARD):
        return

    interpreter = _venv_python()
    if interpreter is None:
        return  # no venv to use; let the real ImportError speak for itself
    if _already_in_venv(interpreter):
        os.environ[_GUARD] = "1"
        return

    os.environ[_GUARD] = "1"

    # After execv, `sys.path[0]` becomes the *script's* directory rather than the
    # repo root, so `scripts` and `app` would both stop resolving. PYTHONPATH is
    # what survives the exec.
    os.environ["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(REPO), os.environ.get("PYTHONPATH", "")) if part
    )

    # `python -m scripts.replay` has already been resolved by the time we see
    # sys.argv - argv[0] is the script path, and re-running it that way is what
    # loses the repo root. Rebuild the `-m` form from __main__'s spec so the
    # relaunch is the command the user actually typed.
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if spec is not None and spec.name:
        argv = [str(interpreter), "-m", spec.name, *sys.argv[1:]]
    else:
        argv = [str(interpreter), *sys.argv]

    # stdout, not stderr: PowerShell renders any stderr from a native command as
    # a red NativeCommandError block, which makes a successful relaunch look like
    # a crash.
    print(f"[bootstrap] re-running under {interpreter}")
    os.execv(str(interpreter), argv)


def default_database(url: str = DEFAULT_SQLITE) -> str:
    """Choose a database before `app.config` freezes the choice.

    Returns the URL in effect, so a caller can print it.
    """
    os.environ.setdefault("DATABASE_URL", url)
    return os.environ["DATABASE_URL"]


def printable_console() -> None:
    """Never crash on a character the console cannot encode.

    `tests/test_scripts.py` already guarantees every CLI *source* file is ASCII,
    because `argparse(description=__doc__)` prints module docstrings and this
    console is cp932. That rule cannot cover **what a language model writes**:
    the FPT gateway's first live narrative came back with an en-dash, and
    `print(bundle.narrative)` died with `UnicodeEncodeError` after the model had
    already been paid for and the whole analysis printed above it.

    Model output is data, not source, so the fix belongs on the way out rather
    than in a rule nobody can enforce upstream. `errors="replace"` degrades one
    character to `?` instead of losing the command - and only on a console that
    cannot represent it, since a UTF-8 terminal encodes it fine. The `.docx`,
    the page and the API are untouched: they were always UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        # Absent under some launchers, and already replacing under others.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - stream is not a tty
            pass


def bootstrap(url: str = DEFAULT_SQLITE) -> str:
    """All three fixes, in the order they have to happen."""
    ensure_venv()
    printable_console()
    return default_database(url)
