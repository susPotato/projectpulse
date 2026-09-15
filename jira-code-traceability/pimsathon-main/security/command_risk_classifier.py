"""Command risk classifier — scores commands 0-100 and assigns risk level.

This is the first gate in the security validation pipeline. It classifies every
command/tool-call/prompt into a risk bucket so SandboxManager can select the
right isolation backend.

Risk levels:
  safe       (0-30)   Business-safe, read-only, no system impact
  moderate   (31-60)  File writes, trusted internal tools, report generation
  high       (61-85)  Interpreters, untrusted commands, external file access
  critical   (86-100) Unknown binaries, privilege changes, shell expansion,
                      system discovery, source-code access, secret access
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class RiskLevel(str, Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKED = "blocked"


@dataclass
class RiskResult:
    score: int           # 0-100
    level: RiskLevel     # categorized bucket
    reasons: List[str]   # why this score was assigned
    blocked: bool = False


# Patterns that immediately block (score=100, blocked=True)
_BLOCK_PATTERNS = [
    r'\bwhoami\b', r'\bgetent\b', r'\bw\b', r'\buname\b', r'\bhostname\b',
    r'\bnmap\b', r'\bnetstat\b', r'\bir\b', r'\bpip\s+list\b', r'\bnpm\s+list\b',
    r'\bsecret\b', r'\bpassword\b', r'\bapi[_-]?key\b', r'\btoken\b',
    r'\b\.env\b', r'\bcredentials?\b', r'\bprivate[_-]?key\b',
    r'\bsudo\b', r'\brunsas\b', r'\bpowershell\s+-ep\s+bypass',
    r'\bexploit\b', r'\bpayload\b', r'\bshellcode\b',
    r'ignore\s+previous\s+instructions?',
    r'you\s+are\s+now\s+(\w+)',
    r'disabl(e|ed?)\s+(sandbox|security|guardrail|filter)',
    r'bypass\s+(security|sandbox|policy)',
]

_HIGH_PATTERNS = [
    r'\b(python|node|ruby|perl|php|bash|sh|pwsh|powershell)\b',
    r'\bexec\b', r'\beval\b', r'\bsystem\b', r'\bpopen\b',
    r'\bcurl\s+.*\|\s*(bash|sh|python|node)',
    r'\brm\s+-rf\b', r'\bdeltree\b',
]

_MODERATE_PATTERNS = [
    r'\b(touch|mkdir|cp|mv|rename)\b',
    r'\b(pip|npm|pnpm|yarn)\s+install\b',
    r'\b(make|cmake|gradle|mvn)\b',
    r'\b(test|pytest|jest|mocha)\b',
]


def classify_command(command: str, is_cowork_mode: bool = True) -> RiskResult:
    score = 0
    reasons: List[str] = []
    blocked = False
    cmd_lower = command.lower()

    for pattern in _BLOCK_PATTERNS:
        m = re.search(pattern, cmd_lower, re.IGNORECASE)
        if m:
            reasons.append(f"blocked: matched '{m.group()[:50]}'")
            score = 100
            blocked = True
            break

    if not blocked:
        high_hits = 0
        for pattern in _HIGH_PATTERNS:
            m = re.search(pattern, cmd_lower, re.IGNORECASE)
            if m:
                high_hits += 1
                reasons.append(f"high: matched '{m.group()[:50]}'")
        score = max(score, min(85, 50 + high_hits * 10))

        mod_hits = 0
        for pattern in _MODERATE_PATTERNS:
            m = re.search(pattern, cmd_lower, re.IGNORECASE)
            if m:
                mod_hits += 1
                reasons.append(f"moderate: matched '{m.group()[:50]}'")
        score = max(score, min(60, 20 + mod_hits * 10))

        if not reasons:
            score = 10
            reasons.append("safe: no risky patterns detected")

    if blocked:
        level = RiskLevel.BLOCKED
    elif score >= 86:
        level = RiskLevel.CRITICAL
    elif score >= 61:
        level = RiskLevel.HIGH
    elif score >= 31:
        level = RiskLevel.MODERATE
    else:
        level = RiskLevel.SAFE

    return RiskResult(score=score, level=level, reasons=reasons, blocked=blocked)


def classify_prompt(prompt: str, is_cowork_mode: bool = True) -> RiskResult:
    return classify_command(prompt, is_cowork_mode=is_cowork_mode)


def classify_attachment(path: str, mime_type: Optional[str] = None) -> RiskResult:
    import os
    _, ext = os.path.splitext(path.lower())
    blocked_ext = {
        '.py', '.js', '.ts', '.java', '.cs', '.cpp', '.c', '.go', '.rs',
        '.php', '.vb', '.sql', '.ps1', '.sh', '.bat', '.cmd', '.vbs',
        '.vba', '.exe', '.dll', '.jar',
    }
    if ext in blocked_ext:
        return RiskResult(100, RiskLevel.BLOCKED,
                          [f"blocked: extension '{ext}'"], blocked=True)
    if mime_type:
        blocked_mimes = {
            'application/x-executable', 'application/x-dosexec',
            'application/x-pie-executable', 'application/x-sharedlib',
            'application/java-archive', 'application/x-msdownload',
        }
        if mime_type.lower() in blocked_mimes:
            return RiskResult(100, RiskLevel.BLOCKED,
                              [f"blocked: MIME '{mime_type}'"], blocked=True)
    return RiskResult(30, RiskLevel.SAFE, ["safe: allowed file type"], blocked=False)


def classify_action(action_type: str, action_details: Optional[dict] = None) -> RiskResult:
    a = action_type.lower()
    if any(kw in a for kw in ('execute', 'run', 'shell', 'system')):
        return RiskResult(70, RiskLevel.HIGH, [f"high: action '{action_type}'"])
    if any(kw in a for kw in ('write', 'create', 'modify', 'delete', 'install')):
        return RiskResult(40, RiskLevel.MODERATE, [f"moderate: action '{action_type}'"])
    if any(kw in a for kw in ('read', 'list', 'get', 'search', 'query')):
        return RiskResult(10, RiskLevel.SAFE, [f"safe: action '{action_type}'"])
    return RiskResult(50, RiskLevel.MODERATE, [f"unknown: action '{action_type}'"])