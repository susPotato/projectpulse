"""Start the retriever console.

    python -m scripts.demo

Then open http://127.0.0.1:8000 and edit the workbooks in `data/demo/`.
"""

from __future__ import annotations

import os
import webbrowser

import uvicorn

from app.db import create_all

HOST, PORT = "127.0.0.1", 8000


def main() -> None:
    # SQLite by default so the console runs with nothing else installed. Point
    # DATABASE_URL at Postgres for the real thing.
    os.environ.setdefault("DATABASE_URL", "sqlite:///pulse.db")
    create_all()

    print(f"retriever console: http://{HOST}:{PORT}")
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception:  # noqa: BLE001 - a browser is a convenience, not a requirement
        pass

    uvicorn.run("app.api.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
