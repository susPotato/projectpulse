"""Build a knowledge graph (Graph-RAG style) of code / document structure.

Two builders:
  - ``build_from_directory`` — stdlib only. Parses Python files with ``ast``
    (files, classes, functions, methods, imports), Markdown/text files by
    heading hierarchy, and JSON files by their own key/array structure. No
    external dependency.
  - ``build_from_codebase_memory`` — best-effort enrichment using the
    codebase-memory-mcp knowledge graph when available; falls back to the local
    builder on any problem.

Both return a :class:`StructureGraph` of nodes + edges that the UI lays out and
renders. A simple layered layout is provided.
"""
from __future__ import annotations

import ast
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".pytest_cache",
                ".mypy_cache", "dist", "build", ".idea", ".vscode"}
CODE_SUFFIXES = {".py"}
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
JSON_SUFFIXES = {".json"}
DEFAULT_MAX_NODES = 400
MAX_IMPORTS_PER_FILE = 8
MAX_JSON_DEPTH = 20           # how many nested levels of keys/arrays to expand
MAX_JSON_KEYS_PER_LEVEL = 200  # cap per object, so one huge JSON can't flood the graph


@dataclass
class GNode:
    id: str
    label: str
    kind: str          # dir | file | class | function | method | module | section
    detail: str = ""
    path: str = ""     # absolute file/folder this node maps to (for "open folder")


@dataclass
class GEdge:
    source: str
    target: str
    type: str = ""     # contains | defines | method | imports | subsection


@dataclass
class StructureGraph:
    nodes: List[GNode] = field(default_factory=list)
    edges: List[GEdge] = field(default_factory=list)
    truncated: bool = False
    max_nodes: int = 0   # 0 = unlimited
    max_edges: int = 0   # 0 = unlimited

    def __post_init__(self):
        self._ids = {n.id for n in self.nodes}

    def add_node(self, node: GNode) -> bool:
        if node.id in self._ids:
            return False
        if self.max_nodes and len(self.nodes) >= self.max_nodes:
            self.truncated = True
            return False
        self.nodes.append(node)
        self._ids.add(node.id)
        return True

    def add_edge(self, source: str, target: str, type_: str = "") -> None:
        if source in self._ids and target in self._ids:
            if self.max_edges and len(self.edges) >= self.max_edges:
                self.truncated = True
                return
            self.edges.append(GEdge(source, target, type_))

    def has(self, node_id: str) -> bool:
        return node_id in self._ids


# --------------------------------------------------------------------------
# Local (stdlib) builder
# --------------------------------------------------------------------------
def build_from_directory(root, mode: str = "all", max_nodes: int = 0,
                         max_edges: int = 0) -> StructureGraph:
    """Build the structure graph for a folder.

    mode:
      - 'files' — the WHOLE folder/file tree: Python and docs parsed deeply, and
        every other file type shown as a plain file node (structure only).
      - 'all'   — Code & Docs: Python + Markdown/text parsed (no other file types).
      - 'code'  — Python files only.
      - 'doc'   — Markdown/text docs only.

    ``max_nodes=0`` (default) means no cap — every node/edge is kept.
    """
    root_path = Path(root).expanduser().resolve()
    graph = StructureGraph(max_nodes=max_nodes, max_edges=max_edges)

    root_id = f"dir:{root_path}"
    graph.add_node(GNode(root_id, root_path.name or str(root_path), "dir", str(root_path), str(root_path)))

    want_code = mode in ("files", "all", "code")
    want_doc = mode in ("files", "all", "doc")
    want_json = mode == "files"   # deep-parse JSON only in the "All" (files) view
    want_other = mode == "files"   # show every remaining file type as a file node

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
        if graph.truncated:
            break
        dpath = Path(dirpath)
        dir_id = f"dir:{dpath}"
        if dpath != root_path:
            graph.add_node(GNode(dir_id, dpath.name, "dir", str(dpath), str(dpath)))
            parent_id = f"dir:{dpath.parent}"
            graph.add_edge(parent_id, dir_id, "contains")

        for fname in sorted(filenames):
            suffix = Path(fname).suffix.lower()
            fpath = dpath / fname
            if want_code and suffix in CODE_SUFFIXES:
                _add_python_file(graph, dir_id, fpath, root_path)
            elif want_json and suffix in JSON_SUFFIXES:
                _add_json_file(graph, dir_id, fpath, root_path)
            elif want_doc and suffix in DOC_SUFFIXES:
                _add_doc_file(graph, dir_id, fpath, root_path)
            elif want_other:
                _add_generic_file(graph, dir_id, fpath, root_path)
            if graph.truncated:
                break
    return graph


