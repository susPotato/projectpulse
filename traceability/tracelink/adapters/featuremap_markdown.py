"""Read a folder of markdown as a *feature map*, not as prose.

The interesting documents a team writes are not paragraphs about the
system; they are tables and bullet lists in which a heading names an area,
a row names a capability, and a code span names the function behind it.
That shape is a traceability artifact the team built by hand and then filed
under `docs/`. Reading it as prose and grepping for filenames — which is
what the first describer did — throws away the only part that is precise.

So the unit here is the **row**, not the file:

    ## 3. WORKSPACE                     <- heading stack
    ### 3.1 Projects                    <-
    | 3.1.4 | `_pick_folder()` | Choose the workspace folder |
      ^ number  ^ claim           ^ label / description

Nothing in this module knows what project it is reading. It assumes only
that the document is markdown, that headings nest, and that a code span is
how people write the name of a thing in the code. A document that matches
none of that yields a feature per heading with no claims, which is the
right answer for a governance policy and costs nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from tracelink.adapters._markdown import (
    CODE_SPAN, KIND_BULLET, KIND_HEADING, KIND_ROW,
    clean_label as _clean_label, split_row as _split_row, walk,
)
from tracelink.artifacts import Claim, Feature, FeatureMap

# A code span that is plausibly the name of something in the code, rather
# than a word the author chose to set in monospace. One of: it carries a
# call suffix, an underscore, a dot, a path separator, or internal capitals.
IDENTIFIER = re.compile(r"^[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)*(?:\s*\([^)]*\))?$")
PATHLIKE = re.compile(r"^[\w./\\-]+\.[A-Za-z][A-Za-z0-9]{0,4}$")
HAS_CAMEL = re.compile(r"[a-z][A-Z]")
# A trailing line locator on a path: `:35`, `#L35`, `:35-40`.
LOCATOR = re.compile(r"(?::|#L)\d+(?:[-:]\d+)?$")
# A single capitalised word: a one-word class name, or just a word.
CAPITALISED = re.compile(r"^[A-Z][a-z][A-Za-z0-9]*$")

#: Provisional: an identifier only if the corpus turns out to have one.
KIND_MAYBE = "symbol?"


def normalise_claim(raw: str) -> tuple[str, str] | None:
    """`_ai_analyze()` -> ("_ai_analyze", "symbol"), or None if it is prose.

    The filter is deliberately conservative in one direction only: a claim
    that slips through and does not resolve shows up as documentation drift,
    which is a *reported* outcome, so a loose filter would quietly inflate a
    headline number. Requiring a structural signal — a call, an underscore,
    a dot, a slash, internal capitals — keeps ordinary words in backticks
    (`week`, `cost`, `status`) out of the drift count.

    One case does not fit that rule in either direction. A class named
    `Billing`, `Config` or `Session` has no internal capital, so it looks
    exactly like a capitalised English word — and both readings are common
    in the same document. Rejecting them loses every single-word class;
    accepting them fills `drift` with words like `Notes` and `Function`.
    Such spans are therefore returned provisionally, as `symbol?`, and
    :func:`tracelink.features.resolve` keeps them only if the code turns
    out to contain one. They can never become drift, because drift is the
    report of claims that did *not* resolve.
    """
    s = raw.strip().strip("`").strip()
    if not s or len(s) > 120:
        return None
    # `ui/dashboard_tab.py:35` and `ui/dashboard_tab.py#L35` are one file
    # with a locator attached — the convention every editor and code host
    # uses. Left on, the trailing digits stop the path matching at all, and
    # a screenshot manifest whose every row is anchored that way
    # contributed 54 rows and nothing else.
    s = LOCATOR.sub("", s)
    if PATHLIKE.match(s) and ("/" in s or "\\" in s):
        return s.replace("\\", "/"), "path"
    if not IDENTIFIER.match(s):
        return None
    name = s.split("(", 1)[0].strip()
    if len(name) < 3:
        return None
    structural = ("(" in s) or ("_" in name) or ("." in name) or HAS_CAMEL.search(name)
    if not structural:
        if CAPITALISED.match(name):
            return name, KIND_MAYBE
        return None
    # A bare `something.py` is a path claim even without a directory.
    if PATHLIKE.match(name) and name.rsplit(".", 1)[-1].isalpha() and "_" not in name[-4:]:
        suffix = name.rsplit(".", 1)[-1]
        if suffix in ("py", "md", "json", "yaml", "yml", "ts", "js", "java", "kt",
                      "cs", "go", "rb", "php", "rs", "cpp", "c", "h", "tsx", "jsx"):
            return name, "path"
    return name, "symbol"


def _claims_in(text: str) -> list[Claim]:
    out: list[Claim] = []
    seen: set[str] = set()
    for raw in CODE_SPAN.findall(text):
        got = normalise_claim(raw)
        if not got:
            continue
        name, kind = got
        if name in seen:
            continue
        seen.add(name)
        out.append(Claim(text=raw.strip(), name=name, kind=kind))
    return out


def _row_label_and_description(cells: list[str]) -> tuple[str, str]:
    """Pick which cell is the name and which is the explanation.

    The convention is stable across trackers and wikis: a narrow cell
    carrying the identifier, and a wider one carrying the sentence. Choosing
    by *content* rather than by column index survives tables that put the
    description first, and tables with a leading numbering column.
    """
    scored = [(i, c) for i, c in enumerate(cells) if _clean_label(c)]
    if not scored:
        return "", ""
    with_code = [(i, c) for i, c in scored if CODE_SPAN.search(c)]
    if with_code:
        li, lcell = with_code[0]
    else:
        li, lcell = max(scored, key=lambda ic: len(ic[1]))
    rest = [c for i, c in scored if i != li]
    # Drop a pure numbering cell: it is navigation, not meaning.
    rest = [c for c in rest if not re.fullmatch(r"[\d.)\s]+", c)]
    return _clean_label(lcell), _clean_label(" — ".join(rest))


def parse_document(text: str, doc: str, start_index: int = 0) -> list[Feature]:
    """Features in one markdown document, in reading order.

    Traversal is :func:`tracelink.adapters._markdown.walk`; everything here
    is the judgement of what a feature *is*, which is this adapter's alone.
    """
    features: list[Feature] = []
    heading: tuple[str, ...] = ()
    # Prose accumulated under the current heading that is not a row or a
    # bullet — it becomes the heading's own feature if nothing else did.
    block: list[str] = []
    block_line = 0
    heading_has_rows = False

    def flush_heading_block() -> None:
        nonlocal block
        body = " ".join(block).strip()
        block = []
        if not heading:
            return
        claims = _claims_in(body)
        # A heading with rows beneath it is already represented by them;
        # emitting the heading too would double-count every claim.
        if heading_has_rows and not claims:
            return
        if not body and not claims and heading_has_rows:
            return
        features.append(Feature(
            fid="", doc=doc, heading=list(heading[:-1]),
            label=_clean_label(heading[-1]),
            description=_clean_label(body)[:600],
            line=block_line or 0, claims=claims,
        ))

    for b in walk(text):
        if b.kind == KIND_HEADING:
            flush_heading_block()
            heading = b.heading
            heading_has_rows = False
            block_line = b.line
            continue

        if b.kind == KIND_ROW:
            label, desc = _row_label_and_description(list(b.cells))
            claims = _claims_in(b.raw)
            if not label and not claims:
                continue
            heading_has_rows = True
            features.append(Feature(
                fid="", doc=doc, heading=list(heading), label=label,
                description=desc, line=b.line, claims=claims,
            ))
            continue

        if b.kind == KIND_BULLET:
            claims = _claims_in(b.text)
            if claims:
                heading_has_rows = True
                features.append(Feature(
                    fid="", doc=doc, heading=list(heading),
                    label=_clean_label(b.text)[:160],
                    description="", line=b.line, claims=claims,
                ))
                continue
            # A bullet joins the prose block without moving its start line:
            # the heading is still the better locator for the paragraph.
            block.append(b.text)
            continue

        if not block:
            block_line = b.line
        block.append(b.text)

    flush_heading_block()

    for i, f in enumerate(features):
        f.fid = f"F{start_index + i:04d}"
    return features


def read_docs(docs_dir: str | Path, exts: tuple[str, ...] = (".md",)) -> FeatureMap:
    """Every markdown document under `docs_dir`, recursively.

    Recursively, because the demo's describer globbed one level and so saw
    three files out of thirty-nine — every architecture decision record,
    every governance policy and every refactor report sat one directory
    down and was silently invisible.
    """
    root = Path(docs_dir)
    features: list[Feature] = []
    if not root.is_dir():
        return FeatureMap(root=str(root))
    paths = sorted(p for p in root.rglob("*") if p.suffix.lower() in exts and p.is_file())
    for p in paths:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        features.extend(parse_document(text, rel, start_index=len(features)))
    return FeatureMap(root=str(root), features=features)
