"""Schedule Task module — AI Create Task.

Turns a natural-language description ("Mỗi thứ 2 lúc 9h, dùng Co4E đọc dữ liệu
CAE, tạo báo cáo, rồi chuyển cho Cowork soạn email...") into a list of task
dicts, via the active provider. The result is a PREVIEW — the UI shows it and
only creates real tasks after the user confirms (spec §9.2).
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .tasks import (
    INPUT_MODES, PRIORITIES, REPEAT_TYPES, RUN_NEXT_MODES, TASK_TYPES, new_task,
)

_SYSTEM = """You convert a user's natural-language request into scheduled tasks
for a desktop automation app. Reply with ONE JSON object only (no prose, no
markdown fences) shaped exactly like:
{"tasks": [{
  "title": str,
  "description": str,
  "task_type": "cowork"|"co4e_code"|"script"|"manual",
  "priority": "low"|"medium"|"high"|"critical",
  "schedule": {"enabled": bool, "run_at": "YYYY-MM-DD HH:MM" or null,
                "repeat_type": "none"|"daily"|"weekly"},
  "input": {"mode": "empty"|"manual"|"previous_task_output", "manual_text": str or null},
  "dependency": {"previous_task_id": "TASK_1" or null,
                  "run_next_mode": "none"|"run_after_success"|"run_always"|"run_after_manual_confirm",
                  "pass_output_to_next": bool}
}]}
Rules: use "co4e_code" for coding/data/file-processing work, "cowork" for
documents/emails/reports/chat-style work. Reference earlier tasks in the same
reply as "TASK_1", "TASK_2" (1-based order). If the user gives a schedule,
fill run_at with the NEXT occurrence from today. Keep 1-4 tasks."""


def _extract_json(text: str) -> Optional[dict]:
    """The first parseable {...} block in the model's reply."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        return None
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
    return None


def _clamp(value, allowed, default):
    return value if value in allowed else default


def normalize_planned_tasks(payload: dict) -> List[Dict[str, Any]]:
    """Turn the model's JSON into real task dicts (all defaults filled) and
    resolve TASK_n references into actual ids + back-links, so the chain works
    both directions (prev's next_task_id AND next's previous_task_id)."""
    raw = (payload or {}).get("tasks") or []
    if not isinstance(raw, list) or not raw:
        return []
    tasks: List[Dict[str, Any]] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        t = new_task(str(item.get("title") or "Untitled task"))
        t["description"] = str(item.get("description") or "")
        t["task_type"] = _clamp(item.get("task_type"), TASK_TYPES, "cowork")
        t["priority"] = _clamp(item.get("priority"), PRIORITIES, "medium")
        t["is_ai_generated"] = True
        sched = item.get("schedule") or {}
        t["schedule"]["enabled"] = bool(sched.get("enabled"))
        t["schedule"]["run_at"] = sched.get("run_at") or None
        t["schedule"]["repeat_type"] = _clamp(sched.get("repeat_type"), REPEAT_TYPES, "none")
        if t["schedule"]["enabled"] and t["schedule"]["run_at"]:
            t["status"] = "scheduled"
        inp = item.get("input") or {}
        t["input"]["mode"] = _clamp(inp.get("mode"), INPUT_MODES, "empty")
        t["input"]["manual_text"] = inp.get("manual_text") or None
        dep = item.get("dependency") or {}
        t["dependency"]["run_next_mode"] = _clamp(dep.get("run_next_mode"),
                                                  RUN_NEXT_MODES, "none")
        t["dependency"]["pass_output_to_next"] = bool(dep.get("pass_output_to_next"))
        t["_prev_ref"] = dep.get("previous_task_id")   # TASK_n, resolved below
        tasks.append(t)

    # Resolve TASK_n → actual ids; wire both directions of the chain.
    for t in tasks:
        ref = t.pop("_prev_ref", None)
        if not ref:
            continue
        m = re.match(r"TASK_(\d+)$", str(ref).strip())
        idx = int(m.group(1)) - 1 if m else -1
        if 0 <= idx < len(tasks) and tasks[idx] is not t:
            prev = tasks[idx]
            t["dependency"]["previous_task_id"] = prev["task_id"]
            t["input"]["previous_task_id"] = prev["task_id"]
            prev["dependency"]["next_task_id"] = t["task_id"]
            if t["dependency"]["run_next_mode"] != "none":
                prev["dependency"]["run_next_mode"] = t["dependency"]["run_next_mode"]
            if t["dependency"]["pass_output_to_next"] or t["input"]["mode"] == "previous_task_output":
                prev["dependency"]["pass_output_to_next"] = True
                t["input"]["mode"] = "previous_task_output"
    return tasks


