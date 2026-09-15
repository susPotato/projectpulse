"""Render a StructureGraph into the bundled D3 knowledge-graph template.

The template (assets/graph_template.html) contains a ``GRAPH_DATA_PLACEHOLDER``
and a ``COLOR_MAP`` object; we inject our nodes/edges and the kind→colour map.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .structure_graph import NODE_KIND_COLORS

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "graph_template.html"


_CDN_D3 = '<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>'


def build_html(graph) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")

    # Inline a bundled d3 (offline) if present; else keep the CDN reference.
    d3_local = TEMPLATE.parent / "d3.min.js"
    if d3_local.exists():
        try:
            d3_src = d3_local.read_text(encoding="utf-8")
            html = html.replace(_CDN_D3, f"<script>{d3_src}</script>")
        except OSError:
            pass

    nodes = [{"id": n.id, "label": n.label, "type": n.kind,
              "description": n.detail, "path": getattr(n, "path", "")}
             for n in graph.nodes]
    links = [{"source": e.source, "target": e.target, "label": e.type}
             for e in graph.edges]
    data_js = json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False)
    data_js = data_js.replace("</", "<\\/")  # never break the <script> block

    html = html.replace("GRAPH_DATA_PLACEHOLDER", data_js)

    color_js = "const COLOR_MAP = " + json.dumps(NODE_KIND_COLORS, ensure_ascii=False) + ";"
    html = re.sub(r"const COLOR_MAP = \{.*?\};", lambda _m: color_js, html,
                  count=1, flags=re.DOTALL)
    return html
