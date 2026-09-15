"""Co4E node canvas — a QGraphicsView node-graph editor.

Renders workflow nodes as draggable cards and edges as rounded orthogonal
("elbow with rounded corners") arrows. Supports: drag to move (positions
persist), click to select (→ right config panel), drag-to-connect from a node's
output port (bottom) to another node, a context-menu "connect" mode, "add step
below" (auto-connected child), delete, zoom (Ctrl+wheel / buttons), auto-fit,
and drops from the sidebar palette (agent/skill/parallel/whole-flow) via the
``application/x-co4e-step`` mime type.

Kept UI-only; the graph model lives in ``core/co4e.py``.
"""
from __future__ import annotations

import copy
import json
from typing import Dict, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene,
    QGraphicsView, QMenu,
)

from ..core.co4e import (
    STEP_DONE, STEP_ERROR, STEP_PLANNED, STEP_RUNNING, Edge, Node, Step,
    compute_waves, new_edge_id, new_node_id,
)

CO4E_MIME = "application/x-co4e-step"

_STATUS_COLOR = {
    "idle": "#5C8DB8", STEP_RUNNING: "#48CAE4", STEP_DONE: "#48D9A0",
    STEP_ERROR: "#E5484D", STEP_PLANNED: "#9B8FF7", "pending": "#7A8DA8",
}
_NODE_W, _NODE_H = 210, 96
_PORT_R = 6                 # output port radius (the drag-to-connect handle)
_PORT_HIT = 15             # click tolerance around a port
_CORNER_R = 12             # edge elbow corner radius


