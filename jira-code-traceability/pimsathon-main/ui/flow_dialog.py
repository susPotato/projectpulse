"""Flow builder dialog — design a Req→Demo pipeline and run it.

The user adds ordered steps, attaches a skill / AI agent / hint to each step,
saves the result as a reusable template, and runs the whole flow. Running a flow
enqueues each step as a Code-agent turn (executed sequentially).
"""
from __future__ import annotations

import copy
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QScrollArea, QSpinBox, QSplitter, QTabWidget, QVBoxLayout,
    QWidget,
)

from ..config import PROVIDER_LABELS
from ..core.custom_agents import list_agents
from ..core.flows import (
    Flow, FlowStep, SubAgent, default_req_to_demo, delete_flow, list_flows,
    save_flow,
)
from ..core.skills import list_skills
from ..core.worker import AgentWorker
from ..i18n import tr
from .agent_manager_tab import AgentManagerTab
from .icons import icon
from .skill_manager_tab import SkillManagerTab


class FlowBuilderDialog(QDialog):
    def __init__(self, parent=None, ctx=None):
        super().__init__(parent)
        self.setWindowTitle(tr("flow.title"))
        self.setMinimumSize(820, 560)
        self.run_requested = False
        self._ctx = ctx                 # for the AI "generate task from hint" button
        self._gen_worker = None
        self._flow = default_req_to_demo()
        self._loaded_name = ""  # template name currently loaded (for rename-safe save)

        root = QVBoxLayout(self)

        # The dialog hosts two inner tabs: the Flow builder itself, and the
        # Agent Manager (create/edit/delete reusable Agent presets) so both
        # live in one window instead of a separate top-level app tab.
        self.tabs = QTabWidget()
        flow_page = QWidget()
        fl = QVBoxLayout(flow_page)

        # --- template bar ---
        bar = QHBoxLayout()
        bar.addWidget(QLabel(tr("flow.template")))
        self.tpl_combo = QComboBox()
        self.tpl_combo.activated.connect(self._load_selected_template)
        bar.addWidget(self.tpl_combo, 1)
        tpl_btn = QPushButton(tr("flow.load_builtin"))
        tpl_btn.setIcon(icon("download"))
        tpl_btn.clicked.connect(self._load_builtin)
        new_btn = QPushButton(tr("flow.new"))
        new_btn.setIcon(icon("new"))
        new_btn.clicked.connect(self._new_flow)
        del_btn = QPushButton(tr("flow.delete_template"))
        del_btn.setIcon(icon("trash"))
        del_btn.clicked.connect(self._delete_template)
        for b in (tpl_btn, new_btn, del_btn):
            bar.addWidget(b)
        fl.addLayout(bar)

        # --- name/description ---
        meta = QFormLayout()
        self.name_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        meta.addRow(tr("flow.name_label"), self.name_edit)
        meta.addRow(tr("flow.description_label"), self.desc_edit)
        fl.addLayout(meta)

        # --- steps list | step editor ---
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel(tr("flow.stages")))
        self.steps_list = QListWidget()
        self.steps_list.currentRowChanged.connect(self._load_step_into_editor)
        ll.addWidget(self.steps_list, 1)
        order = QHBoxLayout()
        up = QPushButton("↑")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("↓")
        down.clicked.connect(lambda: self._move(1))
        rm = QPushButton(tr("flow.remove_stage"))
        rm.setIcon(icon("trash"))
        rm.clicked.connect(self._remove_step)
        for b in (up, down, rm):
            order.addWidget(b)
        ll.addLayout(order)
        split.addWidget(left)

        right_content = QWidget()
        rl = QFormLayout(right_content)
        rl.setRowWrapPolicy(QFormLayout.WrapLongRows)
        rl.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.step_name = QLineEdit()
        self.step_hint = QLineEdit()
        self.step_prompt = QPlainTextEdit()
        self.step_prompt.setMinimumHeight(120)
        self.step_skill = QComboBox()
        # "AI provider" (openai_compat / anthropic / …) + the "Agent" (model)
        # WITHIN that provider (DeepSeek, qwen, … — fetched live from the
        # provider's own model list), side by side on one row.
        self.step_agent = QComboBox()
        self.step_model = QComboBox()
        self.step_model.addItem(tr("flow.default_model"), "")
        self.step_agent.currentIndexChanged.connect(self._reload_step_models)
        agent_row = QWidget()
        arow = QHBoxLayout(agent_row)
        arow.setContentsMargins(0, 0, 0, 0)
        arow.addWidget(self.step_agent, 1)
        arow.addWidget(QLabel(tr("flow.model_label")))
        arow.addWidget(self.step_model, 1)
        # Task field with an AI "generate from hint" button on top.
        task_box = QWidget()
        tb = QVBoxLayout(task_box)
        tb.setContentsMargins(0, 0, 0, 0)
        self._gen_prompt_btn = QPushButton(tr("flow.gen_task_from_hint"))
        self._gen_prompt_btn.setIcon(icon("sparkle"))
        self._gen_prompt_btn.setToolTip(tr("flow.gen_task_tooltip"))
        self._gen_prompt_btn.clicked.connect(self._gen_prompt)
        tb.addWidget(self._gen_prompt_btn)
        tb.addWidget(self.step_prompt)
        rl.addRow(tr("flow.stage_name"), self.step_name)
        rl.addRow(tr("flow.hint"), self.step_hint)               # Hint sits above the task
        rl.addRow(tr("flow.task_prompt"), task_box)
        rl.addRow(tr("flow.skill"), self.step_skill)
        rl.addRow(tr("flow.agent"), agent_row)

        # --- attachments ---
        self._step_attachments: List[str] = []
        self._step_subagents: List[SubAgent] = []
        self.attach_btn = QPushButton(tr("flow.attach_files"))
        self.attach_btn.setIcon(icon("attach"))
        self.attach_btn.clicked.connect(self._pick_attachments)
        rl.addRow(tr("flow.attachments"), self.attach_btn)

        # --- per-stage execution options ---
        self.compact_chk = QCheckBox(tr("flow.compact_after_run"))
        self.compact_chk.setToolTip(tr("flow.compact_after_run_tooltip"))
        rl.addRow("", self.compact_chk)
        self.verify_chk = QCheckBox(tr("flow.self_verify"))
        self.verify_chk.setToolTip(tr("flow.self_verify_tooltip"))
        rl.addRow("", self.verify_chk)
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setToolTip(tr("flow.review_retries_tooltip"))
        rl.addRow(tr("flow.review_retries"), self.retries_spin)

        # --- parallel sub-agents (non-empty => this stage fans out) ---
        sub_box = QWidget()
        sb = QVBoxLayout(sub_box)
        sb.setContentsMargins(0, 0, 0, 0)
        self.subagents_list = QListWidget()
        # No cap on the number of sub-agents; the list itself scrolls once it
        # grows past this height instead of stretching (and overlapping) the
        # rest of the form.
        self.subagents_list.setMinimumHeight(90)
        self.subagents_list.setMaximumHeight(160)
        sb.addWidget(self.subagents_list)
        sub_form = QHBoxLayout()
        self.sub_name_edit = QLineEdit()
        self.sub_name_edit.setPlaceholderText(tr("flow.subagent_name_placeholder"))
        self.sub_prompt_edit = QLineEdit()
        self.sub_prompt_edit.setPlaceholderText(tr("flow.subagent_task_placeholder"))
        sub_add = QPushButton(tr("flow.subagent_add"))
        sub_add.setIcon(icon("plus"))
        sub_add.clicked.connect(self._add_subagent)
        sub_remove = QPushButton(tr("flow.subagent_remove"))
        sub_remove.setIcon(icon("minus"))
        sub_remove.clicked.connect(self._remove_subagent)
        sub_form.addWidget(self.sub_name_edit)
        sub_form.addWidget(self.sub_prompt_edit, 1)
        sub_form.addWidget(sub_add)
        sub_form.addWidget(sub_remove)
        sb.addLayout(sub_form)

        # --- add a sub-agent straight from a saved custom Agent preset ---
        agent_form = QHBoxLayout()
        self.agent_picker = QComboBox()
        self._reload_agent_picker()
        agent_form.addWidget(self.agent_picker, 1)
        agent_add = QPushButton(tr("flow.subagent_add_from_agent"))
        agent_add.setIcon(icon("plus"))
        agent_add.clicked.connect(self._add_subagent_from_agent)
        agent_form.addWidget(agent_add)
        sb.addLayout(agent_form)

        sub_hint = QLabel(tr("flow.subagent_hint"))
        sub_hint.setObjectName("hint")
        sub_hint.setWordWrap(True)
        sb.addWidget(sub_hint)
        rl.addRow(tr("flow.parallel_agents"), sub_box)

        step_btns = QHBoxLayout()
        add_step = QPushButton(tr("flow.add_stage"))
        add_step.setIcon(icon("plus"))
        add_step.setObjectName("primary")
        add_step.clicked.connect(self._add_step)
        upd_step = QPushButton(tr("flow.update_stage"))
        upd_step.setIcon(icon("refresh"))
        upd_step.clicked.connect(self._update_step)
        step_btns.addWidget(add_step)
        step_btns.addWidget(upd_step)
        rl.addRow(step_btns)

        # The step editor form can grow tall (many rows + a growing sub-agent
        # list); scroll it instead of letting rows compress/overlap when the
        # dialog is smaller than the content's natural height.
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QScrollArea.NoFrame)
        right_scroll.setWidget(right_content)
        split.addWidget(right_scroll)
        split.setSizes([300, 520])
        fl.addWidget(split, 1)

        # --- bottom actions ---
        actions = QHBoxLayout()
        save_btn = QPushButton(tr("flow.save_template"))
        save_btn.setIcon(icon("save"))
        save_btn.clicked.connect(self._save_template)
        run_btn = QPushButton(tr("flow.run"))
        run_btn.setIcon(icon("play"))
        run_btn.setObjectName("primary")
        run_btn.clicked.connect(self._run)
        close_btn = QPushButton(tr("flow.close"))
        close_btn.setIcon(icon("close"))
        close_btn.clicked.connect(self.reject)
        actions.addWidget(save_btn)
        actions.addStretch(1)
        actions.addWidget(run_btn)
        actions.addWidget(close_btn)
        fl.addLayout(actions)

        self.tabs.addTab(flow_page, tr("flow.tab_flow"))
        self.agent_manager_tab = AgentManagerTab(self._ctx)
        self.tabs.addTab(self.agent_manager_tab, tr("flow.tab_agents"))
        self.skill_manager_tab = SkillManagerTab(self._ctx)
        self.tabs.addTab(self.skill_manager_tab, tr("flow.tab_skills"))
        # Agents/skills created/edited/deleted on their tabs must show up back
        # on the Flow tab (sub-agent picker / step skill combo) without
        # reopening the whole dialog — cheap enough (reads a small folder) to
        # just refresh on every tab switch rather than wiring a change signal.
        self.tabs.currentChanged.connect(lambda _i: self._reload_agent_picker())
        self.tabs.currentChanged.connect(lambda _i: self._reload_skill_combo())
        root.addWidget(self.tabs, 1)

        self._populate_combos()
        self._reload_templates()
        self._bind_flow(self._flow)

    # ---- AI: generate task prompt from the hint ---------------------
    def _gen_prompt(self) -> None:
        hint = self.step_hint.text().strip()
        name = self.step_name.text().strip()
        if not hint and not name:
            self.step_hint.setFocus()
            return
        if self._ctx is None:
            return
        self._gen_prompt_btn.setEnabled(False)
        self._gen_prompt_btn.setText(tr("skills.generating"))
        ctx = self._ctx

        def job(worker: AgentWorker):
            from ..core.flows import generate_task_prompt
            return {"prompt": generate_task_prompt(
                ctx.build_active_provider(), name, hint, worker.is_cancelled)}

        w = AgentWorker(job)
        w.finished_ok.connect(self._on_gen_prompt)
        w.failed.connect(lambda _e: self._reset_gen_prompt_btn())
        self._gen_worker = w
        w.start()

    def _on_gen_prompt(self, result) -> None:
        text = (result or {}).get("prompt", "")
        if text:
            self.step_prompt.setPlainText(text)
        self._reset_gen_prompt_btn()

    def _reset_gen_prompt_btn(self) -> None:
        self._gen_prompt_btn.setEnabled(True)
        self._gen_prompt_btn.setText(tr("flow.gen_task_from_hint"))

    # ---- attachments (per stage) -------------------------------------
    def _pick_attachments(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, tr("flow.attach_files"))
        if chosen:
            self._step_attachments = chosen
            self._refresh_attach_btn()

    def _refresh_attach_btn(self) -> None:
        n = len(self._step_attachments)
        label = tr("flow.attach_files_count", n=n) if n else tr("flow.attach_files")
        self.attach_btn.setText(label)
        self.attach_btn.setToolTip("\n".join(self._step_attachments))

    # ---- parallel sub-agents (per stage) ------------------------------
    # ``self._step_subagents`` is the source of truth; ``subagents_list`` is
    # just its display — avoids any lossy re-parsing of the list widget text.
    def _add_subagent(self) -> None:
        name = self.sub_name_edit.text().strip()
        if not name:
            self.sub_name_edit.setFocus()
            return
        prompt = self.sub_prompt_edit.text().strip()
        self._step_subagents.append(SubAgent(name=name, prompt=prompt))
        self.sub_name_edit.clear()
        self.sub_prompt_edit.clear()
        self._refresh_subagents_list()

    def _remove_subagent(self) -> None:
        row = self.subagents_list.currentRow()
        if 0 <= row < len(self._step_subagents):
            self._step_subagents.pop(row)
            self._refresh_subagents_list()

    def _refresh_subagents_list(self) -> None:
        self.subagents_list.clear()
        for sub in self._step_subagents:
            text = f"{sub.name}: {sub.prompt}" if sub.prompt else sub.name
            self.subagents_list.addItem(QListWidgetItem(text))

    # ---- add a sub-agent from a saved custom Agent preset -------------
    def _reload_agent_picker(self) -> None:
        self.agent_picker.clear()
        agents = list_agents()
        if not agents:
            self.agent_picker.addItem(tr("flow.subagent_no_agents"), None)
            self.agent_picker.setEnabled(False)
            return
        self.agent_picker.setEnabled(True)
        for a in agents:
            self.agent_picker.addItem(a.name, a)

    def _add_subagent_from_agent(self) -> None:
        agent = self.agent_picker.currentData()
        if agent is None:
            return
        self._step_subagents.append(
            SubAgent(name=agent.name, prompt=agent.prompt, agent=agent.provider,
                     model=agent.model))
        self._refresh_subagents_list()

    # ---- combos / templates -----------------------------------------
    def _reload_skill_combo(self) -> None:
        """Skills added/edited/deleted on the Skills tab must show up here
        without reopening the dialog — same reasoning as _reload_agent_picker
        (cheap: reads a small folder)."""
        current = self.step_skill.currentData()
        self.step_skill.blockSignals(True)
        self.step_skill.clear()
        self.step_skill.addItem(tr("flow.none"), "")
        for s in list_skills():
            self.step_skill.addItem(s.name, s.name)
        idx = self.step_skill.findData(current)
        self.step_skill.setCurrentIndex(max(0, idx))
        self.step_skill.blockSignals(False)

    def _populate_combos(self) -> None:
        self._reload_skill_combo()
        self.step_agent.blockSignals(True)
        self.step_agent.clear()
        self.step_agent.addItem(tr("flow.default_agent"), "")
        for key, label in PROVIDER_LABELS.items():
            self.step_agent.addItem(label, key)
        self.step_agent.blockSignals(False)
        self._reload_step_models()

    def _reload_step_models(self) -> None:
        """Fill the step's Agent (model) combo with the models the selected
        AI provider actually serves (DeepSeek, qwen, … — fetched live in the
        background so the dialog never blocks on a network call)."""
        if self._ctx is None:   # dialog opened without an app context (tests)
            return
        provider_key = self.step_agent.currentData() or self._ctx.config.active_provider
        keep = getattr(self, "_pending_step_model", "") or (self.step_model.currentData() or "")
        self.step_model.clear()
        self.step_model.addItem(tr("flow.default_model"), "")
        if keep:   # keep the stored choice selectable even before the fetch lands
            self.step_model.addItem(keep, keep)
            self.step_model.setCurrentIndex(1)

        def job(worker: AgentWorker):
            try:
                provider = self._ctx.build_provider_for(provider_key)
                return {"models": provider.list_models() or [], "provider": provider_key}
            except Exception:  # noqa: BLE001 — model list is best-effort
                return {"models": [], "provider": provider_key}

        def done(result: dict) -> None:
            if result.get("provider") != (self.step_agent.currentData()
                                          or self._ctx.config.active_provider):
                return   # provider changed again while fetching — stale reply
            current = self.step_model.currentData() or ""
            self.step_model.blockSignals(True)
            self.step_model.clear()
            self.step_model.addItem(tr("flow.default_model"), "")
            for m in result.get("models", []):
                self.step_model.addItem(m, m)
            if current and self.step_model.findData(current) < 0:
                self.step_model.addItem(current, current)
            idx = self.step_model.findData(current)
            self.step_model.setCurrentIndex(max(0, idx))
            self.step_model.blockSignals(False)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda _e: None)
        self._model_workers = getattr(self, "_model_workers", [])
        self._model_workers.append(w)   # keep a ref so the thread isn't GC'd
        w.start()

    def _reload_templates(self) -> None:
        self.tpl_combo.blockSignals(True)
        self.tpl_combo.clear()
        self.tpl_combo.addItem(tr("flow.select_template"), None)
        for flow in list_flows():
            self.tpl_combo.addItem(flow.name, flow)
        self.tpl_combo.blockSignals(False)

    def _load_selected_template(self, _idx: int) -> None:
        flow = self.tpl_combo.currentData()
        if isinstance(flow, Flow):
            self._loaded_name = flow.name
            self._bind_flow(copy.deepcopy(flow))

    def _load_builtin(self) -> None:
        self._loaded_name = ""
        self._bind_flow(default_req_to_demo())

    def _new_flow(self) -> None:
        self._loaded_name = ""
        self._bind_flow(Flow(name=tr("flow.new_flow_name"), description="", steps=[]))

    def _delete_template(self) -> None:
        flow = self.tpl_combo.currentData()
        if isinstance(flow, Flow):
            delete_flow(flow.name)
            self._reload_templates()

    # ---- flow <-> widgets -------------------------------------------
    def _bind_flow(self, flow: Flow) -> None:
        self._flow = flow
        self.name_edit.setText(flow.name)
        self.desc_edit.setText(flow.description)
        self._refresh_steps()
        if flow.steps:
            self.steps_list.setCurrentRow(0)
        else:
            self._clear_editor()

    def _refresh_steps(self) -> None:
        self.steps_list.blockSignals(True)
        self.steps_list.clear()
        for i, step in enumerate(self._flow.steps, 1):
            tags = []
            if step.skill:
                tags.append(f"[skill:{step.skill}]")
            if step.agent or step.model:
                label = PROVIDER_LABELS.get(step.agent, step.agent) if step.agent else ""
                combo = "·".join(x for x in (label, step.model) if x)
                tags.append(f"[agent:{combo}]")
            if step.is_parallel:
                tags.append(f"[parallel×{len(step.parallel_agents)}]")
            if step.attachments:
                tags.append(f"[files:{len(step.attachments)}]")
            if step.self_verify or step.review_retries:
                tags.append("[verify]")
            suffix = ("  " + " ".join(tags)) if tags else ""
            self.steps_list.addItem(QListWidgetItem(f"{i}. {step.name}{suffix}"))
        self.steps_list.blockSignals(False)

    def _clear_editor(self) -> None:
        self.step_name.clear()
        self.step_prompt.clear()
        self.step_hint.clear()
        self.step_skill.setCurrentIndex(0)
        self.step_agent.setCurrentIndex(0)
        self._pending_step_model = ""
        self.step_model.setCurrentIndex(0)
        self._step_attachments = []
        self._refresh_attach_btn()
        self.compact_chk.setChecked(False)
        self.verify_chk.setChecked(False)
        self.retries_spin.setValue(0)
        self._step_subagents = []
        self._refresh_subagents_list()

    def _load_step_into_editor(self, row: int) -> None:
        if not (0 <= row < len(self._flow.steps)):
            return
        step = self._flow.steps[row]
        self.step_name.setText(step.name)
        self.step_prompt.setPlainText(step.prompt)
        self.step_hint.setText(step.hint)
        self.step_skill.setCurrentIndex(max(0, self.step_skill.findData(step.skill)))
        self._pending_step_model = step.model   # survives the async model fetch
        self.step_agent.setCurrentIndex(max(0, self.step_agent.findData(step.agent)))
        if self.step_model.findData(step.model) < 0 and step.model:
            self.step_model.addItem(step.model, step.model)
        self.step_model.setCurrentIndex(max(0, self.step_model.findData(step.model)))
        self._step_attachments = list(step.attachments)
        self._refresh_attach_btn()
        self.compact_chk.setChecked(step.compact_after_run)
        self.verify_chk.setChecked(step.self_verify)
        self.retries_spin.setValue(step.review_retries)
        self._step_subagents = list(step.parallel_agents)
        self._refresh_subagents_list()

    def _editor_step(self) -> Optional[FlowStep]:
        name = self.step_name.text().strip()
        if not name:
            self.step_name.setFocus()
            return None
        return FlowStep(
            name=name,
            prompt=self.step_prompt.toPlainText().strip(),
            skill=self.step_skill.currentData() or "",
            agent=self.step_agent.currentData() or "",
            model=self.step_model.currentData() or "",
            hint=self.step_hint.text().strip(),
            attachments=list(self._step_attachments),
            compact_after_run=self.compact_chk.isChecked(),
            self_verify=self.verify_chk.isChecked(),
            review_retries=self.retries_spin.value(),
            parallel_agents=list(self._step_subagents),
        )

    def _add_step(self) -> None:
        step = self._editor_step()
        if step:
            self._flow.steps.append(step)
            self._refresh_steps()
            self.steps_list.setCurrentRow(len(self._flow.steps) - 1)

    def _update_step(self) -> None:
        row = self.steps_list.currentRow()
        step = self._editor_step()
        if step and 0 <= row < len(self._flow.steps):
            self._flow.steps[row] = step
            self._refresh_steps()
            self.steps_list.setCurrentRow(row)

    def _remove_step(self) -> None:
        row = self.steps_list.currentRow()
        if 0 <= row < len(self._flow.steps):
            self._flow.steps.pop(row)
            self._refresh_steps()

    def _move(self, delta: int) -> None:
        row = self.steps_list.currentRow()
        new = row + delta
        if 0 <= row < len(self._flow.steps) and 0 <= new < len(self._flow.steps):
            steps = self._flow.steps
            steps[row], steps[new] = steps[new], steps[row]
            self._refresh_steps()
            self.steps_list.setCurrentRow(new)

    # ---- result -----------------------------------------------------
    def _collect(self) -> Flow:
        self._flow.name = self.name_edit.text().strip() or tr("flow.default_name")
        self._flow.description = self.desc_edit.text().strip()
        return self._flow

    def _save_template(self) -> None:
        flow = self._collect()
        save_flow(flow, old_name=self._loaded_name)
        self._loaded_name = flow.name
        self._reload_templates()
        idx = self.tpl_combo.findText(flow.name)
        if idx >= 0:
            self.tpl_combo.setCurrentIndex(idx)

    def _run(self) -> None:
        flow = self._collect()
        if not flow.steps:
            return
        self.run_requested = True
        self.accept()

    def result_flow(self) -> Flow:
        return self._collect()
