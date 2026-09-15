"""Sandboxed file/command tools used by the Code agent.

Every path is resolved relative to the working directory and must stay inside
it (path-traversal is rejected). ``run_command`` executes inside the workdir
with a timeout and captured output.
"""
from __future__ import annotations

import ast
import difflib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..providers.base import ToolSpec

CancelFn = Callable[[], bool]

MAX_READ_BYTES = 200_000
COMMAND_TIMEOUT = 120  # seconds


class ToolError(Exception):
    pass


def _flatten_rel(rel: str) -> str:
    """Collapse a sub-folder path down to a bare filename so the file lands in the
    workdir root — EXCEPT the ``.scratch`` sandbox subtree, which is preserved.

    Used by the Cowork agent (flatten_writes=True) so it can never create a
    per-session / per-chat / per-task output sub-folder: every deliverable stays
    directly in the single configured Output folder."""
    parts = Path(rel).parts
    if parts and parts[0] == ".scratch":
        return rel  # temporary sandbox is allowed (and cleaned up afterwards)
    return Path(rel).name or rel


@dataclass
class ToolContext:
    workdir: Path
    flatten_writes: bool = False  # Cowork: force every write into the workdir root
    sandbox: bool = False  # Code tab: isolate run_command/install_package into <workdir>/.venv
    # Sandbox Security Layer — Settings' "Resource Limits" (cpu_percent/memory_mb/
    # disk_mb), applied to every run_command/install_package this context runs.
    # None (default) = no limits, matching pre-existing behavior.
    resource_limits: Optional[Dict[str, float]] = None
    # Sandbox Security Layer — Settings' "Block network for agent commands"
    # (policy-level, see deps.py::network_blocked_env). False (default) =
    # unrestricted, matching pre-existing behavior.
    block_network: bool = False
    # Whether the fetch_url tool may read URLs — SEPARATE from block_network
    # (reading a web page/share link for info is safe; running networked shell
    # commands is the risk). Defaults True; set from agent_security.allow_url_fetch.
    allow_url_fetch: bool = True
    # Jira read connector config (base_url/email/api_token) — None disables the
    # jira_* tools' ability to connect. Populated from config.data["jira"].
    jira: Optional[Dict[str, Any]] = None

    def resolve(self, rel: str) -> Path:
        """Resolve ``rel`` inside the workdir, rejecting escapes."""
        if rel in ("", "."):
            return self.workdir
        candidate = (self.workdir / rel).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise ToolError(f"Invalid path: {rel} ({exc})")
        root = self.workdir.resolve()
        if resolved != root and root not in resolved.parents:
            raise ToolError(
                f"Refused: '{rel}' is outside the working folder ({root})."
            )
        return resolved


