"""Message composer: multiline input, attachments, Send/Stop, message queue.

Several turns can run at once (up to the configured parallel limit). Once that
limit is reached the composer switches to "Queue" mode: extra messages (with
their attachments) are held in the queue and dispatched automatically as running
turns finish and free up a slot. Files/images can be attached to a message.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QKeyEvent
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..config import CONFIG_DIR
from ..i18n import on_language_changed, tr
from .icons import icon, IconLabel


def _save_pasted_image(image) -> str | None:
    """Save a clipboard/drag QImage to the config dir; return its path."""
    try:
        if not isinstance(image, QImage) or image.isNull():
            return None
        folder = CONFIG_DIR / "pasted"
        folder.mkdir(parents=True, exist_ok=True)
        name = "paste-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + ".png"
        path = folder / name
        if image.save(str(path), "PNG"):
            return str(path)
    except Exception:
        return None
    return None


def _is_local_skill_command(text: str) -> bool:
    """True for a bare ``/skill`` (list) or ``/skill:<name>`` (select) command that
    is answered inline instantly — these must run even while a turn is busy, so they
    bypass the message queue (unlike ``/skill:<name> <request>``, which is a real
    turn and should queue)."""
    import re
    t = (text or "").strip()
    return t == "/skill" or bool(re.match(r"^/skill:[\w\-.]+$", t))


def _is_local_agent_command(text: str) -> bool:
    """Same as ``_is_local_skill_command`` but for the ``/agent`` directive: a bare
    ``/agent`` (list) or ``/agent:<name>`` (select) is answered inline instantly."""
    import re
    t = (text or "").strip()
    return t == "/agent" or bool(re.match(r"^/agent:[\w\-.]+$", t))


def _paths_from_mime(md) -> List[str]:
    paths: List[str] = []
    if md.hasUrls():
        for u in md.urls():
            if u.isLocalFile():
                paths.append(u.toLocalFile())
    if not paths and md.hasImage():
        p = _save_pasted_image(md.imageData())
        if p:
            paths.append(p)
    return paths


class _SkillPopup(QListWidget):
    """The ``/skill`` picker.

    Shown as a NON-activating overlay (``WA_ShowWithoutActivating``) — crucially it
    does NOT grab the keyboard, so the input keeps focus and the user can keep
    typing their request after ``/skill``. Navigation / accept / Esc are handled by
    the parent ``_Input``'s key handler (which still receives every key); clicking
    an item selects it; the popup auto-hides when the input loses focus."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                            | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)


