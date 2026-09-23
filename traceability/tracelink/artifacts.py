"""Stage contracts.

Each stage reads one artifact and writes another. Nothing reaches across a
stage boundary except through these types, so any stage can be swapped for a
different implementation without touching its neighbours.

Every artifact carries `schema_version` and the `produced_by` stage name, so
a half-migrated run directory fails loudly instead of silently mixing shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from tracelink.config import SCHEMA_VERSION


# --------------------------------------------------------------------------
# Stage 0a — tickets
# --------------------------------------------------------------------------

@dataclass
class Ticket:
    """One work item, from any tracker.

    `uid` is ours and stable within a run; `key` is the tracker's own
    identifier and may be absent — in the demo export the real feature rows
    are precisely the ones with no key.
    """

    uid: str
    summary: str
    description: str = ""
    key: str | None = None
    parent: str | None = None
    component: str = ""
    status: str = ""
    #: Where this row physically sits in the export, 1-based as a spreadsheet
    #: shows it. The only locator a keyless row has: with no tracker id there
    #: is nothing to search for, and "row 182 of general_report" is how a
    #: person actually finds it again.
    source_row: int | None = None
    source_sheet: str = ""
    #: Fields the export inlined into free text because the tracker's own
    #: columns for them were left empty — `PO: alice`, `Ngày nhận: 46244`.
    #: `tracelink diagnose` finds these; this is where they are kept once
    #: found, so the rest of the pipeline can use them like any other field.
    inline: dict[str, str] = field(default_factory=dict)
    #: Detected script of the original text; "en" when no other is present.
    lang: str = "en"
    #: English rendering, added by `tracelink translate`. The originals above
    #: are never overwritten: they are what the tracker says, what a person
    #: will search for, and what an auditor has to see. Only the matcher
    #: reads these.
    summary_en: str = ""
    description_en: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """The original, as written."""
        return f"{self.summary}\n{self.description}"

    @property
    def match_text(self) -> str:
        """What retrieval reads: English when we have it, plus the original.

        Both, always. A translation can drop a product name the matcher
        needed, and keeping the original alongside costs nothing but a few
        tokens in an index that is already built.
        """
        if self.summary_en or self.description_en:
            return (f"{self.summary_en}\n{self.description_en}\n"
                    f"{self.summary}\n{self.description}")
        return self.text

    @property
    def match_summary(self) -> str:
        return self.summary_en or self.summary


# --------------------------------------------------------------------------
# Stage 0b — code corpus
# --------------------------------------------------------------------------

# Roles decide how a file is indexed, not whether it matters.
ROLE_CODE = "code"               # parsed for symbols
ROLE_DECLARATIVE = "declarative"  # yaml/json/skill/md — indexed for prose
ROLE_TEST = "test"               # code, but evidence of intent not of shipping
ROLE_DATA = "data"               # too big or too generated to read
ROLE_HUBDOC = "hubdoc"           # describes everything, discriminates nothing
ROLE_BINARY = "binary"


@dataclass
class SourceFile:
    path: str            # repo-relative, always forward slashes
    role: str
    size: int
    language: str = ""


@dataclass
class Symbol:
    """A named thing in a file. `sid` is the anchor Stage 3 is asked to cite."""

    sid: str             # "path/to/file.py::Name"
    path: str
    name: str
    kind: str            # class | function | method | interface | artifact | ...
    #: Where it physically is, 1-based and inclusive. 0 means the analyser
    #: did not say. Without this a trace bottoms out at "this file", which
    #: for a 1,738-line `chat_panel.py` is not a location — it is a
    #: direction. Both analysers can supply it, so it is asked for.
    line: int = 0
    end_line: int = 0

    @property
    def span(self) -> tuple[int, int] | None:
        return (self.line, self.end_line) if self.line else None

    @staticmethod
    def make_sid(path: str, name: str) -> str:
        return f"{path}::{name}"


@dataclass
class FileEdge:
    """`src` imports or calls something in `dst`. Weight is how many times.

    File-level rather than symbol-level on purpose: a ticket is traced to
    files, so an edge between symbols would have to be collapsed here anyway,
    and the collapse is easier to reason about when it happens once.
    """

    src: str
    dst: str
    weight: int = 1


@dataclass
class Corpus:
    files: list[SourceFile]
    symbols: list[Symbol]
    root: str
    analyzer: str = ""   # which implementation produced this
    #: Real import/call edges between files. Empty when the analyser could
    #: not supply them (the Python-AST fallback does not).
    edges: list[FileEdge] = field(default_factory=list)

    def by_role(self, *roles: str) -> list[SourceFile]:
        want = set(roles)
        return [f for f in self.files if f.role in want]


# --------------------------------------------------------------------------
# Stage 0c — documentation the team already wrote
# --------------------------------------------------------------------------

# A docs folder is not one thing, and the difference decides what a document
# is allowed to prove. Modality is measured (see `tracelink.features`), never
# taken from a filename or a folder.
#
# Note what these words do and do not claim. `ungrounded` means "names code
# this corpus does not contain" — which covers a design not yet built *and* a
# perfectly accurate description of a different build. Resolution alone cannot
# separate those, so the name does not pretend to. `features` reports a
# near-match signal that usually can.
MODALITY_GROUNDED = "grounded"      # its claims resolve here: usable as evidence
MODALITY_UNGROUNDED = "ungrounded"  # names code absent here: context, never proof
MODALITY_PROCESS = "process"        # names almost no code: how the team works


@dataclass
class Claim:
    """A symbol or path a document names, and what it resolved to in the code.

    The point of keeping the resolution here rather than recomputing it
    downstream is that "the doc says `_ai_analyze()` and there is no such
    function" is a *finding*, not a lookup failure. It has to survive to
    the report.
    """

    text: str                    # as written: "`_ai_analyze()`"
    name: str                    # normalised: "_ai_analyze"
    kind: str                    # symbol | path
    paths: list[str] = field(default_factory=list)
    sids: list[str] = field(default_factory=list)
    #: When unresolved: the closest thing the corpus does have. A document
    #: saying `_submit_message()` against code that defines `submit()` is a
    #: different *build*, not a different *feature*, and the two need very
    #: different responses from a reader.
    near: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.paths)


@dataclass
class Feature:
    """One checkable statement a document makes about the product.

    A heading, a table row, a bullet — whatever unit of the document carries
    a label and (usually) the names of the code behind it. This is the axis
    both sides can be lifted onto: a ticket is capability-shaped and a
    repository is module-shaped, but a feature row is both at once.
    """

    fid: str
    doc: str                     # docs-relative path, forward slashes
    heading: list[str]           # heading stack, outermost first
    label: str
    description: str = ""
    line: int = 0                # 1-based, so a person can find it again
    claims: list[Claim] = field(default_factory=list)
    #: Inherited from the document. Kept per feature because that is what
    #: retrieval and the describer read.
    modality: str = MODALITY_GROUNDED

    @property
    def text(self) -> str:
        """What the matcher reads: the whole path down to this row."""
        return " ".join([*self.heading, self.label, self.description])

    @property
    def resolved_claims(self) -> list[Claim]:
        return [c for c in self.claims if c.resolved]

    @property
    def unresolved_claims(self) -> list[Claim]:
        return [c for c in self.claims if not c.resolved]

    def paths(self) -> list[str]:
        out: list[str] = []
        for c in self.claims:
            for p in c.paths:
                if p not in out:
                    out.append(p)
        return sorted(out)

    def anchors(self) -> list[str]:
        """Symbol leaf names — what to search for inside the candidate file."""
        return sorted({c.name.rsplit(".", 1)[-1]
                       for c in self.resolved_claims if c.kind == "symbol"})


@dataclass
class DocStat:
    """What one document turned out to be, and the numbers that decided it."""

    doc: str
    modality: str
    features: int
    claims: int
    resolved: int
    signals: list[str] = field(default_factory=list)

    @property
    def resolution(self) -> float:
        return self.resolved / self.claims if self.claims else 0.0


@dataclass
class FeatureMap:
    root: str
    features: list[Feature] = field(default_factory=list)
    docs: list[DocStat] = field(default_factory=list)

    def by_fid(self, fid: str) -> Feature | None:
        return self._index.get(fid)

    @property
    def _index(self) -> dict[str, Feature]:
        # Rebuilt on demand rather than cached: a FeatureMap is mutated in
        # place during resolution, and a stale index there would be silent.
        return {f.fid: f for f in self.features}

    def with_modality(self, *modalities: str) -> list[Feature]:
        want = set(modalities)
        return [f for f in self.features if f.modality in want]


# --------------------------------------------------------------------------
# Stage 0e — documentation as a record of *work*
# --------------------------------------------------------------------------
#
# `Feature` reads a document as a statement about code. These read the same
# documents as statements about delivery: who did what, when, whether a gate
# passed, what is still owed. On this project the two are disjoint — the
# tracker knows about 173 features and nothing at all about the ten-EPIC
# refactor the documents record.
#
# One principle runs through all four types: **classify only what is
# structurally detectable, and keep everything else verbatim under the
# author's own column names**. Deciding which column is "the criterion"
# needs the document's language; recording it as `fields["Criterion"]` does
# not. A consumer maps those names once per project, with its eyes open.


@dataclass
class WorkItem:
    """One tracked piece of work, as the team's own document records it."""

    wid: str                     # the document's id. Never invented.
    title: str
    group: str = ""              # heading path: the epic, sprint, section
    done: bool = False
    started: str = ""            # as written, "" when absent or a placeholder
    ended: str = ""
    owner: str = ""              # parenthetical after the id, when present
    deliverables: list[Claim] = field(default_factory=list)
    #: Code the task *names* without claiming to have produced it — the
    #: library it had to work around, the file it read. Separated only when
    #: the document marks its deliverables (see `learn_arrow_convention`),
    #: and kept rather than dropped, because "this task touched PySide6" is
    #: true and only the word "deliverable" was wrong.
    mentions: list[Claim] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    doc: str = ""
    line: int = 0


