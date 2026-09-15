"""Prompt validator — detects and blocks malicious user prompts.

Checks for prompt injection, jailbreak, policy bypass, role override,
system prompt extraction, secret extraction, source code access,
app architecture discovery, agent discovery, MCP discovery.
"""
from __future__ import annotations

from .command_risk_classifier import RiskResult, RiskLevel, classify_prompt


def validate_prompt(prompt: str, is_cowork_mode: bool = True) -> RiskResult:
    """Validate a user prompt before agent processing.
    
    Returns RiskResult with blocked=True if the prompt must be rejected.
    """
    result = classify_prompt(prompt, is_cowork_mode=is_cowork_mode)
    
    if result.blocked:
        result.reasons.insert(0, "Prompt denied by security policy")
    
    return result


PROMPT_DENIED_MESSAGE = "Request denied due to security policy."