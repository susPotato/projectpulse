"""Agentic loop for the Code tab — Claude-CLI style.

Drives the provider with the file/command tools, routing every write/run action
through the permission gate. Read-only tools run without prompting. For models
without native tool-calling, a text fallback protocol is supported: the model
emits a line ``@@TOOL <name> <json-args>``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..providers.base import Provider
from . import agent_roles
from . import agent_security
from .ms365_tools import MS365_WRITE_TOOLS
from .permissions import PermissionGate
from .plan import UPDATE_PLAN_SPEC, normalize_plan_steps
from .tools import TOOL_SPECS, WRITE_TOOLS, ToolContext, describe_action, execute_tool

EmitFn = Callable[[Dict[str, Any]], None]
CancelFn = Callable[[], bool]

MAX_STEPS = 40  # headroom for diagnose → fix → retry loops
_TOOL_LINE = re.compile(r"@@TOOL\s+(\w+)\s+(\{.*\})", re.DOTALL)


def code_system_prompt(workdir: Path, has_memory: bool = False, plan: bool = False,
                       has_plan_tool: bool = False, has_ms365: bool = False) -> str:
    names = ", ".join(t.name for t in TOOL_SPECS)
    plan_note = ("PLAN MODE: only analyze and propose a detailed plan; do NOT write files or run "
                 "commands. When the user asks to gencode/implement, the app switches to ACT.\n"
                 if plan else "")
    memory_note = ""
    if has_memory:
        memory_note = (
            "You also have 'codebase memory' (cmem_*): cmem_get_architecture, cmem_search_graph, "
            "cmem_trace_path, cmem_get_code_snippet, cmem_search_code, cmem_query_graph. "
            "Prefer these to understand code structure (callers/callees, where functions live) "
            "instead of reading/grepping file by file — faster and fewer tokens.\n"
        )
    plan_tool_note = ""
    if has_plan_tool:
        plan_tool_note = (
            "A step checklist is shown to the user. Call update_plan(steps=[{title, status}]) to "
            "keep it in sync as you work — mark the current step 'running', then 'done' when "
            "finished (status is one of pending/running/done).\n"
        )
    ms365_note = ""
    if has_ms365:
        ms365_note = (
            "The user has signed in to Microsoft 365 and enabled some ms365__* tools (Outlook / "
            "Teams / OneDrive / SharePoint / meeting transcripts, via the built-in MS365 MCP "
            "server). Use them whenever the request involves that data — don't say you can't "
            "access it.\n"
        )
    return (
        "You are Cowork Code — a coding assistant that works like a CLI agent.\n"
        f"Current working folder: {workdir}\n"
        + plan_note +
        "You can call the tools: " + names + ".\n"
        + memory_note + plan_tool_note + ms365_note +
        "Break work into steps, read files before editing, and explain each step briefly.\n"
        "For changes to an existing file, prefer edit_file (replace an exact snippet) over "
        "rewriting the whole file with write_file; use write_file only for new files or full "
        "rewrites. Always read_file first so old_string matches exactly.\n"
        "If a task needs a Python library that isn't installed, install it yourself with the "
        "install_package tool (or `pip install` via run_command) and continue — never ask the "
        "user to install libraries by hand. Commands and installs run inside this project's own "
        "isolated '.venv' (created automatically on first use), separate from other projects.\n"
        "When you must GENERATE a deliverable (e.g. .pptx/.docx/.xlsx/.pdf/images) by writing and "
        "running a script: put the generator script and any temporary files in a '.scratch/' "
        "subfolder, run it so the final file lands in the working folder, then DELETE the "
        "'.scratch/' folder. Only the final requested file(s) should remain — never leave "
        "generator scripts or intermediate files behind.\n"
        "Every path must stay inside the working folder.\n"
        "If a command or tool fails, do NOT stop and hand the error back to the user — read the "
        "error, fix the cause (edit the code, install a missing package, correct the command) and "
        "retry. Keep iterating until the task actually works, then run it once more so you can "
        "show the real output the user asked for.\n"
        "If the environment can't call tools directly, emit exactly ONE line of the form:\n"
        "@@TOOL <tool_name> {\"param\": \"value\"}\n"
        "When the task is complete, reply to the user in plain text — include the produced output "
        "— and stop calling tools. Answer in the user's language."
    )


_SKILLS_TAG = "[[ACTIVE_SKILLS]]"
_RULES_TAG = "[[SECURITY_RULES]]"
_PROJECT_TAG = "[project-context]"


def _apply_skills(messages: List[Dict[str, Any]], skills_text: str) -> None:
    """Insert/refresh a single system message carrying the enabled skills."""
    messages[:] = [
        m for m in messages
        if not (m.get("role") == "system" and str(m.get("content", "")).startswith(_SKILLS_TAG))
    ]
    if not skills_text.strip():
        return
    block = {
        "role": "system",
        "content": f"{_SKILLS_TAG}\nThe user enabled the following skills — follow them:\n\n{skills_text}",
    }
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(insert_at, block)


def _apply_security_rules(messages: List[Dict[str, Any]], rules_text: str) -> None:
    """Insert/refresh a single system message carrying the external security/
    restriction rules (core/security_rules.py) — same pattern as _apply_skills,
    but for mandatory guardrails rather than opt-in behaviors."""
    messages[:] = [
        m for m in messages
        if not (m.get("role") == "system" and str(m.get("content", "")).startswith(_RULES_TAG))
    ]
    if not rules_text.strip():
        return
    block = {
        "role": "system",
        "content": (f"{_RULES_TAG}\nMandatory security/restriction rules — check every request "
                    "and action against these BEFORE acting; refuse or ask for clarification "
                    f"instead of proceeding if something would violate them:\n\n{rules_text}"),
    }
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(insert_at, block)


def _apply_project_context(messages: List[Dict[str, Any]], context_text: str) -> None:
    """Insert/refresh a single system message carrying the project's shared
    instructions (Claude-Projects style) — same pattern as _apply_skills, so
    editing the project context takes effect on the NEXT turn of every one of
    its threads, without duplicating blocks in long conversations."""
    messages[:] = [
        m for m in messages
        if not (m.get("role") == "system" and str(m.get("content", "")).startswith(_PROJECT_TAG))
    ]
    if not (context_text or "").strip():
        return
    block = {"role": "system", "content": f"{_PROJECT_TAG}\n{context_text.strip()}"}
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    messages.insert(insert_at, block)


_RECOVERY_NOTE = (
    "[auto-recovery] The previous attempt hit an error. Review the conversation so "
    "far, redo the most recent step if it looks incomplete or wrong, and fix any "
    "issue before continuing."
)


def _call_provider_with_recovery(provider: Provider, messages: List[Dict[str, Any]],
                                 tools, on_text, cancel, on_reasoning=None,
                                 max_retries: int = 1) -> Dict[str, Any]:
    """``provider.chat(...)`` with ONE bounded, silent recovery attempt: if the
    call raises (a dropped connection, an exhausted rate-limit wait, a
    momentarily unreachable gateway, ...), retry once with a short recovery
    note appended ONLY to that retry's OWN copy of ``messages`` — the caller's
    ``messages`` list is never mutated, so the note never leaks into the
    real, persisted conversation. Exhausting the retry re-raises the original
    exception unchanged, preserving existing failure handling (e.g. the
    "model not found" restore-to-composer flow).

    ``on_reasoning`` is only forwarded when given — ``run_code`` never passed
    it before this helper existed, and some lightweight test doubles for
    ``Provider`` don't accept the keyword at all; omitting it when unset
    keeps every existing call site's exact prior calling convention."""
    kwargs = {"tools": tools, "on_text": on_text, "cancel": cancel}
    if on_reasoning is not None:
        kwargs["on_reasoning"] = on_reasoning
    try:
        return provider.chat(messages, **kwargs)
    except Exception:
        if max_retries <= 0 or cancel():
            raise
        recovery = messages + [{"role": "user", "content": _RECOVERY_NOTE}]
        return _call_provider_with_recovery(provider, recovery, tools, on_text, cancel,
                                            on_reasoning, max_retries - 1)


