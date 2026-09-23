"""Stage 0c — check the team's documentation against the code it describes.

Two jobs, and the second only exists because of what the first measures.

**Resolve.** Every symbol and path a document names is looked up in the
corpus. `_ai_analyze()` becomes `ui/dashboard_tab.py::DashboardTab._ai_analyze`
or it becomes nothing, and which of those it is gets written down.

**Classify.** A document whose claims resolve is describing the code in
front of us and may be used as evidence. A document whose claims do not is
describing something else — a target architecture, a refactor not yet done,
a build that shipped later — and may only be used as context. Both are
markdown, both sit in `docs/`, both are written in the present tense by the
same people, and nothing but the resolution rate separates them.

This matters more than it sounds. The alternative — the first describer's
behaviour — is to hand an adjudicator a *plan* under the heading
"Architecture documentation excerpt" and ask whether the ticket is
implemented. The model then reads a confident present-tense description of
the exact feature, written by the team who own the code, and corroborates.
The verdict is wrong, the citation is real, and nothing downstream can
catch it. Measuring modality is the fix, and it costs one pass over a
dictionary.

Measured on the CoWorkLocal docs (39 documents, 2,716 rows): 1 grounded,
33 ungrounded, 5 process. That is not a failure of the documents — it is
the finding. `hackathon/docs` is ahead of `pimsathon-main`: it describes a
4-tier layout (`presentation/`, `application/`) the repository does not
have, and functions (`_submit_message`, `_build_kanban`) it does not
define. Any pipeline that had used those documents as ground truth would
have reported a backlog that is far more complete than it is.
"""

from __future__ import annotations

import collections
import difflib
from pathlib import Path

from tracelink.artifacts import (
    Claim, Corpus, DocStat, Feature, FeatureMap,
    MODALITY_GROUNDED, MODALITY_UNGROUNDED, MODALITY_PROCESS,
)
from tracelink.config import DocsConfig


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

def _symbol_lookup(corpus: Corpus) -> dict[str, list[str]]:
    """Every way a person might write a symbol's name -> the sids that match.

    Analysers disagree about qualification: CodeWiki emits
    `DashboardTab._ai_analyze`, the AST fallback emits `_ai_analyze`, and a
    document writes whichever the author had in front of them. All three
    spellings are indexed so the join does not depend on which analyser ran.
    """
    out: dict[str, list[str]] = collections.defaultdict(list)
    for s in corpus.symbols:
        keys = {s.name, s.name.rsplit(".", 1)[-1]}
        for k in keys:
            if k:
                out[k].append(s.sid)
    return dict(out)


def _path_lookup(corpus: Corpus) -> dict[str, list[str]]:
    out: dict[str, list[str]] = collections.defaultdict(list)
    for f in corpus.files:
        out[f.path].append(f.path)
        out[Path(f.path).name].append(f.path)
    return dict(out)


def _nearest(name: str, leaves: dict[str, str], pool: list[str]) -> str:
    """The closest name the corpus actually defines, or "".

    Cheap first: strip the leading underscores a private-method convention
    adds and removes between builds, which is the commonest single cause of
    a miss (`_duplicate_task` against `duplicate_task`). Only then pay for
    `difflib`.

    The cutoff is 0.92 and was set by looking at what fell either side of
    it, not by taste. At 0.86 the pairs arriving were `nav_project` ~
    `save_project` (0.870) and `run_node` ~ `run_code` (0.875): two
    different functions that happen to share a prefix. Real renames in the
    same data score 0.97 and up. The gap is wide and empty, so the bar sits
    in it. This under-reports — `_submit_message` ~ `submit` scores 0.60 and
    is missed — which is the right direction to be wrong in, because a
    near-match is used to argue that a document is merely out of date.
    """
    bare = name.lstrip("_")
    for candidate in (bare, f"_{bare}", f"__{bare}"):
        if candidate != name and candidate in leaves:
            return leaves[candidate]
    if len(bare) < 6:
        return ""
    close = difflib.get_close_matches(bare, pool, n=1, cutoff=0.92)
    return leaves[close[0]] if close else ""


