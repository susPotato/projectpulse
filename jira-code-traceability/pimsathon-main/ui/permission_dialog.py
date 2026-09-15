"""Dialog shown in Confirm mode before a write/run action executes."""
from __future__ import annotations

from typing import Any, Dict, Tuple

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)

from ..i18n import tr


class PermissionDialog(QDialog):
    def __init__(self, action: Dict[str, Any], parent=None):
        super().__init__(parent)
        preview = action.get("preview", {})
        self.setWindowTitle(tr("permission.title"))
        self.setMinimumWidth(620)

        lay = QVBoxLayout(self)
        heading = QLabel(preview.get("title", action.get("name", tr("permission.default_action"))))
        heading.setStyleSheet("font-weight:600; font-size:14px;")
        lay.addWidget(heading)

        subtitle = {
            "command": tr("permission.subtitle_command"),
            "diff": tr("permission.subtitle_diff"),
        }.get(preview.get("kind", "info"), tr("permission.subtitle_default"))
        sub = QLabel(subtitle)
        sub.setObjectName("hint")
        lay.addWidget(sub)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(preview.get("text", ""))
        view.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        view.setMinimumHeight(260)
        lay.addWidget(view)

        buttons = QDialogButtonBox()
        self.approve_btn = buttons.addButton(tr("permission.approve"), QDialogButtonBox.AcceptRole)
        self.approve_btn.setObjectName("primary")
        self.reject_btn = buttons.addButton(tr("permission.reject"), QDialogButtonBox.RejectRole)
        self.reject_btn.setObjectName("danger")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    @staticmethod
    def ask(action: Dict[str, Any], parent=None) -> Tuple[bool, bool]:
        """Returns ``(approved, False)`` — remember is always False (whitelist removed)."""
        dlg = PermissionDialog(action, parent)
        approved = dlg.exec() == QDialog.Accepted
        return approved, False
