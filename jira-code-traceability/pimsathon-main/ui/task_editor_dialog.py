"""Schedule Task module — the Add/Edit Task dialog.

One scrollable form covering: basics (title/description/type/priority/status),
a simple one-time Schedule, Input (files/links/prompt — always combined, no
"input mode" to pick), Dependency (next task + chain mode + pass-output, with
circular-chain validation on save) and Execution (retry/timeout/approval/
notifications). Only Cowork and Co4E tasks can be created here — the older
Flow/Script/Manual task types, Cron/Repeat scheduling and the "Output mode"
picker were removed as unnecessary complexity (output format follows
whatever the task's own description/prompt asks for)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import PROVIDER_LABELS
from ..core.tasks import (
    PRIORITIES, REPEAT_TYPES, RUN_NEXT_MODES, STATUSES, chain_error,
    depends_cycle_error, new_task, parse_run_at,
)
from ..core.projects import list_projects
from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon

# Only these two task types can be picked when adding/editing a task — Flow,
# Script and Manual were removed from the editor as unnecessary complexity
# (existing tasks of those types, if any, keep running; they just can't be
# created/re-typed here anymore).
EDITOR_TASK_TYPES = ("cowork", "co4e_code")

_PROVIDER_DEFAULT = ""   # "" = the machine's own active provider (Settings default)

# Common cron presets offered in the "Sample" picker so users insert correct
# syntax instead of guessing. (i18n label key, cron expression).
_CRON_SAMPLES = [
    ("schedtask.cron_s_weekday9", "0 9 * * 1-5"),
    ("schedtask.cron_s_daily8", "0 8 * * *"),
    ("schedtask.cron_s_weekly_mon", "0 9 * * 1"),
    ("schedtask.cron_s_monthly1", "0 9 1 * *"),
    ("schedtask.cron_s_every30m", "*/30 * * * *"),
    ("schedtask.cron_s_every2h", "0 */2 * * *"),
]


class TaskEditorDialog(QDialog):
    """Edit ``task`` in place (a deep copy is edited; result() via
    ``edited_task`` after accept). Pass ``all_tasks`` for the next-task combo
    and chain validation."""

    def __init__(self, task: Optional[dict] = None, all_tasks: Optional[List[dict]] = None,
                 parent=None, ctx=None):
        super().__init__(parent)
        self._original = task
        self.ctx = ctx                     # for ✨ AI-generate buttons (optional)
        self._gen_worker: Optional[AgentWorker] = None
        self._live_models: Dict[str, List[str]] = {}   # provider key -> fetched models
        self._model_workers: List[AgentWorker] = []
        self.task = copy.deepcopy(task) if task else new_task()
        self.all_tasks = [t for t in (all_tasks or []) if t["task_id"] != self.task["task_id"]]
        self.edited_task: Optional[dict] = None
        self.setWindowTitle(tr("schedtask.editor_title_edit" if task else "schedtask.editor_title_new"))
        self.resize(560, 680)
        # Flat inputs: every field (text, list, combo, spin, date) is transparent
        # so it shows the page background (the app theme otherwise fills inputs
        # with a lighter box) — just a light outline, consistent with the rest of
        # the app. The combo drop-down popup keeps a solid dark background so its
        # items stay readable.
        self.setStyleSheet(
            "QLineEdit, QPlainTextEdit, QListWidget, QComboBox, QAbstractSpinBox {"
            " background: transparent; border: 1px solid rgba(140,146,152,0.45);"
            " border-radius: 6px; }"
            "QListWidget::item { background: transparent; }"
            "QComboBox QAbstractItemView { background: #111D32; color: #E0F0FF; }")

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        root = QVBoxLayout(content)

        # ---- basics ----------------------------------------------------
        form = QFormLayout()
        self.title_edit = QLineEdit(self.task.get("title", ""))
        # Description is the source of truth. Its ✨ button GENERATES the Prompt
        # (Input) FROM the description — the title is just the task's label and
        # is not used as the basis for generation.
        self.desc_edit = QPlainTextEdit(self.task.get("description", ""))
        self.desc_edit.setMaximumHeight(90)
        self.gen_desc_btn = QPushButton("")
        self.gen_desc_btn.setIcon(icon("sparkle"))
        self.gen_desc_btn.setFixedWidth(34)
        self.gen_desc_btn.setToolTip(tr("schedtask.gen_desc_tooltip"))
        self.gen_desc_btn.clicked.connect(self._gen_prompt_from_description)
        desc_box = QWidget()
        db = QHBoxLayout(desc_box)
        db.setContentsMargins(0, 0, 0, 0)
        db.addWidget(self.desc_edit, 1)
        db.addWidget(self.gen_desc_btn, alignment=Qt.AlignTop)
        self.type_combo = QComboBox()
        for t in EDITOR_TASK_TYPES:
            self.type_combo.addItem(tr(f"schedtask.type.{t}"), t)
        self._select(self.type_combo, self.task.get("task_type", "cowork"))
        # Hide task type row — users don't need to choose it anymore.
        self.type_combo.setParent(None)  # remove from UI
        self.type_combo = None
        self.priority_combo = QComboBox()
        for p in PRIORITIES:
            self.priority_combo.addItem(tr(f"schedtask.priority.{p}"), p)
        self._select(self.priority_combo, self.task.get("priority", "medium"))
        self.status_combo = QComboBox()
        for s in STATUSES:
            self.status_combo.addItem(tr(f"schedtask.status.{s}"), s)
        self._select(self.status_combo, self.task.get("status", "backlog"))
        self.workspace_combo = QComboBox()
        self.workspace_combo.addItem(tr("schedtask.no_workspace"), "")
        for p in list_projects():
            self.workspace_combo.addItem(p.name, p.project_id)
        self._select(self.workspace_combo, self.task.get("project_id", ""))
        # Which model runs the task: pick a provider + model directly (blank =
        # the machine's own Settings default). Replaces the old Admin-agent
        # preset picker. Same drop-list + "Load models" pattern as Agents Admin.
        self.provider_combo = QComboBox()
        self.provider_combo.addItem(tr("schedtask.provider_default"), _PROVIDER_DEFAULT)
        for key, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, key)
        self._select(self.provider_combo, self.task.get("provider", "") or _PROVIDER_DEFAULT)
        self.provider_combo.currentIndexChanged.connect(self._refresh_model_combo)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        if self.task.get("model"):
            self.model_combo.addItem(self.task["model"])
        self.model_combo.setEditText(self.task.get("model", "") or "")
        self.model_combo.lineEdit().setPlaceholderText(tr("schedtask.model_placeholder"))
        self.load_models_btn = QPushButton()
        self.load_models_btn.setIcon(icon("download"))
        self.load_models_btn.setToolTip(tr("schedtask.load_models_tooltip"))
        self.load_models_btn.clicked.connect(self._load_live_models)
        self.load_models_btn.setEnabled(ctx is not None)
        model_box = QWidget()
        mb = QHBoxLayout(model_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.addWidget(self.model_combo, 1)
        mb.addWidget(self.load_models_btn)
        # Skill: apply a saved skill's instructions to the run ("(None)" = none).
        self.skill_combo = QComboBox()
        self.skill_combo.addItem(tr("schedtask.no_skill"), "")
        try:
            from ..core.skills import builtin_skills, list_skills

            for s in list_skills() + builtin_skills():
                self.skill_combo.addItem(s.name, s.slug)
        except Exception:  # noqa: BLE001 — a broken skill file must not block the editor
            pass
        self._select(self.skill_combo, self.task.get("skill_slug", ""))
        # Run kind: an AI agent (Cowork) or a saved Co4E flow (node graph). A
        # flow task runs the whole graph wave-by-wave via the Co4E runner.
        self.run_kind_combo = QComboBox()
        self.run_kind_combo.addItem(tr("schedtask.kind_agent"), "agent")
        self.run_kind_combo.addItem(tr("schedtask.kind_flow"), "flow")
        self.run_kind_combo.setToolTip(tr("schedtask.hint_run_kind"))
        self._select(self.run_kind_combo, "flow" if self.task.get("task_type") == "flow" else "agent")
        self.run_kind_combo.currentIndexChanged.connect(self._on_run_kind_changed)
        self.flow_combo = QComboBox()
        self.flow_combo.setToolTip(tr("schedtask.hint_flow"))
        try:
            from ..core import co4e

            for wf in co4e.list_workflows():
                self.flow_combo.addItem(wf.name, wf.id)
        except Exception:  # noqa: BLE001 — a broken flow file must not block the editor
            pass
        self._select(self.flow_combo, self.task.get("flow", {}).get("flow_id") or "")
        # Task kind: a normal one-time/manual task vs an automation (cronjob).
        # Not everything is recurring — this toggles the recurrence section below.
        self.task_mode_combo = QComboBox()
        self.task_mode_combo.addItem(tr("schedtask.mode_normal"), "normal")
        self.task_mode_combo.addItem(tr("schedtask.mode_automation"), "automation")
        self.task_mode_combo.setToolTip(tr("schedtask.hint_task_mode"))
        # Derive the initial mode from the saved schedule (recurring => automation).
        _sched0 = self.task.get("schedule", {})
        _is_auto = _sched0.get("repeat_type", "none") not in ("none", None)
        self._select(self.task_mode_combo, "automation" if _is_auto else "normal")
        self.task_mode_combo.currentIndexChanged.connect(self._on_task_mode_changed)
        form.addRow(tr("schedtask.f_task_mode"), self.task_mode_combo)
        form.addRow(tr("schedtask.f_title"), self.title_edit)
        form.addRow(tr("schedtask.f_desc"), desc_box)
        form.addRow(tr("schedtask.f_workspace"), self.workspace_combo)
        form.addRow(tr("schedtask.f_run_kind"), self.run_kind_combo)
        form.addRow(tr("schedtask.f_flow"), self.flow_combo)
        form.addRow(tr("schedtask.f_provider"), self.provider_combo)
        form.addRow(tr("schedtask.f_model"), model_box)
        form.addRow(tr("schedtask.f_skill"), self.skill_combo)
        form.addRow(tr("schedtask.f_priority"), self.priority_combo)
        form.addRow(tr("schedtask.f_status"), self.status_combo)
        root.addLayout(form)
        self._main_form = form
        self._model_box = model_box
        self._on_run_kind_changed()   # apply agent/flow row visibility

        # ---- Schedule Setup (one-time OR recurring: daily/weekly/monthly/cron) --
        sched = self.task.get("schedule", {})
        sg = QGroupBox(tr("schedtask.g_schedule"))
        sf = QFormLayout(sg)
        self.sched_enabled = QCheckBox(tr("schedtask.sched_enable"))
        self.sched_enabled.setChecked(bool(sched.get("enabled")))
        self.run_at_edit = QDateTimeEdit()
        # 12-hour clock with an AM/PM field so 9:00 vs 21:00 can't be confused.
        # Storage stays 24-hour ("yyyy-MM-dd HH:mm", see _save) — only the on-
        # screen display is 12-hour; QDateTimeEdit maps between them internally.
        self.run_at_edit.setDisplayFormat("yyyy-MM-dd hh:mm AP")
        self.run_at_edit.setCalendarPopup(True)
        existing = parse_run_at(sched.get("run_at"))
        base = existing or (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
        self.run_at_edit.setDateTime(
            QDateTime(base.year, base.month, base.day, base.hour, base.minute, 0))
        # Repeat: none (one-time) / daily / weekly / monthly / cron (custom interval).
        # For a repeating task the run_at's TIME-OF-DAY is the daily/weekly/… notify
        # time; compute_next_run() rolls it forward after each run.
        self.repeat_combo = QComboBox()
        for r in REPEAT_TYPES:
            self.repeat_combo.addItem(tr(f"schedtask.repeat.{r}"), r)
        self._select(self.repeat_combo, sched.get("repeat_type", "none"))
        self.repeat_combo.currentIndexChanged.connect(self._on_repeat_changed)
        # Cron expression (only relevant when repeat = cron): "min hour dom mon dow".
        self.cron_edit = QLineEdit(sched.get("cron_expression") or "")
        self.cron_edit.setPlaceholderText(tr("schedtask.cron_placeholder"))
        self.cron_edit.setToolTip(tr("schedtask.cron_hint"))
        # Sample picker — inserts a correct expression so users don't guess syntax.
        self.cron_sample = QComboBox()
        self.cron_sample.addItem(tr("schedtask.cron_sample_pick"), "")
        for label_key, expr in _CRON_SAMPLES:
            self.cron_sample.addItem(f"{tr(label_key)}  ·  {expr}", expr)
        self.cron_sample.setToolTip(tr("schedtask.cron_sample_tooltip"))
        self.cron_sample.currentIndexChanged.connect(self._on_cron_sample)
        cron_box = QWidget()
        cbx = QHBoxLayout(cron_box)
        cbx.setContentsMargins(0, 0, 0, 0)
        cbx.addWidget(self.cron_edit, 1)
        cbx.addWidget(self.cron_sample)
        self._cron_box = cron_box
        self.cron_hint = QLabel(tr("schedtask.cron_hint"))
        self.cron_hint.setObjectName("hint")
        self.cron_hint.setWordWrap(True)
        # Recurrence day filters.
        self.working_days_chk = QCheckBox(tr("schedtask.workdays_only"))
        self.working_days_chk.setChecked(bool(sched.get("working_days_only")))
        self.skip_holidays_chk = QCheckBox(tr("schedtask.skip_holidays"))
        self.skip_holidays_chk.setChecked(bool(sched.get("skip_holidays")))
        self.holiday_country_edit = QLineEdit(sched.get("holiday_country", "VN") or "VN")
        self.holiday_country_edit.setMaximumWidth(60)
        self.holiday_country_edit.setToolTip(tr("schedtask.holiday_country"))
        hol_row = QWidget()
        hr = QHBoxLayout(hol_row)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.addWidget(self.skip_holidays_chk)
        hr.addWidget(self.holiday_country_edit)
        hr.addStretch(1)
        # Reminder channel: send a notification when the (scheduled/cron) task
        # finishes — None / Teams (webhook) / Outlook (local desktop, no login).
        ex_sched = self.task.get("execution", {})
        self.notify_combo = QComboBox()
        for ch, key in (("none", "notify.none"), ("teams", "notify.teams"),
                        ("outlook", "notify.outlook")):
            self.notify_combo.addItem(tr(f"schedtask.{key}"), ch)
        self._select(self.notify_combo, ex_sched.get("notify_channel", "none"))
        self.notify_combo.currentIndexChanged.connect(self._on_notify_changed)
        self.notify_email_edit = QLineEdit(ex_sched.get("notify_email", "") or "")
        self.notify_email_edit.setPlaceholderText(tr("schedtask.notify_email_placeholder"))
        self.notify_hint = QLabel(tr("schedtask.notify_hint"))
        self.notify_hint.setObjectName("hint")
        self.notify_hint.setWordWrap(True)
        tz_lbl = QLabel(tr("schedtask.tz_local_note"))
        tz_lbl.setObjectName("hint")
        sf.addRow("", self.sched_enabled)
        sf.addRow(tr("schedtask.f_run_at"), self.run_at_edit)
        sf.addRow(tr("schedtask.f_repeat"), self.repeat_combo)
        sf.addRow(tr("schedtask.f_cron"), self._cron_box)
        sf.addRow("", self.cron_hint)
        sf.addRow("", self.working_days_chk)
        sf.addRow("", hol_row)
        sf.addRow(tr("schedtask.f_notify"), self.notify_combo)
        sf.addRow(tr("schedtask.f_notify_email"), self.notify_email_edit)
        sf.addRow("", self.notify_hint)
        sf.addRow("", tz_lbl)
        root.addWidget(sg)
        # Recurrence rows are shown only in "automation" mode (see _on_task_mode_changed).
        self._sched_form = sf
        self._recurrence_widgets = [self.repeat_combo, self.working_days_chk, hol_row]
        self._cron_widgets = [self._cron_box, self.cron_hint]
        self._on_task_mode_changed()   # apply normal/automation visibility
        self._on_notify_changed()      # show/hide the recipient field

        # ---- Input (files / links / prompt — always combined) -----------
        inp = self.task.get("input", {})
        ig = QGroupBox(tr("schedtask.g_input"))
        iform = QFormLayout(ig)
        # Prompt (no ✨ button here anymore — the Description's ✨ generate now
        # auto-fills this too; after Run the task combines this prompt with the
        # attached files/links below to gather info and act on the request).
        self.manual_text = QPlainTextEdit(inp.get("manual_text") or "")
        self.manual_text.setMaximumHeight(70)
        # Files — a list (not a single text field) so attaching several is a
        # repeated "+" click, not hand-typed ';'-separated text; "+" opens a
        # multi-select file dialog and APPENDS (never wipes what's already
        # there), the trash button removes just the selected row(s).
        self.files_list = QListWidget()
        self.files_list.setMaximumHeight(90)
        self.files_list.setSelectionMode(QListWidget.ExtendedSelection)
        for p in inp.get("file_paths", []) or []:
            self.files_list.addItem(p)
        self.files_add_btn = QPushButton()
        self.files_add_btn.setIcon(icon("plus"))
        self.files_add_btn.setFixedWidth(34)
        self.files_add_btn.clicked.connect(self._add_files)
        self.files_del_btn = QPushButton()
        self.files_del_btn.setIcon(icon("trash"))
        self.files_del_btn.setFixedWidth(34)
        self.files_del_btn.clicked.connect(lambda: self._remove_selected(self.files_list))
        files_btns = QVBoxLayout()
        files_btns.addWidget(self.files_add_btn)
        files_btns.addWidget(self.files_del_btn)
        files_btns.addStretch(1)
        frow = QWidget()
        fl = QHBoxLayout(frow)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.files_list, 1)
        fl.addLayout(files_btns)

        # Links — same "+"-list pattern; "+" prompts for one URL at a time
        # (fetched best-effort and inlined as context, same as file attachments).
        self.links_list = QListWidget()
        self.links_list.setMaximumHeight(90)
        self.links_list.setSelectionMode(QListWidget.ExtendedSelection)
        for u in inp.get("links", []) or []:
            self.links_list.addItem(u)
        self.links_add_btn = QPushButton()
        self.links_add_btn.setIcon(icon("plus"))
        self.links_add_btn.setFixedWidth(34)
        self.links_add_btn.clicked.connect(self._add_link)
        self.links_del_btn = QPushButton()
        self.links_del_btn.setIcon(icon("trash"))
        self.links_del_btn.setFixedWidth(34)
        self.links_del_btn.clicked.connect(lambda: self._remove_selected(self.links_list))
        links_btns = QVBoxLayout()
        links_btns.addWidget(self.links_add_btn)
        links_btns.addWidget(self.links_del_btn)
        links_btns.addStretch(1)
        lrow = QWidget()
        ll = QHBoxLayout(lrow)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(self.links_list, 1)
        ll.addLayout(links_btns)

        iform.addRow(tr("schedtask.f_manual_text"), self.manual_text)
        iform.addRow(tr("schedtask.f_files"), frow)
        iform.addRow(tr("schedtask.f_links"), lrow)
        root.addWidget(ig)

        # ---- Dependency / chain ---------------------------------------------
        dep = self.task.get("dependency", {})
        dg = QGroupBox(tr("schedtask.g_dependency"))
        dform = QFormLayout(dg)
        self.next_combo = QComboBox()
        self.next_combo.addItem(tr("schedtask.none"), None)
        for t in self.all_tasks:
            self.next_combo.addItem(t.get("title") or t["task_id"][:8], t["task_id"])
        self._select(self.next_combo, dep.get("next_task_id"))
        self.run_next_combo = QComboBox()
        for m in RUN_NEXT_MODES:
            self.run_next_combo.addItem(tr(f"schedtask.runnext.{m}"), m)
        self._select(self.run_next_combo, dep.get("run_next_mode", "none"))
        self.pass_output_chk = QCheckBox(tr("schedtask.pass_output"))
        self.pass_output_chk.setChecked(bool(dep.get("pass_output_to_next")))
        self.chain_warn = QLabel("")
        self.chain_warn.setObjectName("hint")
        self.chain_warn.setWordWrap(True)
        self.next_combo.currentIndexChanged.connect(self._check_chain)
        # Fan-in: tick every task this one must WAIT for — it won't run until
        # ALL of them are Done (parallel predecessors feeding one successor).
        self.depends_list = QListWidget()
        self.depends_list.setMaximumHeight(96)
        current_deps = set(dep.get("depends_on") or [])
        for t in self.all_tasks:
            item = QListWidgetItem(t.get("title") or t["task_id"][:8])
            item.setData(Qt.UserRole, t["task_id"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if t["task_id"] in current_deps else Qt.Unchecked)
            self.depends_list.addItem(item)
        dform.addRow(tr("schedtask.f_next_task"), self.next_combo)
        dform.addRow(tr("schedtask.f_run_next"), self.run_next_combo)
        dform.addRow("", self.pass_output_chk)
        dform.addRow(tr("schedtask.f_depends_on"), self.depends_list)
        dform.addRow("", self.chain_warn)
        root.addWidget(dg)
        self._check_chain()

        # ---- Execution ------------------------------------------------------
        ex = self.task.get("execution", {})
        eg = QGroupBox(tr("schedtask.g_execution"))
        eform = QFormLayout(eg)
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setValue(int(ex.get("max_retry", 0) or 0))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 24 * 3600)
        self.timeout_spin.setValue(int(ex.get("timeout_sec", 600) or 600))
        self.timeout_spin.setSuffix(" s")
        self.approval_chk = QCheckBox(tr("schedtask.requires_approval"))
        self.approval_chk.setChecked(bool(ex.get("requires_approval")))
        # Teams notification checkboxes removed from the task editor per request
        # (no "notify to Teams on complete/error" option here anymore).
        eform.addRow(tr("schedtask.f_retry"), self.retry_spin)
        eform.addRow(tr("schedtask.f_timeout"), self.timeout_spin)
        eform.addRow("", self.approval_chk)
        root.addWidget(eg)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setIcon(icon("save"))
        buttons.button(QDialogButtonBox.Cancel).setIcon(icon("close"))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._apply_hints()
        # Scrolling the form must never spin a combo/spin/date box the cursor
        # happens to hover — values only change after clicking into a field.
        from .widgets import guard_wheel
        guard_wheel(self)

    def _apply_hints(self) -> None:
        """Tooltip hints on every non-obvious control, so each option explains
        itself on hover."""
        hints = {
            self.status_combo: "schedtask.hint_status",
            self.workspace_combo: "schedtask.hint_workspace",
            self.provider_combo: "schedtask.hint_provider",
            self.model_combo: "schedtask.hint_model",
            self.skill_combo: "schedtask.hint_skill",
            self.sched_enabled: "schedtask.hint_sched_enable",
            self.run_at_edit: "schedtask.hint_run_at",
            self.files_list: "schedtask.hint_files",
            self.files_add_btn: "schedtask.hint_files",
            self.links_list: "schedtask.hint_links",
            self.links_add_btn: "schedtask.hint_links",
            self.next_combo: "schedtask.hint_next_task",
            self.run_next_combo: "schedtask.hint_run_next",
            self.pass_output_chk: "schedtask.hint_pass_output",
            self.depends_list: "schedtask.hint_depends",
            self.retry_spin: "schedtask.hint_retry",
            self.timeout_spin: "schedtask.hint_timeout",
            self.approval_chk: "schedtask.hint_approval",
        }
        for widget, key in hints.items():
            widget.setToolTip(tr(key))

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _select(combo: QComboBox, data) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr("schedtask.pick_files"))
        for f in files:
            self.files_list.addItem(f)

    def _add_link(self) -> None:
        url, ok = QInputDialog.getText(self, tr("schedtask.add_link_title"),
                                       tr("schedtask.add_link_label"))
        url = url.strip()
        if ok and url:
            self.links_list.addItem(url)

    @staticmethod
    def _remove_selected(list_widget: QListWidget) -> None:
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))

    def _refresh_model_combo(self) -> None:
        """Repopulate the editable model list from whatever was fetched for the
        selected provider (keeps whatever the user has typed)."""
        provider_key = self.provider_combo.currentData()
        current_text = self.model_combo.currentText().strip()
        models = self._live_models.get(provider_key, []) if provider_key else []
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setEditText(current_text)
        self.model_combo.blockSignals(False)

    def _load_live_models(self) -> None:
        """Fetch each provider's real model list on demand (same fetch Agents
        Admin / Settings use) so the Model box becomes a real drop-list."""
        if self.ctx is None:
            return
        self.load_models_btn.setEnabled(False)
        ctx = self.ctx

        def job(_worker: AgentWorker):
            from ..core import preview_ai

            return preview_ai.fetch_live_models(ctx)

        def done(result: dict) -> None:
            self.load_models_btn.setEnabled(True)
            self._live_models = result or {}
            self._refresh_model_combo()
            if not self._live_models:
                QMessageBox.information(self, tr("schedtask.editor_title_new"),
                                        tr("schedtask.load_models_empty"))

        def failed(err: str) -> None:
            self.load_models_btn.setEnabled(True)
            QMessageBox.warning(self, tr("schedtask.editor_title_new"), err)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._model_workers.append(w)
        w.start()

    def _check_chain(self) -> None:
        candidates = self.all_tasks + ([self._original] if self._original else [self.task])
        err = chain_error(candidates, self.task["task_id"], self.next_combo.currentData())
        nxt_id = self.next_combo.currentData()
        warn = err or ""
        if not err and nxt_id:
            nxt = next((t for t in self.all_tasks if t["task_id"] == nxt_id), None)
            if nxt and nxt.get("status") == "paused":
                warn = tr("schedtask.next_paused_warn")
        self.chain_warn.setText(warn)

    def _checked_depends_on(self) -> list:
        ids = []
        for i in range(self.depends_list.count()):
            item = self.depends_list.item(i)
            if item.checkState() == Qt.Checked:
                ids.append(item.data(Qt.UserRole))
        return ids

    def _gen_prompt_from_description(self) -> None:
        """✨ Generate the Prompt (Input) FROM the Description the user entered.
        The title is only the task's label, so it is NOT used as the basis — the
        generated content comes purely from the description. The description
        itself is left exactly as typed; only the Prompt is filled. Does nothing
        (with a hint) when the Description is empty — there's nothing to expand."""
        description = self.desc_edit.toPlainText().strip()
        if self.ctx is None or self._gen_worker is not None:
            return
        if not description:
            QMessageBox.information(self, tr("schedtask.editor_title_new"),
                                    tr("schedtask.gen_needs_description"))
            return
        self.gen_desc_btn.setEnabled(False)
        ctx = self.ctx

        def job(worker: AgentWorker):
            from ..core.ai_task_planner import generate_prompt_from_description

            return {"prompt": generate_prompt_from_description(
                ctx.build_active_provider(), description, cancel=worker.is_cancelled)}

        def done(result: dict) -> None:
            self._gen_worker = None
            self.gen_desc_btn.setEnabled(True)
            self._apply_generated_prompt(result.get("prompt", ""), description)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda _e: (setattr(self, "_gen_worker", None),
                                     self.gen_desc_btn.setEnabled(True)))
        self._gen_worker = w
        w.start()

    def _on_run_kind_changed(self, *_a) -> None:
        """Flow = run a saved Co4E flow (show the flow picker; the graph carries
        its own per-step model/skills, so hide the agent model/skill rows).
        Agent = run a Cowork agent with the chosen provider/model/skill."""
        is_flow = self.run_kind_combo.currentData() == "flow"
        self._main_form.setRowVisible(self.flow_combo, is_flow)
        self._main_form.setRowVisible(self.provider_combo, not is_flow)
        self._main_form.setRowVisible(self._model_box, not is_flow)
        self._main_form.setRowVisible(self.skill_combo, not is_flow)

    def _on_task_mode_changed(self, *_a) -> None:
        """Normal = a one-time / manual task (recurrence hidden). Automation =
        a cronjob (recurrence shown). Toggles the repeat/cron/day-filter rows."""
        auto = self.task_mode_combo.currentData() == "automation"
        for w in self._recurrence_widgets:
            self._sched_form.setRowVisible(w, auto)
        if auto:
            self.sched_enabled.setChecked(True)
            if self.repeat_combo.currentData() in ("none", None):
                self._select(self.repeat_combo, "daily")
        else:
            self._select(self.repeat_combo, "none")
        self._on_repeat_changed()

    def _on_repeat_changed(self, *_a) -> None:
        """Show the cron-expression row only in automation mode with repeat=cron."""
        auto = self.task_mode_combo.currentData() == "automation"
        is_cron = auto and self.repeat_combo.currentData() == "cron"
        for w in self._cron_widgets:
            self._sched_form.setRowVisible(w, is_cron)

    def _on_notify_changed(self, *_a) -> None:
        """The recipient field is only relevant for the Outlook email channel."""
        self.notify_email_edit.setVisible(self.notify_combo.currentData() == "outlook")

    def _on_cron_sample(self, *_a) -> None:
        """Insert the picked sample expression into the cron field, then reset
        the picker back to its placeholder row."""
        expr = self.cron_sample.currentData()
        if expr:
            self.cron_edit.setText(expr)
            self.cron_sample.blockSignals(True)
            self.cron_sample.setCurrentIndex(0)
            self.cron_sample.blockSignals(False)

    def _apply_generated_prompt(self, prompt: str, description_fallback: str = "") -> None:
        """Fill the Prompt (Input) with content generated from the description,
        falling back to the description text when generation produced nothing so
        the Prompt is never left empty. The Description field is not touched."""
        self.manual_text.setPlainText(prompt or description_fallback)

    def _save(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, tr("schedtask.editor_title_new"), tr("schedtask.title_required"))
            return
        err = chain_error(self.all_tasks + [self.task], self.task["task_id"],
                          self.next_combo.currentData())
        if err:
            QMessageBox.warning(self, tr("schedtask.g_dependency"), err)
            return
        deps = self._checked_depends_on()
        err = depends_cycle_error(self.all_tasks + [self.task], self.task["task_id"], deps)
        if err:
            QMessageBox.warning(self, tr("schedtask.g_dependency"), err)
            return
        t = self.task
        t["title"] = title
        t["description"] = self.desc_edit.toPlainText().strip()
        # Run kind: a saved Co4E flow, or an AI agent. A flow needs a selected flow.
        if self.run_kind_combo.currentData() == "flow":
            flow_id = self.flow_combo.currentData()
            if not flow_id:
                QMessageBox.warning(self, tr("schedtask.editor_title_new"), tr("schedtask.flow_required"))
                return
            t["task_type"] = "flow"
            t["flow"]["flow_id"] = flow_id
        else:
            t["flow"]["flow_id"] = None
            # No agent-type picker in the UI: default to cowork, but editing an
            # existing Co4E-code task must not silently convert it to Cowork.
            if t.get("task_type") not in ("cowork", "co4e_code"):
                t["task_type"] = "cowork"
        t["priority"] = self.priority_combo.currentData()
        t["status"] = self.status_combo.currentData()
        t["project_id"] = self.workspace_combo.currentData() or ""
        # Provider/model chosen directly (blank = machine's Settings default);
        # clear any legacy admin-agent pin so it can't override the new choice.
        t["provider"] = self.provider_combo.currentData() or ""
        t["model"] = self.model_combo.currentText().strip()
        t["skill_slug"] = self.skill_combo.currentData() or ""
        t["admin_agent_id"] = ""
        t["schedule"]["enabled"] = self.sched_enabled.isChecked()
        qdt = self.run_at_edit.dateTime()
        t["schedule"]["run_at"] = qdt.toString("yyyy-MM-dd HH:mm")
        # Recurrence (cronjob): repeat type + cron expression + day filters. The
        # branch below turns these into the concrete next run_at via
        # compute_next_run (daily/weekly/monthly/cron all supported by the engine).
        t["schedule"]["repeat_type"] = self.repeat_combo.currentData()
        t["schedule"]["cron_expression"] = self.cron_edit.text().strip() or None
        t["schedule"]["working_days_only"] = self.working_days_chk.isChecked()
        t["schedule"]["skip_holidays"] = self.skip_holidays_chk.isChecked()
        t["schedule"]["holiday_country"] = self.holiday_country_edit.text().strip().upper() or "VN"
        if t["schedule"]["enabled"]:
            from datetime import datetime as _dt

            from ..core.tasks import compute_next_run, format_run_at, shift_off_excluded_days
            if t["schedule"]["repeat_type"] == "cron":
                # due_tasks fires on run_at, so a cron schedule stores its next
                # occurrence there — recomputed again after every run.
                from ..core.cron import validate
                err_c = validate(t["schedule"]["cron_expression"] or "")
                if err_c:
                    QMessageBox.warning(self, tr("schedtask.g_schedule"),
                                        tr("schedtask.cron_invalid", err=err_c))
                    return
                nxt = compute_next_run(t, _dt.now())
                if nxt is None:
                    QMessageBox.warning(self, tr("schedtask.g_schedule"),
                                        tr("schedtask.cron_never_fires"))
                    return
                t["schedule"]["run_at"] = format_run_at(nxt)
            elif t["schedule"]["repeat_type"] == "none":
                # One-time run set on a weekend/holiday → shift to the next
                # allowed day, same time.
                base = parse_run_at(t["schedule"]["run_at"])
                if base is not None:
                    t["schedule"]["run_at"] = format_run_at(
                        shift_off_excluded_days(base, t["schedule"]))
            else:
                # Repeating (daily/weekly/monthly) saved with a run_at already
                # in the past must mean "next occurrence at that time-of-day",
                # NOT "run immediately as catch-up" — e.g. saving "daily 09:00"
                # at 15:00 schedules tomorrow 09:00, it doesn't fire right now.
                base = parse_run_at(t["schedule"]["run_at"])
                if base is not None and base <= _dt.now():
                    nxt = compute_next_run(t, _dt.now())
                    if nxt is not None:
                        t["schedule"]["run_at"] = format_run_at(nxt)
        # Enabling a schedule puts the card (back) on the calendar — including
        # a task that already ran (done/failed) or was parked waiting: only a
        # deliberate Paused, or one currently Running, keeps its lane.
        if t["schedule"]["enabled"] and t["status"] in (
                "backlog", "done", "failed", "waiting_input"):
            t["status"] = "scheduled"
        t["input"]["manual_text"] = self.manual_text.toPlainText().strip() or None
        t["input"]["file_paths"] = [self.files_list.item(i).text()
                                    for i in range(self.files_list.count())]
        t["input"]["links"] = [self.links_list.item(i).text()
                               for i in range(self.links_list.count())]
        # "previous_task_output" mode is set programmatically by the Dependency
        # group's "pass output to next" chaining (see task_scheduler._apply_chain
        # / schedule_task_tab._create_next_from_output) — keep it as-is here;
        # otherwise infer the simple mode from what the user actually filled in
        # (no separate "Input mode" picker to force a choice).
        if t["input"].get("mode") != "previous_task_output":
            if t["input"]["manual_text"]:
                t["input"]["mode"] = "manual"
            elif t["input"]["file_paths"]:
                t["input"]["mode"] = "file"
            else:
                t["input"]["mode"] = "empty"
        # Output format follows whatever the task's own description/prompt
        # asks for — no separate "Output mode" picker.
        t["dependency"]["next_task_id"] = self.next_combo.currentData()
        t["dependency"]["run_next_mode"] = self.run_next_combo.currentData()
        t["dependency"]["pass_output_to_next"] = self.pass_output_chk.isChecked()
        t["dependency"]["depends_on"] = deps
        t["execution"]["max_retry"] = self.retry_spin.value()
        t["execution"]["timeout_sec"] = self.timeout_spin.value()
        t["execution"]["requires_approval"] = self.approval_chk.isChecked()
        # Reminder channel (Teams / Outlook / none). A chosen channel notifies on
        # both completion and error (the scheduler's _notify routes by channel).
        channel = self.notify_combo.currentData()
        email = self.notify_email_edit.text().strip()
        if channel == "outlook" and not email:
            QMessageBox.warning(self, tr("schedtask.g_schedule"), tr("schedtask.notify_need_email"))
            return
        if channel == "teams":
            notifier = self.ctx.teams_notifier() if self.ctx is not None else None
            if notifier is None or not notifier.configured():
                QMessageBox.warning(self, tr("schedtask.g_schedule"), tr("schedtask.notify_need_webhook"))
                return
        t["execution"]["notify_channel"] = channel
        t["execution"]["notify_email"] = email
        t["execution"]["notify_on_complete"] = channel != "none"
        t["execution"]["notify_on_error"] = channel != "none"
        self.edited_task = t
        self.accept()