# --------------------------------------------------------------------------
# Tool specs advertised to the model
# --------------------------------------------------------------------------
TOOL_SPECS: List[ToolSpec] = [
    ToolSpec(
        name="read_file",
        description="Read the contents of a text file in the working folder.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="list_dir",
        description="List files and subfolders at a path (defaults to the workdir root).",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Relative path, default '.'"}},
        },
    ),
    ToolSpec(
        name="write_file",
        description=("Create a NEW file or fully rewrite one. Creates parent folders if needed. "
                     "For small changes to an existing file, prefer edit_file."),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="edit_file",
        description=("Make a precise in-place edit to an EXISTING file by replacing an exact "
                     "snippet — preferred over write_file for small changes. 'old_string' must "
                     "match the file byte-for-byte (include enough surrounding context to be "
                     "unique). Set 'replace_all' to replace every occurrence."),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path to an existing file"},
                "old_string": {"type": "string", "description": "Exact text to find (with context)"},
                "new_string": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    ),
    ToolSpec(
        name="run_command",
        description="Run a shell command in the working folder and return stdout/stderr.",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Command to run"}},
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="install_package",
        description=("Install a Python package (pip) into the app's environment so the task can "
                     "use it. Use this to add any missing library yourself — never ask the user "
                     "to install libraries by hand."),
        parameters={
            "type": "object",
            "properties": {
                "package": {"type": "string",
                            "description": "pip package spec, e.g. 'requests' or 'pandas==2.2.0'"},
            },
            "required": ["package"],
        },
    ),
    ToolSpec(
        name="fetch_url",
        description=("Fetch a web page or an online document by URL and return its text content. "
                     "Use this whenever the user shares a link or the task needs information from "
                     "the web. Supports normal http(s) pages, direct document links (PDF/Office — "
                     "parsed to text), SharePoint/OneDrive share links, and Jira issue links — a "
                     "pasted Jira URL is read via the connected Jira account automatically (no need "
                     "to ask for the issue key)."),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "The http(s) URL to fetch"}},
            "required": ["url"],
        },
    ),
    ToolSpec(
        name="jira_search",
        description=("Search Jira issues with a JQL query and return a summary list. Use this to "
                     "read/gather info from Jira (e.g. 'project = ABX AND status = \"In Progress\"'). "
                     "Read-only."),
        parameters={
            "type": "object",
            "properties": {
                "jql": {"type": "string", "description": "Jira Query Language expression"},
                "max_results": {"type": "integer", "description": "Max issues to return (default 25)"},
            },
            "required": ["jql"],
        },
    ),
    ToolSpec(
        name="jira_get_issue",
        description="Read one Jira issue's details (summary, status, assignee, description) by key, e.g. ABX-123.",
        parameters={
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Issue key, e.g. ABX-123"}},
            "required": ["key"],
        },
    ),
]

# Actions gated by the permission gate in confirm mode (auto-approved in Auto-run).
WRITE_TOOLS = {"write_file", "edit_file", "run_command", "install_package"}


def enabled_tool_specs(security_config=None) -> List[ToolSpec]:
    """The built-in TOOL_SPECS minus any the admin turned OFF in Monitoring →
    Tools (``config.tools_disabled``). Passing None (or a config without the
    field) returns them all — unchanged from before this governance layer."""
    disabled = set(getattr(security_config, "tools_disabled", None) or [])
    if not disabled:
        return list(TOOL_SPECS)
    return [t for t in TOOL_SPECS if t.name not in disabled]


def combine_tool_sources(*sources):
    """Merge several ``(tools, executor)`` pairs — e.g. codebase-memory tools
    plus ``AppContext.build_mcp_tools`` (which since the MCP upgrade already
    includes MS365 via the built-in server) — into the ONE ``extra_tools``/
    ``extra_executor`` pair ``run_cowork``/``run_code`` accept. A source
    with no tools or no executor is skipped."""
    all_tools: List[ToolSpec] = []
    routing: Dict[str, Callable] = {}
    for tools, executor in sources:
        if not tools or executor is None:
            continue
        for spec in tools:
            all_tools.append(spec)
            routing[spec.name] = executor
    if not all_tools:
        return [], None

    def combined_executor(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        executor = routing.get(name)
        if executor is None:
            return {"ok": False, "output": f"Unknown tool: {name}"}
        return executor(name, args)

    return all_tools, combined_executor


# --------------------------------------------------------------------------
# Preview (for the permission dialog) and execution
# --------------------------------------------------------------------------
def describe_action(ctx: ToolContext, name: str, args: Dict[str, Any]) -> Dict[str, str]:
    """Return a human preview of a proposed tool call."""
    if name == "run_command":
        return {"kind": "command", "title": "Run command", "text": str(args.get("command", ""))}
    if name == "fetch_url":
        return {"kind": "info", "title": "Fetch URL", "text": str(args.get("url", ""))}
    if name == "jira_search":
        return {"kind": "info", "title": "Jira search", "text": str(args.get("jql", ""))}
    if name == "jira_get_issue":
        return {"kind": "info", "title": "Jira read issue", "text": str(args.get("key", ""))}
    if name == "install_package":
        return {"kind": "command", "title": "Install Python package",
                "text": f"pip install {args.get('package', '')}"}
    if name == "write_file":
        path = str(args.get("path", ""))
        new = str(args.get("content", ""))
        old = ""
        try:
            target = ctx.resolve(path)
            if target.exists():
                old = target.read_text(encoding="utf-8", errors="replace")
        except (ToolError, OSError):
            pass
        diff = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )) or f"(new file) {path}\n\n{new[:2000]}"
        verb = "Overwrite" if old else "Create file"
        return {"kind": "diff", "title": f"{verb}: {path}", "text": diff}
    if name == "edit_file":
        path = str(args.get("path", ""))
        old_s = str(args.get("old_string", ""))
        new_s = str(args.get("new_string", ""))
        replace_all = bool(args.get("replace_all", False))
        before = after = ""
        try:
            target = ctx.resolve(path)
            if target.exists():
                before = target.read_text(encoding="utf-8", errors="replace")
        except (ToolError, OSError):
            pass
        if old_s and old_s in before:
            after = before.replace(old_s, new_s) if replace_all else before.replace(old_s, new_s, 1)
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
        if not diff:
            diff = f"Edit: {path}\n- {old_s[:1000]}\n+ {new_s[:1000]}"
        return {"kind": "diff", "title": f"Edit: {path}", "text": diff}
    return {"kind": "info", "title": name, "text": _short_json(args)}


