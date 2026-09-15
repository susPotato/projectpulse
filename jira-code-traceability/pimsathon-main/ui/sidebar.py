"""Left sidebar listing previous Cowork conversations."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.history import (
    delete_conversation, list_conversations, load_conversation,
    rename_conversation, set_pinned,
)
from ..i18n import on_language_changed, tr
from .icons import collapse_left_icon, dot_icon, DOT_BLUE, icon
from .widgets import CollapseStrip
from ..state import AppContext



class _SessionDelegate(QStyledItemDelegate):
    """Draw each conversation as a rounded, bordered card so sessions read as
    separate blocks. Group headers (rows with no stored path) keep the default
    look."""

    def paint(self, painter, option, index):  # noqa: N802
        if not index.data(Qt.UserRole):       # group header → default
            super().paint(painter, option, index)
            return
        is_current = bool(index.data(Qt.UserRole + 4))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(3, 3, -5, -3)
        if is_current:
            # The conversation currently on screen — a strong, persistent highlight
            # (deep ocean blue border + filled bg) regardless of Qt's transient selection.
            bg, border = QColor(0, 150, 199, 70), QColor(0, 150, 199, 220)
        elif option.state & QStyle.State_Selected:
            bg, border = QColor(0, 150, 199, 48), QColor(0, 150, 199, 170)
        elif option.state & QStyle.State_MouseOver:
            bg, border = QColor(140, 146, 152, 40), QColor(140, 146, 152, 120)
        else:
            bg, border = QColor(140, 146, 152, 22), QColor(140, 146, 152, 85)
        painter.setBrush(bg)
        painter.setPen(QPen(border, 2 if is_current else 1))
        painter.drawRoundedRect(rect, 10, 10)
        painter.restore()

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.rect = rect.adjusted(8, 0, -6, 0)
        opt.state &= ~QStyle.State_Selected
        opt.state &= ~QStyle.State_MouseOver
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

    def sizeHint(self, option, index):  # noqa: N802
        size = super().sizeHint(option, index)
        if index.data(Qt.UserRole):
            size.setHeight(size.height() + 16)
        return size


class HistorySidebar(QWidget):
    new_chat = Signal(str)          # kind
    open_chat = Signal(str, dict)   # kind, conversation
    collapse_requested = Signal()   # in-header button: collapse to a strip
    expand_requested = Signal()     # strip clicked: re-expand
    refresh_requested = Signal()    # Refresh button: re-list + re-sync agent status
    history_changed = Signal()      # a conversation was deleted — other views (Project tab) should re-sync

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.current_session_id = ""     # conversation currently on screen (highlighted)
        self.running_ids: set = set()    # conversations with a turn running (status dot)
        # When History is embedded inside a project's Cowork sub-tab, it shows
        # ONLY that project's threads. "" = show every project (grouped).
        self._project_filter = ""
        self.setMinimumWidth(220)
        self.setMaximumWidth(360)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Thin clickable line shown when the panel is collapsed (hidden otherwise).
        self._strip = CollapseStrip(tr("sidebar.expand_tooltip"))
        self._strip.clicked.connect(self.expand_requested.emit)
        self._strip.setVisible(False)
        root.addWidget(self._strip)

        # Full panel content (hidden when collapsed). The single collapse button
        # sits in the header, just left of the title.
        self._content = QWidget()
        root.addWidget(self._content, 1)
        c = QVBoxLayout(self._content)
        c.setContentsMargins(8, 10, 8, 10)

        header_row = QHBoxLayout()
        self._collapse_btn = QPushButton()
        self._collapse_btn.setIcon(collapse_left_icon())
        self._collapse_btn.setFixedWidth(28)
        self._collapse_btn.clicked.connect(self.collapse_requested.emit)
        self._header = QLabel()
        self._header.setStyleSheet("font-weight:700; font-size:14px;")
        header_row.addWidget(self._collapse_btn)
        header_row.addWidget(self._header, 1)
        c.addLayout(header_row)

        # Search by title or message content — sits right under the header, above
        # the kind filter (see refresh(), which passes the text to
        # core.history.list_conversations's query filter).
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        self.search_box = QLineEdit()
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self.refresh)
        self.search_box.returnPressed.connect(self.refresh)
        self.search_btn = QPushButton("")
        self.search_btn.setIcon(icon("search"))
        self.search_btn.setFixedWidth(32)
        self.search_btn.clicked.connect(self.refresh)
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(self.search_btn)
        c.addLayout(search_row)

        # Filter removed — no kind filtering in History sidebar
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setItemDelegate(_SessionDelegate(self.tree))
        self.tree.setMouseTracking(True)          # hover highlight on the cards
        self.tree.setIndentation(10)
        self.tree.setRootIsDecorated(False)        # cleaner: no branch triangles
        # Shift/Ctrl-click a range or individual items to multi-select, then
        # right-click → "Delete N selected" to bulk-remove conversations.
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemActivated.connect(self._on_item)
        self.tree.itemClicked.connect(self._on_item)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        c.addWidget(self.tree, 1)

        self._refresh_btn = QPushButton()
        self._refresh_btn.setIcon(icon("refresh"))
        # Emit a request so the app can ALSO re-sync the chat-box agent status,
        # not just re-list conversations.
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        c.addWidget(self._refresh_btn)

        self.refresh()
        on_language_changed(self._retranslate)

    def _retranslate(self) -> None:
        self._strip.setToolTip(tr("sidebar.expand_tooltip"))
        self._collapse_btn.setToolTip(tr("sidebar.collapse_tooltip"))
        self._header.setText(tr("sidebar.header"))
        self.search_box.setPlaceholderText(tr("sidebar.search_placeholder"))
        self.search_btn.setToolTip(tr("sidebar.search_tooltip"))
        self._refresh_btn.setText(tr("sidebar.refresh"))
        self._refresh_btn.setToolTip(tr("sidebar.refresh_tooltip"))
        self.refresh()   # re-render group headers / running suffix in the new language

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse to a thin line (kept visible) or restore the full panel."""
        self._content.setVisible(not collapsed)
        self._strip.setVisible(collapsed)
        if collapsed:
            self.setMinimumWidth(CollapseStrip.WIDTH)
            self.setMaximumWidth(CollapseStrip.WIDTH)
        else:
            self.setMinimumWidth(220)
            self.setMaximumWidth(360)

    def set_project_filter(self, project_id: str) -> None:
        """Restrict History to a single project's threads (used when the sidebar
        is embedded in that project's Cowork sub-tab). '' shows every project."""
        pid = project_id or ""
        if pid == self._project_filter:
            return
        self._project_filter = pid
        self.refresh()

    def set_view_state(self, current_session_id: str, running_ids) -> None:
        """Tell the sidebar which conversation is on screen (to highlight) and which
        ones have a turn running (to mark). Call before refresh()."""
        self.current_session_id = current_session_id or ""
        self.running_ids = set(running_ids or ())

    def refresh(self) -> None:
        """Group conversations by PROJECT (Claude-Projects style): one bold
        section per project, its threads beneath."""
        self.tree.clear()
        query = self.search_box.text() if hasattr(self, "search_box") else ""

        def _make_group(label: str) -> QTreeWidgetItem:
            node = QTreeWidgetItem([label])
            node.setIcon(0, icon("folder"))
            node.setFirstColumnSpanned(True)
            font = node.font(0)
            font.setBold(True)
            node.setFont(0, font)
            # Not selectable — a Shift-click range must skip section headers,
            # never sweep them into a "delete N selected" bulk action.
            node.setFlags(node.flags() & ~Qt.ItemIsSelectable)
            self.tree.addTopLevelItem(node)
            node.setExpanded(True)
            return node

        groups = {}
        try:
            from ..core.projects import list_projects
            projects = list_projects()
        except Exception:
            projects = []
        if self._project_filter:
            projects = [p for p in projects if p.project_id == self._project_filter]
        for p in projects:
            groups[p.project_id] = _make_group(p.name)

        try:
            convos = list_conversations(self.ctx.config.history_dir(), query=query)
        except Exception:
            convos = []

        for meta in convos:
            kind = meta.get("kind", "") or "cowork"
            pid = meta.get("project_id", "") or "default"
            if self._project_filter and pid != self._project_filter:
                continue   # embedded in one project → hide other projects' threads
            parent = groups.get(pid)
            if parent is None and not self._project_filter:
                parent = groups.get("default")
            if parent is None:   # no matching group — flat fallback bucket
                parent = groups.setdefault(pid, _make_group("…"))
            created = (meta.get("created", "") or "").replace("T", " ")[:16]
            title = meta.get("title") or tr("sidebar.empty")
            pinned = bool(meta.get("pinned", False))
            sid = meta.get("session_id", "")
            is_current = bool(sid) and sid == self.current_session_id
            is_running = sid in self.running_ids
            suffix = tr("sidebar.running_suffix") if is_running else ""
            item = QTreeWidgetItem([f"{title}{suffix}\n{created}"])
            # A running turn (blue LED) takes visual priority over the pin icon.
            if is_running:
                item.setIcon(0, dot_icon(DOT_BLUE))
            elif pinned:
                item.setIcon(0, icon("pin"))
            item.setData(0, Qt.UserRole, str(meta["path"]))
            item.setData(0, Qt.UserRole + 1, kind)
            item.setData(0, Qt.UserRole + 2, pinned)
            item.setData(0, Qt.UserRole + 3, title)
            item.setData(0, Qt.UserRole + 4, is_current)   # persistent highlight
            item.setData(0, Qt.UserRole + 5, is_running)
            parent.addChild(item)

        for _pid, node in groups.items():
            if node.childCount() == 0:
                text = tr("sidebar.no_matches") if query.strip() else tr("sidebar.empty")
                empty = QTreeWidgetItem([text])
                empty.setFlags(Qt.NoItemFlags)
                node.addChild(empty)

    def _on_item(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        # Shift/Ctrl-click means "extend the multi-selection", not "open this
        # conversation" — otherwise every click while multi-selecting for a
        # bulk-delete would also jump into that conversation.
        if QApplication.keyboardModifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
            return
        path = item.data(0, Qt.UserRole)
        kind = item.data(0, Qt.UserRole + 1)
        if not path or not kind:
            return
        conv = load_conversation(path)
        self.open_chat.emit(kind, conv)

    def _selected_conversation_items(self):
        return [it for it in self.tree.selectedItems() if it.data(0, Qt.UserRole)]

    @staticmethod
    def _is_multi_selection(item, selected) -> bool:
        """True when the right-clicked card is part of an existing multi-item
        selection — pure boolean, kept separate from _context_menu so it's
        testable without ever invoking Qt's (modal, event-loop-blocking) menu."""
        return len(selected) > 1 and item in selected

    def _context_menu(self, pos) -> None:
        clicked = self.tree.itemAt(pos)
        if clicked is None:
            return
        selected = self._selected_conversation_items()
        if self._is_multi_selection(clicked, selected):
            self._bulk_delete_menu(pos, selected)
            return
        item = clicked
        path = item.data(0, Qt.UserRole)
        if not path:
            return
        pinned = bool(item.data(0, Qt.UserRole + 2))
        title = item.data(0, Qt.UserRole + 3) or ""
        menu = QMenu(self.tree)
        pin_act = menu.addAction(tr("sidebar.menu.unpin") if pinned else tr("sidebar.menu.pin"))
        rename_act = menu.addAction(tr("sidebar.menu.rename"))
        del_act = menu.addAction(tr("sidebar.menu.delete"))
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == pin_act:
            set_pinned(path, not pinned)
            self.refresh()
        elif chosen == rename_act:
            new, ok = QInputDialog.getText(
                self, tr("sidebar.rename.title"), tr("sidebar.rename.label"), text=title)
            if ok and new.strip():
                rename_conversation(path, new.strip())
                self.refresh()
        elif chosen == del_act:
            if QMessageBox.question(
                    self, tr("sidebar.delete.title"), tr("sidebar.delete.confirm", title=title)
            ) == QMessageBox.Yes:
                delete_conversation(path)
                self.refresh()
                self.history_changed.emit()

    def _bulk_delete_menu(self, pos, selected) -> None:
        """Right-click on a multi-selection (Shift/Ctrl-click several
        conversations first): one action deletes every selected conversation.
        The popup itself is a thin wrapper — see _confirm_and_delete_selected
        for the actual (independently testable) confirm+delete logic."""
        menu = QMenu(self.tree)
        del_act = menu.addAction(tr("sidebar.menu.delete_selected", n=len(selected)))
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen == del_act:
            self._confirm_and_delete_selected(selected)

    def _confirm_and_delete_selected(self, selected) -> bool:
        """Confirm, then delete every conversation in ``selected``. Split out
        of _bulk_delete_menu so tests can drive it directly without having to
        fake a real (modal, event-loop-blocking) QMenu popup."""
        if QMessageBox.question(
                self, tr("sidebar.delete.title"),
                tr("sidebar.delete_multi.confirm", n=len(selected))) != QMessageBox.Yes:
            return False
        for item in selected:
            path = item.data(0, Qt.UserRole)
            if path:
                delete_conversation(path)
        self.refresh()
        self.history_changed.emit()
        return True