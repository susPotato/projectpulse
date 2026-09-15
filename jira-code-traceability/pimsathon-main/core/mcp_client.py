"""Real MCP (Model Context Protocol) client — connects to an EXTERNAL MCP
server (any of the community/official servers: filesystem, github,
brave-search, postgres, ...) over stdio, and exposes its tools through the
SAME ``extra_tools``/``extra_executor`` contract already used by
``ms365_tools.py`` — so ``chat_agent.run_cowork``/``code_agent.run_code``
need ZERO changes to gain MCP tools; they just get merged into the caller's
existing ``extra_tools`` list (see ``cowork_tab.py``).

The ``mcp`` SDK is asyncio-only; the agent loop that calls
``executor(name, args)`` runs synchronously on a background QThread. This
bridges the two by running the MCP session's ENTIRE lifetime on its own
dedicated asyncio event loop in a background thread — the server subprocess
is spawned ONCE per :class:`McpServerConnection`, not per tool call —
dispatching each call via ``asyncio.run_coroutine_threadsafe``.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..providers.base import ToolSpec

# Tool names are namespaced "<server_name>__<tool_name>" so two servers can
# each expose a tool called e.g. "search" without colliding.
_SEP = "__"


class McpServerError(RuntimeError):
    pass


class McpServerConnection:
    """One connection to one external MCP server (one stdio subprocess)."""

    def __init__(self, name: str, command: str, args: Optional[List[str]] = None,
                env: Optional[Dict[str, str]] = None):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = env
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session = None
        self._cm_stack: list = []
        self._ready = threading.Event()
        self._start_error: Optional[str] = None

    # ---- lifecycle -----------------------------------------------------
    def start(self, timeout: float = 15.0) -> None:
        """Spawn the server subprocess and complete the MCP handshake.
        Raises :class:`McpServerError` on failure (bad command, the server
        crashed on startup, the handshake timed out, ...)."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise McpServerError(f"MCP server '{self.name}' did not respond within {timeout}s")
        if self._start_error:
            raise McpServerError(f"MCP server '{self.name}' failed to start: {self._start_error}")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._connect())
        except Exception as exc:  # noqa: BLE001 - reported to start() via _start_error
            self._start_error = str(exc)
            self._ready.set()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(self._aclose())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=self.command, args=self.args, env=self.env)
        stdio_cm = stdio_client(params)
        read, write = await stdio_cm.__aenter__()
        self._cm_stack.append(stdio_cm)
        session_cm = ClientSession(read, write)
        session = await session_cm.__aenter__()
        self._cm_stack.append(session_cm)
        await session.initialize()
        self._session = session

    async def _aclose(self) -> None:
        for cm in reversed(self._cm_stack):
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 - shutdown must never raise into the caller
                pass
        self._cm_stack.clear()

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ---- tools -----------------------------------------------------------
    def list_tool_specs(self) -> List[ToolSpec]:
        """The server's tools, wrapped as :class:`ToolSpec` — the same shape
        ``run_cowork``/``run_code`` already expect for ``extra_tools``."""
        result = self._run_coro(self._session.list_tools())
        specs = []
        for t in result.tools:
            specs.append(ToolSpec(
                name=f"{self.name}{_SEP}{t.name}",
                description=t.description or "",
                parameters=t.inputSchema or {"type": "object", "properties": {}},
            ))
        return specs

    def call_tool(self, qualified_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """``extra_executor``-shaped result: ``{"ok": bool, "output": str}``."""
        tool_name = qualified_name.split(_SEP, 1)[1] if _SEP in qualified_name else qualified_name
        try:
            result = self._run_coro(self._session.call_tool(tool_name, args or {}))
        except Exception as exc:  # noqa: BLE001 - an MCP call must never crash the agent turn
            return {"ok": False, "output": f"MCP call to '{self.name}' failed: {exc}"}
        text_parts = [block.text for block in (getattr(result, "content", None) or [])
                     if getattr(block, "text", None)]
        output = "\n".join(text_parts) or "(no output)"
        ok = not getattr(result, "isError", False)
        return {"ok": ok, "output": output}

    def _run_coro(self, coro):
        if self._loop is None:
            raise McpServerError(f"MCP server '{self.name}' is not connected")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=60)


def build_mcp_tools(servers: List[McpServerConnection]) -> Tuple[List[ToolSpec], Optional[Callable]]:
    """Merge every connected server's tools into ONE ``extra_tools``/
    ``extra_executor`` pair — the exact shape ``ms365_tools.build_ms365_tools``
    already returns, so a caller can concatenate both onto the same list
    (see ``cowork_tab.py``)."""
    tools: List[ToolSpec] = []
    routing: Dict[str, McpServerConnection] = {}
    for server in servers:
        try:
            server_tools = server.list_tool_specs()
        except Exception:  # noqa: BLE001 - one broken server must not take down the others
            continue
        for spec in server_tools:
            tools.append(spec)
            routing[spec.name] = server
    if not tools:
        return [], None

    def executor(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        from . import audit_log

        server = routing.get(name)
        if server is None:
            return {"ok": False, "output": f"Unknown MCP tool: {name}"}
        result = server.call_tool(name, args)
        audit_log.record("mcp_call", name, bool(result.get("ok")),
                         str(result.get("output", ""))[:500])
        return result

    return tools, executor
