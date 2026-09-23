"""Stage 1a — inverted indexes over the corpus.

Three token sources, indexed **separately**. Folding them together was one
of the demo's silent score inflators: 1,697 function names mixed into the
filename index raised document frequency everywhere and pushed good stem
matches below the rarity bar.

Document frequency is stored raw. The rarity *bar* is applied at match
time, because it depends on the size of the corpus being searched.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

from tracelink.artifacts import (
    Corpus, ROLE_CODE, ROLE_DATA, ROLE_DECLARATIVE, ROLE_TEST,
)
from tracelink.config import PipelineConfig

WORD = re.compile(r"[a-z][a-z0-9]*")
# MainWindow / model_pricing / block-network -> component words
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
SPLIT = re.compile(r"[_\-. ]")


def split_identifier(name: str, stop: frozenset[str], min_len: int) -> list[str]:
    out = []
    for part in SPLIT.split(name):
        for w in CAMEL.findall(part):
            lw = w.lower()
            if len(lw) >= min_len and lw not in stop:
                out.append(lw)
    return out


def tokenize_text(text: str, stop: frozenset[str], min_len: int) -> set[str]:
    return {w for w in WORD.findall(text.lower())
            if len(w) >= min_len and w not in stop}


# A word a person typed that is really an identifier: `InvoiceEngine`,
# `task_scheduler`, `CsvExporter`.
COMPOUND = re.compile(r"\b(?=\w*[A-Z])[A-Za-z][A-Za-z0-9]*[A-Za-z0-9]\b|\b\w+_\w+\b")


def tokenize_query(text: str, stop: frozenset[str], min_len: int) -> set[str]:
    """Tokens from ticket text, split the way the *index* splits symbols.

    The index stores `InvoiceEngine` as {invoice, engine}, because that is
    how identifiers are searched. Lowercasing a ticket first turns the same
    word into the single token `invoiceengine`, which matches neither — so a
    ticket that names a class by its exact name could not find that class.
    Measured on synthetic data with known answers: 0 of 12 such tickets were
    retrieved before this, 12 of 12 after.

    Both forms are kept. The whole word still matches a file literally named
    after it, and the parts match the symbol index.
    """
    tokens = tokenize_text(text, stop, min_len)
    for compound in COMPOUND.findall(text):
        for part in split_identifier(compound, stop, min_len):
            tokens.add(part)
    return tokens


@dataclass
class Index:
    """Three inverted indexes plus the corpus size each is relative to."""

    stem: dict[str, set[str]] = field(default_factory=dict)
    sym: dict[str, set[str]] = field(default_factory=dict)
    prose: dict[str, set[str]] = field(default_factory=dict)
    #: Word -> feature ids. A fourth token source, and the only one whose
    #: documents are written in the same register as the tickets: a feature
    #: row says "Kéo thả task giữa các cột", and so does the ticket.
    feature: dict[str, set[str]] = field(default_factory=dict)
    n_indexed: int = 0                     # files eligible to be a candidate
    n_prose_docs: int = 0
    n_features: int = 0
    vocab_size: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "stem": {k: sorted(v) for k, v in self.stem.items()},
            "sym": {k: sorted(v) for k, v in self.sym.items()},
            "prose": {k: sorted(v) for k, v in self.prose.items()},
            "feature": {k: sorted(v) for k, v in self.feature.items()},
            "n_indexed": self.n_indexed,
            "n_prose_docs": self.n_prose_docs,
            "n_features": self.n_features,
            "vocab_size": self.vocab_size,
        }

    @staticmethod
    def from_json(d: dict) -> "Index":
        return Index(
            stem={k: set(v) for k, v in d["stem"].items()},
            sym={k: set(v) for k, v in d["sym"].items()},
            prose={k: set(v) for k, v in d["prose"].items()},
            feature={k: set(v) for k, v in d.get("feature", {}).items()},
            n_indexed=d["n_indexed"],
            n_prose_docs=d["n_prose_docs"],
            n_features=d.get("n_features", 0),
            vocab_size=d.get("vocab_size", {}),
        )


# Roles that can be returned as a candidate at all. `data` and `binary` are
# indexed by path only or not at all; `hubdoc` is decided downstream.
CANDIDATE_ROLES = (ROLE_CODE, ROLE_TEST, ROLE_DECLARATIVE, ROLE_DATA)


def build(corpus: Corpus, cfg: PipelineConfig | None = None) -> Index:
    cfg = cfg or PipelineConfig()
    stop = cfg.stopwords.words
    min_len = cfg.retrieval.min_token_len
    root = Path(corpus.root)

    idx = Index()
    eligible = [f for f in corpus.files if f.role in CANDIDATE_ROLES]
    idx.n_indexed = len(eligible)

    stem: dict[str, set[str]] = collections.defaultdict(set)
    sym: dict[str, set[str]] = collections.defaultdict(set)
    prose: dict[str, set[str]] = collections.defaultdict(set)

    for f in eligible:
        p = Path(f.path)
        # Filename and its immediate directory: the highest-precision signal
        # there is, because humans name files after the feature.
        for tok in split_identifier(p.stem, stop, min_len):
            stem[tok].add(f.path)
        if p.parent != Path("."):
            for tok in split_identifier(p.parent.name, stop, min_len):
                stem[tok].add(f.path)

    for s in corpus.symbols:
        for tok in split_identifier(s.name, stop, min_len):
            sym[tok].add(s.path)

    prose_files = [f for f in eligible if f.role == ROLE_DECLARATIVE]
    idx.n_prose_docs = len(prose_files)
    for f in prose_files:
        try:
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        words = tokenize_text(text, stop, min_len)
        idx.vocab_size[f.path] = len(words)
        for w in words:
            prose[w].add(f.path)
        # Config keys read like feature names; index them as stems too.
        for key in re.findall(r"^\s*([A-Za-z_][\w\-]*)\s*:", text, re.M):
            for tok in split_identifier(key, stop, min_len):
                stem[tok].add(f.path)

    idx.stem = dict(stem)
    idx.sym = dict(sym)
    idx.prose = dict(prose)
    return idx


def add_features(idx: Index, fmap: "FeatureMap") -> Index:
    """Index a feature map onto an existing corpus index.

    Kept separate from :func:`build` because documentation is optional and
    because the two have different lifetimes: re-reading the docs should
    not mean re-analysing the repository.

    A feature's own words *and* its heading path are indexed. The heading
    path is what lets a ticket reading "Dashboard — token cost chart" reach
    a row filed under `## DASHBOARD / ### Token Usage & Cost`, where the row
    itself only says "Vẽ biểu đồ spline". Without the ancestors, the row is
    unfindable from the vocabulary a ticket actually uses.
    """
    from tracelink.config import PipelineConfig

    cfg = PipelineConfig()
    stop = cfg.stopwords.words
    min_len = cfg.retrieval.min_token_len

    feature: dict[str, set[str]] = collections.defaultdict(set)
    for f in fmap.features:
        words = tokenize_text(f.text, stop, min_len)
        # Identifier-shaped words from the claims, split the way the symbol
        # index splits them, so `_ai_analyze` is reachable as {analyze}.
        for c in f.claims:
            words.update(split_identifier(c.name, stop, min_len))
        for w in words:
            feature[w].add(f.fid)
    idx.feature = dict(feature)
    idx.n_features = len(fmap.features)
    return idx
