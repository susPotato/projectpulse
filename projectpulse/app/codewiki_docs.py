"""Write a missing `docs/` into a registered repository's clone, with CodeWiki.

Registering a repository enforces a lock: the clone must hold a documentation
tree, because the traceability pipeline reads one. Until this module a
repository without one was simply refused. Now Settings asks instead - *this
repository has no docs/, generate them from the code?* - with a model to pick,
and on yes this writes them into the clone's own `docs/` (or whichever path the
registration names) with CodeWiki (`scripts/codewiki_docs.py`), then saves the
registration exactly as if the documents had been there all along.

**Asked, never assumed.** Written documents say what the team intended;
generated ones say what the code does. A trace against the second cannot find
the gap between the two, so it happens only when somebody chose it.

**A background job, not a request.** A generation is tens of minutes of agent
calls. The request starts it and returns; the page polls `state()`. One at a
time per process, for the same reason `traceability_run` allows one run: a
second on the same small machine makes both slow.

**The generated tree survives a refresh.** `tracelink.source.resolve` moves a
clone with `checkout --force`, which leaves untracked files alone, and the
tree is added to the clone's `.git/info/exclude` so it does not make every
later revision read as `dirty`.

**Resumable.** CodeWiki skips every page already written, so a generation that
stopped part way continues where it was when started again.
"""

from __future__ import annotations

import functools
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable

from app.config import REPO_ROOT, settings
from app.models.base import utcnow

log = logging.getLogger(__name__)

FEATURE = "codewiki"
SCRIPT = REPO_ROOT / "scripts" / "codewiki_docs.py"
LOG_LINES = 300

#: Where each vendor's OpenAI-compatible endpoint is, when `/llm` names none.
BASE_URLS = {
    "fpt": "https://token-api.fpt.ai/v1",
    "openai": "https://api.openai.com/v1",
}

#: Tried when the main model fails a call - a different model on the same
#: gateway, so one model's bad minute does not stop a forty-minute run. The
#: first one that is not the main model is used.
FALLBACK_MODELS = {"fpt": ("GLM-5.2", "Qwen3.6-27B")}

_LOCK = threading.Lock()
_current: dict[str, Any] | None = None


class GenerationBusy(RuntimeError):
    """A generation is already in flight; names which project."""


def python() -> str:
    return (settings.codewiki_python or "").strip() or sys.executable