KIND_DOCREF = "docref"
KIND_MAYBE = "symbol?"


def resolve_claims(claims: list[Claim], corpus: Corpus,
                   known_docs: set[str] | None = None) -> list[Claim]:
    """Look up a bare list of claims against the corpus. Mutates in place.

    Split out of :func:`resolve` so anything that produces `Claim`s can be
    checked the same way — `progress_markdown` emits them for a task's
    deliverables and a defect's affected files, and those have to be
    checked by the same code, or "the code says otherwise" starts meaning
    two different things in one report.
    """
    syms = _symbol_lookup(corpus)
    paths = _path_lookup(corpus)
    known_docs = known_docs or set()
    leaves: dict[str, str] = {}
    for s in corpus.symbols:
        leaves.setdefault(s.name.rsplit(".", 1)[-1], s.sid)
    pool = list(leaves)

    drop: list[Claim] = []
    for claim in claims:
        if claim.kind == "path":
            base = Path(claim.name).name
            if claim.name not in paths and base not in paths and (
                    claim.name in known_docs or base in known_docs):
                claim.kind = KIND_DOCREF
                continue
            hits = paths.get(claim.name) or paths.get(base) or []
            claim.paths = sorted(set(hits))
            claim.sids = []
        else:
            sids = syms.get(claim.name) or syms.get(claim.name.rsplit(".", 1)[-1]) or []
            claim.sids = sorted(set(sids))
            claim.paths = sorted({s.split("::", 1)[0] for s in claim.sids})
            if claim.kind == KIND_MAYBE:
                # A capitalised word is a class if the code says so and an
                # English word otherwise. Only the code can say.
                if claim.resolved:
                    claim.kind = "symbol"
                else:
                    drop.append(claim)
                    continue
        if not claim.resolved and claim.kind == "symbol":
            claim.near = _nearest(claim.name.rsplit(".", 1)[-1], leaves, pool)
    return drop


def resolve(fmap: FeatureMap, corpus: Corpus,
            known_docs: set[str] | None = None) -> FeatureMap:
    """Fill in every claim's `paths`/`sids`, and a near-match when it fails.

    One reclassification happens here rather than in the parser, because it
    needs both sides: a path claim that names *another document* is a
    cross-reference, not a statement about code. Left as a code claim it is
    reported as missing — `drift` listed `GammaTeam_decisions.md` as a
    capability the repository fails to implement — and it drags the
    document's resolution rate down, which is the number modality is
    decided on.
    """
    known_docs = (known_docs or set()) | {
        d for f in fmap.features for d in (f.doc, Path(f.doc).name)}
    # One call for every claim in the map, not one per feature: the lookups
    # are built from the whole corpus, and rebuilding them 2,716 times turns
    # a one-second stage into a minute.
    owner = {id(c): f for f in fmap.features for c in f.claims}
    everything = [c for f in fmap.features for c in f.claims]
    for claim in resolve_claims(everything, corpus, known_docs):
        owner[id(claim)].claims.remove(claim)
    return fmap


# --------------------------------------------------------------------------
# Modality
# --------------------------------------------------------------------------

def _unresolved_path_roots(claims: list[Claim]) -> list[str]:
    """Top-level directories a document names that the repository lacks.

    The single most legible signal that a document is a *design*: it files
    its symbols under `presentation/` and `application/` in a repository
    whose top level is `ui/` and `core/`. Reported so a reader can see why
    a document was called ungrounded without having to trust the ratio.
    """
    roots = collections.Counter()
    for c in claims:
        if c.kind == "path" and not c.resolved and "/" in c.name:
            roots[c.name.split("/", 1)[0]] += 1
    return [r for r, _ in roots.most_common(3)]


