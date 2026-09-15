"""External security/restriction rules the agent must check every request and
response against before acting — analogous to core/skills.py, but for
mandatory guardrails rather than opt-in behaviors. An admin/security team can
edit this file directly; it's re-read fresh on every turn, so no rebuild or
even app restart is needed for a change to take effect.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import CONFIG_DIR

RULES_PATH = CONFIG_DIR / "security_rules.md"
BUNDLED_RULEBASE = Path(__file__).resolve().parent.parent / "assets" / "RULEBASE.md"
# The CODE agent uses a SEPARATE rulebase (RULEBASE.md — incl. any "no coding"
# restriction — applies to the Cowork agent only). This one is intentionally
# empty for now; the Code agent's safety comes from the sandbox until rules are
# defined here. CONFIG_DIR override wins over the bundled placeholder.
BUNDLED_CODE_RULES = Path(__file__).resolve().parent.parent / "assets" / "RULEforCode.md"
CODE_RULES_PATH = CONFIG_DIR / "RULEforCode.md"
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Keep prompt bloat bounded even if someone pastes an entire policy document.
_MAX_CHARS = 20000


def _resolve_rulebase() -> Path:
    """Return the rulebase path:
    1. Configured rulebase_path in agent_security settings (rulebase_path key)
    2. CONFIG_DIR/RULEBASE.md (copied from bundled)
    3. Bundled RULEBASE.md in assets/
    4. Old CONFIG_DIR/security_rules.md (legacy fallback)
    """
    try:
        from ..config import load_config
        cfg = load_config()
        rb_path = (cfg.get("agent_security") or {}).get("rulebase_path", "")
        if rb_path and Path(rb_path).exists():
            return Path(rb_path)
    except Exception:
        pass
    # Copied config rulebase
    copied = CONFIG_DIR / "RULEBASE.md"
    if copied.exists():
        return copied
    # Bundled default
    if BUNDLED_RULEBASE.exists():
        return BUNDLED_RULEBASE
    # Legacy fallback
    if RULES_PATH.exists():
        return RULES_PATH
    return RULES_PATH


def load_rules(path: Path = None) -> str:
    """Best-effort read of the external rules file.

    Returns '' when the file is missing/unreadable/empty — the agent must
    keep working with no rules configured rather than ever block on this."""
    if path is None:
        path = _resolve_rulebase()
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:_MAX_CHARS]


def _resolve_code_rulebase() -> Path:
    """CONFIG_DIR/RULEforCode.md if the admin created one, else the bundled
    (empty) placeholder."""
    return CODE_RULES_PATH if CODE_RULES_PATH.exists() else BUNDLED_CODE_RULES


def load_code_rules(path: Path = None) -> str:
    """Rules for the CODE agent (RULEforCode.md). HTML comments are stripped so
    the placeholder file — which is comment-only — yields NO rules (the Code
    agent is unrestricted beyond the sandbox until real rules are added)."""
    if path is None:
        path = _resolve_code_rulebase()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = _HTML_COMMENT_RE.sub("", raw).strip()
    return text[:_MAX_CHARS]
