"""Scrollable chat transcript built from message bubbles."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..i18n import on_language_changed, tr
from ..theme import ACCENT, resolve_theme
from ..config import CONFIG_DIR
from .osutil import is_image, open_folder, open_path


def _app_theme() -> str:
    """Resolve the current app theme (light or dark) from config."""
    try:
        import json
        with open(CONFIG_DIR / "config.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return resolve_theme(data.get("theme", "dark"))
    except Exception:  # noqa: BLE001
        return "dark"


# Timeline dot color per role (reads on both themes — small, saturated).
_DOT = {
    "user": "#48CAE4", "assistant": "#48D9A0", "tool": "#9B8FF7",
    "error": "#E5484D", "success": "#48D9A0",
}


class _TimelineGutter(QWidget):
    """The left rail of the point-conversation: a vertical connector line with a
    role-colored dot near the top, so stacked messages read as a timeline
    (Claude-Code style) instead of separate boxes."""

    def __init__(self, role: str):
        super().__init__()
        self._role = role
        self.setFixedWidth(22)

    def set_role(self, role: str) -> None:
        self._role = role
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = _app_theme() == "dark"
        x = 11.0
        cy = 15.0
        # connector line (faint) running the full height → continuous rail
        p.setPen(QPen(QColor("#243a56" if dark else "#CBDDEC"), 2))
        p.drawLine(int(x), 0, int(x), self.height())
        # a background ring lifts the dot off the line
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0A1628" if dark else "#E8F4FD"))
        p.drawEllipse(QPointF(x, cy), 7.5, 7.5)
        p.setBrush(QColor(_DOT.get(self._role, "#8FB2D4")))
        p.drawEllipse(QPointF(x, cy), 4.5, 4.5)


def _diff_legend(diff_text: str) -> str:
    """A small badge pair labeling what the colors mean: 'Before → After' for
    an edit, or a single 'Added'/'Removed' badge for a pure create/delete —
    so the before/after distinction is explicit, not just implied by color."""
    has_add = any(ln.startswith("+") and not ln.startswith("+++") for ln in diff_text.splitlines())
    has_del = any(ln.startswith("-") and not ln.startswith("---") for ln in diff_text.splitlines())
    before = (f'<span style="background:#3a1620; color:#ff9aa8; padding:1px 8px; '
              f'border-radius:4px; font-weight:600;">{html.escape(tr("chat.diff_before"))}</span>')
    after = (f'<span style="background:#0d3321; color:#7ee2a8; padding:1px 8px; '
             f'border-radius:4px; font-weight:600;">{html.escape(tr("chat.diff_after"))}</span>')
    if has_add and has_del:
        badge = f'{before}<span style="color:#8b8d98;"> → </span>{after}'
    elif has_add:
        badge = (f'<span style="background:#0d3321; color:#7ee2a8; padding:1px 8px; '
                  f'border-radius:4px; font-weight:600;">{html.escape(tr("chat.diff_added"))}</span>')
    elif has_del:
        badge = (f'<span style="background:#3a1620; color:#ff9aa8; padding:1px 8px; '
                  f'border-radius:4px; font-weight:600;">{html.escape(tr("chat.diff_removed"))}</span>')
    else:
        return ""
    return f'<div style="margin-bottom:6px;">{badge}</div>'


def diff_to_html(diff_text: str) -> str:
    """Render a unified diff with GitHub/Claude-Code-style line coloring —
    additions green, deletions red, hunk headers highlighted — plus an
    explicit Before/After (or Added/Removed) legend, instead of a flat text
    block, so a before/after edit reads at a glance. A brand-new file (an
    empty 'before') naturally renders as all-green, which is exactly what
    ``difflib.unified_diff`` already produces for it."""
    legend = _diff_legend(diff_text)
    rows = []
    for ln in diff_text.splitlines():
        esc = html.escape(ln) if ln else "&nbsp;"
        if ln.startswith(("+++", "---")):
            rows.append(f'<div style="color:#8b8d98;">{esc}</div>')
        elif ln.startswith("@@"):
            rows.append(f'<div style="color:#7c8aff;">{esc}</div>')
        elif ln.startswith("+"):
            rows.append(f'<div style="background:#0d3321; color:#7ee2a8;">{esc}</div>')
        elif ln.startswith("-"):
            rows.append(f'<div style="background:#3a1620; color:#ff9aa8;">{esc}</div>')
        else:
            rows.append(f"<div>{esc}</div>")
    body = "".join(rows) or "(no textual change)"
    return (f'{legend}<div style="font-family:Consolas,\'Courier New\',monospace; font-size:12.5px; '
            f'white-space:pre-wrap;">{body}</div>')


def format_status_line(base: str, ticks: int) -> str:
    """Animated status line for the working indicator, e.g. ``🤖 Running..`` and,
    once the wait is a few seconds long, ``🤖 Running.   ·   5s`` — so a slow
    synthesis clearly reads as still running. ``ticks`` advances every 500 ms."""
    dots = "." * (ticks % 4)
    secs = ticks // 2
    suffix = f"   ·   {secs}s" if secs >= 3 else ""
    return f"{base}{dots}{suffix}"


class ThinkingIndicator(QWidget):
    """A small animated 'the agent is working' line shown while waiting for a
    result, so a long wait never looks like a frozen / empty screen.

    Renders a bot icon + status (e.g. ``🤖 Running…``) and, once the wait passes
    a few seconds, the elapsed time — so a long synthesis clearly reads as still
    running rather than stuck."""

    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 2, 14, 4)
        lay.setSpacing(0)
        self._label = QLabel("")
        self._label.setObjectName("hint")
        lay.addWidget(self._label)
        lay.addStretch(1)
        self._base_key = "chat.running"
        self._override: str | None = None
        self._ticks = 0
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)
        on_language_changed(self._render)

    def start(self, label_key: str = "chat.running") -> None:
        self._base_key = label_key
        self._override = None
        self._ticks = 0
        self._render()
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()

    def set_label(self, label_key: str) -> None:
        if label_key != self._base_key:
            self._base_key = label_key
            self._override = None
            self._render()

    def set_progress_text(self, text: str) -> None:
        """Show an already-formatted, literal status line (e.g. a live "reading
        page 12/40" or streamed command-output detail) instead of a translated
        key — used for fine-grained progress within a single step."""
        self._override = text
        self._render()

    def stop(self) -> None:
        self._timer.stop()
        self._override = None
        self.setVisible(False)

    def _tick(self) -> None:
        self._ticks += 1
        self._render()

    def _render(self) -> None:
        base = self._override if self._override is not None else tr(self._base_key)
        self._label.setText(format_status_line(base, self._ticks))


class MessageBubble(QFrame):
    """One message; assistant/tool bubbles render markdown via QTextBrowser."""

    def __init__(self, role: str, title: str = "", collapsible: bool = False,
                 collapsed: bool = True):
        super().__init__()
        self.role = role
        self._text = ""
        self._collapsible = collapsible
        self._title = title
        self._head = None
        # Point-conversation layout: [dot rail][content column].
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._gutter = _TimelineGutter(role)
        outer.addWidget(self._gutter)
        content = QWidget()
        lay = QVBoxLayout(content)
        lay.setContentsMargins(2, 4, 8, 8)
        lay.setSpacing(4)
        self._content_layout = lay
        outer.addWidget(content, 1)

        if title:
            if collapsible:
                # Clickable header that folds long tool output away to keep the
                # transcript short. Collapsed by default; click to expand.
                self._head = QPushButton(title)
                self._head.setCursor(Qt.PointingHandCursor)
                self._head.setStyleSheet(
                    "QPushButton { text-align:left; border:none; background:transparent;"
                    " font-weight:600; color:#8b8d98; padding:0; }")
                self._head.clicked.connect(self._toggle_body)
                lay.addWidget(self._head)
            else:
                head = QLabel(title)
                head.setStyleSheet("font-weight:600; color:#8b8d98;")
                lay.addWidget(head)

        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setFrameShape(QFrame.NoFrame)
        # Text color adapts to theme.
        self._apply_theme_styles(role)
        lay.addWidget(self.body)

        self._apply_style(role)
        if collapsible and collapsed:
            self.body.setVisible(False)
        if collapsible:
            self._update_head()

    def _toggle_body(self) -> None:
        self.body.setVisible(not self.body.isVisible())
        if self.body.isVisible():
            self._autosize()
        self._update_head()

    def _update_head(self) -> None:
        if not self._head:
            return
        expanded = self.body.isVisible()
        arrow = "▾" if expanded else "▸"
        preview = ""
        if not expanded and self._text.strip():
            first = self._text.strip().splitlines()[0]
            if len(first) > 70:
                first = first[:70] + "…"
            preview = f"   {first}"
        self._head.setText(f"{arrow} {self._title}{preview}")

    def _current_theme(self) -> str:
        """Resolve the current app theme (light or dark)."""
        return _app_theme()

    def _apply_theme_styles(self, role: str) -> None:
        """Apply text color to the body QTextBrowser based on current theme + role."""
        theme = self._current_theme()
        if theme == "light":
            if role == "success":
                text_color = "#1B7A3D"
            elif role == "error":
                text_color = "#C0392B"
            elif role in ("tool",):
                text_color = "#5C6B7A"          # muted (secondary) like Claude's steps
            else:
                text_color = "#1A2332"
        else:
            if role == "success":
                text_color = "#7ee2a8"
            elif role == "error":
                text_color = "#ff9aa8"
            elif role in ("tool",):
                text_color = "#9aa6b8"
            else:
                text_color = "#eceef2"
        self.body.setStyleSheet(f"background: transparent; border: none; color: {text_color};")

    def _apply_style(self, role: str) -> None:
        """Flat timeline row — no bubble box; the left dot/rail conveys role and
        structure (Claude-Code style). The user's own message gets a faint tint
        so questions are easy to pick out when scanning."""
        theme = self._current_theme()
        if role == "user":
            tint = "rgba(72,202,228,0.10)" if theme == "dark" else "rgba(72,202,228,0.14)"
            self.setStyleSheet(
                f"QFrame {{ background: {tint}; border: none; border-radius: 10px; }}")
        else:
            self.setStyleSheet("QFrame { background: transparent; border: none; }")

    def apply_theme(self) -> None:
        """Re-apply theme-dependent styles so existing rows adapt when the app
        theme switches (light ↔ dark)."""
        self._apply_theme_styles(self.role)
        self._apply_style(self.role)
        self._gutter.set_role(self.role)

    def chat_view(self):
        """Walk up the parent chain to find the enclosing ChatView, if any."""
        p = self.parent()
        while p is not None:
            if isinstance(p, ChatView):
                return p
            p = p.parent()
        return None

    def append_delta(self, delta: str) -> None:
        self._text += delta
        self.set_markdown(self._text)

    def set_markdown(self, text: str) -> None:
        self._text = text
        self.body.setMarkdown(text)
        self._autosize()
        if self._collapsible:
            self._update_head()

    def set_plain(self, text: str) -> None:
        self._text = text
        self.body.setPlainText(text)
        self._autosize()
        if self._collapsible:
            self._update_head()

    def append_plain(self, delta: str) -> None:
        self._text += delta
        self.set_plain(self._text)

    def set_diff(self, diff_text: str) -> None:
        """Render a unified diff (see :func:`diff_to_html`) with colored
        before/after lines instead of a flat text block."""
        self._text = diff_text
        self.body.setHtml(diff_to_html(diff_text))
        self._autosize()
        if self._collapsible:
            self._update_head()

    def add_usage(self, text: str) -> None:
        """A small muted token/cost footer under the message (↓in ↑out ▤ctx $cost),
        like Claude Code. Replaces any previous usage line on this bubble."""
        existing = getattr(self, "_usage_lbl", None)
        if existing is not None:
            existing.setText(text)
            return
        lbl = QLabel(text)
        lbl.setObjectName("hint")
        lbl.setStyleSheet("color: rgba(140,146,152,0.9); font-size: 11px;")
        self._usage_lbl = lbl
        self._content_layout.addWidget(lbl)

    def add_delete_link(self, callback) -> None:
        link = QLabel(f'<a href="#del" style="color:#ef6368;">{tr("chat.delete_link")}</a>')
        link.setToolTip(tr("chat.delete_tooltip"))
        link.linkActivated.connect(lambda *_: callback())
        self._content_layout.addWidget(link)

    def add_folder_link(self, folder: str, label: str | None = None) -> None:
        label = label or tr("chat.open_workspace")
        link = QLabel(f'<a href="#open" style="color:{ACCENT};">{label}</a>')
        link.setToolTip(str(folder))
        link.linkActivated.connect(lambda *_: open_folder(folder))
        self._content_layout.addWidget(link)

    def add_attachments(self, paths) -> None:
        """Show attached files: images as thumbnails, others as clickable links."""
        for p in paths:
            path = str(p)
            name = Path(path).name
            if is_image(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    thumb = QLabel()
                    thumb.setPixmap(pix.scaledToWidth(min(320, pix.width()), Qt.SmoothTransformation))
                    thumb.setToolTip(name)
                    thumb.setCursor(Qt.PointingHandCursor)
                    self._content_layout.addWidget(thumb)
                    continue
            file_link = QLabel(f'<a href="#open" style="color:{ACCENT};">{name}</a>')
            file_link.setToolTip(path)
            file_link.linkActivated.connect(lambda *_a, fp=path: open_path(fp))
            self._content_layout.addWidget(file_link)

    def _autosize(self) -> None:
        width = self.body.viewport().width()
        if width <= 0:
            width = 560  # sensible default before the widget is laid out
        self.body.document().setTextWidth(width)
        height = int(self.body.document().size().height()) + 12
        self.body.setFixedHeight(max(28, min(height, 1200)))

    def resizeEvent(self, event):  # noqa: N802 - re-flow on width change
        super().resizeEvent(event)
        self._autosize()


class ChatView(QScrollArea):
    """Scrollable chat transcript.

    Emits ``theme_changed`` (via the apply_theme method) so every child
    ``MessageBubble`` can re-apply its theme-aware inline styles when the
    app switches between light and dark modes."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self._container = QWidget()
        self._lay = QVBoxLayout(self._container)
        self._lay.setContentsMargins(12, 12, 12, 12)
        self._lay.setSpacing(10)
        self._lay.addStretch(1)
        self.setWidget(self._container)

    def apply_theme(self) -> None:
        """Ask every MessageBubble inside this view to re-apply theme styles.

        Called from ``ChatPanel.apply_theme`` whenever the app theme changes."""
        for i in range(self._lay.count()):
            item = self._lay.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, MessageBubble):
                w.apply_theme()

    def _add(self, bubble: MessageBubble) -> MessageBubble:
        # insert before the trailing stretch
        self._lay.insertWidget(self._lay.count() - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def add_user(self, text: str) -> MessageBubble:
        b = MessageBubble("user", tr("chat.you"))
        b.set_plain(text)
        return self._add(b)

    def add_assistant(self, title: str | None = None) -> MessageBubble:
        b = MessageBubble("assistant", title or tr("chat.assistant"))
        return self._add(b)

    def add_tool(self, title: str, body: str, ok: bool = True) -> MessageBubble:
        # Tool steps (run command, generated code/diff, output) are collapsible to
        # keep the transcript short — collapsed when OK, expanded on error.
        b = MessageBubble("tool" if ok else "error", title, collapsible=True, collapsed=ok)
        b.set_plain(body)
        return self._add(b)

    def add_diff(self, title: str, diff_text: str, ok: bool = True) -> MessageBubble:
        """Like :meth:`add_tool`, but renders ``diff_text`` as a colored
        before/after diff (see :func:`diff_to_html`) instead of flat text."""
        b = MessageBubble("tool" if ok else "error", title, collapsible=True, collapsed=ok)
        b.set_diff(diff_text)
        return self._add(b)

    def add_plan(self, body: str) -> MessageBubble:
        """The task plan shown INLINE in the timeline (never a pop-up or side
        panel) — a permanent, always-expanded row whose steps tick off as they
        complete. The agent re-sends the full list on each update; the caller
        updates this same row in place via ``set_plain``."""
        b = MessageBubble("tool", tr("widgets.plan_title"), collapsible=False)
        b.set_plain(body)
        return self._add(b)

    def add_reasoning(self, title: str | None = None) -> MessageBubble:
        # The model's private reasoning — a collapsed, collapsible box so the user
        # can see it's thinking (and expand to read) without it flooding the chat.
        b = MessageBubble("tool", title or tr("chat.thinking"), collapsible=True, collapsed=True)
        return self._add(b)

    def add_error(self, text: str) -> MessageBubble:
        b = MessageBubble("error", tr("chat.error"))
        b.set_plain(text)
        return self._add(b)

    def add_status(self, text: str) -> MessageBubble:
        """A small one-line status marker in the transcript (e.g. '✅ Đã hoàn thành')."""
        b = MessageBubble("tool", "")
        b.set_plain(text)
        return self._add(b)

    def add_success(self, text: str) -> MessageBubble:
        """Like :meth:`add_status`, but styled green — used for the "turn done"
        marker so completion reads as an unmistakable success signal."""
        b = MessageBubble("success", "")
        b.set_plain(text)
        return self._add(b)

    def clear(self) -> None:
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def scroll_to_bottom(self) -> None:
        """Scroll to the newest message, deferred so freshly-added bubbles have
        finished sizing (their height is computed after layout)."""
        QTimer.singleShot(0, self._scroll_to_bottom)
        QTimer.singleShot(80, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
