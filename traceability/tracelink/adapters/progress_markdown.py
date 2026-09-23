"""Stage 0e — read a docs tree as a record of work, not of code.

`featuremap_markdown` asks what these documents say about the codebase.
This asks what they say about the *project*: which tasks were done, by whom,
when, which quality gates were signed off, and what a register records. On
the dataset this was built for the two are disjoint — the tracker holds 173
feature tickets and knows nothing whatsoever about the ten-EPIC refactor the
documents record, with its 63 tasks and 112 real timestamps.

Nothing here may name a project, a file or a language, which for this stage
is the whole difficulty: `R01-T01`, `Start:`, `Team` and `BUG-` are all
specific to one document. Six structural rules take their place.

**R1 — a work item is a checkbox.** A GFM task-list item, or a table row
whose state cell is a checkbox. Both are markdown.

**R2 — a work item must carry an identifier**, and an identifier is a token
whose *prefix recurs* across the document (`min_id_family`, default 3).
Learned, never declared. This is what separates real work from a blank
pro-forma: on the real checklist a naive count reported 14 open tasks when
the answer was 7, because a Definition-of-Done template contributes seven
permanently-unchecked boxes. Templates do not carry ids, and that
generalises past this one document.

**R3 — dates are found by pattern; their labels are learned.** Collect every
``label: <date>`` pair, cluster by label, keep the two commonest; the one
with the earlier median is the start. `Start:`/`End:`, `Inizio:`/`Fine:` and
`開始`/`終了` all work without appearing anywhere in this file.
`tracelink.diagnose` already detects inline fields this way.

**R4 — a table is a work table when half its rows carry a checkbox**, and
its columns are then typed by *content*: the id column is the one matching
the id family, date columns are the ones that parse as dates, the state
column holds the checkbox. Never by position — a table that puts the
description first must still work.

**R5 — a work row that also carries a command is a gate**, not a task. A
criterion nobody can re-run is an opinion; the command is the difference.

**R6 — grouping is the heading stack**, from the shared walker.

Everything a rule does not classify is kept verbatim under the author's own
column name. Guessing which column is "the criterion" needs the document's
language. Recording `fields["Criterion"]` does not.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

from tracelink.adapters._markdown import (
    CODE_SPAN, KIND_BULLET, KIND_HEADING, KIND_ROW, KIND_TEXT,
    clean_label, walk,
)
from tracelink.adapters.featuremap_markdown import normalise_claim
from tracelink.artifacts import (
    Claim, RegisterEntry, Gate, ProgressReport, ScheduleEntry, WorkItem,
)

# A GFM task-list item, as the walker hands it over (the bullet marker is
# already stripped): "[x] **AA-01**: ..."
CHECKBOX = re.compile(r"^\[([ xX])\]\s*(.*)$")
CELL_CHECKBOX = re.compile(r"^\[([ xX])\]$")

# An identifier: letters, then at least one separated group, containing a
# digit somewhere. `R01-T01`, `BUG-001`, `ZZ_101`, `AA-01`.
ID_TOKEN = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+)\b")
# How far into an item an identifier may sit and still be *its* id. An id is
# written at the front; a token deep in a sentence is a cross-reference.
ID_WINDOW = 48

DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)\b")
# A partial date: the way people write a plan column. "24/08", "3-11", and
# "23/08 (CN - 17:00)" — the trailing annotation is a weekday and a time,
# and refusing it left a whole "when" column untyped, so its cells were
# then offered up as the name of the gate beside them.
PART_DATE = re.compile(r"^\D{0,4}(\d{1,2}[/-]\d{1,2})\b.{0,24}$")
# `Label: <date>` — the label is learned, only the shape is fixed here.
LABELLED_DATE = re.compile(
    r"([^\s*|`:][^*|`:]{0,28}?)\s*:\s*`?\s*"
    r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)")
# `**Label**: value` in prose beneath a register entry.
LABELLED_FIELD = re.compile(r"\*\*([^*]{1,40}?)\s*\**\s*\*\*\s*:\s*(.+)$")
# A parenthetical straight after the id: "(Alice)", "(Team Duy)".
OWNER = re.compile(r"^\s*\(([^)]{1,40})\)")
# A command is a code span that is a whole invocation: a program and at
# least one argument, where something in it carries a path separator, a file
# extension or a flag.
#
# The first version searched for "a backticked span containing a space".
# Markdown defeats that: in ``Khóa DTO `A`, `B` `` the text *between* two
# spans is itself delimited by backticks, so `, ` matched and thirty rows of
# a daily plan were filed as quality gates. A command is now matched against
# the whole span, anchored.
INVOCATION = re.compile(r"^\S+(?:\s+\S+)+$")
COMMAND_SHAPE = re.compile(r"[/\\]|\.[A-Za-z]{1,4}\b|(?:^|\s)--?[A-Za-z]")

# "…rewrite the importer ➔ `scripts/check_imports.py`". The arrow separates
# what a task *mentions* from what it *produces*, and the distinction matters:
# without it every dependency named in passing becomes a deliverable, so
# `PySide6` was reported as code missing from the repository.
#
# Typographic, not linguistic — the same class of signal as the checkbox —
# but still only a convention, so it is learned per document and used only
# where it holds.
ARROW = re.compile(r"[➔→⇒»]|-->|->|=>")
MIN_ARROW_FRACTION = 0.5

MIN_ID_FAMILY = 3


# --------------------------------------------------------------------------
# Learning the document's conventions
# --------------------------------------------------------------------------

def command_in(text: str) -> str:
    """The first code span in `text` that is a runnable invocation, or ""."""
    for raw in CODE_SPAN.findall(text):
        span = raw.strip()
        if INVOCATION.match(span) and COMMAND_SHAPE.search(span):
            return span
    return ""


def _id_in(text: str) -> str | None:
    """The first id-shaped token near the front of `text`.

    Matched against the whole string and then filtered by position, rather
    than against a truncated copy: slicing first let a token be cut in half
    at the window edge, so `co4e_canvas` was reported as the identifier
    `co4e_can`.
    """
    for m in ID_TOKEN.finditer(text):
        if m.start() >= ID_WINDOW:
            break
        tok = m.group(1)
        if any(ch.isdigit() for ch in tok):
            return tok
    return None


def _prefix(tok: str) -> str:
    return re.split(r"[-_]", tok, maxsplit=1)[0]


def learn_id_families(candidates: list[str], minimum: int = MIN_ID_FAMILY) -> set[str]:
    """Prefixes that recur often enough to be an identifier convention.

    One `co4e_tab` in a sentence is a filename. Thirty `R01-T01`,
    `R01-T02`, `R02-T01` is a scheme somebody chose.
    """
    counts = collections.Counter(_prefix(c) for c in candidates)
    return {p for p, n in counts.items() if n >= minimum}


def learn_date_labels(text: str) -> tuple[str, str]:
    """The document's own words for "started" and "ended", or ("", "").

    Cluster every `label: <date>` pair by label, keep the two commonest, and
    let the dates decide which is which: the family with the earlier median
    is the start. No vocabulary is consulted, so the same code reads
    `Start:` and `Inizio:`.
    """
    by_label: dict[str, list[str]] = collections.defaultdict(list)
    for label, value in LABELLED_DATE.findall(text):
        key = clean_label(label).strip()
        if key:
            by_label[key].append(value)
    if len(by_label) < 2:
        return "", ""
    top = sorted(by_label.items(), key=lambda kv: -len(kv[1]))[:2]
    (a, av), (b, bv) = top
    return (a, b) if _median(av) <= _median(bv) else (b, a)


def _median(values: list[str]) -> str:
    return sorted(values)[len(values) // 2]


# --------------------------------------------------------------------------
# Field extraction
# --------------------------------------------------------------------------

def _dates_from(text: str, start_label: str, end_label: str) -> tuple[str, str]:
    """Start and end, by label when we learned one, else by order."""
    pairs = [(clean_label(l).strip(), v) for l, v in LABELLED_DATE.findall(text)]
    if start_label or end_label:
        started = next((v for l, v in pairs if l == start_label), "")
        ended = next((v for l, v in pairs if l == end_label), "")
        if started or ended:
            return started, ended
    found = DATE.findall(text)
    if len(found) >= 2:
        return found[0], found[1]
    return (found[0], "") if found else ("", "")


def learn_arrow_convention(lines: list[str]) -> bool:
    """Does this document mark deliverables with an arrow?

    Measured on the real checklist: 49 of 70 task lines carry one, and 92 of
    the 151 code spans on those lines sit after it. Below
    `MIN_ARROW_FRACTION` the convention is not reliable enough to act on and
    every span stays a deliverable, which is the old behaviour.
    """
    if not lines:
        return False
    return sum(1 for l in lines if ARROW.search(l)) / len(lines) >= MIN_ARROW_FRACTION


def _split_on_arrow(text: str, convention: bool) -> tuple[str, str]:
    """(what this task produced, what it merely mentioned)."""
    if not convention or not ARROW.search(text):
        return text, ""
    parts = ARROW.split(text)
    return parts[-1], " ".join(parts[:-1])


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


def _title_from(text: str, wid: str | None) -> str:
    body = text
    if wid:
        body = body.split(wid, 1)[-1]
        body = OWNER.sub("", body, count=1)
    body = re.sub(r"^\s*[*_]*\s*[:\-–—]\s*", "", body)
    # The date annotation is metadata, not part of the title.
    body = LABELLED_DATE.sub("", body)
    return clean_label(body)[:200]


def _owner_after(text: str, wid: str) -> str:
    tail = text.split(wid, 1)[-1] if wid in text else ""
    tail = re.sub(r"^\**", "", tail)
    m = OWNER.match(tail)
    return clean_label(m.group(1)) if m else ""


# --------------------------------------------------------------------------
# Table column typing (R4)
# --------------------------------------------------------------------------

class _Columns:
    """Which column is which, decided by what the cells contain."""

    def __init__(self, rows: list[tuple[str, ...]], families: set[str]):
        width = max((len(r) for r in rows), default=0)
        self.state = self.id = self.planned = self.command = -1
        self.dates: list[int] = []
        for i in range(width):
            col = [r[i] for r in rows if i < len(r)]
            if not col:
                continue
            if self.state < 0 and _fraction(col, CELL_CHECKBOX.match) >= 0.5:
                self.state = i
            elif self.id < 0 and _is_id_column(col, families):
                self.id = i
            elif _fraction(col, DATE.search) >= 0.5:
                self.dates.append(i)
            elif self.command < 0 and _fraction(col, command_in) >= 0.5:
                self.command = i
            elif self.planned < 0 and _fraction(col, _looks_planned) >= 0.5:
                self.planned = i

    @property
    def typed(self) -> set[int]:
        return {self.state, self.id, self.planned, self.command, *self.dates} - {-1}


def _is_id_column(col: list[str], families: set[str]) -> bool:
    """An id column identifies each *row*, so its values must be distinct.

    Without the uniqueness test this fires on any column of identifiers,
    and a Python symbol containing a digit is identifier-shaped: the Co4E
    split maps list `ui/co4e_canvas.py` on all 280 rows, `co4e` became an
    id family, and 829 rows of symbol tables were filed as defect records.
    A value repeated on every row names the *table*, not the row.
    """
    ids = [_id_in(c.strip("`* ")) for c in col]
    got = [i for i in ids if i and _prefix(i) in families]
    if len(got) < 0.5 * len(col) or len(got) < 2:
        return False
    return len(set(got)) >= 0.8 * len(got)


def _bare(cell: str) -> str:
    """Markdown stripped, but nothing else.

    `clean_label` removes leading section numbering, which is right for a
    heading and destructive for a plan cell: it turns `**24/08**` into
    `/08`. A date-shaped cell has to be tested before that runs.
    """
    return CODE_SPAN.sub(r"\1", cell).strip("*_` ").strip()


def _looks_planned(cell: str) -> bool:
    return bool(PART_DATE.match(_bare(cell)))


def _fraction(col: list[str], test) -> float:
    return sum(1 for c in col if c and test(c)) / len(col) if col else 0.0


def _unclassified(cells: tuple[str, ...], header: tuple[str, ...],
                  cols: _Columns) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, cell in enumerate(cells):
        if i in cols.typed or not cell:
            continue
        key = header[i] if i < len(header) and header[i] else f"column {i + 1}"
        out[key] = clean_label(cell)
    return out


# --------------------------------------------------------------------------
# The adapter
# --------------------------------------------------------------------------

def parse_document(text: str, doc: str, report: ProgressReport,
                   minimum: int = MIN_ID_FAMILY) -> None:
    """Read one document into `report`. Appends; never raises on bad input."""
    blocks = list(walk(text))

    # Pass 1: learn this document's conventions.
    candidates: list[str] = []
    for b in blocks:
        if b.kind == KIND_BULLET:
            m = CHECKBOX.match(b.text)
            if m and (tok := _id_in(clean_label(m.group(2)))):
                candidates.append(tok)
        elif b.kind == KIND_ROW:
            for cell in b.cells:
                if tok := _id_in(cell.strip("`* ")):
                    candidates.append(tok)
    families = learn_id_families(candidates, minimum)
    start_label, end_label = learn_date_labels(text)
    arrow_convention = learn_arrow_convention(
        [b.text for b in blocks if b.kind == KIND_BULLET and CHECKBOX.match(b.text)])

    # Group table rows so a table is typed as a whole, not row by row.
    tables: dict[tuple[int, ...], list] = {}
    order: list = []
    for i, b in enumerate(blocks):
        if b.kind != KIND_ROW:
            continue
        prev = blocks[i - 1] if i else None
        key = (b.header, b.heading)
        if order and order[-1][0] == key and prev is not None and prev.kind == KIND_ROW:
            order[-1][1].append(b)
        else:
            order.append((key, [b]))
    tables = order

    seen_ids: set[str] = set()

    def note(kind: str, reason: str, line: int, text_: str) -> None:
        report.unparsed.append({"doc": doc, "line": line, "kind": kind,
                                "reason": reason, "text": text_[:120]})

    # --- bullets ------------------------------------------------------
    for i, b in enumerate(blocks):
        if b.kind != KIND_BULLET:
            continue
        m = CHECKBOX.match(b.text)
        if not m:
            continue
        done = m.group(1).lower() == "x"
        body = m.group(2)
        wid = _id_in(clean_label(body))
        if not wid or _prefix(wid) not in families:
            note("item", "no-id", b.line, b.text)
            continue
        if wid in seen_ids:
            note("item", "duplicate-id", b.line, b.text)
            continue
        seen_ids.add(wid)
        # A task's dates are written on the line or the one under it.
        tail = blocks[i + 1].text if i + 1 < len(blocks) and \
            blocks[i + 1].kind in (KIND_TEXT, KIND_BULLET) and \
            not CHECKBOX.match(blocks[i + 1].text) else ""
        started, ended = _dates_from(f"{body}\n{tail}", start_label, end_label)
        produced, mentioned = _split_on_arrow(body, arrow_convention)
        report.items.append(WorkItem(
            wid=wid, title=_title_from(body, wid), group=" / ".join(b.heading),
            done=done, started=started, ended=ended,
            owner=_owner_after(body, wid),
            deliverables=_claims_in(produced), mentions=_claims_in(mentioned),
            doc=doc, line=b.line,
        ))

    # --- tables -------------------------------------------------------
    for (header, heading), rows in tables:
        cells = [r.cells for r in rows]
        cols = _Columns(cells, families)
        is_work = cols.state >= 0
        has_ids = cols.id >= 0

        for r in rows:
            c = r.cells
            group = " / ".join(heading)
            if len(c) != len(header) and header:
                note("row", "ragged-row", r.line, r.raw)
                continue

            def at(i: int) -> str:
                return c[i].strip() if 0 <= i < len(c) else ""

            dates = [DATE.search(at(i)).group(1) for i in cols.dates
                     if DATE.search(at(i))]
            started = dates[0] if dates else ""
            ended = dates[1] if len(dates) > 1 else ""
            done = CELL_CHECKBOX.match(at(cols.state)) is not None and \
                at(cols.state).lower() == "[x]"

            if is_work and cols.command >= 0:
                report.gates.append(Gate(
                    name=_gate_name(c, header, cols),
                    command=command_in(at(cols.command)),
                    group=group, signed_off=done, started=started, ended=ended,
                    fields=_unclassified(c, header, cols), doc=doc, line=r.line))
                continue

            if is_work and has_ids:
                wid = _id_in(at(cols.id).strip("`* "))
                if not wid:
                    note("row", "no-id", r.line, r.raw)
                    continue
                if wid in seen_ids:
                    note("row", "duplicate-id", r.line, r.raw)
                    continue
                seen_ids.add(wid)
                report.items.append(WorkItem(
                    wid=wid, title=_longest_text(c, header, cols), group=group,
                    done=done, started=started, ended=ended,
                    deliverables=_claims_in(r.raw),
                    fields=_unclassified(c, header, cols), doc=doc, line=r.line))
                continue

            if is_work:
                report.schedule.append(ScheduleEntry(
                    group=group, title=_longest_text(c, header, cols),
                    planned=_bare(at(cols.planned)) if cols.planned >= 0 else "",
                    started=started, ended=ended, done=done,
                    doc=doc, line=r.line))
                continue

            if has_ids:
                did = _id_in(at(cols.id).strip("`* "))
                if not did:
                    continue
                report.register.append(RegisterEntry(
                    rid=did, group=group, found=started,
                    anchors=_claims_in(r.raw),
                    fields=_unclassified(c, header, cols), doc=doc, line=r.line))

    _attach_detail(blocks, report, doc)


def _gate_name(cells, header, cols: _Columns) -> str:
    """The gate's name: the first unclassified text cell, else the heading."""
    for i, cell in enumerate(cells):
        if i in cols.typed or not cell:
            continue
        # Tested with `_bare`, not `clean_label`: the latter strips leading
        # numbers, so a "when" column of `**23/08 (CN)**` no longer looked
        # like a date and was printed as the gate's name — as `/08 (CN)`.
        if PART_DATE.match(_bare(cell)):
            continue
        label = clean_label(cell)
        if label:
            return label
    return ""