def generate_task_description(provider, title: str, cancel=None) -> str:
    """Best-effort ✨ helper: draft a task's description from its title.
    Returns '' on any error so the editor never breaks."""
    title = (title or "").strip()
    if not title:
        return ""
    messages = [
        {"role": "system", "content":
            "You write the DESCRIPTION of a scheduled automation task. Given its title, "
            "write 2-4 concise sentences describing exactly what the task should do "
            "(inputs, action, expected output). Reply with ONLY the description text, "
            "in the same language as the title."},
        {"role": "user", "content": title},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001
        return ""
    return (a.get("content") or "").strip()


def generate_task_input_text(provider, title: str, description: str, cancel=None) -> str:
    """Best-effort ✨ helper: draft the task's Prompt/Input text from its
    title + description. Returns '' on any error so the editor never breaks."""
    title = (title or "").strip()
    if not title:
        return ""
    messages = [
        {"role": "system", "content":
            "You write the INPUT/PROMPT text for a scheduled automation task — extra "
            "context, data, or instructions the agent will need beyond the title and "
            "description. Given the task's title and description, write 2-4 concise "
            "sentences. Reply with ONLY the prompt text, in the same language as the title."},
        {"role": "user", "content": f"Title: {title}\nDescription: {description or '(none)'}"},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001
        return ""
    return (a.get("content") or "").strip()


def generate_prompt_from_description(provider, description: str, cancel=None) -> str:
    """Best-effort ✨ helper: expand the task's DESCRIPTION into the ready-to-run
    Prompt/Input text the agent will act on. The title is intentionally NOT used
    — in Schedule Task the title is just the card's label, so the content comes
    only from the description. Returns '' on empty input or any error so the
    editor never breaks."""
    description = (description or "").strip()
    if not description:
        return ""
    messages = [
        {"role": "system", "content":
            "You turn a task DESCRIPTION into the ready-to-run PROMPT an AI agent will "
            "execute for a scheduled automation task. Rewrite the description as clear, "
            "actionable instructions (what to do, with which inputs, and the expected "
            "output). Do NOT invent a topic from a title — use ONLY the description. "
            "Reply with ONLY the prompt text, in the same language as the description."},
        {"role": "user", "content": description},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001
        return ""
    return (a.get("content") or "").strip()


def generate_agent_prompt(provider, name: str = "", role: str = "", hint: str = "", cancel=None) -> str:
    """Best-effort ✨ helper: draft the INSTRUCTIONS/prompt for a Co4E agent from
    its name + role (+ optional hint) — used when the user hasn't attached a
    skill and wants the agent's behaviour written for them. Returns '' on error."""
    name = (name or "").strip()
    role = (role or "").strip()
    if not name and not role and not hint:
        return ""
    who = f"{name} ({role})" if role else name or role
    messages = [
        {"role": "system", "content":
            "You write the INSTRUCTIONS (system prompt) for a specialized AI agent that runs as one "
            "step in a workflow. Given the agent's name/role (and any hint), write clear, imperative "
            "guidance: what this agent is responsible for, how it should work, and what its output "
            "should be. A few concise sentences or short bullets. Reply with ONLY the instructions "
            "text — no title, no preamble."},
        {"role": "user", "content": f"Agent: {who}" + (f"\nHint: {hint}" if hint else "")},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001 — generation must never break the dialog
        return ""
    return (a.get("content") or "").strip()


def plan_tasks(provider, description: str, cancel=None) -> List[Dict[str, Any]]:
    """description → normalized task dicts (NOT yet saved). Raises RuntimeError
    when the model's reply has no parseable task JSON."""
    from datetime import datetime

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Today is {datetime.now().strftime('%Y-%m-%d %H:%M %A')}.\n"
                                     f"Request: {description}"},
    ]
    reply = provider.chat(messages, cancel=cancel)
    payload = _extract_json(reply.get("content", ""))
    tasks = normalize_planned_tasks(payload) if payload else []
    if not tasks:
        raise RuntimeError("AI reply did not contain a valid task list — try rephrasing.")
    return tasks