def _add_generic_file(graph: StructureGraph, dir_id: str, fpath: Path, root: Path) -> None:
    """Add a plain file node (no inner parsing) so the full folder/file tree is
    shown — used for file types beyond Python/docs in 'files' mode."""
    file_id = f"file:{fpath}"
    if graph.add_node(GNode(file_id, fpath.name, "file", _rel(fpath, root), str(fpath))):
        graph.add_edge(dir_id, file_id, "contains")


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _add_python_file(graph: StructureGraph, dir_id: str, fpath: Path, root: Path) -> None:
    file_id = f"file:{fpath}"
    if not graph.add_node(GNode(file_id, fpath.name, "file", _rel(fpath, root), str(fpath))):
        return
    graph.add_edge(dir_id, file_id, "contains")
    fp = str(fpath)
    try:
        tree = ast.parse(fpath.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_id = f"{file_id}::{node.name}"
            graph.add_node(GNode(fn_id, node.name + "()", "function", _rel(fpath, root), fp))
            graph.add_edge(file_id, fn_id, "defines")
        elif isinstance(node, ast.ClassDef):
            cls_id = f"{file_id}::{node.name}"
            graph.add_node(GNode(cls_id, node.name, "class", _rel(fpath, root), fp))
            graph.add_edge(file_id, cls_id, "defines")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_id = f"{cls_id}.{item.name}"
                    graph.add_node(GNode(m_id, item.name + "()", "method", f"{node.name}.{item.name}", fp))
                    graph.add_edge(cls_id, m_id, "method")

    imports = _module_imports(tree)[:MAX_IMPORTS_PER_FILE]
    for mod in imports:
        mod_id = f"mod:{mod}"
        graph.add_node(GNode(mod_id, mod, "module", "import"))
        graph.add_edge(file_id, mod_id, "imports")


def _module_imports(tree: ast.AST) -> List[str]:
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module.split(".")[0])
    seen, out = set(), []
    for m in mods:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _add_doc_file(graph: StructureGraph, dir_id: str, fpath: Path, root: Path) -> None:
    file_id = f"file:{fpath}"
    fp = str(fpath)
    if not graph.add_node(GNode(file_id, fpath.name, "file", _rel(fpath, root), fp)):
        return
    graph.add_edge(dir_id, file_id, "contains")
    try:
        lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return

    # Track the most recent heading id at each level to build hierarchy.
    last_at_level: Dict[int, str] = {0: file_id}
    counter = 0
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        title = stripped[level:].strip()
        if not title or level > 6:
            continue
        counter += 1
        sec_id = f"{file_id}#sec{counter}"
        if not graph.add_node(GNode(sec_id, title[:48], "section", title, fp)):
            break
        parent = next((last_at_level[lv] for lv in range(level - 1, -1, -1) if lv in last_at_level), file_id)
        graph.add_edge(parent, sec_id, "subsection")
        last_at_level[level] = sec_id
        # invalidate deeper levels
        for lv in list(last_at_level):
            if lv > level:
                del last_at_level[lv]


def _add_json_file(graph: StructureGraph, dir_id: str, fpath: Path, root: Path) -> None:
    """Parse a JSON file's OWN structure (object keys / array shape) into
    nodes — the same way Python files become classes/functions and Markdown
    becomes heading sections — instead of showing up as just a flat file
    node with nothing inside it."""
    file_id = f"file:{fpath}"
    fp = str(fpath)
    if not graph.add_node(GNode(file_id, fpath.name, "file", _rel(fpath, root), fp)):
        return
    graph.add_edge(dir_id, file_id, "contains")
    try:
        data = json.loads(fpath.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, RecursionError):
        # RecursionError: a syntactically valid but very deeply nested document
        # (e.g. 1000+ levels of arrays) blows Python's json parser's recursion
        # budget — skip just this file rather than aborting the whole scan.
        return
    _add_json_value(graph, file_id, fp, data, depth=0)


def _json_scalar_preview(value) -> str:
    if isinstance(value, dict):
        return f"{{…}} ({len(value)} keys)"
    if isinstance(value, list):
        return f"[…] ({len(value)} items)"
    return str(value)[:40]