def execute_tool(ctx: ToolContext, name: str, args: Dict[str, Any],
                  cancel: Optional[CancelFn] = None,
                  on_output: Optional[Callable[[str], None]] = None,
                  agent_role: str = "") -> Dict[str, Any]:
    """Run a tool and return ``{"ok": bool, "output": str}``.

    ``cancel`` is only used by the long-running tools (``run_command``,
    ``install_package``) so the Stop button can interrupt a running subprocess
    instead of waiting for it to finish or time out. ``on_output``, likewise
    only used by those two, streams live stdout/stderr lines as they arrive.

    ``agent_role`` tags the resulting audit-log entry (see ``audit_log.py`` /
    ``agent_roles.py``) — every call is recorded there regardless, this only
    labels WHICH agent role made it."""
    from . import audit_log

    try:
        if name == "read_file":
            result = _read_file(ctx, args)
        elif name == "list_dir":
            result = _list_dir(ctx, args)
        elif name == "write_file":
            result = _write_file(ctx, args)
        elif name == "edit_file":
            result = _edit_file(ctx, args)
        elif name == "run_command":
            result = _run_command(ctx, args, cancel, on_output)
        elif name == "install_package":
            result = _install_package(ctx, args, cancel, on_output)
        elif name == "fetch_url":
            result = _fetch_url(ctx, args)
        elif name == "jira_search":
            result = _jira_search(ctx, args)
        elif name == "jira_get_issue":
            result = _jira_get_issue(ctx, args)
        else:
            result = {"ok": False, "output": f"Tool not found: {name}"}
    except ToolError as exc:
        result = {"ok": False, "output": str(exc)}
    except Exception as exc:  # defensive: a tool must never crash the agent
        result = {"ok": False, "output": f"Error running {name}: {exc}"}
    audit_log.record("tool_call", name, bool(result.get("ok")),
                     str(result.get("output", ""))[:500], agent_role=agent_role)
    return result


def _fetch_url(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a URL's text content (web page / online document / SharePoint-
    OneDrive share link) via link_fetch — the same parser task-link attachments
    use. Honors the Sandbox Security Layer's "Block network" policy."""
    url = str(args.get("url", "")).strip()
    if not url:
        return {"ok": False, "output": "fetch_url: 'url' is required."}
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "output": f"fetch_url: not an http(s) URL: {url}"}
    if not ctx.allow_url_fetch:
        return {"ok": False,
                "output": ("fetch_url: URL fetching is turned off in Settings → Security "
                           "(\"Allow the agent to fetch URLs\").")}
    # A pasted Jira issue link on the CONNECTED Jira host is read via the
    # authenticated API (so private issues resolve, not a login page). Public
    # links / any other URL fall through to the normal fetcher below.
    from . import jira_tool
    if jira_tool.is_jira_issue_url(ctx.jira, url):
        return {"ok": True, "output": jira_tool.get_issue_by_url(ctx.jira, url)}
    from .link_fetch import fetch_link_preview

    return {"ok": True, "output": fetch_link_preview(url)}


