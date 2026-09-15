"""Chat loops for the Cowork tab.

``run_chat`` is a plain streaming chat. ``run_cowork`` additionally exposes a
``save_file`` tool so the agent can produce real files (e.g. export an answer to
.md/.txt/.csv) into an output folder — those appear in the Output box.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..providers.base import Provider, ToolSpec
from . import agent_roles
from . import agent_security
from .code_agent import (
    _apply_project_context, _apply_security_rules, _apply_skills, _call_provider_with_recovery,
)
from .deps import _can_pip
from .java_runtime import find_java
from .security_rules import load_rules
from .plan import UPDATE_PLAN_SPEC, normalize_plan_steps
from .skills import active_skills_text
from .tools import TOOL_SPECS, ToolContext, _snapshot, describe_action, execute_tool

# Generator / helper scripts — never a final deliverable in Cowork's output.
_SCRIPT_EXTS = {".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bat", ".ps1", ".rb", ".pl"}

EmitFn = Callable[[Dict[str, Any]], None]
CancelFn = Callable[[], bool]


COWORK_SYSTEM_PROMPT = (
    "You are Cowork Local — a friendly internal assistant. Answer concisely and "
    "accurately in the user's language. When unsure, say so.\n"
    "When the user attaches files, their content is inlined below in the message under "
    "'[Attachments]'. ALWAYS read and use the attached file content to answer the request.\n"
    "When workspace/output folder files are present, their content is inlined under "
    "'[Workspace files]'. These are existing files in the output folder — treat them as "
    "input data. ALWAYS read and use them to answer the request. Reference specific data, "
    "tables, or sections from these files in your response.\n"
    "If any file content cannot be read, tell the user which file failed."
)

COWORK_TOOL_PROMPT = (
    COWORK_SYSTEM_PROMPT
    + "\n\nWhen files are present (shown as '[Attachments]' and '[Workspace files]' in the message), "
    "you MUST read their content carefully and use it to fulfill the request. Extract data, "
    "summarize, analyze, or transform the file content as requested. Reference specific "
    "sections/values from the files in your answer.\n\n"
    "A question or analysis ABOUT attached files/links (e.g. summarize, compare, extract a "
    "number, explain, answer a question) is answered DIRECTLY IN THE CHAT as text — do NOT "
    "call save_file or write a generator script for it. Only create an actual file when the "
    "user explicitly asks you to produce/export/save one (e.g. 'xuất ra file', 'tạo file', "
    "'lưu thành…', 'export as .docx/.xlsx/.pptx', naming a document/report/spreadsheet/deck to "
    "deliver) — reading and reasoning about a file's content is not, by itself, a request to "
    "create a new file.\n\n"
    "You can create real files for the user in the output folder, in WHATEVER format the "
    "user asks for (they may name a format/extension directly, e.g. 'xuất ra excel', 'as a "
    ".pptx', or describe styling, e.g. 'dạng bảng' (as a table), 'có màu' (with color/styling)"
    " — always honor the exact format and styling requested; if none is stated, pick the format "
    "that best fits the content (tabular/numeric data → .xlsx or a Markdown table; long-form "
    "text → .md or .docx; slides → .pptx) and briefly say which you chose.\n"
    "• For text (.md/.txt/.csv/.json/.html): call save_file(filename, content) ONCE with the "
    "FINAL content — a 'table' request for these means a real Markdown/HTML table, not a bullet "
    "list. Re-saving the same filename overwrites in place.\n"
    "• For a plain-data EXCEL file (.xlsx) with no special styling, just call "
    "save_file('name.xlsx', <content>) where content is CSV, a tab-separated table, a Markdown "
    "table, or JSON rows — it is turned into a REAL workbook automatically (do NOT write raw text "
    "to a .xlsx yourself, and you don't need a script for the simple case).\n"
    "• For a document/deck/PDF/image, OR a spreadsheet needing styling/formulas/multiple sheets "
    "(.docx/.pptx/.pdf/png…, or .xlsx with colors, cell formatting, charts): write a short Python "
    "script and run it with run_command, actually applying that styling via the library's API "
    "(e.g. openpyxl cell.fill/font colors, python-docx run.font.color/table styles) rather than "
    "only describing it. Install any needed package yourself with install_package (e.g. "
    "python-pptx, openpyxl, python-docx) — never ask the user to install.\n"
    "Put the generator script and ALL temporary files in a '.scratch/' subfolder, run it so the "
    "FINAL file lands DIRECTLY in the output folder root, then delete '.scratch/'. Save the final "
    "file(s) at the output root — do NOT create any other sub-folder (no per-session, per-chat, "
    "per-task or per-date folders). Only the final requested file(s) may remain — never leave "
    "generator scripts or intermediate files in the output.\n"
    "If a command fails, read the error, fix it, and retry until the file is produced; then "
    "report the final file name. For plain conversation, do NOT call any tool.\n"
    "For any task that takes more than one step, FIRST call update_plan with a short checklist "
    "(2–6 short imperative steps, each status 'pending'); then, as you work, call update_plan "
    "again to mark the current step 'running' and finished steps 'done'. Skip the plan for a "
    "trivial one-line reply.\n"
    "Do NOT ask the user clarifying or confirmation questions — make reasonable assumptions and "
    "carry out the ORIGINAL request end-to-end on your own, then report only the final result. "
    "Only show the final deliverable; never present intermediate scripts or temporary files."
)

# Appended to COWORK_TOOL_PROMPT only when java_runtime.find_java() finds a JVM
# on this machine (opendataloader-pdf wraps a Java CLI tool) — see run_cowork.
OPENDATALOADER_PDF_PROMPT = (
    "For an EXPLICIT request to convert/extract a PDF's structure to JSON (tables, "
    "headings, reading order — not just a flat text dump): install_package("
    "'opendataloader-pdf'), then run a short script calling "
    "opendataloader_pdf.convert(input_path=[pdf_path], output_dir=output_dir, "
    "format='json'). For a non-PDF source, first convert it to PDF with a headless "
    "LibreOffice command (soffice --headless --convert-to pdf --outdir <dir> <file>), "
    "then run opendataloader-pdf on the resulting PDF."
)

SAVE_FILE_SPEC = ToolSpec(
    name="save_file",
    description=("Save plain-text content to a file (e.g. .md, .txt, .csv, .json, .html) when "
                 "the user asks. Use real Markdown/HTML table syntax when a table is requested."),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "File name with extension"},
            "content": {"type": "string", "description": "Full file content"},
        },
        "required": ["filename", "content"],
    },
)

# Strip ONLY the characters that are actually invalid in a file name on
# Windows/macOS/Linux (path separators, wildcards, reserved punctuation and control
# chars). Everything else — including Unicode letters like Vietnamese "Báo cáo" or
# Japanese/中文 — is kept, so the file name stays readable and reflects the content
# instead of turning accents into underscores.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _safe_filename(name: str) -> str:
    base = Path(str(name)).name.strip()
    base = _UNSAFE.sub("_", base).strip(" _.") or "output.txt"
    if "." not in base:
        base += ".txt"
    return base


def _titled_filename(title: str, agent_filename: str) -> str:
    """Name the output after the chat title, keeping the agent's extension."""
    ext = Path(str(agent_filename)).suffix or ".md"
    # Sanitize the WHOLE title (don't run Path().name on it — a title may contain
    # "/" or ":" which would wrongly truncate it; _UNSAFE turns those into "_").
    base = _UNSAFE.sub("_", str(title).strip()).strip(" _.") if title else ""
    base = base[:80].strip(" _.")   # keep names to a sane length
    if not base:
        base = Path(str(agent_filename)).stem or "output"
    return base + ext


