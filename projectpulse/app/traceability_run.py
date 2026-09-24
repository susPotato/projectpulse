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
import time
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


def run_dir_for(project_id: str) -> Path:
    """Where this project's run lives. One directory per project, reused.

    Not one per timestamp, and that is load-bearing rather than tidy: the
    verdict cache sits inside the run directory, so a second run of the same
    project reads the first one's answers and pays only for what changed.
    Naming directories by the clock would make every run a first run.
    """
    return runs_root() / _slug(project_id)


def has_run(project_id: str) -> bool:
    """Whether this project has a run to sync against.

    `tickets.json` rather than the directory: `run_dir_for` creates the
    directory before the pipeline writes anything, and a seeded cache with no
    artifacts is not a run. `tracelink_view.available_runs` uses the same file
    as its test for the same reason, so the page and this agree about what
    counts as traced.
    """
    return (run_dir_for(project_id) / "tickets.json").exists()


#: Cleared by a `new` run, in the order a reader would look for them. The
#: verdict cache is deliberately NOT here: it is content-addressed, so an
#: entry whose inputs have changed is unreachable rather than wrong, and
#: deleting it would re-bill every ticket to gain nothing. "Start over" means
#: the conclusions, not the receipts.
ARTIFACT_GLOB = "*.json"


def reset_artifacts(run_dir: Path) -> int:
    """Delete a previous run's artifacts, keeping its cache. Returns the count.

    Without this a `new` run is indistinguishable from a sync: the pipeline
    overwrites the stages it runs, so a stage that is skipped this time - or
    fails - leaves the previous run's file in place, and the page reads the
    two together as one result. That is the "accepted and silently empty"
    failure the upload path already learned to refuse, wearing a different
    hat.
    """
    if not run_dir.is_dir():
        return 0
    removed = 0
    for path in sorted(run_dir.glob(ARTIFACT_GLOB)):
        if path.is_file():
            path.unlink()
            removed += 1
    # `adjudicate.log` is the one non-JSON artifact a run leaves behind.
    stale_log = run_dir / "adjudicate.log"
    if stale_log.is_file():
        stale_log.unlink()
        removed += 1
    return removed


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


#: Whether to run the stages that call a model. Off unless set, because the
#: cost is per ticket and a button nobody warned about is the wrong place to
#: discover that.
#:
#: **Setting it is not the same as agreeing to spend.** `adjudicate` caches
#: every response on disk by the exact content that produced it
#: (`tracelink/adjudicate.py#cache_key`), and the run directory is named after
#: the project rather than the clock - so the second run of a project reads
#: the first one's cache and bills nothing. With no vendor credentials set at
#: all the CLI says so and serves only what is cached, failing uncached calls
#: individually rather than aborting the run. That combination is what makes a
#: live demo of a real run free: the verdicts on screen were genuinely
#: computed, just not today.
VERDICTS_ENV = "PULSE_TRACELINK_VERDICTS"


