"""Schedule Task module — run one task and write its artifacts.

``execute_task`` dispatches by ``task_type`` to the app's existing engines:

- ``cowork``     → ``chat_agent.run_cowork``  (documents/answers, real files)
- ``co4e_code``  → ``code_agent.run_code``    (code agent with file/command tools)
- ``script``     → local subprocess with a timeout
- ``flow``       → the task's own simple step list, run sequentially, each
                   step's output appended to the next step's input
- ``manual``     → never auto-runs; returns a note

Every run gets an artifact folder ``task_artifacts/<task_id>/<run_id>/`` with
``output.md``, ``logs.txt``, ``error.txt`` and ``generated_files/`` (spec §11.2).
The permission question (spec §13) is decided BEFORE this module is called:
the scheduler refuses to auto-run tasks with ``requires_approval`` (they park
in Waiting Input), so executors here run with an auto gate.
"""
from __future__ import annotations

import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import agent_roles
from . import agent_security
from . import projects
from .permissions import PermissionGate
from .tasks import ARTIFACTS_DIR, resolve_input_text
from .tools import ToolContext

EmitFn = Callable[[Dict[str, Any]], None]
CancelFn = Callable[[], bool]


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def artifact_dir(task_id: str, run_id: str) -> Path:
    d = ARTIFACTS_DIR / task_id / run_id
    (d / "generated_files").mkdir(parents=True, exist_ok=True)
    return d


def _last_assistant_text(messages) -> str:
    for m in reversed(messages or []):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            return m["content"]
    return ""


_OUTPUT_MODE_HINTS = {
    "file": "Produce a real, saved FILE as the deliverable (not just a chat reply).",
    "folder": "Produce a real folder of files as the deliverable.",
    "markdown": "Write the deliverable as a Markdown document.",
    "json": "Write the deliverable as valid JSON.",
    "code_diff": "Produce the change as a code diff/patch, with file paths.",
}


def _output_mode_hint(task: Dict[str, Any]) -> str:
    return _OUTPUT_MODE_HINTS.get(task.get("output", {}).get("output_mode", "text"), "")


_MAX_INLINE_FOLDER_FILE_CHARS = 20_000  # mirrors tasks.py's _MAX_INLINE_FILE_CHARS


def _project_folder_input_text(project: Optional[projects.Project], max_files: int) -> str:
    """Recursively scan a task's linked project folder (any depth of
    sub-folders) and inline its readable files as input — mirrors the
    interactive Cowork chat's own auto-scan of its workspace folder, so a
    task linked to a project automatically "sees" whatever is already sitting
    in that project's folder, nested files included, the same way opening
    that project in Cowork already does."""
    if project is None:
        return ""
    from .doc_extract import extract_text, find_input_files

    files, total = find_input_files(project.workspace_dir(), max_files=max_files)
    if not files:
        return ""
    lines = ["\n--- Project folder files ---",
             "Existing files in this task's linked project folder (sub-folders "
             "included) — read and use them as input data:"]
    for f in files:
        text, note = extract_text(str(f))
        if text is None:
            lines.append(f"- {f.name} ({note}; located at {f})")
            continue
        if len(text) > _MAX_INLINE_FOLDER_FILE_CHARS:
            text = text[:_MAX_INLINE_FOLDER_FILE_CHARS] + "\n…(truncated)…"
        lines.append(f"- {f.name} ({f})\n--- Content of {f.name} ---\n{text}\n--- end of {f.name} ---")
    if total > len(files):
        lines.append(f"…({total - len(files)} more files in the project folder "
                     "were not loaded — attachment limit)")
    return "\n".join(lines)


def _build_prompt(task: Dict[str, Any], tasks_dir: Path = None,
                  project: Optional[projects.Project] = None,
                  max_files: int = 10) -> str:
    parts = [task.get("description") or task.get("title") or ""]
    extra = resolve_input_text(task, tasks_dir)
    if extra:
        parts.append(f"\n--- Input ---\n{extra}")
    folder_text = _project_folder_input_text(project, max_files)
    if folder_text:
        parts.append(folder_text)
    hint = _output_mode_hint(task)
    if hint:
        parts.append(f"\n--- Output requirement ---\n{hint}")
    return "\n".join(p for p in parts if p)


def _format_output_md(task: Dict[str, Any], output_text: str) -> str:
    """output.md always states what was asked BEFORE what was delivered — so
    it fully stands on its own (opened directly) AND still works as a
    dependent task's input (which needs the original ask for context, not
    just a bare answer)."""
    title = task.get("title", "")
    description = task.get("description", "")
    header = f"# Yêu cầu (Request)\n**{title}**"
    if description:
        header += f"\n\n{description}"
    return f"{header}\n\n# Kết quả (Output)\n\n{output_text or '(no output)'}"


def _save_history_session(ctx, task_type: str, title: str, messages,
                          session_id: str, project_id: str = "") -> None:
    """Each task run IS one conversation session: a Cowork-type run shows up
    in the History sidebar under Cowork, a Co4E-type run under Code — exactly
    like a chat the user typed themselves, prefixed "[Task]" so it's
    recognizable. ``project_id`` (the task's OWN linked project, when any)
    tags it so it shows up filtered into that project's Workspace → Cowork
    sub-tab too — the sidebar's ``HistorySidebar.set_project_filter`` hides
    any conversation whose project_id doesn't match, so a task run saved
    without one is invisible there even though the task really is running.
    Best-effort: history must never break a run."""
    try:
        from .history import save_conversation

        kind = "cowork" if task_type == "cowork" else "code"
        save_conversation(ctx.config.history_dir(), kind, session_id,
                          messages, title=f"[Task] {title}"[:80], project_id=project_id)
    except Exception:  # noqa: BLE001
        pass


_TIMEOUT_NOTICE_TMPL = (
    "⏱️ **Task đã dừng: AI model không phản hồi trong {timeout}s (quá thời gian chờ).**\n\n"
    "Nguyên nhân có thể: mất kết nối mạng, provider/API đang quá tải hoặc gặp sự cố, "
    "hoặc cấu hình provider (API key/model) trong Settings không đúng.\n\n"
    "Hướng dẫn xử lý:\n"
    "- Kiểm tra kết nối Internet, sau đó chuột phải vào task → Run now để chạy lại.\n"
    "- Vào Settings kiểm tra API key/model của provider đang dùng.\n"
    "- Nếu task cần nhiều thời gian hơn để hoàn thành bình thường, tăng "
    "Execution → Timeout của task này rồi lưu lại.\n"
    "- Nếu vẫn lỗi, thử đổi sang provider khác trong Settings để kiểm tra."
)


def _cancel_with_timeout(cancel: CancelFn, timeout_sec: Optional[int]) -> Tuple[CancelFn, Callable[[], bool]]:
    """Wrap ``cancel`` so it also fires once ``timeout_sec`` of wall-clock time
    elapses. ``timed_out()`` tells the caller whether THAT is why it stopped
    (vs. a real user Stop) — best-effort: a single provider HTTP call can
    still block up to its own internal read timeout if the connection goes
    fully silent, since a blocking network read can't be pre-empted from
    outside, but this catches the common "stuck for way too long" cases
    (slow trickle, stuck tool loop) at the task's own configured Timeout."""
    if not timeout_sec:
        return cancel, (lambda: False)
    deadline = time.monotonic() + timeout_sec
    state = {"timed_out": False}

    def wrapped() -> bool:
        if cancel():
            return True
        if time.monotonic() >= deadline:
            state["timed_out"] = True
            return True
        return False

    return wrapped, (lambda: state["timed_out"])


def _run_agent(ctx, task_type: str, prompt: str, out_dir: Path,
               emit: EmitFn, cancel: CancelFn, title: str = "",
               timeout_sec: Optional[int] = None,
               project: Optional[projects.Project] = None,
               admin_agent=None, provider_name: str = "", model: str = "",
               skill_slug: str = "") -> Tuple[str, bool, str]:
    """Run one cowork/co4e prompt and return ``(answer_text, timed_out,
    plan_incomplete_reason)``.

    The run's conversation session is saved to History IMMEDIATELY when the
    run starts (so the user sees at a glance that the task really is
    executing, without waiting for it to finish), re-saved after every
    assistant turn (live progress on reopen), and once more at the end —
    including on errors, where the partial conversation is exactly what the
    user needs to see. On a timeout, a notice + troubleshooting steps is
    appended as an assistant message so it shows up right in that chat, not
    just buried in error.txt.

    ``plan_incomplete_reason`` (see ``core/plan.py``) is non-empty when the
    agent DID create a checklist via ``update_plan`` but left it with a step
    not 'done' (still pending/running, or explicitly 'error') — the caller
    uses this to avoid reporting the task "done" when the agent's own
    checklist says the work wasn't actually finished."""
    from . import usage_tracker
    from .history import new_session_id
    from .plan import plan_incomplete_reason

    usage_tracker.set_context("task", title)   # Dashboard: cost per task
    # The task picks its own provider/model (blank = the machine's Settings
    # default, see state.build_provider_for). A legacy Admin-agent preset
    # (task.admin_agent_id), if still set on an older task, keeps working and
    # takes precedence — it pins the provider/model AND prepends instructions.
    if admin_agent is not None:
        from .admin_agents import build_agent_provider

        provider = build_agent_provider(ctx, admin_agent)
        agent_instructions = admin_agent.effective_prompt()
        if agent_instructions:
            prompt = f"{agent_instructions}\n\n{prompt}"
    elif provider_name or model:
        # An explicit per-task provider/model override.
        provider = ctx.build_provider_for(provider_name or None, model or None)
    else:
        # Neither overridden → the machine's own Settings default, exactly as before.
        provider = ctx.build_active_provider()
    # A chosen skill's instructions are prepended so this unattended run follows
    # them, mirroring how the interactive chat applies /skill.
    if skill_slug:
        from .skills import skill_prefix_for

        skill_text = skill_prefix_for(skill_slug)
        if skill_text:
            prompt = f"{skill_text}\n\n{prompt}"
    # This is an UNATTENDED run (no human watching to catch a half-finished
    # job) — push the agent to actually use the Plan checklist so completion
    # can be verified afterward, instead of just trusting "no exception".
    prompt = (
        "This runs unattended (Schedule Task) — no one is watching live. Use "
        "update_plan to track your steps and keep it accurate: mark a step "
        "'error' (not silently skip it) if it genuinely can't be completed.\n\n"
        f"{prompt}"
    )
    messages = [{"role": "user", "content": prompt}]
    session_id = new_session_id()
    project_id = project.project_id if project is not None else ""
    _save_history_session(ctx, task_type, title, messages, session_id, project_id)
    # Tell the scheduler the session now genuinely EXISTS on disk — it
    # refreshes History on this, not on the earlier "task_started" signal
    # (which fires before this worker thread even begins), so the running
    # task's conversation actually shows up in Cowork/Code while it runs.
    emit({"type": "history_ready", "session_id": session_id})

    last_plan_steps: List[Dict[str, str]] = []

    def emit_and_autosave(ev):
        emit(ev)
        if not isinstance(ev, dict):
            return
        if ev.get("type") == "assistant_done":
            _save_history_session(ctx, task_type, title, messages, session_id, project_id)
        elif ev.get("type") == "plan_set":
            last_plan_steps[:] = ev.get("steps") or []

    project_context = projects.project_context_text(project)
    watched_cancel, timed_out = _cancel_with_timeout(cancel, timeout_sec)
    try:
        if task_type == "cowork":
            from .chat_agent import run_cowork
            run_cowork(provider, messages, out_dir, emit_and_autosave, watched_cancel,
                      security_config=ctx.config, agent_role=agent_roles.TASK,
                      project_context=project_context)
        else:
            from .code_agent import run_code
            limits, block_network = agent_security.sandbox_settings(ctx.config)
            task_ctx = ToolContext(out_dir, resource_limits=limits, block_network=block_network,
                                   allow_url_fetch=agent_security.url_fetch_allowed(ctx.config),
                                   jira=ctx.config.data.get("jira"))
            run_code(provider, messages, task_ctx, PermissionGate("auto", agent_role=agent_roles.TASK),
                     emit_and_autosave, watched_cancel, security_config=ctx.config,
                     project_context=project_context)
        if timed_out() and not cancel():
            notice = _TIMEOUT_NOTICE_TMPL.format(timeout=timeout_sec)
            messages.append({"role": "assistant", "content": notice})
            emit_and_autosave({"type": "text", "delta": notice})
            emit_and_autosave({"type": "assistant_done", "content": notice})
    finally:
        _save_history_session(ctx, task_type, title, messages, session_id, project_id)
    incomplete = "" if (cancel() or timed_out()) else plan_incomplete_reason(last_plan_steps)
    return _last_assistant_text(messages), timed_out(), incomplete


def _run_script(command: str, out_dir: Path, timeout_sec: int) -> str:
    if not command.strip():
        raise RuntimeError("Script task has no command configured.")
    proc = subprocess.run(command, shell=True, cwd=str(out_dir),
                          capture_output=True, text=True, timeout=max(1, timeout_sec))
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    if proc.returncode != 0:
        raise RuntimeError(f"Script exited with code {proc.returncode}:\n{output[-2000:]}")
    return output


def execute_task(ctx, task: Dict[str, Any], run_id: str,
                 emit: Optional[EmitFn] = None, cancel: Optional[CancelFn] = None,
                 tasks_dir: Path = None) -> Dict[str, Any]:
    """Run ``task`` synchronously (call from a worker thread). Returns
    ``{"ok": bool, "output": str, "artifact": str, "error": str}`` and always
    writes the artifact files, even on failure."""
    emit = emit or (lambda ev: None)
    cancel = cancel or (lambda: False)
    adir = artifact_dir(task["task_id"], run_id)
    gen_dir = adir / "generated_files"
    project = projects.load_project(task.get("project_id")) if task.get("project_id") else None
    run_dir = project.workspace_dir() if project else gen_dir
    if project:
        run_dir.mkdir(parents=True, exist_ok=True)
    log_lines = [f"run_id: {run_id}", f"task: {task.get('title', '')}",
                 f"type: {task.get('task_type')}",
                 f"start: {datetime.now().isoformat(timespec='seconds')}"]
    ok, output_text, error = True, "", ""
    try:
        ttype = task.get("task_type", "manual")
        timeout = int(task.get("execution", {}).get("timeout_sec", 600) or 600)
        if ttype == "manual":
            output_text = "Manual task — nothing to execute."
        elif ttype == "script":
            output_text = _run_script(task.get("script_command", ""), gen_dir, timeout)
        elif ttype in ("cowork", "co4e_code"):
            max_files = int(ctx.config.data.get("attachments", {}).get("max_files", 10) or 0)
            prompt = _build_prompt(task, tasks_dir, project, max_files)
            admin_agent = None
            if task.get("admin_agent_id"):
                from .admin_agents import agents_admin_dir, load_agent

                admin_agent = load_agent(task["admin_agent_id"],
                                         agents_admin_dir(ctx.config.shared_dir))
            output_text, timed_out, plan_incomplete = _run_agent(
                ctx, ttype, prompt, run_dir, emit, cancel,
                title=task.get("title", ""), timeout_sec=timeout,
                project=project, admin_agent=admin_agent,
                provider_name=task.get("provider", ""), model=task.get("model", ""),
                skill_slug=task.get("skill_slug", ""))
            if timed_out:
                ok, error = False, f"Timed out after {timeout}s waiting for the AI model to respond."
            elif plan_incomplete:
                ok, error = False, plan_incomplete
        elif ttype == "flow":
            output_text = _run_flow(ctx, task, gen_dir, emit, cancel, tasks_dir, project=project)
        else:
            raise RuntimeError(f"Unknown task type: {ttype}")
    except Exception as exc:  # noqa: BLE001 — a task must never crash the scheduler
        ok, error = False, str(exc)
    log_lines.append(f"end: {datetime.now().isoformat(timespec='seconds')}")
    log_lines.append(f"status: {'success' if ok else 'failed'}")
    try:
        (adir / "output.md").write_text(_format_output_md(task, output_text), encoding="utf-8")
        (adir / "logs.txt").write_text("\n".join(log_lines), encoding="utf-8")
        if error:
            (adir / "error.txt").write_text(error, encoding="utf-8")
    except OSError:
        pass
    return {"ok": ok, "output": output_text, "artifact": str(adir), "error": error}


def _resolve_co4e_workflow(flow_id: str):
    """A task's ``flow.flow_id`` points at a saved Co4E flow. Return the
    ``co4e.Workflow`` or None (→ legacy inline steps)."""
    if not flow_id:
        return None
    from . import co4e
    return co4e.get_workflow(flow_id)


