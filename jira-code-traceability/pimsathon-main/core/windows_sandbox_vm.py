"""Windows Sandbox VM — high-risk execution in ephemeral VM isolation.

Uses .wsb configuration files to launch Windows Sandbox with:
- Full filesystem isolation
- Optional full network disablement
- Disabled clipboard, printer, audio input, video input, vGPU
- Mounts only approved workspace folder
- Captures stdout, stderr, exit code back to safe output files
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

_IS_WINDOWS = sys.platform == "win32"


def is_windows_sandbox_available() -> bool:
    """Check if Windows Sandbox is available (Win 10/11 Pro/Enterprise with virtualization)."""
    if not _IS_WINDOWS:
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Virtualization",
            0,
            winreg.KEY_READ,
        )
        val, _ = winreg.QueryValueEx(key, "VirtualizationEnabled")
        winreg.CloseKey(key)
        return val == 1
    except Exception:
        pass
    return False


class WindowsSandboxVM:
    """Sandbox using Windows Sandbox VM for critical-risk execution."""

    def run_command(
        self,
        command: str,
        workdir: str = "",
        block_network: bool = True,
        memory_mb: int = 1024,
        timeout_sec: int = 300,
    ) -> Dict[str, Any]:
        """Run command in Windows Sandbox VM.

        Generates a temporary .wsb config, launches the sandbox, runs the
        command inside, captures output, and cleans up.
        """
        if not _IS_WINDOWS:
            return self._error("Windows Sandbox is only available on Windows")

        if not is_windows_sandbox_available():
            return self._error(
                "Windows Sandbox is not available or not enabled on this system"
            )

        # Create output capture files
        stdout_file = Path(tempfile.gettempdir()) / f"wsb_stdout_{os.getpid()}.txt"
        stderr_file = Path(tempfile.gettempdir()) / f"wsb_stderr_{os.getpid()}.txt"
        exit_file = Path(tempfile.gettempdir()) / f"wsb_exit_{os.getpid()}.txt"

        # Escape command for batch
        safe_cmd = command.replace('"', '"^"')

        # Build batch script to capture output
        batch = (
            f'cmd /c ("{safe_cmd}" > "{stdout_file}" 2> "{stderr_file}" && '
            f'echo %errorlevel% > "{exit_file}" || echo %errorlevel% > "{exit_file}")'
        )

        # Build .wsb config
        wsb_content = [
            "<Configuration>",
            f"  <MemoryMB>{memory_mb}</MemoryMB>",
        ]
        if block_network:
            wsb_content.append("  <Networking>Disable</Networking>")
        wsb_content.append("  <Clipboard>Disable</Clipboard>")
        wsb_content.append("  <Printer>Disable</Printer>")
        wsb_content.append("  <AudioInput>Disable</AudioInput>")
        wsb_content.append("  <VideoInput>Disable</VideoInput>")
        wsb_content.append("  <VGpu>Disable</VGpu>")

        if workdir:
            wsb_content.append(f"  <Volume>{workdir}={workdir}</Volume>")

        wsb_content.append(f'  <LogonCommand>')
        wsb_content.append(f'    <Command>{batch}</Command>')
        wsb_content.append(f"  </LogonCommand>")
        wsb_content.append("</Configuration>")

        wsb_path = Path(tempfile.gettempdir()) / f"cowork_sandbox_{os.getpid()}.wsb"

        try:
            wsb_path.write_text("\n".join(wsb_content), encoding="utf-8")

            # Launch Windows Sandbox
            proc = subprocess.Popen(
                [str(wsb_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Wait for the sandbox to complete (it exits when logon command finishes)
            try:
                proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                return {
                    "ok": False,
                    "stdout": "",
                    "stderr": f"Timeout after {timeout_sec}s",
                    "returncode": -1,
                    "sandbox": "windows_sandbox",
                }

            # Read results
            stdout_text = ""
            stderr_text = ""
            returncode = proc.returncode or 0

            if stdout_file.exists():
                stdout_text = stdout_file.read_text(encoding="utf-8", errors="replace")
            if stderr_file.exists():
                stderr_text = stderr_file.read_text(encoding="utf-8", errors="replace")
            if exit_file.exists():
                try:
                    returncode = int(exit_file.read_text().strip())
                except ValueError:
                    pass

            return {
                "ok": returncode == 0,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "returncode": returncode,
                "sandbox": "windows_sandbox",
            }
        except Exception as exc:
            return self._error(str(exc))
        finally:
            # Cleanup temp files
            for f in (wsb_path, stdout_file, stderr_file, exit_file):
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass

    def _error(self, message: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "stdout": "",
            "stderr": message,
            "returncode": -1,
            "sandbox": "windows_sandbox",
        }