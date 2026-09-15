"""Auto-compress a conversation when it nears the model's context budget.

When the running message list exceeds a configurable fraction (default 80%) of
the model's context window ("memory quota"), the oldest turns are summarized
into one compact note so the conversation can keep going without overflowing.
Kept Qt-free and pure so it's unit-testable and usable by any agent loop
(Cowork chat, Co4E runner, Schedule Task — all go through chat_agent.run_cowork).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .usage_tracker import estimate_tokens

DEFAULT_LIMIT = 128_000          # tokens; used when a model isn't in the map below
DEFAULT_THRESHOLD = 0.8          # compact once usage passes 80% of the limit
_KEEP_RECENT = 6                 # most-recent messages always kept verbatim

# Approximate context windows by model-name substring (longest match wins).
_MODEL_LIMITS = {
    "claude": 200_000,
    "opus": 200_000,
    "sonnet": 200_000,
    "haiku": 200_000,
    "gpt-4o": 128_000,
    "gpt-4.1": 1_000_000,
    "o1": 200_000,
    "o3": 200_000,
    "gpt-4": 128_000,
    "gpt-3.5": 16_000,
    "gemini": 1_000_000,
}


def model_context_limit(model: str) -> int:
    m = (model or "").lower()
    best = 0
    limit = DEFAULT_LIMIT
    for key, val in _MODEL_LIMITS.items():
        if key in m and len(key) > best:
            best, limit = len(key), val
    return limit


def _ctx_conf(config) -> Dict[str, Any]:
    if config is None:
        return {}
    try:
        return config.data.get("context", {}) or {}
    except AttributeError:
        return {}


def context_limit(config, model: str = "") -> int:
    """Configured override (context.limit_tokens > 0) else the model's window."""
    conf = _ctx_conf(config)
    override = int(conf.get("limit_tokens", 0) or 0)
    return override if override > 0 else model_context_limit(model)


def auto_compact_enabled(config) -> bool:
    conf = _ctx_conf(config)
    return bool(conf.get("auto_compact", True))


def threshold(config) -> float:
    conf = _ctx_conf(config)
    try:
        t = float(conf.get("compact_threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        t = DEFAULT_THRESHOLD
    return t if 0.1 <= t <= 0.99 else DEFAULT_THRESHOLD


def _msg_text(m: Dict[str, Any]) -> str:
    c = m.get("content", "")
    if isinstance(c, str):
        return c
    # tool-call/structured content: stringify defensively
    return str(c)


def estimate_messages_tokens(messages: List[Dict[str, Any]]) -> int:
    return sum(estimate_tokens(_msg_text(m)) for m in messages)


def should_compact(messages: List[Dict[str, Any]], limit: int,
                   thresh: float = DEFAULT_THRESHOLD) -> bool:
    if limit <= 0 or len(messages) <= _KEEP_RECENT + 2:
        return False
    return estimate_messages_tokens(messages) > limit * thresh


_SUMMARY_PROMPT = (
    "You compress a conversation to save context. Summarize the messages below "
    "into a concise but information-dense note that preserves: the user's goals, "
    "key decisions, facts, file names/paths, and any state needed to continue. "
    "Reply with ONLY the summary text.")


def _summarize(provider, middle: List[Dict[str, Any]], cancel=None) -> str:
    convo = "\n\n".join(f"[{m.get('role', '?')}] {_msg_text(m)}" for m in middle)
    try:
        a = provider.chat([{"role": "system", "content": _SUMMARY_PROMPT},
                           {"role": "user", "content": convo[:60_000]}],
                          tools=None, on_text=None, cancel=cancel)
        text = (a.get("content") or "").strip()
        if text:
            return text
    except Exception:  # noqa: BLE001 — compaction must never break the turn
        pass
    # Fallback: keep the head of the oldest content so nothing is silently lost.
    return convo[:4000] + ("\n…(older context truncated)" if len(convo) > 4000 else "")


def compact_messages(provider, messages: List[Dict[str, Any]], *,
                     keep_recent: int = _KEEP_RECENT, cancel=None) -> List[Dict[str, Any]]:
    """Return a compacted copy: system message(s) at the front (if any) + a
    single summary of the middle + the last ``keep_recent`` messages verbatim.
    Returns the list unchanged when there's nothing worth compacting."""
    if len(messages) <= keep_recent + 2:
        return messages
    head_n = 1 if messages and messages[0].get("role") == "system" else 0
    head = messages[:head_n]
    tail = messages[-keep_recent:]
    middle = messages[head_n:-keep_recent]
    if not middle:
        return messages
    summary = _summarize(provider, middle, cancel=cancel)
    note = {"role": "system",
            "content": f"[Conversation summary — older messages compressed to save memory]\n{summary}"}
    return list(head) + [note] + list(tail)


def maybe_compact(provider, messages: List[Dict[str, Any]], config,
                  emit=None, cancel=None) -> bool:
    """If auto-compact is on and usage is over threshold, compact ``messages``
    IN PLACE. Returns True when a compaction happened. Safe/no-op when config
    is None or the feature is off."""
    if not auto_compact_enabled(config):
        return False
    model = getattr(provider, "model", "") or ""
    limit = context_limit(config, model)
    if not should_compact(messages, limit, threshold(config)):
        return False
    compacted = compact_messages(provider, messages, cancel=cancel)
    if compacted is messages or len(compacted) >= len(messages):
        return False
    messages[:] = compacted
    if emit:
        try:
            emit({"type": "notice", "level": "info",
                  "text": "🧹 Conversation compressed to stay within the memory limit."})
        except Exception:  # noqa: BLE001
            pass
    return True