def _run_co4e_flow(ctx, task: Dict[str, Any], wf, gen_dir: Path,
                   emit: EmitFn, cancel: CancelFn, tasks_dir: Path = None) -> str:
    """Run a saved Co4E flow (node graph) as a scheduled task — the same
    wave-by-wave runner the Co4E tab uses, inside the app's sandbox + security
    framework (``co4e_runner`` calls ``run_cowork`` with the app config). The
    task's resolved input (manual text / attachments / previous-task output) is
    fed into the flow's entry steps automatically."""
    import copy

    from . import co4e_runner

    nodes = [copy.deepcopy(n) for n in wf.nodes]
    edges = list(wf.edges)
    if not nodes:
        raise RuntimeError("Co4E flow has no steps.")
    # Inject the task input into root steps (no predecessor) as extra context.
    input_text = resolve_input_text(task, tasks_dir)
    if input_text:
        targets = {e.target for e in edges}
        for n in nodes:
            if n.id not in targets:
                base = n.data.instructions or ""
                n.data.instructions = f"{base}\n\n--- Task input ---\n{input_text[:20000]}".strip()
    labels = {n.id: n.data.label for n in nodes}
    outputs: Dict[str, str] = {}

    def _emit(ev):
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "stage_text":
            emit({"type": "text", "delta": ev.get("delta", "")})
        elif t == "node_status" and ev.get("status") == "running":
            emit({"type": "text", "delta": f"\n▶ {labels.get(ev.get('node_id'), '')}\n"})
        elif t == "node_output":
            outputs[ev.get("node_id")] = ev.get("output", "")

    result = co4e_runner.run_workflow(ctx, nodes, edges, gen_dir, _emit, cancel, plan_mode=False)
    outputs = result or outputs
    parts = [f"## {labels.get(nid, nid)}\n{outputs[nid]}" for nid in
             (n.id for n in nodes) if outputs.get(nid)]
    return "\n\n".join(parts)


def _run_flow(ctx, task: Dict[str, Any], gen_dir: Path,
              emit: EmitFn, cancel: CancelFn, tasks_dir: Path = None,
              project: Optional[projects.Project] = None) -> str:
    """Run a task's flow. If ``flow.flow_id`` points at a saved/built-in Co4E
    flow, run that node graph (Co4E runner). Otherwise fall back to the task's
    own simple sequential steps; each step's output feeds the next step's
    prompt (previous_step_output chaining)."""
    wf = _resolve_co4e_workflow((task.get("flow") or {}).get("flow_id"))
    if wf is not None:
        return _run_co4e_flow(ctx, task, wf, gen_dir, emit, cancel, tasks_dir)
    steps = [s for s in task.get("flow", {}).get("steps", []) if s.get("enabled", True)]
    if not steps:
        raise RuntimeError("Flow task has no steps.")
    prev_output = resolve_input_text(task, tasks_dir)
    outputs = []
    for i, step in enumerate(steps, 1):
        if cancel():
            break
        emit({"type": "text", "delta": f"\n▶ Step {i}/{len(steps)}: {step.get('name', '')}\n"})
        prompt = step.get("prompt") or step.get("name") or ""
        if prev_output:
            prompt += f"\n\n--- Previous output ---\n{prev_output[-20000:]}"
        executor = step.get("executor", "cowork")
        if executor == "script":
            out = _run_script(step.get("prompt", ""), gen_dir,
                              int(task.get("execution", {}).get("timeout_sec", 600) or 600))
        elif executor in ("cowork", "co4e"):
            hint = _output_mode_hint(task)
            if hint:
                prompt += f"\n\n--- Output requirement ---\n{hint}"
            step_timeout = int(task.get("execution", {}).get("timeout_sec", 600) or 600)
            out, timed_out, plan_incomplete = _run_agent(
                ctx, "cowork" if executor == "cowork" else "co4e_code",
                prompt, gen_dir, emit, cancel,
                title=f"{task.get('title', '')} — {step.get('name', '')}",
                timeout_sec=step_timeout, project=project,
                provider_name=task.get("provider", ""), model=task.get("model", ""),
                skill_slug=task.get("skill_slug", ""))
            if timed_out:
                outputs.append(f"## Step {i}: {step.get('name', '')}\n{out}")
                raise RuntimeError(
                    f"Step '{step.get('name', '')}' timed out after {step_timeout}s "
                    "waiting for the AI model to respond.")
            if plan_incomplete:
                outputs.append(f"## Step {i}: {step.get('name', '')}\n{out}")
                raise RuntimeError(f"Step '{step.get('name', '')}': {plan_incomplete}")
        else:   # manual step — skipped in automated runs
            out = f"(manual step '{step.get('name', '')}' skipped)"
        outputs.append(f"## Step {i}: {step.get('name', '')}\n{out}")
        prev_output = out
    return "\n\n".join(outputs)