class _NodeItem(QGraphicsObject):
    """One draggable step card. Emits signals via the parent canvas."""

    def __init__(self, node: Node, canvas: "Co4ECanvas"):
        super().__init__()
        self.node = node
        self.canvas = canvas
        self.status = "idle"
        self._porting = False
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setPos(node.x, node.y)
        self.setZValue(2)

    def boundingRect(self) -> QRectF:
        # slack left/right so the input/output ports (now on the sides) paint cleanly
        return QRectF(-_PORT_R - 2, -3, _NODE_W + 2 * _PORT_R + 4, _NODE_H + 6)

    def _card_rect(self) -> QRectF:
        return QRectF(1, 1, _NODE_W - 2, _NODE_H - 2)

    def paint(self, p, _opt, _widget=None):
        step = self.node.data
        accent = QColor(_STATUS_COLOR.get(self.status, "#5C8DB8"))
        body = QColor("#0D1F35")
        border = QColor("#48CAE4") if self.isSelected() else QColor("#1A2D4A")
        p.setRenderHint(p.RenderHint.Antialiasing)
        rect = self._card_rect()
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        p.fillPath(path, QBrush(body))
        p.setPen(QPen(border, 2 if self.isSelected() else 1))
        p.drawPath(path)
        # header stripe
        hdr = QRectF(rect.left(), rect.top(), rect.width(), 26)
        hpath = QPainterPath()
        hpath.addRoundedRect(hdr, 10, 10)
        p.fillPath(hpath, QBrush(accent.darker(160)))
        # label
        p.setPen(QColor("#E0F0FF"))
        f = p.font(); f.setBold(True); f.setPointSize(9); p.setFont(f)
        p.drawText(QRectF(10, 4, _NODE_W - 20, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   _elide(step.label, 26))
        # role badge + status
        f.setBold(False); f.setPointSize(8); p.setFont(f)
        p.setPen(accent)
        p.drawText(QRectF(10, 30, _NODE_W - 20, 16), Qt.AlignLeft, step.role)
        # body: instructions preview OR sub-agent chips
        p.setPen(QColor("#8FB2D4"))
        if step.is_parallel:
            preview = "⇉ " + ", ".join(s.agent for s in step.sub_agents) if step.sub_agents else "⇉ (no sub-agents)"
        else:
            preview = step.instructions or "(no instructions)"
        p.drawText(QRectF(10, 46, _NODE_W - 20, 30), Qt.TextWordWrap | Qt.AlignTop,
                   _elide(preview, 66))
        # footer: model + skills + status dot
        p.setPen(QColor("#5C8DB8"))
        foot = []
        if step.model:
            foot.append(step.model)
        if step.skills:
            foot.append(f"skills:{len(step.skills)}")
        foot.append(self.status)
        p.drawText(QRectF(10, _NODE_H - 18, _NODE_W - 20, 14), Qt.AlignLeft,
                   _elide(" · ".join(foot), 34))
        # ---- ports ---------------------------------------------------------
        # input port (top-center): hollow. output port (bottom-center): filled —
        # the drag handle you pull to wire an edge to another step.
        port_col = QColor("#48CAE4")
        # input port (left-center): hollow. output port (right-center): filled —
        # the drag handle you pull to wire an edge to the next step (left→right).
        p.setBrush(QBrush(body)); p.setPen(QPen(port_col, 1.4))
        p.drawEllipse(QPointF(1, _NODE_H / 2), _PORT_R - 1, _PORT_R - 1)
        p.setBrush(QBrush(port_col)); p.setPen(QPen(port_col, 1.4))
        p.drawEllipse(QPointF(_NODE_W - 1, _NODE_H / 2), _PORT_R, _PORT_R)

    def _in_out_port(self, pos: QPointF) -> bool:
        d = pos - QPointF(_NODE_W, _NODE_H / 2)
        return (d.x() * d.x() + d.y() * d.y()) ** 0.5 <= _PORT_HIT

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.node.x = float(self.pos().x())
            self.node.y = float(self.pos().y())
            self.canvas._reposition_edges()
            self.canvas.graph_changed.emit()
        elif change == QGraphicsItem.ItemSelectedHasChanged:
            # a selected/edited node comes to the front (above the edges at z=3)
            self.setZValue(4 if value else 2)
            if value:
                self.canvas.node_selected.emit(self.node.id)
        return super().itemChange(change, value)

    def hoverMoveEvent(self, e):
        # a hand cursor over the output port hints it's draggable-to-connect
        self.setCursor(Qt.PointingHandCursor if self._in_out_port(e.pos()) else Qt.ArrowCursor)
        super().hoverMoveEvent(e)

    def mousePressEvent(self, e):
        if self.canvas._connect_from is not None:
            self.canvas._finish_connect(self.node.id)
            e.accept()
            return
        if e.button() == Qt.LeftButton and self._in_out_port(e.pos()):
            # start a manual drag-to-connect from this node's output port
            self._porting = True
            self.canvas.begin_port_drag(self.node.id, self.mapToScene(QPointF(_NODE_W, _NODE_H / 2)))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._porting:
            self.canvas.update_port_drag(self.mapToScene(e.pos()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._porting:
            self._porting = False
            self.canvas.finish_port_drag(self.mapToScene(e.pos()))
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.canvas.node_activated.emit(self.node.id)
        e.accept()

    def contextMenuEvent(self, e):
        menu = QMenu()
        a_add = menu.addAction("＋ Add next step")
        a_conn = menu.addAction("→ Connect from here")
        a_del = menu.addAction("🗑 Delete step")
        chosen = menu.exec(e.screenPos())
        if chosen is a_add:
            self.canvas.add_step_below(self.node.id)
        elif chosen is a_conn:
            self.canvas.begin_connect(self.node.id)
        elif chosen is a_del:
            self.canvas.delete_node(self.node.id)
        e.accept()

    def center(self) -> QPointF:
        return self.pos() + QPointF(_NODE_W / 2, _NODE_H / 2)


def _dist(a: QPointF, b: QPointF) -> float:
    return ((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2) ** 0.5


def _towards(a: QPointF, b: QPointF, d: float) -> QPointF:
    dist = _dist(a, b)
    if dist < 1e-6:
        return QPointF(a)
    t = d / dist
    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)


def _rounded_path(points, r: float = _CORNER_R) -> QPainterPath:
    """Build a path through axis-aligned ``points`` with rounded corners at each
    bend ("vuông bo cong ở góc")."""
    if not points:
        return QPainterPath()
    path = QPainterPath(points[0])
    if len(points) == 1:
        return path
    for i in range(1, len(points) - 1):
        prev, cur, nxt = points[i - 1], points[i], points[i + 1]
        rr = min(r, _dist(prev, cur) / 2.0, _dist(cur, nxt) / 2.0)
        path.lineTo(_towards(cur, prev, rr))
        path.quadTo(cur, _towards(cur, nxt, rr))
    path.lineTo(points[-1])
    return path


def _seg_hits_rect(p1: QPointF, p2: QPointF, rect: QRectF) -> bool:
    """Axis-aligned segment vs rectangle overlap (all routed segments are H or V)."""
    x1, y1, x2, y2 = p1.x(), p1.y(), p2.x(), p2.y()
    if abs(y1 - y2) < 0.5:                       # horizontal
        if rect.top() <= y1 <= rect.bottom():
            lo, hi = sorted((x1, x2))
            return not (hi < rect.left() or lo > rect.right())
        return False
    if abs(x1 - x2) < 0.5:                       # vertical
        if rect.left() <= x1 <= rect.right():
            lo, hi = sorted((y1, y2))
            return not (hi < rect.top() or lo > rect.bottom())
        return False
    box = QRectF(QPointF(min(x1, x2), min(y1, y2)), QPointF(max(x1, x2), max(y1, y2)))
    return rect.intersects(box)


def _hits(points, obstacles) -> bool:
    for i in range(len(points) - 1):
        for r in obstacles:
            if _seg_hits_rect(points[i], points[i + 1], r):
                return True
    return False


def _route(src: QPointF, dst: QPointF, obstacles=None):
    """Waypoints for a LEFT→RIGHT orthogonal edge from ``src`` (a node's right
    output) to ``dst`` (the next node's left input) that AVOIDS the other node
    rectangles: try the straight elbow, then a clear vertical band, then a
    top/bottom detour — so a connector never overlaps or hides behind a step."""
    obstacles = list(obstacles or [])
    if abs(src.y() - dst.y()) < 1.5:
        cand = [src, dst]
        if not _hits(cand, obstacles):
            return cand
    mid_x = (src.x() + dst.x()) / 2.0
    base = [src, QPointF(mid_x, src.y()), QPointF(mid_x, dst.y()), dst]
    if not _hits(base, obstacles):
        return base
    # 1) slide the vertical run to a clear band between the two columns
    lo, hi = min(src.x(), dst.x()) + 6, max(src.x(), dst.x()) - 6
    if hi > lo:
        for frac in (0.5, 0.35, 0.65, 0.2, 0.8):
            x = lo + (hi - lo) * frac
            cand = [src, QPointF(x, src.y()), QPointF(x, dst.y()), dst]
            if not _hits(cand, obstacles):
                return cand
    # 2) detour above/below every obstacle, then back in
    margin = 44.0
    ys = [src.y(), dst.y()] + [r.top() for r in obstacles] + [r.bottom() for r in obstacles]
    out_x, in_x = src.x() + 34, dst.x() - 34   # short stubs out of the side ports
    for side_y in (min(ys) - margin, max(ys) + margin):
        cand = [src, QPointF(out_x, src.y()), QPointF(out_x, side_y),
                QPointF(in_x, side_y), QPointF(in_x, dst.y()), dst]
        if not _hits(cand, obstacles):
            return cand
    return base


def _ortho_path(src: QPointF, dst: QPointF, r: float = _CORNER_R) -> QPainterPath:
    """Rounded orthogonal elbow (no obstacle avoidance) — used for the transient
    drag-to-connect line and by callers that pass no obstacles."""
    return _rounded_path(_route(src, dst), r)


class _EdgeItem(QGraphicsPathItem):
    def __init__(self, edge: Edge, canvas: "Co4ECanvas"):
        super().__init__()
        self.edge = edge
        self.canvas = canvas
        self._dst: Optional[QPointF] = None
        # Above node cards (z=2) so a connecting line is never hidden behind a
        # step; a selected node bumps itself to the front while being edited.
        self.setZValue(3)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._hover = False
        self._apply_pen()

    def _apply_pen(self):
        if self.isSelected():
            color, w = QColor("#48CAE4"), 3
        elif self._hover:
            color, w = QColor("#6FA8C8"), 3
        else:
            color, w = QColor("#3A5A78"), 2
        self.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def update_path(self, points):
        self._dst = points[-1] if points else None
        self.setPath(_rounded_path(points))

    def boundingRect(self):
        return super().boundingRect().adjusted(-10, -10, 10, 10)   # room for the arrowhead

    def shape(self):
        # Widen the clickable/selectable area so a thin line is easy to grab.
        from PySide6.QtGui import QPainterPathStroker
        stroker = QPainterPathStroker()
        stroker.setWidth(14)
        return stroker.createStroke(self.path())

    def hoverEnterEvent(self, e):
        self._hover = True
        self._apply_pen()
        self.update()
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        self._hover = False
        self._apply_pen()
        self.update()
        super().hoverLeaveEvent(e)

    def paint(self, p, opt, widget=None):
        self._apply_pen()
        super().paint(p, opt, widget)
        # arrowhead at the target, pointing right into its (left) input port
        if self._dst is not None:
            p.setRenderHint(p.RenderHint.Antialiasing)
            tip = self._dst
            s = 7.0
            tri = QPolygonF([
                QPointF(tip.x() + 1, tip.y()),
                QPointF(tip.x() - s, tip.y() - s * 0.7),
                QPointF(tip.x() - s, tip.y() + s * 0.7),
            ])
            col = self.pen().color()
            p.setBrush(QBrush(col))
            p.setPen(QPen(col, 1))
            p.drawPolygon(tri)

    def contextMenuEvent(self, e):
        menu = QMenu()
        act_del = menu.addAction("🗑 Delete connection")
        if menu.exec(e.screenPos()) is act_del:
            self.canvas.delete_edge(self.edge)
        e.accept()


def _elide(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


class Co4ECanvas(QGraphicsView):
    node_selected = Signal(str)     # a node was clicked (→ config panel)
    node_activated = Signal(str)    # double-clicked
    graph_changed = Signal()        # nodes/edges/positions changed (autosave)

    _ZOOM_MIN, _ZOOM_MAX = 0.3, 3.0

    def __init__(self):
        super().__init__()
        self.setObjectName("co4eCanvas")   # themed frame (see theme.py)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(self.renderHints().Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self._nodes: Dict[str, _NodeItem] = {}
        self._edges: list[_EdgeItem] = []
        self._connect_from: Optional[str] = None
        self._zoom = 1.0
        self._panning = False         # middle-mouse drag-to-pan
        self._pan_start = None
        self._overlay = None          # bottom-left zoom/fit controls (parented to viewport)
        # manual drag-to-connect state
        self._port_src: Optional[str] = None
        self._port_src_pt: Optional[QPointF] = None
        self._temp_edge: Optional[QGraphicsPathItem] = None

    # ---- bottom-left overlay (zoom / fit) --------------------------------
    def add_overlay(self, widget) -> None:
        self._overlay = widget
        widget.setParent(self.viewport())
        widget.show()
        widget.raise_()
        self._place_overlay()

    def _place_overlay(self) -> None:
        if self._overlay is not None:
            self._overlay.adjustSize()
            vp = self.viewport()
            self._overlay.move(12, vp.height() - self._overlay.height() - 12)
            self._overlay.raise_()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._place_overlay()

    def scrollContentsBy(self, dx, dy):  # noqa: N802
        # QGraphicsView scrolls the viewport's child widgets along with the
        # scene, so panning/scrolling would drag the zoom overlay off-corner.
        # Re-pin it after every scroll so +/−/fit stay fixed in place.
        super().scrollContentsBy(dx, dy)
        self._place_overlay()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self._place_overlay()   # viewport size is final once shown

    # ---- load / serialize -------------------------------------------------
    def load(self, nodes, edges) -> None:
        self._scene.clear()
        self._nodes.clear()
        self._edges.clear()
        self._connect_from = None
        self._port_src = None
        self._temp_edge = None
        for n in nodes:
            item = _NodeItem(n, self)
            self._nodes[n.id] = item
            self._scene.addItem(item)
        for e in edges:
            if e.source in self._nodes and e.target in self._nodes:
                self._add_edge_item(e)
        self._reposition_edges()

    def nodes(self):
        return [it.node for it in self._nodes.values()]

    def edges(self):
        return [it.edge for it in self._edges]

    # ---- mutation ---------------------------------------------------------
    def add_node(self, step: Step, x: float = 60.0, y: float = 60.0,
                 connect_from: str = "") -> str:
        node = Node(id=new_node_id(), x=x, y=y, data=step)
        item = _NodeItem(node, self)
        self._nodes[node.id] = item
        self._scene.addItem(item)
        if connect_from and connect_from in self._nodes:
            self._make_edge(connect_from, node.id)
        self._reposition_edges()
        self.graph_changed.emit()
        self.node_selected.emit(node.id)
        return node.id

    def add_step_below(self, node_id: str) -> None:
        """Add the next step to the RIGHT of ``node_id`` (horizontal flow)."""
        parent = self._nodes.get(node_id)
        if parent is None:
            return
        step = Step(label="New Step")
        self.add_node(step, x=parent.node.x + _NODE_W + 150, y=parent.node.y, connect_from=node_id)

    def _chain_tail(self) -> str:
        """A node with no outgoing edge (so a freshly added node chains on)."""
        sources = {e.edge.source for e in self._edges}
        tails = [nid for nid in self._nodes if nid not in sources]
        return tails[-1] if tails else (next(reversed(self._nodes), "") if self._nodes else "")

    def add_palette_step(self, step: Step, pos: QPointF) -> None:
        tail = self._chain_tail()
        self.add_node(step, x=pos.x(), y=pos.y(), connect_from=tail)

    def begin_connect(self, source_id: str) -> None:
        self._connect_from = source_id

    def _finish_connect(self, target_id: str) -> None:
        src = self._connect_from
        self._connect_from = None
        if src and src != target_id:
            self._make_edge(src, target_id)

    # ---- manual drag-to-connect (from a node's output port) ---------------
    def begin_port_drag(self, source_id: str, scene_pt: QPointF) -> None:
        self._port_src = source_id
        self._port_src_pt = scene_pt
        self._temp_edge = QGraphicsPathItem()
        self._temp_edge.setZValue(3.5)      # above nodes + edges while connecting
        self._temp_edge.setPen(QPen(QColor("#48CAE4"), 2, Qt.DashLine, Qt.RoundCap))
        self._scene.addItem(self._temp_edge)

    def update_port_drag(self, scene_pt: QPointF) -> None:
        if self._temp_edge is None or self._port_src_pt is None:
            return
        self._temp_edge.setPath(_ortho_path(self._port_src_pt, scene_pt))

    def finish_port_drag(self, scene_pt: QPointF) -> None:
        src = self._port_src
        if self._temp_edge is not None:
            self._scene.removeItem(self._temp_edge)
        self._temp_edge = None
        self._port_src = None
        self._port_src_pt = None
        tgt = self._node_at(scene_pt)
        if src and tgt and tgt != src:
            self._make_edge(src, tgt)

    def _node_at(self, scene_pt: QPointF) -> Optional[str]:
        for it in self._scene.items(scene_pt):
            if isinstance(it, _NodeItem):
                return it.node.id
        return None

    def _make_edge(self, source: str, target: str) -> None:
        if source == target:
            return
        if any(e.edge.source == source and e.edge.target == target for e in self._edges):
            return
        edge = Edge(id=new_edge_id(source, target), source=source, target=target)
        self._add_edge_item(edge)
        self._reposition_edges()
        self.graph_changed.emit()

    def _add_edge_item(self, edge: Edge) -> None:
        item = _EdgeItem(edge, self)
        self._edges.append(item)
        self._scene.addItem(item)

    def delete_edge(self, edge: Edge) -> None:
        for e in list(self._edges):
            if e.edge is edge or (e.edge.source == edge.source and e.edge.target == edge.target):
                self._scene.removeItem(e)
                self._edges.remove(e)
        self.graph_changed.emit()

    def delete_node(self, node_id: str) -> None:
        item = self._nodes.pop(node_id, None)
        if item is None:
            return
        self._scene.removeItem(item)
        for e in list(self._edges):
            if e.edge.source == node_id or e.edge.target == node_id:
                self._scene.removeItem(e)
                self._edges.remove(e)
        self._reposition_edges()
        self.graph_changed.emit()

    def delete_selected(self) -> None:
        for nid in [it.node.id for it in self._nodes.values() if it.isSelected()]:
            self.delete_node(nid)
        for e in [it.edge for it in self._edges if it.isSelected()]:
            self.delete_edge(e)

    # ---- zoom / fit -------------------------------------------------------
    def _zoom_by(self, factor: float) -> None:
        # Derive the CURRENT scale from the live transform (never a separate
        # accumulator that can drift out of sync with fit_view/relayout/reset —
        # that drift is what made the +/− buttons and Ctrl+wheel randomly stop
        # working). Clamp the TARGET to the range and apply the exact factor to
        # reach it, so zooming still works right up to the limits.
        cur = self.transform().m11() or 1.0
        target = max(self._ZOOM_MIN, min(self._ZOOM_MAX, cur * factor))
        if abs(target - cur) < 1e-6:
            return
        self.scale(target / cur, target / cur)
        self._zoom = target

    def zoom_in(self) -> None:
        self._zoom_by(1.15)

    def zoom_out(self) -> None:
        self._zoom_by(1 / 1.15)

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 1.0

    def wheelEvent(self, e):
        # Ctrl+wheel = zoom (anchored under the cursor); Shift+wheel = pan
        # horizontally; plain wheel scrolls vertically.
        if e.modifiers() & Qt.ControlModifier:
            self._zoom_by(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
            return
        if e.modifiers() & Qt.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - e.angleDelta().y())
            e.accept()
            return
        super().wheelEvent(e)

    # ---- middle-mouse drag-to-pan ----------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning and self._pan_start is not None:
            pos = e.position().toPoint()
            delta = pos - self._pan_start
            self._pan_start = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def fit_view(self) -> None:
        """Auto-fit: zoom/pan so every node is visible with a small margin."""
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            return
        self.setSceneRect(rect.adjusted(-60, -60, 60, 60))
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        # keep the zoom accumulator in sync with the transform fitInView applied
        self._zoom = self.transform().m11() or 1.0

    def relayout(self, hgap: float = 110.0, vgap: float = 40.0) -> None:
        """Arrange nodes LEFT→RIGHT by dependency depth: each topological wave is
        a column (x = wave), siblings stacked vertically within it. Used to turn
        an old top-down graph into the horizontal flow layout."""
        nodes = [it.node for it in self._nodes.values()]
        edges = [it.edge for it in self._edges]
        if not nodes:
            return
        waves = compute_waves(nodes, edges)
        from collections import defaultdict
        cols: Dict[int, list] = defaultdict(list)
        for n in nodes:
            cols[waves.get(n.id, 0)].append(n)
        for w in sorted(cols):
            for row, n in enumerate(sorted(cols[w], key=lambda nn: (nn.y, nn.x))):
                item = self._nodes.get(n.id)
                if item is not None:
                    item.setPos(w * (_NODE_W + hgap), row * (_NODE_H + vgap))
        self._reposition_edges()

    def relayout_if_vertical(self) -> None:
        """Convert a graph that's stacked vertically (the old top-down layout, or
        overlapping nodes) into the horizontal left→right layout — but leave a
        graph the user already arranged horizontally untouched."""
        nodes = [it.node for it in self._nodes.values()]
        if len(nodes) < 2:
            return
        xs = [n.x for n in nodes]
        if max(xs) - min(xs) < _NODE_W:      # all in one column → it's vertical
            self.relayout()

    def add_workflow(self, nodes, edges, at: Optional[QPointF] = None) -> None:
        """Drop/merge a whole flow's nodes+edges onto the canvas with fresh ids
        (so the same template can be dropped several times). Offsets it near
        ``at`` when given, else tiles it beside whatever is already there."""
        remap: Dict[str, str] = {}
        # offset so a dropped template doesn't land exactly on existing nodes
        ox = (at.x() - nodes[0].x) if (at and nodes) else (60 if self._nodes else 0)
        oy = (at.y() - nodes[0].y) if (at and nodes) else (60 if self._nodes else 0)
        for n in nodes:
            new = Node(id=new_node_id(), x=n.x + ox, y=n.y + oy, data=copy.deepcopy(n.data))
            remap[n.id] = new.id
            item = _NodeItem(new, self)
            self._nodes[new.id] = item
            self._scene.addItem(item)
        for e in edges:
            s, t = remap.get(e.source), remap.get(e.target)
            if s and t:
                self._add_edge_item(Edge(id=new_edge_id(s, t), source=s, target=t))
        self._reposition_edges()
        self.graph_changed.emit()

    def update_node_status(self, node_id: str, status: str) -> None:
        item = self._nodes.get(node_id)
        if item is not None:
            item.status = status
            item.update()

    def reset_statuses(self) -> None:
        for it in self._nodes.values():
            it.status = "idle"
            it.update()

    def refresh_node(self, node_id: str) -> None:
        item = self._nodes.get(node_id)
        if item is not None:
            item.update()

    def _node_rects(self, exclude):
        """Rectangles of every node except ``exclude`` (inflated a little), used
        as obstacles the edge router steers around."""
        m = 12.0
        out = []
        for nid, item in self._nodes.items():
            if nid in exclude:
                continue
            p = item.pos()
            out.append(QRectF(p.x(), p.y(), _NODE_W, _NODE_H).adjusted(-m, -m, m, m))
        return out

    def _reposition_edges(self) -> None:
        for e in self._edges:
            s = self._nodes.get(e.edge.source)
            t = self._nodes.get(e.edge.target)
            if s is None or t is None:
                continue
            src = s.pos() + QPointF(_NODE_W, _NODE_H / 2)   # right-center (output)
            dst = t.pos() + QPointF(0, _NODE_H / 2)          # left-center (input)
            obstacles = self._node_rects({e.edge.source, e.edge.target})
            e.update_path(_route(src, dst, obstacles))

    # ---- key / drop -------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
            return
        if e.key() == Qt.Key_Escape:
            self._connect_from = None
            if self._temp_edge is not None:
                self._scene.removeItem(self._temp_edge)
                self._temp_edge = None
                self._port_src = None
            return
        if e.key() in (Qt.Key_Plus, Qt.Key_Equal) and (e.modifiers() & Qt.ControlModifier):
            self.zoom_in(); return
        if e.key() == Qt.Key_Minus and (e.modifiers() & Qt.ControlModifier):
            self.zoom_out(); return
        if e.key() == Qt.Key_0 and (e.modifiers() & Qt.ControlModifier):
            self.reset_zoom(); return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(CO4E_MIME):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(CO4E_MIME):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(CO4E_MIME):
            super().dropEvent(e)
            return
        try:
            payload = json.loads(bytes(e.mimeData().data(CO4E_MIME)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        pos = self.mapToScene(e.position().toPoint())
        if isinstance(payload, dict) and payload.get("kind") == "workflow":
            # A whole flow dragged from the sidebar → merge its graph in.
            from ..core.co4e import workflow_from_dict
            wf = workflow_from_dict(payload.get("workflow", {}))
            if wf.nodes:
                self.add_workflow(wf.nodes, wf.edges, at=pos)
        else:
            from ..core.co4e import step_from_dict
            self.add_palette_step(step_from_dict(payload), pos)
        e.acceptProposedAction()
