"""Stage 0b — read the code side.

Two implementations of one contract:

* :func:`codewiki_symbols` — CodeWiki's dependency analyser. Free, offline,
  no API key, and it speaks Python, Java, Kotlin, C#, C, C++, PHP, Ruby,
  JavaScript and TypeScript. Its component ids (``file::Symbol``) are
  already the anchor format a verdict is asked to cite.
* :func:`ast_symbols` — a Python-only fallback for when CodeWiki is not
  installed. Honest about being a fallback; it does not pretend to coverage.

Role classification is *structural*, never by filename. The demo hardcoded
``assets/d3`` and ``i18n.py``; here a file is `data` because it is large and
low-symbol, and a `hubdoc` because it matches too much of the backlog —
which is a property Stage 1 measures, not something we can know up front.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path

from tracelink.artifacts import (
    Corpus, FileEdge, SourceFile, Symbol,
    ROLE_BINARY, ROLE_CODE, ROLE_DATA, ROLE_DECLARATIVE, ROLE_TEST,
)
from tracelink.config import CorpusConfig

logger = logging.getLogger(__name__)

BINARY_EXT = {".png", ".ico", ".icns", ".jpg", ".jpeg", ".gif", ".svg",
              ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".7z",
              ".exe", ".dll", ".so", ".dylib", ".pyc"}

DECLARATIVE_EXT = {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg",
                   ".md", ".rst", ".txt", ".skill", ".env", ".properties"}

CODE_EXT = {
    ".py": "python", ".java": "java", ".kt": "kotlin", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".php": "php", ".rb": "ruby", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
}

TEST_HINTS = ("test", "tests", "spec", "specs", "testing", "__tests__")


def looks_like_test(rel: str) -> bool:
    """Public: `delivery` needs the same rule, and two copies would drift.

    `tests` plural was missing, which is the commonest convention there
    is — so `tests/fakes/fake_provider.py` was classified as production
    source, and a helper under `tests/` counted against a "no file over
    400 lines" rule written for production code.

    Matched as whole path segments, not substrings: `contest/` is not a
    test directory and `core/latest_run.py` is not a test file.
    """
    parts = rel.lower().split("/")
    stem = parts[-1].rsplit(".", 1)[0]
    if any(p in TEST_HINTS for p in parts[:-1]):
        return True
    return stem.startswith("test_") or stem.endswith(("_test", "_spec", ".test", ".spec"))


def _is_vendored(rel: str) -> bool:
    """Minified or bundled third-party code: not this team's work."""
    low = rel.lower()
    return ".min." in low or "/vendor/" in low or low.startswith("vendor/")


def classify(rel: str, size: int, cfg: CorpusConfig) -> tuple[str, str]:
    """Return (role, language). Structural signals only."""
    suffix = Path(rel).suffix.lower()
    if suffix in BINARY_EXT:
        return ROLE_BINARY, ""
    if _is_vendored(rel):
        return ROLE_DATA, CODE_EXT.get(suffix, "")
    if suffix in CODE_EXT:
        lang = CODE_EXT[suffix]
        if size > cfg.data_size_bytes:
            # A very large source file is almost always generated (string
            # tables, bindings). Index its path, do not read its words.
            return ROLE_DATA, lang
        return (ROLE_TEST if looks_like_test(rel) else ROLE_CODE), lang
    if suffix in DECLARATIVE_EXT:
        if size > cfg.data_size_bytes:
            return ROLE_DATA, ""
        return ROLE_DECLARATIVE, ""
    return ROLE_DATA, ""


def walk_files(root: Path, cfg: CorpusConfig) -> list[SourceFile]:
    out: list[SourceFile] = []
    skip = set(cfg.exclude_dirs)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                size = p.stat().st_size
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            role, lang = classify(rel, size, cfg)
            out.append(SourceFile(path=rel, role=role, size=size, language=lang))
    return sorted(out, key=lambda f: f.path)


# --------------------------------------------------------------------------
# Symbol extraction
# --------------------------------------------------------------------------