def _longest_text(cells, header, cols: _Columns) -> str:
    best = ""
    for i, cell in enumerate(cells):
        if i in cols.typed:
            continue
        label = clean_label(cell)
        if len(label) > len(best):
            best = label
    return best[:200]


def _attach_detail(blocks, report: ProgressReport, doc: str) -> None:
    """`**Label**: value` prose under a heading that names a register entry.

    Kept unclassified on purpose. Telling a root cause from a prevention
    rule means reading the label, and reading the label means knowing the
    language. Both are preserved; neither is interpreted.
    """
    by_id = {d.rid: d for d in report.register if d.doc == doc}
    if not by_id:
        return
    current: str | None = None
    for b in blocks:
        if b.kind == KIND_HEADING:
            current = next((d for d in by_id if d in b.text), None)
            continue
        if current is None or b.kind not in (KIND_BULLET, KIND_TEXT):
            continue
        m = LABELLED_FIELD.match(b.text.strip())
        if m:
            by_id[current].detail[clean_label(m.group(1))] = clean_label(m.group(2))


def read_progress(docs_dir: str | Path, exts: tuple[str, ...] = (".md",),
                  minimum: int = MIN_ID_FAMILY) -> ProgressReport:
    """Every markdown document under `docs_dir`, recursively."""
    root = Path(docs_dir)
    report = ProgressReport(root=str(root))
    if not root.is_dir():
        return report
    for p in sorted(q for q in root.rglob("*")
                    if q.suffix.lower() in exts and q.is_file()):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        parse_document(text, p.relative_to(root).as_posix(), report, minimum)
    return report
