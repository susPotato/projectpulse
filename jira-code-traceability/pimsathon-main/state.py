"""Shared application context passed to the UI widgets."""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Optional, Tuple

from .config import AppConfig


def resolve_agent_default(
    active_provider: str,
    setting_model: str,
    current_model: str,
    model_provider: Optional[str],
    user_override: bool,
) -> Tuple[str, bool]:
    """Decide which model a tab's **Agent** selector should default to.

    Rule: the default always follows Settings (the active provider's configured
    model). A per-tab model the user picked by hand survives only while the active
    provider is unchanged — so a fresh launch, or switching the active provider in
    Settings, snaps every tab back to the Settings model, while a deliberate
    runtime override keeps working until then.

    Returns ``(model, keep_override)`` — ``model`` is the model to select
    (``''`` means "use the provider's own default") and ``keep_override`` says
    whether the user's manual override is still in effect.
    """
    if user_override and model_provider == active_provider and current_model:
        return current_model, True
    return setting_model, False


class AppContext:
    """Holds the live config and small convenience factories."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.started_at = time.time()   # for Monitoring's Sandbox Details "Created"/"Uptime"
        self._mcp_connections: dict = {}  # server name -> McpServerConnection
        self._ext_connections: dict = {}  # connector id -> McpServerConnection (mcp_stdio mode only)
        # Guards the two connection caches above. build_mcp_tools() runs on EVERY
        # chat turn's own AgentWorker thread, so several turns (multiple Cowork
        # tabs, parallel Co4E flows, scheduled tasks) can enter it at once. The
        # cache is populated check-then-create ("conn is None → spawn → store");
        # without this lock two concurrent turns both see None and each spawns a
        # subprocess for the SAME server — one leaks as an orphan and the wrong
        # object may be handed out. The lock makes connection setup atomic; the
        # provider/HTTP path itself is already thread-safe (a fresh provider per
        # call, module-level `requests`, MCP calls multiplexed on the server's
        # own event loop), so concurrent model calls never needed serializing.
        self._conn_lock = threading.Lock()
        self._routing_service = None       # lazy RoutingService (Auto Model Routing)
        self._routing_lock = threading.Lock()
        # The workspace (project) currently selected in the Workspace screen.
        # Per-workspace modes (routing + auto-run) resolve against THIS project
        # so each workspace keeps its own modes. Updated by WorkspaceTab on
        # project switch; "default" is the auto-seeded starter workspace.
        self.active_project_id = "default"

    @property
    def role(self) -> str:
        return "admin"  # no authentication layer, always full access

    # ---- Per-workspace modes (Auto Model Routing + Auto-run) ----------------
    def _current_project(self):
        """The workspace currently selected in the Workspace screen, or None."""
        pid = getattr(self, "active_project_id", "") or ""
        if not pid:
            return None
        from .core.projects import load_project
        return load_project(pid)

    def project_routing_mode(self, surface: str) -> str:
        """Effective Off/Auto/Manual routing mode for a chat ``surface`` in the
        ACTIVE workspace: the workspace's own override wins; otherwise the
        global default (``config.routing_mode_for``). This is what makes each
        workspace keep its own routing mode."""
        project = self._current_project()
        if project is not None:
            mode = (project.routing_modes or {}).get(surface, "")
            if mode in ("off", "auto", "manual"):
                return mode
        return self.config.routing_mode_for(surface)

    def set_project_routing_mode(self, surface: str, mode: str) -> None:
        """Persist a surface's routing mode for the ACTIVE workspace. With no
        workspace selected, falls back to the global setting so behaviour
        outside a project stays global."""
        mode = mode if mode in ("off", "auto", "manual") else "off"
        project = self._current_project()
        if project is None:
            self.config.set_routing_mode_for(surface, mode)
            return
        from .core.projects import save_project
        modes = dict(project.routing_modes or {})
        modes[surface] = mode
        project.routing_modes = modes
        save_project(project)

    def project_confirm_commands(self) -> bool:
        """Whether to CONFIRM before running a command in the ACTIVE workspace
        (True → show the Approve/Reject dialog; False → auto-run). The
        workspace's own ``auto_run`` override wins; otherwise the global
        ``agent_security.cowork_confirm_commands``."""
        project = self._current_project()
        if project is not None and project.auto_run is not None:
            return not bool(project.auto_run)   # auto_run True → no confirm (auto-approve)
        return bool(self.config.agent_security.get("cowork_confirm_commands"))

    def project_auto_run(self) -> bool:
        """Convenience inverse of :meth:`project_confirm_commands` — True means
        commands auto-approve (no confirm dialog) in the active workspace."""
        return not self.project_confirm_commands()

    def set_project_auto_run(self, auto_run: Optional[bool]) -> None:
        """Persist the ACTIVE workspace's auto-run override. ``None`` → follow
        the global setting. With no workspace selected, writes the global
        confirm flag instead (``auto_run True`` ⇒ no confirm)."""
        project = self._current_project()
        if project is None:
            if auto_run is not None:
                self.config.agent_security["cowork_confirm_commands"] = (not auto_run)
                self.save()
            return
        from .core.projects import save_project
        project.auto_run = auto_run
        save_project(project)

    def routing(self):
        """The shared :class:`~cowork_local.core.routing.service.RoutingService`
        for Auto Model Assessment & Routing — created on first use so importing
        state.py never pulls in the routing stack (and its deps) at startup.

        One instance per app: it owns the assessment store + the in-memory
        pending-switch registry, both of which must be shared across every chat
        surface (Cowork / Co4E / AI-Edit) so a switch confirmed on one screen
        and the scores probed by the scheduler are visible everywhere."""
        if self._routing_service is None:
            with self._routing_lock:
                if self._routing_service is None:
                    from .core.routing.service import RoutingService
                    self._routing_service = RoutingService(self)
        return self._routing_service

    def build_active_provider(self):
        """Construct the currently selected provider (called inside workers)."""
        return self.build_provider_for(self.config.active_provider)

    def build_provider_for(self, name: str, model: str | None = None):
        """Construct a provider by key, optionally overriding the model (per-tab
        agent/model selection)."""
        from .providers import build_provider

        name = name or self.config.active_provider
        conf = dict(self.config.provider_conf(name))
        if model:
            conf["model"] = model
        # A self-signed/internal-CA gateway is handled automatically by each
        # provider (see providers.base.Provider._request / core.tls_trust) —
        # this is only an explicit override for advanced/IT-managed setups
        # (COWORK_CA_BUNDLE env var), no longer exposed in Settings.
        conf["ca_bundle"] = self.config.ca_bundle
        return build_provider(name, conf)

    def teams_notifier(self):
        from .core.teams import TeamsNotifier

        return TeamsNotifier(self.config.teams.get("webhook_url", ""), ca_bundle=self.config.ca_bundle)

    def save(self) -> None:
        self.config.save()

    # ---- 🔌 MCP Layer — external MCP servers this app connects to as a client
    def build_mcp_tools(self):
        """``(tools, executor)`` for every enabled, successfully-connected MCP
        server in Settings, PLUS the built-in MS365 server when Microsoft 365
        is signed in with a connector enabled (``_ms365_builtin_connection``),
        PLUS every enabled unified Connector (CAD/CAE/MS365/Other — Settings →
        "Connectors (MCP)", see ``core/ext_connectors.py``). Reuses
        connections across calls/turns (spawning a subprocess per turn would
        be slow and wasteful). A server/connector that fails to connect is
        skipped, not a hard failure for the turn."""
        # Master switch (Monitoring → Tools → Connector): when the admin turns
        # "Connect to external" off, the agent connects to NO external
        # connectors/MCP at all — no subprocesses spawned, no REST calls.
        if not self.config.connect_external:
            return [], None
        from .core.ext_connectors import build_ext_connector_tools
        from .core.mcp_client import McpServerConnection
        from .core.mcp_client import build_mcp_tools as _merge_mcp_tools
        from .core.tools import combine_tool_sources

        # Serialize the check-then-create against the connection caches so
        # concurrent turns share one subprocess per server instead of racing to
        # spawn duplicates (see _conn_lock in __init__). The lock is held while
        # connections are established (a one-time cost per server per app run);
        # once warm, every turn just finds the cached connection and returns.
        with self._conn_lock:
            active = []
            for entry in self.config.mcp_servers:
                if not entry.get("enabled", True):
                    continue
                name = entry.get("name", "")
                command = entry.get("command", "")
                if not name or not command:
                    continue
                conn = self._mcp_connections.get(name)
                if conn is None:
                    conn = McpServerConnection(name, command, entry.get("args") or [],
                                               entry.get("env") or None)
                    try:
                        conn.start()
                    except Exception:  # noqa: BLE001 - one broken server must not block the turn
                        continue
                    self._mcp_connections[name] = conn
                active.append(conn)
            builtin = self._ms365_builtin_connection(skip={c.name for c in active})
            if builtin is not None:
                active.append(builtin)
            mcp_tools, mcp_executor = _merge_mcp_tools(active)

            ext = self.config.ext_connectors
            all_connectors = [*ext.get("cad", []), *ext.get("cae", []),
                              *ext.get("ms365", []), *ext.get("other", [])]
            ext_tools, ext_executor = build_ext_connector_tools(all_connectors, self._ext_connections)

            # Locally-synced OneDrive/SharePoint (no sign-in) — reads/writes the
            # OneDrive-desktop-synced folders directly, gated on ms365.connectors.
            from .core.ms365_local import build_ms365_local_tools
            local_tools, local_executor = build_ms365_local_tools(self.config)

        return combine_tool_sources((mcp_tools, mcp_executor), (ext_tools, ext_executor),
                                    (local_tools, local_executor))

    # ---- built-in MS365 MCP server (mcp_servers/ms365_server.py) ---------
    _MS365_BUILTIN = "ms365"

    def _ms365_available(self) -> bool:
        """Should the built-in MS365 MCP server exist right now? Mirrors the
        gate ``ms365_tools.build_ms365_tools`` enforces internally: external
        internet allowed + at least one connector on + signed in."""
        ms365 = self.config.ms365
        if not ms365.get("allow_external_internet"):
            return False
        if not any((ms365.get("connectors") or {}).values()):
            return False
        from .core.ms365_auth import signed_in_account

        return signed_in_account(ms365.get("tenant_id", ""),
                                 ms365.get("client_id", "")) is not None

    def _ms365_builtin_connection(self, skip=frozenset()):
        """Connection to the built-in MS365 MCP server — spawned on demand,
        stopped again when the user signs out / disables every connector.
        ``skip`` lets a user-configured server named 'ms365' take precedence."""
        import os
        import sys
        from pathlib import Path

        from .core.mcp_client import McpServerConnection

        name = self._MS365_BUILTIN
        if name in skip:
            return None
        if not self._ms365_available():
            stale = self._mcp_connections.pop(name, None)
            if stale is not None:
                try:
                    stale.stop()
                except Exception:  # noqa: BLE001
                    pass
            return None
        conn = self._mcp_connections.get(name)
        if conn is None:
            # The subprocess must import cowork_local even in a from-source run
            # (PYTHONPATH=src) — prepend this package's parent dir explicitly.
            env = dict(os.environ)
            src_root = str(Path(__file__).resolve().parent.parent)
            env["PYTHONPATH"] = (src_root + os.pathsep + env["PYTHONPATH"]
                                 if env.get("PYTHONPATH") else src_root)
            conn = McpServerConnection(
                name, sys.executable,
                ["-m", "cowork_local.mcp_servers.ms365_server"], env)
            try:
                conn.start()
            except Exception:  # noqa: BLE001 - MS365 down must not block the turn
                return None
            self._mcp_connections[name] = conn
        return conn

    def stop_mcp_connections(self) -> None:
        """Terminate every connected MCP server's subprocess (incl. External
        Connectors in mcp_stdio mode) — called on app shutdown so none of
        them linger as orphan processes."""
        from .core.ext_connectors import stop_ext_connections

        with self._conn_lock:
            for conn in self._mcp_connections.values():
                try:
                    conn.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._mcp_connections.clear()
            stop_ext_connections(self._ext_connections)
