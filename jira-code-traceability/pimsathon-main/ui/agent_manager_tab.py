"""Agent Manager tab: create/edit/delete reusable custom Agent presets.

A saved Agent here is just a (name, description, task prompt, provider)
preset. It shows up in the Flow Manager (flow_dialog.py) so any Flow stage
can add it as a parallel sub-agent in one click, instead of retyping the
same name/task by hand every time.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from ..config import PROVIDER_LABELS
from ..core.custom_agents import (
    CustomAgent, delete_agent, generate_agent_prompt, list_agents, save_agent,
)
from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon


class AgentManagerTab(QWidget):
    """A full tab (not a dialog) so custom Agents can be managed on their
    own, independent of any single Flow."""

    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        self.ctx = ctx               # for the AI "generate prompt from description" button
        self._gen_worker = None
        self._loaded_name = ""

        root = QVBoxLayout(self)
        hint = QLabel(tr("agentmgr.hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel(tr("agentmgr.list_label")))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load_into_editor)
        ll.addWidget(self.list, 1)
        del_btn = QPushButton(tr("agentmgr.delete_btn"))
        del_btn.setIcon(icon("trash"))
        del_btn.clicked.connect(self._delete)
        ll.addWidget(del_btn)
        split.addWidget(left)

        editor = QWidget()
        el = QFormLayout(editor)
        el.setRowWrapPolicy(QFormLayout.WrapLongRows)
        el.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.name_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setMinimumHeight(160)
        # "AI provider" + the "Agent" (model) within it (DeepSeek, qwen, … —
        # fetched live from the provider), side by side on one row.
        self.provider_combo = QComboBox()
        self.provider_combo.addItem(tr("flow.default_agent"), "")
        for key, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, key)
        self.model_combo = QComboBox()
        self.model_combo.addItem(tr("flow.default_model"), "")
        self.provider_combo.currentIndexChanged.connect(self._reload_models)
        provider_row = QWidget()
        prow = QHBoxLayout(provider_row)
        prow.setContentsMargins(0, 0, 0, 0)
        prow.addWidget(self.provider_combo, 1)
        prow.addWidget(QLabel(tr("flow.model_label")))
        prow.addWidget(self.model_combo, 1)
        el.addRow(tr("agentmgr.name_label"), self.name_edit)
        el.addRow(tr("agentmgr.desc_label"), self.desc_edit)

        # Prompt field with an AI "generate from description" button on top.
        prompt_box = QWidget()
        pb = QVBoxLayout(prompt_box)
        pb.setContentsMargins(0, 0, 0, 0)
        self._gen_prompt_btn = QPushButton(tr("agentmgr.gen_prompt_btn"))
        self._gen_prompt_btn.setIcon(icon("sparkle"))
        self._gen_prompt_btn.setToolTip(tr("agentmgr.gen_prompt_tooltip"))
        self._gen_prompt_btn.clicked.connect(self._gen_prompt)
        pb.addWidget(self._gen_prompt_btn)
        pb.addWidget(self.prompt_edit)
        el.addRow(tr("agentmgr.prompt_label"), prompt_box)
        el.addRow(tr("agentmgr.provider_label"), provider_row)

        # Scrollable so the form never compresses/overlaps on a small window.
        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QScrollArea.NoFrame)
        editor_scroll.setWidget(editor)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(editor_scroll, 1)
        btns = QHBoxLayout()
        new_btn = QPushButton(tr("agentmgr.new_btn"))
        new_btn.setIcon(icon("new"))
        new_btn.clicked.connect(self._new_agent)
        save_btn = QPushButton(tr("agentmgr.save_btn"))
        save_btn.setIcon(icon("save"))
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        btns.addWidget(new_btn)
        btns.addStretch(1)
        btns.addWidget(save_btn)
        rl.addLayout(btns)
        split.addWidget(right)
        split.setSizes([260, 520])
        root.addWidget(split, 1)

        self._reload_list()
        self._reload_models()

    # ---- Agent (model) list, fetched live from the selected provider -----
    def _reload_models(self) -> None:
        if self.ctx is None:
            return
        provider_key = self.provider_combo.currentData() or self.ctx.config.active_provider
        keep = getattr(self, "_pending_model", "") or (self.model_combo.currentData() or "")
        self.model_combo.clear()
        self.model_combo.addItem(tr("flow.default_model"), "")
        if keep:
            self.model_combo.addItem(keep, keep)
            self.model_combo.setCurrentIndex(1)
        ctx = self.ctx

        def job(worker: AgentWorker):
            try:
                return {"models": ctx.build_provider_for(provider_key).list_models() or [],
                        "provider": provider_key}
            except Exception:  # noqa: BLE001 — model list is best-effort
                return {"models": [], "provider": provider_key}

        def done(result: dict) -> None:
            if result.get("provider") != (self.provider_combo.currentData()
                                          or ctx.config.active_provider):
                return   # provider changed again while fetching
            current = self.model_combo.currentData() or ""
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            self.model_combo.addItem(tr("flow.default_model"), "")
            for m in result.get("models", []):
                self.model_combo.addItem(m, m)
            if current and self.model_combo.findData(current) < 0:
                self.model_combo.addItem(current, current)
            self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(current)))
            self.model_combo.blockSignals(False)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda _e: None)
        self._model_workers = getattr(self, "_model_workers", [])
        self._model_workers.append(w)   # keep a ref so the thread isn't GC'd
        w.start()

    # ---- AI: draft the prompt from the short name/description -----------
    def _gen_prompt(self) -> None:
        name = self.name_edit.text().strip()
        desc = self.desc_edit.text().strip()
        if not name and not desc:
            self.desc_edit.setFocus()
            return
        if self.ctx is None:
            return
        self._gen_prompt_btn.setEnabled(False)
        self._gen_prompt_btn.setText(tr("skills.generating"))
        ctx = self.ctx

        def job(worker: AgentWorker):
            return {"prompt": generate_agent_prompt(
                ctx.build_active_provider(), name, desc, worker.is_cancelled)}

        w = AgentWorker(job)
        w.finished_ok.connect(self._on_gen_prompt)
        w.failed.connect(lambda _e: self._reset_gen_prompt_btn())
        self._gen_worker = w
        w.start()

    def _on_gen_prompt(self, result) -> None:
        text = (result or {}).get("prompt", "")
        if text:
            self.prompt_edit.setPlainText(text)
        self._reset_gen_prompt_btn()

    def _reset_gen_prompt_btn(self) -> None:
        self._gen_prompt_btn.setEnabled(True)
        self._gen_prompt_btn.setText(tr("agentmgr.gen_prompt_btn"))

    # ---- list <-> editor ------------------------------------------------
    def _reload_list(self, select_name: str = "") -> None:
        self.list.blockSignals(True)
        self.list.clear()
        agents = list_agents()
        for a in agents:
            text = a.name + (f"  —  {a.description}" if a.description else "")
            self.list.addItem(QListWidgetItem(text))
        self.list.blockSignals(False)
        if select_name:
            for i, a in enumerate(agents):
                if a.name == select_name:
                    self.list.setCurrentRow(i)
                    return
        if agents:
            self.list.setCurrentRow(0)
        else:
            self._clear_editor()

    def _current_agent(self) -> Optional[CustomAgent]:
        row = self.list.currentRow()
        agents = list_agents()
        if 0 <= row < len(agents):
            return agents[row]
        return None

    def _load_into_editor(self, _row: int) -> None:
        agent = self._current_agent()
        if agent is None:
            self._clear_editor()
            return
        self._loaded_name = agent.name
        self.name_edit.setText(agent.name)
        self.desc_edit.setText(agent.description)
        self.prompt_edit.setPlainText(agent.prompt)
        self._pending_model = agent.model   # survives the async model fetch
        idx = self.provider_combo.findData(agent.provider)
        self.provider_combo.setCurrentIndex(max(0, idx))
        if agent.model and self.model_combo.findData(agent.model) < 0:
            self.model_combo.addItem(agent.model, agent.model)
        self.model_combo.setCurrentIndex(max(0, self.model_combo.findData(agent.model)))

    def _clear_editor(self) -> None:
        self._loaded_name = ""
        self.name_edit.clear()
        self.desc_edit.clear()
        self.prompt_edit.clear()
        self._pending_model = ""
        self.provider_combo.setCurrentIndex(0)
        self.model_combo.setCurrentIndex(0)

    def _new_agent(self) -> None:
        self.list.setCurrentRow(-1)
        self._clear_editor()
        self.name_edit.setFocus()

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setFocus()
            return
        agent = CustomAgent(
            name=name,
            description=self.desc_edit.text().strip(),
            prompt=self.prompt_edit.toPlainText().strip(),
            provider=self.provider_combo.currentData() or "",
            model=self.model_combo.currentData() or "",
        )
        save_agent(agent, old_name=self._loaded_name)
        self._loaded_name = agent.name
        self._reload_list(select_name=agent.name)

    def _delete(self) -> None:
        agent = self._current_agent()
        if agent is None:
            return
        if QMessageBox.question(
            self, tr("agentmgr.delete_btn"),
            tr("agentmgr.delete_confirm", name=agent.name),
        ) != QMessageBox.Yes:
            return
        delete_agent(agent.name)
        self._reload_list()
