"""Attachment validator — inspects attached files for security risks."""
from __future__ import annotations

from .command_risk_classifier import classify_attachment

ATTACHMENT_DENIED_MESSAGE = "Attached content failed security validation."


def validate_attachment(path: str, mime_type: str = "") -> bool:
    result = classify_attachment(path, mime_type or None)
    return not result.blocked