"""Sandbox Manager — central strategy manager for risk-based backend selection.

Routes commands through the safest available backend based on:
- Risk level (safe, moderate, high, critical, blocked)
- OS capability
- Policy configuration
- Administrator settings

Backend priority order:
  SAFE       -> direct or integrity_job_wfp
  MODERATE   -> integrity_job_wfp
  HIGH       -> appcontainer
  CRITICAL   -> windows_sandbox or block
  BLOCKED    -> always block
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..security.command_risk_classifier import (
    RiskLevel, RiskResult, classify_command,
)
from ..security.audit_logger import record as audit_record

_IS_WINDOWS = sys.platform == "win32"


@dataclass
class ExecutionConfig:
    """Configuration for sandbox execution."""
    enabled: bool = True
    default_backend: str = "appcontainer"
    allow_direct_fallback: bool = False
    allow_docker_fallback: bool = False
    block_network_by_default: bool = True
    deny_on_unknown_risk: bool = True
    is_cowork_mode: bool = True


class SandboxManager:
    """Central sandbox manager that selects and routes to the right backend."""

    def __init__(self, config: Optional[ExecutionConfig] = None):
        self.config = config or ExecutionConfig()
        self._backends: Dict[str, Any] = {}

    # ---- Backend availability checks ----

    def check_backend_availability(self) -> Dict[str, bool]:
        """Check which backends are available on this system."""
        result: Dict[str, bool] = {
            "direct": True,
            "integrity_job_wfp": _IS_WINDOWS,
        }

        # AppContainer
        try:
            from .appcontainer_sandbox import is_appcontainer_available
            result["appcontainer"] = is_appcontainer_available()
        except Exception:
            result["appcontainer"] = False

        # Windows Sandbox
        try:
            from .windows_sandbox_vm import is_windows_sandbox_available
            result["windows_sandbox"] = is_windows_sandbox_available()
        except Exception:
            result["windows_sandbox"] = False

        result["blocked"] = True  # always available
        return result

    # ---- Backend selection ----

    def select_backend(self, risk_level: RiskLevel, context: Optional[Dict] = None) -> str:
        """Select the sandbox backend based on risk level and policy."""
        availability = self.check_backend_availability()

        if risk_level == RiskLevel.BLOCKED:
            return "blocked"

        routing = {
            RiskLevel.SAFE: ["integrity_job_wfp", "direct"],
            RiskLevel.MODERATE: ["integrity_job_wfp", "direct"],
            RiskLevel.HIGH: ["appcontainer", "integrity_job_wfp"],
            RiskLevel.CRITICAL: ["windows_sandbox", "appcontainer", "blocked"],
        }

        preferred = routing.get(risk_level, ["blocked"])

        for backend in preferred:
            if availability.get(backend):
                return backend

        # Fallback logic
        if self.config.allow_direct_fallback and risk_level != RiskLevel.CRITICAL:
            return "direct"
        return "blocked"

    # ---- Main execution entry point ----

    def run(
        self,
        command: str,
        workdir: str = "",
        context: Optional[Dict] = None,
        block_network: bool = True,
        timeout_sec: int = 120,
        user: str = "",
        project: str = "",
        workspace: str = "",
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Execute a command through the appropriate sandbox backend.

        Flow:
        1. Classify risk
        2. Select backend
        3. Execute through sandbox
        4. Log audit event
        5. Return normalized result
        """
        if not self.config.enabled:
            # Sandbox disabled — use direct execution (legacy path)
            return self._run_direct(command, workdir, block_network, timeout_sec, cancel)

        # Step 1: Classify risk
        risk = classify_command(command, is_cowork_mode=self.config.is_cowork_mode)

        # Step 2: If blocked, deny immediately
        if risk.blocked:
            denial = "Command blocked by security policy: " + "; ".join(risk.reasons)
            audit_record(
                action_type="run_command",
                result_status="denied",
                user=user, project=project, workspace=workspace,
                prompt_category="command_execution",
                risk_score=risk.score,
                backend_selected="blocked",
                command=command,
                working_directory=workdir,
                network_blocked=True,
                denial_reason=denial,
            )
            return {
                "ok": False,
                "stdout": "",
                "stderr": denial,
                "returncode": -1,
                "sandbox": "blocked",
                "risk_level": risk.level.value,
                "risk_score": risk.score,
            }

        # Step 3: Select backend
        effective_network = block_network or self.config.block_network_by_default
        backend = self.select_backend(risk.level, context)

        # Step 4: Execute
        if backend == "blocked":
            denial = f"Risk level '{risk.level.value}' requires stronger isolation than available"
            audit_record(
                action_type="run_command",
                result_status="denied",
                user=user, project=project, workspace=workspace,
                prompt_category="command_execution",
                risk_score=risk.score,
                backend_selected="blocked",
                command=command,
                working_directory=workdir,
                network_blocked=True,
                denial_reason=denial,
            )
            return {
                "ok": False,
                "stdout": "",
                "stderr": denial,
                "returncode": -1,
                "sandbox": "blocked",
                "risk_level": risk.level.value,
                "risk_score": risk.score,
            }

        result = self._execute_with_backend(
            backend, command, workdir, effective_network, timeout_sec, cancel
        )

        # Step 5: Audit log
        audit_record(
            action_type="run_command",
            result_status="executed" if result.get("ok") else "error",
            user=user, project=project, workspace=workspace,
            prompt_category="command_execution",
            risk_score=risk.score,
            backend_selected=backend,
            command=command,
            working_directory=workdir,
            network_blocked=effective_network,
            return_code=result.get("returncode", -1),
        )

        result["risk_level"] = risk.level.value
        result["risk_score"] = risk.score
        return result

    # ---- Backend execution dispatch ----

    def _execute_with_backend(
        self,
        backend: str,
        command: str,
        workdir: str,
        block_network: bool,
        timeout_sec: int,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Dispatch execution to the selected backend."""
        if backend == "direct":
            return self._run_direct(command, workdir, block_network, timeout_sec, cancel)

        if backend == "integrity_job_wfp":
            from .integrity_sandbox import IntegritySandbox
            sb = IntegritySandbox()
            return sb.run_command(
                command, workdir=workdir,
                block_network=block_network, timeout_sec=timeout_sec, cancel=cancel,
            )

        if backend == "appcontainer":
            from .appcontainer_sandbox import get_sandbox
            sb = get_sandbox()
            return sb.run_command(
                command, workdir=workdir,
                block_network=block_network, timeout_sec=timeout_sec,
            )

        if backend == "windows_sandbox":
            from .windows_sandbox_vm import WindowsSandboxVM
            sb = WindowsSandboxVM()
            return sb.run_command(
                command, workdir=workdir,
                block_network=block_network, timeout_sec=timeout_sec,
            )

        return {
            "ok": False,
            "stdout": "",
            "stderr": f"Unknown backend: {backend}",
            "returncode": -1,
            "sandbox": "error",
        }

    def _run_direct(
        self,
        command: str,
        workdir: str,
        block_network: bool,
        timeout_sec: int,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """Direct process execution (least isolated, fallback only).

        When ``cancel`` is given, routes through ``deps.run_cancellable`` so the
        Stop button / Kill Switch can interrupt a running command (it polls
        cancel and kills the whole process tree). When ``cancel`` is None the
        original blocking ``subprocess.run`` path is used unchanged."""
        import subprocess
        import os

        env = os.environ.copy()
        if block_network:
            from .deps import network_blocked_env
            env = network_blocked_env(env)

        if cancel is not None:
            from .deps import run_cancellable
            try:
                rc, output, cancelled, timed_out, resource_exceeded = run_cancellable(
                    command, cwd=workdir or None, timeout=timeout_sec,
                    cancel=cancel, shell=True, env=env,
                )
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "stdout": "", "stderr": str(exc),
                        "returncode": -1, "sandbox": "direct"}
            if cancelled:
                stderr = "Cancelled by user."
            elif timed_out:
                stderr = f"Timeout after {timeout_sec}s"
            elif resource_exceeded:
                stderr = "Resource limit exceeded."
            else:
                stderr = ""
            return {
                "ok": (rc == 0) and not (cancelled or timed_out or resource_exceeded),
                "stdout": output,
                "stderr": stderr,
                "returncode": rc if rc is not None else -1,
                "sandbox": "direct",
            }

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=workdir or None,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_sec,
            )
            return {
                "ok": proc.returncode == 0,
                "stdout": proc.stdout.decode("utf-8", errors="replace"),
                "stderr": proc.stderr.decode("utf-8", errors="replace"),
                "returncode": proc.returncode,
                "sandbox": "direct",
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"Timeout after {timeout_sec}s",
                "returncode": -1,
                "sandbox": "direct",
            }
        except Exception as exc:
            return {
                "ok": False,
                "stdout": "",
                "stderr": str(exc),
                "returncode": -1,
                "sandbox": "direct",
            }