"""Send an email via the LOCAL Outlook desktop app — no credentials, no SMTP.

Uses the already-signed-in Outlook via COM automation (pywin32), so a corporate
Windows user gets working email reminders with zero setup: the mail is created
and sent from their own Outlook profile. Best-effort — returns ``(ok, message)``
and never raises, so a notification failure can't break a scheduled task.

Falls back with a clear message when Outlook / pywin32 isn't available (e.g. a
non-Windows machine or Outlook not installed), so the caller can surface it.
"""
from __future__ import annotations

from typing import Tuple

_OL_MAIL_ITEM = 0   # Outlook.OlItemType.olMailItem


def available() -> bool:
    """True when the local-Outlook send path can even be attempted."""
    try:
        import win32com.client  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def send_via_outlook(to: str, subject: str, body: str) -> Tuple[bool, str]:
    """Send an email through the local Outlook desktop app. ``to`` may be a
    comma/semicolon-separated list of addresses. Returns ``(ok, message)``."""
    to = (to or "").strip()
    if not to:
        return False, "No recipient address for the Outlook reminder."
    try:
        import pythoncom  # part of pywin32
        import win32com.client
    except Exception as exc:  # noqa: BLE001
        return False, (f"Local Outlook is not available ({exc}). Install Outlook "
                       "desktop (and pywin32) or use the Teams channel instead.")
    # COM must be initialized on the calling (worker) thread.
    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001 — already initialized is fine
        pass
    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        mail = outlook.CreateItem(_OL_MAIL_ITEM)
        mail.To = to.replace(";", ",")
        mail.Subject = subject or "(reminder)"
        mail.Body = body or ""
        mail.Send()
        return True, "Sent via Outlook."
    except Exception as exc:  # noqa: BLE001 — never raise into the scheduler
        return False, f"Outlook send failed: {exc}"
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass
