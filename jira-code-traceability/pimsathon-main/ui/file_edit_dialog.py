"""View a file inside the app and edit it with AI.

Opened from the Files pane's context menu (Input/Output lists in Cowork chat).
Plain-text files load into an editable viewer; binary documents (docx/pdf/…)
show their EXTRACTED text read-only (view + ask-about, but no save — writing
extracted text back would destroy the original format).

"AI Edit" sends the current content + the user's instruction to the active
provider and replaces the editor content with the model's full rewrite — the
user reviews it and clicks Save (a one-time ``.bak`` backup of the original is
written next to the file the first time it's saved from this dialog).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon

_MAX_TEXT_BYTES = 2_000_000          # bigger files: view the head, no editing
_FENCE_RE = re.compile(r"^\s*```[\w-]*\n(.*)\n```\s*$", re.DOTALL)
# Binary document formats: always view via text extraction, never edit as plain
# text (they'd be corrupted) — even a small one that happens to utf-8-decode.
_BINARY_DOC_SUFFIXES = {".pdf", ".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm",
                        ".ppt", ".pptx", ".odt", ".ods", ".odp"}

_SYSTEM_PROMPT = (
    "You edit files. You are given the FULL current content of a file and an "
    "instruction. Apply the instruction and reply with ONLY the complete new "
    "file content — no explanations, no markdown code fences, no preamble. "
    "Keep everything the instruction doesn't ask to change byte-identical."
)


def _strip_fences(text: str) -> str:
    """Models sometimes wrap the whole reply in one ``` fence despite the
    instructions — unwrap that single outer fence, leave anything else alone."""
    m = _FENCE_RE.match(text or "")
    return m.group(1) if m else (text or "")


class FileEditDialog(QDialog):
    """Pick/view a file and apply AI edits to it (see module docstring)."""

    def __init__(self, ctx=None, path: str = "", parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker: Optional[AgentWorker] = None
        self._editable = False
        self._backed_up = False
        self.setWindowTitle(tr("fileedit.title"))
        self.resize(760, 620)

        root = QVBoxLayout(self)

        # ---- file row ----------------------------------------------------
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.browse_btn = QPushButton()
        self.browse_btn.setIcon(icon("folder"))
        self.browse_btn.setToolTip(tr("fileedit.browse_tooltip"))
        self.browse_btn.setFixedWidth(34)
        self.browse_btn.clicked.connect(self._browse)
        self.reload_btn = QPushButton()
        self.reload_btn.setIcon(icon("refresh"))
        self.reload_btn.setToolTip(tr("fileedit.reload_tooltip"))
        self.reload_btn.setFixedWidth(34)
        self.reload_btn.clicked.connect(self._reload)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_btn)
        row.addWidget(self.reload_btn)
        root.addLayout(row)

        # ---- content -----------------------------------------------------
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(tr("fileedit.pick_hint"))
        root.addWidget(self.editor, 1)
        self.status_lbl = QLabel("")
        self.status_lbl.setObjectName("hint")
        self.status_lbl.setWordWrap(True)
        root.addWidget(self.status_lbl)

        # ---- AI edit row ---------------------------------------------------
        ai_row = QHBoxLayout()
        self.instruction_edit = QLineEdit()
        self.instruction_edit.setPlaceholderText(tr("fileedit.instruction_placeholder"))
        self.instruction_edit.returnPressed.connect(self._ai_edit)
        self.ai_btn = QPushButton(tr("fileedit.ai_btn"))
        self.ai_btn.setIcon(icon("sparkle"))
        self.ai_btn.clicked.connect(self._ai_edit)
        ai_row.addWidget(self.instruction_edit, 1)
        ai_row.addWidget(self.ai_btn)
        root.addLayout(ai_row)

        # ---- actions -------------------------------------------------------
        btn_row = QHBoxLayout()
        self.save_btn = QPushButton(tr("fileedit.save_btn"))
        self.save_btn.setIcon(icon("save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        self.close_btn = QPushButton(tr("fileedit.close_btn"))
        self.close_btn.setIcon(icon("close"))
        self.close_btn.clicked.connect(self.reject)
        btn_row.addStretch(1)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

        self._set_editable(False)
        if path:
            self.load_file(path)

    # ---- loading -----------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("fileedit.title"))
        if path:
            self.load_file(path)

    def _reload(self) -> None:
        if self.path_edit.text():
            self.load_file(self.path_edit.text())

    def load_file(self, path: str) -> None:
        """Load ``path``: text files editable; binary documents read-only via
        the same extractor attachments use (doc_extract)."""
        p = Path(path)
        self.path_edit.setText(str(p))
        self._backed_up = False
        if not p.is_file():
            self.editor.setPlainText("")
            self._set_editable(False)
            self.status_lbl.setText(tr("fileedit.not_found", path=str(p)))
            return
        raw = b""
        is_text = False
        if p.suffix.lower() not in _BINARY_DOC_SUFFIXES:
            try:
                raw = p.read_bytes()[:_MAX_TEXT_BYTES + 1]
                text = raw.decode("utf-8")
                is_text = "\x00" not in text
            except (UnicodeDecodeError, OSError):
                is_text = False
        if is_text and len(raw) <= _MAX_TEXT_BYTES:
            self.editor.setPlainText(text)
            self._set_editable(True)
            self.status_lbl.setText(tr("fileedit.loaded_editable"))
            return
        # Binary / oversized → extracted text, read-only.
        from ..core.doc_extract import extract_text

        try:
            extracted, note = extract_text(str(p))
        except Exception as exc:  # noqa: BLE001 — viewing must never crash
            extracted, note = None, str(exc)
        self.editor.setPlainText(extracted or "")
        self._set_editable(False)
        self.status_lbl.setText(tr("fileedit.loaded_readonly")
                                + (f" ({note})" if note else ""))

    def _set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.editor.setReadOnly(not editable)
        self.save_btn.setEnabled(editable)
        self.ai_btn.setEnabled(editable and self.ctx is not None)
        self.instruction_edit.setEnabled(editable and self.ctx is not None)

    # ---- AI edit -------------------------------------------------------------
    def _ai_edit(self) -> None:
        instruction = self.instruction_edit.text().strip()
        if (not instruction or not self._editable or self.ctx is None
                or self._worker is not None):
            if not instruction and self._editable:
                self.status_lbl.setText(tr("fileedit.needs_instruction"))
            return
        content = self.editor.toPlainText()
        name = Path(self.path_edit.text()).name
        self.ai_btn.setEnabled(False)
        self.status_lbl.setText(tr("fileedit.ai_working"))
        ctx = self.ctx

        def job(worker: AgentWorker):
            provider = ctx.build_active_provider()
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content":
                    f"File: {name}\nInstruction: {instruction}\n\n"
                    f"--- CURRENT FILE CONTENT ---\n{content}"},
            ]
            a = provider.chat(messages, tools=None, on_text=None, cancel=worker.is_cancelled)
            return {"text": (a.get("content") or "").strip()}

        def done(result: dict) -> None:
            self._worker = None
            self.ai_btn.setEnabled(True)
            new_text = _strip_fences(result.get("text", ""))
            if new_text:
                self.editor.setPlainText(new_text)
                self.status_lbl.setText(tr("fileedit.ai_done"))
            else:
                self.status_lbl.setText(tr("fileedit.ai_empty"))

        def failed(err: str) -> None:
            self._worker = None
            self.ai_btn.setEnabled(True)
            self.status_lbl.setText(tr("fileedit.ai_failed", err=err[:300]))

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._worker = w
        w.start()

    # ---- save -----------------------------------------------------------------
    def _save(self) -> None:
        path = self.path_edit.text()
        if not path or not self._editable:
            return
        p = Path(path)
        try:
            # One-time backup of the on-disk original per dialog session.
            if not self._backed_up and p.is_file():
                bak = p.with_suffix(p.suffix + ".bak")
                bak.write_bytes(p.read_bytes())
                self._backed_up = True
            p.write_text(self.editor.toPlainText(), encoding="utf-8")
            self.status_lbl.setText(tr("fileedit.saved", path=p.name))
        except OSError as exc:
            QMessageBox.warning(self, tr("fileedit.title"), str(exc))