def classify(fmap: FeatureMap, cfg: DocsConfig | None = None) -> FeatureMap:
    """Assign a modality per document, then stamp it on that document's rows.

    Symbols and paths are counted apart, because they fail for different
    reasons and only one of them is decisive. A document can know every
    function in the current code and still be a plan — the split maps here
    resolve 127 of 233 symbols and 2 of 10 paths, because they take today's
    symbols and place them in files that do not exist yet. Averaging those
    two rates into one number calls such a document grounded and hands a
    refactor plan to the adjudicator as a description of the build.

    So: naming files the repository does not have is disqualifying on its
    own. Otherwise the symbol rate decides.
    """
    cfg = cfg or DocsConfig()
    by_doc: dict[str, list[Feature]] = collections.defaultdict(list)
    for f in fmap.features:
        by_doc[f.doc].append(f)

    stats: list[DocStat] = []
    for doc, feats in sorted(by_doc.items()):
        # Distinct names: a document that mentions one function forty times
        # has made one checkable statement, not forty.
        distinct: dict[str, Claim] = {}
        for f in feats:
            for c in f.claims:
                if c.kind == KIND_DOCREF:
                    continue
                distinct.setdefault(c.name, c)
        uniq = list(distinct.values())
        n = len(uniq)
        n_ok = sum(1 for c in uniq if c.resolved)

        symbols = [c for c in uniq if c.kind == "symbol"]
        files = [c for c in uniq if c.kind == "path"]
        sym_ok = sum(1 for c in symbols if c.resolved)
        file_ok = sum(1 for c in files if c.resolved)
        sym_rate = sym_ok / len(symbols) if symbols else 0.0
        file_rate = file_ok / len(files) if files else 1.0

        signals: list[str] = []
        if n < cfg.min_claims:
            modality = MODALITY_PROCESS
            signals.append(f"names {n} checkable code claim(s), fewer than "
                           f"{cfg.min_claims} — read for context, not for links")
        elif files and file_rate < cfg.descriptive_resolution:
            modality = MODALITY_UNGROUNDED
            signals.append(f"places code in {len(files) - file_ok} of {len(files)} "
                           f"files the repository does not have")
            roots = _unresolved_path_roots(uniq)
            if roots:
                signals.append("under " + ", ".join(f"{r}/" for r in roots)
                               + " — no such directory here")
            if symbols and sym_rate >= cfg.descriptive_resolution:
                signals.append(f"but {sym_ok}/{len(symbols)} of the symbols it names "
                               f"do exist: it is describing a move, not a build")
        elif sym_rate >= cfg.descriptive_resolution:
            modality = MODALITY_GROUNDED
            signals.append(f"{sym_ok}/{len(symbols)} symbols it names exist here "
                           f"({sym_rate:.0%})")
        else:
            modality = MODALITY_UNGROUNDED
            signals.append(f"only {sym_ok}/{len(symbols)} symbols it names exist here "
                           f"({sym_rate:.0%})")
            near = [c for c in symbols if not c.resolved and c.near]
            missed = len(symbols) - sym_ok
            if missed and len(near) / missed >= 0.4:
                eg = near[0]
                signals.append(
                    f"{len(near)} of {missed} misses have a near-match in the code "
                    f"({eg.name} ~ {eg.near.split('::')[-1]}) — this reads as a "
                    f"different build of the same product, not a design")

        for f in feats:
            f.modality = modality
        stats.append(DocStat(doc=doc, modality=modality, features=len(feats),
                             claims=n, resolved=n_ok, signals=signals))

    fmap.docs = stats
    return fmap


