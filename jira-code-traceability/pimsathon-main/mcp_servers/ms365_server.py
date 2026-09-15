"""Built-in MCP server for Microsoft 365 — ``python -m
cowork_local.mcp_servers.ms365_server``.

Wraps the existing Graph integration (``core/ms365_tools.build_ms365_tools``
→ ``core/ms365_graph``) as a standard stdio MCP server, so M365 tools reach
agents through the SAME MCP client layer as every external server
(``core/mcp_client.py``): calls are audited as ``kind="mcp_call"``, appear in
Monitoring's MCP Call History, and tool names arrive namespaced as
``ms365__<tool>`` (e.g. ``ms365__send_mail``).

Auth needs nothing new: the MSAL token cache lives in the OS credential
store (``core/ms365_auth.py``), which this subprocess shares with the GUI —
signing in via Settings → "Kết nối Microsoft 365" is enough.

Config is re-read from ``~/.cowork_local/config.json`` on EVERY list/call, so
toggling a connector (or signing out) in Settings applies on the next agent
turn without restarting this server.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Tool names inside this server drop the legacy "ms365_" prefix — the MCP
# client namespaces them "ms365__<name>", and "ms365__ms365_send_mail" would
# be silly. The legacy executor still dispatches by the prefixed name, so we
# strip on the way out and re-add on the way in.
_PREFIX = "ms365_"


def _strip(name: str) -> str:
    return name[len(_PREFIX):] if name.startswith(_PREFIX) else name


def _fresh_tools() -> Tuple[list, Any]:
    """(specs, executor) from a FRESH config read — see module docstring."""
    from cowork_local.config import AppConfig
    from cowork_local.core.ms365_tools import build_ms365_tools

    return build_ms365_tools(AppConfig.load())


def _tool_list() -> List[Dict[str, Any]]:
    """Plain-dict tool descriptions (name/description/inputSchema) — kept
    SDK-type-free so tests can call it without an MCP session."""
    specs, _executor = _fresh_tools()
    return [{"name": _strip(s.name), "description": s.description,
            "inputSchema": s.parameters} for s in specs]


def _dispatch(name: str, args: Dict[str, Any]) -> str:
    """Run one tool through the legacy executor; returns its output text or
    raises RuntimeError (the MCP SDK turns that into an isError result)."""
    _specs, executor = _fresh_tools()
    if executor is None:
        raise RuntimeError(
            "Microsoft 365 is not available: not signed in, no connector enabled, "
            "or external internet access is off (see Settings).")
    result = executor(_PREFIX + _strip(name), args or {})
    output = str(result.get("output", ""))
    if not result.get("ok"):
        raise RuntimeError(output or f"MS365 tool '{name}' failed.")
    return output


def build_server():
    import mcp.types as types
    from mcp.server.lowlevel import Server

    app = Server("ms365")

    @app.list_tools()
    async def list_tools() -> List["types.Tool"]:
        return [types.Tool(**t) for t in _tool_list()]

    @app.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List["types.TextContent"]:
        return [types.TextContent(type="text", text=_dispatch(name, arguments or {}))]

    return app


def main() -> None:
    import anyio
    from mcp.server.stdio import stdio_server

    app = build_server()

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()
