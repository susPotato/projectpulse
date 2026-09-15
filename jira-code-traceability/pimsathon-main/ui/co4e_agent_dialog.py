"""Co4E custom-agent editor — create/edit a persisted persona.

Mirrors nova's agent-config-panel field set: name, role, icon, instructions,
model, permission preset, and an attached-skills checklist.
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..core.co4e import PERMISSION_PRESETS, CustomAgent
from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon, icon_picker_combo


class Co4EAgentDialog(QDialog):
    def __init__(self, ctx, agent: CustomAgent, skill_names: List[str], parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._agent = agent
        self.setWindowTitle(tr("co4e.agent_edit_title") if agent.name else tr("co4e.agent_new_title"))
        self.resize(460, 480)
        form = QFormLayout(self)

        self.name_edit = QLineEdit(agent.name)
        form.addRow(tr("co4e.f_name"), self.name_edit)
        self.role_edit = QLineEdit(agent.role or "AGENT")
        form.addRow(tr("co4e.f_role"), self.role_edit)
        # Dropdown of every icon in the registry (Monitoring's Icon Management
        # set + built-ins), each row previewing its actual glyph — still
        # editable so a not-yet-added custom name can be typed directly.
        self.icon_edit = icon_picker_combo(agent.icon)
        self.icon_edit.lineEdit().setPlaceholderText(tr("co4e.f_icon_placeholder"))
        form.addRow(tr("co4e.f_icon"), self.icon_edit)
        self.instructions_edit = QPlainTextEdit(agent.instructions)
        self.instructions_edit.setMaximumHeight(150)
        # ✨ AI-assist — draft the agent's instructions from its name + role
        # (handy when you're not attaching a skill).
        self.gen_btn = QPushButton(tr("co4e.ai_draft"))
        self.gen_btn.setIcon(icon("sparkle"))
        self.gen_btn.setToolTip(tr("co4e.ai_draft_tooltip"))
        self.gen_btn.setEnabled(ctx is not None)
        self.gen_btn.clicked.connect(self._ai_draft)
        instr_box = QWidget()
        ib = QVBoxLayout(instr_box)
        ib.setContentsMargins(0, 0, 0, 0)
        ib.addWidget(self.instructions_edit)
        ib.addWidget(self.gen_btn, alignment=Qt.AlignRight)
        form.addRow(tr("co4e.f_instructions"), instr_box)

        # Extra context — free-text background/info fed to the agent at run time.
        self.context_edit = QPlainTextEdit(getattr(agent, "context", ""))
        self.context_edit.setMaximumHeight(90)
        self.context_edit.setPlaceholderText(tr("co4e.f_context_placeholder"))
        form.addRow(tr("co4e.f_context"), self.context_edit)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setEditText(agent.model)
        self.load_btn = QPushButton()
        self.load_btn.setIcon(icon("download"))
        self.load_btn.setToolTip(tr("co4e.load_models_tooltip"))
        self.load_btn.clicked.connect(self._load_models)
        self.load_btn.setEnabled(ctx is not None)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.load_btn)
        mrow = QWidget(); mrow.setLayout(model_row)
        form.addRow(tr("co4e.f_model"), mrow)

        self.perm_combo = QComboBox()
        for preset in PERMISSION_PRESETS:
            self.perm_combo.addItem(tr(f"co4e.perm.{preset}"), preset)
        idx = self.perm_combo.findData(agent.permission_preset)
        self.perm_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow(tr("co4e.f_permission"), self.perm_combo)

        self.skills_list = QListWidget()
        self.skills_list.setMaximumHeight(120)
        for name in skill_names:
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if name in agent.skills else Qt.Unchecked)
            self.skills_list.addItem(it)
        form.addRow(tr("co4e.f_skills"), self.skills_list)

        # Attachments — files whose extracted text is fed to the agent at run time.
        self.attach_list = QListWidget()
        self.attach_list.setMaximumHeight(70)
        self._attachments: List[str] = list(getattr(agent, "attachments", []) or [])
        self._refresh_attach_list()
        attach_add = QPushButton(tr("co4e.attach_add"))
        attach_add.setIcon(icon("plus"))
        attach_add.clicked.connect(self._add_attachment)
        attach_del = QPushButton(tr("co4e.attach_remove"))
        attach_del.setIcon(icon("trash"))
        attach_del.clicked.connect(self._del_attachment)
        ab = QHBoxLayout()
        ab.addWidget(attach_add)
        ab.addWidget(attach_del)
        ab.addStretch(1)
        abtn = QWidget(); abtn.setLayout(ab)
        form.addRow(tr("co4e.f_attachments"), self.attach_list)
        form.addRow("", abtn)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _ai_draft(self) -> None:
        """Draft the instructions from the agent's name + role — first asking
        for an optional description so the generated instructions can be more
        specific/detailed than name+role alone would produce."""
        if self.ctx is None:
            return
        name = self.name_edit.text().strip()
        role = self.role_edit.text().strip()
        if not name and not role:
            return
        hint, ok = QInputDialog.getMultiLineText(
            self, tr("co4e.ai_draft_hint_title"), tr("co4e.ai_draft_hint_label"))
        if not ok:
            return
        hint = hint.strip()
        self.gen_btn.setEnabled(False)
        ctx = self.ctx

        def job(worker: AgentWorker):
            from ..core.ai_task_planner import generate_agent_prompt
            return {"text": generate_agent_prompt(ctx.build_active_provider(), name, role, hint,
                                                  cancel=worker.is_cancelled)}

        def done(result: dict):
            self.gen_btn.setEnabled(True)
            if result.get("text"):
                self.instructions_edit.setPlainText(result["text"])

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda _e: self.gen_btn.setEnabled(True))
        self._gen_worker = w
        w.start()

    def _refresh_attach_list(self) -> None:
        from pathlib import Path as _P
        self.attach_list.clear()
        for p in self._attachments:
            item = QListWidgetItem(_P(p).name)
            item.setToolTip(p)
            self.attach_list.addItem(item)

    def _add_attachment(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(self, tr("co4e.attach_add"))
        for f in files:
            if f and f not in self._attachments:
                self._attachments.append(f)
        self._refresh_attach_list()

    def _del_attachment(self) -> None:
        row = self.attach_list.currentRow()
        if 0 <= row < len(self._attachments):
            self._attachments.pop(row)
            self._refresh_attach_list()

    def result_agent(self) -> CustomAgent:
        a = self._agent
        a.name = self.name_edit.text().strip() or "Agent"
        a.role = (self.role_edit.text().strip() or "AGENT").upper()
        a.icon = self.icon_edit.currentText().strip()
        a.instructions = self.instructions_edit.toPlainText().strip()
        a.context = self.context_edit.toPlainText().strip()
        a.model = self.model_combo.currentText().strip()
        a.permission_preset = self.perm_combo.currentData() or "inherit"
        a.skills = [self.skills_list.item(i).text()
                    for i in range(self.skills_list.count())
                    if self.skills_list.item(i).checkState() == Qt.Checked]
        a.attachments = list(self._attachments)
        return a

    def _load_models(self) -> None:
        if self.ctx is None:
            return
        from ..core import preview_ai
        from ..core.worker import AgentWorker

        self.load_btn.setEnabled(False)
        ctx = self.ctx

        def job(_w):
            return preview_ai.fetch_live_models(ctx)

        def done(result: dict):
            self.load_btn.setEnabled(True)
            models = []
            for lst in (result or {}).values():
                models.extend(lst)
            cur = self.model_combo.currentText()
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItems(sorted(set(models)))
            self.model_combo.setEditText(cur)
            self.model_combo.blockSignals(False)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda _e: self.load_btn.setEnabled(True))
        self._worker = w
        w.start()