def _structure_summary(filename: str, content: str) -> str:
    """One line describing the SHAPE of content about to be saved (row/column
    counts, JSON keys, heading outline...) so the preview shown before the
    write lets the user spot a wrong format — e.g. asked for a table, got a
    bullet list — without having to open the file afterwards."""
    ext = Path(filename).suffix.lower()
    lines = content.splitlines()
    if ext == ".json":
        import json
        try:
            data = json.loads(content) if content.strip() else None
        except (ValueError, TypeError):
            return "[Structure] JSON — could not parse (check syntax before relying on it)"
        if isinstance(data, list):
            return f"[Structure] JSON array — {len(data)} item(s)"
        if isinstance(data, dict):
            keys = ", ".join(list(data.keys())[:8])
            return f"[Structure] JSON object — keys: {keys}"
        return "[Structure] JSON"
    if ext == ".csv":
        rows = [ln for ln in lines if ln.strip()]
        cols = rows[0].count(",") + 1 if rows else 0
        return f"[Structure] CSV — {max(len(rows) - 1, 0)} data row(s) × {cols} column(s)"
    table_rows = [ln for ln in lines if ln.strip().startswith("|")]
    if len(table_rows) >= 2:
        cols = max(table_rows[0].count("|") - 1, 0)
        return f"[Structure] Markdown table — {len(table_rows) - 2} data row(s) × {cols} column(s)"
    headings = [ln.lstrip("# ").strip() for ln in lines if ln.lstrip().startswith("#")]
    if headings:
        outline = " / ".join(headings[:5]) + (" / …" if len(headings) > 5 else "")
        return f"[Structure] {len(lines)} line(s), {len(headings)} heading(s): {outline}"
    return f"[Structure] {len(lines)} line(s), {len(content)} character(s) of plain text"