def codewiki_symbols(root: Path) -> tuple[list[Symbol], list[FileEdge]] | None:
    """Symbols via CodeWiki's dependency analyser, or None if unavailable.

    CodeWiki keys components with the host OS separator, so ids arrive as
    ``core\\accounts.py::Account`` on Windows while every other artifact in
    this pipeline uses forward slashes. Normalising here is the single
    place that mismatch is allowed to exist.
    """
    try:
        os.environ.setdefault("LLM_API_KEY", "unused-analysis-is-local")
        from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder
        from codewiki.src.config import Config
    except Exception as exc:                      # noqa: BLE001
        logger.info("CodeWiki not importable (%s); using the Python-only fallback", exc)
        return None

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(
            repo_path=str(root),
            output_dir=tmp,
            dependency_graph_dir=str(Path(tmp) / "graphs"),
            docs_dir=str(Path(tmp) / "docs"),
            max_depth=2,
            llm_base_url="http://localhost/v1",
            llm_api_key="unused",
            main_model="unused",
            cluster_model="unused",
        )
        try:
            components, _leaves = DependencyGraphBuilder(cfg).build_dependency_graph()
        except Exception as exc:                  # noqa: BLE001
            logger.warning("CodeWiki analysis failed (%s); falling back", exc)
            return None

    symbols: list[Symbol] = []
    edge_weight: dict[tuple[str, str], int] = {}
    for cid, node in components.items():
        norm = cid.replace("\\", "/")
        if "::" not in norm:
            continue
        path, name = norm.split("::", 1)
        symbols.append(Symbol(sid=norm, path=path, name=name,
                              kind=getattr(node, "component_type", "") or "",
                              line=getattr(node, "start_line", 0) or 0,
                              end_line=getattr(node, "end_line", 0) or 0))
        # Real import/call edges, collapsed to the file level. Self-edges are
        # dropped: a file using itself says nothing about coupling.
        for dep in getattr(node, "depends_on", None) or ():
            dst = dep.replace("\\", "/").split("::")[0]
            if dst and dst != path:
                edge_weight[(path, dst)] = edge_weight.get((path, dst), 0) + 1

    edges = [FileEdge(src=a, dst=b, weight=w)
             for (a, b), w in sorted(edge_weight.items())]
    return symbols, edges


def ast_symbols(root: Path, files: list[SourceFile]) -> list[Symbol]:
    """Python-only fallback: classes and functions via the stdlib parser."""
    symbols: list[Symbol] = []
    for f in files:
        if f.language != "python" or f.role not in (ROLE_CODE, ROLE_TEST):
            continue
        try:
            tree = ast.parse((root / f.path).read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                kind = "class"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "function"
            else:
                continue
            symbols.append(Symbol(
                sid=Symbol.make_sid(f.path, node.name),
                path=f.path, name=node.name, kind=kind,
                line=getattr(node, "lineno", 0) or 0,
                end_line=getattr(node, "end_lineno", 0) or getattr(node, "lineno", 0) or 0))
    return symbols


def build(root: str | Path, cfg: CorpusConfig | None = None) -> Corpus:
    cfg = cfg or CorpusConfig()
    root = Path(root).resolve()
    files = walk_files(root, cfg)

    got = codewiki_symbols(root) if cfg.prefer_codewiki else None
    if got is None:
        # The fallback parses names but not relationships, so there are no
        # edges. Saying "no edges" is right; inventing them from imports we
        # did not resolve would be worse than having none.
        symbols, edges = ast_symbols(root, files), []
        analyzer = "python-ast"
    else:
        symbols, edges = got
        analyzer = "codewiki"

    known = {f.path for f in files}
    kept = [s for s in symbols if s.path in known]
    if len(kept) != len(symbols):
        logger.debug("dropped %d symbols outside the walked tree", len(symbols) - len(kept))

    known_paths = {f.path for f in files}
    kept_edges = [e for e in edges
                  if e.src in known_paths and e.dst in known_paths]
    return Corpus(files=files, symbols=kept, root=str(root), analyzer=analyzer,
                  edges=kept_edges)
