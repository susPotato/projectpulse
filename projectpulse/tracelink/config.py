"""Every tunable in the pipeline, in one place.

The demo run taught us that absolute thresholds are the enemy: a `df <= 4`
rarity bar means "rare" on a 5,000-file repo and "appears in 31% of the
corpus" on a 13-file one. So bars are expressed as a *fraction of the
indexed corpus*, with a floor (tiny corpora) and a ceiling (huge ones).

Nothing here may name a project, a file or a language.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA_VERSION = 1


def corpus_bar(n_docs: int, fraction: float, floor: int, ceiling: int) -> int:
    """Document-frequency bar for "rare enough to be evidence".

    A token is informative when it appears in at most this many documents.
    Scales with the corpus instead of pretending 148 files is universal.
    """
    return max(floor, min(ceiling, math.ceil(fraction * n_docs)))


@dataclass
class RetrievalConfig:
    """Bars for the three matchers. Fractions are of the indexed corpus."""

    # Filename/path stems: highest-precision signal, so the widest bar.
    stem_fraction: float = 0.035
    stem_floor: int = 2
    stem_ceiling: int = 12

    # Symbol names: far noisier (a `center()` method is not a Notification
    # Center), so a harder bar.
    sym_fraction: float = 0.02
    sym_floor: int = 1
    sym_ceiling: int = 6

    # Prose content words in declarative files. The prose corpus is usually
    # tiny (a dozen yaml/md files), so the floor does the work here and the
    # fraction only matters once a project carries real documentation.
    prose_fraction: float = 0.02
    prose_floor: int = 2
    prose_ceiling: int = 6

    # A prose match needs this many shared rare words, plus one more per
    # `prose_vocab_step` words of the document's vocabulary (length
    # normalisation: a big document must share proportionally more).
    prose_min_shared: int = 3
    prose_vocab_step: int = 600

    # Minimum token length considered at all.
    min_token_len: int = 4

    # Tickets sharing a title head collapse onto identical candidate sets.
    # When a head is this common, fall back to the full title for tokens.
    head_collision_threshold: int = 3

    def stem_bar(self, n: int) -> int:
        return corpus_bar(n, self.stem_fraction, self.stem_floor, self.stem_ceiling)

    def sym_bar(self, n: int) -> int:
        return corpus_bar(n, self.sym_fraction, self.sym_floor, self.sym_ceiling)

    def prose_bar(self, n: int) -> int:
        return corpus_bar(n, self.prose_fraction, self.prose_floor, self.prose_ceiling)


@dataclass
class CorpusConfig:
    """How the code side is read."""

    # Prefer CodeWiki's multi-language dependency analyser; fall back to a
    # Python-only AST pass when it is not importable.
    prefer_codewiki: bool = True
    # Files above this size are indexed by path only — a 242 KB translation
    # blob is 9,649 strings and 4 functions, and its words mean nothing.
    data_size_bytes: int = 200_000
    # A document mentioning this fraction of all tickets describes everything
    # and therefore discriminates nothing. Detected, not hardcoded by name.
    hubdoc_ticket_fraction: float = 0.5
    exclude_dirs: tuple[str, ...] = (
        "__pycache__", ".git", "node_modules", ".venv", "venv",
        "dist", "build", ".idea", ".vscode", "target", "bin", "obj",
    )


@dataclass
class DocsConfig:
    """How a documentation set is read.

    The demo pipeline treated a docs folder as one thing: markdown that
    might mention a candidate file. On a real team's docs that is wrong in
    a way that manufactures false corroboration. Some documents describe
    what shipped; others specify what should be built *next*, in the same
    folder, in the same voice, often naming files that do not exist yet.
    Shown to an adjudicator as "architecture documentation", the second
    kind is an argument that the feature is already there.

    So modality is measured, from the fraction of the symbols and paths a
    document names that actually resolve in the corpus. Nothing here names
    a document, a folder or a language.
    """

    # Below this many code claims, a document is not making checkable
    # statements about code at all (a review policy, a definition of done).
    min_claims: int = 3
    # At or above this resolution a document is describing the code in front
    # of us. Below it, it is describing some other version of it.
    descriptive_resolution: float = 0.5

    # Rarity bar for the feature index, as a fraction of the feature count.
    # A feature row is one table line, so there are far more of them than
    # there are files — 2,716 rows from 39 documents here. The ceiling is
    # correspondingly higher: at the file-side ceiling of 8, a token had to
    # appear in 8 of 2,716 rows to count, the matcher fired for 8 tickets
    # of 173, and the stage was decorative. At the fraction (55) it fires
    # for 132.
    feature_fraction: float = 0.02
    feature_floor: int = 2
    feature_ceiling: int = 60
    # Rare words a ticket must share with a feature before they are linked.
    feature_min_shared: int = 2
    # Most features one ticket may be linked to. A ticket that matches
    # twenty rows has matched the document, not a feature.
    max_features_per_ticket: int = 4

    # A claim that resolves to more symbols than this names a convention,
    # not a location. A document writing `__init__` matches the constructor
    # of all 166 classes, and a map built from it pins every section to
    # every file — which reads as total coverage and is worth nothing. Such
    # claims still count as resolved (the name *is* in the code); they are
    # just not allowed to point at a line.
    max_pin_targets: int = 4

    def feature_bar(self, n: int) -> int:
        return corpus_bar(n, self.feature_fraction,
                          self.feature_floor, self.feature_ceiling)


@dataclass
class Stopwords:
    """Generic English/dev noise only.

    Deliberately excludes domain nouns. The demo's list carried `dialog`,
    `manager` and `integration`, which are meaningful in a desktop-app
    backlog and meaningless in a payments one — domain noise is discovered
    by the corpus-relative rarity bar, not declared here.
    """

    words: frozenset[str] = frozenset("""
    the and for with via per from new all use using based other auto full
    this that then than when where which what your you our are was were has
    have had not but its into out over under more most some such can could
    should would will shall may might must one two three also only just like
    about after before between during each both few many much any own same
    so no nor too very true false null none
    """.split())


@dataclass
class PipelineConfig:
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    corpus: CorpusConfig = field(default_factory=CorpusConfig)
    docs: DocsConfig = field(default_factory=DocsConfig)
    stopwords: Stopwords = field(default_factory=Stopwords)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stopwords"]["words"] = sorted(d["stopwords"]["words"])
        return d
