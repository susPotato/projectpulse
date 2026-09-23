"""Stage 0c — what structure does this export actually contain?

Run before trusting anything downstream. A tracker export can have 400
columns and carry almost nothing; it can also hide its real structure in a
free-text field or in row order rather than in the fields built for it.
Both are common, and both silently change what the rest of the pipeline is
able to say.

This asks six questions, in order of how badly a wrong answer hurts:

1. Which columns carry anything at all?
2. Are any of the **relationship** fields populated - links, parents,
   epics, sprints? If not, no dependency can be read from the tracker and
   any we show must be inferred and labelled as such.
3. Which columns are constant? A column with one value across every row is
   a column that cannot distinguish anything.
4. Which columns *partition* the rows usefully, and is the row order
   grouped by one of them? Row order is structure somebody created by hand
   and no field records.
5. Is there structure buried in free text - dates, ids, people?
6. Do the titles follow a convention, and does it collide?

Deterministic and free. No model, no network.
"""

from __future__ import annotations

import collections
import itertools
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# Fields that, across the common trackers, record a relationship between
# work items. Matched case-insensitively against the export's own headers.
RELATIONSHIP_FIELDS = (
    "linked issues", "sub-tasks", "subtasks", "parent", "parent link",
    "epic link", "epic name", "sprint", "blocks", "blocked by",
    "depends on", "predecessor", "successor", "related issue id",
    "related jira id", "outward issue link", "inward issue link",
)

# Deliberately NOT a relationship: Jira's `Rank` is a lexical ordering token
# used to sort a board. It is always populated and says nothing about what
# depends on what, so counting it would turn "no dependencies recorded"
# into "dependencies recorded" on every Jira export ever produced.
ORDERING_FIELDS = ("rank", "rank (obsolete)", "backlog order")

# Fields that carry scheduling substance, as opposed to a timestamp the
# tracker sets on its own.
SCHEDULE_FIELDS = (
    "due date", "start date", "planned start", "planned end date",
    "baseline start date", "baseline end date", "target start", "target end",
    "original estimate", "remaining estimate", "time spent", "story points",
    "progress", "percentdone",
)

# Timestamps the tracker writes for you. Present on every export and never
# evidence of planning.
AUTOMATIC_FIELDS = ("created", "updated", "last viewed", "resolved")

EXCEL_EPOCH = date(1899, 12, 30)
DATE_SERIAL = re.compile(r"\b(4[0-9]{4}|5[0-9]{4})\b")
PERSONISH = re.compile(r"^\s*([A-Za-zÀ-ỹ /]{2,24})\s*:\s*(.+)$")


@dataclass
class Finding:
    """One observation, with the severity of acting on it wrongly."""

    level: str          # blocker | warning | note
    topic: str
    detail: str


@dataclass
class Diagnosis:
    rows: int
    columns: int
    populated: list[tuple[str, int, int]] = field(default_factory=list)
    constant: list[tuple[str, str]] = field(default_factory=list)
    relationships: dict[str, int] = field(default_factory=dict)
    schedule: dict[str, int] = field(default_factory=dict)
    automatic: dict[str, int] = field(default_factory=dict)
    partitions: list[tuple[str, int, float]] = field(default_factory=list)
    order_runs: dict[str, tuple[int, int]] = field(default_factory=dict)
    text_structure: dict[str, Any] = field(default_factory=dict)
    title_convention: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["findings"] = [f.__dict__ for f in self.findings]
        return d


def _matches(header: str, keys: tuple[str, ...]) -> bool:
    """Whole-phrase match, never substring.

    A substring test reads "Detection (D) Ranking" as the `rank` field and
    "Parent Link" as fine but "Apparent Cause" as a parent too. On an export
    with 421 columns from a tracker's global field catalogue, that is not a
    hypothetical - both of those exist in the demo sheet.
    """
    words = re.findall(r"[a-z0-9]+", header)
    for k in keys:
        kw = re.findall(r"[a-z0-9]+", k)
        if not kw:
            continue
        n = len(kw)
        for i in range(len(words) - n + 1):
            if words[i:i + n] == kw:
                return True
    return False


def _values(rows: list[dict[str, Any]], col: str) -> list[str]:
    out = []
    for r in rows:
        v = r.get(col)
        if v not in (None, ""):
            out.append(str(v).strip())
    return out


