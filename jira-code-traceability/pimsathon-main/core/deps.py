"""Runtime dependency helper.

Tasks and document extraction should never ask the user to install support
libraries by hand — when something is missing we try to ``pip install`` it into
the running interpreter automatically. In a packaged (frozen) build pip isn't
available, so callers must still degrade gracefully if this returns None/False.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

CancelFn = Callable[[], bool]

_POLL_SECS = 0.2

_FAILED: set[str] = set()  # packages we already tried and couldn't install

# 🔒 Sandbox Security Layer — Resource Usage: every subprocess spawned via
# run_cancellable registers its pid here for the duration of the run, so the
# Monitoring Dashboard can show live psutil stats without its own tracking.
_active_pids_lock = threading.Lock()
_ACTIVE_PIDS: set[int] = set()


def active_pids() -> List[int]:
    with _active_pids_lock:
        return sorted(_ACTIVE_PIDS)

# Substrings (lower-cased) in pip's output that mark a TRANSIENT failure (flaky
# network) worth silently retrying, as opposed to a deterministic one (bad
# package name, no matching version, syntax error in a requirement) where
# retrying would just waste time and reproduce the same error.
_TRANSIENT_MARKERS = (
    "connection reset", "connection aborted", "connection refused",
    "read timed out", "timed out", "temporary failure", "getaddrinfo failed",
    "could not fetch url", "network is unreachable", "max retries exceeded",
    "remote end closed connection", "econnreset",
)


def _kill_tree(proc: "subprocess.Popen", job_handle: Optional[int] = None) -> None:
    """Kill a subprocess AND any children it spawned (e.g. a shell wrapping the
    real command, or a build tool that forks workers) — plain ``proc.kill()``
    only kills the direct child and would leave the real work running.

    ``job_handle`` (Windows only), when the process was successfully assigned
    to one at spawn time, is tried FIRST — a Job Object catches reparented/
    detached processes that ``taskkill /T``'s PID-tree walk can miss (see
    win_job.py). Falls back to ``taskkill /T`` if there's no job handle."""
    if sys.platform == "win32" and job_handle:
        from .win_job import terminate_job

        if terminate_job(job_handle):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
        else:
            import os
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:  # noqa: BLE001 - killing must never itself raise
        pass
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def run_cancellable(
    args, *, cwd: str | None = None, timeout: float | None = None,
    cancel: Optional[CancelFn] = None, shell: bool = False,
    on_output: Optional[Callable[[str], None]] = None,
    env: Optional[Dict[str, str]] = None,
    limits: Optional[Dict[str, float]] = None,
) -> Tuple[Optional[int], str, bool, bool, bool]:
    """Run a subprocess so the Stop button can actually interrupt it.

    ``subprocess.run(..., timeout=...)`` blocks the calling thread until the
    process exits or the timeout fires — the cooperative cancel flag checked
    elsewhere in the agent loop has no chance to run, so Stop appears to do
    nothing while a command (or ``pip install``) is executing. This polls
    ``cancel()`` every ``_POLL_SECS`` instead and kills the whole process tree
    the moment the user stops, or the timeout is hit.

    ``on_output``, if given, is called with each line of stdout/stderr AS IT
    ARRIVES (not just at the end) so a caller can stream live progress to the
    UI for long-running commands — purely a side channel; the return value is
    unaffected.

    ``limits`` (Sandbox Security Layer — see ``resource_limits.py``), if
    given, is a dict of any subset of ``cpu_percent``/``memory_mb``/
    ``disk_mb``: the process TREE's usage is polled on the same cadence as
    cancel/timeout, and the tree is killed the moment a cap is exceeded.

    On Windows, the process is additionally assigned to a Job Object at spawn
    time (see ``win_job.py``) — a stronger tree-kill than ``taskkill /T``
    alone, since it also catches reparented/detached children. Best-effort:
    a failure to create/assign the job just means the existing taskkill
    fallback is used, same as before this was added.

    Returns ``(returncode, combined_output, cancelled, timed_out,
    resource_exceeded)``; on a failure to even launch the process,
    ``returncode`` is ``None`` and the output holds the launch error."""
    cancel = cancel or (lambda: False)
    popen_kwargs = {} if sys.platform == "win32" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(
            args, shell=shell, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env, **popen_kwargs,
        )
    except OSError as exc:
        return None, str(exc), False, False, False

    with _active_pids_lock:
        _ACTIVE_PIDS.add(proc.pid)
    try:
        return _run_cancellable_body(proc, cancel, timeout, on_output, limits)
    finally:
        with _active_pids_lock:
            _ACTIVE_PIDS.discard(proc.pid)


