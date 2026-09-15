"""Local-synced Microsoft 365 (OneDrive / SharePoint) access — NO sign-in.

The OneDrive desktop client already authenticates the user and syncs their
OneDrive plus any added SharePoint libraries into local folders. This module
exposes those synced folders as agent tools using plain filesystem I/O, so the
agent can browse / read / write MS365 files with ZERO OAuth / token / tenant —
the OS handles auth + sync. This is the "auto-connect, no SSO" path.

Limitations (by design): only content already synced to the machine is
visible; cloud-only (online-only / not-yet-downloaded) items won't appear;
writes land in the local sync folder and OneDrive uploads them afterwards.

Every path is resolved UNDER a detected OneDrive root and any attempt to escape
it (``..`` / absolute paths outside the root) is refused, so the model can only
reach the user's own synced Microsoft 365 content.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .. import paths
from ..providers.base import ToolSpec

_MAX_READ_CHARS = 500_000
_PREFIX = "ms365_local"


def _roots() -> List[Path]:
    return paths.detect_onedrive_roots()


def _primary_root() -> Optional[Path]:
    return paths.primary_onedrive_root()


def _resolve_under(root: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``root``; refuse anything that escapes it."""
    root_r = root.resolve()
    target = (root_r / (rel or "").lstrip("/\\")).resolve()
    if target != root_r and root_r not in target.parents:
        raise PermissionError("Path escapes the synced Microsoft 365 folder.")
    return target


def _list_dir(base: Path, rel: str) -> dict:
    target = _resolve_under(base, rel)
    if not target.exists():
        raise FileNotFoundError(f"Not found: {rel or '.'}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a folder: {rel}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        rel_path = str(child.relative_to(base)).replace(os.sep, "/")
        entries.append({"name": child.name, "path": rel_path,
                        "type": "folder" if child.is_dir() else "file",
                        "size": child.stat().st_size if child.is_file() else None})
    return {"root": str(base), "path": rel or "", "entries": entries}


def _read_file(base: Path, rel: str) -> str:
    target = _resolve_under(base, rel)
    if not target.is_file():
        raise FileNotFoundError(f"Not a file: {rel}")
    data = target.read_text(encoding="utf-8", errors="replace")
    return data[:_MAX_READ_CHARS]


def _write_file(base: Path, rel: str, content: str) -> dict:
    target = _resolve_under(base, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")
    return {"written": str(target.relative_to(base)).replace(os.sep, "/"),
            "bytes": len(content or "")}


def build_ms365_local_tools(config) -> Tuple[List[ToolSpec], Optional[Callable[[str, dict], dict]]]:
    """``(tools, executor)`` for the locally-synced OneDrive/SharePoint folders.

    Enabled purely by the ``ms365.connectors`` toggles (onedrive / sharepoint)
    — no sign-in. Returns ``([], None)`` when neither is on or no OneDrive
    folder is synced on this machine."""
    ms365 = getattr(config, "ms365", {}) or {}
    conns = ms365.get("connectors", {}) or {}
    want_onedrive = bool(conns.get("onedrive"))
    want_sharepoint = bool(conns.get("sharepoint"))
    if not (want_onedrive or want_sharepoint):
        return [], None
    roots = _roots()
    if not roots:
        return [], None
    primary = roots[0]

    tools: List[ToolSpec] = []
    if want_onedrive:
        tools += [
            ToolSpec(f"{_PREFIX}__onedrive_list",
                     "List files/folders in the locally-synced OneDrive (no sign-in). "
                     "'path' is relative to the OneDrive sync root; empty = the root.",
                     {"type": "object", "properties": {"path": {"type": "string"}}}),
            ToolSpec(f"{_PREFIX}__onedrive_read",
                     "Read a text file from the locally-synced OneDrive. 'path' is "
                     "relative to the OneDrive sync root.",
                     {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}),
            ToolSpec(f"{_PREFIX}__onedrive_write",
                     "Write/overwrite a text file in the locally-synced OneDrive (OneDrive "
                     "uploads it afterwards). 'path' is relative to the OneDrive sync root.",
                     {"type": "object", "properties": {"path": {"type": "string"},
                                                       "content": {"type": "string"}},
                      "required": ["path", "content"]}),
        ]
    if want_sharepoint:
        tools += [
            ToolSpec(f"{_PREFIX}__sharepoint_list",
                     "List locally-synced SharePoint content (no sign-in). Empty 'path' "
                     "lists the synced libraries/folders; drill in with a relative path.",
                     {"type": "object", "properties": {"path": {"type": "string"}}}),
            ToolSpec(f"{_PREFIX}__sharepoint_read",
                     "Read a text file from a locally-synced SharePoint library. 'path' is "
                     "relative to the sync root.",
                     {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}),
        ]

    def executor(name: str, args: dict) -> dict:
        ok = False
        detail = ""
        try:
            if name in (f"{_PREFIX}__onedrive_list", f"{_PREFIX}__sharepoint_list"):
                out = _list_dir(primary, args.get("path", ""))
            elif name in (f"{_PREFIX}__onedrive_read", f"{_PREFIX}__sharepoint_read"):
                out = _read_file(primary, args["path"])
            elif name == f"{_PREFIX}__onedrive_write":
                out = _write_file(primary, args["path"], args.get("content", ""))
            else:
                return {"ok": False, "output": f"Unknown tool: {name}"}
            ok = True
            import json
            result = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            detail = (args.get("path", "") or "/")
            return {"ok": True, "output": result}
        except Exception as exc:  # noqa: BLE001 — surface as a normal tool failure
            detail = str(exc)
            return {"ok": False, "output": f"Local MS365 error: {exc}"}
        finally:
            try:
                from . import audit_log
                audit_log.record("mcp_call", name, ok, detail[:200])
            except Exception:  # noqa: BLE001 — audit must never break a tool call
                pass

    return tools, executor