def verdict_stages_enabled() -> bool:
    return os.environ.get(VERDICTS_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _commands(*, run_dir: Path, export: Path, repo: Path, docs: Path,
              project_id: str, project_filter: str, done_status: str,
              verdicts: bool) -> list[tuple[str, list[str]]]:
    """The commands to run in order, as they would be typed by hand.

    `-m tracelink` rather than a console script: the package is source on the
    path in the image (`PULSE_TRACELINK_HOME=/app`), not an installed
    distribution, so there is no entry point to call.

    A list rather than one command, because the free pipeline and the stages
    that call a model are separate subcommands upstream and should stay that
    way here: `build_plan` deliberately contains no paid stage, and teaching
    it one would put the cost decision inside a function whose whole job is
    "every free stage, in dependency order".
    """
    base = [sys.executable, "-m", "tracelink", "--run", str(run_dir)]
    argv = [*base, "pipeline",
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

    commands = [("pipeline", argv)]
    if verdicts:
        # `--all`, not the default `--limit 12`: a demo that adjudicates the
        # first twelve tickets and reports the rest as unanalysed is a worse
        # answer than the cache already holds. `--docs` is the prose fallback
        # for a ticket whose candidate set comes back empty.
        commands.append(("adjudicate", [*base, "adjudicate", "--all",
                                        "--docs", str(docs)]))
        # Reads the verdicts that were just written; cheap, and it is what
        # turns a feature area into the sentence the page shows.
        commands.append(("explain", [*base, "explain"]))
        # Not a model call: it re-checks every citation against the source, so
        # a cached verdict that quotes a file which has since changed is
        # caught rather than shown.
        commands.append(("verify", [*base, "verify"]))
    return commands


def _model_env(env: dict[str, str]) -> dict[str, str]:
    """The model and credential the verdict stages should use.

    The choice on `/llm` ("Traceability verdicts") wins; with none saved, an
    explicit `TRACELINK_MODEL` in the environment is left alone, and otherwise
    tracelink's own default (Claude) applies. The child reads credentials from
    its environment only, so a key saved on `/llm` has to be handed over here.
    """
    from app.llm import features, keys

    out: dict[str, str] = {}
    chosen = features.resolve("traceability")
    if chosen.source == "override" or not env.get("TRACELINK_MODEL"):
        out["TRACELINK_MODEL"] = (f"fpt:{chosen.model}" if chosen.provider == "fpt"
                                  else chosen.model)
    for provider, var in (("anthropic", "ANTHROPIC_API_KEY"), ("fpt", "FPT_API_KEY")):
        if not env.get(var):
            stored = keys.get(provider)
            if stored:
                out[var] = stored
    return out


def _worker(commands: list[tuple[str, list[str]]], env: dict[str, str],
            record: dict[str, Any]) -> None:
    """Run each command in order, keeping the tail of the output. Never raises.

    Stops at the first failure. The commands are ordered by dependency -
    `adjudicate` reads what `pipeline` wrote - so carrying on past a failure
    would produce a later stage's error about a missing artifact instead of
    the real one, which is the harder of the two to act on.
    """
    for name, argv in commands:
        with _LOCK:
            record["command"] = name
        if not _one(argv, env, record):
            return
    with _LOCK:
        record["status"] = "done"
        record["exit_code"] = 0
        record["finished_at"] = utcnow().isoformat()
        record["stage"] = ""
        record["command"] = ""


def _one(argv: list[str], env: dict[str, str], record: dict[str, Any]) -> bool:
    """One subprocess. Returns whether it succeeded."""
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
        return False

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
    if code == 0:
        return True

    with _LOCK:
        record["exit_code"] = code
        record["status"] = "failed"
        record["finished_at"] = utcnow().isoformat()
        record["stage"] = ""
        record["error"] = (
            f"{record.get('command') or 'the pipeline'} exited {code}. The last "
            f"lines of its output say which stage stopped and what it wanted."
        )
    return False


#: How long a `reuse` takes before it reports done.
#:
#: Nothing is computed in that time - the run is already on disk and this
#: re-reads it. The pause is there so the board clears and comes back rather
#: than flickering, which on a result this dense reads as a glitch rather than
#: as a refresh.
#:
#: **It is not pretending to work, and the page must not say that it is.** The
#: button is labelled Reuse and the progress line says "Reusing the previous
#: run" - a spinner that claimed to be building a corpus would be a different
#: thing entirely. Set `PULSE_TRACELINK_REUSE_SECONDS=0` to remove it.
REUSE_ENV = "PULSE_TRACELINK_REUSE_SECONDS"
REUSE_DEFAULT_SECONDS = 15.0


def reuse_seconds() -> float:
    raw = os.environ.get(REUSE_ENV, "").strip()
    if not raw:
        return REUSE_DEFAULT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        # A typo should not stop the button working; the default is harmless.
        log.warning("%s=%r is not a number, using %s", REUSE_ENV, raw,
                    REUSE_DEFAULT_SECONDS)
        return REUSE_DEFAULT_SECONDS


def start_reuse(*, project_id: str, actor: str = "") -> dict[str, Any]:
    """Re-read the run this project already has. Computes nothing.

    Separate from `start` rather than a mode inside it, because it needs none
    of what a real run needs - no clone, no backlog export, no subprocess -
    and threading four unused arguments through `start` to reach a branch that
    ignores them is how a function stops being readable.

    It still goes through the same record and the same `state()`, so the page
    polls one endpoint and the log tells you afterwards which of the two
    happened.
    """
    global _current

    with _LOCK:
        if _current is not None and _current.get("status") == "running":
            raise RunBusy(
                f"a run is already in flight for "
                f"{_current.get('project_id')!r}. Wait for it, or check its "
                f"progress on this page."
            )

    run_dir = run_dir_for(project_id)
    if not has_run(project_id):
        raise RunUnavailable(
            "this project has no run to reuse. Recalculate produces one."
        )

    delay = reuse_seconds()
    record: dict[str, Any] = {
        "project_id": project_id,
        "run": str(run_dir),
        "status": "running",
        "stage": "Reusing the previous run",
        "started_at": utcnow().isoformat(),
        "finished_at": None,
        "exit_code": None,
        "error": "",
        "export_name": "",
        "started_by": actor,
        "command": "reuse",
        "mode": "reuse",
        "cleared": 0,
        # Nothing was computed, and the record says so rather than leaving the
        # page to infer it from a mode string.
        "computed": False,
        "verdicts": verdict_stages_enabled(),
        "cached_verdicts": len(list((run_dir / "cache").glob("*.json")))
        if (run_dir / "cache").is_dir() else 0,
        "log": deque(maxlen=LOG_LINES),
        "pid": None,
    }
    record["log"].append(
        f"reuse: serving the run already in {run_dir}. Nothing is recomputed; "
        f"press Recalculate to rebuild it from the code and the backlog."
    )

    with _LOCK:
        _current = record

    def _settle() -> None:
        if delay:
            time.sleep(delay)
        with _LOCK:
            record["status"] = "done"
            record["exit_code"] = 0
            record["finished_at"] = utcnow().isoformat()
            record["stage"] = ""
            record["command"] = ""

    threading.Thread(target=_settle, name=f"tracelink-reuse-{_slug(project_id)}",
                     daemon=True).start()

    log.info("traceability reuse for %s -> %s", project_id, run_dir)
    out = dict(record)
    out["log"] = []
    return out


def start(*, project_id: str, repo_tree: Path, docs_dir: Path,
          export_bytes: bytes, export_name: str = "",
          project_filter: str = "", done_status: str = "",
          actor: str = "", mode: str = "sync") -> dict[str, Any]:
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

    run_dir = run_dir_for(project_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    # Before the temp file and before the thread: a `new` run that cleared the
    # old artifacts only once the subprocess reached them would leave the page
    # reading last week's numbers under this run's progress bar.
    cleared = reset_artifacts(run_dir) if mode == "new" else 0

    # Kept for the life of the run rather than a `with` block: the subprocess
    # outlives this call, and deleting the export out from under the stage
    # that is reading it is a race that would look like a corrupt workbook.
    # The export's own suffix: the reader picks the format from it, and a
    # backlog built from synced Jira issues is a CSV, not a workbook.
    suffix = Path(export_name or "").suffix.lower()
    if suffix not in {".xlsx", ".csv"}:
        suffix = ".xlsx"
    handle, raw_path = tempfile.mkstemp(prefix="pulse-export-", suffix=suffix)
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
    env.update(_model_env(env))

    verdicts = verdict_stages_enabled()
    commands = _commands(run_dir=run_dir, export=export_path, repo=repo_tree,
                         docs=docs_dir, project_id=project_id,
                         project_filter=project_filter, done_status=done_status,
                         verdicts=verdicts)


    record: dict[str, Any] = {
        "project_id": project_id,
        "run": str(run_dir),
        "status": "running",
        "stage": "",
        #: Which subcommand is in flight. The page already shows `stage` from
        #: the CLI's own banners; this says which of them it belongs to, so
        #: "adjudicate" is distinguishable from a pipeline stage of the same
        #: name in a log somebody is reading after the fact.
        "command": "",
        "mode": mode,
        #: Which model the verdict stages call (`fpt:<name>` for the gateway),
        #: empty when they are off. Set on `/llm`, "Traceability verdicts".
        "model": env.get("TRACELINK_MODEL", "") if verdicts else "",
        #: This one really does rebuild the artifacts. The page tells the two
        #: apart by this rather than by parsing `mode`, so adding a third mode
        #: later cannot silently make a reuse describe itself as a rebuild.
        "computed": True,
        #: How many of the previous run's artifacts a `new` run removed. Zero
        #: on a sync, and zero on the first trace of a project - which is the
        #: honest way to say "there was nothing here before" without the page
        #: having to guess from a timestamp.
        "cleared": cleared,
        "verdicts": verdicts,
        #: How many cached verdicts were sitting in this run directory before
        #: it started. The honest version of "will this cost anything": a run
        #: over the same tickets with a full cache spends nothing, and a
        #: number here of zero is the warning that it will.
        "cached_verdicts": len(list((run_dir / "cache").glob("*.json")))
        if (run_dir / "cache").is_dir() else 0,
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
            _worker(commands, env, record)
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
