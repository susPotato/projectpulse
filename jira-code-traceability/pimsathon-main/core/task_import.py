"""Schedule Task — multi-format importer.

Import tasks from several input file formats, all producing the same task dicts
the Excel importer returns (previewed, then saved on confirm):

  * .xlsx / .xls  → the fill-in template (delegates to ``task_excel``)
  * .csv          → same columns as the template (header row, order-flexible)
  * .json         → a list of task objects (or ``{"tasks": [...]}``) — the most
                    expressive form: supports Co4E-flow tasks via ``flow_id``.

Every format runs through the SAME field mapping / enum fallbacks as the Excel
importer (``task_excel.task_from_cells`` / ``resolve_depends``), so behaviour is
consistent across formats.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from . import task_excel
from .tasks import PRIORITIES, REPEAT_TYPES, TASK_TYPES, new_task, parse_run_at

SUPPORTED_EXTS = (".xlsx", ".xls", ".csv", ".json")
IMPORT_FILTER = "Tasks (*.xlsx *.xls *.csv *.json)"

_HEADER_ALIASES = {h.split(" (")[0].strip().lower(): i for i, h in enumerate(task_excel.HEADERS)}


def import_tasks(path: str | Path) -> List[Dict[str, Any]]:
    """Dispatch by file extension. Raises ``ValueError`` with a human message on
    an unusable / unsupported file. Imported tasks are auto-chained to run in the
    file's top→bottom order (unless the file already defines dependencies)."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls"):
        tasks = task_excel.import_tasks(p)
    elif ext == ".csv":
        tasks = _import_csv(p)
    elif ext == ".json":
        tasks = _import_json(p)
    else:
        raise ValueError(f"Unsupported file type '{ext}'. Use one of: {', '.join(SUPPORTED_EXTS)}.")
    return auto_chain_in_order(tasks)


