"""Stage 2 — say what the candidate code does, with anchors a verdict can cite.

An *interface*, not a CodeWiki wrapper. The demo's doc ablation found that
withholding generated docs changed 9 of 12 verdicts not at all, 2 only in
confidence, and 1 materially — documentation value is concentrated, not
uniform. So the describer is chosen per ticket rather than per run, and the
cheap one is the default.

Implementations here are all free. A generating describer (one that calls
CodeWiki to produce a doc that does not exist yet) belongs behind the same
interface and is deliberately not written until the free ones are measured.

That ablation was run against *generated* docs. Documentation a team wrote
itself behaves differently enough to need its own describer: it is organised
by capability rather than by module, it is written in the same words as the
tickets, and — the part that makes it dangerous as well as useful — a large
part of it describes a version of the system that is not the one on disk.
:class:`DocFeature` is that describer, and every claim it shows has already
been checked against the corpus before the model sees it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from tracelink.artifacts import (
    Corpus, Description, FeatureMap, TicketCandidates,
    MODALITY_GROUNDED, MODALITY_PROCESS, MODALITY_UNGROUNDED,
)

# Extensions we are willing to quote as source.
FENCE = {
    ".py": "python", ".java": "java", ".kt": "kotlin", ".cs": "csharp",
    ".js": "javascript", ".ts": "typescript", ".tsx": "tsx", ".rb": "ruby",
    ".go": "go", ".rs": "rust", ".php": "php", ".c": "c", ".cpp": "cpp",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json", ".md": "markdown",
}


class Describer(Protocol):
    name: str

    def describe(self, tc: TicketCandidates, corpus: Corpus) -> Description:
        ...


def _anchors(tc: TicketCandidates) -> set[str]:
    """The tokens that made these files candidates — where to look inside them."""
    out: set[str] = set()
    for c in tc.candidates:
        for e in c.evidence:
            if e.strong:
                out.update(a for a in e.anchor.split(",") if a)
    return out


def _best_window(lines: list[str], anchors: set[str], size: int) -> tuple[int, int]:
    """The `size`-line window containing the most anchor mentions.

    The demo's adjudicator took `lines[:220]` — the top of the file. A symbol
    at line 400 was therefore never shown to the model, which then answered
    "unverified" about code that was right there. Locating the window is the
    single highest-value thing this stage does.
    """
    if len(lines) <= size:
        return 0, len(lines)
    if not anchors:
        return 0, size

    pattern = re.compile("|".join(re.escape(a) for a in sorted(anchors)), re.I)
    hits = [1 if pattern.search(line) else 0 for line in lines]
    if not any(hits):
        return 0, size

    # Sliding window over a prefix sum.
    running = sum(hits[:size])
    best, best_at = running, 0
    for i in range(1, len(lines) - size + 1):
        running += hits[i + size - 1] - hits[i - 1]
        if running > best:
            best, best_at = running, i
    # Back off to a blank line so the excerpt starts at a plausible boundary.
    start = best_at
    for i in range(best_at, max(0, best_at - 12), -1):
        if not lines[i].strip():
            start = i + 1
            break
    return start, min(len(lines), start + size)


class RawSource:
    """Quote the candidate files themselves, windowed around the anchors.

    Free, works on any language, and never depends on a doc existing. This is
    the describer that makes the pipeline runnable on a repository nobody has
    documented — which is the normal case.
    """

    name = "raw-source"

    def __init__(self, max_files: int = 3, window: int = 160, head: int = 24):
        self.max_files = max_files
        self.window = window
        self.head = head

    def describe(self, tc: TicketCandidates, corpus: Corpus) -> Description:
        root = Path(corpus.root)
        anchors = _anchors(tc)
        paths = tc.strong_paths()[: self.max_files]
        chunks: list[str] = []
        used: list[str] = []
        cited: list[str] = []

        by_path: dict[str, list[str]] = {}
        for s in corpus.symbols:
            by_path.setdefault(s.path, []).append(s.name)

        for rel in paths:
            p = root / rel
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            used.append(rel)
            lo, hi = _best_window(lines, anchors, self.window)

            body: list[str] = []
            if lo > self.head:
                body.append(f"# --- lines 1-{self.head} ---")
                body.extend(lines[: self.head])
                body.append(f"# ... [{lo - self.head} lines omitted] ...")
                body.append(f"# --- lines {lo + 1}-{hi} ---")
            body.extend(lines[lo:hi])
            if hi < len(lines):
                body.append(f"# ... [{len(lines) - hi} lines omitted] ...")

            lang = FENCE.get(Path(rel).suffix.lower(), "")
            names = by_path.get(rel, [])
            cited.extend(f"{rel}::{n}" for n in names[:40])
            header = f"### {rel} ({len(lines)} lines"
            if names:
                header += f", {len(names)} symbols: {', '.join(sorted(names)[:8])}"
                if len(names) > 8:
                    header += ", …"
            header += ")"
            chunks.append(f"{header}\n```{lang}\n" + "\n".join(body) + "\n```")

        text = "\n\n".join(chunks) or "(no candidate files could be read)"
        return Description(paths=used, text=text, source=self.name, anchors=cited)


class DocFeature:
    """Quote the team's own row for this ticket, with every claim checked.

    The describer the other two cannot be. `RawSource` shows code and asks a
    model to infer intent from it; `ExistingDocs` shows whatever prose
    happens to name the file. This one shows the sentence a person on the
    team wrote about this exact capability, next to the function they said
    implements it, next to whether that function is actually there.

    Three properties matter, and all three are about *not* overclaiming:

    1. **Claims arrive already checked.** ``_apply_budget()`` is shown as
       resolved to a real sid or as absent, decided in Python before the
       call. The model is not asked to search for it and is not able to
       hallucinate that it found it.
    2. **Ungrounded documents are labelled as such, in the text.** A
       refactor plan describes the feature in confident present tense; shown
       unlabelled under "documentation", it is an argument for corroboration
       that no downstream check can catch. Here it arrives with its own
       measurement attached.
    3. **A near-match is shown when there is one.** "The document says
       `_submit_message`, this build has `submit`" is the difference between
       a missing feature and a renamed one, and it is not the adjudicator's
       job to guess which.
    """

    name = "doc-feature"

    BANNER = {
        MODALITY_GROUNDED:
            "This document describes the code in this repository "
            "(measured: most of the symbols it names are present).",
        MODALITY_UNGROUNDED:
            "WARNING — this document does NOT describe the repository you are "
            "being shown. Most of the symbols or files it names do not exist "
            "here: it is a design, a refactor plan, or a different build. "
            "Treat it as a statement of intent. It is NEVER evidence that "
            "something is implemented.",
        MODALITY_PROCESS:
            "This document is about how the team works, not about code.",
    }

    def __init__(self, fmap: FeatureMap, max_features: int = 4):
        self.fmap = fmap
        self.max_features = max_features

    def describe(self, tc: TicketCandidates, corpus: Corpus) -> Description:
        fids: list[str] = []
        for c in tc.candidates:
            for e in c.evidence:
                if e.feature and e.feature not in fids:
                    fids.append(e.feature)
        if not fids:
            return Description(paths=[], text="", source=self.name)

        index = self.fmap._index
        features = [f for f in (index.get(f) for f in fids) if f is not None]
        # Grounded rows first: the adjudicator reads earlier context as
        # framing for what follows, so the trustworthy statement goes first
        # and the plan arrives already relativised by it.
        # Grounded first, then by how much of the row is actually checkable.
        # A row naming four functions that exist says more than a paragraph
        # of prose naming one file, and the prompt has finite room.
        order = {MODALITY_GROUNDED: 0, MODALITY_PROCESS: 1, MODALITY_UNGROUNDED: 2}
        features.sort(key=lambda f: (order.get(f.modality, 3),
                                     -len(f.resolved_claims), f.fid))
        features = features[: self.max_features]

        chunks: list[str] = []
        paths: list[str] = []
        anchors: list[str] = []
        seen_docs: set[str] = set()

        for f in features:
            head = " / ".join(h for h in f.heading if h)
            lines = [f"### {f.doc}:{f.line} — {head}" if head
                     else f"### {f.doc}:{f.line}"]
            if f.doc not in seen_docs:
                seen_docs.add(f.doc)
                stat = next((d for d in self.fmap.docs if d.doc == f.doc), None)
                lines.append(f"_{self.BANNER.get(f.modality, '')}_")
                if stat and stat.signals:
                    lines.append(f"_Basis: {'; '.join(stat.signals)}._")
            lines.append("")
            lines.append(f"**{f.label}**" if f.label else "")
            if f.description:
                # Hard cap: some rows are a progress log, and the prompt has
                # better uses for the tokens than someone's sprint note.
                lines.append(f.description[:280]
                             + ("…" if len(f.description) > 280 else ""))
            if f.claims:
                lines.append("")
                lines.append("What the document says implements it, checked "
                             "against this repository:")
            for c in f.claims:
                if c.kind == "docref":
                    continue
                if c.resolved:
                    # A path claim resolves to files and has no symbol ids.
                    # Reading only `sids` printed nothing at all for those,
                    # so a row whose every claim was a filename arrived as a
                    # heading with an empty checklist under it.
                    targets = c.sids[:2] or c.paths[:2]
                    for t in targets:
                        lines.append(f"- `{c.name}` -> found: `{t}`")
                        anchors.append(t)
                    paths.extend(c.paths)
                elif c.near:
                    lines.append(f"- `{c.name}` -> NOT in this repository; "
                                 f"nearest name that is: `{c.near}`")
                else:
                    lines.append(f"- `{c.name}` -> NOT in this repository, "
                                 f"and nothing resembles it")
            chunks.append("\n".join(l for l in lines if l is not None))

        return Description(paths=sorted(set(paths)), text="\n\n".join(chunks),
                           source=self.name, anchors=anchors)


class ExistingDocs:
    """Excerpt any pre-existing markdown that mentions these files.

    The fallback for a project with documentation but no parseable feature
    structure — a wiki export, an ADR folder, CodeWiki output. Returns
    empty text when nothing covers the candidates, which is the common case
    and must not be disguised as coverage.

    Reads the tree **recursively**. Globbing one level is how the demo saw
    three files out of thirty-nine: every architecture decision record and
    every refactor report sat one directory down, and the describer reported
    "no documentation" about a project with a hundred pages of it.
    """

    name = "existing-docs"

    def __init__(self, docs_dir: str | Path, max_docs: int = 2, window: int = 70,
                 skip_docs: set[str] | None = None):
        self.docs_dir = Path(docs_dir)
        self.max_docs = max_docs
        self.window = window
        #: Docs-relative paths to leave out — used to withhold documents
        #: measured as not describing this corpus.
        self.skip_docs = skip_docs or set()
        self._cache: dict[str, list[str]] | None = None

    def _load(self) -> dict[str, list[str]]:
        if self._cache is None:
            self._cache = {}
            if self.docs_dir.is_dir():
                for p in sorted(self.docs_dir.rglob("*.md")):
                    rel = p.relative_to(self.docs_dir).as_posix()
                    if rel in self.skip_docs:
                        continue
                    self._cache[rel] = p.read_text(
                        encoding="utf-8", errors="ignore").splitlines()
        return self._cache

    def describe(self, tc: TicketCandidates, corpus: Corpus) -> Description:
        docs = self._load()
        paths = tc.strong_paths()
        # Match on basename: docs reference `chat_panel.py`, not the full path.
        bases = {Path(p).name for p in paths}
        if not bases:
            return Description(paths=[], text="", source=self.name)

        pattern = re.compile("|".join(re.escape(b) for b in sorted(bases)))
        out: list[str] = []
        for name, lines in docs.items():
            hits = [i for i, l in enumerate(lines) if pattern.search(l)]
            if not hits:
                continue
            lo = max(0, hits[0] - 8)
            hi = min(len(lines), hits[-1] + 12)
            chunk = lines[lo:hi][: self.window]
            out.append(f"--- from {name} ---\n" + "\n".join(chunk))
            if len(out) >= self.max_docs:
                break

        return Description(paths=paths, text="\n\n".join(out), source=self.name)


class Bundle:
    """Run several describers and keep the ones that produced anything.

    Order matters: the adjudication prompt shows them in the order given, and
    the model reads earlier context as framing for later. Docs first, source
    second — the same order the demo's PoC used.
    """

    name = "bundle"

    def __init__(self, *describers: Describer):
        self.describers = describers

    def describe_all(self, tc: TicketCandidates, corpus: Corpus) -> list[Description]:
        out = []
        for d in self.describers:
            desc = d.describe(tc, corpus)
            if desc.text.strip():
                out.append(desc)
        return out
