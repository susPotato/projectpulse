"""Start the retriever console.

    python -m scripts.demo

Then open http://127.0.0.1:8000 and edit the workbooks in `data/demo/`.
"""

from __future__ import annotations

# Must run before any `app.*` import: `app.config` freezes DATABASE_URL when it is
# imported, so choosing a database after that line has no effect. See _bootstrap.
from scripts._bootstrap import bootstrap

DATABASE_URL = bootstrap()

import webbrowser  # noqa: E402

import uvicorn  # noqa: E402

from app.db import check_connection, create_all  # noqa: E402

HOST, PORT = "127.0.0.1", 8000


def main() -> None:
    print(f"database: {DATABASE_URL}")

    problem = check_connection()
    if problem is not None:
        # Refuse to start rather than serve a console whose every request fails.
        raise SystemExit(f"cannot start: {problem}")

    create_all()

    print(f"retriever console: http://{HOST}:{PORT}")
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception:  # noqa: BLE001 - a browser is a convenience, not a requirement
        pass

    uvicorn.run("app.api.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
