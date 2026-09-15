"""Skill Manager tab: manage custom skills, embedded inside Flow Management
(tab, next to Agents) — the same skills also apply to Cowork/Code chats.

A thin QWidget wrapper around the same CRUD logic as ``skills_dialog.py``'s
``SkillsDialog`` (kept there too, since Cowork/Code still open Skills as a
standalone dialog via the "Skills" button) — refactoring both to share one
implementation isn't worth the churn for a straightforward list+form CRUD.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..core.skills import (
    Skill, delete_skill, export_skill_md, generate_skill,
    generate_skill_from_template, list_skills, save_skill,
)
from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon
from .skills_dialog import SkillEditDialog


class SkillManagerTab(QWidget):
    def __init__(self, ctx=None, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._auto_worker: Optional[AgentWorker] = None
        self._template_worker: Optional[AgentWorker] = None

        lay = QVBoxLayout(self)
        hint = QLabel(tr("skills.hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
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
        row.addWidget(self._auto_btn)
        row.addWidget(self._template_btn)
        row.addWidget(import_btn)
        row.addWidget(export_btn)
        row.addWidget(dup_btn)
        row.addWidget(edit_btn)
        row.addWidget(del_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.reload()

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
        dlg = SkillEditDialog(self, skill, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill())
            self.reload()

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
            self.reload()

    def reload(self) -> None:
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
            self.reload()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, tr("skills.import_dialog_title"), tr("skills.import_failed", err=exc))

    def _export_md(self) -> None:
        skill = self._current_skill()
        if skill is None:
            QMessageBox.information(self, tr("skills.export_btn"), tr("skills.export_pick"))
            return
        from ..core.skills import Skill as _Skill
        default = f"{_Skill(name=skill.name).slug}.md"
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
        self.reload()
        dlg = SkillEditDialog(self, copy, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill(), old_name=copy.name)
            self.reload()

    def _edit(self) -> None:
        skill = self._current_skill()
        if skill is None:
            return
        dlg = SkillEditDialog(self, skill, ctx=self._ctx)
        if dlg.exec():
            save_skill(dlg.result_skill(), old_name=skill.name)
            self.reload()

    def _delete(self) -> None:
        skill = self._current_skill()
        if skill is None:
            return
        delete_skill(skill.name)
        self.reload()