def _parse_react(content: str) -> List[Dict[str, Any]]:
    """Extract @@TOOL fallback calls from assistant text."""
    calls: List[Dict[str, Any]] = []
    for i, match in enumerate(_TOOL_LINE.finditer(content or "")):
        name = match.group(1)
        try:
            args = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue
        calls.append({"id": f"react_{i}", "name": name, "arguments": args})
    return calls


def run_code(
    provider: Provider,
    messages: List[Dict[str, Any]],
    ctx: ToolContext,
    gate: PermissionGate,
    emit: EmitFn,
    cancel: Optional[CancelFn] = None,
    max_steps: int = MAX_STEPS,
    extra_tools: Optional[List] = None,
    extra_executor=None,
    skills_text: str = "",
    rules_text: str = "",
    project_context: str = "",
    plan: bool = False,
    security_config=None,
) -> List[Dict[str, Any]]:
    """``security_config`` is the app's ``AppConfig`` — enables the same
    active guardrails as ``chat_agent.run_cowork`` (see its docstring).
    ``None`` (the default) disables both checks."""
    cancel = cancel or (lambda: False)
    extra_tools = extra_tools or []
    extra_names = {t.name for t in extra_tools}
    # update_plan drives the Plan panel (same as run_cowork) — always
    # available, not opt-in via extra_tools, so unattended Schedule Tasks
    # running through the code agent also get the completion self-check.
    from .tools import enabled_tool_specs
    all_tools = enabled_tool_specs(security_config) + extra_tools + [UPDATE_PLAN_SPEC]
    # MS365 tools that send/write data (mail, Teams messages, OneDrive
    # writes) are gated exactly like write_file/run_command — only the
    # read/list ms365 tools count as "read-only, never confirm". Names are
    # the MCP-qualified "ms365__*" form the agent sees (see ms365_tools.py).
    gated_tools = WRITE_TOOLS | MS365_WRITE_TOOLS
    # In PLAN mode, don't advertise write/run tools (analysis only).
    advertised = [t for t in all_tools if t.name not in gated_tools] if plan else all_tools
    has_memory = any(t.name.startswith("cmem_") for t in extra_tools)
    has_plan_tool = True
    has_ms365 = any(t.name.startswith("ms365_") for t in extra_tools)
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system",
                            "content": code_system_prompt(
                                ctx.workdir, has_memory, plan, has_plan_tool, has_ms365)})
    _apply_skills(messages, skills_text)
    # The CODE agent uses RULEforCode.md (empty for now), NOT Cowork's
    # RULEBASE.md — its safety comes from the sandbox until code rules exist.
    from .security_rules import load_code_rules
    _apply_security_rules(messages, rules_text or load_code_rules())
    _apply_project_context(messages, project_context)
    # Active guardrail — reviews the request itself and can refuse to proceed
    # at all. Raises SecurityBlocked on a violation. agent_kind="code" → the
    # code rulebase (RULEforCode.md), so RULEBASE.md's rules don't gate coding.
    agent_security.enforce_prompt(provider, messages, security_config, emit, agent_kind="code")

    for _ in range(max_steps):
        if cancel():
            break

        def on_text(piece: str) -> None:
            emit({"type": "text", "delta": piece})

        assistant = _call_provider_with_recovery(provider, messages, advertised, on_text, cancel)
        if not assistant.get("tool_calls"):
            fallback = _parse_react(assistant.get("content", ""))
            if fallback:
                assistant["tool_calls"] = fallback
        messages.append(assistant)
        if not assistant.get("tool_calls") and not (assistant.get("content") or "").strip():
            # Reasoning-only reply with no answer and no tool call — never leave a
            # blank final turn (a Schedule Task run reads this back as its final
            # answer, so a blank one silently produces "(no output)").
            assistant["content"] = "*(model returned only its reasoning — try rephrasing)*"
        emit({"type": "assistant_done", "content": assistant.get("content", "")})

        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            break

        for tc in tool_calls:
            if cancel():
                return messages
            name, args, tc_id = tc["name"], tc.get("arguments", {}), tc["id"]
            # update_plan drives the Plan panel only — no chat bubble, no file.
            if name == "update_plan":
                steps = normalize_plan_steps(args.get("steps"))
                emit({"type": "plan_set", "steps": steps})
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                 "content": "Plan updated."})
                continue
            if plan and name in gated_tools:
                emit({"type": "tool_result", "id": tc_id, "name": name, "ok": False,
                      "output": "PLAN mode: action skipped (switch to Act to execute)."})
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                 "content": "PLAN mode: not executed."})
                continue
            is_extra = name in extra_names
            preview = ({"kind": "info", "title": name, "text": str(args)}
                       if is_extra else describe_action(ctx, name, args))
            emit({"type": "tool_proposed", "id": tc_id, "name": name, "args": args, "preview": preview})
            # Active guardrail on run_command/install_package, checked BEFORE
            # asking the user to confirm — a no-op for every other tool or
            # when disabled. Raises SecurityBlocked on a violation. Code agent
            # → RULEforCode.md rulebase (not Cowork's RULEBASE.md).
            agent_security.enforce_command(provider, name, args, security_config, emit,
                                           agent_kind="code")

            if name in gated_tools:
                approved = gate.request({"id": tc_id, "name": name, "args": args, "preview": preview})
            else:
                approved = True  # read-only tools (incl. codebase memory) never confirm

            if cancel():
                return messages

            if not approved:
                result = {"ok": False, "output": "User rejected the action."}
            else:
                emit({"type": "tool_start", "id": tc_id, "name": name})
                if is_extra and extra_executor is not None:
                    result = extra_executor(name, args)
                else:
                    def on_output(line: str, _id=tc_id, _name=name) -> None:
                        emit({"type": "tool_output", "id": _id, "name": _name, "delta": line})
                    result = execute_tool(ctx, name, args, cancel=cancel, on_output=on_output,
                                          agent_role=agent_roles.CODE)

            evt = {
                "type": "tool_result", "id": tc_id, "name": name,
                "ok": result["ok"], "output": result["output"],
            }
            if isinstance(args, dict) and args.get("path"):
                # absolute path so the UI can open the containing folder
                evt["path"] = str(ctx.workdir / str(args["path"]))
            if isinstance(result, dict) and result.get("produced"):
                evt["produced"] = result["produced"]
            emit(evt)
            messages.append({
                "role": "tool", "tool_call_id": tc_id, "name": name,
                "content": result["output"],
            })
    return messages
