"""Schedule Task module — task model, repository and pure scheduling logic.

Everything here is Qt-free so it can be unit-tested headlessly; the Qt wrapper
that actually runs tasks in the background lives in ``task_scheduler.py``.

Storage follows the app's existing pattern (one JSON file per item, like
history/agents): ``~/.cowork_local/tasks/<task_id>.json``. Run artifacts go to
``~/.cowork_local/task_artifacts/<task_id>/<run_id>/``.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import CONFIG_DIR

TASKS_DIR = CONFIG_DIR / "tasks"
ARTIFACTS_DIR = CONFIG_DIR / "task_artifacts"

STATUSES = ("backlog", "scheduled", "running", "waiting_input", "done", "failed", "paused")
TASK_TYPES = ("cowork", "co4e_code", "flow", "script", "manual")
PRIORITIES = ("low", "medium", "high", "critical")
REPEAT_TYPES = ("none", "daily", "weekly", "monthly", "cron")  # monthly/cron: placeholder
RUN_NEXT_MODES = ("none", "run_after_success", "run_always", "run_after_manual_confirm")
INPUT_MODES = ("empty", "manual", "file", "previous_task_output")
OUTPUT_MODES = ("text", "file", "folder", "json", "markdown", "code_diff")

_TIME_FMT = "%Y-%m-%d %H:%M"   # run_at stored as a local naive timestamp string
_MAX_RUNS_KEPT = 20            # recent run summaries kept inside the task file

# Defaults follow spec §5.3 exactly: empty input, no next task, no output
# chaining, schedule disabled, status Backlog.
DEFAULT_TASK: Dict[str, Any] = {
    "task_id": "",
    "title": "",
    "description": "",
    "task_type": "manual",
    "agent_executor": "system",
    "project_id": "",   # optional workspace/project this task's agent runs in
    # Which model runs the task. Blank provider/model = the machine's own
    # Settings default (see state.build_provider_for). Replaces the older
    # per-task Admin-agent preset (admin_agent_id) as the way to choose a model.
    "provider": "",
    "model": "",
    "skill_slug": "",   # optional skill applied to the run (its instructions are prepended)
    "status": "backlog",
    "priority": "medium",
    "created_at": "",
    "updated_at": "",
    "created_by": "",
    "is_ai_generated": False,
    "schedule": {
        "enabled": False,
        "run_at": None,            # "YYYY-MM-DD HH:MM" local time
        "timezone": "local",
        "repeat_type": "none",
        "cron_expression": None,   # used when repeat_type == "cron" (see core/cron.py)
        "working_days_only": False,
        "skip_holidays": False,    # skip public holidays of holiday_country
        "holiday_country": "VN",   # ISO country code for the holiday calendar
    },
    "flow": {"flow_id": None, "selected_flow_template": None, "steps": []},
    "input": {
        "mode": "empty",
        "manual_text": None,
        "file_paths": [],      # attached local files — always used, any mode
        "links": [],           # attached URLs — always used, any mode
        "previous_task_id": None,
    },
    "output": {"output_mode": "text"},
    "dependency": {
        "next_task_id": None,
        "previous_task_id": None,
        "depends_on": [],          # fan-in: ALL of these must be Done first
        "run_next_mode": "none",
        "pass_output_to_next": False,
    },
    "execution": {
        "max_retry": 0,
        "timeout_sec": 600,
        "requires_approval": False,
        "notify_on_complete": False,
        "notify_on_error": False,
        # Scheduled-reminder channel: "none" | "teams" | "outlook". When not
        # "none" the scheduler notifies on both completion AND error via that
        # channel (Teams webhook, or the local Outlook desktop app — no login).
        "notify_channel": "none",
        "notify_email": "",         # recipient address(es) for the "outlook" channel
    },
    "logs": {"last_run_id": None, "last_status": None, "last_error": None},
    "script_command": "",
    "runs": [],
}


def _now_str() -> str:
    return datetime.now().strftime(_TIME_FMT)


def parse_run_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, _TIME_FMT)
    except ValueError:
        return None


def format_run_at(dt: datetime) -> str:
    return dt.strftime(_TIME_FMT)


def new_task(title: str = "", **overrides) -> Dict[str, Any]:
    """A fresh task dict with spec-mandated defaults. ``overrides`` merge
    shallowly for top-level keys and dict-merge for the nested groups."""
    task = copy.deepcopy(DEFAULT_TASK)
    task["task_id"] = uuid.uuid4().hex
    task["title"] = title
    task["created_at"] = task["updated_at"] = datetime.now().isoformat(timespec="seconds")
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(task.get(key), dict):
            task[key].update(value)
        else:
            task[key] = value
    return task


def _normalize(task: Dict[str, Any]) -> Dict[str, Any]:
    """Fill any missing keys with defaults (tolerates files from older
    versions / hand edits) without dropping unknown extras."""
    base = copy.deepcopy(DEFAULT_TASK)
    for key, value in task.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


# ---- repository ----------------------------------------------------------
def task_path(task_id: str, directory: Path = None) -> Path:
    return (directory or TASKS_DIR) / f"{task_id}.json"


def save_task(task: Dict[str, Any], directory: Path = None) -> Path:
    directory = directory or TASKS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    task["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = task_path(task["task_id"], directory)
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_task(task_id: str, directory: Path = None) -> Optional[Dict[str, Any]]:
    path = task_path(task_id, directory)
    try:
        return _normalize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def list_tasks(directory: Path = None) -> List[Dict[str, Any]]:
    directory = directory or TASKS_DIR
    if not directory.exists():
        return []
    items: List[Dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            items.append(_normalize(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return items


def delete_task(task_id: str, directory: Path = None) -> None:
    try:
        task_path(task_id, directory).unlink()
    except OSError:
        pass


def duplicate_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """A copy with a fresh id that KEEPS the whole configuration — schedule,
    input, flow steps, dependencies, execution options — so re-running an
    already-run task needs no re-editing ('duplicate task đã chạy để không
    phải chỉnh sửa nhiều'). Only the identity and run history reset. A
    repeating/future schedule stays enabled and rolls forward to its next
    occurrence; a one-shot time already in the past is disabled (it would
    otherwise re-fire immediately and surprise the user)."""
    dup = copy.deepcopy(task)
    dup["task_id"] = uuid.uuid4().hex
    dup["title"] = f"{task.get('title', '')} (copy)"
    dup["logs"] = {"last_run_id": None, "last_status": None, "last_error": None}
    dup["runs"] = []
    dup["created_at"] = dup["updated_at"] = datetime.now().isoformat(timespec="seconds")
    now = datetime.now()
    if dup["schedule"].get("enabled"):
        nxt = compute_next_run(dup, now)
        run_at = parse_run_at(dup["schedule"].get("run_at"))
        if nxt is not None:                       # repeating/cron → next occurrence
            dup["schedule"]["run_at"] = format_run_at(nxt)
            dup["status"] = "scheduled"
        elif run_at is not None and run_at > now:  # one-shot still in the future
            dup["status"] = "scheduled"
        else:                                      # one-shot already fired
            dup["schedule"]["enabled"] = False
            dup["status"] = "backlog"
    else:
        dup["status"] = "backlog"
    return dup


# ---- fan-in dependencies ("chờ các task") ----------------------------------
def _all_prerequisites(task: Dict[str, Any]) -> List[str]:
    """Every task id this one must wait for: the depends_on list plus the
    legacy single previous_task_id (older files), deduplicated."""
    dep = task.get("dependency", {})
    ids = list(dep.get("depends_on") or [])
    legacy = dep.get("previous_task_id")
    if legacy and legacy not in ids:
        ids.append(legacy)
    return ids


def dependencies_met(task: Dict[str, Any], directory: Path = None) -> bool:
    """True when EVERY prerequisite has completed successfully at least once.
    A task with unmet prerequisites must not run — it doesn't have its input
    yet (spec: 'chưa Done task trước thì task sau không chạy')."""
    for pid in _all_prerequisites(task):
        prev = load_task(pid, directory)
        if prev is None:
            continue   # prerequisite deleted → don't block forever
        if prev.get("logs", {}).get("last_status") != "success":
            return False
    return True


def depends_cycle_error(tasks: List[Dict[str, Any]], task_id: str,
                        depends_on: List[str]) -> Optional[str]:
    """Validate a proposed depends_on list: no self-wait, no wait-cycle
    (A waits B while B — directly or transitively — waits A)."""
    if task_id in (depends_on or []):
        return "A task cannot wait for itself."
    by_id = {t["task_id"]: t for t in tasks}
    # DFS from each proposed prerequisite through ITS prerequisites.
    for start in depends_on or []:
        stack, seen = [start], set()
        while stack:
            cur = stack.pop()
            if cur == task_id:
                return "This would create a circular wait between tasks."
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(_all_prerequisites(by_id.get(cur, {})))
    return None


# ---- chain validation -----------------------------------------------------
def chain_error(tasks: List[Dict[str, Any]], task_id: str,
                next_task_id: Optional[str]) -> Optional[str]:
    """Validate assigning ``next_task_id`` as ``task_id``'s next task.
    Returns an error string (self-link / circular chain / unknown id), or
    None when the assignment is safe."""
    if not next_task_id:
        return None
    if next_task_id == task_id:
        return "A task cannot chain to itself."
    by_id = {t["task_id"]: t for t in tasks}
    if next_task_id not in by_id:
        return "Next task does not exist."
    # Walk forward from the proposed next task; reaching task_id again means
    # the new edge would close a cycle.
    seen = {task_id}
    cur = next_task_id
    while cur:
        if cur in seen:
            return "This would create a circular task chain."
        seen.add(cur)
        cur = (by_id.get(cur) or {}).get("dependency", {}).get("next_task_id")
    return None


# ---- schedule math --------------------------------------------------------
def _is_excluded_day(dt: datetime, sched: Dict[str, Any]) -> bool:
    """True when ``dt`` falls on a day this schedule must skip: a weekend
    (working_days_only) or a public holiday of the configured country."""
    if sched.get("working_days_only") and dt.weekday() >= 5:   # 5=Sat, 6=Sun
        return True
    if sched.get("skip_holidays"):
        from .holiday_calendar import is_holiday

        if is_holiday(dt.date(), sched.get("holiday_country", "")):
            return True
    return False


def _add_month(dt: datetime) -> datetime:
    import calendar

    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def shift_off_excluded_days(dt: datetime, sched: Dict[str, Any]) -> datetime:
    """Push ``dt`` forward one day at a time until it lands on an allowed day
    (same time of day) — used for one-time schedules set on a weekend/holiday."""
    guard = 0
    while _is_excluded_day(dt, sched) and guard < 400:
        dt += timedelta(days=1)
        guard += 1
    return dt


def compute_next_run(task: Dict[str, Any], after: datetime) -> Optional[datetime]:
    """The next run time strictly after ``after`` for a repeating task
    (daily / weekly / monthly / cron), or None for one-shot schedules.
    Occurrences on excluded days (weekends with working_days_only, public
    holidays with skip_holidays+holiday_country) are skipped forward."""
    sched = task.get("schedule", {})
    repeat = sched.get("repeat_type", "none")

    if repeat == "cron":
        from .cron import Cron, CronError

        try:
            cron = Cron(sched.get("cron_expression") or "")
        except CronError:
            return None
        nxt = cron.next_after(after)
        guard = 0
        while nxt is not None and _is_excluded_day(nxt, sched) and guard < 400:
            nxt = cron.next_after(nxt)
            guard += 1
        return nxt

    base = parse_run_at(sched.get("run_at"))
    if base is None:
        return None
    if repeat == "daily":
        advance = lambda d: d + timedelta(days=1)          # noqa: E731
    elif repeat == "weekly":
        advance = lambda d: d + timedelta(weeks=1)         # noqa: E731
    elif repeat == "monthly":
        advance = _add_month
    else:
        return None
    nxt = base
    while nxt <= after:
        nxt = advance(nxt)
    guard = 0
    while _is_excluded_day(nxt, sched) and guard < 400:
        nxt = advance(nxt)
        guard += 1
    return nxt


def due_tasks(tasks: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """Tasks that should start now: Scheduled + schedule enabled + run_at due."""
    due = []
    for t in tasks:
        if t.get("status") != "scheduled":
            continue
        sched = t.get("schedule", {})
        if not sched.get("enabled"):
            continue
        run_at = parse_run_at(sched.get("run_at"))
        if run_at is not None and run_at <= now:
            due.append(t)
    return due


# ---- post-run bookkeeping (pure; scheduler applies + saves) ---------------
def _append_run_record(task: Dict[str, Any], ok: bool, run_id: str, error: str,
                       now: datetime) -> None:
    """Shared by ``advance_after_run``/``record_interrupted_run``: write the
    last-run log + append to the recent-runs list every completion path uses,
    so a run is never recorded by one code path and silently skipped by
    another."""
    task["logs"] = {"last_run_id": run_id,
                    "last_status": "success" if ok else "failed",
                    "last_error": None if ok else (error or "failed")}
    task.setdefault("runs", []).append({
        "run_id": run_id,
        "status": "success" if ok else "failed",
        "finished_at": now.strftime(_TIME_FMT),
        "error": None if ok else (error or "failed")[:500],
    })
    task["runs"] = task["runs"][-_MAX_RUNS_KEPT:]


def advance_after_run(task: Dict[str, Any], ok: bool, run_id: str, error: str = "",
                      now: Optional[datetime] = None) -> Dict[str, Any]:
    """Mutate ``task`` after a run: status, last-run log, repeat reschedule.
    Returns the same dict for chaining convenience."""
    now = now or datetime.now()
    _append_run_record(task, ok, run_id, error, now)
    # Gate on the REPEAT TYPE, not the current "enabled" flag: a repeating
    # task must re-arm itself after ANY successful run, including one fired
    # manually via "Run now" while enabled happened to be off (e.g. a task
    # tested by hand before its first automatic occurrence). Gating on
    # "enabled" here was the bug — a manual run on such a task silently
    # dropped it to Done with the schedule left disabled, so the recurring
    # time the user configured (e.g. "every week") would never fire again.
    repeat = task["schedule"].get("repeat_type", "none")
    nxt = compute_next_run(task, now) if (ok and repeat != "none") else None
    if nxt is not None:
        task["schedule"]["run_at"] = format_run_at(nxt)
        task["schedule"]["enabled"] = True    # re-arm regardless of prior state
        task["status"] = "scheduled"          # repeating task goes back on the calendar
    else:
        if repeat == "none":
            task["schedule"]["enabled"] = False   # one-shot: don't fire again
        task["status"] = "done" if ok else "failed"
    return task


def record_interrupted_run(task: Dict[str, Any], run_id: str, error: str,
                           now: Optional[datetime] = None) -> Dict[str, Any]:
    """Record a run that never reached a real finish (the app was closed or
    killed while the task was still "running") — same run-history bookkeeping
    as ``advance_after_run``, but WITHOUT its success-only repeat re-arm
    logic: an interruption isn't a genuine failure of the task's own logic,
    so a task whose schedule was still enabled simply goes back on the
    calendar exactly as it was, instead of being forced into a terminal
    "failed" state that would silently stop a recurring task from ever
    firing again. Returns the same dict for chaining convenience."""
    now = now or datetime.now()
    _append_run_record(task, False, run_id, error, now)
    task["status"] = "scheduled" if task["schedule"].get("enabled") else "failed"
    return task


def chain_action(task: Dict[str, Any], ok: bool) -> Optional[Tuple[str, str]]:
    """What to do with the next task after this run, if anything:
    ``("enqueue", next_id)`` — run it now; ``("await_confirm", next_id)`` —
    park it in Waiting Input until the user confirms; None — no chaining."""
    dep = task.get("dependency", {})
    next_id = dep.get("next_task_id")
    if not next_id:
        return None
    mode = dep.get("run_next_mode", "none")
    if mode == "run_always" or (mode == "run_after_success" and ok):
        return ("enqueue", next_id)
    if mode == "run_after_manual_confirm" and ok:
        return ("await_confirm", next_id)
    return None


# ---- input resolution -----------------------------------------------------
_MAX_INLINE_FILE_CHARS = 20_000
_MAX_INLINE_OUTPUT_CHARS = 20_000


_MAX_FOLDER_ATTACHMENT_FILES = 10


def _folder_attachment_text(folder: Path) -> str:
    """A ``file_paths``/``links`` entry that turned out to be a local/network
    FOLDER (not a single file or a fetchable URL) — recursively inline its
    files, mirroring ``task_executors.py``'s own project-folder auto-scan
    (``_project_folder_input_text``) so a folder attached directly on the
    task behaves the same way as a folder linked via its Project."""
    from .doc_extract import extract_text, find_input_files

    files, total = find_input_files(folder, max_files=_MAX_FOLDER_ATTACHMENT_FILES)
    if not files:
        return f"[Folder: {folder}] (no readable files found)"
    lines = [f"[Folder: {folder}]"]
    for f in files:
        text, note = extract_text(str(f))
        if text is None:
            lines.append(f"- {f.name} ({note}; located at {f})")
            continue
        if len(text) > _MAX_INLINE_FILE_CHARS:
            text = text[:_MAX_INLINE_FILE_CHARS] + "\n…(truncated)…"
        lines.append(f"- {f.name} ({f})\n--- Content of {f.name} ---\n{text}\n--- end of {f.name} ---")
    if total > len(files):
        lines.append(f"…({total - len(files)} more files in this folder were not loaded — attachment limit)")
    return "\n".join(lines)


def _local_path_attachment_text(value: str) -> Optional[str]:
    """If ``value`` is an existing local/network path (folder or single
    file), return its inlined content; ``None`` when it isn't a local path at
    all, so the caller falls back to treating it as a URL."""
    from .doc_extract import extract_text

    p = Path(value)
    try:
        exists = p.exists()
    except OSError:
        return None   # e.g. an invalid path shape — let URL fetching try instead
    if not exists:
        return None
    if p.is_dir():
        return _folder_attachment_text(p)
    text, note = extract_text(str(p))
    if text is not None:
        return f"[File: {p}]\n{text[:_MAX_INLINE_FILE_CHARS]}"
    return f"[File not readable: {p}] ({note})"


def _resolve_attachments_text(inp: Dict[str, Any]) -> List[str]:
    """Local files/folders + link previews attached to a task — used
    regardless of ``input.mode`` (an explicit attachment is never silently
    dropped just because a different mode is selected, mirroring how
    attachments work in the Cowork/Co4E chat composer). Uses the same
    doc-aware ``doc_extract.extract_text`` every other attachment path in the
    app uses (docx/xlsx/pptx/pdf, not just plain text), and a ``links`` entry
    that's actually a local/network FOLDER path (not a URL) is recursively
    scanned instead of failing to fetch it as one file."""
    parts: List[str] = []
    for fp in inp.get("file_paths", []) or []:
        parts.append(_local_path_attachment_text(fp) or f"[File not readable: {fp}]")
    for url in inp.get("links", []) or []:
        local = _local_path_attachment_text(url)
        if local is not None:
            parts.append(local)
            continue
        from .link_fetch import fetch_link_preview

        preview = fetch_link_preview(url)
        if preview:
            parts.append(preview)
    return parts


def resolve_input_text(task: Dict[str, Any], directory: Path = None) -> str:
    """Build the input block appended to the task description when it runs.
    ``previous_task_output`` reads the chained task's latest artifact (path is
    preserved; only a bounded preview is inlined, per spec §7.2). Attached
    files/links are always included on top of whichever mode is selected."""
    inp = task.get("input", {})
    mode = inp.get("mode", "empty")
    parts: List[str] = []
    if mode == "manual" and inp.get("manual_text"):
        parts.append(inp["manual_text"])
    elif mode == "previous_task_output":
        prev_ids = _input_prerequisites(task)
        for prev_id in prev_ids:
            prev = load_task(prev_id, directory)
            if not prev:
                continue
            run_id = prev.get("logs", {}).get("last_run_id")
            if not run_id:
                continue
            out_file = ARTIFACTS_DIR / prev_id / run_id / "output.md"
            try:
                preview = out_file.read_text(
                    encoding="utf-8", errors="replace")[:_MAX_INLINE_OUTPUT_CHARS]
                parts.append(f"[Output of task '{prev.get('title', '')}' "
                             f"(full artifact: {out_file.parent})]\n{preview}")
            except OSError:
                parts.append(f"[Task '{prev.get('title', '')}' output folder: {out_file.parent}]")
    parts.extend(_resolve_attachments_text(inp))
    return "\n\n".join(parts)


def _input_prerequisites(task: Dict[str, Any]) -> List[str]:
    """Which predecessors feed this task's input: the explicit single
    previous_task_id if set, else ALL depends_on prerequisites (fan-in —
    several parallel tasks all passing their output to this one)."""
    inp = task.get("input", {})
    single = inp.get("previous_task_id") or task.get("dependency", {}).get("previous_task_id")
    fan_in = task.get("dependency", {}).get("depends_on") or []
    ids = list(fan_in)
    if single and single not in ids:
        ids.insert(0, single)
    return ids


def previous_output_ready(task: Dict[str, Any], directory: Path = None) -> bool:
    """False when input.mode=previous_task_output but no feeding task has a
    finished run yet — the task must wait (spec §7.3)."""
    inp = task.get("input", {})
    if inp.get("mode") != "previous_task_output":
        return True
    prev_ids = _input_prerequisites(task)
    if not prev_ids:
        return False
    for pid in prev_ids:
        prev = load_task(pid, directory)
        if not (prev and prev.get("logs", {}).get("last_run_id")):
            return False
    return True
