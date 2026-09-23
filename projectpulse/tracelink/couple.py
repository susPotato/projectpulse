"""Stage 6 — which tickets depend on, or disturb, which others.

The tracker records no links between these tickets, so any relationship
here is **inferred and must be labelled as such**. It is inferred from the
code, not from the text: two tickets are related when the code that
implements them is related, and the specific file or import that connects
them is carried on the edge so a reader can reject it.

Two kinds, kept apart because they license different actions:

``depends-on`` (directional)
    A's code imports or calls B's code. Changing B can break A. Grounded in
    a real edge from the dependency graph.

``shares-code`` (symmetric)
    A and B are both implemented in the same file. Neither depends on the
    other, but they cannot be worked on independently, and a regression in
    one is a regression risk for the other.

**What this deliberately will not do.** It does not infer a relationship
from wording, from a shared component, or from a hub file. A file that
half the backlog touches - `i18n.py`, a config module, an app shell -
connects everything to everything, which is a statement about the file and
not about any pair of tickets. Those are excluded by fan-out, measured, and
the exclusions are reported.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Literal

from tracelink.artifacts import Corpus, Ticket, Verdict

KIND_DEPENDS = "depends-on"
KIND_SHARES = "shares-code"


@dataclass
class Link:
    """One inferred relationship, with the evidence that produced it."""

    src: str                      # ticket uid
    dst: str                      # ticket uid
    kind: str                     # depends-on | shares-code
    confidence: str               # high | medium | low
    via: list[str] = field(default_factory=list)   # files / edges behind it
    weight: int = 1

    def as_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "kind": self.kind,
                "confidence": self.confidence, "via": self.via,
                "weight": self.weight}


@dataclass
class CoupleConfig:
    """Bars, expressed against the backlog rather than as magic numbers."""

    #: A file implicated in more than this fraction of tickets is shared
    #: infrastructure, not a link between any two of them.
    hub_file_fraction: float = 0.10
    #: A file imported by more than this fraction of the codebase is a
    #: utility. An edge through it says "everyone uses the logger".
    hub_fanin_fraction: float = 0.15
    #: Never let either bar fall below this on a small backlog.
    hub_floor: int = 4
    #: Cap on links reported per ticket, worst-first, so one busy ticket
    #: cannot bury the rest.
    max_per_ticket: int = 8


def cited_files(verdicts: list[Verdict]) -> dict[str, set[str]]:
    """uid -> the files a settled verdict actually rested on.

    Candidates are deliberately not used. The matcher proposes noisy files
    by design, and a relationship built on two tickets sharing a bad guess
    would be a confident statement about nothing.
    """
    out: dict[str, set[str]] = {}
    for v in verdicts:
        if v.verdict == "unverified":
            continue
        files = {e.file.replace("\\", "/") for e in v.evidence if e.file}
        if files:
            out[v.uid] = files
    return out


def hub_files(
    by_uid: dict[str, set[str]],
    corpus: Corpus,
    cfg: CoupleConfig,
) -> tuple[set[str], dict[str, str]]:
    """Files too widely shared to link any particular pair of tickets."""
    n_tickets = max(1, len(by_uid))
    n_files = max(1, len({f.path for f in corpus.files}))

    touch = collections.Counter()
    for files in by_uid.values():
        for f in files:
            touch[f] += 1
    fanin = collections.Counter(e.dst for e in corpus.edges)

    touch_bar = max(cfg.hub_floor, int(cfg.hub_file_fraction * n_tickets))
    fanin_bar = max(cfg.hub_floor, int(cfg.hub_fanin_fraction * n_files))

    hubs: set[str] = set()
    why: dict[str, str] = {}
    for f, n in touch.items():
        if n > touch_bar:
            hubs.add(f)
            why[f] = f"implicated in {n} tickets (bar {touch_bar})"
    for f, n in fanin.items():
        if n > fanin_bar and f not in hubs:
            hubs.add(f)
            why[f] = f"imported by {n} files (bar {fanin_bar})"
    return hubs, why


def infer(
    tickets: list[Ticket],
    verdicts: list[Verdict],
    corpus: Corpus,
    cfg: CoupleConfig | None = None,
) -> tuple[list[Link], dict]:
    cfg = cfg or CoupleConfig()
    by_uid = cited_files(verdicts)
    hubs, why = hub_files(by_uid, corpus, cfg)

    # file -> tickets implemented there, hubs removed.
    owners: dict[str, set[str]] = collections.defaultdict(set)
    for uid, files in by_uid.items():
        for f in files:
            if f not in hubs:
                owners[f].add(uid)

    links: dict[tuple[str, str, str], Link] = {}

    def add(src, dst, kind, via, weight, confidence):
        key = (src, dst, kind)
        cur = links.get(key)
        if cur is None:
            links[key] = Link(src=src, dst=dst, kind=kind, via=[via],
                              weight=weight, confidence=confidence)
            return
        if via not in cur.via:
            cur.via.append(via)
        cur.weight += weight
        # More independent evidence raises confidence; it never lowers it.
        if len(cur.via) >= 2 and cur.confidence == "medium":
            cur.confidence = "high"

    # 1. Same file => the two cannot be worked on independently.
    for f, uids in owners.items():
        if len(uids) < 2:
            continue
        ordered = sorted(uids)
        # A file shared by a handful of tickets is a strong signal; one
        # shared by many is weak even after the hub bar, so say which.
        conf = "high" if len(ordered) <= 3 else "medium" if len(ordered) <= 6 else "low"
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                add(a, b, KIND_SHARES, f, 1, conf)

    # 2. A's file imports B's file => A depends on B.
    for e in corpus.edges:
        if e.src in hubs or e.dst in hubs:
            continue
        srcs, dsts = owners.get(e.src), owners.get(e.dst)
        if not srcs or not dsts:
            continue
        via = f"{e.src} -> {e.dst}"
        # A heavier import edge is a stronger claim, but the number of
        # tickets on each end dilutes it: if six tickets sit on each side we
        # cannot say which pair the import is really about.
        spread = len(srcs) * len(dsts)
        conf = "high" if spread <= 2 else "medium" if spread <= 6 else "low"
        for a in srcs:
            for b in dsts:
                if a != b:
                    add(a, b, KIND_DEPENDS, via, e.weight, conf)

    ranked = _rank_and_cap(links.values(), cfg)
    stats = {
        "tickets_with_cited_code": len(by_uid),
        "hub_files_excluded": len(hubs),
        "hub_reasons": dict(sorted(why.items())),
        "links": len(ranked),
        "depends_on": sum(1 for l in ranked if l.kind == KIND_DEPENDS),
        "shares_code": sum(1 for l in ranked if l.kind == KIND_SHARES),
        "high_confidence": sum(1 for l in ranked if l.confidence == "high"),
    }
    return ranked, stats


_ORDER = {"high": 0, "medium": 1, "low": 2}


def _rank_and_cap(links, cfg: CoupleConfig) -> list[Link]:
    ranked = sorted(links, key=lambda l: (_ORDER[l.confidence], -l.weight,
                                          l.src, l.dst))
    seen = collections.Counter()
    out = []
    for l in ranked:
        if seen[l.src] >= cfg.max_per_ticket or seen[l.dst] >= cfg.max_per_ticket:
            continue
        seen[l.src] += 1
        seen[l.dst] += 1
        out.append(l)
    return out


def by_ticket(links: list[Link]) -> dict[str, list[dict]]:
    """Links keyed by ticket, with direction stated from that ticket's view."""
    out: dict[str, list[dict]] = collections.defaultdict(list)
    for l in links:
        out[l.src].append({**l.as_dict(), "other": l.dst,
                           "direction": "to" if l.kind == KIND_DEPENDS else "with"})
        out[l.dst].append({**l.as_dict(), "other": l.src,
                           "direction": "from" if l.kind == KIND_DEPENDS else "with"})
    return dict(out)
