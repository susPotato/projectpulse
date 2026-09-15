"""Read-only Jira connector for the agent (jira_search / jira_get_issue).

Talks to Jira Cloud's REST API v2 with Basic auth (email + API token), so no
OAuth/app setup is needed — the user pastes a base URL, their Atlassian email
and an API token (id.atlassian.com → Security → API tokens) once in
Monitoring → Tools. Read-only: it fetches issues/fields, never writes.

Every function returns a human-readable text block (or an explanatory error
string) and never raises, so a Jira hiccup can't break an agent turn.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

_TIMEOUT = (10, 20)
_MAX_RESULTS = 25
_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def _conf(config: Dict[str, Any] | None) -> Dict[str, str]:
    return {k: str((config or {}).get(k, "") or "").strip()
            for k in ("base_url", "email", "api_token")}


def configured(config: Dict[str, Any] | None) -> bool:
    c = _conf(config)
    return bool(c["base_url"] and c["email"] and c["api_token"])


def key_from_url(url: str) -> Optional[str]:
    """Extract an issue key (ABX-123) from a Jira URL — handles /browse/KEY and
    boards/backlog links with ?selectedIssue=KEY. Returns None if none found."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    sel = parse_qs(parsed.query or "").get("selectedIssue")
    if sel:
        m = _KEY_RE.search(sel[0])
        if m:
            return m.group(1)
    m = _KEY_RE.search(parsed.path or "")
    return m.group(1) if m else None


def base_url_from_link(url: str) -> str:
    """Derive the Jira site base URL (scheme://host) from ANY pasted Jira link,
    so the user can paste an issue/board link and the base URL fills itself."""
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return ""
    if p.scheme in ("http", "https") and p.hostname:
        return f"{p.scheme}://{p.hostname}"
    return ""


def is_jira_issue_url(config: Dict[str, Any] | None, url: str) -> bool:
    """True when ``url`` points at an issue on the CONFIGURED Jira host — so a
    pasted link can be resolved through the authenticated API instead of a raw
    (login-walled) HTTP fetch."""
    if not configured(config) or not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        base = (urlparse(_conf(config)["base_url"]).hostname or "").lower()
    except ValueError:
        return False
    return bool(host and base and host == base and key_from_url(url))


def get_issue_by_url(config: Dict[str, Any] | None, url: str) -> str:
    """Read the issue a Jira URL points at, via the authenticated API."""
    key = key_from_url(url)
    if not key:
        return f"[Jira link: {url}] (couldn't find an issue key in the URL)."
    return get_issue(config, key)


def _get(config: Dict[str, Any], path: str, params: dict = None):
    from . import tls_trust

    c = _conf(config)
    url = c["base_url"].rstrip("/") + path
    # Same TLS auto-recovery the LLM provider calls get (core/tls_trust.py) —
    # a corporate gateway that terminates TLS with its own certificate used to
    # break this outright with SSLCertVerificationError.
    resp = tls_trust.request("get", url, params=params or {}, timeout=_TIMEOUT,
                             auth=(c["email"], c["api_token"]),
                             headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def _fmt_issue(it: dict) -> str:
    f = it.get("fields", {}) or {}
    status = (f.get("status") or {}).get("name", "?")
    assignee = (f.get("assignee") or {}).get("displayName", "unassigned")
    prio = (f.get("priority") or {}).get("name", "")
    parts = [f"{it.get('key', '?')} — {f.get('summary', '(no summary)')}",
             f"  status: {status} · assignee: {assignee}" + (f" · priority: {prio}" if prio else "")]
    return "\n".join(parts)


def search(config: Dict[str, Any] | None, jql: str, max_results: int = _MAX_RESULTS) -> str:
    """Search issues by JQL, e.g. ``project = ABX AND status = "In Progress"``."""
    if not configured(config):
        return ("Jira is not configured. Set base URL, email and API token in "
                "Monitoring → Tools → Jira first.")
    if not (jql or "").strip():
        return "jira_search: a JQL query is required."
    try:
        data = _get(config, "/rest/api/2/search",
                    {"jql": jql, "maxResults": max(1, min(max_results, 50)),
                     "fields": "summary,status,assignee,priority"})
    except Exception as exc:  # noqa: BLE001
        return f"Jira search failed: {exc}"
    issues: List[dict] = data.get("issues", []) or []
    if not issues:
        return f"No issues match: {jql}"
    total = data.get("total", len(issues))
    head = f"Found {total} issue(s) for `{jql}` (showing {len(issues)}):\n"
    return head + "\n\n".join(_fmt_issue(it) for it in issues)


def get_issue(config: Dict[str, Any] | None, key: str) -> str:
    """Fetch one issue's key fields + description by key (e.g. ABX-123)."""
    if not configured(config):
        return ("Jira is not configured. Set base URL, email and API token in "
                "Monitoring → Tools → Jira first.")
    key = (key or "").strip()
    if not key:
        return "jira_get_issue: an issue key is required (e.g. ABX-123)."
    try:
        it = _get(config, f"/rest/api/2/issue/{key}",
                  {"fields": "summary,status,assignee,priority,description,labels,updated"})
    except Exception as exc:  # noqa: BLE001
        return f"Could not fetch {key}: {exc}"
    f = it.get("fields", {}) or {}
    desc = f.get("description")
    if isinstance(desc, dict):      # ADF (v3) → not requested here, but be safe
        desc = "(rich-text description — open in Jira)"
    lines = [_fmt_issue(it)]
    if f.get("labels"):
        lines.append(f"  labels: {', '.join(f['labels'])}")
    if f.get("updated"):
        lines.append(f"  updated: {f['updated']}")
    if desc:
        lines.append(f"\n{str(desc)[:4000]}")
    return "\n".join(lines)