def auto_chain_in_order(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chain imported tasks so they run one after another in the file's row order
    (top → bottom): each task triggers the next on success, and the FIRST task is
    scheduled to start immediately. Skipped when the file already defines its own
    dependencies/chains (those are respected instead)."""
    if len(tasks) < 1:
        return tasks
    already = any((t.get("dependency", {}).get("depends_on")
                   or t.get("dependency", {}).get("next_task_id")) for t in tasks)
    if already:
        return tasks
    from datetime import datetime

    from .tasks import format_run_at
    for i in range(len(tasks) - 1):
        dep = tasks[i].setdefault("dependency", {})
        dep["next_task_id"] = tasks[i + 1]["task_id"]
        dep["run_next_mode"] = "run_after_success"
    first = tasks[0]
    if not first.get("schedule", {}).get("enabled"):
        first.setdefault("schedule", {})["enabled"] = True
        first["schedule"]["run_at"] = format_run_at(datetime.now())
        first["status"] = "scheduled"
    return tasks


# ---- CSV -----------------------------------------------------------------
def _import_csv(path: Path) -> List[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"Cannot read CSV file: {exc}") from exc
    reader = list(csv.reader(text.splitlines()))
    if not reader:
        raise ValueError("The CSV file is empty.")
    header = [str(c or "").strip().lower() for c in reader[0]]
    # A header row lets columns be in any order; without one, assume template order.
    has_header = any(h in _HEADER_ALIASES for h in header)
    col_map = None
    if has_header:
        col_map = {i: _HEADER_ALIASES[h] for i, h in enumerate(header) if h in _HEADER_ALIASES}
    data_rows = reader[1:] if has_header else reader

    tasks: List[Dict[str, Any]] = []
    depends_raw: List[List[str]] = []
    for row in data_rows:
        if col_map is not None:
            cells = [None] * len(task_excel.HEADERS)
            for src_i, dst_i in col_map.items():
                if src_i < len(row):
                    cells[dst_i] = row[src_i]
        else:
            cells = list(row)
        t, depends = task_excel.task_from_cells(cells)
        if t is None:
            continue
        tasks.append(t)
        depends_raw.append(depends)
    if not tasks:
        raise ValueError("No task rows found in the CSV file.")
    task_excel.resolve_depends(tasks, depends_raw)
    return tasks


# ---- JSON ----------------------------------------------------------------
def _import_json(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON file: {exc}") from exc
    if isinstance(data, dict) and isinstance(data.get("tasks"), list):
        data = data["tasks"]
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of task objects (or {\"tasks\": [...]}).")

    tasks: List[Dict[str, Any]] = []
    depends_raw: List[List[str]] = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        t, depends = _task_from_mapping(obj)
        if t is None:
            continue
        tasks.append(t)
        depends_raw.append(depends)
    if not tasks:
        raise ValueError("No task objects found in the JSON file.")
    # depends_on may reference titles OR ids — resolve titles here, keep ids.
    task_excel.resolve_depends(tasks, depends_raw)
    return tasks


def _pick(d: Dict[str, Any], *keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _clamp(value, allowed, default):
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def _task_from_mapping(d: Dict[str, Any]):
    """Map a JSON object to a task dict + its depends-on titles. Recognises the
    template field names plus friendly aliases, and — uniquely for JSON — a
    ``flow_id`` that turns the task into a Co4E-flow run."""
    title = str(_pick(d, "title", "name")).strip()
    if not title:
        return None, None
    t = new_task(title)
    t["description"] = str(_pick(d, "description", "desc")).strip()
    flow_id = str(_pick(d, "flow_id", "co4e_flow", "flow")).strip()
    if flow_id:
        t["task_type"] = "flow"
        t["flow"]["flow_id"] = flow_id
    else:
        t["task_type"] = _clamp(_pick(d, "task_type", "type", default="cowork"),
                                TASK_TYPES, "cowork")
    t["priority"] = _clamp(_pick(d, "priority", default="medium"), PRIORITIES, "medium")
    t["script_command"] = str(_pick(d, "script_command", "command")).strip()
    t["provider"] = str(_pick(d, "provider")).strip()
    t["model"] = str(_pick(d, "model")).strip()
    t["skill_slug"] = str(_pick(d, "skill_slug", "skill")).strip()
    sched = d.get("schedule") if isinstance(d.get("schedule"), dict) else d
    enabled = _truthy(_pick(sched, "schedule_enabled", "enabled", default=False))
    run_at = str(_pick(sched, "run_at")).strip()[:16]
    t["schedule"]["run_at"] = run_at if parse_run_at(run_at) else None
    t["schedule"]["repeat_type"] = _clamp(_pick(sched, "repeat", "repeat_type", default="none"),
                                          REPEAT_TYPES, "none")
    t["schedule"]["cron_expression"] = str(_pick(sched, "cron_expression", "cron")).strip() or None
    if enabled and (t["schedule"]["run_at"] or t["schedule"]["repeat_type"] == "cron"):
        t["schedule"]["enabled"] = True
        t["status"] = "scheduled"
    if _truthy(_pick(d, "use_previous_output", default=False)):
        t["input"]["mode"] = "previous_task_output"
    manual_text = str(_pick(d, "manual_text", "input_text")).strip()
    if manual_text:
        t["input"]["mode"] = "manual"
        t["input"]["manual_text"] = manual_text
    files = _pick(d, "file_paths", "files", default=[])
    if isinstance(files, list):
        t["input"]["file_paths"] = [str(x) for x in files if x]
    links = _pick(d, "links", "urls", default=[])
    if isinstance(links, list):
        t["input"]["links"] = [str(x) for x in links if x]
    t["execution"]["requires_approval"] = _truthy(_pick(d, "requires_approval", default=False))
    channel = str(_pick(d, "notify_channel", "reminder")).strip().lower()
    t["execution"]["notify_channel"] = channel if channel in ("teams", "outlook") else "none"
    t["execution"]["notify_email"] = str(_pick(d, "notify_email", "reminder_email")).strip()
    if t["execution"]["notify_channel"] != "none":
        t["execution"]["notify_on_complete"] = True
        t["execution"]["notify_on_error"] = True
    depends = _pick(d, "depends_on", "depends", default=[])
    if isinstance(depends, str):
        depends = [s.strip() for s in depends.split(";") if s.strip()]
    elif isinstance(depends, list):
        depends = [str(x).strip() for x in depends if str(x).strip()]
    else:
        depends = []
    return t, depends


_TRUE = {"yes", "y", "true", "1", "x", "có", "co"}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE
