"""Thin Microsoft Graph REST wrapper for the MS365 connectors.

Every function takes a bearer ``token`` (from ``ms365_auth.get_access_token``)
and returns plain dict/list data straight from Graph's JSON — the caller
(``ms365_tools.py``) is responsible for turning that into a tool result.
Raises :class:`Ms365GraphError` on any non-2xx response so callers can
surface the real Graph error message instead of a generic failure.
"""
from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from . import tls_trust

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEOUT = 30


class Ms365GraphError(Exception):
    pass


class TeamsLinkError(Exception):
    pass


def _headers(token: str, extra: Optional[dict] = None) -> Dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if extra:
        h.update(extra)
    return h


def _request(method: str, url: str, token: str, **kwargs) -> requests.Response:
    if not url.startswith("http"):
        url = f"{GRAPH_BASE}{url}"
    headers = _headers(token, kwargs.pop("headers", None))
    # Same TLS auto-recovery every other outbound HTTPS call in this app uses
    # (see core/tls_trust.py): a corporate network that intercepts traffic to
    # the internal AI gateway with a self-signed certificate typically
    # intercepts graph.microsoft.com the same way, so Graph calls need the
    # same trust-on-first-use handling instead of failing outright.
    kwargs["verify"] = tls_trust.verify_for(url, None)
    try:
        resp = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.exceptions.SSLError as exc:
        if not tls_trust.looks_like_cert_trust_error(exc):
            raise
        pinned = tls_trust.capture_and_trust(url)
        if not pinned:
            raise
        kwargs["verify"] = pinned
        resp = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error", {}).get("message", resp.text)
        except ValueError:
            detail = resp.text
        raise Ms365GraphError(f"Graph API error {resp.status_code}: {detail}")
    return resp


def _path_segment(path: str) -> str:
    """Encode a OneDrive/SharePoint relative path for the ``root:/{path}:``
    addressing form Graph uses."""
    return quote(path.strip("/"), safe="/")


# ---- Outlook ---------------------------------------------------------------
def list_mail(token: str, top: int = 10, folder: str = "inbox") -> List[dict]:
    resp = _request("GET", f"/me/mailFolders/{quote(folder)}/messages"
                            f"?$top={int(top)}&$select=subject,from,receivedDateTime,bodyPreview,webLink",
                     token)
    return resp.json().get("value", [])


def send_mail(token: str, to: str, subject: str, body: str) -> None:
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()],
        }
    }
    _request("POST", "/me/sendMail", token, json=payload)


def list_calendar_events(token: str, top: int = 10) -> List[dict]:
    resp = _request("GET", f"/me/events?$top={int(top)}"
                            "&$select=subject,start,end,organizer,location&$orderby=start/dateTime",
                     token)
    return resp.json().get("value", [])


# ---- Teams ------------------------------------------------------------------
def list_teams(token: str) -> List[dict]:
    resp = _request("GET", "/me/joinedTeams", token)
    return resp.json().get("value", [])


def list_channels(token: str, team_id: str) -> List[dict]:
    resp = _request("GET", f"/teams/{quote(team_id)}/channels", token)
    return resp.json().get("value", [])


def list_channel_messages(token: str, team_id: str, channel_id: str, top: int = 20) -> List[dict]:
    resp = _request("GET", f"/teams/{quote(team_id)}/channels/{quote(channel_id)}/messages"
                            f"?$top={int(top)}", token)
    return resp.json().get("value", [])


def send_channel_message(token: str, team_id: str, channel_id: str, text: str) -> None:
    payload = {"body": {"content": text}}
    _request("POST", f"/teams/{quote(team_id)}/channels/{quote(channel_id)}/messages", token,
              json=payload)


def get_channel(token: str, team_id: str, channel_id: str) -> dict:
    resp = _request("GET", f"/teams/{quote(team_id)}/channels/{quote(channel_id)}", token)
    return resp.json()


def get_chat(token: str, chat_id: str) -> dict:
    resp = _request("GET", f"/chats/{quote(chat_id)}", token)
    return resp.json()


def send_chat_message(token: str, chat_id: str, text: str) -> None:
    _request("POST", f"/chats/{quote(chat_id)}/messages", token, json={"body": {"content": text}})


# ---- "paste a Teams link" convenience -----------------------------------
_LINK_THREAD_RE = re.compile(r"/l/(?:channel|chat|message)/([^/?]+)")


