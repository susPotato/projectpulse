"""Run the traceability pipeline on this server, for one delivery project.

Until now the Traceability page could only *read* runs: `tracelink_view`
opens a run directory as data, and the runs it found were the ones baked into
the image at build time. Registering a repository wrote a row nothing
consumed. This module is the missing half - it takes a registration, a backlog
export and a clone, and produces a run the page can already read.

**A subprocess, not an in-process call.** `tracelink.pipeline.run_plan` takes
an `invoke` callable and the CLI's own `main` is the obvious thing to pass -
upstream does exactly that. Here it would mean running twelve stages inside
the web worker: `basicConfig` reaching for the app's logging, `sys.exit` from
argparse inside a request, a corpus build's peak memory measured against the
same 512 MB the API is serving from, and no way to kill a run that will not
finish. A subprocess gets all four for free, and costs one interpreter start.

**One run at a time, per process.** Not a queue, a refusal: the machine this
deploys to has one shared CPU and 512 MB, and a second concurrent corpus build
is how both fail instead of one succeeding. `state()` reports what is running
so the page can say so rather than silently doing nothing.

⚠️ **State here is per-process and in memory.** With more than one uvicorn
worker, a run started on one is invisible to the others, and a restart forgets
a run in flight while its subprocess keeps going. That is survivable because
the artifacts on disk are the real output and `tracelink_view` reads those - a
forgotten run still finishes and still shows up on the page. It would not be
survivable as a job queue, and this is deliberately not one.

⚠️ **Artifacts land wherever `TRACELINK_RUNS` points.** On Fly that is inside
the image, which does not survive the machine stopping - so a run done today
is gone after the next idle-stop unless that path is on a volume. The run
still works; it just is not kept. See DEPLOY.md.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import REPO_ROOT, settings
from app.models.base import utcnow

log = logging.getLogger(__name__)

#: How much of the pipeline's output to keep for the page. The stages are
#: chatty and the interesting part is always the end - a failing stage names
#: what it wanted in its last few lines.
LOG_LINES = 400

#: Guards `_current`. Held only to read or replace the dict, never across the
#: subprocess itself.
_LOCK = threading.Lock()

#: The run in flight, or the last one to finish. `None` before the first.
_current: dict[str, Any] | None = None


class RunBusy(RuntimeError):
    """A run is already in flight. Names which project, so the page can say."""


class RunUnavailable(RuntimeError):
    """This server cannot run the pipeline, and says which piece is missing."""


def _slug(project_id: str) -> str:
    """A directory name for a project id.

    Ids carry colons (`excel:Project:1:HRMS`), which are legal in a POSIX
    path and are not on Windows - and this has to behave the same in a
    container and on the developer's machine, or a run directory created
    locally cannot be read back by the code that made it.
    """
    return re.sub(r"[^a-z0-9]+", "-", project_id.lower()).strip("-") or "run"


def runs_root() -> Path:
    """Where run directories go. Created if absent.

    Falls back to `<repo>/traceability_runs`, which is where the image already
    keeps the baked ones, so a server with no `TRACELINK_RUNS` set writes
    beside them rather than refusing.

    ⚠️ Must agree with `app/api/tracelink_view.runs_root`, which is where the
    page *reads* them. The two disagreeing does not fail - it produces a run
    nobody can see, which is worse than an error because the pipeline reports
    success.
    """
    raw = os.environ.get("TRACELINK_RUNS", "").strip()
    root = Path(raw).expanduser() if raw else REPO_ROOT / "traceability_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def state() -> dict[str, Any]:
    """The run in flight, or the last one to finish, or an empty answer."""
    with _LOCK:
        if _current is None:
            return {"running": False, "run": None}
        snapshot = dict(_current)
    # Copied out rather than returned live: the worker thread appends to this
    # deque while a request is serialising it, and a mutated-during-iteration
    # deque raises inside the response rather than at the point of the bug.
    snapshot["log"] = list(snapshot.get("log") or [])
    return {"running": snapshot.get("status") == "running", "run": snapshot}


def _argv(*, run_dir: Path, export: Path, repo: Path, docs: Path,
          project_id: str, project_filter: str, done_status: str) -> list[str]:
    """The command, as the CLI would be typed by hand.

    `-m tracelink` rather than a console script: the package is source on the
    path in the image (`PULSE_TRACELINK_HOME=/app`), not an installed
    distribution, so there is no entry point to call.
    """
    argv = [sys.executable, "-m", "tracelink", "--run", str(run_dir), "pipeline",
            "--export", str(export), "--repo", str(repo), "--docs", str(docs),
            # Without this the run's manifest carries no project id and
            # `tracelink_view.run_for_project` cannot match it to anything -
            # the run would complete and the page would still say "Not traced".
            "--project-id", project_id,
            # `map` and `drift` report to stdout and write nothing any other
            # stage or the page reads, so they are pure cost here.
            "--quiet"]
    if project_filter:
        argv += ["--project", project_filter]
    if done_status:
        argv += ["--done-status", done_status]
    return argv


def _worker(argv: list[str], env: dict[str, str], record: dict[str, Any]) -> None:
    """Run the pipeline and keep the tail of its output. Never raises."""
    try:
        proc = subprocess.Popen(
            argv,
            # `tracelink` is source on the path at the repository root, so the
            # child has to start there for `-m tracelink` to resolve.
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        with _LOCK:
            record["status"] = "failed"
            record["error"] = f"could not start the pipeline: {exc}"
            record["finished_at"] = utcnow().isoformat()
        return

    with _LOCK:
        record["pid"] = proc.pid

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        with _LOCK:
            record["log"].append(line)
            # The stage banners the CLI prints are the only progress signal it
            # gives, and they are what a person watching actually wants.
            if line.startswith("=== "):
                record["stage"] = line.strip("= ").strip()
    code = proc.wait()

    with _LOCK:
        record["exit_code"] = code
        record["status"] = "done" if code == 0 else "failed"
        record["finished_at"] = utcnow().isoformat()
        record["stage"] = ""
        if code != 0:
            record["error"] = (
                f"the pipeline exited {code}. The last lines of its output say "
                f"which stage stopped and what it wanted."
            )


def start(*, project_id: str, repo_tree: Path, docs_dir: Path,
          export_bytes: bytes, export_name: str = "",
          project_filter: str = "", done_status: str = "",
          actor: str = "") -> dict[str, Any]:
    """Begin a run. Returns the record immediately; it completes in a thread.

    The export is written to a temp file rather than passed as bytes because
    every stage that reads it takes a path - `read_raw` opens a workbook, and
    handing it a buffer would mean changing the adapters for the one caller
    that does not have a file.
    """
    global _current

    with _LOCK:
        if _current is not None and _current.get("status") == "running":
            raise RunBusy(
                f"a run is already in flight for "
                f"{_current.get('project_id')!r}. This server runs one at a "
                f"time - a second corpus build on one shared CPU makes both "
                f"slow rather than either quick. Wait for it, or check its "
                f"progress on this page."
            )

    if not export_bytes:
        raise RunUnavailable(
            "there is no backlog export for this project. The `tickets` and "
            "`governance` stages read the tracker export as a workbook, so a "
            "run without one has no backlog to trace. Upload the export you "
            "would pass to `tracelink tickets`."
        )

    run_dir = runs_root() / _slug(project_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Kept for the life of the run rather than a `with` block: the subprocess
    # outlives this call, and deleting the export out from under the stage
    # that is reading it is a race that would look like a corrupt workbook.
    handle, raw_path = tempfile.mkstemp(prefix="pulse-export-", suffix=".xlsx")
    with os.fdopen(handle, "wb") as fh:
        fh.write(export_bytes)
    export_path = Path(raw_path)

    env = dict(os.environ)
    # The child imports `tracelink`, and on a host where the package is not
    # installed that resolves only via this. `source.py` sets the same
    # variable for the same reason.
    home = (settings.tracelink_home or "").strip() or str(REPO_ROOT)
    env["PYTHONPATH"] = os.pathsep.join(
        [home, env["PYTHONPATH"]] if env.get("PYTHONPATH") else [home]
    )

    argv = _argv(run_dir=run_dir, export=export_path, repo=repo_tree,
                 docs=docs_dir, project_id=project_id,
                 project_filter=project_filter, done_status=done_status)

    record: dict[str, Any] = {
        "project_id": project_id,
        "run": str(run_dir),
        "status": "running",
        "stage": "",
        "started_at": utcnow().isoformat(),
        "finished_at": None,
        "exit_code": None,
        "error": "",
        "export_name": export_name,
        "started_by": actor,
        "log": deque(maxlen=LOG_LINES),
        "pid": None,
    }

    with _LOCK:
        _current = record

    def _run_and_clean() -> None:
        try:
            _worker(argv, env, record)
        finally:
            export_path.unlink(missing_ok=True)

    # Daemon: a run in flight must not stop the server shutting down. The
    # artifacts written so far stay on disk and the page names what is
    # missing, which is the same state a half-finished run leaves anyway.
    threading.Thread(target=_run_and_clean, name=f"tracelink-{_slug(project_id)}",
                     daemon=True).start()

    log.info("traceability run started for %s -> %s", project_id, run_dir)
    out = dict(record)
    out["log"] = []
    return out
