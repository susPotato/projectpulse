"""Structure (RAG) tab — knowledge graph of code / document structure.

Primary view is the D3 knowledge-graph (WebEngine) which gently auto-rotates
when idle and opens a node's storage folder on click. If WebEngine isn't
available (e.g. the standalone .exe), a native draggable QGraphicsView is the
in-app fallback. The graph auto-updates when the Code agent produces output,
and an Agent box on the right answers questions over the graph (Graph-RAG).
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGraphicsEllipseItem, QGraphicsLineItem,
    QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSplitter, QStackedWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

def _frozen_onefile() -> bool:
    """True only for a PyInstaller ONEFILE build. Onefile extracts itself to a
    temp dir (sys._MEIPASS under %TEMP%), where the QtWebEngine helper process
    can't run — creating a QWebEngineView hard-crashes the app (reported as
    "click the graph tab → app closes"). A ONEDIR build keeps _MEIPASS as the
    ``_internal`` folder right next to the exe, where WebEngine works fine, so
    it keeps the full embedded D3 view."""
    if not getattr(sys, "frozen", False):
        return False
    meipass = getattr(sys, "_MEIPASS", "")
    if not meipass:
        return False
    try:
        return Path(meipass).resolve().parent != Path(sys.executable).resolve().parent
    except OSError:  # can't tell → play safe: use the native fallback
        return True


try:  # WebEngine + WebChannel are optional PySide6 add-ons
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebChannel import QWebChannel
    _HAS_WEB = not _frozen_onefile()
except Exception:  # pragma: no cover
    _HAS_WEB = False

from ..core.structure_graph import EDGE_KIND_COLORS, NODE_KIND_COLORS
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .icons import collapse_right_icon, icon
from .osutil import open_folder, open_location
from .widgets import CollapseStrip

try:
    from PySide6.QtWidgets import QGraphicsItem  # noqa: F401 — ensure available
except Exception:
    pass


class _Bridge(QObject):
    """Exposed to the D3 page so a Shift+click on a node can open its
    storage folder/link (local path or URL — see osutil.open_location)."""

    @Slot(str)
    def openPath(self, path: str) -> None:  # noqa: N802 - JS-facing name
        if path:
            open_location(path)


class _Edge(QGraphicsLineItem):
    def __init__(self, a: "_Node", b: "_Node", type_: str = ""):
        super().__init__()
        self.a, self.b = a, b
        self.type = type_
        # Colour the edge by its RELATIONSHIP type (contains/defines/method/…),
        # so the graph shows what each connection MEANS — falling back to the
        # source node's tint for any untyped edge.
        color = QColor(EDGE_KIND_COLORS.get(type_, "")) if type_ else QColor()
        if not color.isValid():
            color = a.brush().color().lighter(130)
        self._color = color
        self.setPen(QPen(color, 1.4))
        self.setZValue(-1)
        # A small label naming the relationship, shown at the edge midpoint.
        self._label = None
        if type_:
            self._label = QGraphicsSimpleTextItem(type_, self)
            self._label.setBrush(QBrush(color.lighter(140)))
            f = QFont()
            f.setPointSize(7)
            self._label.setFont(f)
            self._label.setZValue(0)
        a.edges.append(self)
        b.edges.append(self)
        self.adjust()

    def adjust(self) -> None:
        pa, pb = self.a.scenePos(), self.b.scenePos()
        self.setLine(pa.x(), pa.y(), pb.x(), pb.y())
        if self._label is not None:
            br = self._label.boundingRect()
            self._label.setPos((pa.x() + pb.x()) / 2 - br.width() / 2,
                                (pa.y() + pb.y()) / 2 - br.height() / 2)


class _Node(QGraphicsEllipseItem):
    def __init__(self, data, radius: int):
        super().__init__(-radius, -radius, 2 * radius, 2 * radius)
        self.data = data
        self.edges = []
        color = QColor(NODE_KIND_COLORS.get(data.kind, "#888888"))
        self.setBrush(QBrush(color))
        self.setPen(QPen(color.darker(160), 1.5))
        self.setFlags(
            QGraphicsEllipseItem.ItemIsMovable
            | QGraphicsEllipseItem.ItemIsSelectable
            | QGraphicsEllipseItem.ItemSendsGeometryChanges
        )
        self.setZValue(1)
        label = QGraphicsSimpleTextItem(data.label, self)
        label.setBrush(QBrush(QColor("#e6e6e6")))
        label.setPos(radius + 3, -8)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsEllipseItem.ItemPositionHasChanged:
            for edge in self.edges:
                edge.adjust()
        return super().itemChange(change, value)


class _GraphView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setDragMode(QGraphicsView.NoDrag)
        self._panning = False
        self._pan_start = QPointF()

    def wheelEvent(self, e):  # noqa: N802
        self.scale(1.15 if e.angleDelta().y() > 0 else 1 / 1.15,
                   1.15 if e.angleDelta().y() > 0 else 1 / 1.15)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.itemAt(e.pos()) is None:
            self._panning = True
            self._pan_start = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._panning:
            delta = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        """Double-click or Ctrl+click on a node opens its storage folder."""
        item = self.itemAt(e.pos())
        if isinstance(item, _Node) and getattr(item.data, "path", ""):
            open_folder(item.data.path)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)


class StructureGraphView(QWidget):
    status_message = Signal(str)

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._worker: AgentWorker | None = None
        self._node_items: list[_Node] = []
        self._edge_items: list[_Edge] = []
        self._centroid = QPointF(0, 0)
        self._link = 120
        self._graph = None
        self._needs_scan = False
        self._scan_seq = 0   # only the latest scan's result is rendered (no stale overwrite)
        self._ask_worker: AgentWorker | None = None
        self._answer = ""
        self._detail_mode = "idle"   # "answer" | "node" | "idle" — what self.detail shows
        # TEMPORARY extracted file content for Q&A (real content, not just the
        # graph structure). Kept only while this tab is shown — cleared on leaving
        # the tab or switching project/root (see _clear_extracts / hideEvent).
        self._extract_cache: dict = {}          # path -> extracted text
        self._extract_dir = None                # temp folder for md/json dumps
        self._active_project_id = ""  # "" = free path; set = scan locked to that project's sandbox

        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(1500)
        self._rescan_timer.timeout.connect(self._scan)

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.path_edit = QLineEdit(str(ctx.config.cowork_output_dir()))
        self.path_edit.setPlaceholderText(tr("structure.path_placeholder"))
        self._pick_btn = QPushButton()
        self._pick_btn.setIcon(icon("folder"))
        self._pick_btn.setObjectName("primary")
        self._pick_btn.clicked.connect(self._pick)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self._scan_btn = QPushButton()
        self._scan_btn.setIcon(icon("search"))
        self._scan_btn.setObjectName("primary")
        self._scan_btn.clicked.connect(self._scan)
        bar.addWidget(self.path_edit, 1)
        bar.addWidget(self._pick_btn)
        bar.addWidget(self.project_combo)
        bar.addWidget(self._scan_btn)
        root.addLayout(bar)
        self._refresh_project_combo()

        # Toolbar: messages toggle + export
        bar2 = QHBoxLayout()
        bar2.addStretch(1)
        self._msgs_toggle_btn = QPushButton()
        self._msgs_toggle_btn.setIcon(icon("message"))
        self._msgs_toggle_btn.setToolTip(tr("structure.msgs_tooltip"))
        self._msgs_toggle_btn.clicked.connect(self._toggle_messages)
        bar2.addWidget(self._msgs_toggle_btn)

        self._export_btn = QPushButton()
        self._export_btn.setIcon(icon("upload"))
        self._export_btn.setObjectName("primary")
        self._export_btn.clicked.connect(self._export)
        bar2.addWidget(self._export_btn)

        root.addLayout(bar2)

        split = QSplitter(Qt.Horizontal)
        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QColor("#0D1F35"))  # deep ocean dark bg
        self.scene.selectionChanged.connect(self._on_selection)
        self.view = _GraphView(self.scene)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.view)
        # A "Messages" view: all conversation messages grouped BY DAY, shown as
        # JSON — a plain tree switched in via setCurrentWidget (never touches the
        # D3/WebEngine graph). Populated from the (project-scoped) history store.
        from PySide6.QtWidgets import QTreeWidget
        self._msgs_view = QTreeWidget()
        self._msgs_view.setHeaderHidden(True)
        self._msgs_view.itemClicked.connect(self._show_msg_json)
        self._stack.addWidget(self._msgs_view)
        self.web = None
        self._bridge = None
        self._channel = None

        # The legend + Show-relationship control live INSIDE the D3 graph
        # template now (assets/graph_template.html) — the graph column is just
        # the stack (native view / D3 web / messages).
        split.addWidget(self._stack)

        # Right-side agent panel (GraphRAG Q&A)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        # Agent panel header with collapse button
        ag_hdr = QHBoxLayout()
        self._ag_collapse = QPushButton()
        self._ag_collapse.setIcon(collapse_right_icon())
        self._ag_collapse.setFixedWidth(28)
        self._ag_collapse.clicked.connect(lambda: self._set_agent_collapsed(True))
        self._ag_label = QLabel()
        ag_hdr.addWidget(self._ag_collapse)
        ag_hdr.addWidget(self._ag_label, 1)
        rl.addLayout(ag_hdr)

        # Ask row
        ask_row = QHBoxLayout()
        self.ask_edit = QLineEdit()
        self.ask_edit.returnPressed.connect(self._ask)
        self._ask_btn = QPushButton()
        self._ask_btn.setIcon(icon("chat"))
        self._ask_btn.setObjectName("primary")
        self._ask_btn.clicked.connect(self._ask)
        ask_row.addWidget(self.ask_edit, 1)
        ask_row.addWidget(self._ask_btn)
        rl.addLayout(ask_row)

        # Detail browser
        self.detail = QTextBrowser()
        self.detail.setReadOnly(True)
        self.detail.setOpenLinks(False)
        self.detail.anchorClicked.connect(self._on_detail_link)
        rl.addWidget(self.detail, 1)

        self._agent_panel = right

        self._agent_strip = CollapseStrip(tr("structure.expand_agent_tooltip"), expand_dir="left")
        self._agent_strip.clicked.connect(lambda: self._set_agent_collapsed(False))
        self._agent_strip.setVisible(False)
        self._agent_pane = QWidget()
        apl = QHBoxLayout(self._agent_pane)
        apl.setContentsMargins(0, 0, 0, 0)
        apl.setSpacing(0)
        apl.addWidget(self._agent_strip)
        apl.addWidget(right, 1)

        self._split = split
        split.addWidget(self._agent_pane)
        split.setChildrenCollapsible(False)
        split.setSizes([840, 320])
        root.addWidget(split, 1)
        on_language_changed(self._retranslate)

    def _retranslate(self) -> None:
        self.path_edit.setPlaceholderText(tr("structure.path_placeholder"))
        self._pick_btn.setText(tr("structure.browse"))
        self._scan_btn.setText(tr("structure.scan"))
        self._export_btn.setText(tr("structure.export_png"))
        showing = self._stack.currentWidget() is getattr(self, "_msgs_view", None)
        self._msgs_toggle_btn.setText(tr("structure.graph_btn") if showing else tr("structure.msgs_btn"))
        self._ag_collapse.setToolTip(tr("structure.collapse_agent_tooltip"))
        self._ag_label.setText(tr("structure.agent_header"))
        self.ask_edit.setPlaceholderText(tr("structure.ask_placeholder"))
        self._ask_btn.setText(tr("structure.ask"))
        if self._detail_mode == "idle":
            self.detail.setPlaceholderText(tr("structure.detail_placeholder"))
        self._agent_strip.setToolTip(tr("structure.expand_agent_tooltip"))
        self.project_combo.setToolTip(tr("structure.project_tooltip"))
        self._refresh_project_combo()

    # ---- project sandbox lock -----------------------------------------
    def _refresh_project_combo(self) -> None:
        from ..core.projects import list_projects

        keep = self._active_project_id
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem(tr("structure.project_none"), "")
        row_to_select = 0
        for i, p in enumerate(list_projects(), start=1):
            self.project_combo.addItem(p.name, p.project_id)
            if p.project_id == keep:
                row_to_select = i
        self.project_combo.setCurrentIndex(row_to_select)
        self.project_combo.blockSignals(False)

    def set_project(self, project_id: str) -> None:
        pid = project_id or ""
        self._refresh_project_combo()
        target = self.project_combo.findData(pid)
        if target < 0:
            target = 0
        if self.project_combo.currentIndex() == target:
            self._on_project_changed(target)
        else:
            self.project_combo.setCurrentIndex(target)

    def _on_project_changed(self, _idx: int) -> None:
        from ..core.projects import load_project

        pid = self.project_combo.currentData() or ""
        project_changed = pid != self._active_project_id
        if project_changed:
            self._clear_extracts()      # different workspace → drop temp extraction
        self._active_project_id = pid
        locked = bool(pid)
        self.path_edit.setReadOnly(locked)
        # Also disable the folder-pick button — otherwise the scan path is only
        # "locked" against typing, but the picker could still repoint it outside
        # the selected project's sandbox, breaking GraphRAG scope isolation.
        self._pick_btn.setEnabled(not locked)
        if locked:
            project = load_project(pid)
            if project is not None:
                self.path_edit.setText(str(project.workspace_dir()))
        if project_changed:
            self._needs_scan = True
            if self.web is not None:
                self._needs_scan = False
                self._scan()

    # ---- helpers -----------------------------------------------------
    def _pick(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("structure.pick_folder_title"), self.path_edit.text())
        if chosen:
            self.path_edit.setText(chosen)

    def schedule_rescan(self, path: str = "") -> None:
        if self._graph is None:
            self._needs_scan = True
            return
        self._rescan_timer.start()

    # ---- Messages (by day, as JSON) --------------------------------------
    def _toggle_messages(self) -> None:
        """Switch between the knowledge graph and the Messages-by-day view."""
        showing = self._stack.currentWidget() is self._msgs_view
        if showing:
            self._stack.setCurrentWidget(self.web if self.web is not None else self.view)
        else:
            self._reload_messages()
            self._stack.setCurrentWidget(self._msgs_view)
        self._msgs_toggle_btn.setText(
            tr("structure.graph_btn") if not showing else tr("structure.msgs_btn"))

    def _reload_messages(self) -> None:
        """Build the tree: day → conversation. Click a conversation to see its
        messages as JSON. Scoped to the current project (its history folder)."""
        from collections import OrderedDict

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItem

        from ..core.history import list_conversations
        self._msgs_view.clear()
        pid = self._active_project_id or ""
        by_day: "OrderedDict[str, list]" = OrderedDict()
        try:
            convs = list_conversations(self.ctx.config.history_dir())
        except Exception:  # noqa: BLE001
            convs = []
        for conv in convs:
            if pid and conv.get("project_id", "default") != pid:
                continue
            day = (conv.get("created") or "")[:10] or "—"
            by_day.setdefault(day, []).append(conv)
        if not by_day:
            self._msgs_view.addTopLevelItem(QTreeWidgetItem([tr("structure.msgs_none")]))
            return
        for day in sorted(by_day, reverse=True):
            convs_d = by_day[day]
            day_item = QTreeWidgetItem([f"{day}   ({len(convs_d)})"])
            for conv in convs_d:
                it = QTreeWidgetItem([conv.get("title", "(untitled)")])
                it.setData(0, Qt.UserRole, str(conv.get("path", "")))
                day_item.addChild(it)
            self._msgs_view.addTopLevelItem(day_item)
            day_item.setExpanded(True)

    def _show_msg_json(self, item, _col: int = 0) -> None:
        import html
        import json

        from PySide6.QtCore import Qt

        from ..core.history import load_conversation
        path = item.data(0, Qt.UserRole)
        if not path:
            return
        try:
            conv = load_conversation(path)
            payload = {"title": conv.get("title", ""), "created": conv.get("created", ""),
                       "kind": conv.get("kind", ""), "project_id": conv.get("project_id", ""),
                       "messages": conv.get("messages", [])}
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            text = f"(could not read: {exc})"
        self.detail.setHtml(
            f'<pre style="white-space:pre-wrap; font-family:Consolas,monospace; '
            f'font-size:12px;">{html.escape(text)}</pre>')

    def _ensure_web(self) -> None:
        if self.web is not None or not _HAS_WEB:
            return
        self.web = QWebEngineView()
        self._bridge = _Bridge()
        self._channel = QWebChannel()
        self._channel.registerObject("py", self._bridge)
        self.web.page().setWebChannel(self._channel)
        self._stack.addWidget(self.web)
        self._stack.setCurrentWidget(self.web)
        if self._graph is not None:
            self._render_d3()

    def auto_scan_and_fit(self) -> None:
        self._ensure_web()
        if not self.path_edit.text().strip():
            return
        if getattr(self, "_worker", None) is not None and self._worker.isRunning():
            self._fit()
            self._preserve_answer()
            return
        if self._graph is not None and not self._needs_scan:
            self._fit()
            self._preserve_answer()
            return
        self._needs_scan = False
        self._scan()

    # ---- scan --------------------------------------------------------
    def _scan(self) -> None:
        path = self.path_edit.text().strip() or str(Path.cwd())
        mode = "files"  # default: scan all files (filter removed)
        use_cmem = bool(self.ctx.config.codebase_memory.get("enabled"))
        cmem_bin = self.ctx.config.codebase_memory.get("binary_path", "")
        st = self.ctx.config.structure
        max_nodes = int(st.get("max_nodes", 500) or 0)
        max_edges = int(st.get("max_edges", 500) or 0)
        self._scan_seq += 1
        seq = self._scan_seq
        self.status_message.emit(tr("structure.scanning"))

        def job(worker: AgentWorker):
            from ..core.structure_graph import (
                build_from_codebase_memory, build_from_directory, force_layout,
            )
            if use_cmem:
                from ..core.codebase_memory import CodebaseMemory
                mem = CodebaseMemory(cmem_bin)
                graph = (build_from_codebase_memory(mem, path, mode, max_nodes, max_edges)
                         if mem.available else build_from_directory(path, mode, max_nodes, max_edges))
            else:
                graph = build_from_directory(path, mode, max_nodes, max_edges)
            pos = force_layout(graph)
            return {"graph": graph, "pos": pos, "seq": seq}

        w = AgentWorker(job)
        w.finished_ok.connect(self._render)
        w.failed.connect(lambda e: self.status_message.emit(tr("structure.scan_error", err=e)))
        self._worker = w
        w.start()

    def _render(self, result: dict) -> None:
        if result.get("seq") is not None and result["seq"] != self._scan_seq:
            return
        graph = result.get("graph")
        pos = result.get("pos", {})
        if graph is None:
            return
        self._graph = graph

        self.scene.clear()
        self.scene.setBackgroundBrush(QColor("#0D1F35"))  # restore deep ocean bg after clear
        self._node_items = []
        self._edge_items = []
        degree = {n.id: 0 for n in graph.nodes}
        for e in graph.edges:
            if e.source in degree:
                degree[e.source] += 1
            if e.target in degree:
                degree[e.target] += 1
        items = {}
        sx = sy = 0.0
        for node in graph.nodes:
            radius = int(8 + min(20, 2.2 * math.sqrt(degree.get(node.id, 0))))
            item = _Node(node, radius)
            x, y = pos.get(node.id, (0, 0))
            item.setPos(x, y)
            self.scene.addItem(item)
            items[node.id] = item
            self._node_items.append(item)
            sx += x
            sy += y
        for edge in graph.edges:
            a, b = items.get(edge.source), items.get(edge.target)
            if a and b:
                e = _Edge(a, b, getattr(edge, "type", ""))
                self.scene.addItem(e)
                self._edge_items.append(e)
        n = max(1, len(self._node_items))
        self._centroid = QPointF(sx / n, sy / n)
        self._fit()

        if self.web is not None:
            self._render_d3()

        note = tr("structure.truncated_note") if getattr(graph, "truncated", False) else ""
        self.status_message.emit(tr(
            "structure.graph_summary", nodes=len(graph.nodes), edges=len(graph.edges), note=note))
        self._preserve_answer()

    def _render_d3(self) -> None:
        if self.web is None or self._graph is None:
            return
        from ..core.d3_graph import build_html
        try:
            self.web.setHtml(build_html(self._graph), QUrl("https://cowork.local/"))
        except Exception as exc:
            self.status_message.emit(f"D3 view error: {exc}")

    # ---- native interactions ----------------------------------------
    def _on_selection(self) -> None:
        for item in self.scene.selectedItems():
            if isinstance(item, _Node):
                d = item.data
                self.detail.setPlainText(f"[{d.kind.upper()}] {d.label}\n\n{d.detail}")
                self._detail_mode = "node"
                return

    def _preserve_answer(self) -> None:
        if self._detail_mode == "answer" and self._answer.strip():
            self._render_answer()

    def _set_agent_collapsed(self, collapsed: bool) -> None:
        strip_w = CollapseStrip.WIDTH + 2
        self._agent_panel.setVisible(not collapsed)
        self._agent_strip.setVisible(collapsed)
        if collapsed:
            self._agent_pane.setMaximumWidth(strip_w)
            sizes = self._split.sizes()
            if len(sizes) == 2:
                self._split.setSizes([max(1, sum(sizes) - strip_w), strip_w])
        else:
            self._agent_pane.setMaximumWidth(16777215)
            self._split.setSizes([840, 320])

    def _fit(self) -> None:
        if self.web is not None and self._stack.currentWidget() is self.web:
            self.web.page().runJavaScript("window.fitGraph && window.fitGraph();")
            return
        rect = self.scene.itemsBoundingRect()
        if not rect.isNull():
            self.view.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("structure.export_title"), "structure-graph.png", "PNG (*.png)")
        if not path:
            return
        showing_d3 = (self.web is not None and self._stack.currentWidget() is self.web)
        if showing_d3:
            self._export_d3_png(path)
        else:
            self._export_widget_grab(path)

    def _export_d3_png(self, path: str) -> None:
        def on_result(data_url) -> None:
            if not isinstance(data_url, str) or "," not in data_url:
                self._export_widget_grab(path)
                return
            import base64
            try:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(data_url.split(",", 1)[1]))
                self.status_message.emit(tr("structure.export_done", path=path))
            except (OSError, ValueError) as exc:
                self.status_message.emit(tr("structure.export_failed", err=str(exc)))
        self.web.page().runJavaScript("window.exportPng ? window.exportPng() : ''", on_result)

    def _export_widget_grab(self, path: str) -> None:
        ok = self._stack.currentWidget().grab().save(path, "PNG")
        if ok:
            self.status_message.emit(tr("structure.export_done", path=path))
        else:
            self.status_message.emit(tr("structure.export_failed", err="grab() returned no image"))

    # ---- agent Q&A over the graph -----------------------------------
    @staticmethod
    def _graph_context(graph) -> str:
        from collections import defaultdict
        by_kind = defaultdict(list)
        for n in graph.nodes:
            by_kind[n.kind].append(n.label)
        lines = []
        for kind in ("file", "class", "function", "method", "module", "section"):
            items = by_kind.get(kind, [])
            if items:
                lines.append(f"{kind} ({len(items)}): " + ", ".join(items[:60]))
        id2label = {n.id: n.label for n in graph.nodes}
        rels = [f"{id2label.get(e.source, e.source)} -{e.type}-> {id2label.get(e.target, e.target)}"
                for e in graph.edges[:140]]
        if rels:
            lines.append("Relationships (sample):\n" + "\n".join(rels))
        return "\n".join(lines)[:7000]

    def _matched_sources(self, text: str):
        if self._graph is None or not text:
            return []
        found: dict[str, tuple[str, str, str]] = {}
        for n in self._graph.nodes:
            if not n.path:
                continue
            label = n.label.rstrip("()")
            if len(label) < 3:
                continue
            if n.path not in found and re.search(rf"\b{re.escape(label)}\b", text):
                found[n.path] = (n.kind, n.label, n.detail or n.path)
        return sorted(found.items(), key=lambda kv: kv[1][1].lower())[:12]

    def _linkify_files(self, text: str, sources) -> str:
        """Turn file/entity NAMES mentioned in the answer into clickable links that
        open the file — so the user can click a name in the answer to view it."""
        for path, (kind, label, rel) in sources:
            href = QUrl.fromLocalFile(path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
            tokens = []
            base = Path(path).name
            if base and len(base) >= 3:
                tokens.append(base)
            lab = (label or "").rstrip("()").strip()
            if lab and lab != base and len(lab) >= 3:
                tokens.append(lab)
            for tok in tokens:
                esc = re.escape(tok)
                # `tok` (code span) → keep the code style but make it a link
                text = re.sub(rf"`{esc}`", f"[`{tok}`]({href})", text)
                # bare tok, not already inside a link / path / code span
                text = re.sub(rf"(?<![\w`/\\.\]\)]){esc}(?![\w`\]\(])", f"[{tok}]({href})", text)
        return text

    def _render_answer(self) -> None:
        text = self._answer
        sources = self._matched_sources(text)
        if sources:
            # 1) Make the file/entity names IN THE ANSWER clickable (open on click).
            text = self._linkify_files(text, sources)
            # 2) Append a clickable "Related sources" section listing each file.
            lines = [text, "", "---", f"**{tr('structure.related_sources')}**"]
            for path, (kind, label, rel) in sources:
                href = QUrl.fromLocalFile(path).toString(QUrl.ComponentFormattingOption.FullyEncoded)
                # kind badge for context (file/function/section/json_key)
                kind_badge = f" [{kind.upper()}]" if kind not in ("file",) else ""
                lines.append(f"- **[{label}⧉]({href})**{kind_badge} — `{rel}`")
            text = "\n".join(lines)
        self.detail.setMarkdown(text)

    def _on_detail_link(self, url: QUrl) -> None:
        if url.isLocalFile():
            p = url.toLocalFile()
            # Open the FILE itself for viewing (fall back to its folder for a dir).
            if Path(p).is_file():
                open_location(p)
            else:
                open_folder(p)

    def _ask(self) -> None:
        question = self.ask_edit.text().strip()
        if not question:
            return
        from ..core.skills import parse_skill_command
        skill_prefix, question, info = parse_skill_command(question)
        if info is not None:
            self.detail.setMarkdown(info)
            self._detail_mode = "answer"
            self.ask_edit.clear()
            return
        if self._graph is None:
            self.status_message.emit(tr("structure.scan_first"))
            return
        context = self._graph_context(self._graph)
        # Real file CONTENT to answer from (extracted temporarily in the worker):
        file_paths = self._candidate_file_paths()
        extract_cache = dict(self._extract_cache)
        extract_dir = str(self._extract_tmp_dir())
        self._answer = ""
        self._detail_mode = "answer"
        self.detail.setPlainText("…")
        self.ask_edit.clear()

        active_project_id = self._active_project_id

        # Collect selected node context for auto-filtering
        selected_nodes = [item.data for item in self.scene.selectedItems() if isinstance(item, _Node)]
        selected_context = ""
        if selected_nodes:
            node_lines = []
            for nd in selected_nodes:
                node_lines.append(f"- {nd.label} (kind: {nd.kind}, path: {getattr(nd, 'path', '')})")
                if nd.detail:
                    node_lines.append(f"  detail: {nd.detail}")
            # Also gather connected nodes
            connected_ids = set()
            for nd in selected_nodes:
                for edge in self._graph.edges:
                    if edge.source == nd.id:
                        connected_ids.add(edge.target)
                    elif edge.target == nd.id:
                        connected_ids.add(edge.source)
            connected_nodes = [n for n in self._graph.nodes if n.id in connected_ids]
            if connected_nodes:
                node_lines.append("\nConnected nodes:")
                for cn in connected_nodes:
                    node_lines.append(f"- {cn.label} (kind: {cn.kind})")
            selected_context = "\n".join(node_lines)

        def job(worker: AgentWorker):
            provider = self.ctx.build_active_provider()
            system = ("You answer questions about a code/document knowledge graph. Use the provided "
                      "graph context AND the extracted file contents to retrieve, synthesize and "
                      "explain the answer. Be concise. Answer ONLY from what is provided (graph "
                      "context + extracted contents) — never invent files, functions, or facts that "
                      "aren't in it.\n\n"
                      "EACH answer MUST include source citations so the user can verify where "
                      "information came from. For every factual claim, file reference, or code "
                      "element you mention, add a citation using this format:\n\n"
                      "  [source: filename.ext, line/section: XXX]\n\n"
                      "Rules for citations:\n"
                      "  1. Cite the EXACT file path from the graph context (use the path field).\n"
                      "  2. For Python files: cite the function/class name and approximate line "
                      "     if available, or the module name.\n"
                      "  3. For document files (.md, .txt): cite the section heading.\n"
                      "  4. For JSON files: cite the key path (e.g. settings > database > host).\n"
                      "  5. Place citations inline after the relevant sentence or fact.\n"
                      "  6. At the end of your answer, add a '---' separator followed by a "
                      "     numbered **Sources cited:** section listing each unique source with "
                      "     its full path so the user can click to open it.\n\n"
                      "Example citation format in text:\n"
                      "  The `process_data()` function handles CSV parsing "
                      "[source: src/utils/parser.py, function: process_data].\n\n"
                      "Example end-of-answer source list:\n"
                      "  ---\n"
                      "  **Sources cited:**\n"
                      "  1. `src/utils/parser.py` — process_data function\n"
                      "  2. `docs/api.md` — Section: Authentication\n")
            if skill_prefix:
                system += "\n\nFollow this skill:\n" + skill_prefix
            if active_project_id:
                from ..core.projects import load_project, project_context_text
                proj_ctx = project_context_text(load_project(active_project_id))
                if proj_ctx:
                    system += "\n\n" + proj_ctx
            user_content = f"Graph context:\n{context}"
            if selected_context:
                user_content += f"\n\nSelected node(s) context (focus your answer on these):\n{selected_context}"
            # Auto-extract the actual file contents (temporary) so the answer is
            # synthesized from real content, not just the graph structure.
            content_block, new_cache = _extract_file_contents(file_paths, extract_cache, extract_dir)
            if content_block:
                user_content += ("\n\nExtracted file contents (read these to answer about file "
                                 "details/data; cite the file path):\n" + content_block)
            user_content += f"\n\nQuestion: {question}"
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ]
            from ..core import agent_roles, audit_log
            ok = True
            try:
                provider.chat(messages, on_text=lambda t: worker.emit_event({"type": "text", "delta": t}),
                              cancel=worker.is_cancelled)
            except Exception:
                ok = False
                raise
            finally:
                audit_log.record("tool_call", "graphrag_ask", ok, question[:500],
                                 agent_role=agent_roles.KNOWLEDGE)
            return {"extracted": new_cache}

        w = AgentWorker(job)
        w.event.connect(self._on_ask_event)
        w.finished_ok.connect(self._on_ask_done)
        w.failed.connect(lambda e: self.detail.setPlainText(f"Error: {e}"))
        self._ask_worker = w
        w.start()

    def _on_ask_event(self, ev: dict) -> None:
        if ev.get("type") == "text":
            if self._answer == "":
                self.detail.clear()
            self._answer += ev.get("delta", "")
            self.detail.setPlainText(self._answer)

    def _on_ask_done(self, result: dict) -> None:
        # Keep the (temporary) extracted content so repeated questions reuse it
        # without re-extracting — dropped when leaving the tab (_clear_extracts).
        if isinstance(result, dict):
            self._extract_cache.update(result.get("extracted", {}) or {})
        self._render_answer()

    # ---- temporary file-content extraction for Q&A ------------------------
    def _candidate_file_paths(self) -> list:
        """File paths to read for a question: the SELECTED file nodes if any, else
        every file node in the graph (capped downstream)."""
        from pathlib import Path as _P
        if self._graph is None:
            return []
        sel = [item.data for item in self.scene.selectedItems() if isinstance(item, _Node)]
        nodes = sel or list(self._graph.nodes)
        out, seen = [], set()
        for nd in nodes:
            p = (getattr(nd, "path", "") or "").strip()
            if p and p not in seen and _P(p).is_file():
                seen.add(p)
                out.append(p)
        return out

    def _extract_tmp_dir(self):
        from pathlib import Path as _P
        if self._extract_dir is None:
            import tempfile
            from ..config import CONFIG_DIR
            base = CONFIG_DIR / "tmp" / "graphrag_extract"
            base.mkdir(parents=True, exist_ok=True)
            self._extract_dir = _P(tempfile.mkdtemp(dir=str(base)))
        return self._extract_dir

    def _clear_extracts(self) -> None:
        """Discard the temporary extracted content (on leaving the tab / switching
        project). The extraction is a scratch aid, never persisted."""
        self._extract_cache = {}
        d, self._extract_dir = self._extract_dir, None
        if d is not None:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def hideEvent(self, e):  # noqa: N802
        # Leaving the GraphRAG tab → drop the temporary extracted info.
        self._clear_extracts()
        super().hideEvent(e)





# --------------------------------------------------------------------------
# Temporary file-content extraction for Graph-RAG Q&A (runs in the ask worker)
# --------------------------------------------------------------------------
def _pdf_to_markdown(pdf_path, out_dir) -> str | None:
    """Convert a PDF to Markdown with opendataloader-pdf when available (richer
    structure than a plain text dump). Best-effort — returns None if the package
    isn't installed or the call fails, so the caller falls back to doc_extract."""
    from pathlib import Path as _P
    try:
        import opendataloader_pdf  # optional; auto-installed elsewhere if present
    except Exception:  # noqa: BLE001
        try:
            from ..core.deps import ensure_module
            if ensure_module("opendataloader_pdf", "opendataloader-pdf") is None:
                return None
            import opendataloader_pdf  # noqa: F811
        except Exception:  # noqa: BLE001
            return None
    out = _P(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for call in (
        lambda: opendataloader_pdf.convert(input_path=[str(pdf_path)], output_dir=str(out),
                                           generate_markdown=True),
        lambda: opendataloader_pdf.convert(input_path=str(pdf_path), output_dir=str(out)),
        lambda: opendataloader_pdf.convert(str(pdf_path), str(out)),
    ):
        try:
            call()
            break
        except TypeError:
            continue
        except Exception:  # noqa: BLE001
            return None
    mds = list(out.rglob(_P(pdf_path).stem + "*.md")) or list(out.rglob("*.md"))
    for md in mds:
        try:
            return md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def _extract_file_contents(paths, cache: dict, tmp_dir,
                           max_files: int = 15, max_total: int = 120_000):
    """Read the ACTUAL content of ``paths`` (PDF→markdown via opendataloader when
    available, else doc_extract for office/pdf/text). Returns ``(block, cache)``
    — ``block`` is the concatenated content for the prompt (bounded), ``cache``
    maps path→text for reuse. Never raises."""
    from pathlib import Path as _P
    from ..core import doc_extract
    cache = dict(cache or {})
    parts, total = [], 0
    for p in paths[:max_files]:
        if total >= max_total:
            break
        text = cache.get(p)
        if text is None:
            try:
                if _P(p).suffix.lower() == ".pdf":
                    text = _pdf_to_markdown(p, tmp_dir)
                    if not text:
                        text, _n = doc_extract.extract_text(p)
                else:
                    text, _n = doc_extract.extract_text(p)
            except Exception:  # noqa: BLE001
                text = ""
            cache[p] = text or ""
        text = cache.get(p) or ""
        if not text:
            continue
        chunk = text[: max(0, max_total - total)]
        total += len(chunk)
        parts.append(f'--- {_P(p).name} ({p}) ---\n{chunk}')
    return ("\n\n".join(parts), cache)
