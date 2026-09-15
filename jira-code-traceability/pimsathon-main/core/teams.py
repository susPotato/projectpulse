"""Microsoft Teams notifications via Incoming Webhook / Power Automate Workflow.

The user pastes a webhook URL in Settings. We try the common payload formats in
order so it works with both classic Incoming Webhook connectors (MessageCard)
and the newer Workflows (Adaptive Card) URLs.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import requests

from . import tls_trust

_TIMEOUT = 20
ACCENT = "F37021"


class TeamsNotifier:
    def __init__(self, webhook_url: str = "", ca_bundle: str = ""):
        self.webhook_url = (webhook_url or "").strip()
        self.ca_bundle = (ca_bundle or "").strip()

    @property
    def configured(self) -> bool:
        return self.webhook_url.startswith("http")

    def send(
        self,
        title: str,
        text: str,
        facts: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, str]:
        """Post a notification. Returns ``(ok, detail)``."""
        if not self.configured:
            return False, "Teams webhook URL is not configured."

        # Workflows webhooks expect an Adaptive Card; classic connectors expect a
        # MessageCard. Try both, then a plain-text fallback.
        payloads = [
            self._adaptive_card(title, text, facts),
            self._message_card(title, text, facts),
            {"text": f"**{title}**\n\n{text}"},
        ]
        warn = self.url_warning()
        last = ""
        for payload in payloads:
            try:
                resp = self._post(self.webhook_url, payload)
            except requests.RequestException as exc:
                last = f"Teams connection error: {exc}"
                continue
            if resp.status_code < 300:
                return True, "Notification sent to Teams."
            last = self._explain(resp)
        if warn:
            last = f"{last}  {warn}"
        return False, last

    _WEBHOOK_HOSTS = ("logic.azure.com", "webhook.office.com", "office.com", "powerplatform", "powerautomate")

    def url_warning(self) -> str:
        """Return a hint if the configured URL doesn't look like a real webhook."""
        url = self.webhook_url.lower()
        if not any(h in url for h in self._WEBHOOK_HOSTS):
            return ("⚠ This URL doesn't look like a Teams webhook — it should contain "
                    "'logic.azure.com' or 'webhook.office.com'. Copy the FULL HTTP URL from "
                    "Teams → Workflows → 'Post to a channel when a webhook request is received'.")
        if "logic.azure.com" in url and "sig=" not in url:
            return "⚠ The Workflows URL looks incomplete (missing '&sig=...'). Copy the entire URL."
        return ""

    def _post(self, url: str, payload: Dict):
        """POST while preserving the method across redirects.

        ``requests`` downgrades POST→GET on 301/302/303 redirects, and Teams
        webhooks (``*.webhook.office.com``) often 302 to a regional endpoint —
        the GET then fails with 405. We follow redirects manually as POST.
        """
        current = url
        resp = None
        for _ in range(5):
            verify = tls_trust.verify_for(current, self.ca_bundle)
            try:
                resp = requests.post(
                    current,
                    json=payload,
                    timeout=_TIMEOUT,
                    allow_redirects=False,
                    headers={"Content-Type": "application/json"},
                    verify=verify,
                )
            except requests.exceptions.SSLError as exc:
                # Self-signed/internal-CA gateway: capture and pin its exact
                # certificate instead of asking the user to hunt down a .pem
                # file — see core.tls_trust.
                if self.ca_bundle or not tls_trust.looks_like_cert_trust_error(exc):
                    raise
                pinned = tls_trust.capture_and_trust(current)
                if not pinned:
                    raise
                resp = requests.post(
                    current, json=payload, timeout=_TIMEOUT, allow_redirects=False,
                    headers={"Content-Type": "application/json"}, verify=pinned,
                )
            if resp.status_code in (301, 302, 303, 307, 308):
                location = (getattr(resp, "headers", {}) or {}).get("Location")
                if location:
                    current = location
                    continue
            return resp
        return resp

    @staticmethod
    def _explain(resp) -> str:
        code = resp.status_code
        body = (getattr(resp, "text", "") or "")[:200]
        if code == 405:
            return ("Teams returned 405 (Method Not Allowed). The webhook URL is likely the "
                    "wrong type or expired. Recreate it via Teams → Workflows → "
                    "'Post to a channel when a webhook request is received' and paste the new URL.")
        if code in (401, 403):
            return f"Teams returned {code} (forbidden). The webhook may be revoked — recreate the URL."
        if code == 404:
            return "Teams returned 404. The webhook URL does not exist — check it or create a new one."
        return f"Teams error {code}: {body}"

    @staticmethod
    def _message_card(title: str, text: str, facts: Optional[Dict[str, str]]) -> Dict:
        section: Dict = {"activityTitle": title, "text": text}
        if facts:
            section["facts"] = [{"name": k, "value": v} for k, v in facts.items()]
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": ACCENT,
            "summary": title,
            "sections": [section],
        }

    @staticmethod
    def _adaptive_card(title: str, text: str, facts: Optional[Dict[str, str]]) -> Dict:
        body: List[Dict] = [
            {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium"},
            {"type": "TextBlock", "text": text, "wrap": True},
        ]
        if facts:
            body.append({
                "type": "FactSet",
                "facts": [{"title": k, "value": v} for k, v in facts.items()],
            })
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                },
            }],
        }