class _Input(QPlainTextEdit):
    """Plain text edit: submits on Enter, accepts pasted/dropped images & files."""

    submit = Signal()
    media_added = Signal(list)
    manage_skills = Signal()   # user picked "Manage skills…" in the /skill popup

    MIN_HEIGHT = 64     # ~2 lines
    MAX_HEIGHT = 220    # ~8 lines, then it scrolls

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        # Use a clean Latin/Vietnamese-friendly UI font for the input (the global
        # '*' rule falls back to Japanese faces, which mis-render some glyphs).
        self.setStyleSheet(
            "font-family: 'Segoe UI', 'Helvetica Neue', 'Arial', sans-serif; font-size: 14px;"
        )
        # Grow with the text (up to MAX_HEIGHT), then scroll instead.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self._adjust_height)
        # "/skill" + "/agent" command popup — lists skills / agents inline.
        self._skill_popup = _SkillPopup(self)
        self._popup_kind = "skill"            # which command the popup is showing
        self._skill_popup.itemClicked.connect(self._accept_item)
        self.textChanged.connect(self._maybe_show_skills)
        self._adjust_height()

    # ---- /skill autocomplete ----------------------------------------
    def _skill_token(self):
        """Locate a ``/skill[:partial]`` command the cursor is currently typing —
        ANYWHERE in the message, not just at the start (so "dùng /skill:foo …"
        with text typed before it still triggers the picker). Mirrors
        ``core.skills.parse_skill_command``'s whitespace-boundary rule.

        Returns ``(start_offset, partial_filter)`` — ``start_offset`` is where the
        ``/skill`` token begins in the document, ``partial_filter`` is the text
        typed after ``:`` (``''`` while still typing the command word itself) — or
        ``None`` when the cursor isn't inside a ``/skill`` token."""
        import re
        pos = self.textCursor().position()
        before = self.toPlainText()[:pos]
        # The token is the whitespace-delimited word ending at the cursor; its
        # start must be the document start or follow whitespace (same boundary
        # parse_skill_command enforces with its (?<!\S) lookbehind).
        start = re.search(r"\S*$", before).start()
        token = before[start:]
        if len(token) >= 2 and "/skill".startswith(token):
            return start, ""   # typing "/s", "/sk", … "/skill" → show the whole list
        m = re.match(r"^/skill:?([\w\-.]*)$", token)
        return (start, m.group(1)) if m else None

    def _skill_filter(self):
        """Return the partial filter while a '/skill' command is being typed
        (anywhere in the message), or None."""
        tok = self._skill_token()
        return tok[1] if tok else None

    def _agent_token(self):
        """Locate a ``/agent[:partial]`` command the cursor is typing (mirror of
        ``_skill_token``). Returns ``(start_offset, partial)`` or None."""
        import re
        pos = self.textCursor().position()
        before = self.toPlainText()[:pos]
        start = re.search(r"\S*$", before).start()
        token = before[start:]
        if len(token) >= 2 and "/agent".startswith(token):
            return start, ""
        m = re.match(r"^/agent:?([\w\-.]*)$", token)
        return (start, m.group(1)) if m else None

    def _maybe_show_skills(self) -> None:
        # One popup serves both commands: show skills while typing /skill, agents
        # while typing /agent (Cowork parity with the Co4E chat).
        stok = self._skill_token()
        if stok is not None:
            self._popup_kind = "skill"
            self._populate_skill_popup(stok[1])
            self._show_cmd_popup()
            return
        atok = self._agent_token()
        if atok is not None:
            self._popup_kind = "agent"
            self._populate_agent_popup(atok[1])
            self._show_cmd_popup()
            return
        self._skill_popup.hide()

    def _populate_skill_popup(self, filt: str) -> None:
        try:
            from ..core.skills import builtin_skills, list_skills
            # Include always-on built-ins so the picker is usable before the user
            # has created any custom skill.
            skills = list_skills() + builtin_skills()
        except Exception:
            skills = []
        f = (filt or "").lower()
        matches = [s for s in skills
                   if f in s.name.lower() or f in s.slug.lower() or f in (s.description or "").lower()]
        self._skill_popup.clear()
        for s in matches:
            text = ("✓ " if s.enabled else "   ") + s.name
            if s.description:
                text += f"  —  {s.description}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, s.slug)
            self._skill_popup.addItem(item)
        if not matches:
            empty = QListWidgetItem(tr("composer.no_skills"))
            empty.setFlags(Qt.NoItemFlags)
            self._skill_popup.addItem(empty)
        manage = QListWidgetItem(tr("composer.manage_skills"))
        manage.setData(Qt.UserRole, "__manage__")
        self._skill_popup.addItem(manage)
        self._skill_popup.setCurrentRow(0 if matches else self._skill_popup.count() - 1)

    def _populate_agent_popup(self, filt: str) -> None:
        try:
            from ..core.agent_command import collect_agents
            agents = collect_agents("")   # built-ins + local admin + custom agents
        except Exception:
            agents = []
        f = (filt or "").lower()
        matches = [a for a in agents
                   if f in a["slug"].lower() or f in a["name"].lower() or f in (a.get("desc") or "").lower()]
        self._skill_popup.clear()
        for a in matches:
            text = a["name"] + (f"  —  {a['desc']}" if a.get("desc") else "")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, a["slug"])
            self._skill_popup.addItem(item)
        if not matches:
            empty = QListWidgetItem(tr("composer.no_agents"))
            empty.setFlags(Qt.NoItemFlags)
            self._skill_popup.addItem(empty)
        self._skill_popup.setCurrentRow(0 if matches else self._skill_popup.count() - 1)

    def _show_cmd_popup(self) -> None:
        rows = min(7, self._skill_popup.count())
        h = 10 + rows * 22
        self._skill_popup.resize(max(300, self.width()), h)
        top_left = self.mapToGlobal(self.rect().topLeft())
        self._skill_popup.move(top_left.x(), top_left.y() - h - 2)
        self._skill_popup.show()

    def _dismiss_skill_popup(self) -> None:
        """Hide the /skill picker (Esc)."""
        self._skill_popup.hide()

    def focusOutEvent(self, e) -> None:  # noqa: N802
        # The popup never grabs focus, so a click away lands here → dismiss it
        # (unless the click is on the popup itself, e.g. picking an item).
        if not self._skill_popup.underMouse():
            self._skill_popup.hide()
        super().focusOutEvent(e)

    def _accept_item(self, item=None) -> None:
        """Dispatch popup selection to the right handler based on which command
        (``/skill`` or ``/agent``) the popup is currently showing."""
        if self._popup_kind == "agent":
            self._accept_agent(item)
        else:
            self._accept_skill(item)

    def _replace_token(self, tok, replacement: str) -> None:
        pos = self.textCursor().position()
        start = tok[0] if tok else pos
        full = self.toPlainText()
        new_text = full[:start] + replacement + full[pos:]
        new_pos = start + len(replacement)
        self.blockSignals(True)
        self.setPlainText(new_text)
        self.blockSignals(False)
        cur = self.textCursor()
        cur.setPosition(min(new_pos, len(new_text)))
        self.setTextCursor(cur)
        self._adjust_height()
        self.setFocus()

    def _accept_skill(self, item=None) -> None:
        item = item or self._skill_popup.currentItem()
        self._skill_popup.hide()
        if item is None:
            return
        slug = item.data(Qt.UserRole)
        if slug == "__manage__":
            self.manage_skills.emit()   # open the Skills manager
            return
        if not slug:
            return
        # Replace ONLY the /skill token the cursor is on — text typed before it
        # ("dùng …") and after it is preserved, so the command can sit mid-sentence.
        self._replace_token(self._skill_token(), f"/skill:{slug} ")

    def _accept_agent(self, item=None) -> None:
        item = item or self._skill_popup.currentItem()
        self._skill_popup.hide()
        if item is None:
            return
        slug = item.data(Qt.UserRole)
        if not slug:
            return
        self._replace_token(self._agent_token(), f"/agent:{slug} ")

    def _adjust_height(self, *_a) -> None:
        # QPlainTextEdit reports the document height in LINES (not pixels), so
        # convert via line spacing to get the real pixel height.
        lines = self.document().size().height() or 1
        line_px = self.fontMetrics().lineSpacing()
        h = int(lines * line_px + 2 * self.frameWidth() + 12)
        h = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, h))
        if h != self.height():
            self.setFixedHeight(h)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if self._skill_popup.isVisible():
            k = e.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                n = self._skill_popup.count()
                if n:
                    step = 1 if k == Qt.Key_Down else -1
                    self._skill_popup.setCurrentRow((self._skill_popup.currentRow() + step) % n)
                return
            if k == Qt.Key_Tab:
                self._accept_item()   # Tab = autocomplete the highlighted item
                return
            if k == Qt.Key_Escape:
                self._dismiss_skill_popup()
                return
            if k in (Qt.Key_Return, Qt.Key_Enter):
                item = self._skill_popup.currentItem()
                slug = item.data(Qt.UserRole) if item else None
                is_agent = self._popup_kind == "agent"
                tok = self._agent_token() if is_agent else self._skill_token()
                prefix = "/agent:" if is_agent else "/skill:"
                token = self.toPlainText()[tok[0]:self.textCursor().position()] if tok else ""
                exact = bool(slug) and slug != "__manage__" and token == f"{prefix}{slug}"
                if slug and slug != "__manage__" and not exact:
                    # A suggestion is highlighted but not yet fully typed —
                    # Enter completes it into the box first (same as Tab),
                    # instead of submitting a partial/mistyped slug that
                    # the parser would just reject as "not found".
                    self._accept_item(item)
                    return
                # Slug already fully typed (or nothing usable is highlighted,
                # e.g. the "no skills found" placeholder) — Enter RUNS the
                # /skill command as typed: hide the popup and fall through to
                # the normal submit below.
                self._skill_popup.hide()
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - paste
        paths = _paths_from_mime(source)
        if paths:
            self.media_added.emit(paths)
            return
        super().insertFromMimeData(source)

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802
        if source.hasImage() or source.hasUrls():
            return True
        return super().canInsertFromMimeData(source)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e) -> None:  # noqa: N802
        paths = _paths_from_mime(e.mimeData())
        if paths:
            self.media_added.emit(paths)
            e.acceptProposedAction()
            return
        super().dropEvent(e)