def parse_teams_link(url: str) -> Dict[str, str]:
    """Parse a link copied from Teams ("Get link to channel" or a message's
    "Copy link") into a Graph-addressable target:
    ``{"kind": "channel", "team_id": ..., "channel_id": ...}`` or
    ``{"kind": "chat", "chat_id": ...}``."""
    url = (url or "").strip()
    if not url:
        raise TeamsLinkError("Empty link.")
    match = _LINK_THREAD_RE.search(url)
    if not match:
        raise TeamsLinkError(
            "Unrecognized Teams link — paste a channel link ('Get link to channel') "
            "or a chat/message link copied from Teams.")
    thread_id = unquote(match.group(1))
    group_id = (parse_qs(urlparse(url).query).get("groupId") or [""])[0]
    if group_id:
        return {"kind": "channel", "team_id": group_id, "channel_id": thread_id}
    return {"kind": "chat", "chat_id": thread_id}


# ---- OneDrive -----------------------------------------------------------
def list_onedrive_files(token: str, path: str = "") -> List[dict]:
    url = "/me/drive/root/children" if not path else f"/me/drive/root:/{_path_segment(path)}:/children"
    resp = _request("GET", url, token)
    return resp.json().get("value", [])


def read_onedrive_file(token: str, path: str, max_chars: int = 50_000) -> str:
    resp = _request("GET", f"/me/drive/root:/{_path_segment(path)}:/content", token)
    return resp.content.decode("utf-8", errors="replace")[:max_chars]


def write_onedrive_file(token: str, path: str, content: str) -> dict:
    resp = _request("PUT", f"/me/drive/root:/{_path_segment(path)}:/content", token,
                     data=content.encode("utf-8"),
                     headers={"Content-Type": "text/plain"})
    return resp.json()


def _encode_share_url(url: str) -> str:
    """Encode a OneDrive/SharePoint sharing URL into Graph's ``u!<base64url>``
    share-id form (see Microsoft's 'Get access to shared items' docs)."""
    b64 = base64.urlsafe_b64encode(url.strip().encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{b64}"


def read_shared_file(token: str, share_url: str, max_chars: int = 50_000) -> str:
    """Read the content of an item shared via a OneDrive/SharePoint sharing
    LINK (e.g. an admin's "Anyone with the link" rules document) — resolved
    through Graph's ``/shares`` endpoint, so it works for a link into anyone's
    drive, not just the signed-in user's own OneDrive (unlike
    :func:`read_onedrive_file`, which only reads by path in ``/me/drive``)."""
    share_id = _encode_share_url(share_url)
    resp = _request("GET", f"/shares/{share_id}/driveItem/content", token)
    return resp.content.decode("utf-8", errors="replace")[:max_chars]


# ---- SharePoint --------------------------------------------------------
def list_sharepoint_sites(token: str, query: str) -> List[dict]:
    resp = _request("GET", f"/sites?search={quote(query)}", token)
    return resp.json().get("value", [])


def list_sharepoint_files(token: str, site_id: str, path: str = "") -> List[dict]:
    url = (f"/sites/{quote(site_id)}/drive/root/children" if not path
           else f"/sites/{quote(site_id)}/drive/root:/{_path_segment(path)}:/children")
    resp = _request("GET", url, token)
    return resp.json().get("value", [])


# ---- Teams meeting transcripts ------------------------------------------
def find_online_meeting(token: str, join_url: str) -> List[dict]:
    resp = _request("GET", f"/me/onlineMeetings?$filter=JoinWebUrl eq '{quote(join_url, safe='')}'",
                     token)
    return resp.json().get("value", [])


def list_meeting_transcripts(token: str, meeting_id: str) -> List[dict]:
    resp = _request("GET", f"/me/onlineMeetings/{quote(meeting_id)}/transcripts", token)
    return resp.json().get("value", [])


def get_meeting_transcript_content(token: str, meeting_id: str, transcript_id: str,
                                   max_chars: int = 50_000) -> str:
    resp = _request(
        "GET",
        f"/me/onlineMeetings/{quote(meeting_id)}/transcripts/{quote(transcript_id)}/content"
        "?$format=text/vtt",
        token)
    return resp.content.decode("utf-8", errors="replace")[:max_chars]