def build(docs_dir: str | Path, corpus: Corpus,
          cfg: DocsConfig | None = None, json_docs: bool = True) -> FeatureMap:
    """Parse, resolve, classify. The whole stage in one call.

    Both adapters feed one map. A team's generated documentation — a
    control inventory, a screenshot manifest, a symbol table — is the same
    kind of evidence as its prose, already in the shape the markdown
    adapter works to recover, and reading only `*.md` left 300 KB of it
    unread here.
    """
    from tracelink.adapters.featuremap_json import read_json_docs
    from tracelink.adapters.featuremap_markdown import read_docs

    fmap = read_docs(docs_dir)
    if json_docs:
        fmap.features.extend(read_json_docs(docs_dir, start_index=len(fmap.features)))
    # Every file in the docs tree, not only the ones parsed. A manifest
    # referencing `screens/dashboard-dark.png` is pointing at a sibling
    # artifact; counted as code, it is a capability the repository failed
    # to implement.
    root = Path(docs_dir)
    known_docs = {q.relative_to(root).as_posix() for q in root.rglob("*") if q.is_file()}
    known_docs |= {q.name for q in root.rglob("*") if q.is_file()}
    resolve(fmap, corpus, known_docs)
    classify(fmap, cfg)
    return fmap


# --------------------------------------------------------------------------
# Drift — the mirror of `shadow`
# --------------------------------------------------------------------------

def drift(fmap: FeatureMap, modalities: tuple[str, ...] = (MODALITY_GROUNDED,)
          ) -> list[dict]:
    """Claims a document makes that no code supports.

    `shadow` finds code no ticket accounts for. This finds the opposite: a
    documented capability with nothing behind it. Restricted to grounded
    documents by default, because an unresolved claim in a design is not
    drift — it is the design.
    """
    want = set(modalities)
    rows: dict[str, dict] = {}
    for f in fmap.features:
        if f.modality not in want:
            continue
        for c in f.unresolved_claims:
            if c.kind == KIND_DOCREF:
                continue
            row = rows.setdefault(c.name, {
                "name": c.name, "kind": c.kind, "mentions": 0, "where": [],
            })
            row["mentions"] += 1
            if len(row["where"]) < 4:
                row["where"].append({
                    "doc": f.doc, "line": f.line, "fid": f.fid,
                    "label": f.label, "heading": " / ".join(h for h in f.heading if h),
                })
    return sorted(rows.values(), key=lambda r: (-r["mentions"], r["name"]))


def skew(fmap: FeatureMap) -> list[dict]:
    """Per document: does it miss because it is a design, or a later build?

    Both look identical in a resolution rate, and the difference decides
    what a reader should do about it. A design names files that do not
    exist. A later build names functions that *almost* exist — `submit`
    where the doc says `_submit_message`, `build_job` where it says
    `_build_job`. Counting near-matches separates them without asking
    anybody to read the document.
    """
    by_doc: dict[str, list[Claim]] = collections.defaultdict(list)
    for f in fmap.features:
        for c in f.claims:
            by_doc[f.doc].append(c)

    rows = []
    for doc, claims in by_doc.items():
        uniq = list({c.name: c for c in claims}.values())
        syms = [c for c in uniq if c.kind == "symbol"]
        missed = [c for c in syms if not c.resolved]
        near = [c for c in missed if c.near]
        rows.append({
            "doc": doc,
            "symbols": len(syms),
            "missing": len(missed),
            "near_matches": len(near),
            "near_fraction": len(near) / len(missed) if missed else 0.0,
            "examples": [{"doc_says": c.name, "code_has": c.near.split("::")[-1],
                          "in": c.near.split("::")[0]} for c in near[:5]],
        })
    return sorted(rows, key=lambda r: -r["near_matches"])


def summary(fmap: FeatureMap) -> dict:
    by_mod = collections.Counter(f.modality for f in fmap.features)
    claims = [c for f in fmap.features for c in f.claims
              if c.kind != KIND_DOCREF]
    distinct = {c.name: c for c in claims}
    return {
        "docs": len(fmap.docs),
        "features": len(fmap.features),
        "features_by_modality": dict(by_mod),
        "claims": len(distinct),
        "claims_resolved": sum(1 for c in distinct.values() if c.resolved),
        "files_reached": len({p for f in fmap.features for p in f.paths()}),
    }
