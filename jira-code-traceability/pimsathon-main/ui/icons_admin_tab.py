"""Icons — a Monitoring sub-tab to browse the built-in icon set and add custom
icons for agents / flows.

Shows the built-in glyphs (the names usable in a Co4E step/agent ``icon`` field)
and the user's own imported SVG icons, with Add / Delete. Custom icons are saved
via ``core/custom_icons.py`` and become usable by name immediately.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..core import custom_icons
from ..i18n import on_language_changed, tr
from ..state import AppContext
from . import icons as icons_mod
from .icons import icon


def _grid() -> QListWidget:
    g = QListWidget()
    g.setViewMode(QListWidget.IconMode)
    g.setResizeMode(QListWidget.Adjust)
    g.setMovement(QListWidget.Static)
    g.setIconSize(QSize(28, 28))
    g.setGridSize(QSize(96, 74))
    g.setSpacing(4)
    return g


class IconsAdminTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)
        self._hint = QLabel(); self._hint.setObjectName("hint"); self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        # search over built-in names
        self.search = QLineEdit()
        self.search.textChanged.connect(self._reload_builtin)
        root.addWidget(self.search)

        self._builtin_lbl = QLabel()
        root.addWidget(self._builtin_lbl)
        self.builtin_grid = _grid()
        root.addWidget(self.builtin_grid, 2)

        self._custom_lbl = QLabel()
        root.addWidget(self._custom_lbl)
        self.custom_grid = _grid()
        root.addWidget(self.custom_grid, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton(); self.add_btn.setIcon(icon("plus"))
        self.add_btn.clicked.connect(self._add_icon)
        self.paste_btn = QPushButton()
        self.paste_btn.clicked.connect(self._add_from_svg_text)
        self.del_btn = QPushButton(); self.del_btn.setIcon(icon("trash"))
        self.del_btn.clicked.connect(self._delete_icon)
        btns.addWidget(self.add_btn); btns.addWidget(self.paste_btn)
        btns.addWidget(self.del_btn); btns.addStretch(1)
        root.addLayout(btns)

        on_language_changed(self._retranslate)
        self._retranslate()

    # ---- rendering --------------------------------------------------------
    def _reload_builtin(self, *_a) -> None:
        q = self.search.text().strip().lower()
        self.builtin_grid.clear()
        for name in sorted(icons_mod._PATHS):
            if q and q not in name:
                continue
            it = QListWidgetItem(icon(name), name)
            it.setToolTip(name)
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            self.builtin_grid.addItem(it)

    def _reload_custom(self) -> None:
        self.custom_grid.clear()
        for name in custom_icons.list_custom():
            it = QListWidgetItem(icon(name), name)
            it.setToolTip(name)
            it.setData(Qt.UserRole, name)
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            self.custom_grid.addItem(it)

    # ---- actions ----------------------------------------------------------
    def _add_icon(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, tr("icons_admin.add"), "", "SVG (*.svg)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, tr("icons_admin.name_prompt"),
                                        tr("icons_admin.name_prompt"))
        if not ok:
            return
        try:
            custom_icons.add_from_file(path, name.strip())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("icons_admin.title"), str(exc))
            return
        self._reload_custom()

    def _add_from_svg_text(self) -> None:
        name, ok = QInputDialog.getText(self, tr("icons_admin.name_prompt"),
                                        tr("icons_admin.name_prompt"))
        if not ok or not name.strip():
            return
        svg, ok = QInputDialog.getMultiLineText(self, tr("icons_admin.paste"),
                                                tr("icons_admin.paste_prompt"))
        if not ok:
            return
        try:
            custom_icons.add_svg(name.strip(), svg)
        except ValueError as exc:
            QMessageBox.warning(self, tr("icons_admin.title"), str(exc))
            return
        self._reload_custom()

    def _delete_icon(self) -> None:
        item = self.custom_grid.currentItem()
        if item is None:
            QMessageBox.information(self, tr("icons_admin.title"), tr("icons_admin.select_custom"))
            return
        custom_icons.delete_custom(item.data(Qt.UserRole))
        self._reload_custom()

    def _retranslate(self) -> None:
        self._hint.setText(tr("icons_admin.hint"))
        self.search.setPlaceholderText(tr("icons_admin.search"))
        self._builtin_lbl.setText(tr("icons_admin.builtin"))
        self._custom_lbl.setText(tr("icons_admin.custom"))
        self.add_btn.setText(tr("icons_admin.add"))
        self.paste_btn.setText(tr("icons_admin.paste"))
        self.del_btn.setText(tr("icons_admin.delete"))
        self._reload_builtin()
        self._reload_custom()