@functools.lru_cache(maxsize=4)
def _importable(interpreter: str) -> str:
    """Why `interpreter` cannot run the generator, or "" when it can."""
    try:
        done = subprocess.run(
            [interpreter, "-c", "import codewiki.cli.adapters.doc_generator"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"cannot start {interpreter}: {exc}"
    if done.returncode != 0:
        last = (done.stderr.strip().splitlines() or ["no output"])[-1]
        return (f"CodeWiki's generator is not installed for {interpreter} "
                f"({last}). Build the image with PULSE_CODEWIKI_GENERATE=1, or "
                f"set PULSE_CODEWIKI_PYTHON to a venv that has it.")
    return ""


def unavailable() -> str:
    """Why docs cannot be generated here, or "" when they can."""
    from app.llm import features

    chosen = features.resolve(FEATURE)
    if not chosen.enabled:
        return "generating documentation is switched off on /llm (CodeWiki docs)."
    if not chosen.has_credential:
        return (f"no {chosen.provider} key is saved for CodeWiki docs. Save one "
                f"on /llm.")
    return _importable(python())


def offer() -> dict[str, Any]:
    """What the "generate docs?" dialog shows: can it, and on which models."""
    from app.llm import features

    chosen = features.resolve(FEATURE)
    spec = features.FEATURES[FEATURE]
    options = [dict(o) for o in spec.model_options.get(chosen.provider, ())]
    if chosen.model and chosen.model not in {o["id"] for o in options}:
        options.insert(0, {"id": chosen.model, "label": chosen.model})
    return {
        "available": not unavailable(),
        "reason": unavailable(),
        "provider": chosen.provider,
        "model": chosen.model,
        "models": options,
    }


def model_env(model: str = "") -> dict[str, str]:
    """What the generator subprocess reads: endpoint, key and models."""
    from app.llm import features
    from app.narration.store import ENV_KEYS

    chosen = features.resolve(FEATURE)
    key = chosen.api_key or next(
        (os.environ[n] for n in ENV_KEYS.get(chosen.provider, ()) if os.environ.get(n)),
        "",
    )
    # Only a base URL saved for *this* feature. The resolution falls back to
    # narration's, which may belong to another vendor entirely.
    saved = features.overrides().get(FEATURE, {})
    main = (model or "").strip() or chosen.model
    return {
        "CODEWIKI_BASE_URL": (str(saved.get("base_url") or "")
                              or BASE_URLS.get(chosen.provider, "")),
        "CODEWIKI_API_KEY": key,
        "CODEWIKI_MODEL": main,
        "CODEWIKI_FALLBACK_MODEL": next(
            (m for m in FALLBACK_MODELS.get(chosen.provider, ()) if m != main), main),
    }


def _exclude(tree: Path, docs_dir: Path) -> None:
    """Keep the generated tree out of `git status`, so the clone stays clean."""
    info = tree / ".git" / "info"
    try:
        rel = docs_dir.resolve().relative_to(tree.resolve()).as_posix()
        info.mkdir(parents=True, exist_ok=True)
        path = info / "exclude"
        line = f"/{rel}/"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if line not in existing.splitlines():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(("" if existing.endswith("\n") or not existing else "\n")
                         + line + "\n")
    except (OSError, ValueError) as exc:
        log.warning("could not exclude %s from git status: %s", docs_dir, exc)


def state(project_id: str = "") -> dict[str, Any] | None:
    """The generation in flight or last finished, optionally for one project."""
    with _LOCK:
        if _current is None or (project_id and _current["project_id"] != project_id):
            return None
        out = dict(_current)
        out["log"] = list(_current["log"])[-40:]
    docs = Path(out["docs_dir"])
    out["pages"] = len(list(docs.glob("*.md"))) if docs.is_dir() else 0
    return out


def start(*, project_id: str, tree: Path, docs_dir: Path, commit: str,
          model: str, actor: str, on_done: Callable[[], dict[str, Any]]
          ) -> dict[str, Any]:
    """Begin generating into `docs_dir`; `on_done` saves the registration."""
    global _current

    with _LOCK:
        if _current is not None and _current["status"] == "running":
            raise GenerationBusy(
                f"documentation is already being generated for "
                f"{_current['project_id']!r}. One at a time - wait for it."
            )
        env = dict(os.environ)
        env.update(model_env(model))
        # CodeWiki's agents write pages with the platform's default encoding,
        # which on a Windows host is cp1252 - measured: two of four pages came
        # out as mojibake. The image is UTF-8 already; this makes a host so.
        env["PYTHONUTF8"] = "1"
        record: dict[str, Any] = {
            "project_id": project_id,
            "status": "running",
            "model": env["CODEWIKI_MODEL"],
            "docs_dir": str(docs_dir),
            "commit": commit,
            "started_by": actor,
            "started_at": utcnow().isoformat(),
            "finished_at": None,
            "error": "",
            "registered": None,
            "log": deque(maxlen=LOG_LINES),
        }
        _current = record

    argv = [python(), str(SCRIPT), "--repo", str(tree), "--out", str(docs_dir),
            "--commit", commit or ""]

    def _finish(status: str, error: str = "") -> None:
        with _LOCK:
            record["status"] = status
            record["error"] = error
            record["finished_at"] = utcnow().isoformat()

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                argv, cwd=str(REPO_ROOT), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", bufsize=1,
            )
        except OSError as exc:
            _finish("failed", f"could not start CodeWiki: {exc}")
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            with _LOCK:
                record["log"].append(line.rstrip("\n"))
        code = proc.wait()
        if code != 0:
            _finish("failed", f"CodeWiki exited {code}. The pages written so far "
                              f"are kept; generating again resumes from them.")
            return
        _exclude(tree, docs_dir)
        try:
            registered = on_done()
        except Exception as exc:  # noqa: BLE001 - reported on the page
            log.exception("saving the registration after generation failed")
            _finish("failed", f"the documents were written but the repository "
                              f"could not be registered: {exc}")
            return
        with _LOCK:
            record["registered"] = registered
        _finish("done")

    threading.Thread(target=_run, name=f"codewiki-{project_id}", daemon=True).start()
    log.info("codewiki generation started for %s -> %s", project_id, docs_dir)
    return state(project_id) or {}