def _jira_search(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import jira_tool

    out = jira_tool.search(ctx.jira, str(args.get("jql", "")),
                           int(args.get("max_results", 25) or 25))
    return {"ok": not out.lower().startswith(("jira is not configured", "jira search failed")),
            "output": out}


def _jira_get_issue(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    from . import jira_tool

    out = jira_tool.get_issue(ctx.jira, str(args.get("key", "")))
    return {"ok": not out.lower().startswith(("jira is not configured", "could not fetch")),
            "output": out}


def _read_file(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    target = ctx.resolve(str(args.get("path", "")))
    if not target.exists():
        return {"ok": False, "output": f"File not found: {args.get('path')}"}
    data = target.read_bytes()[:MAX_READ_BYTES]
    text = data.decode("utf-8", errors="replace")
    return {"ok": True, "output": text}


def _list_dir(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(args.get("path", ".") or ".")
    target = ctx.resolve(rel)
    # A missing/not-yet-created path is NOT a tool failure — report it as an
    # ordinary result so the agent can create it or pick another path and keep
    # going. Returning ok=False here surfaced a false "tool failed: list_dir" in
    # Co4E flows and could stall a step on a recoverable situation.
    if not target.exists():
        return {"ok": True, "output": f"(path '{rel}' does not exist yet — create it or use another path)"}
    if target.is_file():
        return {"ok": True, "output": f"('{rel}' is a file, not a directory)"}
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        marker = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{marker}")
    return {"ok": True, "output": "\n".join(entries) or "(empty folder)"}


def _check_python_syntax(target: Path, content: str) -> str:
    """Return a short warning if ``content`` is invalid Python, else ''.

    Catches syntax errors the instant a .py file is written/edited — before the
    agent wastes a whole run_command round-trip just to get the same error back
    from a traceback."""
    if target.suffix.lower() not in (".py", ".pyw"):
        return ""
    try:
        ast.parse(content, filename=str(target))
        return ""
    except SyntaxError as exc:
        return f"\n⚠ Syntax error at line {exc.lineno}: {exc.msg} — fix this before running the file."


def _write_file(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = str(args.get("path", ""))
    if ctx.flatten_writes:
        rel = _flatten_rel(rel)
    target = ctx.resolve(rel)
    content = str(args.get("content", ""))
    target.parent.mkdir(parents=True, exist_ok=True)
    # A .xlsx is a binary package — build a REAL workbook from the content
    # (CSV/TSV/Markdown-table/JSON) rather than writing raw text (which corrupts it).
    if target.suffix.lower() in (".xlsx", ".xlsm"):
        from . import xlsx_write
        if xlsx_write.build_xlsx_from_text(target, content):
            return {"ok": True, "path": str(target),
                    "output": f"Wrote spreadsheet {rel} ({target.name})."}
        return {"ok": False, "output": "Could not build the .xlsx (openpyxl unavailable) — "
                "write a .csv instead, or use a generator script."}
    target.write_text(content, encoding="utf-8")
    warning = _check_python_syntax(target, content)
    return {"ok": True, "path": str(target),
            "output": f"Wrote {len(content)} chars to {rel}.{warning}"}


def _edit_file(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    """Replace an exact snippet inside an existing file (precise patch edit)."""
    rel = str(args.get("path", ""))
    if ctx.flatten_writes:
        rel = _flatten_rel(rel)
    target = ctx.resolve(rel)
    if not target.exists():
        return {"ok": False,
                "output": f"File not found: {rel} — use write_file to create it."}
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    replace_all = bool(args.get("replace_all", False))
    if not old:
        return {"ok": False, "output": "old_string is empty — provide the exact text to replace."}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "output": f"Could not read file: {exc}"}
    count = text.count(old)
    if count == 0:
        return {"ok": False, "output": ("old_string not found. Read the file and copy the exact "
                                        "text to replace, including indentation/whitespace.")}
    if count > 1 and not replace_all:
        return {"ok": False, "output": (f"old_string appears {count} times — add surrounding "
                                        "context to make it unique, or set replace_all=true.")}
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    target.write_text(updated, encoding="utf-8")
    n = count if replace_all else 1
    warning = _check_python_syntax(target, updated)
    return {"ok": True,
            "output": f"Edited {args.get('path')} ({n} replacement{'' if n == 1 else 's'}).{warning}"}


def _sandbox_python(ctx: ToolContext, cancel: Optional[CancelFn] = None,
                     on_output: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Lazily create/reuse this ctx's project sandbox venv (Code tab only —
    ``ctx.sandbox``); returns its python path, or None to use the app's own."""
    if not ctx.sandbox:
        return None
    from .deps import ensure_project_venv

    py = ensure_project_venv(ctx.workdir, cancel=cancel, on_output=on_output)
    return str(py) if py else None


def _install_package(ctx: ToolContext, args: Dict[str, Any], cancel: Optional[CancelFn] = None,
                      on_output: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    from .deps import pip_install

    package = str(args.get("package", "")).strip()
    if not package:
        return {"ok": False, "output": "No package specified."}
    python = _sandbox_python(ctx, cancel, on_output)
    ok, detail = pip_install(package, cancel=cancel, on_output=on_output, python=python)
    head = f"Installed {package}." if ok else f"Could not install {package}."
    return {"ok": ok, "output": f"{head}\n{detail}"}


_SNAPSHOT_SKIP = {".git", "__pycache__", "node_modules", ".scratch", ".venv",
                  ".idea", ".mypy_cache", ".pytest_cache"}


def _snapshot(workdir: Path) -> Dict[str, Any]:
    """Map of file path -> (mtime, size) under the workdir (noise dirs skipped)."""
    snap: Dict[str, Any] = {}
    try:
        for dirpath, dirnames, filenames in os.walk(str(workdir)):
            dirnames[:] = [d for d in dirnames if d not in _SNAPSHOT_SKIP]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                    snap[full] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    pass
            if len(snap) > 5000:
                return snap
    except OSError:
        pass
    return snap


def _run_command(ctx: ToolContext, args: Dict[str, Any],
                  cancel: Optional[CancelFn] = None,
                  on_output: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    from .deps import network_blocked_env, run_cancellable, sandbox_env
    from .sandbox_manager import SandboxManager, ExecutionConfig
    from ..security.command_risk_classifier import classify_command

    command = str(args.get("command", "")).strip()
    if not command:
        return {"ok": False, "output": "Empty command."}

    # --- Security validation pipeline ---
    risk = classify_command(command, is_cowork_mode=ctx.flatten_writes)
    if risk.blocked:
        denial = "Command blocked by security policy: " + "; ".join(risk.reasons)
        return {"ok": False, "output": denial}

    # Route through SandboxManager for risk-based isolation
    mgr = SandboxManager(ExecutionConfig(
        enabled=True,
        block_network_by_default=ctx.block_network,
        is_cowork_mode=ctx.flatten_writes,
    ))
    sandbox_result = mgr.run(
        command=command,
        workdir=str(ctx.workdir),
        block_network=ctx.block_network,
        timeout_sec=COMMAND_TIMEOUT,
        cancel=cancel,
    )
    # Sandbox ALWAYS executes (never double-run). Return its result directly.
    if sandbox_result.get("sandbox") == "blocked":
        return {"ok": False, "output": sandbox_result.get("stderr", "Command blocked")}
    out = sandbox_result.get("stdout", "").strip() or "(no output)"
    err = sandbox_result.get("stderr", "")
    rc = sandbox_result.get("returncode", -1)
    if err:
        out = f"{out}\n{err}" if out else err
    return {"ok": sandbox_result.get("ok", False), "output": f"[exit {rc}]\n{out}"}


def _short_json(obj: Any, limit: int = 500) -> str:
    import json
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + " …"