def _add_json_value(graph: StructureGraph, parent_id: str, fp: str, value, depth: int) -> None:
    if depth >= MAX_JSON_DEPTH:
        return
    if isinstance(value, dict):
        # Child ids are the parent's id plus this key's POSITION, never the key
        # text itself — a key that happens to contain the "#" join character
        # (or matches another key's text at a different depth) can otherwise
        # collide with a deeper node's id and silently delete it from the graph.
        for i, (key, val) in enumerate(list(value.items())[:MAX_JSON_KEYS_PER_LEVEL]):
            key_id = f"{parent_id}#{i}"
            # Only dicts and arrays-of-objects get expanded further; a scalar
            # array (e.g. ["a","b","c"]) has nothing more to show, so it's
            # labelled with its preview and left as a leaf like any scalar.
            needs_recursion = isinstance(val, dict) or (
                isinstance(val, list) and val and isinstance(val[0], dict))
            # Always show key: value (even for nested objects/arrays show a preview)
            label = f"{key}: {_json_scalar_preview(val)}"
            if not graph.add_node(GNode(key_id, label[:120], "json_key", str(key), fp)):
                continue
            graph.add_edge(parent_id, key_id, "contains")
            if needs_recursion:
                _add_json_value(graph, key_id, fp, val, depth + 1)
    elif isinstance(value, list) and value and isinstance(value[0], dict):
        # Arrays of objects: show the FIRST element's shape as a representative
        # sample rather than exploding every item (a 500-row JSON array would
        # otherwise flood the graph with near-identical nodes).
        sample_id = f"{parent_id}#0"
        if graph.add_node(GNode(sample_id, f"[0] of {len(value)} (sample)", "json_key",
                                "array item sample", fp)):
            graph.add_edge(parent_id, sample_id, "contains")
            _add_json_value(graph, sample_id, fp, value[0], depth + 1)


# --------------------------------------------------------------------------
# Codebase-memory builder (best effort, falls back to local)
# --------------------------------------------------------------------------
def build_from_codebase_memory(mem, repo_path, mode: str = "all",
                               max_nodes: int = 0, max_edges: int = 0) -> StructureGraph:
    """Try to build from the codebase-memory knowledge graph; fall back local."""
    try:
        graph = StructureGraph(max_nodes=max_nodes, max_edges=max_edges)
        produced = 0
        for label, kind in (("Class", "class"), ("Function", "function")):
            res = mem.call("search_graph", {"label": label, "limit": 150})
            for item in _iter_results(res):
                name = item.get("name") or item.get("label")
                if not name:
                    continue
                file = item.get("file") or item.get("path") or ""
                nid = f"{label}:{file}:{name}"
                if graph.add_node(GNode(nid, name, kind, file)):
                    produced += 1
                if file:
                    fid = f"file:{file}"
                    graph.add_node(GNode(fid, Path(file).name, "file", file))
                    graph.add_edge(fid, nid, "defines")
        if produced >= 3:
            return graph
    except Exception:
        pass
    return build_from_directory(repo_path, mode=mode)


def _iter_results(res):
    if isinstance(res, dict):
        for key in ("results", "nodes", "items", "data"):
            val = res.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    if isinstance(res, list):
        return [x for x in res if isinstance(x, dict)]
    return []


# --------------------------------------------------------------------------
# Layout (layered by distance from roots)
# --------------------------------------------------------------------------
def layered_layout(graph: StructureGraph, col_w: int = 280, row_h: int = 64) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, int]]:
    indeg = {n.id: 0 for n in graph.nodes}
    adj = defaultdict(list)
    for e in graph.edges:
        if e.target in indeg:
            indeg[e.target] += 1
        adj[e.source].append(e.target)

    roots = [n.id for n in graph.nodes if indeg.get(n.id, 0) == 0]
    if not roots and graph.nodes:
        roots = [graph.nodes[0].id]

    level: Dict[str, int] = {}
    dq = deque()
    for r in roots:
        level[r] = 0
        dq.append(r)
    while dq:
        cur = dq.popleft()
        for nxt in adj[cur]:
            if nxt not in level:
                level[nxt] = level[cur] + 1
                dq.append(nxt)
    for n in graph.nodes:
        level.setdefault(n.id, 0)

    by_level: Dict[int, List[str]] = defaultdict(list)
    for n in graph.nodes:
        by_level[level[n.id]].append(n.id)

    pos: Dict[str, Tuple[int, int]] = {}
    for lv in sorted(by_level):
        for i, nid in enumerate(by_level[lv]):
            pos[nid] = (lv * col_w, i * row_h)
    return pos, level


