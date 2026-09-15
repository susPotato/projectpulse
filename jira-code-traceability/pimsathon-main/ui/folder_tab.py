"""Folder tab — a two-pane file explorer for the Workspace.

Left: a directory tree (QFileSystemModel). Right: view / edit the selected file
directly in the folder:

* **Source code + text/config + HTML source** — an editable code editor with
  VS-Code-style syntax colouring (Pygments), line numbers, dark theme.
* **HTML** — a rendered Preview (WebEngine when available, else rich text) with
  a Preview⇄Edit toggle.
* **Office docs** (doc/docx/ppt/pptx/xls/xlsx/pdf) — an in-app text preview
  (extracted via the same parser attachments use) plus "Open externally" for
  full-fidelity viewing.
* **Images** — shown inline.

Everything is best-effort and never raises: an unreadable/oversized/binary file
degrades to an explanatory note.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QComboBox, QFileSystemModel, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QTreeView,
    QVBoxLayout, QWidget,
)

from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .chat_view import ChatView
from .icons import icon
from .libreoffice_view import DOC_SUFFIXES

try:
    from .structure_graph_view import _HAS_WEB
except Exception:  # pragma: no cover
    _HAS_WEB = False

try:
    from PySide6.QtPdf import QPdfDocument  # noqa: F401
    from PySide6.QtPdfWidgets import QPdfView  # noqa: F401
    _HAS_PDF = True
except Exception:  # pragma: no cover - QtPdf not bundled
    _HAS_PDF = False

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico"}
_HTML_SUFFIXES = {".html", ".htm"}
_PPTX_SUFFIXES = {".pptx"}          # text-editable in-app via python-pptx (no PowerPoint)
_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}  # viewed as a real table via openpyxl (no LibreOffice)
_MAX_EDIT_BYTES = 2_000_000        # above this, view read-only / note only
_MAX_HIGHLIGHT_CHARS = 300_000     # skip colouring huge files (keeps typing snappy)


# ── VS-Code-Dark+-ish token palette ────────────────────────────────────────
def _fmt(color: str, *, italic: bool = False, bold: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if italic:
        f.setFontItalic(True)
    if bold:
        f.setFontWeight(QFont.Bold)
    return f


class PygmentsHighlighter(QSyntaxHighlighter):
    """Colour the whole document with Pygments and apply per-block. Re-lexes the
    full text (debounced) so multi-line strings/comments colour correctly."""

    def __init__(self, document):
        super().__init__(document)
        from pygments.lexers.special import TextLexer
        self._lexer = TextLexer(stripnl=False)
        self._ranges: list[tuple[int, int, QTextCharFormat]] = []
        self._rules = self._build_rules()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._retokenize)
        document.contentsChanged.connect(self._timer.start)

    @staticmethod
    def _build_rules():
        from pygments.token import (
            Comment, Error, Keyword, Name, Number, Operator, Punctuation, String,
        )
        # Ordered specific → general: first matching token type wins.
        return [
            (Comment, _fmt("#6A9955", italic=True)),
            (Keyword.Type, _fmt("#4EC9B0")),
            (Keyword, _fmt("#569CD6")),
            (Name.Function, _fmt("#DCDCAA")),
            (Name.Class, _fmt("#4EC9B0")),
            (Name.Decorator, _fmt("#DCDCAA")),
            (Name.Builtin, _fmt("#4EC9B0")),
            (Name.Tag, _fmt("#569CD6")),
            (Name.Attribute, _fmt("#9CDCFE")),
            (String.Doc, _fmt("#6A9955", italic=True)),
            (String, _fmt("#CE9178")),
            (Number, _fmt("#B5CEA8")),
            (Operator, _fmt("#D4D4D4")),
            (Punctuation, _fmt("#D4D4D4")),
            (Error, _fmt("#F44747")),
        ]

    def set_filename(self, filename: str, text: str = "") -> None:
        from pygments.lexers import get_lexer_for_filename, guess_lexer
        from pygments.lexers.special import TextLexer
        from pygments.util import ClassNotFound
        try:
            self._lexer = get_lexer_for_filename(filename, stripnl=False)
        except ClassNotFound:
            try:
                self._lexer = guess_lexer(text) if text.strip() else TextLexer()
            except ClassNotFound:
                self._lexer = TextLexer(stripnl=False)
        self._retokenize()

    def _fmt_for(self, tok):
        for ttype, fmt in self._rules:
            if tok in ttype:
                return fmt
        return None

    def _retokenize(self) -> None:
        from pygments import lex
        text = self.document().toPlainText()
        self._ranges = []
        if len(text) <= _MAX_HIGHLIGHT_CHARS:
            pos = 0
            for tok, val in lex(text, self._lexer):
                fmt = self._fmt_for(tok)
                if fmt is not None and val:
                    self._ranges.append((pos, pos + len(val), fmt))
                pos += len(val)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        if not self._ranges:
            return
        bstart = self.currentBlock().position()
        bend = bstart + len(text)
        for start, end, fmt in self._ranges:
            if end <= bstart or start >= bend:
                continue
            s = max(start, bstart) - bstart
            e = min(end, bend) - bstart
            if e > s:
                self.setFormat(s, e - s, fmt)


class _LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.line_number_width(), 0)

    def paintEvent(self, event):  # noqa: N802
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """A dark, monospaced editor with a line-number gutter + Pygments colouring —
    the Sublime/VS-Code look for viewing & editing source files."""

    def __init__(self):
        super().__init__()
        self.setObjectName("codeEditor")
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        font = QFont("Consolas")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        self.setStyleSheet(
            "#codeEditor { background: #1e1e1e; color: #d4d4d4; border: none; "
            "selection-background-color: #264f78; }")
        self._gutter = _LineNumbers(self)
        self.blockCountChanged.connect(lambda _=0: self._update_gutter_width())
        self.updateRequest.connect(self._on_update_request)
        self._highlighter = PygmentsHighlighter(self.document())
        self._update_gutter_width()

    # ---- line-number gutter -------------------------------------------------
    def line_number_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _on_update_request(self, rect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.line_number_width(), cr.height()))

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#1a1a1a"))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        painter.setPen(QColor("#858585"))
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, int(top), self._gutter.width() - 6,
                                 self.fontMetrics().height(), Qt.AlignRight,
                                 str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            num += 1

    def load_file(self, path: str, text: str) -> None:
        self.setPlainText(text)
        self._highlighter.set_filename(path, text)


class FolderTab(QWidget):
    """Two-pane file explorer: directory tree + view/edit pane."""

    status_message = Signal(str)

    def __init__(self, ctx: AppContext, cowork=None):
        super().__init__()
        self.ctx = ctx
        self._cowork = cowork          # shared Cowork tab → reuse its conversation
        self._ai_worker = None
        self._ai_queue: list[str] = []      # instructions waiting for the current run
        self._img_scan_worker = None        # background scan for image models (all providers)
        self._all_image_models: list = []   # [(provider_key, model)] found across ALL providers
        self._ai_models_provider = ""       # which provider the AI-edit model list was fetched for
        self._edit_kind: Optional[str] = None   # None | "html" | "pptx" (what the editor holds)
        self._current_file: Optional[str] = None
        self._root = str(ctx.config.cowork_output_dir())
        self._pdf_view = None          # lazy QtPdf view for office/pdf rendering
        self._pdf_doc = None
        self._pdf_tmp: Optional[str] = None
        self._pdf_cache: dict = {}     # (path, mtime) → converted .pdf path
        self._convert_worker = None
        self._xlsx_view = None         # lazy QTabWidget table view for spreadsheets

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.path_edit = QLineEdit(self._root)
        self.path_edit.setReadOnly(True)
        self._open_btn = QPushButton()
        self._open_btn.setIcon(icon("folder"))
        self._open_btn.setObjectName("primary")
        self._open_btn.clicked.connect(self._pick_root)
        bar.addWidget(self.path_edit, 1)
        bar.addWidget(self._open_btn)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)

        # ---- left: directory tree ------------------------------------------
        self.model = QFileSystemModel()
        self.model.setRootPath(self._root)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(self._root))
        for col in (1, 2, 3):          # hide Size / Type / Date-modified columns
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(self._on_tree_clicked)
        split.addWidget(self.tree)

        # ---- right: view / edit pane ---------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        hdr = QHBoxLayout()
        self.file_label = QLabel("")
        self.file_label.setStyleSheet("font-weight:600;")
        self.file_label.setWordWrap(True)
        hdr.addWidget(self.file_label, 1)
        self.mode_btn = QPushButton()      # Preview⇄Edit toggle (HTML / PPTX)
        self.mode_btn.setCheckable(True)
        self.mode_btn.clicked.connect(self._toggle_edit_mode)
        self.mode_btn.setVisible(False)
        hdr.addWidget(self.mode_btn)
        self.ai_btn = QPushButton()        # expand/collapse the AI-edit panel
        self.ai_btn.setIcon(icon("sparkle"))
        self.ai_btn.setCheckable(True)
        self.ai_btn.clicked.connect(self._toggle_ai_panel)
        hdr.addWidget(self.ai_btn)
        self.save_btn = QPushButton()
        self.save_btn.setIcon(icon("save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setVisible(False)
        hdr.addWidget(self.save_btn)
        self.ext_btn = QPushButton()
        self.ext_btn.setIcon(icon("upload"))
        self.ext_btn.clicked.connect(self._open_external)
        self.ext_btn.setVisible(False)
        hdr.addWidget(self.ext_btn)
        rl.addLayout(hdr)

        self.stack = QStackedWidget()
        self._placeholder = QLabel("")
        self._placeholder.setObjectName("hint")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self.stack.addWidget(self._placeholder)                       # 0

        self.editor = CodeEditor()                                    # 1
        self.stack.addWidget(self.editor)

        # HTML preview: a lightweight QTextBrowser fallback always exists; a real
        # QWebEngineView is created LAZILY the first time an HTML file is
        # previewed (so startup/tests never build WebEngine, and the onefile
        # build — where WebEngine crashes — stays on the fallback).
        self.web = QTextBrowser()                                     # 2
        self.web.setOpenExternalLinks(True)
        self.stack.addWidget(self.web)
        self._engine = None

        self.doc_view = QTextBrowser()                                # 3
        self.doc_view.setObjectName("docPreview")
        self.stack.addWidget(self.doc_view)

        self._img_scroll = QScrollArea()                              # 4
        self._img_scroll.setWidgetResizable(True)
        self._img_label = QLabel("")
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_scroll.setWidget(self._img_label)
        self.stack.addWidget(self._img_scroll)

        # Preview/editor on the left, a collapsible AI-edit panel on the right.
        content_split = QSplitter(Qt.Horizontal)
        content_split.addWidget(self.stack)
        content_split.addWidget(self._build_ai_panel())
        content_split.setStretchFactor(0, 1)
        content_split.setStretchFactor(1, 0)
        content_split.setSizes([700, 320])
        self._content_split = content_split
        self._ai_panel.setVisible(False)   # default collapsed
        rl.addWidget(content_split, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 800])
        root.addWidget(split, 1)

        # Terminal CLI below the file view — collapsible, default collapsed;
        # opening it points the shell at the current workspace folder.
        from .terminal_panel import TerminalPanel

        self.terminal = TerminalPanel()
        self.terminal.set_cwd(self._root)
        self.terminal.expanded.connect(lambda: self.terminal.set_cwd(self._root))
        root.addWidget(self.terminal)

        on_language_changed(self._retranslate)
        self._retranslate()

    # ---- public API ---------------------------------------------------------
    def set_root(self, path: str) -> None:
        p = str(path or "").strip()
        if not p or not os.path.isdir(p):
            return
        self._root = p
        self.path_edit.setText(p)
        self.model.setRootPath(p)
        self.tree.setRootIndex(self.model.index(p))
        if getattr(self, "terminal", None) is not None:
            self.terminal.set_cwd(p)   # terminal follows the workspace folder

    # ---- tree selection ------------------------------------------------------
    def _pick_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("folder.open_folder"), self._root)
        if chosen:
            self.set_root(chosen)

    def _on_tree_clicked(self, index) -> None:
        path = self.model.filePath(index)
        if path and os.path.isfile(path):
            self.open_file(path)

    # ---- open a file the right way -------------------------------------------
    def open_file(self, path: str, reset: bool = True) -> None:
        # Switching to a DIFFERENT file starts a fresh AI-edit conversation, so
        # the previous file's chat can't bleed into (hallucinate) the new file.
        # (reset=False when the AI just CREATED this file — keep that chat.)
        if reset and path != self._current_file:
            self._reset_ai_conversation()
        self._current_file = path
        self.file_label.setText(path)
        suffix = Path(path).suffix.lower()
        self.mode_btn.setVisible(False)
        self.save_btn.setVisible(False)
        self.ext_btn.setVisible(False)
        self._edit_kind = None
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0

        if suffix in _IMAGE_SUFFIXES:
            self._show_image(path)
        elif suffix in _HTML_SUFFIXES:
            self._show_html(path, mode_preview=True)
        elif suffix in _PPTX_SUFFIXES and _pptx_available():
            self._show_pptx(path, mode_preview=True)
        elif suffix in _EXCEL_SUFFIXES:
            self._show_excel(path)
        elif suffix in DOC_SUFFIXES:
            self._show_document(path)
        elif size > _MAX_EDIT_BYTES or not _is_probably_text(path):
            self._show_binary(path)
        else:
            self._show_code(path)

    def _show_code(self, path: str) -> None:
        text = _read_text(path)
        self.editor.setReadOnly(False)
        self.editor.load_file(path, text)
        self.save_btn.setVisible(True)
        self.stack.setCurrentWidget(self.editor)

    def _show_html(self, path: str, mode_preview: bool) -> None:
        self._edit_kind = "html"
        self.mode_btn.setVisible(True)
        self.mode_btn.setChecked(not mode_preview)   # checked = Edit
        self._retranslate_mode_btn()
        if mode_preview:
            from PySide6.QtCore import QUrl
            html = _read_text(path)
            engine = self._ensure_engine()
            if engine is not None:
                engine.setHtml(html, QUrl.fromLocalFile(path))
                self.stack.setCurrentWidget(engine)
            else:
                self.web.setHtml(html)
                self.stack.setCurrentWidget(self.web)
            self.save_btn.setVisible(False)
        else:
            self._show_code(path)

    def _show_pptx(self, path: str, mode_preview: bool) -> None:
        """PPTX: Preview renders the slides (PDF via LibreOffice); Edit shows the
        deck's text (marker-delimited per box) in the editor. Saving/AI-editing
        writes the text back into the .pptx silently (no PowerPoint window)."""
        self._edit_kind = "pptx"
        self.mode_btn.setVisible(True)
        self.mode_btn.setChecked(not mode_preview)   # checked = Edit
        self._retranslate_mode_btn()
        self.ext_btn.setVisible(True)
        if mode_preview:
            self._show_document(path)                # PDF render of the slides
            self.mode_btn.setVisible(True)           # _show_document doesn't touch it
        else:
            from ..core.pptx_edit import pptx_to_text
            try:
                text = pptx_to_text(path)
            except Exception as exc:  # noqa: BLE001
                text = f"[could not read pptx text: {exc}]"
            self.editor.setReadOnly(False)
            self.editor.load_file(path + ".txt", text)   # .txt → plain highlighting
            self.save_btn.setVisible(True)
            self.stack.setCurrentWidget(self.editor)

    def _ensure_engine(self):
        """Create the QWebEngineView on first HTML preview (only when WebEngine
        is safe to use); otherwise stay on the QTextBrowser fallback."""
        if not _HAS_WEB:
            return None
        if self._engine is None:
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView
                self._engine = QWebEngineView()
                self.stack.addWidget(self._engine)
            except Exception:  # noqa: BLE001
                self._engine = None
        return self._engine

    def _toggle_edit_mode(self) -> None:
        if not self._current_file:
            return
        preview = not self.mode_btn.isChecked()   # checked = Edit
        if self._edit_kind == "pptx":
            self._show_pptx(self._current_file, mode_preview=preview)
        else:
            self._show_html(self._current_file, mode_preview=preview)

    def _show_excel(self, path: str) -> None:
        """View a spreadsheet as a real TABLE (openpyxl) — one tab per sheet — so
        Excel is viewable WITHOUT LibreOffice/PowerPoint. Bounded rows/cols keep
        large workbooks snappy. Falls back to the document (PDF/text) path if the
        workbook can't be read."""
        self.ext_btn.setVisible(True)
        try:
            from ..core.deps import ensure_module
            ensure_module("openpyxl", "openpyxl")
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
        except Exception:  # noqa: BLE001 - no openpyxl / unreadable → try PDF/text
            self._show_document(path)
            return
        MAX_ROWS, MAX_COLS = 2000, 100
        if self._xlsx_view is None:
            self._xlsx_view = QTabWidget()
            self.stack.addWidget(self._xlsx_view)
        tabs = self._xlsx_view
        while tabs.count():
            w = tabs.widget(0); tabs.removeTab(0); w.deleteLater()
        try:
            for ws in wb.worksheets:
                rows = list(ws.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True))
                ncols = max((len(r) for r in rows), default=0)
                table = QTableWidget(len(rows), ncols)
                table.setEditTriggers(QTableWidget.NoEditTriggers)
                table.horizontalHeader().setVisible(False)
                for r, row in enumerate(rows):
                    for c, val in enumerate(row):
                        if val is not None:
                            table.setItem(r, c, QTableWidgetItem(str(val)))
                table.resizeColumnsToContents()
                title = ws.title + (" (…)" if (ws.max_row or 0) > MAX_ROWS
                                    or (ws.max_column or 0) > MAX_COLS else "")
                tabs.addTab(table, title)
        finally:
            wb.close()
        if tabs.count() == 0:
            self._show_document(path)
            return
        self.stack.setCurrentWidget(tabs)

    def _show_document(self, path: str) -> None:
        """Office docs (ppt/pptx/doc/docx/xls/…) + PDF are RENDERED via QtPdf —
        LibreOffice converts them to PDF first. Falls back to text extraction
        when QtPdf/LibreOffice aren't available."""
        self.ext_btn.setVisible(True)
        suffix = Path(path).suffix.lower()
        if not _HAS_PDF:
            self._show_document_text(path)
            return
        if suffix == ".pdf":
            self._render_pdf(path)
            return
        # Cached conversion (per path+mtime) → render immediately.
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0
        cached = self._pdf_cache.get((path, mtime))
        if cached and os.path.exists(cached):
            self._render_pdf(cached)
            return
        # Convert to PDF off the UI thread (LibreOffice → MS Office COM). Only
        # skip to text when NEITHER is possible (no LibreOffice AND not Windows,
        # where COM may drive an installed Office). This is what lets a large
        # .pptx/.docx render via MS Office when LibreOffice isn't installed.
        from ..core.doc_extract import convert_to_pdf, find_soffice
        if not find_soffice() and os.name != "nt":
            self._show_document_text(path)
            return
        self.doc_view.setPlainText(tr("folder.converting"))
        self.stack.setCurrentWidget(self.doc_view)
        if self._pdf_tmp is None:
            import tempfile
            self._pdf_tmp = tempfile.mkdtemp(prefix="cowork_folder_pdf_")
        src, out_dir = path, self._pdf_tmp

        def job(worker):
            return {"src": src, "mtime": mtime, "pdf": convert_to_pdf(src, out_dir)}

        def done(result):
            if result.get("src") != self._current_file:
                return   # user moved on to another file
            pdf = result.get("pdf")
            if pdf:
                self._pdf_cache[(result["src"], result["mtime"])] = pdf
                self._render_pdf(pdf)
            else:
                self._show_document_text(src)

        worker = AgentWorker(job)
        worker.finished_ok.connect(done)
        worker.failed.connect(lambda _e, p=src: self._show_document_text(p))
        self._convert_worker = worker
        worker.start()

    def _ensure_pdf_view(self):
        if not _HAS_PDF:
            return None
        if self._pdf_view is None:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView
            self._pdf_doc = QPdfDocument(self)
            self._pdf_view = QPdfView(self)
            self._pdf_view.setDocument(self._pdf_doc)
            try:
                self._pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
                self._pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            except Exception:  # noqa: BLE001 - enum names vary slightly across versions
                pass
            self.stack.addWidget(self._pdf_view)
        return self._pdf_view

    def _render_pdf(self, pdf_path: str) -> None:
        view = self._ensure_pdf_view()
        if view is None:
            self._show_document_text(pdf_path)
            return
        self._pdf_doc.load(pdf_path)
        self.stack.setCurrentWidget(view)

    def _show_document_text(self, path: str) -> None:
        from ..core.doc_extract import extract_text
        try:
            text, note = extract_text(path)
        except Exception as exc:  # noqa: BLE001
            text, note = None, str(exc)
        body = text if text else tr("folder.doc_unreadable", note=note or "?")
        self.doc_view.setPlainText(body)
        self.stack.setCurrentWidget(self.doc_view)

    def _show_image(self, path: str) -> None:
        from PySide6.QtGui import QPixmap
        pix = QPixmap(path)
        if pix.isNull():
            self._show_binary(path)
            return
        self._img_label.setPixmap(pix)
        self._img_label.resize(pix.size())
        self.ext_btn.setVisible(True)
        self.stack.setCurrentWidget(self._img_scroll)

    def _show_binary(self, path: str) -> None:
        self._placeholder.setText(tr("folder.binary_file"))
        self.ext_btn.setVisible(True)
        self.stack.setCurrentWidget(self._placeholder)

    # ---- save / external -----------------------------------------------------
    def _save(self) -> None:
        if not self._current_file:
            return
        try:
            if self._edit_kind == "pptx":
                if not self._write_pptx(self.editor.toPlainText()):
                    return
            else:
                Path(self._current_file).write_text(self.editor.toPlainText(), encoding="utf-8")
            self.status_message.emit(tr("folder.saved", name=Path(self._current_file).name))
        except Exception as exc:  # noqa: BLE001
            self.status_message.emit(tr("folder.save_error", err=str(exc)))

    def _write_pptx(self, content: str, skip_confirm: bool = False) -> bool:
        """Write edited pptx text back into the deck. If the edit REPLACES any
        image, ask the user to confirm first (image edits are gated so a future
        image-processing model can't touch pictures without an explicit OK).
        ``skip_confirm`` is used when the image was already confirmed (e.g. just
        generated). Returns False if the user declined."""
        from ..core import pptx_edit
        if not skip_confirm and pptx_edit.image_change_requested(content):
            from PySide6.QtWidgets import QMessageBox
            ok = QMessageBox.question(self, tr("folder.ai_image_confirm_title"),
                                      tr("folder.ai_image_confirm"))
            if ok != QMessageBox.Yes:
                self.status_message.emit(tr("folder.ai_image_declined"))
                return False
        pptx_edit.apply_text_to_pptx(self._current_file, content)
        return True

    def _open_external(self) -> None:
        if self._current_file:
            from .osutil import open_location
            open_location(self._current_file)

    # ---- AI edit panel -------------------------------------------------------
    def _build_ai_panel(self) -> QWidget:
        self._ai_panel = QWidget()
        v = QVBoxLayout(self._ai_panel)
        v.setContentsMargins(6, 0, 0, 0)
        v.setSpacing(4)
        title_row = QHBoxLayout()
        self._ai_title = QLabel(tr("folder.ai_edit"))
        self._ai_title.setStyleSheet("font-weight:600;")
        title_row.addWidget(self._ai_title)
        title_row.addStretch(1)
        # Live status — stays visible so that, after doing other tasks and
        # coming back to this tab, the current "processing/done" state is shown.
        self._ai_status = QLabel("")
        self._ai_status.setObjectName("hint")
        title_row.addWidget(self._ai_status)
        v.addLayout(title_row)
        # A Cowork-style inline timeline (streaming bubbles + plan) — the AI edit
        # "processing" reads exactly like the Cowork chat.
        self.ai_chat = ChatView()
        v.addWidget(self.ai_chat, 1)

        # AI-edit's OWN model picker (independent of the Cowork/Settings agent) —
        # the chosen model runs the edit; "(auto)" uses the provider default.
        self._ai_models: list[str] = []
        model_row = QHBoxLayout()
        self._ai_model_lbl = QLabel(tr("folder.ai_model_label"))
        self._ai_model_lbl.setObjectName("hint")
        model_row.addWidget(self._ai_model_lbl)
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.addItem(tr("folder.ai_model_auto"), None)
        model_row.addWidget(self.ai_model_combo, 1)
        # Off/Auto/Manual routing toggle for AI-Edit (surface key "ai_edit").
        from .routing_toggle import RoutingToggle
        self.ai_routing_toggle = RoutingToggle(self.ctx, "ai_edit")
        model_row.addWidget(self.ai_routing_toggle)
        # Routing override for the next AI-edit run (set by _ai_apply_routing).
        self._ai_routed_provider = None
        self._ai_routed_model = None
        v.addLayout(model_row)

        row = QHBoxLayout()
        self.ai_input = QLineEdit()
        self.ai_input.setPlaceholderText(tr("folder.ai_placeholder"))
        self.ai_input.returnPressed.connect(self._ai_send)
        row.addWidget(self.ai_input, 1)
        self.ai_send_btn = QPushButton(tr("folder.ai_send"))
        self.ai_send_btn.setObjectName("primary")
        self.ai_send_btn.clicked.connect(self._ai_send)
        row.addWidget(self.ai_send_btn)
        v.addLayout(row)

        # Confirmation bar — the proposed edit is NOT applied/saved until the
        # user reviews the diff and clicks Apply (Discard keeps the original).
        self._ai_confirm_row = QWidget()
        cf = QHBoxLayout(self._ai_confirm_row)
        cf.setContentsMargins(0, 0, 0, 0)
        cf.addStretch(1)
        self._ai_discard_btn = QPushButton(tr("folder.ai_discard"))
        self._ai_discard_btn.clicked.connect(self._ai_discard)
        cf.addWidget(self._ai_discard_btn)
        self._ai_apply_btn = QPushButton(tr("folder.ai_apply"))
        self._ai_apply_btn.setObjectName("primary")
        self._ai_apply_btn.clicked.connect(self._ai_apply)
        cf.addWidget(self._ai_apply_btn)
        self._ai_confirm_row.setVisible(False)
        self._ai_pending = None          # proposed content awaiting confirmation
        v.addWidget(self._ai_confirm_row)
        return self._ai_panel

    def _reset_ai_conversation(self) -> None:
        """Clear the AI-edit chat so each file starts a clean conversation. A
        run in progress (editing the previous file) is left untouched — the
        reset applies the next time a file is opened while idle."""
        if getattr(self, "ai_chat", None) is None or self._ai_worker is not None:
            return
        self.ai_chat.clear()
        self.ai_btn.setText(tr("folder.ai_edit"))
        self._ai_pending = None
        self._ai_confirm_row.setVisible(False)
        if hasattr(self, "_ai_status"):
            self._ai_status.setText("")

    def _toggle_ai_panel(self) -> None:
        show = self.ai_btn.isChecked()
        self._ai_panel.setVisible(show)
        if show:
            self._content_split.setSizes([700, 320])
            self.ai_input.setFocus()
            # Populate the list on first open, AND re-fetch when the active
            # provider changed since it was last loaded — otherwise the picker
            # would keep another provider's models and a pick would resolve to
            # the wrong/default model at the new endpoint.
            if (self.ai_model_combo.count() <= 1
                    or self._ai_models_provider != self.ctx.config.active_provider):
                self.refresh_ai_models()
            # Reopening acknowledges any 'done' badge (unless still running).
            if self._ai_worker is None:
                self.ai_btn.setText(tr("folder.ai_edit"))
                self._ai_status.setText("")

    def refresh_ai_models(self) -> None:
        """Fetch the active provider's model list (background) into the AI-edit
        picker — independent of the Cowork/Settings agent. Called on first open
        and whenever the active provider changes, so the picked model always
        belongs to the provider that will actually run the edit."""
        name = self.ctx.config.active_provider
        setting_model = self.ctx.config.provider_conf(name).get("model", "")

        def job(worker):
            prov = self.ctx.build_provider_for(name)
            try:
                models = list(getattr(prov, "list_models", lambda: [])() or [])
            except Exception:  # noqa: BLE001
                models = []
            return {"models": models}

        def done(res):
            fetched = list(res.get("models", []))
            # Always offer the Settings-configured model as an explicit choice,
            # even when the provider can't list models (some gateways don't) —
            # so the picker is never just "(auto)" and the user can always pick a
            # concrete model instead of falling through to the default.
            self._ai_models = list(dict.fromkeys(
                ([setting_model] if setting_model else []) + [m for m in fetched if m]))
            self._ai_models_provider = name
            cur = self.ai_model_combo.currentData()
            self.ai_model_combo.blockSignals(True)
            self.ai_model_combo.clear()
            self.ai_model_combo.addItem(tr("folder.ai_model_auto"), None)
            for m in self._ai_models:
                self.ai_model_combo.addItem(m, m)
            # Keep the user's pick if it exists on THIS provider; otherwise reset
            # to "(auto)" (a stale pick must never be sent to the new endpoint).
            idx = self.ai_model_combo.findData(cur)
            self.ai_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
            self.ai_model_combo.blockSignals(False)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        self._ai_models_worker = w
        w.start()
        # Proactively discover image models across ALL providers so an image
        # suggestion is ready the moment the user asks for one.
        self._scan_all_image_models()

    def _scan_all_image_models(self, then_suggest: bool = False) -> None:
        """Background: find image-capable models across EVERY configured provider
        (not just the active one), so we can suggest one when an edit involves
        images even if the active provider has none. Caches
        ``self._all_image_models = [(provider_key, model)]``."""
        if self._img_scan_worker is not None:
            if then_suggest:
                self._pending_img_suggest = True
            return
        providers = dict(self.ctx.config.data.get("providers", {}))
        # Only providers that actually have an endpoint/key configured.
        candidates = [k for k, c in providers.items()
                      if (c.get("base_url") or c.get("api_key"))]

        def job(worker):
            from ..core import image_gen
            found = []
            for key in candidates:
                try:
                    prov = self.ctx.build_provider_for(key)
                    models = list(getattr(prov, "list_models", lambda: [])() or [])
                except Exception:  # noqa: BLE001 - a broken provider must not block the scan
                    models = []
                for m in models:
                    if image_gen.looks_like_image_model(m):
                        found.append((key, m))
            return {"found": found}

        def done(res):
            self._img_scan_worker = None
            self._all_image_models = list(res.get("found", []))
            if getattr(self, "_pending_img_suggest", False):
                self._pending_img_suggest = False
                self._suggest_cross_provider_image()

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda _e: setattr(self, "_img_scan_worker", None))
        self._img_scan_worker = w
        if then_suggest:
            self._pending_img_suggest = True
        w.start()

    def _ensure_editor_for_ai(self) -> bool:
        """Make the current file editable in the code editor (switching an HTML
        preview to edit, or loading a text file). Returns False when there's no
        file open or it isn't a text/code file."""
        path = self._current_file
        if not path or not os.path.isfile(path):
            return False
        suffix = Path(path).suffix.lower()
        if suffix in _HTML_SUFFIXES:
            self._show_html(path, mode_preview=False)   # → editor with the HTML source
            return True
        if suffix in _PPTX_SUFFIXES and _pptx_available():
            self._show_pptx(path, mode_preview=False)   # → editor with the deck's text
            return True
        if suffix in DOC_SUFFIXES or suffix in _IMAGE_SUFFIXES:
            return False
        if _is_probably_text(path):
            self._show_code(path)
            return True
        return False

    def _ai_provider(self):
        """Build a provider using the model chosen in AI-edit's own picker
        ('(auto)' → the active provider's default). NOT tied to the Cowork agent.

        An Auto/Manual routing override (set by :meth:`_ai_apply_routing` for the
        current run) takes precedence over the picker."""
        if getattr(self, "_ai_routed_provider", None) or getattr(self, "_ai_routed_model", None):
            provider = self._ai_routed_provider or self.ctx.config.active_provider
            return self.ctx.build_provider_for(provider, self._ai_routed_model or None)
        model = self.ai_model_combo.currentData() if hasattr(self, "ai_model_combo") else None
        return self.ctx.build_provider_for(self.ctx.config.active_provider, model or None)

    def _ai_apply_routing(self, instruction: str) -> None:
        """Auto Model Routing for the AI-Edit surface (always a CODING task).

        Off → no-op. Auto → silently pick the best coding model. Manual → ask
        first. Sets ``self._ai_routed_provider``/``_ai_routed_model`` for this
        run; :meth:`_ai_provider` honours them. Never raises."""
        self._ai_routed_provider = None
        self._ai_routed_model = None
        if not (instruction or "").strip():
            return
        try:
            from ..core.routing.models import TaskType
            mode = self.ctx.project_routing_mode("ai_edit")  # per-workspace mode
            if mode == "off":
                return
            service = self.ctx.routing()
            cur_provider = self.ctx.config.active_provider
            picked = self.ai_model_combo.currentData() if hasattr(self, "ai_model_combo") else None
            cur_model = picked or self.ctx.config.provider_conf(cur_provider).get("model", "")
            result = service.route(
                "ai_edit", instruction, cur_provider, cur_model,
                mode_override=mode, task_type=TaskType.CODING,
            )
            if not result.should_switch:
                return
            target = result.target()
            if target is None:
                return
            to_provider, to_model = target
            if mode == "manual":
                from .routing_toggle import confirm_switch
                timeout = float(self.ctx.config.routing.get("confirm_timeout_sec", 60) or 60)
                if not confirm_switch(self, result.decision, timeout):
                    return
            self._ai_routed_provider = to_provider
            self._ai_routed_model = to_model
            self.ai_chat.add_status(tr(
                "routing.switched_notice",
                model=to_model, task=result.task_type.value,
                gain=f"{result.decision.score_gain:.2f}"))
        except Exception:  # noqa: BLE001 — routing must never block an edit
            self._ai_routed_provider = None
            self._ai_routed_model = None

    def _ai_image_model(self):
        """Resolve the model+endpoint for image generation, searching ALL
        providers. Returns ``(model, base_url, api_key)`` — ``base_url``/``api_key``
        are ``None`` when the active provider is used; set when the image model
        lives on a DIFFERENT provider.

        Priority: the picked model if image-capable → an image model on the active
        provider → the first image model found on ANY other provider → FALL BACK
        to whatever model the user picked in AI-edit (so generation is still
        attempted with their choice); ``None`` only when nothing is picked
        ('(auto)' → provider default)."""
        from ..core import image_gen
        picked = self.ai_model_combo.currentData() if hasattr(self, "ai_model_combo") else None
        if picked and image_gen.looks_like_image_model(picked):
            return picked, None, None
        local = image_gen.suggest_image_model(self._ai_models)
        if local:
            return local, None, None
        for key, model in self._all_image_models:           # any other configured provider
            conf = self.ctx.config.provider_conf(key)
            return model, (conf.get("base_url") or None), (conf.get("api_key") or None)
        # No image-specific model found anywhere → use the user's PICKED model
        # (or provider default when '(auto)' is selected).
        return (picked or None), None, None

    _IMAGE_WORDS = ("image", "picture", "photo", "illustration", "icon", "logo", "diagram",
                    "ảnh", "hình", "minh họa", "biểu tượng", "画像", "イラスト")

    def _maybe_suggest_image_model(self, instruction: str) -> None:
        """If the request looks image-related, suggest a suitable image model
        BEFORE running — searching the active provider first, then ALL providers.
        The suggested model is what image generation will auto-use."""
        from ..core import image_gen
        low = (instruction or "").lower()
        if not any(w in low for w in self._IMAGE_WORDS):
            return
        picked = self.ai_model_combo.currentData()
        if picked and image_gen.looks_like_image_model(picked):
            return
        local = image_gen.suggest_image_model(self._ai_models)
        if local:
            self.ai_chat.add_status(tr("folder.ai_image_suggest", model=local))
            return
        # None on the active provider → look across ALL providers (cached, or scan
        # now and suggest when the scan returns).
        if self._all_image_models:
            self._suggest_cross_provider_image()
        elif self._img_scan_worker is not None:
            self._pending_img_suggest = True          # a scan is already running
        else:
            self._scan_all_image_models(then_suggest=True)

    def _suggest_cross_provider_image(self) -> None:
        """Post a suggestion listing image models found on OTHER providers. When
        none exist anywhere, fall back to telling the user their PICKED model
        will be used for image generation (or that there's nothing to use)."""
        from ..config import PROVIDER_LABELS
        if not self._all_image_models:
            picked = self.ai_model_combo.currentData()
            if picked:
                self.ai_chat.add_status(tr("folder.ai_image_use_selected", model=picked))
            else:
                self.ai_chat.add_status(tr("folder.ai_image_none"))
            return
        seen, lines = set(), []
        for key, model in self._all_image_models:
            tag = (key, model)
            if tag in seen:
                continue
            seen.add(tag)
            lines.append(f"• {model}  ({PROVIDER_LABELS.get(key, key)})")
            if len(lines) >= 5:
                break
        self.ai_chat.add_status(tr("folder.ai_image_suggest_all") + "\n" + "\n".join(lines))

    def _cowork_context(self) -> str:
        """The whole Cowork conversation (recent turns) as background context —
        so the AI edit is aware of what was discussed there."""
        cw = self._cowork
        msgs = getattr(cw, "messages", None) if cw is not None else None
        if not msgs:
            return ""
        lines = [f"{m['role']}: {str(m['content'])[:1000]}"
                 for m in msgs if m.get("role") in ("user", "assistant") and m.get("content")]
        return "\n".join(lines[-12:])

    def _ai_send(self) -> None:
        if not self._root or not os.path.isdir(self._root):
            self.ai_chat.add_error(tr("folder.ai_no_file"))
            return
        instruction = self.ai_input.text().strip()
        if not instruction:
            return
        self.ai_input.clear()
        self.ai_chat.add_user(instruction)
        # QUEUE: while a run is active OR a proposal is awaiting Apply/Discard,
        # hold the new instruction and run it when the pipeline goes idle. Lets
        # the user line up several edits without waiting for each to finish.
        if self._ai_worker is not None or self._ai_pending is not None:
            self._ai_queue.append(instruction)
            self.ai_chat.add_status(tr("folder.ai_queued", n=len(self._ai_queue)))
            self._update_queue_status()
            return
        self._ai_start(instruction)

    def _ai_start(self, instruction: str) -> None:
        """Begin processing one instruction (plan → edit). Assumes the pipeline
        is idle (the queue calls this when the previous run finishes)."""
        # If a text/code/HTML file is open (even in Preview), switch it into the
        # editor so AI can edit it. If nothing editable is open, that's fine —
        # the request may be to CREATE a new file (the model names it via FILE:).
        editable = self.stack.currentWidget() is self.editor
        if not editable:
            editable = self._ensure_editor_for_ai()
        self._maybe_suggest_image_model(instruction)
        # Auto Model Routing (may switch to the best coding model for this run).
        self._ai_apply_routing(instruction)
        has_file = editable and bool(self._current_file)
        self._ai_running_file = Path(self._current_file).name if has_file else tr("folder.ai_new_file")
        self._ai_set_busy(True)
        # Announce start on the status bar so it's visible even from another tab —
        # the edit keeps running in the background until it finishes.
        self.status_message.emit(tr("folder.ai_running", name=self._ai_running_file))
        # Two phases so the PLAN is shown INLINE *before* the edit runs.
        self._ai_ctx = {
            "filename": Path(self._current_file).name if has_file else "",
            "content": self.editor.toPlainText() if has_file else "",
            "convo": self._cowork_context(),
            "instruction": instruction,
            "provider": self._ai_provider(),
            "plan": "",
        }
        # Reset the token/cost tally for THIS prompt (plan + edit calls sum into it).
        self._ai_prompt_usage = {"in": 0, "out": 0, "cache": 0, "cost": 0.0}
        self._ai_run_plan()

    def _update_queue_status(self) -> None:
        """Reflect the number of queued instructions on the panel status line."""
        n = len(self._ai_queue)
        if n and hasattr(self, "_ai_status"):
            self._ai_status.setText("⏳ " + tr("folder.ai_status_running")
                                    + "  ·  " + tr("folder.ai_queue_count", n=n))
            self._ai_status.setStyleSheet("color:#0096C7;")

    def _ai_maybe_dequeue(self) -> None:
        """When the pipeline is fully idle, start the next queued instruction."""
        if self._ai_worker is not None or self._ai_pending is not None:
            return
        if not self._ai_queue:
            return
        nxt = self._ai_queue.pop(0)
        self._update_queue_status()
        self._ai_start(nxt)

    # ---- phase 1: plan -------------------------------------------------------
    # ---- token / cost accounting for AI-edit (like Cowork's per-message footer) --
    def _ai_add_usage(self, usage) -> None:
        """Add one model call's usage (plan or edit) to THIS prompt's tally."""
        if not isinstance(usage, dict):
            return
        tot = getattr(self, "_ai_prompt_usage", None)
        if tot is None:
            tot = self._ai_prompt_usage = {"in": 0, "out": 0, "cache": 0, "cost": 0.0}
        tot["in"] += int(usage.get("in", 0) or 0)
        tot["out"] += int(usage.get("out", 0) or 0)
        tot["cache"] += int(usage.get("cache", 0) or 0)
        tot["cost"] += float(usage.get("cost_usd", 0.0) or 0.0)

    def _ai_show_usage(self, bubble) -> None:
        """Footer under the AI-edit reply: ↓in ↑out ▤ctx $cost for the whole
        prompt (plan + edit), priced in the display currency — same as Cowork."""
        tot = getattr(self, "_ai_prompt_usage", None)
        if bubble is None or not tot or not (tot["in"] or tot["out"]):
            return
        from ..core import model_pricing as mp, usage_tracker as ut
        pricing = {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}
        line = (f"↓{mp.format_tokens(int(tot['in']))} ↑{mp.format_tokens(int(tot['out']))} "
                f"▤{mp.format_tokens(int(tot['in'] + tot['out'] + tot['cache']))} "
                f"{ut.format_cost(tot['cost'], pricing)}")
        try:
            bubble.add_usage(line)
        except Exception:  # noqa: BLE001 - a usage footer must never break the edit
            pass

    def _ai_run_plan(self) -> None:
        c = self._ai_ctx
        plan_bubble = self.ai_chat.add_plan(tr("folder.ai_planning"))
        self.ai_chat.scroll_to_bottom()

        def job(worker):
            from ..core import usage_tracker as ut
            from ..core.co4e_runner import _usage_delta
            provider = c["provider"]
            messages = [{"role": "system", "content":
                         "You are an AI file editor. Give a SHORT numbered plan (2-4 steps) for "
                         "the requested change. Plan ONLY — do NOT output any code."}]
            if c["convo"]:
                messages.append({"role": "system",
                                 "content": "Context from the user's Cowork conversation:\n" + c["convo"]})
            messages.append({"role": "user", "content":
                             f"File: {c['filename']}\n\nCurrent content:\n```\n{c['content']}\n```\n\n"
                             f"Request: {c['instruction']}"})
            ut.set_context("folder", c.get("filename") or "AI edit")   # Dashboard + usage
            ut.begin_accumulation(); base = ut.accumulated()
            try:
                r = provider.chat(messages, tools=None, cancel=worker.is_cancelled)
                txt = r.get("content", "") if isinstance(r, dict) else str(r)
                usage = _usage_delta(base, self.ctx.config)
            finally:
                ut.end_accumulation()
            return {"plan": provider.strip_think(txt) or "", "usage": usage}

        worker = AgentWorker(job)
        worker.finished_ok.connect(lambda res, b=plan_bubble: self._ai_plan_done(res, b))
        worker.failed.connect(lambda err, b=plan_bubble: self._ai_failed(err, b))
        self._ai_worker = worker
        worker.start()

    def _ai_plan_done(self, result, plan_bubble) -> None:
        self._ai_add_usage((result or {}).get("usage"))    # plan-step tokens
        plan = ((result or {}).get("plan") or "").strip()
        self._ai_ctx["plan"] = plan
        plan_bubble.set_plain(plan or tr("folder.ai_empty"))
        self.ai_chat.scroll_to_bottom()
        self._ai_run_edit()   # now execute the plan

    # ---- phase 2: execute (edit the file) ------------------------------------
    def _ai_run_edit(self) -> None:
        c = self._ai_ctx
        bubble = self.ai_chat.add_assistant(tr("folder.ai_edit"))
        self.ai_chat.scroll_to_bottom()

        pptx_note = ("\nThis is a PPTX shown as marker blocks '### Slide N / Box M', where N is the "
                     "1-based SLIDE NUMBER and M the box on that slide. When the user refers to a "
                     "slide by number (e.g. 'edit slide 3'), change ONLY the blocks under '### Slide "
                     "3' and leave every other slide's block exactly as-is. Each block has fields "
                     "type/pos/size/font/text (and image for pictures). To change TEXT COLOUR or "
                     "FONT edit the `font:` line — e.g. `font: name=Arial size=28 bold=1 "
                     "color=FF0000`. Keep all block markers and structure.") if self._edit_kind == "pptx" else ""

        # When creating a NEW deck (request mentions slides/pptx and we're not
        # already editing one), tell the model the marker format to emit so we can
        # build a real .pptx from it.
        _pptx_words = ("pptx", "powerpoint", "slide", "presentation", "deck",
                       "スライド", "プレゼン", "trình chiếu", "trinh chieu", "bài thuyết trình")
        wants_new_pptx = (self._edit_kind != "pptx"
                          and any(w in c["instruction"].lower() for w in _pptx_words))
        new_pptx_note = ("\nTo CREATE a PowerPoint, name it `FILE: <name>.pptx` and output the slides "
                         "as marker blocks — one block per shape:\n"
                         "### Slide 1 / Box 1\ntype: text\npos: 0.5, 0.4\nsize: 9.0, 1.2\n"
                         "font: name=Calibri size=32 bold=1 color=1F3864\ntext:\nTitle here\n\n"
                         "### Slide 1 / Box 2\ntype: text\npos: 0.5, 1.8\nsize: 9.0, 4.5\n"
                         "text:\nBullet one\nBullet two\n\n"
                         "Increment the Slide number for each new slide; pos/size are in inches; "
                         "font color is RRGGBB hex.") if wants_new_pptx else ""

        imggen_note = ""
        try:
            from ..core import image_gen
            if image_gen.is_configured(self.ctx.config):
                imggen_note = ("\nYou can also GENERATE an illustration image: add a line "
                               "`IMAGE_GEN: <describe the image> => <relative/path.png>`. Use a "
                               "generated image e.g. as a new picture, or (for pptx) set a picture "
                               "box's `image:` field to that same path to insert it.")
        except Exception:  # noqa: BLE001
            pass

        def job(worker):
            provider = c["provider"]
            open_note = (f"the currently-open file '{c['filename']}'" if c["filename"]
                         else "no file is open")
            messages = [{"role": "system", "content":
                         "You are an AI file editor inside an app. Following the plan, output the "
                         "COMPLETE file content in ONE fenced code block (```), and nothing after "
                         "it. Preserve everything you were not asked to change.\n"
                         "If the request is to CREATE A NEW file (or a different file than the one "
                         "open), put a line `FILE: <relative/path/name.ext>` (relative to the "
                         "current folder) immediately before the code block. Omit FILE to edit the "
                         f"open file. Right now, {open_note}." + pptx_note + new_pptx_note + imggen_note}]
            if c["convo"]:
                messages.append({"role": "system",
                                 "content": "Context from the user's Cowork conversation:\n" + c["convo"]})
            if c["plan"]:
                messages.append({"role": "system", "content": "Plan to follow:\n" + c["plan"]})
            cur = (f"File: {c['filename']}\n\nCurrent content:\n```\n{c['content']}\n```\n\n"
                   if c["filename"] else "No file is currently open.\n\n")
            messages.append({"role": "user", "content": cur + f"Request: {c['instruction']}"})

            def on_text(piece: str) -> None:
                worker.emit_event({"type": "text", "delta": piece})

            from ..core import usage_tracker as ut
            from ..core.co4e_runner import _usage_delta
            ut.set_context("folder", c.get("filename") or "AI edit")   # Dashboard + usage
            ut.begin_accumulation(); base = ut.accumulated()
            try:
                r = provider.chat(messages, tools=None, on_text=on_text, cancel=worker.is_cancelled)
                txt = r.get("content", "") if isinstance(r, dict) else str(r)
                usage = _usage_delta(base, self.ctx.config)
            finally:
                ut.end_accumulation()
            return {"text": provider.strip_think(txt) or "", "usage": usage}

        worker = AgentWorker(job)
        worker.event.connect(lambda ev, b=bubble: self._ai_stream(ev, b))
        worker.finished_ok.connect(lambda res, b=bubble: self._ai_done(res, b))
        worker.failed.connect(lambda err, b=bubble: self._ai_failed(err, b))
        self._ai_worker = worker
        worker.start()

    def _ai_stream(self, ev, bubble) -> None:
        if isinstance(ev, dict) and ev.get("type") == "text":
            bubble.append_delta(ev.get("delta", ""))
            self.ai_chat.scroll_to_bottom()

    def _ai_done(self, result, bubble) -> None:
        self._ai_worker = None
        self._ai_set_busy(False)
        self._ai_add_usage((result or {}).get("usage"))   # edit-step tokens
        self._ai_show_usage(bubble)                        # footer: prompt total (plan+edit)
        text = ((result or {}).get("text") or "").strip()
        target, new_content, summary, image_gens = _parse_ai_output(text)
        if new_content is None and not image_gens:
            bubble.set_markdown(text or tr("folder.ai_empty"))
            self.ai_chat.scroll_to_bottom()
            self._ai_flag_done()
            return
        # Decide edit-current vs create-new. A FILE: naming a path different from
        # the open file (or when nothing is open) → CREATE a new file.
        create = bool(target) and (not self._current_file
                                   or Path(target).name != Path(self._current_file).name)
        # PROPOSE the change — nothing is written until the user clicks Apply.
        self._ai_pending = {"content": new_content,
                            "target": target if create else None,
                            "image_gens": image_gens}
        hint = tr("folder.ai_review_hint")
        bubble.set_markdown((summary + "\n\n" if summary else "") + "_" + hint + "_")
        if new_content is not None:
            import difflib
            old = "" if create else self.editor.toPlainText()
            diff = "".join(difflib.unified_diff(
                old.splitlines(keepends=True), new_content.splitlines(keepends=True),
                fromfile=("(new file)" if create else "current"),
                tofile=(target if create else "proposed"))) or "(no textual difference)"
            title = tr("folder.ai_proposed_new", name=target) if create else tr("folder.ai_proposed")
            self.ai_chat.add_diff(title, diff)
        if image_gens:
            listing = "\n".join(f"• {p}  ({prompt[:60]})" for prompt, p in image_gens)
            self.ai_chat.add_status(tr("folder.ai_image_plan") + "\n" + listing)
        self._ai_confirm_row.setVisible(True)
        self.ai_chat.scroll_to_bottom()
        name = target if create else getattr(self, "_ai_running_file", "")
        self.status_message.emit(tr("folder.ai_proposed_status", name=name))
        self._ai_status.setText("● " + hint)
        self._ai_status.setStyleSheet("color:#c77d00;")

    def _ai_apply(self) -> None:
        """Confirmed by the user. If the edit GENERATES images, ask the image
        gate then generate them (off-thread) before finalising the file edit."""
        if not self._ai_pending:
            return
        p = self._ai_pending
        self._ai_pending = None
        self._ai_confirm_row.setVisible(False)
        if p.get("image_gens"):
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, tr("folder.ai_image_confirm_title"),
                                    tr("folder.ai_image_confirm_gen")) != QMessageBox.Yes:
                self.status_message.emit(tr("folder.ai_image_declined"))
                return
            self._ai_generate_then_finalize(p)
            return
        self._ai_finalize_apply(p)

    def _ai_generate_then_finalize(self, p: dict) -> None:
        imgs = p.get("image_gens") or []
        root = os.path.normpath(self._root)
        img_model, img_base, img_key = self._ai_image_model()   # may target another provider
        self._ai_set_busy(True)
        self.status_message.emit(tr("folder.ai_generating"))

        def job(worker):
            from ..core import image_gen
            results = []
            for prompt, rel in imgs:
                dest = rel if os.path.isabs(rel) else os.path.join(root, rel)
                dest = os.path.normpath(dest)
                if os.path.commonpath([dest, root]) != root:
                    results.append((rel, False, "path escapes the folder"))
                    continue
                try:
                    os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
                except OSError as exc:
                    results.append((rel, False, str(exc)))
                    continue
                ok, msg = image_gen.generate_image(self.ctx.config, prompt, dest,
                                                   model=img_model, base_url=img_base, api_key=img_key)
                results.append((dest, ok, msg))
            return {"results": results}

        worker = AgentWorker(job)
        worker.finished_ok.connect(lambda res, pp=p: self._ai_images_done(res, pp))
        worker.failed.connect(lambda err, pp=p: self._ai_images_done({"results": [], "err": err}, pp))
        self._ai_worker = worker
        worker.start()

    def _ai_images_done(self, res: dict, p: dict) -> None:
        self._ai_worker = None
        self._ai_set_busy(False)
        created = []
        for dest, ok, msg in res.get("results", []):
            if ok:
                created.append(dest)
                self.ai_chat.add_success("✓ " + tr("folder.ai_image_created", name=Path(dest).name))
            else:
                self.ai_chat.add_error(tr("folder.ai_image_failed", err=msg))
        # Now apply any text/file edit (pptx image: fields now point at real files).
        self._ai_finalize_apply(p, images_done=True)
        # If it was only image generation, open the first new image.
        if p.get("content") is None and not p.get("target") and created:
            self.open_file(created[0], reset=False)

    def _ai_finalize_apply(self, p: dict, images_done: bool = False) -> None:
        content = p.get("content")
        target = p.get("target")
        if content is None:
            self.ai_chat.scroll_to_bottom()
            self._ai_flag_done()
            self.status_message.emit(tr("folder.ai_done", name=getattr(self, "_ai_running_file", "")))
            return
        if target:
            dest = self._create_new_file(target, content)
            if dest is None:
                return
            self.ai_chat.add_success("✓ " + tr("folder.ai_created", name=Path(dest).name))
            self.status_message.emit(tr("folder.ai_created", name=Path(dest).name))
        else:
            self.editor.setPlainText(content)     # live update in the editor/preview
            self._ai_write_out(content, skip_image_confirm=images_done)
            self.ai_chat.add_success("✓ " + tr("folder.ai_applied"))
            self.status_message.emit(tr("folder.ai_done", name=getattr(self, "_ai_running_file", "")))
        self.ai_chat.scroll_to_bottom()
        self._ai_flag_done()

    def _create_new_file(self, target: str, content: str) -> Optional[str]:
        """Create ``target`` (relative to the folder root) with ``content`` and
        open it — like Cowork's save_file. Refuses paths escaping the root."""
        root = os.path.normpath(self._root)
        dest = target if os.path.isabs(target) else os.path.join(root, target)
        dest = os.path.normpath(dest)
        if os.path.commonpath([dest, root]) != root:
            self.status_message.emit(tr("folder.ai_error", err="path escapes the folder"))
            return None
        try:
            os.makedirs(os.path.dirname(dest) or root, exist_ok=True)
            if Path(dest).suffix.lower() in _PPTX_SUFFIXES and _pptx_available():
                # A .pptx is a binary package — build a real deck from the marker
                # text (writing text straight to .pptx would corrupt it).
                from ..core import pptx_edit
                pptx_edit.create_pptx_from_text(dest, content)
            else:
                Path(dest).write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - OS error or pptx build failure
            self.status_message.emit(tr("folder.save_error", err=str(exc)))
            return None
        self.open_file(dest, reset=False)   # show the new file; keep this AI chat
        return dest

    def _ai_discard(self) -> None:
        self._ai_pending = None
        self._ai_confirm_row.setVisible(False)
        self.ai_chat.add_status(tr("folder.ai_discarded"))
        self.ai_chat.scroll_to_bottom()
        self._ai_status.setText("")
        self._ai_maybe_dequeue()   # discarding resolves the gate → run the next queued edit

    def _ai_write_out(self, content: str, skip_image_confirm: bool = False) -> None:
        """Persist the confirmed content to disk AND refresh the preview.
        pptx text is written back into the deck (no PowerPoint window)."""
        if not self._current_file:
            return
        try:
            if self._edit_kind == "pptx":
                if not self._write_pptx(content, skip_confirm=skip_image_confirm):
                    return
            else:
                Path(self._current_file).write_text(content, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.status_message.emit(tr("folder.save_error", err=str(exc)))
            return
        # Refresh preview: HTML re-renders; pptx re-renders the slides; code stays
        # in the (now-saved) editor.
        suffix = Path(self._current_file).suffix.lower()
        if suffix in _HTML_SUFFIXES:
            self._show_html(self._current_file, mode_preview=True)
        elif suffix in _PPTX_SUFFIXES:
            self._show_pptx(self._current_file, mode_preview=True)

    def _ai_failed(self, err, bubble) -> None:
        self._ai_worker = None
        bubble.set_markdown(tr("folder.ai_error", err=err))
        self._ai_set_busy(False)
        self.status_message.emit(tr("folder.ai_error", err=err))
        self._ai_flag_done()

    def _ai_set_busy(self, busy: bool) -> None:
        self.ai_input.setEnabled(not busy)
        self.ai_send_btn.setEnabled(not busy)
        if busy:
            self._ai_status.setText("⏳ " + tr("folder.ai_status_running"))
            self._ai_status.setStyleSheet("color:#0096C7;")
            self.ai_btn.setText(tr("folder.ai_edit") + " ⏳")   # visible even when collapsed
        else:
            self._ai_status.setText("")
            self.ai_btn.setText(tr("folder.ai_edit"))

    def _ai_flag_done(self) -> None:
        """After a background run, show a 'done' badge on the panel/button so the
        user notices the result when they return to the tab; cleared on reopen.
        If more instructions are queued, start the next one instead."""
        if self._ai_worker is None and self._ai_pending is None and self._ai_queue:
            self._ai_maybe_dequeue()
            return
        self._ai_status.setText("✓ " + tr("folder.ai_status_done"))
        self._ai_status.setStyleSheet("color:#1f9d63;")
        if not self.ai_btn.isChecked() or self._ai_panel.isHidden():
            self.ai_btn.setText(tr("folder.ai_edit") + " ✓")

    # ---- i18n ----------------------------------------------------------------
    def _retranslate_mode_btn(self) -> None:
        # Button label shows the action it performs: in Preview → "Edit"; in Edit → "Preview".
        self.mode_btn.setText(tr("folder.edit") if not self.mode_btn.isChecked()
                              else tr("folder.preview"))

    def _retranslate(self) -> None:
        self.path_edit.setPlaceholderText(tr("folder.path_placeholder"))
        self._open_btn.setText(tr("folder.open_folder"))
        self.save_btn.setText(tr("folder.save"))
        self.ext_btn.setText(tr("folder.open_external"))
        self.ai_btn.setText(tr("folder.ai_edit"))
        self.ai_btn.setToolTip(tr("folder.ai_edit_tooltip"))
        self._ai_title.setText(tr("folder.ai_edit"))
        self.ai_input.setPlaceholderText(tr("folder.ai_placeholder"))
        self.ai_send_btn.setText(tr("folder.ai_send"))
        self._ai_model_lbl.setText(tr("folder.ai_model_label"))
        if self.ai_model_combo.count() >= 1 and self.ai_model_combo.itemData(0) is None:
            self.ai_model_combo.setItemText(0, tr("folder.ai_model_auto"))
        self._ai_apply_btn.setText(tr("folder.ai_apply"))
        self._ai_discard_btn.setText(tr("folder.ai_discard"))
        if not self._current_file:
            self._placeholder.setText(tr("folder.select_file"))
        self._retranslate_mode_btn()


_PPTX_READY = None   # cached: pptx-editing library available (after auto-install)


def _pptx_available() -> bool:
    """True when python-pptx is importable. If it's MISSING, auto-download &
    install it (via deps.ensure_module) so pptx editing 'just works' — cached so
    the (one-time) install is attempted only once."""
    global _PPTX_READY
    if _PPTX_READY is None:
        try:
            from ..core.deps import ensure_module
            _PPTX_READY = ensure_module("pptx", "python-pptx") is not None
        except Exception:  # noqa: BLE001
            _PPTX_READY = False
    return _PPTX_READY


def _split_code_block(text: str):
    """Split an AI reply into ``(file_content, summary)``. ``file_content`` is
    the first fenced code block (the edited file); ``summary`` is any prose
    before it. Returns ``(None, text)`` when there's no code block."""
    import re
    m = re.search(r"```[^\n]*\n(.*?)```", text or "", re.DOTALL)
    if not m:
        return None, (text or "")
    return m.group(1), (text[:m.start()].strip())


def _parse_ai_output(text: str):
    """Parse an AI edit reply into ``(target, content, summary, image_gens)``.
    ``FILE: <path>`` names a NEW file to create; ``IMAGE_GEN: <prompt> => <path>``
    lines request generated illustration images (relative paths)."""
    import re
    content, summary = _split_code_block(text)
    target = None
    m = re.search(r"(?mi)^\s*FILE:\s*(.+?)\s*$", text or "")
    if m:
        target = m.group(1).strip().strip("`\"'")
    image_gens = []
    for gm in re.finditer(r"(?mi)^\s*IMAGE_GEN:\s*(.+?)\s*=>\s*(\S+)\s*$", text or ""):
        image_gens.append((gm.group(1).strip(), gm.group(2).strip().strip("`\"'")))
    # Strip the directive lines out of the shown summary.
    summary = re.sub(r"(?mi)^\s*(FILE|IMAGE_GEN):\s*.+?$", "", summary).strip()
    return target, content, summary, image_gens


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[could not read file: {exc}]"


def _is_probably_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
    except OSError:
        return False
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        # Latin-ish text still edits fine via errors="replace"; only reject on
        # a hard binary signal (NUL above), so most source files pass.
        return True
