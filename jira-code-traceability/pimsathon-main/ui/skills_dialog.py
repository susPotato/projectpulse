"""Manage custom agent skills: add, edit, delete, enable/disable."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from ..core.skills import (
    Skill, delete_skill, export_skill_md, generate_skill,
    generate_skill_from_template, generate_skill_instructions, list_skills,
    save_skill,
)
from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon


class SkillEditDialog(QDialog):
    def __init__(self, parent=None, skill: Optional[Skill] = None, ctx=None):
        super().__init__(parent)
        self.setWindowTitle(tr("skills.edit_title") if skill else tr("skills.add_title"))
        self.setMinimumWidth(520)
        self._enabled = skill.enabled if skill else False  # new skills start disabled
        self._ctx = ctx               # for the AI "generate from description" button
        self._gen_worker = None

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(tr("skills.name_label")))
        self.name = QLineEdit(skill.name if skill else "")
        self.name.setPlaceholderText(tr("skills.name_placeholder"))
        lay.addWidget(self.name)

        lay.addWidget(QLabel(tr("skills.desc_label")))
        self.desc = QLineEdit(skill.description if skill else "")
        lay.addWidget(self.desc)

        instr_hdr = QHBoxLayout()
        instr_hdr.addWidget(QLabel(tr("skills.instructions_label")), 1)
        self._gen_btn = QPushButton(tr("skills.gen_from_desc"))
        self._gen_btn.setIcon(icon("sparkle"))
        self._gen_btn.setToolTip(tr("skills.gen_from_desc_tooltip"))
        self._gen_btn.clicked.connect(self._gen_instructions)
        instr_hdr.addWidget(self._gen_btn)
        lay.addLayout(instr_hdr)
        self.instr = QPlainTextEdit(skill.instructions if skill else "")
        self.instr.setPlaceholderText(tr("skills.instructions_placeholder"))
        self.instr.setMinimumHeight(180)
        lay.addWidget(self.instr)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    # ---- AI: draft the instructions from the short description -------
    def _gen_instructions(self) -> None:
        desc = self.desc.text().strip()
        name = self.name.text().strip()
        if not desc and not name:
            self.desc.setFocus()
            return
        if self._ctx is None:
            return
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("skills.generating"))
        ctx = self._ctx

        def job(worker: AgentWorker):
            return {"text": generate_skill_instructions(
                ctx.build_active_provider(), desc, name, worker.is_cancelled)}

        w = AgentWorker(job)
        w.finished_ok.connect(self._on_gen)
        w.failed.connect(lambda _e: self._reset_gen_btn())
        self._gen_worker = w
        w.start()

    def _on_gen(self, result) -> None:
        text = (result or {}).get("text", "")
        if text:
            self.instr.setPlainText(text)
        self._reset_gen_btn()

    def _reset_gen_btn(self) -> None:
        self._gen_btn.setEnabled(True)
        self._gen_btn.setText(tr("skills.gen_from_desc"))

    def _on_accept(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def result_skill(self) -> Skill:
        return Skill(
            name=self.name.text().strip(),
            description=self.desc.text().strip(),
            instructions=self.instr.toPlainText().strip(),
            enabled=self._enabled,
        )


class SkillsDialog(QDialog):
    def __init__(self, parent=None, ctx=None):
        super().__init__(parent)
        self._ctx = ctx   # passed to SkillEditDialog for AI-assisted generation
        self._auto_worker = None
        self._template_worker = None
        self.setWindowTitle(tr("skills.title"))
        self.setMinimumSize(560, 420)

        lay = QVBoxLayout(self)
        hint = QLabel(tr("skills.hint"))
        hint.setObjectName("hint")
        lay.addWidget(hint)

        self.list = QListWidget()
        self.list.itemChanged.connect(self._on_check)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        self._auto_btn = QPushButton(tr("skills.auto_generate"))
        self._auto_btn.setIcon(icon("sparkle"))
        self._auto_btn.setObjectName("primary")
        self._auto_btn.setToolTip(tr("skills.auto_generate_tooltip"))
        self._auto_btn.clicked.connect(self._auto_generate)
        self._template_btn = QPushButton(tr("skills.from_template"))
        self._template_btn.setIcon(icon("document"))
        self._template_btn.setToolTip(tr("skills.from_template_tooltip"))
        self._template_btn.clicked.connect(self._from_template)
        import_btn = QPushButton(tr("skills.import_btn"))
        import_btn.setIcon(icon("download"))
        import_btn.setToolTip(tr("skills.import_tooltip"))
        import_btn.clicked.connect(self._import)
        export_btn = QPushButton(tr("skills.export_btn"))
        export_btn.setIcon(icon("upload"))
        export_btn.setToolTip(tr("skills.export_tooltip"))
        export_btn.clicked.connect(self._export_md)
        dup_btn = QPushButton(tr("skills.duplicate_btn"))
        dup_btn.setIcon(icon("document"))
        dup_btn.setToolTip(tr("skills.duplicate_tooltip"))
        dup_btn.clicked.connect(self._duplicate)
        edit_btn = QPushButton(tr("skills.edit_btn"))
        edit_btn.setIcon(icon("edit"))
        edit_btn.clicked.connect(self._edit)
        del_btn = QPushButton(tr("skills.delete_btn"))
        del_btn.setIcon(icon("trash"))
        del_btn.clicked.connect(self._delete)
        close_btn = QPushButton(tr("skills.close_btn"))
        close_btn.setIcon(icon("close"))
        close_btn.clicked.connect(self.accept)
        row.addWidget(self._auto_btn)
        row.addWidget(self._template_btn)
        row.addWidget(import_btn)
        row.addWidget(export_btn)
        row.addWidget(dup_btn)
        row.addWidget(edit_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        lay.addLayout(row)

        self._reload()

    # ---- AI: auto-generate a whole skill from a one-line description ----
    def _auto_generate(self) -> None:
        if self._ctx is None:
            QMessageBox.information(self, tr("skills.auto_generate_title"),
                                    tr("skills.auto_generate_unavailable"))
            return
        prompt, ok = QInputDialog.getMultiLineText(
            self, tr("skills.auto_generate_title"),
            tr("skills.auto_generate_prompt"), "")
        if not ok or not prompt.strip():
            return
        self._auto_btn.setEnabled(False)
        self._auto_btn.setText(tr("skills.generating"))
        ctx = self._ctx
        text = prompt.strip()

        def job(worker: AgentWorker):
            try:
                skill = generate_skill(ctx.build_active_provider(), text, worker.is_cancelled)
                return {"skill": skill}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

        w = AgentWorker(job)
        w.finished_ok.connect(self._on_auto_generated)
        w.failed.connect(lambda _e: self._on_auto_generated({"error": "failed"}))
        self._auto_worker = w
        w.start()

    def _on_auto_generated(self, result) -> None:
        self._auto_btn.setEnabled(True)
        self._auto_btn.setText(tr("skills.auto_generate"))
        skill = (result or {}).get("skill")
        if not isinstance(skill, Skill):
            QMessageBox.information(
                self, tr("skills.auto_generate_title"), tr("skills.auto_generate_failed"))
            return
        # Open the editor pre-filled so the user can review/tweak before saving.
        dlg = SkillEditDialog(self, skill, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill())
            self._reload()

    # ---- AI: analyze a pptx/xlsx TEMPLATE file's structure into a skill ----
    def _from_template(self) -> None:
        if self._ctx is None:
            QMessageBox.information(self, tr("skills.from_template_title"),
                                    tr("skills.auto_generate_unavailable"))
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("skills.from_template_dialog_title"), "",
            tr("skills.from_template_dialog_filter"))
        if not path:
            return
        self._template_btn.setEnabled(False)
        self._template_btn.setText(tr("skills.generating"))
        ctx = self._ctx

        def job(worker: AgentWorker):
            try:
                skill = generate_skill_from_template(
                    ctx.build_active_provider(), path, worker.is_cancelled)
                return {"skill": skill}
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

        w = AgentWorker(job)
        w.finished_ok.connect(self._on_template_generated)
        w.failed.connect(lambda _e: self._on_template_generated({"error": "failed"}))
        self._template_worker = w
        w.start()

    def _on_template_generated(self, result) -> None:
        self._template_btn.setEnabled(True)
        self._template_btn.setText(tr("skills.from_template"))
        skill = (result or {}).get("skill")
        if not isinstance(skill, Skill):
            QMessageBox.information(
                self, tr("skills.from_template_title"), tr("skills.from_template_failed"))
            return
        dlg = SkillEditDialog(self, skill, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill())
            self._reload()

    def _reload(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for s in list_skills():
            text = s.name + (f"  —  {s.description}" if s.description else "")
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if s.enabled else Qt.Unchecked)
            item.setData(Qt.UserRole, s)
            self.list.addItem(item)
        if self.list.count() == 0:
            placeholder = QListWidgetItem(tr("skills.no_skills"))
            placeholder.setFlags(Qt.NoItemFlags)
            self.list.addItem(placeholder)
        self.list.blockSignals(False)

    def _on_check(self, item: QListWidgetItem) -> None:
        skill = item.data(Qt.UserRole)
        if not isinstance(skill, Skill):
            return
        skill.enabled = item.checkState() == Qt.Checked
        save_skill(skill)

    def _current_skill(self) -> Optional[Skill]:
        item = self.list.currentItem()
        if item is None:
            return None
        skill = item.data(Qt.UserRole)
        return skill if isinstance(skill, Skill) else None

    def _import(self) -> None:
        from ..core.skills import import_skill_file

        path, _ = QFileDialog.getOpenFileName(
            self, tr("skills.import_dialog_title"), "", tr("skills.import_dialog_filter"))
        if not path:
            return
        try:
            import_skill_file(path)
            self._reload()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("skills.import_dialog_title"), tr("skills.import_failed", err=exc))

    def _export_md(self) -> None:
        skill = self._current_skill()
        if skill is None:
            QMessageBox.information(self, tr("skills.export_btn"), tr("skills.export_pick"))
            return
        default = f"{Skill(name=skill.name).slug}.md"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("skills.export_dialog_title"), default, tr("skills.export_dialog_filter"))
        if not path:
            return
        try:
            out = export_skill_md(skill, path)
            QMessageBox.information(self, tr("skills.export_btn"),
                                    tr("skills.export_done", path=str(out)))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("skills.export_btn"), tr("skills.export_failed", err=exc))

    def _duplicate(self) -> None:
        skill = self._current_skill()
        if skill is None:
            QMessageBox.information(self, tr("skills.duplicate_btn"), tr("skills.export_pick"))
            return
        copy = Skill(name=tr("skills.copy_name", name=skill.name),
                     description=skill.description, instructions=skill.instructions, enabled=False)
        save_skill(copy)
        self._reload()
        # Open the copy for immediate editing/renaming.
        dlg = SkillEditDialog(self, copy, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill(), old_name=copy.name)
            self._reload()

    def _edit(self) -> None:
        skill = self._current_skill()
        if skill is None:
            return
        dlg = SkillEditDialog(self, skill, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill(), old_name=skill.name)
            self._reload()

    def _delete(self) -> None:
        skill = self._current_skill()
        if skill is None:
            return
        delete_skill(skill.name)
        self._reload()
