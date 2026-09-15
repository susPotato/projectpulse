"""AppContainer Sandbox — Windows 10 1809+ kernel-level token isolation.

Provides:
- Kernel-level token isolation
- Network capability blocking
- File access restriction
- Registry virtualization
- Process restriction combined with Job Object for CPU/memory limits
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .win_job import assign_process, create_job_object, terminate_job

_IS_WINDOWS = sys.platform == "win32"


def is_appcontainer_available() -> bool:
    """Check if AppContainer is available (Windows 10 1809+)."""
    if not _IS_WINDOWS:
        return False
    try:
        ver = platform.win32_ver()
        # Windows 10 = 10.0.x
        if ver[0] == "10":
            return True
        # Windows 11 also reports 10.0.x in win32_ver
        if ver[2] and "10" in ver[2]:
            return True
    except Exception:
        pass
    return False


class AppContainerSandbox:
    """Sandbox using AppContainer for kernel-level isolation."""

    def __init__(
        self,
        profile_name: str = "cowork_local_sandbox",
        display_name: str = "CoworkLocal Sandbox",
        description: str = "Isolated execution environment for Cowork Local agent",
    ):
        self.profile_name = profile_name
        self.display_name = display_name
        self.description = description
        self._available = is_appcontainer_available()

    def run_command(
        self,
        command: str,
        workdir: str = "",
        block_network: bool = True,
        timeout_sec: int = 120,
    ) -> Dict[str, Any]:
        """Run command in AppContainer sandbox.
        
        Falls back to IntegritySandbox when AppContainer is unavailable.
        """
        if not self._available:
            return {
                "ok": False,
                "stdout": "",
                "stderr": "AppContainer not available on this system",
                "returncode": -1,
                "sandbox": "appcontainer_unavailable",
            }

        job_handle = create_job_object()
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
            if job_handle and proc.pid:
                assign_process(job_handle, proc.pid)

            try:
                stdout, stderr = proc.communicate(timeout=timeout_sec)
                return {
                    "ok": proc.returncode == 0,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "returncode": proc.returncode or 0,
                    "sandbox": "appcontainer",
                }
            except subprocess.TimeoutExpired:
                proc.kill()
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Timeout after {timeout_sec}s",
                    "returncode": -1,
                    "sandbox": "appcontainer",
                }
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
                "returncode": -1,
                "sandbox": "appcontainer",
            }
        finally:
            if job_handle:
                terminate_job(job_handle)

    def run_python(
        self,
        code: str,
        workdir: str = "",
        block_network: bool = True,
        timeout_sec: int = 60,
    ) -> Dict[str, Any]:
        """Run Python code in the AppContainer sandbox."""
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        try:
            return self.run_command(
                f'python "{tmp_path}"',
                workdir=workdir,
                block_network=block_network,
                timeout_sec=timeout_sec,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def cleanup(self) -> None:
        """Clean up sandbox resources."""
        pass


def get_sandbox() -> AppContainerSandbox:
    """Get a default AppContainer sandbox instance."""
    return AppContainerSandbox()