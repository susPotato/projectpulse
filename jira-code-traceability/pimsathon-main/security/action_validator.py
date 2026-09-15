"""Action validator — classifies agent actions and enforces policy."""
from __future__ import annotations

from .command_risk_classifier import classify_action


ACTION_DENIED_MESSAGE = "Action denied by security policy."


def validate_action(action_type: str, action_details: dict = None) -> bool:
    """Return True if the action is allowed, False if blocked."""
    result = classify_action(action_type, action_details)
    return not result.blocked