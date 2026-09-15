"""Entry point: ``python -m cowork_local``."""
from __future__ import annotations

import sys


def main() -> int:
    # Imported lazily so that ``-h`` style tooling and tests can import the
    # package without spinning up a full Qt application.
    from .app import run

    return run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
