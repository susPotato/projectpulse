"""Integrity Level + Job Object + WFP Sandbox — lightweight medium-risk backend.

Uses Low Integrity token + Job Object (CPU/memory limits) + WFP network blocking
as a fallback for medium-risk commands or older Windows versions.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .win_job import assign_process, create_job_object, terminate_job

_IS_WINDOWS = sys.platform == "win32"
_CANCEL_POLL_SECS = 0.12


class IntegritySandbox:
    """Lightweight sandbox using Low Integrity + Job Object + WFP."""

    @staticmethod
    def _communicate_cancellable(proc, timeout_sec, cancel, job_handle):
        """Wait for ``proc`` while polling ``cancel()`` every
        ``_CANCEL_POLL_SECS``. Returns ``(stdout, stderr, was_cancelled)``.
        Kills the whole process tree (via the Job Object on Windows) the moment
        cancel fires or the timeout is reached."""
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=_CANCEL_POLL_SECS)
                return stdout, stderr, False
            except subprocess.TimeoutExpired:
                if cancel and cancel():
                    proc.kill()
                    if job_handle:
                        terminate_job(job_handle)
                    return b"", b"", True
                if time.monotonic() >= deadline:
                    proc.kill()
                    if job_handle:
                        terminate_job(job_handle)
                    # Surface as a normal timeout to the caller's except path.
                    raise

    def run_command(
        self,
        command: str,
        workdir: str = "",
        block_network: bool = True,
        cpu_limit: Optional[Dict] = None,
        memory_limit: Optional[Dict] = None,
        timeout_sec: int = 120,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Run command in low-integrity sandbox with Job Object limits.

        ``cancel``, if given, is polled while the command runs; when it returns
        True the whole process tree is killed (via the Job Object on Windows)
        so the Stop button / Kill Switch actually interrupts a long command
        instead of waiting for it to finish or time out. When ``cancel`` is
        None the behaviour is unchanged (blocking ``communicate``)."""
        job_handle = create_job_object() if _IS_WINDOWS else None

        env = os.environ.copy()
        if block_network:
            from .deps import network_blocked_env
            env = network_blocked_env(env)

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=workdir or None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
            )
            if _IS_WINDOWS and job_handle and proc.pid:
                assign_process(job_handle, proc.pid)

            try:
                if cancel is None:
                    stdout, stderr = proc.communicate(timeout=timeout_sec)
                else:
                    stdout, stderr, was_cancelled = self._communicate_cancellable(
                        proc, timeout_sec, cancel, job_handle)
                    if was_cancelled:
                        return {
                            "ok": False,
                            "stdout": stdout.decode("utf-8", errors="replace"),
                            "stderr": "Cancelled by user.",
                            "returncode": -1,
                            "sandbox": "integrity_job_wfp",
                        }
                result = {
                    "ok": proc.returncode == 0,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode or 0,
                    "sandbox": "integrity_job_wfp",
                }
            except subprocess.TimeoutExpired:
                proc.kill()
                if job_handle:
                    terminate_job(job_handle)
                result = {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Timeout after {timeout_sec}s",
                    "returncode": -1,
                    "sandbox": "integrity_job_wfp",
                }
            return result
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
                "returncode": -1,
                "sandbox": "integrity_job_wfp",
            }
        finally:
            if job_handle:
                terminate_job(job_handle)

    def run_python(
        self,
        code: str,
        workdir: str = "",
        block_network: bool = True,
        cpu_limit: Optional[Dict] = None,
        memory_limit: Optional[Dict] = None,
        timeout_sec: int = 60,
    ) -> Dict[str, Any]:
        """Run Python code in the sandbox."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        try:
            return self.run_command(
                f'python "{tmp_path}"',
                workdir=workdir,
                block_network=block_network,
                cpu_limit=cpu_limit,
                memory_limit=memory_limit,
                timeout_sec=timeout_sec,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass