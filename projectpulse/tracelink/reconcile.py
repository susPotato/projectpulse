"""Stage 5b — three witnesses to one delivery, and where they disagree.

The tracker says a feature shipped. The team's own documentation says a
task is open. The code either contains the function or it does not. Two of
those disagreeing tells you something is wrong; three tell you *which one*.

The join is on **resolved code anchors, never on text**. A work item and a
ticket belong together when the files they reach overlap. Matching titles
would mean matching Vietnamese task descriptions against English ticket
summaries, which is a translation problem wearing a traceability hat.

Two rules keep this from manufacturing findings, both learned the hard way
elsewhere in this pipeline:

1. **Absence is never inferred from a failed lookup.** A work item that
   names no deliverable is `unknown` on the code axis, not `absent`. This
   is the same reason `adjudicate` has three verdicts and not two.
2. **An unverifiable claim from an ungrounded document may not assert that
   something is done.** 33 of the 39 documents here describe a build that
   is not the one on disk (`features.classify`). But the discount applies
   per *item*, not per document: a task whose deliverable we looked up
   ourselves does not need the document to vouch for it. Only where there
   was nothing to check does modality decide, and there the item reports
   `claimed-done-elsewhere` rather than `done`.

A third rule is about the tracker rather than the docs: **which status
means "finished" is a decision, not a fact.** `Release it` is this
tracker's word for it; another says `Closed`, `Resolved`, `Hoàn thành`.
Guessing would be naming a project, so the caller passes `done_status` and
the answer records what was assumed. With nothing passed, the tracker axis
is honestly `unknown` and every row says so.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from tracelink.artifacts import (
    FeatureMap, ProgressReport, Ticket, TicketCandidates,
    MODALITY_GROUNDED,
)

DONE, OPEN, UNKNOWN = "done", "open", "unknown"
PRESENT, ABSENT = "present", "absent"
ELSEWHERE = "claimed-done-elsewhere"

#: (tracker, docs, code) -> what it means. Only fully-known triples appear;
#: anything with an `unknown` in it is labelled `unknown` and shown as such.
LABELS: dict[tuple[str, str, str], str] = {
    (DONE, DONE, PRESENT): "agreed",
    (DONE, DONE, ABSENT): "both-sides-wrong",
    (DONE, OPEN, PRESENT): "undertracked-doc",
    (DONE, OPEN, ABSENT): "tracker-optimistic",
    (OPEN, DONE, PRESENT): "untracked-delivery",
    (OPEN, DONE, ABSENT): "doc-optimistic",
    (OPEN, OPEN, PRESENT): "unclaimed-code",
    (OPEN, OPEN, ABSENT): "agreed-outstanding",
}

#: Ranked worst-first for reporting. A finding nobody reads is not a finding.
SEVERITY = [
    "both-sides-wrong", "doc-optimistic", "tracker-optimistic",
    "untracked-delivery", "undertracked-doc", "unclaimed-code",
    "agreed-outstanding", "agreed", "unknown",
]


@dataclass
class Row:
    """One work item, seen from all three sides."""

    wid: str
    title: str
    group: str
    tracker: str = UNKNOWN
    docs: str = UNKNOWN
    code: str = UNKNOWN
    label: str = "unknown"
    paths: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    #: How this item reached the tracker: by its own resolved deliverable,
    #: through the section it sits in, or not at all. An indirect join is a
    #: weaker claim and has to look like one.
    joined_via: str = "none"
    doc: str = ""
    line: int = 0

    @property
    def severity(self) -> int:
        return SEVERITY.index(self.label) if self.label in SEVERITY else len(SEVERITY)

    @property
    def confidence(self) -> str:
        """`direct` when the item's own code anchor found the tickets.

        This is not a quality score sprinkled on afterwards, it is
        structural, and the structure is uncomfortable: an item joins by
        `claim` only when a deliverable resolved, and `code=absent` means
        none did. So **every `absent` row is necessarily area-joined** —
        which is to say every `both-sides-wrong`, `tracker-optimistic`,
        `doc-optimistic` and `agreed-outstanding` finding rests on
        area-level evidence about the tracker, always, by construction.
        Measured on the real set: 30 absent rows, 30 of them section-joined.

        There is no better join available for them. A task claiming to have
        written a file that does not exist has nothing for a ticket to
        touch. So the answer is to report the weakness, not to hide it
        behind a label that reads the same as a direct hit.
        """
        return "direct" if self.joined_via == "claim" else "area"


def _code_state(item) -> tuple[str, list[str], list[str]]:
    """present / absent / unknown, plus what resolved and what did not."""
    claims = [c for c in item.deliverables if c.kind != "docref"]
    if not claims:
        # Rule 1: naming nothing is not evidence of nothing.
        return UNKNOWN, [], []
    found = [c.name for c in claims if c.resolved]
    missing = [c.name for c in claims if not c.resolved]
    return (PRESENT if found else ABSENT), found, missing


def _docs_state(item, code: str, ungrounded_docs: set[str]) -> str:
    """Rule 2, applied per *item* rather than per document.

    The first version discounted every completed task in an ungrounded
    document. Every document here is ungrounded, so all 56 finished tasks
    became `claimed-done-elsewhere`, which is not in the label table, and
    57 of 63 rows came out `unknown` — including 29 where the deliverable
    was verifiably missing and 25 where it was verifiably present. The
    report knew the answer and discarded it at the last step.

    The correction is the principle this pipeline already applies in
    retrieval: *strength is decided per claim, not per document.* If a task
    says it produced `AtomicJsonFile` and that class is in the corpus, the
    claim is checked and the rest of the document is beside the point. So
    the document's modality only decides the cases we could not check for
    ourselves, which is what it was ever good for.
    """
    if not item.done:
        return OPEN
    if code != UNKNOWN:
        # We looked. The document's general reliability adds nothing.
        return DONE
    return ELSEWHERE if item.doc in ungrounded_docs else DONE


def section_paths(fmap: FeatureMap | None) -> dict[str, set[str]]:
    """Doc section -> every file the rows in it reach.

    The second way a work item can find its tickets, and the one that makes
    the interesting half of the truth table reachable at all.

    Joining only on an item's *own* resolved deliverable has a hole exactly
    where the findings are: an item whose code is missing resolves nothing,
    so it joins to no ticket, so its tracker state is unknown — and
    `agreed-outstanding`, `tracker-optimistic`, `doc-optimistic` and
    `both-sides-wrong` can never be produced. Those are the four labels
    worth having. A section pins files through its *other* rows, which is
    how a task whose own function was never written still knows which part
    of the system it was about.
    """
    out: dict[str, set[str]] = collections.defaultdict(set)
    if fmap is None:
        return out
    for f in fmap.features:
        key = f"{f.doc}#{' / '.join(h for h in f.heading if h)}"
        for c in f.claims:
            out[key].update(c.paths)
    return dict(out)


def reconcile(
    progress: ProgressReport,
    tickets: list[Ticket],
    candidates: list[TicketCandidates],
    fmap: FeatureMap | None = None,
    done_status: set[str] | None = None,
) -> list[Row]:
    done_status = {s.strip().lower() for s in (done_status or set()) if s.strip()}
    ungrounded = ({d.doc for d in fmap.docs if d.modality != MODALITY_GROUNDED}
                  if fmap else set())

    by_uid = {t.uid: t for t in tickets}
    # file -> the tickets whose strong candidate set reaches it
    touching: dict[str, list[str]] = collections.defaultdict(list)
    for tc in candidates:
        for p in tc.strong_paths():
            touching[p].append(tc.uid)

    sections = section_paths(fmap)

    rows: list[Row] = []
    for item in progress.items:
        code, found, missing = _code_state(item)
        own = sorted({p for c in item.deliverables for p in c.paths})
        via = "claim" if own else "none"
        paths = own
        if not paths:
            paths = sorted(sections.get(f"{item.doc}#{item.group}", set()))
            via = "section" if paths else "none"

        uids: list[str] = []
        for p in paths:
            uids.extend(touching.get(p, ()))
        uids = sorted(set(uids))
        if not uids:
            via = "none"

        if not uids:
            tracker = UNKNOWN
        elif not done_status:
            # No mapping supplied, so we do not know what any status means.
            tracker = UNKNOWN
        else:
            states = {(by_uid[u].status or "").strip().lower() for u in uids
                      if u in by_uid}
            tracker = DONE if states & done_status else OPEN

        docs = _docs_state(item, code, ungrounded)
        # ELSEWHERE is deliberately not in LABELS: it is not a claim of
        # done-ness, so no triple containing it can be `agreed`.
        label = LABELS.get((tracker, docs, code), "unknown")

        rows.append(Row(
            wid=item.wid, title=item.title, group=item.group,
            tracker=tracker, docs=docs, code=code, label=label,
            paths=paths, tickets=uids, anchors=found, missing=missing,
            joined_via=via,
            doc=item.doc, line=item.line,
        ))

    rows.sort(key=lambda r: (r.severity, r.wid))
    return rows


def summary(rows: list[Row], confidence: str | None = None) -> dict[str, int]:
    if confidence:
        rows = [r for r in rows if r.confidence == confidence]
    counts = collections.Counter(r.label for r in rows)
    return {k: counts[k] for k in SEVERITY if counts[k]}


def degenerate_axes(rows: list[Row]) -> dict[str, str]:
    """Axes that came out the same on every row, and so decided nothing.

    A label like `both-sides-wrong` reads as two independent sources
    agreeing. It is only worth that if each axis could have come out the
    other way. On the CoWorkLocal run the tracker axis is `done` on all 63
    rows: a work item joins tens of tickets — 31 at the median, 104 at the
    widest — and `tracker` is `done` if *any one* of them is, against a
    backlog where 142 of 173 are marked `Release it`. The rule fires on
    every row by construction.

    So the tracker contributes no information there, and `both-sides-wrong`
    means only "the documents say done and the code has nothing". Reporting
    that as two sources agreeing overstates it by one whole source.

    This does not change any label. It reports which axes were constant so
    the caller can say so, because silently presenting a constant as
    evidence is the failure worth avoiding.
    """
    out: dict[str, str] = {}
    if not rows:
        return out
    for axis in ("tracker", "docs", "code"):
        values = {getattr(r, axis) for r in rows}
        if len(values) == 1:
            out[axis] = values.pop()
    return out


def join_width(rows: list[Row]) -> dict[str, int]:
    """How many tickets each work item reached. Wide joins are weak joins."""
    sizes = sorted(len(r.tickets) for r in rows)
    if not sizes:
        return {}
    return {"median": sizes[len(sizes) // 2], "max": sizes[-1],
            "min": sizes[0], "rows": len(sizes)}