def _networkx_layout(graph: StructureGraph, width: int, height: int):
    """Use networkx spring layout when available (better for heavy graphs)."""
    try:
        import networkx as nx
    except ImportError:
        return None
    try:
        g = nx.Graph()
        g.add_nodes_from(n.id for n in graph.nodes)
        g.add_edges_from((e.source, e.target) for e in graph.edges)
        if g.number_of_nodes() == 0:
            return None
        pos = nx.spring_layout(g, seed=42)  # deterministic
        scale = min(width, height) * 0.45
        out = {}
        for nid, (x, y) in pos.items():
            out[nid] = (int(width / 2 + x * scale), int(height / 2 + y * scale))
        return out
    except Exception:
        return None


def force_layout(graph: StructureGraph, width: int = 1600, height: int = 1000) -> Dict[str, Tuple[int, int]]:
    """Layout positions. Prefers networkx spring layout when installed
    (faster/nicer for heavy graphs); otherwise a deterministic pure-Python
    Fruchterman-Reingold fallback (no extra dependency).
    """
    import math

    nx_pos = _networkx_layout(graph, width, height)
    if nx_pos is not None:
        return nx_pos

    ids = [n.id for n in graph.nodes]
    n = len(ids) or 1
    k = math.sqrt((width * height) / n)
    # deterministic initial placement on a circle
    pos = {}
    for i, nid in enumerate(ids):
        ang = 2 * math.pi * i / n
        pos[nid] = [width / 2 + (width / 3) * math.cos(ang),
                    height / 2 + (height / 3) * math.sin(ang)]

    edges = [(e.source, e.target) for e in graph.edges if e.source in pos and e.target in pos]
    iterations = min(220, max(20, 7000 // n))
    t = width / 10.0
    for _ in range(iterations):
        disp = {nid: [0.0, 0.0] for nid in ids}
        for i in range(n):
            a = ids[i]
            ax, ay = pos[a]
            for j in range(i + 1, n):
                b = ids[j]
                dx = ax - pos[b][0]
                dy = ay - pos[b][1]
                dist = math.hypot(dx, dy) or 0.01
                force = k * k / dist
                ux, uy = dx / dist, dy / dist
                disp[a][0] += ux * force
                disp[a][1] += uy * force
                disp[b][0] -= ux * force
                disp[b][1] -= uy * force
        for a, b in edges:
            dx = pos[a][0] - pos[b][0]
            dy = pos[a][1] - pos[b][1]
            dist = math.hypot(dx, dy) or 0.01
            force = dist * dist / k
            ux, uy = dx / dist, dy / dist
            disp[a][0] -= ux * force
            disp[a][1] -= uy * force
            disp[b][0] += ux * force
            disp[b][1] += uy * force
        for nid in ids:
            dx, dy = disp[nid]
            d = math.hypot(dx, dy) or 0.01
            pos[nid][0] += dx / d * min(d, t)
            pos[nid][1] += dy / d * min(d, t)
        t *= 0.95
    return {nid: (int(p[0]), int(p[1])) for nid, p in pos.items()}


NODE_KIND_COLORS = {
    "dir": "#64748b",
    "file": "#3b82f6",
    "class": "#f37021",
    "function": "#22a06b",
    "method": "#8b5cf6",
    "module": "#eab308",
    "section": "#ec4899",
    "json_key": "#06b6d4",
}

# The relationship (edge) types the builders emit, each with a distinct colour
# so the graph doesn't just show anonymous lines — every edge now carries a
# defined, colour-coded meaning (shown as a label on the edge + in the legend).
#   contains   — a folder/file holds another folder/file (tree structure)
#   defines    — a file/module defines a class or top-level function
#   method     — a class owns a method
#   imports    — a module imports another module
#   subsection — a doc/JSON section nests a subsection/key
EDGE_KIND_COLORS = {
    "contains": "#7c8aa0",
    "defines": "#22a06b",
    "method": "#8b5cf6",
    "imports": "#eab308",
    "subsection": "#ec4899",
}