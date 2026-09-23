"""Which section of which document documents which lines of which file.

`features` answers "does this claim resolve?" one claim at a time. That is
the right unit for checking a document and the wrong unit for reading one.
Nobody wants to know that `_apply_budget` exists; they want to know that
**`function_list.md` §1.1 "Token Usage & Cost" is about
`ui/dashboard_tab.py` lines 180-300**, and that nothing in the whole
documentation set says anything about `core/co4e_runner.py`.

So this module aggregates claims up to the unit a person actually wrote —
the section — and down to the unit a person actually reads — a line range.
Both directions are built from the same table:

    section  ->  (file, first line, last line, the symbols in between)
    file     ->  the sections that document it, and how much of it they reach

Everything here is arithmetic over `features.json` and `corpus.json`. No
model, no embedding, no keyword matching: a section points at a file
because the team wrote a function's name in it and that function is at
those lines. A link is either there or it is not, and you can open the two
files and see it.

The third output is the one that is hard to get any other way: **code no
section reaches**. `shadow` finds code no ticket claims and `drift` finds
claims no code supports; this finds code nobody documented, which is a
different question with a different owner.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from tracelink.artifacts import (
    Corpus, Feature, FeatureMap, MODALITY_GROUNDED, ROLE_CODE, ROLE_TEST,
)
from tracelink.config import DocsConfig

# How many lines either side of a symbol still count as "the same region".
# Two methods four lines apart are one area of a file to a reader; printing
# them as two ranges is technically true and useless.
STITCH = 12


@dataclass
class Region:
    """A contiguous stretch of one file that one doc section accounts for."""

    path: str
    start: int
    end: int
    symbols: list[str] = field(default_factory=list)

    @property
    def lines(self) -> int:
        """Zero when we know the file but not where in it.

        `start == 0` is the analyser saying it had no line numbers. Counting
        that as one line would put a file-level guess into the same column
        as a located range.
        """
        return 0 if not self.start else max(0, self.end - self.start + 1)

    @property
    def located(self) -> bool:
        return bool(self.start)

    def __str__(self) -> str:
        return f"{self.path}:{self.start}-{self.end}" if self.start else self.path


@dataclass
class Section:
    """One heading of one document, and the code it pins.

    The unit is the *deepest* heading that has rows under it, because that
    is the unit the author treated as one thing. Rolling up to the document
    loses the distinction between six `## DASHBOARD` subsections; splitting
    down to the row gives 2,716 entries nobody will read.
    """

    key: str                     # "doc.md#Heading / Sub-heading"
    doc: str
    heading: list[str]
    modality: str
    line: int                    # first line of the section in the document
    rows: int                    # feature rows beneath it
    claims: int
    resolved: int
    #: Resolved, but to too many symbols to point anywhere. Counted rather
    #: than dropped: "this section names 9 things and 6 of them are called
    #: `refresh`" is a fact about the document worth seeing.
    ambiguous: int = 0
    regions: list[Region] = field(default_factory=list)

    @property
    def title(self) -> str:
        return " / ".join(h for h in self.heading if h) or self.doc

    @property
    def files(self) -> list[str]:
        return sorted({r.path for r in self.regions})

    @property
    def lines_covered(self) -> int:
        return sum(r.lines for r in self.regions)

    @property
    def symbols_pinned(self) -> int:
        """How many distinct symbols this section puts a finger on.

        The ranking metric, in preference to `lines_covered`. A section
        naming one god-class covers 1,670 lines and locates nothing; one
        naming eight methods covers 300 and locates eight things.
        """
        return len({s for r in self.regions for s in r.symbols})


@dataclass
class FileCoverage:
    """One source file, seen from the documentation's side.

    Two measures, because they disagree and the disagreement is the point.
    A section that writes `ChatPanel` covers lines 69-1738 — the whole file
    — and by lines is total coverage. It has told you nothing about any of
    the 71 methods inside. `symbols_named` is the honest one; `fraction` is
    kept beside it so the gap between them is visible rather than a choice
    somebody made silently.
    """

    path: str
    lines: int
    symbol_lines: int            # lines belonging to some named symbol
    documented_lines: int
    symbols: int = 0             # named symbols in the file
    symbols_named: int = 0       # of those, ones a section names individually
    sections: list[str] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        """Of the lines that *could* be documented, how many are."""
        return self.documented_lines / self.symbol_lines if self.symbol_lines else 0.0

    @property
    def precision(self) -> float:
        """Of the symbols in the file, how many are individually named."""
        return self.symbols_named / self.symbols if self.symbols else 0.0


@dataclass
class DocMap:
    sections: list[Section] = field(default_factory=list)
    files: list[FileCoverage] = field(default_factory=list)

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)

    @property
    def undocumented(self) -> list[FileCoverage]:
        return [f for f in self.files if not f.sections]


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------

def section_key(f: Feature) -> str:
    return f"{f.doc}#" + " / ".join(h for h in f.heading if h)


def _merge(spans: list[tuple[int, int, str]], path: str,
           stitch: int = STITCH) -> list[Region]:
    """Overlapping or near-adjacent symbol spans -> readable regions."""
    if not spans:
        return []
    spans = sorted(spans)
    out: list[Region] = []
    start, end, name = spans[0]
    names = [name]
    for s, e, n in spans[1:]:
        if s <= end + stitch:
            end = max(end, e)
            names.append(n)
        else:
            out.append(Region(path=path, start=start, end=end,
                              symbols=sorted(set(names))))
            start, end, names = s, e, [n]
    out.append(Region(path=path, start=start, end=end, symbols=sorted(set(names))))
    return out


def build(fmap: FeatureMap, corpus: Corpus,
          cfg: DocsConfig | None = None) -> DocMap:
    """The whole map, in one pass over resolved claims."""
    cfg = cfg or DocsConfig()
    spans = {s.sid: s for s in corpus.symbols}

    grouped: dict[str, list[Feature]] = collections.defaultdict(list)
    for f in fmap.features:
        grouped[section_key(f)].append(f)

    sections: list[Section] = []
    covering: dict[str, set[str]] = collections.defaultdict(set)
    covered_spans: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    named_symbols: dict[str, set[str]] = collections.defaultdict(set)

    for key, feats in sorted(grouped.items()):
        head = feats[0]
        per_file: dict[str, list[tuple[int, int, str]]] = collections.defaultdict(list)
        n_claims = n_ok = n_ambiguous = 0
        for f in feats:
            for c in f.claims:
                if c.kind == "docref":
                    continue
                n_claims += 1
                if not c.resolved:
                    continue
                n_ok += 1
                # A name matching half the repository is a convention, not a
                # location. `__init__` resolved to 166 constructors and gave
                # a report in which every section documented every file.
                if len(c.sids) > cfg.max_pin_targets:
                    n_ambiguous += 1
                    continue
                if c.sids:
                    for sid in c.sids:
                        sym = spans.get(sid)
                        if sym is None:
                            continue
                        named_symbols[sym.path].add(sym.name)
                        if sym.line:
                            per_file[sym.path].append(
                                (sym.line, max(sym.end_line, sym.line), sym.name))
                        else:
                            # Analyser gave no lines; the file is still the
                            # honest answer, and saying "1-0" would not be.
                            per_file.setdefault(sym.path, [])
                else:
                    for p in c.paths:
                        per_file.setdefault(p, [])

        regions: list[Region] = []
        for path, sp in sorted(per_file.items()):
            # No spans means the analyser gave no line numbers, not that the
            # section is unrelated to the file. Emit an unlocated region so
            # the link survives; dropping it silently lost the whole map on
            # a corpus built before symbols carried lines.
            merged = _merge(sp, path) or [Region(path=path, start=0, end=0)]
            regions.extend(merged)
            covering[path].add(key)
            covered_spans[path].extend((r.start, r.end) for r in merged if r.located)

        sections.append(Section(
            key=key, doc=head.doc, heading=head.heading, modality=head.modality,
            line=min(f.line for f in feats), rows=len(feats),
            claims=n_claims, resolved=n_ok, ambiguous=n_ambiguous, regions=regions,
        ))

    # ---- the code side -------------------------------------------------
    by_path: dict[str, list] = collections.defaultdict(list)
    for s in corpus.symbols:
        by_path[s.path].append(s)

    files: list[FileCoverage] = []
    for sf in corpus.files:
        if sf.role not in (ROLE_CODE, ROLE_TEST):
            continue
        syms = by_path.get(sf.path, [])
        sym_lines = _span_total([(s.line, max(s.end_line, s.line))
                                 for s in syms if s.line])
        doc_lines = _span_total(covered_spans.get(sf.path, []))
        files.append(FileCoverage(
            path=sf.path,
            lines=_line_count(sf),
            symbol_lines=sym_lines,
            documented_lines=min(doc_lines, sym_lines) if sym_lines else doc_lines,
            symbols=len(syms),
            symbols_named=len(named_symbols.get(sf.path, ())),
            sections=sorted(covering.get(sf.path, ())),
        ))

    return DocMap(sections=sections, files=sorted(files, key=lambda f: f.path))


def _line_count(sf) -> int:
    """Approximate, and only ever shown as context for a ratio.

    The corpus stores byte size, not line count, and re-reading 172 files to
    print one column is not worth it. 34 bytes per line is the measured mean
    for this corpus; the number is never used in a denominator.
    """
    return max(1, sf.size // 34)


def _span_total(spans: list[tuple[int, int]]) -> int:
    """Total distinct lines covered by possibly-overlapping spans."""
    if not spans:
        return 0
    total = 0
    spans = sorted(spans)
    cur_s, cur_e = spans[0]
    for s, e in spans[1:]:
        if s <= cur_e + 1:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
    return total + cur_e - cur_s + 1


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------

def sections_for(dm: DocMap, path: str) -> list[tuple[Section, list[Region]]]:
    """Every section that reaches into one file, with the lines it reaches."""
    out = []
    for s in dm.sections:
        rs = [r for r in s.regions if r.path == path]
        if rs:
            out.append((s, sorted(rs, key=lambda r: r.start)))
    return sorted(out, key=lambda sr: -sum(r.lines for r in sr[1]))


def summary(dm: DocMap) -> dict:
    pinned = [s for s in dm.sections if s.regions]
    grounded = [s for s in pinned if s.modality == MODALITY_GROUNDED]
    with_lines = [r for s in dm.sections for r in s.regions if r.lines > 1]
    documented = [f for f in dm.files if f.sections]
    return {
        "sections": len(dm.sections),
        "sections_pinned_to_code": len(pinned),
        "sections_from_grounded_docs": len(grounded),
        "regions": len(with_lines),
        "files": len(dm.files),
        "files_documented": len(documented),
        "files_undocumented": len(dm.undocumented),
        "median_line_coverage_of_documented_files": _median(
            [f.fraction for f in documented]),
        "median_symbol_precision_of_documented_files": _median(
            [f.precision for f in documented]),
        "ambiguous_claims": sum(s.ambiguous for s in dm.sections),
    }


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2
