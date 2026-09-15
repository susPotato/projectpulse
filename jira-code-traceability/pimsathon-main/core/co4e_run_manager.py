"""Co4E run manager — tracks concurrent flow runs and their live status.

The app already runs several Cowork/Code turns at once (each on its own
``AgentWorker`` QThread). This brings the same to Co4E: any number of flows can
run in parallel, each on its own worker, with live per-run status
(running / done / error / stopped) and progress (done / total steps).

The manager is the single source of truth — the Co4E "Running flows" list reads
it and refreshes on every ``changed`` signal (and when the tab is re-shown), so
switching sub-tabs never loses, stales, or drops status. ``event`` re-emits each
run's node-level events tagged with the run id, so the canvas/chat can mirror
the run that is currently open.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from .co4e import STEP_DONE, STEP_ERROR, STEP_PLANNED, Workflow

_TERMINAL_NODE = {STEP_DONE, STEP_ERROR, STEP_PLANNED}
_HISTORY_CAP = 500          # keep the most-recent N runs on disk


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _current_user() -> str:
    """Best-effort creator name for a run (signed-in MS365 identity → OS user)."""
    import os
    return (os.environ.get("USERNAME") or os.environ.get("USER") or "you")


class RunHandle:
    """Live state for one flow run. Mutated by the manager as events arrive."""

    def __init__(self, run_id: str, wf_id: str, name: str, total: int,
                 plan_mode: bool, manual: bool, created_by: str = "", created_at: str = "",
                 project_id: str = ""):
        self.id = run_id
        self.wf_id = wf_id
        self.name = name
        self.project_id = project_id   # workspace this run belongs to (Flow Status is per-project)
        self.total = max(0, total)
        self.done = 0
        self.status = "running"     # running | done | error | stopped
        self.plan_mode = plan_mode
        self.manual = manual
        self.created_by = created_by
        self.created_at = created_at
        self.error = ""
        self.node_status: Dict[str, str] = {}
        self.worker = None
        self.wf = None            # the Workflow this run ran — lets Runs reopen it
                                  # even after its tab was closed / it was never saved
        self.out_dir = ""         # workspace folder this run wrote its files into

    @property
    def running(self) -> bool:
        return self.status == "running"

    def progress_text(self) -> str:
        return f"{self.done}/{self.total}" if self.total else self.status

    # ---- persistence ------------------------------------------------------
    def to_record(self) -> dict:
        """Serialize for the on-disk run history. The workflow snapshot is kept
        so a past run can be reopened even if its saved flow was later edited or
        deleted."""
        from .co4e import workflow_to_dict
        return {
            "id": self.id, "wf_id": self.wf_id, "name": self.name,
            "total": self.total, "done": self.done, "status": self.status,
            "plan_mode": self.plan_mode, "manual": self.manual,
            "created_by": self.created_by, "created_at": self.created_at,
            "error": self.error, "node_status": dict(self.node_status),
            "wf": workflow_to_dict(self.wf) if self.wf is not None else None,
            "out_dir": self.out_dir, "project_id": self.project_id,
        }

    @classmethod
    def from_record(cls, rec: dict) -> "RunHandle":
        from .co4e import workflow_from_dict
        rec = dict(rec or {})
        h = cls(str(rec.get("id", "")), str(rec.get("wf_id", "")),
                rec.get("name", ""), int(rec.get("total", 0) or 0),
                bool(rec.get("plan_mode")), bool(rec.get("manual")),
                created_by=rec.get("created_by", ""), created_at=rec.get("created_at", ""))
        h.done = int(rec.get("done", 0) or 0)
        h.status = rec.get("status", "done")
        # a run persisted as "running" means the app closed mid-run — its worker
        # is gone, so it's no longer live: settle it as "stopped".
        if h.status == "running":
            h.status = "stopped"
        h.error = rec.get("error", "")
        h.node_status = dict(rec.get("node_status") or {})
        h.out_dir = rec.get("out_dir", "")
        h.project_id = rec.get("project_id", "")
        wfd = rec.get("wf")
        h.wf = workflow_from_dict(wfd) if wfd else None
        return h


class Co4ERunManager(QObject):
    changed = Signal()               # any run's status/progress changed → refresh views
    event = Signal(str, dict)        # (run_id, ev) — node-level events, for mirroring

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._runs: Dict[str, RunHandle] = {}
        self._seq = 0
        self._output_root: Optional[Path] = None   # active workspace's co4e output base
        self._project_id: str = ""                 # active workspace — Flow Status is filtered to it
        self._load_history()          # restore past runs so the Flow Status tab
                                      # keeps its full history across restarts
        # every status/progress change is persisted, so history is never lost
        self.changed.connect(self._save_history)

    # ---- persistence ------------------------------------------------------
    def _history_path(self) -> Path:
        from .co4e import CO4E_DIR
        return CO4E_DIR / "run_history.json"

    def _load_history(self) -> None:
        path = self._history_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        max_seq = 0
        for rec in data.get("runs", []):
            try:
                handle = RunHandle.from_record(rec)
            except Exception:
                continue
            if not handle.id:
                continue
            self._runs[handle.id] = handle
            if handle.id.startswith("run") and handle.id[3:].isdigit():
                max_seq = max(max_seq, int(handle.id[3:]))
        self._seq = max_seq           # avoid minting ids that collide with history

    def _save_history(self) -> None:
        path = self._history_path()
        runs = list(self._runs.values())[-_HISTORY_CAP:]
        payload = {"runs": [h.to_record() for h in runs]}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(path)         # atomic — never leaves a half-written file
        except OSError:
            pass

    # ---- lifecycle --------------------------------------------------------
    def _next_id(self) -> str:
        self._seq += 1
        return f"run{self._seq}"

    def start(self, wf: Workflow, *, skill_map: Optional[Dict[str, str]] = None,
              plan_mode: bool = False, only_nodes: Optional[set] = None,
              seed_outputs: Optional[Dict[str, str]] = None,
              manual: bool = False, label: Optional[str] = None) -> str:
        """Launch a flow (or a subset via ``only_nodes``) on its own worker and
        return the new run id. Runs concurrently with every other active run."""
        from . import co4e_runner
        from .worker import AgentWorker

        import copy as _copy

        run_id = self._next_id()
        total = len(only_nodes) if only_nodes else len(wf.nodes)
        handle = RunHandle(run_id, wf.id, label or wf.name, total, plan_mode, manual,
                           created_by=_current_user(), created_at=_now_str(),
                           project_id=self._project_id)
        # Keep a DEEP COPY so Runs can reopen the flow exactly as it ran — even if
        # the live canvas is later edited (nodes moved, edges removed) while the
        # run is still tracked. A shared reference caused reopened runs to show a
        # disconnected/blank graph.
        handle.wf = _copy.deepcopy(wf)
        nodes = list(wf.nodes)
        edges = list(wf.edges)
        out_dir = self._out_dir(wf)
        handle.out_dir = str(out_dir)         # where this run writes its files (workspace)
        ctx = self.ctx
        sk = dict(skill_map or {})
        only = set(only_nodes) if only_nodes else None
        seed = dict(seed_outputs or {})

        run_label = handle.name
        def job(worker: AgentWorker):
            return co4e_runner.run_workflow(
                ctx, nodes, edges, out_dir, worker.emit_event, worker.is_cancelled,
                plan_mode=plan_mode, skill_map=sk, only_nodes=only, seed_outputs=seed,
                usage_label=run_label)

        worker = AgentWorker(job)
        handle.worker = worker
        worker.event.connect(lambda ev, rid=run_id: self._on_event(rid, ev))
        worker.finished_ok.connect(lambda _r, rid=run_id: self._on_finished(rid))
        worker.failed.connect(lambda e, rid=run_id: self._on_failed(rid, e))
        self._runs[run_id] = handle
        worker.start()
        self.changed.emit()
        return run_id

    # ---- worker callbacks -------------------------------------------------
    def _on_event(self, run_id: str, ev: dict) -> None:
        handle = self._runs.get(run_id)
        if handle is not None and isinstance(ev, dict):
            t = ev.get("type")
            if t == "node_status":
                handle.node_status[ev.get("node_id")] = ev.get("status")
                handle.done = sum(1 for s in handle.node_status.values() if s in _TERMINAL_NODE)
                self.changed.emit()
            elif t == "run_done":
                if handle.status == "running":
                    handle.status = "done" if ev.get("ok", True) else "error"
                self.changed.emit()
        self.event.emit(run_id, ev)

    def _on_finished(self, run_id: str) -> None:
        handle = self._runs.get(run_id)
        if handle is not None and handle.status == "running":
            # job returned without a run_done event (shouldn't happen) — settle it
            handle.status = "done"
            self.changed.emit()

    def _on_failed(self, run_id: str, err: str) -> None:
        handle = self._runs.get(run_id)
        if handle is not None:
            handle.status = "error"
            handle.error = str(err)
            self.event.emit(run_id, {"type": "run_error", "error": str(err)})
            self.changed.emit()

    # ---- control ----------------------------------------------------------
    def stop(self, run_id: str) -> None:
        handle = self._runs.get(run_id)
        if handle is not None and handle.worker is not None and handle.running:
            handle.worker.request_stop()
            handle.status = "stopped"
            self.changed.emit()

    def stop_all(self) -> None:
        # Only the CURRENT workspace's runs (Flow Status is per-project).
        for run_id in [r for r, h in self._runs.items() if self._belongs(h)]:
            self.stop(run_id)

    def rename(self, run_id: str, new_name: str) -> None:
        """Rename a run in the Flow Status history (and its kept workflow snapshot),
        then persist + refresh views. No-op on a blank name / unknown run."""
        handle = self._runs.get(run_id)
        new_name = (new_name or "").strip()
        if handle is None or not new_name or new_name == handle.name:
            return
        handle.name = new_name
        if handle.wf is not None:
            handle.wf.name = new_name
        self.changed.emit()

    def remove(self, run_id: str) -> None:
        handle = self._runs.get(run_id)
        if handle is not None and handle.running:
            self.stop(run_id)
        self._runs.pop(run_id, None)
        self.changed.emit()

    def clear_finished(self) -> None:
        # Only clear finished runs of the CURRENT workspace.
        for run_id in [r for r, h in self._runs.items() if not h.running and self._belongs(h)]:
            self._runs.pop(run_id, None)
        self.changed.emit()

    # ---- queries ----------------------------------------------------------
    def _belongs(self, h: RunHandle) -> bool:
        """Whether a run belongs to the currently-selected workspace."""
        return getattr(h, "project_id", "") == self._project_id

    def runs(self) -> List[RunHandle]:
        """Runs of the CURRENT workspace only — Flow Status is per-project."""
        return [h for h in self._runs.values() if self._belongs(h)]

    def all_runs(self) -> List[RunHandle]:
        """Every tracked run across all workspaces (background tracking)."""
        return list(self._runs.values())

    def get(self, run_id: str) -> Optional[RunHandle]:
        return self._runs.get(run_id)

    def active_count(self) -> int:
        return sum(1 for h in self._runs.values() if h.running and self._belongs(h))

    def set_current_project(self, project_id: str) -> None:
        """Filter Flow Status (and new runs) to this workspace. Runs started while
        this is set are tagged with it; the Runs view shows only matching runs."""
        pid = project_id or ""
        if pid != self._project_id:
            self._project_id = pid
            self.changed.emit()          # re-render Flow Status for the new workspace

    def set_output_root(self, root: Optional[Path]) -> None:
        """Point flow outputs at the SELECTED workspace's co4e folder (set by the
        Co4E tab when a project is chosen). ``None`` → fall back to the global
        Cowork output dir."""
        self._output_root = Path(root) if root else None

    def _out_dir(self, wf: Workflow) -> Path:
        # Flow deliverables are written into the SELECTED workspace (the active
        # project's folder) so they land where the user works with files (Folder
        # tab), not in the config/install folder. One subfolder per flow keeps
        # runs tidy. Falls back to the global Cowork output dir when no workspace
        # is selected.
        from .co4e import slugify
        base = self._output_root
        if base is None:
            try:
                base = self.ctx.config.cowork_output_dir() / "co4e"
            except Exception:  # noqa: BLE001 - fall back to the config dir if unavailable
                from .co4e import CO4E_DIR
                base = CO4E_DIR / "runs" / "co4e"
        d = Path(base) / slugify(wf.name or "flow")
        d.mkdir(parents=True, exist_ok=True)
        return d
