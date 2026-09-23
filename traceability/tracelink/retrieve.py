"""Stage 1b — narrow the corpus to a few candidate files per ticket.

Deterministic. No LLM, no API key, no embeddings. It does not decide whether
a ticket's claim is true; it decides *where to look*.

Two corrections to the demo matcher, both structural rather than tuned:

1. **Bars are corpus-relative** (see :mod:`tracelink.config`). `df <= 5` is
   not a statement about rarity, it is a statement about a 148-file repo.
2. **Title-head collision is detected and repaired.** 63 of 143 demo
   tickets shared a candidate set with another ticket because only the
   text before the em-dash was tokenised — ten `Cowork Chat — …` tickets
   got byte-identical answers. When a head is shared, the tail is what
   distinguishes them, so it is tokenised too and flagged.

A fourth matcher, `doc`, runs only when the project has documentation
(:mod:`tracelink.features`). It is the one that reaches a *capability*
rather than a filename, and the one whose vocabulary matches the tickets'
own — see :func:`_doc_evidence`.
"""

from __future__ import annotations

import collections
import re

from tracelink.artifacts import (
    Candidate, Evidence, FeatureMap, Ticket, TicketCandidates,
)
from tracelink.config import PipelineConfig
from tracelink.index import (
    Index, split_identifier, tokenize_query, tokenize_text,
)

# Jira titles conventionally read "Area — specific thing".
HEAD_SPLIT = re.compile(r"\s+[—–]\s+|\s+--\s+|:\s+|\s+\|\s+")


def title_head(summary: str) -> str:
    return HEAD_SPLIT.split(summary, maxsplit=1)[0].strip()


def head_counts(tickets: list[Ticket]) -> collections.Counter:
    return collections.Counter(title_head(t.match_summary).lower() for t in tickets)


def _hits(index: dict[str, set[str]], tok: str) -> set[str]:
    return index.get(tok, set())


def _doc_evidence(
    ticket: Ticket,
    idx: Index,
    cfg: PipelineConfig,
    fmap: FeatureMap,
    by_path: dict[str, list[Evidence]],
) -> None:
    """Fourth matcher: ticket -> documented feature -> the code it names.

    The only matcher that routes through a human's own statement of what
    the product does, rather than through a name that happens to be spelled
    the same in two places.

    **What it was measured to be worth**, over the same 173 tickets with
    `--no-docs` as the baseline: tickets with a strong candidate set go
    158 -> 167 (91.3% -> 96.5%), tickets with nothing at all 7 -> 3,
    distinct candidate sets 143 -> 157. It fires for 132 tickets and is
    the sole strong witness for 9. The cost is a median candidate set one
    file larger, 5 -> 6.

    Most of that came from one rule, `enough()` below. With the default
    two-shared-words bar the matcher fired for 57 tickets and changed
    coverage by 2, because a one-line table row rarely has two rare words
    to share. Letting a hit on the row's *own function name* carry the bar
    alone is what made the stage worth running.

    It does *not* fix title-head collision, which is what the first draft
    of this docstring claimed. That was already repaired by tokenising the
    tail (see the module docstring): 1 of 36 head-sharing tickets still
    collides with a sibling, with or without documentation.

    Two rules keep it honest:

    * The **full** ticket text is always used, never just the head. The head
      is what collides; the tail is the whole point of asking.
    * Strength is decided **per claim, not per document**. If a row says the
      budget card is `_apply_budget()` and `_apply_budget` is really there,
      that link is true however wrong the rest of the document is — and
      conversely, a row in an otherwise reliable document that names a
      function nobody wrote proves nothing. Document modality decides how
      the row is *framed* for the adjudicator (see `describe.DocFeature`),
      not whether the row is allowed to retrieve.
    """
    d = cfg.docs
    stop = cfg.stopwords.words
    bar = d.feature_bar(idx.n_features or 1)

    words = tokenize_query(ticket.match_text, stop, cfg.retrieval.min_token_len)
    shared: dict[str, set[str]] = collections.defaultdict(set)
    for w in sorted(words):
        hits = idx.feature.get(w, set())
        if hits and len(hits) <= bar:
            for fid in hits:
                shared[fid].add(w)

    index = fmap._index

    def enough(fid: str, ws: set[str]) -> bool:
        """How many shared words this feature needs to count.

        Normally two, because one word in common with a one-line table row
        is a coincidence waiting to happen. But a row is not only prose: it
        names its own functions, and a ticket word that matches one of
        *those* is a different quality of evidence. "refund" against a row
        whose claim is `refund()` is the match this stage exists to make,
        and a short row rarely has a second rare word to offer. So a claim
        hit is worth the whole bar on its own.
        """
        if len(ws) >= d.feature_min_shared:
            return True
        feature = index.get(fid)
        if feature is None:
            return False
        named = {part for c in feature.claims
                 for part in split_identifier(c.name, stop,
                                              cfg.retrieval.min_token_len)}
        return bool(ws & named)

    ranked = sorted(
        ((fid, ws) for fid, ws in shared.items() if enough(fid, ws)),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )[: d.max_features_per_ticket]
    for fid, ws in ranked:
        feature = index.get(fid)
        if feature is None:
            continue
        anchors = feature.anchors()
        for claim in feature.claims:
            for path in claim.paths:
                by_path[path].append(Evidence(
                    matcher="doc",
                    # The anchor is what `describe` searches the file for, so
                    # it must be a name that occurs in the source — the
                    # symbol the document named, not the feature id.
                    anchor=",".join(anchors[:4]) or claim.name,
                    df=len(shared[fid]), bar=d.feature_min_shared,
                    strong=claim.resolved, feature=fid,
                ))


def retrieve_one(
    ticket: Ticket,
    idx: Index,
    cfg: PipelineConfig,
    head_is_shared: bool,
    fmap: FeatureMap | None = None,
) -> TicketCandidates:
    r = cfg.retrieval
    stop = cfg.stopwords.words
    stem_bar = r.stem_bar(idx.n_indexed)
    sym_bar = r.sym_bar(idx.n_indexed)
    prose_bar = r.prose_bar(idx.n_prose_docs or idx.n_indexed)

    # Identifier tokens. The head alone is the precise signal; when the head
    # is shared with other tickets it cannot discriminate, so the tail must
    # carry the work.
    head = title_head(ticket.match_summary)
    ident_source = ticket.match_summary if head_is_shared else head
    ident_tokens = tokenize_query(ident_source, stop, r.min_token_len)

    by_path: dict[str, list[Evidence]] = collections.defaultdict(list)

    for tok in sorted(ident_tokens):
        for matcher, index, bar in (("stem", idx.stem, stem_bar),
                                    ("sym", idx.sym, sym_bar)):
            hits = _hits(index, tok)
            if not hits:
                continue
            strong = len(hits) <= bar
            for path in hits:
                by_path[path].append(Evidence(matcher=matcher, anchor=tok,
                                              df=len(hits), bar=bar, strong=strong))

    # Prose: rare-word overlap with declarative files, length-normalised so
    # a big document must share proportionally more to qualify.
    ticket_words = tokenize_text(ticket.match_text, stop, r.min_token_len)
    shared: dict[str, set[str]] = collections.defaultdict(set)
    for w in ticket_words:
        hits = _hits(idx.prose, w)
        if hits and len(hits) <= prose_bar:
            for path in hits:
                shared[path].add(w)
    for path, words in shared.items():
        need = r.prose_min_shared + idx.vocab_size.get(path, 0) // r.prose_vocab_step
        if len(words) >= need:
            by_path[path].append(Evidence(
                matcher="prose", anchor=",".join(sorted(words)[:4]),
                df=len(words), bar=need, strong=True))

    if fmap is not None and idx.feature:
        _doc_evidence(ticket, idx, cfg, fmap, by_path)

    candidates = [Candidate(path=p, evidence=ev) for p, ev in sorted(by_path.items())]
    return TicketCandidates(uid=ticket.uid, candidates=candidates,
                            head_collision=head_is_shared)


def retrieve(
    tickets: list[Ticket],
    idx: Index,
    cfg: PipelineConfig | None = None,
    fmap: FeatureMap | None = None,
) -> list[TicketCandidates]:
    cfg = cfg or PipelineConfig()
    heads = head_counts(tickets)
    threshold = cfg.retrieval.head_collision_threshold
    return [
        retrieve_one(t, idx, cfg,
                     head_is_shared=heads[title_head(t.match_summary).lower()] >= threshold,
                     fmap=fmap)
        for t in tickets
    ]


def detect_hubdocs(
    results: list[TicketCandidates],
    cfg: PipelineConfig | None = None,
) -> set[str]:
    """Files that match too much of the backlog to discriminate anything.

    The demo excluded `assets/RULEBASE.md` by name after noticing it matched
    134 of 173 tickets. That is a measurement, so make it one: any file
    proposed for more than `hubdoc_ticket_fraction` of tickets describes
    everything and therefore says nothing about any one ticket.
    """
    cfg = cfg or PipelineConfig()
    if not results:
        return set()
    counts = collections.Counter(
        c.path for r in results for c in r.candidates if c.strong
    )
    limit = cfg.corpus.hubdoc_ticket_fraction * len(results)
    return {p for p, n in counts.items() if n > limit}


def drop_paths(results: list[TicketCandidates], paths: set[str]) -> list[TicketCandidates]:
    if not paths:
        return results
    out = []
    for r in results:
        out.append(TicketCandidates(
            uid=r.uid,
            candidates=[c for c in r.candidates if c.path not in paths],
            head_collision=r.head_collision,
        ))
    return out
