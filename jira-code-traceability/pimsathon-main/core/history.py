"""Conversation persistence with a per-conversation file model.

Each conversation is stored as one JSON file::

    {"kind": "cowork"|"code", "session_id": str, "title": str,
     "created": ISO8601, "messages": [...canonical...]}

File name: ``<kind>__<session_id>.json`` so the sidebar can group by kind and
sort by recency. History can live locally or in a OneDrive folder (resolved by
``AppConfig.history_dir``).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ..config import HISTORY_DIR


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def derive_title(messages: List[Dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            text = " ".join(m["content"].split())
            return text[:60] + ("…" if len(text) > 60 else "")
    return "(empty)"


def save_conversation(
    directory: Path,
    kind: str,
    session_id: str,
    messages: List[Dict[str, Any]],
    title: str = "",
    created: str = "",
    inputs: List[str] | None = None,
    outputs: List[str] | None = None,
    project_id: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}__{session_id}.json"
    pinned = False  # preserve pin flag + project across autosaves
    prev_project = ""
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            pinned = bool(prev.get("pinned", False))
            prev_project = prev.get("project_id", "")
        except (OSError, json.JSONDecodeError):
            pinned = False
    payload = {
        "kind": kind,
        "session_id": session_id,
        "title": title or derive_title(messages),
        "created": created or datetime.now().isoformat(timespec="seconds"),
        "pinned": pinned,
        # A conversation belongs to a project (Claude-Projects style); legacy
        # files without one fall back to the default project.
        "project_id": project_id or prev_project or "default",
        "inputs": list(inputs or []),
        "outputs": list(outputs or []),
        "messages": messages,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete_conversation(path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass


def rename_conversation(path, new_title: str) -> None:
    data = load_conversation(path)
    data["title"] = new_title
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_pinned(path, pinned: bool) -> None:
    data = load_conversation(path)
    data["pinned"] = bool(pinned)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_conversation(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"kind": "", "title": "(read error)", "messages": []}
    if isinstance(data, list):  # tolerate legacy format
        data = {"kind": "", "title": derive_title(data), "messages": data}
    return data


def _matches_query(query: str, title: str, messages: List[Dict[str, Any]]) -> bool:
    """True if ``query`` (already lowercased) appears in the title or in any
    message's text content — a conversation "matches" by title OR content."""
    if query in (title or "").lower():
        return True
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str) and query in content.lower():
            return True
    return False


def list_conversations(directory: Path = HISTORY_DIR, query: str = "") -> List[Dict[str, Any]]:
    """List saved conversations, most recent first (pinned always on top).

    ``query`` (from the sidebar's search box), when non-empty, keeps only
    conversations whose title OR any message's content contains it
    (case-insensitive) — since every file is already parsed to build the
    metadata below, this search costs no extra I/O over listing alone."""
    if not directory or not directory.exists():
        return []
    q = (query or "").strip().lower()
    items: List[Dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            data = {"kind": "", "title": derive_title(data), "messages": data}
        title = data.get("title", path.stem)
        if q and not _matches_query(q, title, data.get("messages", [])):
            continue
        items.append({
            "path": path,
            "kind": data.get("kind", ""),
            "title": title,
            "created": data.get("created", ""),
            "session_id": data.get("session_id", path.stem),
            "pinned": bool(data.get("pinned", False)),
            "project_id": data.get("project_id", "") or "default",
            "count": len(data.get("messages", [])),
            "mtime": path.stat().st_mtime,
        })
    # pinned first, then most recent
    items.sort(key=lambda d: (not d["pinned"], -d["mtime"]))
    return items