def _unique_path(folder: Path, name: str) -> Path:
    """A non-colliding path for ``name`` inside ``folder`` (adds ' (2)', ' (3)'…)."""
    dest = folder / name
    if not dest.exists():
        return dest
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 2
    while True:
        cand = folder / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def _cleanup_cowork_intermediates(output_dir: Path, before: Dict[str, Any],
                                  cancelled: bool = False):
    """Tidy the output folder so it keeps ONLY the final deliverable(s), flat.

    Runs even on abrupt stop. Three jobs:
      1. delete the ``.scratch`` sandbox;
      2. delete generator scripts created this turn (once a real file exists);
      3. FLATTEN — move any deliverable the agent wrote into a sub-folder up to
         the output root, then remove the emptied sub-folders. This guarantees
         every file lands directly in the single configured Output folder and the
         app never accumulates per-session / per-task sub-folders.

    Returns ``(removed, moved)`` where ``removed`` is a list of paths the UI should
    drop and ``moved`` is a list of ``(old_path, new_path)`` pairs."""
    import shutil

    removed: List[str] = []
    moved: List[tuple] = []
    scratch = output_dir / ".scratch"
    if scratch.exists():
        # A deliverable the agent's generator script wrote INSIDE .scratch
        # (instead of the output root) must be rescued before the sandbox is
        # wiped — otherwise it's destroyed with no trace and no error shown.
        for p in scratch.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in _SCRIPT_EXTS:
                removed.append(str(p))
                continue
            try:
                dest = _unique_path(output_dir, p.name)
                p.replace(dest)
                moved.append((str(p), str(dest)))
            except OSError:
                removed.append(str(p))
        shutil.rmtree(scratch, ignore_errors=True)
    after = _snapshot(output_dir)
    created = [Path(p) for p, v in after.items() if before.get(p) != v]
    scripts = [p for p in created if p.suffix.lower() in _SCRIPT_EXTS]
    deliverables = [p for p in created if p.suffix.lower() not in _SCRIPT_EXTS]
    # Drop generator scripts when a real deliverable was produced, or on a stop.
    if cancelled or deliverables:
        for s in scripts:
            try:
                s.unlink()
                removed.append(str(s))
            except OSError:
                pass
    # Flatten: pull every deliverable out of any sub-folder into the output root.
    try:
        root = output_dir.resolve()
    except OSError:
        root = output_dir
    for p in deliverables:
        try:
            if not p.exists() or p.resolve().parent == root:
                continue  # missing or already flat
            dest = _unique_path(output_dir, p.name)
            p.replace(dest)
            moved.append((str(p), str(dest)))
        except OSError:
            pass
    # Remove every empty sub-folder left under the Output root (e.g. a per-session
    # folder the agent created). rmdir only deletes EMPTY dirs, so a deliverable is
    # never lost; deepest dirs first so emptied parents collapse in the same pass.
    try:
        subdirs = sorted((q for q in output_dir.rglob("*") if q.is_dir()),
                         key=lambda q: len(q.parts), reverse=True)
        for d in subdirs:
            try:
                d.rmdir()
            except OSError:
                pass
    except OSError:
        pass
    return removed, moved


