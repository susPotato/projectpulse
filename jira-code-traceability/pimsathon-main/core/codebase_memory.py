"""Integration with codebase-memory-mcp (https://github.com/DeusData/codebase-memory-mcp).

The Code agent gains "codebase memory" — a persistent knowledge graph of the
working directory (functions, classes, call chains, routes). We bridge to the
single static binary through its documented CLI mode::

    codebase-memory-mcp cli --raw <tool> '<json-args>'

so there is no long-lived MCP process to manage. Indexing and queries run
locally; nothing leaves the machine.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..providers.base import ToolSpec

BINARY_NAME = "codebase-memory-mcp"
_QUERY_TIMEOUT = 90
_INDEX_TIMEOUT = 900


class CodebaseMemoryError(RuntimeError):
    pass


def resolve_binary(configured: str = "") -> Optional[str]:
    """Return a usable binary path, or None if not installed."""
    if configured:
        p = Path(configured).expanduser()
        if p.exists():
            return str(p)
    return shutil.which(BINARY_NAME)


def pip_install() -> Tuple[bool, str]:
    """Install the codebase-memory-mcp package into the current environment."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", BINARY_NAME],
            capture_output=True, text=True, timeout=900,
        )
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if proc.returncode == 0:
        return True, "Installed codebase-memory-mcp."
    return False, (proc.stderr or proc.stdout or "pip install failed")[-400:]


def _extract_json(text: str):
    """Parse JSON from CLI output that may contain log lines before/after it."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    starts = [p for p in (text.find("{"), text.find("[")) if p >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in (text.rfind("}"), text.rfind("]")):
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None


class CodebaseMemory:
    def __init__(self, binary_path: str = ""):
        self.binary = resolve_binary(binary_path)

    @property
    def available(self) -> bool:
        return self.binary is not None

    def _run(self, tool: str, args: Dict[str, Any], timeout: int) -> Dict[str, Any]:
        if not self.binary:
            raise CodebaseMemoryError(
                "codebase-memory-mcp is not installed. See the instructions in Settings."
            )
        # Note: no '--raw' flag — some binary versions reject it ("unknown tool: --raw").
        cmd = [self.binary, "cli", tool, json.dumps(args, ensure_ascii=False)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise CodebaseMemoryError(f"'{tool}' timed out ({timeout}s).")
        except OSError as exc:
            raise CodebaseMemoryError(f"Could not run the binary: {exc}")
        out = (proc.stdout or "").strip()
        # The binary may print download/init logs around the JSON — extract it.
        parsed = _extract_json(out)
        if parsed is not None:
            return parsed
        if proc.returncode != 0:
            detail = (proc.stderr or out or f"{tool} error (exit {proc.returncode})").strip()
            raise CodebaseMemoryError(detail[:300])
        return {"raw": out}

    # ---- high level ops ---------------------------------------------
    def index_repository(self, repo_path: str) -> Dict[str, Any]:
        return self._run("index_repository", {"repo_path": str(repo_path)}, _INDEX_TIMEOUT)

    def list_projects(self) -> Dict[str, Any]:
        return self._run("list_projects", {}, _QUERY_TIMEOUT)

    def call(self, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
        timeout = _INDEX_TIMEOUT if tool == "index_repository" else _QUERY_TIMEOUT
        return self._run(tool, args, timeout)


# Agent-facing tools (namespaced cmem_*). Read-only — never gated by permission.
CMEM_TOOL_SPECS: List[ToolSpec] = [
    ToolSpec(
        name="cmem_get_architecture",
        description="Architecture overview of the indexed codebase: languages, packages, routes, hotspots, clusters.",
        parameters={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="cmem_search_graph",
        description="Find nodes by label/name pattern/file pattern in the knowledge graph (functions, classes, routes...).",
        parameters={
            "type": "object",
            "properties": {
                "name_pattern": {"type": "string", "description": "Name regex, e.g. '.*Handler.*'"},
                "label": {"type": "string", "description": "Function | Class | Route ..."},
                "file_pattern": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    ),
    ToolSpec(
        name="cmem_trace_path",
        description="Trace the call graph: who calls a function and what it calls (BFS, depth 1-5).",
        parameters={
            "type": "object",
            "properties": {
                "function_name": {"type": "string"},
                "direction": {"type": "string", "enum": ["inbound", "outbound", "both"]},
                "depth": {"type": "integer"},
            },
            "required": ["function_name"],
        },
    ),
    ToolSpec(
        name="cmem_get_code_snippet",
        description="Get the source code of a function by its qualified name.",
        parameters={
            "type": "object",
            "properties": {"qualified_name": {"type": "string"}},
            "required": ["qualified_name"],
        },
    ),
    ToolSpec(
        name="cmem_search_code",
        description="Grep-like text search within the project's indexed files.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="cmem_query_graph",
        description="Run a read-only Cypher-like query on the knowledge graph.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
]

CMEM_TOOL_NAMES = {t.name for t in CMEM_TOOL_SPECS}
_CLI_NAME = {t.name: t.name[len("cmem_"):] for t in CMEM_TOOL_SPECS}


def make_executor(mem: CodebaseMemory):
    """Return an executor(name, args) -> {ok, output} for cmem_* tools."""

    def execute(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        cli_tool = _CLI_NAME.get(name)
        if not cli_tool:
            return {"ok": False, "output": f"Unsupported codebase-memory tool: {name}"}
        try:
            result = mem.call(cli_tool, args or {})
        except CodebaseMemoryError as exc:
            return {"ok": False, "output": str(exc)}
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if len(text) > 12000:
            text = text[:12000] + "\n… (truncated)"
        return {"ok": True, "output": text}

    return execute
