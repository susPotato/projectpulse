"""Unified Connectors (MCP) — Settings → "Connectors (MCP)". Categories:
CAD / CAE / MS365 / Other (Other = generic MCP servers, the merged-in old
"MCP Servers" section).

Real native automation for CAD/CAE software (NX Open, CATIA Automation API,
ABAQUS/CAE scripting, ANSYS ACT, ...) requires each vendor's own licensed SDK
installed on the machine — this app does not bundle, install, or emulate any
of those. Instead an Admin points a connector at something that already
exists in their environment:

  - ``mode="mcp_stdio"`` — a real MCP server for that app (in-house or
    third-party), reusing :class:`core.mcp_client.McpServerConnection`
    verbatim (identical to the "MCP Servers" Settings section).
  - ``mode="rest_api"`` — a REST API the app/vendor exposes. Since there is
    no standard schema across NX/CATIA/ANSYS/etc., this exposes ONE generic
    HTTP-request tool per connector, scoped to the connector's own
    ``base_url`` + auth header — the model picks method/path/body, never the
    base URL or credentials.

Both modes funnel into the same ``(tools, executor)`` shape every other tool
source in this app already uses (see ``core/tools.py::combine_tool_sources``
and ``core/mcp_client.py::build_mcp_tools``).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from ..providers.base import ToolSpec

CATEGORIES: Tuple[str, ...] = ("cad", "cae", "ms365", "other")

# Quick-fill presets shown in the Add dialog — just a name/id seed, no
# hardcoded connection details (those are always vendor/site-specific).
PRESETS: Dict[str, List[Dict[str, str]]] = {
    "cad": [
        {"id": "nx", "name": "NX"},
        {"id": "catia_v5", "name": "CATIA V5"},
        {"id": "catia_v6", "name": "CATIA V6"},
        {"id": "solidworks", "name": "SolidWorks"},
        {"id": "autocad", "name": "AutoCAD"},
    ],
    "cae": [
        {"id": "ansa", "name": "ANSA"},
        {"id": "abaqus", "name": "ABAQUS"},
        {"id": "hyperworks", "name": "HyperWorks (Hyper)"},
        {"id": "ansys", "name": "ANSYS"},
    ],
    "ms365": [
        {"id": "ms365", "name": "Microsoft 365"},
        {"id": "onedrive", "name": "OneDrive"},
        {"id": "sharepoint", "name": "SharePoint"},
    ],
    # "Other" = any generic MCP system (what used to be the separate "MCP
    # Servers" section). No name presets — the admin types the server's name.
    "other": [],
}

_SEP = "__"
_SENSITIVE_KEYS = ("api_key",)


def new_connector(category: str, preset_id: str = "", name: str = "") -> Dict[str, Any]:
    """A fresh connector entry (not yet saved) for the Add dialog."""
    return {
        "id": preset_id or name.strip().lower().replace(" ", "_"),
        "name": name,
        "category": category,
        "enabled": False,
        "mode": "mcp_stdio",
        "command": "",
        "args": [],
        "env": {},
        "base_url": "",
        "api_key": "",
        "auth_header": "Authorization",
        "auth_scheme": "Bearer",
    }


def _redact(entry: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(entry)
    for k in _SENSITIVE_KEYS:
        if out.get(k):
            out[k] = "•" * 8
    return out


class RestApiConnector:
    """One REST-API-mode connector — exposes a single generic HTTP-request
    tool scoped to ``base_url``, so the model can call whatever endpoint the
    vendor documents without this app knowing that vendor's API shape."""

    def __init__(self, entry: Dict[str, Any]):
        self.id = entry.get("id") or entry.get("name", "")
        self.display_name = entry.get("name") or self.id
        self.base_url = (entry.get("base_url") or "").rstrip("/") + "/"
        self.api_key = entry.get("api_key") or ""
        self.auth_header = entry.get("auth_header") or "Authorization"
        self.auth_scheme = entry.get("auth_scheme") or "Bearer"

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name=f"{self.id}{_SEP}http_request",
            description=(
                f"Call the {self.display_name} REST API (base URL fixed to "
                f"{self.base_url}, configured in Settings — you only choose "
                "the method/path/body). Use this for any documented "
                f"{self.display_name} HTTP endpoint."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "path": {"type": "string",
                             "description": "Path relative to the connector's base URL, e.g. 'items/123'."},
                    "query": {"type": "object", "description": "Optional query-string parameters."},
                    "json_body": {"type": "object", "description": "Optional JSON request body."},
                },
                "required": ["method", "path"],
            },
        )

    def call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from .tls_trust import request_any_method as tls_request

        method = str(args.get("method", "GET")).upper()
        path = str(args.get("path", "")).lstrip("/")
        url = urljoin(self.base_url, path)
        # urljoin with an absolute-URL `path` would escape base_url entirely —
        # refuse that so a connector can never be redirected off its own host.
        if not url.startswith(self.base_url):
            return {"ok": False, "output": "Refused: path must stay within the connector's base URL."}
        headers = {}
        if self.api_key:
            headers[self.auth_header] = (
                f"{self.auth_scheme} {self.api_key}".strip() if self.auth_scheme else self.api_key
            )
        try:
            # Same TLS auto-recovery the LLM provider calls get (core/tls_trust
            # .py) — a corporate gateway that terminates TLS with its own
            # certificate used to break this outright with SSLCertVerificationError.
            resp = tls_request(
                method, url, headers=headers,
                params=args.get("query") or None,
                json=args.get("json_body") or None,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 - a network error must not crash the turn
            return {"ok": False, "output": f"{self.display_name} request failed: {exc}"}
        ok = 200 <= resp.status_code < 300
        text = resp.text[:4000]
        return {"ok": ok, "output": f"HTTP {resp.status_code}\n{text}"}

    def test_connection(self) -> Tuple[bool, str]:
        from .tls_trust import request as tls_request

        if not self.base_url.strip("/"):
            return False, "No base URL configured."
        headers = {}
        if self.api_key:
            headers[self.auth_header] = (
                f"{self.auth_scheme} {self.api_key}".strip() if self.auth_scheme else self.api_key
            )
        try:
            resp = tls_request("get", self.base_url, headers=headers, timeout=10)
            return True, f"Reached {self.base_url} (HTTP {resp.status_code})"
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not reach {self.base_url}: {exc}"


def build_ext_connector_tools(
    connectors: List[Dict[str, Any]],
    mcp_connection_cache: Optional[Dict[str, Any]] = None,
) -> Tuple[List[ToolSpec], Optional[Callable]]:
    """``(tools, executor)`` for every ENABLED connector across all
    categories. ``mcp_stdio`` entries reuse long-lived
    :class:`~core.mcp_client.McpServerConnection` objects cached in
    ``mcp_connection_cache`` (keyed by connector id) so a subprocess is
    spawned once, not per turn — same convention as
    ``AppContext.build_mcp_tools``. A connector that fails to start/connect
    is skipped, never a hard failure for the turn."""
    from .mcp_client import McpServerConnection
    from .mcp_client import build_mcp_tools as _merge_mcp_tools
    from .tools import combine_tool_sources

    cache = mcp_connection_cache if mcp_connection_cache is not None else {}
    mcp_conns = []
    rest_sources: List[Tuple[List[ToolSpec], Callable]] = []

    for entry in connectors:
        if not entry.get("enabled"):
            continue
        cid = entry.get("id") or entry.get("name", "")
        if not cid:
            continue
        mode = entry.get("mode", "mcp_stdio")
        if mode == "mcp_stdio":
            command = entry.get("command", "")
            if not command:
                continue
            conn = cache.get(cid)
            if conn is None:
                conn = McpServerConnection(cid, command, entry.get("args") or [],
                                           entry.get("env") or None)
                try:
                    conn.start()
                except Exception:  # noqa: BLE001 - one broken connector must not block the turn
                    continue
                cache[cid] = conn
            mcp_conns.append(conn)
        elif mode == "rest_api":
            if not entry.get("base_url"):
                continue
            rc = RestApiConnector(entry)
            spec = rc.tool_spec()

            def _executor(name: str, args: Dict[str, Any], _rc=rc) -> Dict[str, Any]:
                from . import audit_log

                result = _rc.call(args)
                # Logged as "mcp_call" (not a new kind) so REST-API connector
                # activity shows up in Monitoring's existing "MCP Call
                # History" tab alongside mcp_stdio connectors, instead of
                # being invisible outside the catch-all Action Logs tab.
                audit_log.record("mcp_call", name, bool(result.get("ok")),
                                 str(result.get("output", ""))[:500])
                return result

            rest_sources.append(([spec], _executor))

    mcp_tools, mcp_executor = ([], None)
    if mcp_conns:
        mcp_tools, mcp_executor = _merge_mcp_tools(mcp_conns)

    return combine_tool_sources((mcp_tools, mcp_executor), *rest_sources)


def stop_ext_connections(mcp_connection_cache: Dict[str, Any]) -> None:
    """Terminate every cached ``mcp_stdio`` connector subprocess — called on
    app shutdown, mirrors ``AppContext.stop_mcp_connections``."""
    for conn in mcp_connection_cache.values():
        try:
            conn.stop()
        except Exception:  # noqa: BLE001
            pass
    mcp_connection_cache.clear()