def _run_cancellable_body(
    proc: "subprocess.Popen", cancel: CancelFn, timeout: Optional[float],
    on_output: Optional[Callable[[str], None]], limits: Optional[Dict[str, float]],
) -> Tuple[Optional[int], str, bool, bool, bool]:
    job_handle = None
    if sys.platform == "win32":
        from .win_job import assign_process, create_job_object

        job_handle = create_job_object()
        if job_handle is not None:
            assign_process(job_handle, proc.pid)

    if limits:
        from .resource_limits import prime_cpu_counter

        prime_cpu_counter(proc.pid)

    collected: Dict[str, list] = {"out": [], "err": []}

    def _read_stream(stream, key: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                collected[key].append(line)
                if on_output is not None:
                    try:
                        on_output(line)
                    except Exception:  # noqa: BLE001 - a UI callback must never kill the tool
                        pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    out_thread = threading.Thread(target=_read_stream, args=(proc.stdout, "out"), daemon=True)
    err_thread = threading.Thread(target=_read_stream, args=(proc.stderr, "err"), daemon=True)
    out_thread.start()
    err_thread.start()

    start = time.monotonic()
    cancelled = timed_out = resource_exceeded = False
    resource_reason = ""
    while out_thread.is_alive() or err_thread.is_alive():
        if cancel():
            cancelled = True
            _kill_tree(proc, job_handle)
            break
        if timeout is not None and (time.monotonic() - start) > timeout:
            timed_out = True
            _kill_tree(proc, job_handle)
            break
        if limits:
            from .resource_limits import check_limits

            resource_reason = check_limits(proc.pid, limits) or ""
            if resource_reason:
                resource_exceeded = True
                _kill_tree(proc, job_handle)
                break
        time.sleep(_POLL_SECS)
    out_thread.join(timeout=5)
    err_thread.join(timeout=5)
    try:
        proc.wait(timeout=5)  # reap so returncode is populated
    except subprocess.TimeoutExpired:
        pass

    out, err = "".join(collected["out"]), "".join(collected["err"])
    combined = out + (("\n[stderr]\n" + err) if err else "")
    if resource_exceeded:
        combined += f"\n[resource limit] {resource_reason}\n"
    return proc.returncode, combined, cancelled, timed_out, resource_exceeded


def network_blocked_env(base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Env vars that make well-behaved HTTP clients refuse to reach the
    network — proxy vars pointed at a black-hole loopback port nothing
    listens on (connection refused instantly, no hang).

    This is a POLICY-level control (Sandbox Security Layer — "Network
    Control"), not a kernel firewall: it stops the vast majority of scripted
    network calls (``requests``/``curl``/``wget``/``npm``/``pip`` all honor
    these standard proxy env vars) without requiring admin rights or a
    bundled driver — a tool that ignores proxy env vars entirely (rare, but
    possible) would still get through. Combine with the Agent Security
    command whitelist for defense in depth."""
    import os

    env = dict(base_env if base_env is not None else os.environ)
    blackhole = "http://127.0.0.1:1"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env[key] = blackhole
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    return env


def _can_pip() -> bool:
    # A PyInstaller/py2exe build has no usable pip; don't attempt installs there.
    return not getattr(sys, "frozen", False)


def ensure_module(module: str, package: str | None = None):
    """Import ``module``, auto-installing ``package`` (pip) first if needed.

    Returns the imported module, or None if it isn't available and can't be
    installed (offline, no pip, frozen build, …)."""
    try:
        return importlib.import_module(module)
    except ImportError:
        pass
    pkg = package or module
    if pkg in _FAILED or not _can_pip():
        return None
    ok, _ = pip_install(pkg)
    if not ok:
        _FAILED.add(pkg)
        return None
    try:
        importlib.invalidate_caches()
        return importlib.import_module(module)
    except ImportError:
        _FAILED.add(pkg)
        return None


def venv_python_path(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def ensure_project_venv(workdir: Path, cancel: Optional[CancelFn] = None,
                         on_output: Optional[Callable[[str], None]] = None) -> Optional[Path]:
    """Create (if missing) and return the interpreter of a per-project sandbox
    virtual environment at ``<workdir>/.venv`` — so packages the Code agent
    installs for one project never leak into another project's runs or into
    the app's own environment. Returns None (caller should fall back to the
    app's own interpreter) when a venv can't be created (offline, no pip, a
    packaged/frozen build, ...) — sandboxing is a nice-to-have, never a hard
    requirement for the agent to keep working."""
    if not _can_pip():
        return None
    venv_dir = workdir / ".venv"
    py = venv_python_path(venv_dir)
    if py.exists():
        return py
    if on_output is not None:
        on_output("[sandbox] creating project virtual environment (.venv)…\n")
    returncode, out, cancelled, _, _ = run_cancellable(
        [sys.executable, "-m", "venv", str(venv_dir)],
        timeout=120, cancel=cancel, on_output=on_output,
    )
    if returncode == 0 and py.exists():
        return py
    if on_output is not None:
        on_output(f"[sandbox] could not create .venv, using the app's own environment ({out.strip()[-300:]})\n")
    return None


def sandbox_env(python_path: Path) -> Dict[str, str]:
    """Env vars that make a subprocess behave as if this venv were activated —
    bare ``python``/``pip`` in a shell command then resolve to the sandbox."""
    import os

    env = dict(os.environ)
    bin_dir = str(Path(python_path).parent)
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(Path(python_path).parent.parent)
    env.pop("PYTHONHOME", None)
    return env


def pip_install(package: str, cancel: Optional[CancelFn] = None,
                 on_output: Optional[Callable[[str], None]] = None,
                 python: Optional[str] = None, retries: int = 2) -> tuple[bool, str]:
    """Install a pip package into ``python`` (default: the app's own interpreter).
    Returns (ok, output).

    Cancellable (see :func:`run_cancellable`) so hitting Stop while a package is
    installing actually kills pip instead of blocking until it finishes.

    A failure that looks like a flaky network blip (connection reset, timeout,
    DNS failure...) is retried automatically up to ``retries`` times with a
    short backoff — a deterministic failure (no matching version, bad package
    name) is NOT retried, since repeating it would just waste time."""
    if not _can_pip():
        return False, "This packaged build can't install packages at runtime."
    exe = python or sys.executable
    attempt = 0
    while True:
        attempt += 1
        returncode, out, cancelled, timed_out, _ = run_cancellable(
            [exe, "-m", "pip", "install", "--disable-pip-version-check", package],
            timeout=600, cancel=cancel, on_output=on_output,
        )
        if returncode is None:
            return False, f"pip failed to run: {out}"
        if cancelled:
            return False, "Installation cancelled by user."
        if timed_out:
            return False, "pip install timed out (600s) and was cancelled."
        if returncode == 0:
            return True, (out.strip()[-4000:] or "(no output)")
        transient = any(marker in out.lower() for marker in _TRANSIENT_MARKERS)
        if not transient or attempt > retries or (cancel and cancel()):
            return False, (out.strip()[-4000:] or "(no output)")
        if on_output is not None:
            on_output(f"\n[retry] transient network error — retrying ({attempt}/{retries})…\n")
        time.sleep(1.5 * attempt)