def diagnose(rows: list[dict[str, Any]], headers: list[str]) -> Diagnosis:
    """`rows` are raw column->value dicts, straight from the sheet."""
    n = len(rows)
    d = Diagnosis(rows=n, columns=len(headers))
    if not n:
        d.findings.append(Finding("blocker", "empty", "No rows to diagnose."))
        return d

    lower = {h: h.lower().strip() for h in headers}

    # 1. Fill census.
    fill: dict[str, int] = {}
    for h in headers:
        fill[h] = len(_values(rows, h))
    d.populated = sorted(((h, c, len({v for v in _values(rows, h)}))
                          for h, c in fill.items() if c),
                         key=lambda t: -t[1])

    if len(d.populated) < len(headers) * 0.05:
        d.findings.append(Finding(
            "note", "sparse export",
            f"Only {len(d.populated)} of {len(headers)} columns carry any value. "
            f"The rest are the tracker's global field catalogue, not this "
            f"project's data."))

    # 2. Relationships.
    for h in headers:
        if _matches(lower[h], RELATIONSHIP_FIELDS):
            d.relationships[h] = fill[h]
    linked = {k: v for k, v in d.relationships.items() if v}
    if not linked:
        # Two ways to have no dependencies, and both are the same blocker for
        # anything downstream: the columns exist and are empty, or the export
        # never carried them. Only reporting the first meant a lean export -
        # a plan workbook, a CSV somebody assembled by hand - passed silently
        # as though its dependencies had been checked.
        detail = (
            "Every relationship field is empty (" +
            ", ".join(sorted(d.relationships)[:6]) + ")."
            if d.relationships else
            "This export carries no relationship column at all (no linked "
            "issues, parent, epic, sprint or predecessor field)."
        )
        d.findings.append(Finding(
            "blocker", "no recorded dependencies",
            detail + " Nothing in this export says which work item depends on "
            "which, so any dependency shown downstream is inferred and must "
            "be labelled as such."))
    elif linked:
        d.findings.append(Finding(
            "note", "recorded dependencies",
            "Populated: " + ", ".join(f"{k} ({v})" for k, v in linked.items())))

    # 3. Schedule substance vs tracker bookkeeping.
    for h in headers:
        if _matches(lower[h], SCHEDULE_FIELDS):
            d.schedule[h] = fill[h]
        if _matches(lower[h], AUTOMATIC_FIELDS):
            d.automatic[h] = fill[h]
    real = {k: v for k, v in d.schedule.items() if v}
    if not real:
        d.findings.append(Finding(
            "blocker", "no schedule data",
            "No due date, estimate, baseline or progress field is populated. "
            "Nothing here can support a schedule."))

    # A planning field that exactly equals a tracker timestamp is that
    # timestamp wearing a different hat, not a plan.
    for s_col in [h for h, v in d.schedule.items() if v]:
        for a_col in [h for h, v in d.automatic.items() if v]:
            # Compare row by row over the rows where both are present. A
            # list-to-list comparison misses this whenever one column has a
            # single gap - and one gap is exactly what the demo export has.
            pairs = [(str(r[s_col]), str(r[a_col])) for r in rows
                     if r.get(s_col) not in (None, "")
                     and r.get(a_col) not in (None, "")]
            if len(pairs) < 3:
                continue
            same = sum(1 for a, b in pairs if a == b)
            if same == len(pairs):
                d.findings.append(Finding(
                    "warning", "planning field is a timestamp",
                    f"{s_col!r} equals {a_col!r} on all {len(pairs)} rows that "
                    f"have both. It records when the item was created, not "
                    f"when work is planned to start."))

    # 4. Constants and partitions.
    for h, count, distinct in d.populated:
        if distinct == 1 and count == n:
            d.constant.append((h, _values(rows, h)[0][:40]))
        elif 1 < distinct <= max(2, n // 3) and count >= n * 0.5:
            d.partitions.append((h, distinct, round(count / n, 3)))
    d.partitions.sort(key=lambda t: -t[2])

    # Row order carries structure nobody recorded: if a column's values come
    # in few long runs rather than scattered, the sheet was authored in
    # blocks of it.
    for h, distinct, _cov in d.partitions[:6]:
        seq = [str(r.get(h) or "") for r in rows]
        runs = sum(1 for _ in itertools.groupby(seq))
        d.order_runs[h] = (runs, distinct)
        if runs <= distinct * 2:
            d.findings.append(Finding(
                "note", "row order is structure",
                f"The {n} rows form only {runs} contiguous runs of {h!r} "
                f"({distinct} distinct values). The sheet was written in "
                f"blocks of {h}, which is ordering no field records."))

    # 5. Structure hiding in free text.
    longest = [h for h, c, dist in d.populated if dist > 1 and
               max((len(v) for v in _values(rows, h)), default=0) > 40]
    for h in longest[:4]:
        vals = _values(rows, h)
        serials = sorted({m for v in vals for m in DATE_SERIAL.findall(v)})
        labels = collections.Counter()
        for v in vals:
            for line in re.split(r"[\n;|]", v):
                m = PERSONISH.match(line)
                if m:
                    labels[m.group(1).strip()] += 1
        info: dict[str, Any] = {}
        if serials:
            info["date_serials"] = {
                s: str(EXCEL_EPOCH + timedelta(days=int(s))) for s in serials[:6]}
            d.findings.append(Finding(
                "warning", "dates buried in text",
                f"{h!r} contains raw spreadsheet date serials "
                f"({', '.join(serials[:3])} = "
                f"{', '.join(info['date_serials'][s] for s in serials[:3])}). "
                f"They are invisible to every date field in the tracker."))
        if labels:
            info["inline_fields"] = dict(labels.most_common(8))
            d.findings.append(Finding(
                "note", "fields inlined in text",
                f"{h!r} carries key:value pairs — " +
                ", ".join(f"{k} ({v})" for k, v in labels.most_common(4)) +
                ". These are real fields the export does not have columns for."))
        if info:
            d.text_structure[h] = info

    return d


def title_convention(titles: list[str], separators=("—", "–", " - ", ":")) -> dict:
    """Do titles follow `Area <sep> detail`, and how often does the head repeat?"""
    heads, split = [], 0
    for t in titles:
        head = t
        for sep in separators:
            if sep in t:
                head = t.split(sep, 1)[0]
                split += 1
                break
        heads.append(head.strip())
    counts = collections.Counter(heads)
    shared = {h: c for h, c in counts.items() if c > 1}
    return {
        "titles": len(titles),
        "with_separator": split,
        "distinct_heads": len(counts),
        "shared_heads": len(shared),
        "rows_under_a_shared_head": sum(shared.values()),
        "worst": counts.most_common(5),
    }


def render(d: Diagnosis) -> str:
    out = ["=" * 72, "EXPORT DIAGNOSTIC", "=" * 72, ""]
    out.append(f"  {d.rows} rows, {d.columns} columns, "
               f"{len(d.populated)} of them populated")
    out.append("")

    order = {"blocker": 0, "warning": 1, "note": 2}
    for f in sorted(d.findings, key=lambda f: order.get(f.level, 3)):
        out.append(f"  [{f.level.upper():<7}] {f.topic}")
        for line in _wrap(f.detail, 66):
            out.append(f"            {line}")
        out.append("")

    out += ["-" * 72, "COLUMNS THAT CARRY ANYTHING", ""]
    for h, count, distinct in d.populated[:20]:
        flag = "  (constant)" if distinct == 1 and count == d.rows else ""
        out.append(f"  {count:>5}/{d.rows:<5} {h:<28} {distinct:>4} distinct{flag}")

    if d.relationships:
        out += ["", "-" * 72, "RELATIONSHIP FIELDS", ""]
        for h, v in sorted(d.relationships.items()):
            out.append(f"  {v:>5}/{d.rows:<5} {h}")

    if d.schedule or d.automatic:
        out += ["", "-" * 72, "SCHEDULE FIELDS vs TRACKER TIMESTAMPS", ""]
        for h, v in sorted(d.schedule.items()):
            out.append(f"  {v:>5}/{d.rows:<5} {h}   (planning)")
        for h, v in sorted(d.automatic.items()):
            out.append(f"  {v:>5}/{d.rows:<5} {h}   (set by the tracker)")

    if d.partitions:
        out += ["", "-" * 72, "COLUMNS THAT GROUP THE ROWS", ""]
        for h, distinct, cov in d.partitions[:8]:
            runs = d.order_runs.get(h)
            tail = f"   rows arrive in {runs[0]} runs" if runs else ""
            out.append(f"  {h:<28} {distinct:>4} groups, "
                       f"{cov * 100:4.0f}% filled{tail}")

    if d.title_convention:
        t = d.title_convention
        out += ["", "-" * 72, "TITLE CONVENTION", ""]
        out.append(f"  {t['with_separator']}/{t['titles']} titles use "
                   f"'Area — detail'")
        out.append(f"  {t['distinct_heads']} distinct heads; "
                   f"{t['rows_under_a_shared_head']} rows sit under a head "
                   f"shared with another row")
        for h, c in t["worst"][:4]:
            out.append(f"     {c:>3} x  {h[:52]}")
    return "\n".join(out)


def _wrap(text: str, width: int) -> list[str]:
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out