@dataclass
class ScheduleEntry:
    """A planned-versus-actual row: the same work seen from the calendar.

    Kept apart from `WorkItem` because a daily plan is a second *view* of
    work already counted, not more of it. Folding the two together doubles
    every task; dropping the plan loses the only record of slip.
    """

    group: str = ""
    title: str = ""
    planned: str = ""            # the column the author wrote the plan in
    started: str = ""
    ended: str = ""
    done: bool = False
    doc: str = ""
    line: int = 0


@dataclass
class Gate:
    """A quality gate: a work row that carries its own verification command.

    That command is what makes a gate different from a task, and it is the
    reason this type exists — a criterion nobody can re-run is an opinion.
    """

    name: str
    command: str = ""
    group: str = ""
    signed_off: bool = False
    started: str = ""
    ended: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    doc: str = ""
    line: int = 0


@dataclass
class RegisterEntry:
    """A row from a register: an id table with no checkboxes."""

    rid: str
    group: str = ""
    found: str = ""
    anchors: list[Claim] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    #: `**Label**: value` pairs from the prose beneath, kept unclassified.
    #: Telling a root cause from a prevention rule needs the language; this
    #: keeps both without claiming to know which is which.
    detail: dict[str, str] = field(default_factory=dict)
    doc: str = ""
    line: int = 0


@dataclass
class ProgressReport:
    root: str
    items: list[WorkItem] = field(default_factory=list)
    schedule: list[ScheduleEntry] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    register: list[RegisterEntry] = field(default_factory=list)
    #: Every task-shaped line that did *not* become a record, with the
    #: reason. Nobody has labelled this extraction, so it cannot be scored;
    #: the only defence against silently losing half a document is that
    #: losing anything is itself an output. Same principle as `ExistingDocs`
    #: returning empty rather than disguising a gap.
    unparsed: list[dict] = field(default_factory=list)

    @property
    def open_items(self) -> list[WorkItem]:
        return [i for i in self.items if not i.done]


# --------------------------------------------------------------------------
# Stage 1 — retrieval
# --------------------------------------------------------------------------

@dataclass
class Evidence:
    """Why a file was proposed. Always inspectable — never a bare score."""

    matcher: str         # stem | sym | prose | doc
    anchor: str          # the token(s) that fired
    df: int              # document frequency of that token
    bar: int             # the corpus-relative bar it was judged against
    strong: bool
    #: Set by the `doc` matcher: the feature that proposed this file. The
    #: describer reads it back to quote the team's own words for the area
    #: instead of guessing from the source alone.
    feature: str = ""


@dataclass
class Candidate:
    path: str
    evidence: list[Evidence]

    @property
    def strong(self) -> bool:
        return any(e.strong for e in self.evidence)


@dataclass
class TicketCandidates:
    uid: str
    candidates: list[Candidate]
    # True when this ticket's title head is shared with others, so its
    # candidate set is probably not specific to it.
    head_collision: bool = False

    def strong_paths(self) -> list[str]:
        return sorted(c.path for c in self.candidates if c.strong)


# --------------------------------------------------------------------------
# Stage 2 — descriptions (the CodeWiki-shaped hole, behind an interface)
# --------------------------------------------------------------------------

@dataclass
class Description:
    """What some code does, with anchors a verdict can cite."""

    paths: list[str]
    text: str
    source: str          # which describer produced it
    anchors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


# --------------------------------------------------------------------------
# Stage 3 — verdicts
# --------------------------------------------------------------------------

VERDICTS = ("corroborated", "contradicted", "unverified")


@dataclass
class VerdictEvidence:
    file: str
    why: str
    symbol: str = ""


@dataclass
class Verdict:
    uid: str
    verdict: str
    confidence: str      # high | medium | low
    reasoning: str
    evidence: list[VerdictEvidence] = field(default_factory=list)
    status_conflict: bool = False
    describer: str = ""
    cost_usd: float = 0.0


# --------------------------------------------------------------------------
# Eval — ground truth
# --------------------------------------------------------------------------

@dataclass
class Label:
    """A human's answer, which is the only thing a score may be measured on."""

    uid: str
    relevant_paths: list[str]      # files that genuinely relate to the ticket
    verdict: str | None = None     # optional: the human's own adjudication
    note: str = ""
    labeller: str = ""


# --------------------------------------------------------------------------
# Envelope IO
# --------------------------------------------------------------------------

def _encode(o: Any) -> Any:
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"cannot serialise {type(o).__name__}")


def save(path: str | Path, stage: str, payload: Any, **meta: Any) -> Path:
    from tracelink import freshness as _FR

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Record what this artifact was built from, so a later change to an input
    # is detectable by content rather than by guessing from timestamps. Done
    # here because every stage writes through this one function.
    inputs = _FR.stamp(p)
    if inputs:
        meta = {**meta, "inputs": inputs}
    body = {
        "schema_version": SCHEMA_VERSION,
        "produced_by": stage,
        "meta": meta,
        "payload": payload,
    }
    p.write_text(
        json.dumps(body, default=_encode, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return p


def load(path: str | Path, expect_stage: str | None = None) -> dict[str, Any]:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    got = body.get("schema_version")
    if got != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: schema_version {got}, this build speaks {SCHEMA_VERSION}. "
            f"Re-run the stage that wrote it rather than mixing shapes."
        )
    if expect_stage and body.get("produced_by") != expect_stage:
        raise ValueError(
            f"{path}: produced by {body.get('produced_by')!r}, expected {expect_stage!r}"
        )
    return body


def load_payload(path: str | Path, expect_stage: str | None = None) -> Any:
    return load(path, expect_stage)["payload"]


def rebuild_tickets(rows: Iterable[dict[str, Any]]) -> list[Ticket]:
    return [Ticket(**r) for r in rows]


def rebuild_corpus(payload: dict[str, Any]) -> Corpus:
    return Corpus(
        files=[SourceFile(**f) for f in payload["files"]],
        symbols=[Symbol(**s) for s in payload["symbols"]],
        root=payload["root"],
        analyzer=payload.get("analyzer", ""),
        edges=[FileEdge(**e) for e in payload.get("edges", [])],
    )


def rebuild_features(payload: dict[str, Any]) -> FeatureMap:
    features = []
    for f in payload.get("features", []):
        f = dict(f)
        f["claims"] = [Claim(**c) for c in f.get("claims", [])]
        features.append(Feature(**f))
    return FeatureMap(
        root=payload["root"],
        features=features,
        docs=[DocStat(**d) for d in payload.get("docs", [])],
    )


def rebuild_progress(payload: dict[str, Any]) -> ProgressReport:
    def claims(rows: Iterable[dict[str, Any]]) -> list[Claim]:
        return [Claim(**c) for c in rows]

    items = []
    for r in payload.get("items", []):
        r = dict(r)
        r["deliverables"] = claims(r.get("deliverables", []))
        r["mentions"] = claims(r.get("mentions", []))
        items.append(WorkItem(**r))
    register = []
    for r in payload.get("register", []):
        r = dict(r)
        r["anchors"] = claims(r.get("anchors", []))
        register.append(RegisterEntry(**r))
    return ProgressReport(
        root=payload["root"],
        items=items,
        schedule=[ScheduleEntry(**r) for r in payload.get("schedule", [])],
        gates=[Gate(**r) for r in payload.get("gates", [])],
        register=register,
        unparsed=list(payload.get("unparsed", [])),
    )


def rebuild_candidates(rows: Iterable[dict[str, Any]]) -> list[TicketCandidates]:
    out = []
    for r in rows:
        out.append(TicketCandidates(
            uid=r["uid"],
            candidates=[
                Candidate(path=c["path"],
                          evidence=[Evidence(**e) for e in c["evidence"]])
                for c in r["candidates"]
            ],
            head_collision=r.get("head_collision", False),
        ))
    return out


def rebuild_labels(rows: Iterable[dict[str, Any]]) -> list[Label]:
    return [Label(**r) for r in rows]


def rebuild_verdicts(rows: Iterable[dict[str, Any]]) -> list[Verdict]:
    out = []
    for r in rows:
        r = dict(r)
        r["evidence"] = [VerdictEvidence(**e) for e in r.get("evidence", [])]
        out.append(Verdict(**r))
    return out
