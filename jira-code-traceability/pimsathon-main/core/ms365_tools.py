"""Expose signed-in Microsoft 365 connectors (Outlook/Teams/OneDrive/
SharePoint/meeting transcripts) as tool specs + one executor callback.

Since the MCP upgrade, agents no longer receive these tools directly:
:func:`build_ms365_tools` now runs INSIDE the built-in MS365 MCP server
subprocess (``mcp_servers/ms365_server.py``), which re-exposes each spec as
an MCP tool — agents see them namespaced ``ms365__<name>`` (e.g.
``ms365__send_mail``) through the same MCP client layer as every external
server, and every call lands in the audit log as ``kind="mcp_call"``.
Returns ``([], None)`` whenever MS365 isn't signed in / no connector is
enabled, so the server exposes no tools until then.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..providers.base import ToolSpec
from . import ms365_graph as graph
from .ms365_auth import Ms365AuthError, get_access_token, signed_in_account

_CONNECTOR_SPECS: Dict[str, List[ToolSpec]] = {
    "outlook": [
        ToolSpec(
            name="ms365_list_mail",
            description="List recent Outlook mail (subject, sender, received time, preview).",
            parameters={"type": "object", "properties": {
                "top": {"type": "integer", "description": "Max messages, default 10"},
                "folder": {"type": "string", "description": "Mail folder, default 'inbox'"},
            }},
        ),
        ToolSpec(
            name="ms365_send_mail",
            description="Send an email from the signed-in Outlook account.",
            parameters={"type": "object", "properties": {
                "to": {"type": "string", "description": "Comma-separated recipient addresses"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            }, "required": ["to", "subject", "body"]},
        ),
        ToolSpec(
            name="ms365_list_calendar_events",
            description="List upcoming Outlook calendar events.",
            parameters={"type": "object", "properties": {
                "top": {"type": "integer", "description": "Max events, default 10"},
            }},
        ),
    ],
    "teams": [
        ToolSpec(
            name="ms365_list_teams",
            description="List the Microsoft Teams the signed-in user has joined.",
            parameters={"type": "object", "properties": {}},
        ),
        ToolSpec(
            name="ms365_list_channels",
            description="List channels of a Microsoft Team.",
            parameters={"type": "object", "properties": {
                "team_id": {"type": "string"},
            }, "required": ["team_id"]},
        ),
        ToolSpec(
            name="ms365_list_channel_messages",
            description="List recent messages in a Teams channel.",
            parameters={"type": "object", "properties": {
                "team_id": {"type": "string"}, "channel_id": {"type": "string"},
                "top": {"type": "integer", "description": "Max messages, default 20"},
            }, "required": ["team_id", "channel_id"]},
        ),
        ToolSpec(
            name="ms365_send_channel_message",
            description="Post a message to a Teams channel.",
            parameters={"type": "object", "properties": {
                "team_id": {"type": "string"}, "channel_id": {"type": "string"},
                "text": {"type": "string"},
            }, "required": ["team_id", "channel_id", "text"]},
        ),
        ToolSpec(
            name="ms365_send_to_connected_teams",
            description=("Send a message to the Teams channel/chat the user connected via a "
                         "pasted link in Settings — no team/channel/chat id needed."),
            parameters={"type": "object", "properties": {
                "text": {"type": "string"},
            }, "required": ["text"]},
        ),
    ],
    "onedrive": [
        ToolSpec(
            name="ms365_list_onedrive_files",
            description="List files/folders in the signed-in user's OneDrive.",
            parameters={"type": "object", "properties": {
                "path": {"type": "string", "description": "Relative folder path, default root"},
            }},
        ),
        ToolSpec(
            name="ms365_read_onedrive_file",
            description="Read a text file's content from OneDrive.",
            parameters={"type": "object", "properties": {
                "path": {"type": "string"},
            }, "required": ["path"]},
        ),
        ToolSpec(
            name="ms365_write_onedrive_file",
            description="Create/overwrite a small text file on OneDrive.",
            parameters={"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"},
            }, "required": ["path", "content"]},
        ),
    ],
    "sharepoint": [
        ToolSpec(
            name="ms365_list_sharepoint_sites",
            description="Search SharePoint sites by keyword.",
            parameters={"type": "object", "properties": {
                "query": {"type": "string"},
            }, "required": ["query"]},
        ),
        ToolSpec(
            name="ms365_list_sharepoint_files",
            description="List files/folders in a SharePoint site's document library.",
            parameters={"type": "object", "properties": {
                "site_id": {"type": "string"}, "path": {"type": "string"},
            }, "required": ["site_id"]},
        ),
    ],
    "meeting_transcript": [
        ToolSpec(
            name="ms365_find_online_meeting",
            description="Find a Teams online meeting by its join URL (to get its meeting id).",
            parameters={"type": "object", "properties": {
                "join_url": {"type": "string"},
            }, "required": ["join_url"]},
        ),
        ToolSpec(
            name="ms365_list_meeting_transcripts",
            description="List available transcripts for a Teams meeting.",
            parameters={"type": "object", "properties": {
                "meeting_id": {"type": "string"},
            }, "required": ["meeting_id"]},
        ),
        ToolSpec(
            name="ms365_get_meeting_transcript",
            description="Fetch the text content of a Teams meeting transcript.",
            parameters={"type": "object", "properties": {
                "meeting_id": {"type": "string"}, "transcript_id": {"type": "string"},
            }, "required": ["meeting_id", "transcript_id"]},
        ),
    ],
}

# Tool names that send/write data somewhere outside this machine — the Code
# tab's permission gate (core/code_agent.py) must treat these exactly like
# write_file/run_command: confirm in "confirm" mode, never advertise in Plan
# mode. Every other ms365 tool is read-only (list/read) and safe to
# auto-approve like the rest of the read-only tool set.
# NOTE: these are the MCP-QUALIFIED names the agent actually sees
# ("<server>__<tool>", server "ms365", prefix stripped by the built-in
# server — see mcp_servers/ms365_server.py), NOT the internal ms365_* names
# the executor below dispatches on.
MS365_WRITE_TOOLS = {
    "ms365__send_mail",
    "ms365__send_channel_message",
    "ms365__send_to_connected_teams",
    "ms365__write_onedrive_file",
}

_MAX_OUTPUT_CHARS = 20_000


def _dump(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    if len(text) > _MAX_OUTPUT_CHARS:
        text = text[:_MAX_OUTPUT_CHARS] + f"\n…(truncated to {_MAX_OUTPUT_CHARS} chars)…"
    return text


def build_ms365_tools(config) -> Tuple[List[ToolSpec], Optional[Callable[[str, dict], dict]]]:
    """Tool specs + executor for whichever MS365 connectors are enabled AND
    signed in. Returns ``([], None)`` when nothing is signed in / enabled, or
    when "Allow external internet access" is off, so the agent never even
    sees these tools — this is the authoritative enforcement of that switch
    (the Settings UI also disables the connector checkboxes live, but this
    check is what actually stops a Graph call from happening even if a
    connector was left ``true`` in a hand-edited or stale config.json)."""
    ms365 = config.ms365
    if not ms365.get("allow_external_internet"):
        return [], None
    tenant_id, client_id = ms365.get("tenant_id", ""), ms365.get("client_id", "")
    if not signed_in_account(tenant_id, client_id):
        return [], None
    connectors = ms365.get("connectors", {})
    specs: List[ToolSpec] = []
    for key, group in _CONNECTOR_SPECS.items():
        if connectors.get(key):
            specs.extend(group)
    if not specs:
        return [], None

    def executor(name: str, args: dict) -> dict:
        args = args or {}
        try:
            token = get_access_token(tenant_id, client_id)
            if name == "ms365_list_mail":
                return {"ok": True, "output": _dump(graph.list_mail(
                    token, args.get("top", 10), args.get("folder", "inbox")))}
            if name == "ms365_send_mail":
                graph.send_mail(token, args["to"], args["subject"], args["body"])
                return {"ok": True, "output": "Mail sent."}
            if name == "ms365_list_calendar_events":
                return {"ok": True,
                        "output": _dump(graph.list_calendar_events(token, args.get("top", 10)))}
            if name == "ms365_list_teams":
                return {"ok": True, "output": _dump(graph.list_teams(token))}
            if name == "ms365_list_channels":
                return {"ok": True, "output": _dump(graph.list_channels(token, args["team_id"]))}
            if name == "ms365_list_channel_messages":
                return {"ok": True, "output": _dump(graph.list_channel_messages(
                    token, args["team_id"], args["channel_id"], args.get("top", 20)))}
            if name == "ms365_send_channel_message":
                graph.send_channel_message(token, args["team_id"], args["channel_id"], args["text"])
                return {"ok": True, "output": "Message posted."}
            if name == "ms365_send_to_connected_teams":
                target = ms365.get("teams_target")
                if not target:
                    return {"ok": False,
                            "output": "No Teams chat/channel connected yet — paste a link in Settings first."}
                if target.get("kind") == "channel":
                    graph.send_channel_message(token, target["team_id"], target["channel_id"], args["text"])
                else:
                    graph.send_chat_message(token, target["chat_id"], args["text"])
                return {"ok": True, "output": "Message sent to the connected Teams chat/channel."}
            if name == "ms365_list_onedrive_files":
                return {"ok": True,
                        "output": _dump(graph.list_onedrive_files(token, args.get("path", "")))}
            if name == "ms365_read_onedrive_file":
                return {"ok": True, "output": graph.read_onedrive_file(token, args["path"])}
            if name == "ms365_write_onedrive_file":
                return {"ok": True, "output": _dump(graph.write_onedrive_file(
                    token, args["path"], args["content"]))}
            if name == "ms365_list_sharepoint_sites":
                return {"ok": True, "output": _dump(graph.list_sharepoint_sites(token, args["query"]))}
            if name == "ms365_list_sharepoint_files":
                return {"ok": True, "output": _dump(graph.list_sharepoint_files(
                    token, args["site_id"], args.get("path", "")))}
            if name == "ms365_find_online_meeting":
                return {"ok": True, "output": _dump(graph.find_online_meeting(token, args["join_url"]))}
            if name == "ms365_list_meeting_transcripts":
                return {"ok": True,
                        "output": _dump(graph.list_meeting_transcripts(token, args["meeting_id"]))}
            if name == "ms365_get_meeting_transcript":
                return {"ok": True, "output": graph.get_meeting_transcript_content(
                    token, args["meeting_id"], args["transcript_id"])}
            return {"ok": False, "output": f"Unknown MS365 tool: {name}"}
        except (Ms365AuthError, graph.Ms365GraphError, KeyError) as exc:
            return {"ok": False, "output": str(exc)}
        except Exception as exc:  # defensive: a tool must never crash the agent
            return {"ok": False, "output": f"Error running {name}: {exc}"}

    return specs, executor
