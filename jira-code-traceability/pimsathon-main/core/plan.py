"""Auto-plan for the Code tab.

For each Code-tab message we (1) ask the model to split the request into a short
step checklist (shown in the preview's **Plan** view), then (2) give the agent an
``update_plan`` tool so it can tick steps off as it works.

The parse/normalize helpers are pure (no Qt, no provider) and unit-tested;
``decompose_request`` makes one provider call and is best-effort — it returns
``[]`` on any problem so it never blocks the chat.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..providers.base import Provider, ToolSpec
from .flows import STEP_DONE, STEP_ERROR, STEP_PENDING, STEP_RUNNING

_VALID_STATUS = {STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_ERROR}
MAX_PLAN_STEPS = 8


UPDATE_PLAN_SPEC = ToolSpec(
    name="update_plan",
    description=(
        "Update the task checklist shown to the user. Pass the FULL list of steps "
        "with each step's status (pending / running / done / error). Call it as you "
        "work: mark the current step 'running', then 'done' when finished, or "
        "'error' if it genuinely could not be completed (explain why in your reply "
        "text — for an unattended Schedule Task, any step left 'error' or not "
        "'done' by the time you finish means the task is NOT reported as done)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "status": {"type": "string",
                                   "enum": [STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_ERROR]},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["steps"],
    },
)


def parse_plan_steps(text: str, max_steps: int = MAX_PLAN_STEPS) -> List[str]:
    """Extract step titles from an LLM response.

    Accepts a JSON array (of strings or ``{title}`` objects); failing that, falls
    back to numbered / bulleted lines. Returns a trimmed, de-duplicated list (empty
    when nothing parses)."""
    text = (text or "").strip()
    if not text:
        return []
    titles: List[str] = []
    # 1) JSON array anywhere in the text.
    start, end = text.find("["), text.rfind("]")
    if 0 <= start < end:
        try:
            arr = json.loads(text[start:end + 1])
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, str):
                        titles.append(item)
                    elif isinstance(item, dict) and item.get("title"):
                        titles.append(str(item["title"]))
        except json.JSONDecodeError:
            pass
    # 2) Fallback: numbered / bulleted lines.
    if not titles:
        for line in text.splitlines():
            m = re.match(r"\s*(?:\d+[.)]|[-*•])\s+(.*\S)", line)
            if m:
                titles.append(m.group(1))
    # Tidy: strip, drop empties, de-dupe (preserve order), cap at max_steps.
    out: List[str] = []
    seen = set()
    for t in titles:
        t = t.strip().strip('"').strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= max_steps:
            break
    return out


def plan_incomplete_reason(steps: List[Dict[str, str]]) -> str:
    """``""`` when there's no plan, or every step ended up 'done'; otherwise a
    short human-readable note naming the step(s) that are NOT done (still
    pending/running, or explicitly marked 'error') — used so a Schedule Task
    isn't reported "done" when its own checklist says the work wasn't
    actually finished."""
    if not steps:
        return ""
    bad = [s for s in steps if s.get("status") != STEP_DONE]
    if not bad:
        return ""
    errored = [s["title"] for s in bad if s.get("status") == STEP_ERROR]
    unfinished = [s["title"] for s in bad if s.get("status") != STEP_ERROR]
    parts = []
    if errored:
        parts.append(f"failed: {', '.join(errored)}")
    if unfinished:
        parts.append(f"left unfinished: {', '.join(unfinished)}")
    return "The task's own plan reports steps not completed (" + "; ".join(parts) + ")."


def normalize_plan_steps(raw: Any) -> List[Dict[str, str]]:
    """Validate the agent's ``update_plan`` ``steps`` argument into
    ``[{title, status}]``. Non-dict items are dropped; an unknown/missing status is
    clamped to ``pending``."""
    out: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        status = str(item.get("status", STEP_PENDING)).strip().lower()
        if status not in _VALID_STATUS:
            status = STEP_PENDING
        out.append({"title": title, "status": status})
    return out


_DECOMPOSE_SYSTEM = (
    "You are a planning assistant. Break the user's coding request into a SHORT "
    "ordered checklist of concrete steps (2 to {n} steps; fewer is fine). "
    "Reply with ONLY a JSON array of short imperative step titles — no prose, no "
    "code fences. Example: [\"Read the config\", \"Add the new field\", \"Run tests\"]."
)


def decompose_request(provider: Provider, request: str,
                      cancel: Optional[Any] = None,
                      max_steps: int = MAX_PLAN_STEPS) -> List[str]:
    """Best-effort: ask the model to split ``request`` into step titles. Returns
    ``[]`` on any error/timeout so a failed plan never blocks the turn."""
    request = (request or "").strip()
    if not request:
        return []
    messages = [
        {"role": "system", "content": _DECOMPOSE_SYSTEM.format(n=max_steps)},
        {"role": "user", "content": request},
    ]
    try:
        assistant = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001 - planning must never break the turn
        return []
    return parse_plan_steps(assistant.get("content", ""), max_steps)