class Composer(QWidget):
    submitted = Signal(str, list)        # (text, attachment paths)
    stop_requested = Signal()
    queue_changed = Signal(int)
    attachments_added = Signal(list)     # current attachment paths (pushed to the Input box)
    attachment_removed = Signal(str)     # a wrongly-added attachment was removed
    attach_limit_note = Signal(str)      # shown when the attachment-count limit is hit
    manage_skills = Signal()             # relayed from the /skill popup "Manage skills…"

    def __init__(self, placeholder_key: str = "composer.placeholder_default"):
        super().__init__()
        self._placeholder_key = placeholder_key   # i18n key, re-looked-up on language change
        self._queue: List[Dict] = []          # each: {"text": str, "attachments": [str]}
        self._attachments: List[str] = []
        self._max_attachments = 0             # 0 = unlimited; set from Settings
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # --- queue strip (hidden when empty) ---
        self.queue_box = QWidget()
        qlay = QVBoxLayout(self.queue_box)
        qlay.setContentsMargins(0, 0, 0, 0)
        self.queue_label = QLabel()
        self.queue_label.setObjectName("hint")
        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(78)
        self.queue_list.itemDoubleClicked.connect(self._remove_queue_item)
        qlay.addWidget(self.queue_label)
        qlay.addWidget(self.queue_list)
        self.queue_box.setVisible(False)
        root.addWidget(self.queue_box)

        # --- attachments strip (hidden when empty) ---
        self.attach_box = QWidget()
        alay = QVBoxLayout(self.attach_box)
        alay.setContentsMargins(0, 0, 0, 0)
        self.attach_label = QLabel()
        self.attach_label.setObjectName("hint")
        self.attach_list = QListWidget()
        # Single horizontal row of chips; scroll sideways when there are many.
        self.attach_list.setFlow(QListView.LeftToRight)
        self.attach_list.setWrapping(False)
        self.attach_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.attach_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.attach_list.setFixedHeight(40)
        self.attach_list.itemDoubleClicked.connect(self._remove_attachment)
        alay.addWidget(self.attach_label)
        alay.addWidget(self.attach_list)
        self.attach_box.setVisible(False)
        root.addWidget(self.attach_box)

        # --- input row ---
        row = QHBoxLayout()
        self.input = _Input()
        self.input.setPlaceholderText(tr(self._placeholder_key))
        self.input.submit.connect(self._on_submit)
        self.input.media_added.connect(self._add_paths)
        self.input.manage_skills.connect(self.manage_skills.emit)
        row.addWidget(self.input, 1)

        btns = QVBoxLayout()
        self.attach_btn = QPushButton("")
        self.attach_btn.setIcon(icon("attach"))
        self.attach_btn.clicked.connect(self._pick_attachments)
        self.send_btn = QPushButton()
        self.send_btn.setIcon(icon("upload"))
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self._on_submit)
        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(icon("stop"))
        self.stop_btn.setObjectName("danger")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        btns.addWidget(self.attach_btn)
        btns.addWidget(self.send_btn)
        btns.addWidget(self.stop_btn)
        row.addLayout(btns)
        root.addLayout(row)

        # bottom row: left slot (e.g. Cowork's output-folder picker) — stretch —
        # right slot (e.g. Plan/Act toggle, the per-tab Agent combo on Code/Cowork)
        self._bottom_left_count = 0
        self.extra_row = QHBoxLayout()
        self.extra_row.setContentsMargins(0, 0, 0, 0)
        self.extra_row.addStretch(1)
        root.addLayout(self.extra_row)

        on_language_changed(self._retranslate)

    def _retranslate(self) -> None:
        self.queue_list.setToolTip(tr("composer.queue_tooltip"))
        self.attach_list.setToolTip(tr("composer.attachments_tooltip"))
        self.attach_btn.setToolTip(tr("composer.attach_btn_tooltip"))
        self.send_btn.setText(tr("composer.queue_btn") if self._busy else tr("composer.send"))
        self.stop_btn.setText(tr("composer.stop"))
        if self.input.toPlainText().strip() == "" and not self._attachments:
            self.input.setPlaceholderText(tr(self._placeholder_key))
        self._refresh_queue()
        self._refresh_attachments()

    def add_bottom_right(self, widget) -> None:
        self.extra_row.addWidget(widget)

    def add_bottom_left(self, widget) -> None:
        """Insert before the stretch, after any previously-added left widget —
        so repeated calls read left-to-right in call order, same row as
        whatever add_bottom_right widgets (e.g. the Agent combo) sit on the
        right of the stretch."""
        self.extra_row.insertWidget(self._bottom_left_count, widget)
        self._bottom_left_count += 1

    # ---- public API --------------------------------------------------
    def set_text(self, text: str) -> None:
        self.input.setPlainText(text)
        self.input.setFocus()

    def reset_input(self) -> None:
        """Clear the input + pending attachments and restore the default placeholder
        (used on New chat so no stale text or 'Attached: …' hint carries over)."""
        self.input.clear()
        self._attachments = []
        self._refresh_attachments()
        self.input.setPlaceholderText(tr(self._placeholder_key))

    def set_busy(self, busy: bool) -> None:
        """Capacity gate: when True, new sends are queued (the Send button reads
        'Queue'). Independent of whether any turn is running — see set_running."""
        self._busy = busy
        self.send_btn.setText(tr("composer.queue_btn") if busy else tr("composer.send"))

    def set_running(self, running: bool) -> None:
        """Show the Stop button whenever at least one turn is running (may be True
        even when not at capacity, so a single in-flight message can be stopped)."""
        self.stop_btn.setVisible(running)

    def has_queue(self) -> bool:
        return bool(self._queue)

    def pop_next(self) -> Dict | None:
        if not self._queue:
            return None
        item = self._queue.pop(0)
        self._refresh_queue()
        return item

    def clear_queue(self) -> None:
        self._queue.clear()
        self._refresh_queue()

    def enqueue(self, text: str, attachments: List[str] | None = None) -> None:
        self._queue.append({"text": text, "attachments": list(attachments or [])})
        self._refresh_queue()

    # ---- attachments -------------------------------------------------
    def set_max_attachments(self, n: int) -> None:
        self._max_attachments = max(0, int(n or 0))

    def _add_one(self, path: str) -> bool:
        """Add a file unless it's a duplicate or the count limit is reached.
        Returns False (and notifies) when the limit blocked it."""
        if not path or path in self._attachments:
            return True
        if self._max_attachments and len(self._attachments) >= self._max_attachments:
            self.attach_limit_note.emit(tr("chatpanel.attach_limit", n=self._max_attachments))
            return False
        self._attachments.append(path)
        return True

    def _pick_attachments(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, tr("composer.attach_dialog_title"), "",
            tr("composer.attach_dialog_filter"),
        )
        for f in files:
            if not self._add_one(f):
                break
        self._refresh_attachments()

    def _add_paths(self, paths: List[str]) -> None:
        """Add attachments from paste / drag-drop."""
        for p in paths:
            if not self._add_one(p):
                break
        self._refresh_attachments()
        if paths:
            names = ", ".join(Path(p).name for p in paths)
            self.input.setPlaceholderText(tr("chatpanel.attached_hint", names=names))

    def _remove_attachment(self, item: QListWidgetItem) -> None:
        idx = self.attach_list.row(item)
        if 0 <= idx < len(self._attachments):
            self._remove_attachment_path(self._attachments[idx])

    def _remove_attachment_path(self, path: str) -> None:
        """Remove one wrongly-added file (✕ button or double-click)."""
        if path in self._attachments:
            self._attachments.remove(path)
            self._refresh_attachments()
            self.attachment_removed.emit(path)   # also drop it from the Input panel

    def _refresh_attachments(self) -> None:
        self.attach_list.clear()
        for p in self._attachments:
            item = QListWidgetItem()
            row = QWidget()
            row.setStyleSheet("background: rgba(140,146,152,0.18); border-radius: 6px;")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 2, 4, 2)
            h.setSpacing(4)
            short = Path(p).name
            if len(short) > 22:
                short = short[:19] + "…"
            name = IconLabel("attach", short, size=13)
            name.setToolTip(p)
            remove = QPushButton()
            remove.setIcon(icon("close", size=12))
            remove.setObjectName("danger")
            remove.setFixedSize(18, 18)
            remove.setToolTip(tr("composer.remove_tooltip"))
            remove.setCursor(Qt.PointingHandCursor)
            remove.clicked.connect(lambda _=False, path=p: self._remove_attachment_path(path))
            h.addWidget(name)       # compact chip (no stretch → many fit in one row)
            h.addWidget(remove)
            item.setSizeHint(row.sizeHint())
            self.attach_list.addItem(item)
            self.attach_list.setItemWidget(item, row)
        self.attach_label.setText(tr("composer.attachments_label", n=len(self._attachments)))
        self.attach_box.setVisible(bool(self._attachments))
        if self._attachments:
            self.attachments_added.emit(list(self._attachments))

    # ---- submit / queue ----------------------------------------------
    def _on_submit(self) -> None:
        text = self.input.toPlainText().strip()
        attachments = list(self._attachments)
        if not text and not attachments:
            return
        self.input.clear()
        self._attachments = []
        self._refresh_attachments()
        self.input.setPlaceholderText(tr(self._placeholder_key))   # clear any "Attached: …" hint
        # A local /skill or /agent list/select command is answered inline instantly
        # — run it now even while a turn is busy (don't bury it in the queue).
        if self._busy and not (_is_local_skill_command(text) or _is_local_agent_command(text)):
            self._queue.append({"text": text, "attachments": attachments})
            self._refresh_queue()
        else:
            self.submitted.emit(text, attachments)

    def _remove_queue_item(self, item: QListWidgetItem) -> None:
        idx = self.queue_list.row(item)
        if 0 <= idx < len(self._queue):
            self._queue.pop(idx)
            self._refresh_queue()

    def _refresh_queue(self) -> None:
        self.queue_list.clear()
        for i, entry in enumerate(self._queue, 1):
            text = entry.get("text", "")
            n = len(entry.get("attachments", []))
            preview = text if len(text) <= 70 else text[:70] + "…"
            if n:
                preview += f"  (+{n})"
            self.queue_list.addItem(f"{i}. {preview}")
        self.queue_label.setText(tr("composer.queue_label", n=len(self._queue)))
        self.queue_box.setVisible(bool(self._queue))
        self.queue_changed.emit(len(self._queue))