def _do_save_file(output_dir: Path, title: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """save_file handler — overwrite in place so only the final text file remains."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        fname = _titled_filename(title, args.get("filename", "output.txt"))
        target = output_dir / fname
        content = str(args.get("content", ""))
        # A .xlsx is a binary package — build a REAL workbook from the content
        # (CSV/TSV/Markdown-table/JSON) instead of writing raw text (which corrupts it).
        if target.suffix.lower() in (".xlsx", ".xlsm"):
            from . import xlsx_write
            if xlsx_write.build_xlsx_from_text(target, content):
                return {"ok": True, "output": f"Saved spreadsheet {target.name}.", "path": str(target)}
            return {"ok": False, "output": "Could not build the .xlsx (openpyxl unavailable) — "
                    "save as .csv instead, or write a generator script."}
        target.write_text(content, encoding="utf-8")
        return {"ok": True, "output": f"Saved {target.name}.", "path": str(target)}
    except OSError as exc:
        return {"ok": False, "output": f"Save failed: {exc}"}


def run_chat(
    provider: Provider,
    messages: List[Dict[str, Any]],
    emit: EmitFn,
    cancel: Optional[CancelFn] = None,
) -> Dict[str, Any]:
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": COWORK_SYSTEM_PROMPT})
    # Rulebase: always attach security rules so the agent follows them every turn
    _apply_security_rules(messages, load_rules())

    def on_text(piece: str) -> None:
        emit({"type": "text", "delta": piece})

    def on_reasoning(piece: str) -> None:
        # Stream the model's reasoning so the UI can show a live, collapsible
        # "Thinking" box (and keep the indicator active).
        emit({"type": "reasoning", "delta": piece})

    assistant = provider.chat(messages, tools=None, on_text=on_text, cancel=cancel,
                              on_reasoning=on_reasoning)
    messages.append(assistant)
    if not (assistant.get("content") or "").strip() and not assistant.get("tool_calls"):
        # Reasoning-only reply (model thought but produced no answer) — never leave
        # the user with a blank bubble.
        emit({"type": "text", "delta": "*(model returned only its reasoning — try rephrasing)*"})
    emit({"type": "assistant_done", "content": assistant.get("content", "")})
    return assistant


def run_cowork(
    provider: Provider,
    messages: List[Dict[str, Any]],
    output_dir: Path,
    emit: EmitFn,
    cancel: Optional[CancelFn] = None,
    max_steps: int = 30,
    title: str = "",
    extra_tools: Optional[List[ToolSpec]] = None,
    extra_executor=None,
    project_context: str = "",
    security_config=None,
    gate=None,
    agent_role: str = agent_roles.COWORK,
    allowed_tools: Optional[List[str]] = None,
    run_to_completion: bool = False,
    completion_max_steps: int = 200,
    enforce_rules: bool = True,
) -> List[Dict[str, Any]]:
    """Cowork chat that can produce real files.

    ``agent_role`` (see ``agent_roles.py``) tags every tool call this run
    makes in the audit log — it defaults to the interactive Cowork tab's own
    role, but Schedule Task overrides it to ``agent_roles.TASK`` for
    ``cowork``-type tasks so they aren't misattributed to the Cowork tab.

    ``gate`` (a ``PermissionGate``, Sandbox Security Layer — Permission
    Management), when given, is asked to approve every ``run_command``/
    ``install_package`` call before it executes — same mechanism
    ``code_agent.run_code`` already uses. ``None`` (the default) preserves
    the pre-existing behavior: Cowork auto-runs without asking.

    ``save_file`` writes text directly; for documents/spreadsheets/decks the agent
    writes and runs a generator script (file/command/install tools), keeping only
    the final deliverable in the output folder (helper scripts live under
    ``.scratch/`` which the UI hides and the agent deletes when done).

    ``extra_tools``/``extra_executor`` plug in extra capabilities the same way
    ``code_agent.run_code`` does (e.g. signed-in Microsoft 365 connectors) —
    any tool call whose name is in ``extra_tools`` is routed to
    ``extra_executor(name, args)`` instead of the built-in file/command tools.

    ``security_config`` is the app's ``AppConfig`` — when its
    ``agent_security`` layers are enabled, the request is checked (see
    ``core.agent_security.enforce_prompt``) before the loop starts, and every
    ``run_command``/``install_package`` call is checked again
    (``enforce_command``) right before it executes. ``None`` (the default,
    e.g. headless callers that don't pass one) disables both checks.

    ``run_to_completion`` (used by Co4E flow steps) lifts the tool-use budget
    from ``max_steps`` to ``completion_max_steps`` so a single step keeps
    working through as many tool calls as its instruction needs and finishes
    the task, instead of being cut off at 30 turns mid-work. The turn still
    ends naturally the moment the model stops calling tools; the higher number
    is only a runaway safety ceiling, and hitting it emits a visible note."""
    from . import audit_log

    cancel = cancel or (lambda: False)
    output_dir.mkdir(parents=True, exist_ok=True)
    limits, block_network = agent_security.sandbox_settings(security_config)
    ctx = ToolContext(output_dir, flatten_writes=True,  # keep every file in the Output root
                      resource_limits=limits, block_network=block_network,
                      allow_url_fetch=agent_security.url_fetch_allowed(security_config),
                      jira=(security_config.data.get("jira") if security_config else None))
    extra_tools = extra_tools or []
    extra_names = {t.name for t in extra_tools}
    # update_plan drives the Plan panel (above Output); it produces no file.
    # Built-in tools the admin disabled (Monitoring → Tools) are filtered out.
    from .tools import enabled_tool_specs
    tool_specs = [SAVE_FILE_SPEC, UPDATE_PLAN_SPEC] + enabled_tool_specs(security_config) + extra_tools
    # Permission scope (Co4E steps / any caller): restrict the ADVERTISED tools
    # to ``allowed_tools`` so e.g. a "read-only" step literally cannot write, run
    # commands or install packages. update_plan is always kept (no side effects);
    # extra_tools (signed-in connectors) are left as-is — scope governs the
    # built-in file/command capability, not opted-in external connectors.
    if allowed_tools is not None:
        allow = set(allowed_tools) | {"update_plan"} | extra_names
        tool_specs = [t for t in tool_specs if t.name in allow]
    if not messages or messages[0].get("role") != "system":
        prompt = COWORK_TOOL_PROMPT
        if any(n.startswith("ms365_") for n in extra_names):   # matches MCP "ms365__*" too
            prompt += ("\nThe user has signed in to Microsoft 365 and enabled some ms365__* tools "
                       "(Outlook / Teams / OneDrive / SharePoint / meeting transcripts, via the "
                       "built-in MS365 MCP server). Use them whenever the request involves that "
                       "data — don't say you can't access it.")
        if find_java() is not None and _can_pip():
            # opendataloader-pdf wraps a Java CLI AND is install_package'd on
            # first use — only mention this capability when BOTH the JVM is
            # present and installing packages is actually possible (pip is
            # disabled in a frozen .exe build), so the agent never gets
            # steered into a command that's guaranteed to fail on this machine.
            prompt += "\n\n" + OPENDATALOADER_PDF_PROMPT
        messages.insert(0, {"role": "system", "content": prompt})
    # Enabled skills are followed in Cowork too (same as the Code tab).
    _apply_skills(messages, active_skills_text())
    _apply_security_rules(messages, load_rules())
    # Claude-Projects-style shared context: every thread of a project follows it.
    _apply_project_context(messages, project_context)
    # Active guardrail — reviews the request itself (not just a prompt hint)
    # and can refuse to proceed at all. Raises SecurityBlocked on a violation.
    # ``enforce_rules=False`` (Co4E flow/chat) skips the rulebase check: those runs
    # are already confined to the workspace sandbox, so editing/commenting code
    # inside it isn't rule-restricted.
    if enforce_rules:
        agent_security.enforce_prompt(provider, messages, security_config, emit)

    before = _snapshot(output_dir)
    # Flow steps run to completion (their instruction may need many tool calls);
    # interactive Cowork/Code keep the tight 30-turn cap. The turn still ends the
    # instant the model stops calling tools — this is only the runaway ceiling.
    effective_max_steps = completion_max_steps if run_to_completion else max_steps
    completed_naturally = False
    try:
        from . import context_budget
        for _ in range(effective_max_steps):
            if cancel():
                break
            # Auto-compress when nearing the model's context budget (~80%) — keeps
            # long Cowork / Co4E conversations (and tool-heavy turns) from
            # overflowing. Summarizes old turns in place; no-op when off/short.
            context_budget.maybe_compact(provider, messages, security_config,
                                         emit=emit, cancel=cancel)

            def on_text(piece: str) -> None:
                emit({"type": "text", "delta": piece})

            def on_reasoning(piece: str) -> None:
                # Stream reasoning → live collapsible "Thinking" box + indicator.
                emit({"type": "reasoning", "delta": piece})

            assistant = _call_provider_with_recovery(provider, messages, tool_specs, on_text,
                                                     cancel, on_reasoning)
            messages.append(assistant)

            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls and not (assistant.get("content") or "").strip():
                # Reasoning-only reply with no answer and no tool call — surface a
                # short note so the turn never ends on a blank bubble. Written into
                # ``assistant["content"]`` itself (not just emitted) so it also
                # lands in ``messages`` — otherwise a Schedule Task run (which has
                # no live UI watching ``emit``) reads back an empty final answer
                # and its output.md ends up saying "(no output)".
                assistant["content"] = "*(model returned only its reasoning — try rephrasing)*"
                emit({"type": "text", "delta": assistant["content"]})
            emit({"type": "assistant_done", "content": assistant.get("content", "")})

            if not tool_calls:
                completed_naturally = True
                break

            for tc in tool_calls:
                if cancel():
                    break
                tc_id, name, args = tc["id"], tc["name"], tc.get("arguments", {})
                # update_plan drives the Plan panel only — no chat bubble, no file.
                if name == "update_plan":
                    steps = normalize_plan_steps(args.get("steps"))
                    emit({"type": "plan_set", "steps": steps})
                    audit_log.record("tool_call", name, True, f"{len(steps)} step(s)",
                                     agent_role=agent_roles.PLANNER)
                    messages.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                     "content": "Plan updated."})
                    continue
                if name in extra_names and extra_executor is not None:
                    preview = {"kind": "info", "title": name, "text": str(args)}
                    emit({"type": "tool_proposed", "id": tc_id, "name": name, "args": args,
                          "preview": preview})
                    result = extra_executor(name, args)
                    emit({"type": "tool_result", "id": tc_id, "name": name,
                          "ok": result.get("ok", False), "output": result.get("output", "")})
                    messages.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                     "content": result.get("output", "")})
                    continue
                # Surface the step (generated content / command) in the chat first.
                if name == "save_file":
                    fname = _titled_filename(title, args.get("filename", "output.txt"))
                    content_str = str(args.get("content", ""))
                    summary = _structure_summary(fname, content_str)
                    old_content = ""
                    existing = output_dir / fname
                    if existing.exists():
                        try:
                            old_content = existing.read_text(encoding="utf-8", errors="replace")
                        except OSError:
                            pass
                    # A brand-new file naturally renders all-green (before = ""); an
                    # overwrite shows the real before/after, like editing any file.
                    diff = "".join(difflib.unified_diff(
                        old_content.splitlines(keepends=True), content_str.splitlines(keepends=True),
                        fromfile=f"a/{fname}", tofile=f"b/{fname}",
                    )) or content_str[:4000]
                    preview = {"kind": "diff", "title": f"Save {fname}",
                               "text": f"{summary}\n\n{diff[:4000]}"}
                else:
                    preview = describe_action(ctx, name, args)
                emit({"type": "tool_proposed", "id": tc_id, "name": name, "args": args, "preview": preview})
                # Active guardrail on run_command/install_package — raises
                # SecurityBlocked (caught by the outer finally, then propagated)
                # on a violation; a no-op for every other tool or when disabled.
                # Skipped (with enforce_rules) for sandboxed Co4E runs.
                if enforce_rules:
                    agent_security.enforce_command(provider, name, args, security_config, emit)

                # Permission Management (Sandbox Security Layer) — only when a
                # gate was actually supplied (Settings: "confirm before running
                # commands"); None preserves the pre-existing auto-run behavior.
                if gate is not None and name in ("run_command", "install_package"):
                    approved = gate.request({"name": name, "args": args, "preview": preview})
                    if not approved:
                        result = {"ok": False, "output": "Rejected by user."}
                        evt = {"type": "tool_result", "id": tc_id, "name": name,
                              "ok": False, "output": result["output"]}
                        emit(evt)
                        messages.append({"role": "tool", "tool_call_id": tc_id,
                                        "name": name, "content": result["output"]})
                        continue

                if name == "save_file":
                    result = _do_save_file(output_dir, title, args)
                else:
                    def on_output(line: str, _id=tc_id, _name=name) -> None:
                        emit({"type": "tool_output", "id": _id, "name": _name, "delta": line})
                    result = execute_tool(ctx, name, args, cancel=cancel, on_output=on_output,
                                          agent_role=agent_role)

                evt = {"type": "tool_result", "id": tc_id, "name": name,
                       "ok": result.get("ok", False), "output": result.get("output", "")}
                path = result.get("path")
                if not path and isinstance(args, dict) and args.get("path"):
                    path = str(ctx.workdir / str(args["path"]))
                if path:
                    evt["path"] = path
                if result.get("produced"):
                    evt["produced"] = result["produced"]
                emit(evt)
                messages.append({"role": "tool", "tool_call_id": tc_id, "name": name,
                                 "content": result.get("output", "")})
        # Ran out of the step budget while still mid-work — never silent, so the
        # user knows the step was cut off by the ceiling (not truly finished).
        if not completed_naturally and not cancel():
            note = (f"\n\n⚠️ Reached the {effective_max_steps}-step safety limit before the task "
                    "signalled completion — stopping here. Re-run to continue if more work remains.")
            emit({"type": "text", "delta": note})
            if messages and messages[-1].get("role") == "assistant":
                messages[-1]["content"] = (messages[-1].get("content") or "") + note
    finally:
        # Always tidy up: drop the .scratch sandbox + generator scripts and flatten
        # any sub-folder so the output keeps only the final file(s) directly in the
        # configured Output folder — runs on success AND on abrupt stop.
        removed, moved = _cleanup_cowork_intermediates(output_dir, before, cancelled=cancel())
        drop = list(removed) + [old for old, _new in moved]
        if drop:
            emit({"type": "outputs_removed", "paths": drop})
        if moved:
            emit({"type": "outputs_added", "paths": [new for _old, new in moved]})
    return messages
