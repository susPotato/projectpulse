"""Co4E runner — executes a workflow graph wave-by-wave.

Ported from nova's client-side wave runner: compute topological waves, run each
wave's stages in order, thread each wave's outputs into the next wave's prompts
(a node receives the concatenated outputs of its graph predecessors). Each stage
is one agent turn via ``chat_agent.run_cowork`` (full tool use, sandbox policy),
using the stage's own model/scope.

Runs SYNCHRONOUSLY — call it from a worker thread. Progress is reported through
an ``emit`` callback as dict events:
  {"type": "node_status", "node_id", "status"}   idle|running|done|error|planned
  {"type": "node_output", "node_id", "output"}   final text of a node
  {"type": "stage_text",  "node_id", "delta"}    streamed token (for the chat log)
  {"type": "run_done",    "ok": bool}
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import agent_roles
from .co4e import (
    STEP_DONE, STEP_ERROR, STEP_PLANNED, STEP_RUNNING, Edge, Node,
    compile_run_stages, stage_node_id,
)

EmitFn = Callable[[dict], None]
CancelFn = Callable[[], bool]


def _predecessors(nodes: List[Node], edges: List[Edge]) -> Dict[str, List[str]]:
    ids = {n.id for n in nodes}
    preds: Dict[str, List[str]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.source in ids and e.target in ids:
            preds[e.target].append(e.source)
    return preds


def _label_of(nodes: List[Node], node_id: str) -> str:
    for n in nodes:
        if n.id == node_id:
            return n.data.label
    return node_id


_MAX_ATTACH_CHARS = 100_000   # per step, across all its attachments


def _attachments_text(node, out_dir=None) -> str:
    """Read a step's attached files into its prompt. Handles every file type:
    MS Office / PDF / OpenDocument / text (via doc_extract), IMAGES (noted with
    their workspace path so a vision step can use them), and ZIP archives —
    which are auto-EXTRACTED into the workspace (``out_dir/attachments/<name>``)
    and whose contents are then read + processed. Best-effort; never fatal."""
    if node is None or not getattr(node.data, "attachments", None):
        return ""
    from pathlib import Path as _P

    from .doc_extract import extract_archive, extract_text, is_image, is_zip

    parts, budget = [], _MAX_ATTACH_CHARS

    def _read_into(path, label, indent=""):
        nonlocal budget
        name = _P(path).name
        if is_image(path):
            parts.append(f'{indent}--- {label} "{name}" (image at {path}) ---')
            return
        try:
            text, note = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            text, note = None, str(exc)
        if text:
            chunk = text[:budget]
            budget -= len(chunk)
            parts.append(f'{indent}--- {label} "{name}" ---\n{chunk}')
        else:
            parts.append(f'{indent}--- {label} "{name}" (could not read: {note or "unknown"}) ---')

    for path in node.data.attachments:
        if budget <= 0:
            break
        name = _P(path).name
        if is_zip(path):
            dest = (_P(out_dir) / "attachments" / _P(name).stem) if out_dir else _P(path).with_suffix("")
            files = extract_archive(path, dest)
            parts.append(f'--- Attached archive "{name}" extracted to workspace: '
                         f'{dest} ({len(files)} files — read/edit them there) ---')
            for f in files:
                if budget <= 0:
                    break
                _read_into(f, "Extracted file", indent="  ")
        else:
            _read_into(path, "Attached file")
    return "\n\n".join(parts)


def _last_assistant_text(messages: List[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return str(m["content"])
    return ""


# Prepended to every EXECUTION stage (not plan-only runs) so each step behaves
# like an autonomous coding agent (Claude Code / opencode style): plan first,
# then carry the work out end-to-end using its skills, context and attached files,
# and verify before finishing. run_cowork already runs the agentic tool-use loop
# (update_plan + read/write/edit/run) to completion; this directive makes the
# plan-then-execute + quality intent explicit per step.
_STEP_EXEC_DIRECTIVE = (
    "You are an autonomous agent executing ONE step of a larger workflow. Work "
    "like a senior engineer using a coding CLI:\n"
    "1) FIRST call the update_plan tool with a short checklist of what this step "
    "needs (2–5 concrete items).\n"
    "2) THEN carry the plan out end-to-end IN THE WORKSPACE — read/create/edit "
    "files and run commands as needed. Use the attached skills, the extra context "
    "and the attached files/upstream outputs below as your source material.\n"
    "3) Keep going until the step's goal is fully met — do not stop half-done or "
    "just describe what you would do; actually produce the deliverable.\n"
    "4) RECOVER from tool errors instead of giving up: if a tool call fails (e.g. a "
    "path doesn't exist, a directory is missing, a command errors), adapt — create "
    "the folder/file, correct the path or arguments, or try another approach — and "
    "continue. A single failed tool call is NOT the end of the step.\n"
    "5) Before finishing, VERIFY your work (if it's code, make sure it runs / is "
    "correct) and mark plan items done.\n\n"
)


_VERIFY_PROMPT = (
    "You just finished this step. Self-review your work against the step's goal, "
    "and if it involved code, CHECK that it actually runs / is correct.\n\n"
    "Step goal:\n{goal}\n\nYour result so far:\n{output}\n\n"
    "If the work fully meets the goal and is correct, reply with exactly: VERIFIED\n"
    "Otherwise FIX the problems now (edit/rewrite files, re-run as needed) and reply "
    "with the corrected final result."
)


def _run_self_verify(ctx, node, output: str, goal: str, out_dir: Path, scope,
                     emit: EmitFn, cancel: CancelFn) -> str:
    """After a step finishes, have the agent self-evaluate (and fix) its own work
    BEFORE the next step runs. Repeats up to the step's ``max_verify_rounds`` or
    until it replies VERIFIED. Runs with the step's own permission scope, inside
    the sandbox/security framework (via ``run_cowork``)."""
    from . import agent_roles
    from .chat_agent import run_cowork

    step = node.data
    rounds = max(1, int(getattr(step, "max_verify_rounds", 1) or 1))
    for r in range(rounds):
        if cancel():
            break
        emit({"type": "stage_text", "node_id": node.id,
              "delta": f"\n🔍 Self-verify {r + 1}/{rounds}…\n"})
        prompt = _VERIFY_PROMPT.format(goal=goal or step.label, output=output[:12000])
        messages = [{"role": "user", "content": prompt}]
        provider = ctx.build_provider_for(None, step.model or None)

        def _emit_stage(ev, _nid=node.id):
            if isinstance(ev, dict) and ev.get("type") == "text":
                emit({"type": "stage_text", "node_id": _nid, "delta": ev.get("delta", "")})
            elif isinstance(ev, dict) and ev.get("type") == "tool_proposed":
                prev = ev.get("preview") or {}
                if isinstance(prev, dict) and prev.get("kind") == "diff":
                    emit({"type": "node_diff", "node_id": _nid,
                          "title": prev.get("title") or ev.get("name", ""),
                          "diff": prev.get("text", "")})

        try:
            run_cowork(provider, messages, out_dir, _emit_stage, cancel,
                       security_config=ctx.config, agent_role=agent_roles.TASK,
                       allowed_tools=scope, run_to_completion=True, enforce_rules=False)
        except Exception as exc:  # noqa: BLE001 — verification must never kill the run
            emit({"type": "stage_text", "node_id": node.id, "delta": f"\n[verify error: {exc}]\n"})
            break
        reply = _last_assistant_text(messages).strip()
        # "VERIFIED" (possibly with a trailing note) → accept the current output.
        if reply[:8].upper().startswith("VERIFIED"):
            emit({"type": "stage_text", "node_id": node.id, "delta": "✓ verified\n"})
            break
        if reply:                       # the agent revised the work → take the fix
            output = reply
    return output


def _usage_delta(base: dict, config) -> dict:
    """Tokens + USD cost recorded on THIS worker thread since ``base`` (an earlier
    ``usage_tracker.accumulated()`` snapshot) — i.e. one node's own usage. Cost is
    priced per-model over just the turns added since ``base``."""
    from . import model_pricing as mp, usage_tracker as ut
    cur = ut.accumulated()
    new_events = cur["events"][len(base.get("events", [])):]
    cost = sum(mp.turn_cost_usd(e.get("model", ""), e.get("in", 0), e.get("out", 0), config)
               for e in new_events)
    return {
        "in": max(0, cur["in"] - base.get("in", 0)),
        "out": max(0, cur["out"] - base.get("out", 0)),
        "cache": max(0, cur["cache"] - base.get("cache", 0)),
        "cost_usd": cost,
    }


def run_workflow(ctx, nodes: List[Node], edges: List[Edge], out_dir: Path,
                 emit: EmitFn, cancel: CancelFn, *, plan_mode: bool = False,
                 skill_map: Dict[str, str] = None,
                 only_nodes: Optional[set] = None,
                 seed_outputs: Optional[Dict[str, str]] = None,
                 usage_label: Optional[str] = None) -> Dict[str, str]:
    """Run the graph (or just ``only_nodes`` + their compiled stages). Returns
    ``{node_id: output_text}``. Never raises — a failing stage is reported as an
    error status and the run continues with whatever context it has.

    ``seed_outputs`` pre-loads outputs of already-run predecessor nodes so a
    single step run (Manual mode / "run this step") still receives its upstream
    context — the adjacent previous step's output is fed in automatically, with
    no manual wiring.

    ``usage_label`` tags this run's token usage for the Dashboard (source
    ``co4e``) and enables per-step token/cost accounting: each ``node_output``
    event carries a ``usage`` block (↓in ↑out ▤ctx $cost) the flow chat renders,
    exactly like Cowork's per-message footer."""
    from . import usage_tracker as ut
    from .chat_agent import run_cowork

    emit = emit or (lambda ev: None)
    cancel = cancel or (lambda: False)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Attribute Co4E turns to the Dashboard + start this thread's usage
    # accumulator so we can diff per-step token/cost below.
    ut.set_context("co4e", usage_label or "flow")
    ut.begin_accumulation()
    skill_map = skill_map or {}
    preds = _predecessors(nodes, edges)
    outputs: Dict[str, str] = dict(seed_outputs or {})     # node_id -> final text

    run_nodes = [n for n in nodes if only_nodes is None or n.id in only_nodes]
    if not run_nodes:
        emit({"type": "run_done", "ok": True})
        ut.end_accumulation()
        return outputs

    by_id = {n.id: n for n in nodes}

    # Group compiled stages by wave, preserving per-node context threading.
    def extra_context_for(node_id: str) -> Dict[str, str]:
        parts = []
        att = _attachments_text(by_id.get(node_id), out_dir)
        if att:
            parts.append(att)
        for p in preds.get(node_id, []):
            if outputs.get(p):
                parts.append(f'--- Output of previous step "{_label_of(nodes, p)}" ---\n{outputs[p]}')
        return {node_id: "\n\n".join(parts)} if parts else {}

    # Compile once to discover wave ordering, then (re)build each node's prompt
    # with fresh upstream context right before it runs.
    all_stages = compile_run_stages(run_nodes, edges, plan_mode=plan_mode, skill_map=skill_map)
    waves = sorted({s.wave for s in all_stages})
    ok_all = True
    ran: set = set()      # node ids already executed — never run a node twice
                          # (a parallel node appears in both its sub-agent wave
                          # AND its join wave, which used to double-run it)

    for wave in waves:
        if cancel():
            break
        wave_node_ids = [n.id for n in run_nodes
                         if any(s.wave == wave and s.node_id == n.id for s in all_stages)]
        for node_id in wave_node_ids:
            if cancel():
                break
            if node_id in ran:
                continue
            ran.add(node_id)
            node = next(n for n in run_nodes if n.id == node_id)
            # Recompile THIS node's stages with current upstream outputs.
            stages = [s for s in compile_run_stages(
                [node], edges, extra_context=extra_context_for(node_id),
                plan_mode=plan_mode, skill_map=skill_map)]
            emit({"type": "node_status", "node_id": node_id, "status": STEP_RUNNING})
            usage_base = ut.accumulated()   # token/cost baseline for THIS node
            node_out_parts = []
            sub_outputs: List[str] = []   # sub-agent outputs of THIS node → its join
            join_output: Optional[str] = None
            node_ok = True
            for st in stages:
                if cancel():
                    node_ok = False
                    break
                provider = ctx.build_provider_for(None, st.model or None)
                is_join = st.id.endswith("__pjoin")
                prompt = st.prompt
                if not plan_mode:
                    # Auto plan-then-execute for real runs (plan_mode keeps its
                    # own plan-only preamble from compile_run_stages).
                    prompt = _STEP_EXEC_DIRECTIVE + prompt
                if is_join and sub_outputs:
                    # Feed the sub-agents' results to the coordinator so it can
                    # actually consolidate them — this is what "combine the agents
                    # into one result" needs (the prompt alone doesn't carry them).
                    joined = "\n\n".join(
                        f'--- Output of sub-agent {i + 1} ---\n{o}'
                        for i, o in enumerate(sub_outputs) if o)
                    prompt = f"{prompt}\n\n{joined}"
                messages = [{"role": "user", "content": prompt}]

                def _emit_stage(ev, _nid=node_id):
                    if not isinstance(ev, dict):
                        return
                    t = ev.get("type")
                    if t == "text":
                        emit({"type": "stage_text", "node_id": _nid, "delta": ev.get("delta", "")})
                    elif t == "plan_set":
                        # surface the step's own plan inline in the flow conversation
                        emit({"type": "node_plan", "node_id": _nid, "steps": ev.get("steps") or []})
                    elif t == "tool_proposed":
                        # Surface a before/after diff (write/edit/save) in the flow log,
                        # like Claude, so what changed is easy to query.
                        prev = ev.get("preview") or {}
                        if isinstance(prev, dict) and prev.get("kind") == "diff":
                            emit({"type": "node_diff", "node_id": _nid,
                                  "title": prev.get("title") or ev.get("name", ""),
                                  "diff": prev.get("text", "")})
                    elif t == "tool_result":
                        emit({"type": "node_tool", "node_id": _nid,
                              "name": ev.get("name", ""), "ok": bool(ev.get("ok", True))})

                try:
                    run_cowork(provider, messages, out_dir, _emit_stage, cancel,
                               security_config=ctx.config, agent_role=agent_roles.TASK,
                               allowed_tools=st.scope, run_to_completion=True, enforce_rules=False)
                    out = _last_assistant_text(messages)
                except Exception as exc:  # noqa: BLE001 — one stage must not kill the run
                    node_ok = False
                    out = f"[error: {exc}]"
                node_out_parts.append(out)
                if is_join:
                    join_output = out
                else:
                    sub_outputs.append(out)
            # When a coordinator (join) ran, ITS consolidated result is the node's
            # output downstream; otherwise concatenate the stage outputs.
            output = join_output if join_output is not None else \
                "\n\n".join(p for p in node_out_parts if p)
            # Self-verify: the step self-evaluates (and fixes) its own work BEFORE
            # the next step runs — only when it succeeded, in real (non-plan) runs.
            if node_ok and not plan_mode and getattr(node.data, "self_verify", False):
                from .co4e import PRESET_SCOPES
                scope = PRESET_SCOPES.get(node.data.permission_preset)
                goal = node.data.instructions or node.data.label
                if not cancel():
                    output = _run_self_verify(ctx, node, output, goal, out_dir, scope, emit, cancel)
            outputs[node_id] = output
            emit({"type": "node_output", "node_id": node_id, "output": output,
                  "usage": _usage_delta(usage_base, ctx.config)})
            status = STEP_PLANNED if plan_mode else (STEP_DONE if node_ok else STEP_ERROR)
            emit({"type": "node_status", "node_id": node_id, "status": status})
            ok_all = ok_all and node_ok

    # A node that never ran (unexpected wave/skip) must NOT let the run report a
    # clean "done" — mark any un-run node as error so status reflects reality.
    if not cancel():
        skipped = [n.id for n in run_nodes if n.id not in ran]
        for nid in skipped:
            emit({"type": "node_status", "node_id": nid, "status": STEP_ERROR})
        if skipped:
            ok_all = False

    emit({"type": "run_done", "ok": ok_all and not cancel()})
    ut.end_accumulation()
    return outputs
