"""When a backlog is two backlogs in one sheet.

A single corroboration rate over the whole export assumes the rows mean
the same kind of thing. In the CoWorkLocal export they do not. 149 rows
carry a fix version, sit in one contiguous block, are almost all marked
`Release it`, and corroborate at 50%. The other 24 sit in their own block
at the end of the sheet, carry no fix version, are mostly `To Do` or
`In Progress`, are written in Vietnamese, and corroborate at 4%.

The first block is a **record of work already done**. The second is a
**roadmap** - "Add a screen describing the business of the system",
"Integrate remote via Slack". Code for those is missing because it was
never written, so `unverified` is the right answer and not a retrieval
failure. Averaged together they produce one number, 55% unverified, that
describes neither population and reads as though the pipeline failed.

**A seam is found, not configured.** What marks these apart is not the
fix-version field itself but that its values form *contiguous runs* in
sheet order: a column whose values block up that way is recording when the
rows were typed, not a property of the work. So this looks for fields whose
runs are close to their distinct values, which is the shape of two lists
appended rather than one list sorted.

What it deliberately does not do:

* **It does not decide which cohort is which.** "Shipped inventory" and
  "roadmap" are readings of the text, not of the layout. This reports that
  a seam exists, how the groups differ, and where the boundary falls.
* **It does not merge or drop rows.** Both cohorts are real backlog and
  both are reported; the only claim is that one average over them is not
  a description of either.
* **It needs no verdicts.** The seam is in the export. Verdicts, when
  present, are broken down by it rather than used to find it.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any

from tracelink.artifacts import Ticket, Verdict

#: Fields worth testing for a seam. `component` is included because a
#: component column that blocks up is the same phenomenon.
CANDIDATES = ("fix_version", "status", "component", "issue_type", "lang")

#: A field with more distinct values than this is an identifier, not a
#: grouping, and every row is its own run.
MAX_GROUPS = 12

#: Marks a candidate that asks only whether a field is filled in. A sparse
#: column often marks the seam through its *presence* while its values are
#: scattered: CoWorkLocal has four fix versions in ten runs, but "has one
#: at all" splits the sheet in two. Which is the real signal - one list was
#: typed with release metadata and one was not.
PRESENCE = "?"


def value(t: Ticket, field_name: str) -> str:
    """One field of a ticket, wherever the adapter put it.

    A name ending in `?` asks whether the field is set, not what it says.
    """
    if field_name.endswith(PRESENCE):
        base = field_name[:-1]
        return "set" if value(t, base) else "unset"
    if field_name in ("status", "component", "lang"):
        return str(getattr(t, field_name, "") or "")
    return str((t.extra or {}).get(field_name, "") or "")


def _runs(values: list[str]) -> int:
    """How many contiguous stretches of equal value the sequence forms."""
    return sum(1 for i, v in enumerate(values) if i == 0 or values[i - 1] != v)


@dataclass
class Seam:
    """A field whose values block up in sheet order."""

    field: str
    #: Distinct values, and the contiguous runs they form.
    groups: int
    runs: int
    sizes: dict[str, int]
    #: Where each group's rows start and end in the sheet.
    spans: dict[str, tuple[int, int]]

    @property
    def contiguity(self) -> float:
        """1.0 when every value occupies exactly one block of the sheet."""
        return round(self.groups / self.runs, 3) if self.runs else 0.0

    @property
    def is_seam(self) -> bool:
        # Two runs per group still reads as blocked-up; beyond that the
        # field is interleaved and describes the work, not the typing.
        return self.groups > 1 and self.contiguity >= 0.5


def _ordered(tickets: list[Ticket]) -> list[Ticket]:
    """Sheet order. Rows without one keep the order they arrived in."""
    return sorted(tickets, key=lambda t: (t.source_row is None, t.source_row or 0))


def seams(tickets: list[Ticket]) -> list[Seam]:
    """Every candidate field, most contiguous first."""
    rows = _ordered(tickets)
    out: list[Seam] = []
    # Presence first: it is the coarser reading, and when both a field and
    # its presence block up, the two-way split is the one to report.
    for name in [c + PRESENCE for c in CANDIDATES] + list(CANDIDATES):
        vals = [value(t, name) for t in rows]
        distinct = set(vals)
        if len(distinct) < 2 or len(distinct) > MAX_GROUPS:
            continue
        spans: dict[str, tuple[int, int]] = {}
        for t, v in zip(rows, vals):
            if t.source_row is None:
                continue
            lo, hi = spans.get(v, (t.source_row, t.source_row))
            spans[v] = (min(lo, t.source_row), max(hi, t.source_row))
        out.append(Seam(field=name, groups=len(distinct), runs=_runs(vals),
                        sizes=dict(collections.Counter(vals)), spans=spans))
    out.sort(key=lambda s: (-s.contiguity, -s.groups))
    return out


#: An inline key on at least this share of rows is one the sheet expects
#: every row to carry — a role — rather than an occasional note.
ROLE_SHARE = 0.5


def role_keys(tickets: list[Ticket]) -> list[str]:
    """Inline keys this sheet treats as roles, learned rather than named.

    The export has no assignee column; it writes `PO:`, `BA:` and
    `Developer:` inside the description prose. Hardcoding those three would
    report "no roles" for any team that writes `Reviewer` or `Trưởng nhóm`,
    so the test is coverage: a key most rows carry is one the sheet expects,
    and `Ngày nhận` on four rows out of 173 is a note, not a role.
    """
    if not tickets:
        return []
    seen = collections.Counter(k for t in tickets for k in (t.inline or {}))
    bar = len(tickets) * ROLE_SHARE
    return sorted(k for k, n in seen.items() if n >= bar)


def ownership(tickets: list[Ticket],
              keys: list[str] | None = None) -> dict[str, Any]:
    """How concentrated the named roles are, and on whom.

    Distinct from "which rows are nameless", which the product already
    measures. A backlog can name somebody on every row and still be one
    person doing everything — here one name holds 90% of all 493 role
    assignments and is PO, BA *and* Developer on 147 of 149 rows in one
    cohort, and on none at all in the other. Coverage cannot see that.

    `solo` counts only rows where *every* learned role is filled and they
    are all the same person. A row missing its PO is not evidence that one
    person holds all three, and counting it as such inflated this measure
    to 15 of 24 on a cohort whose real answer is zero.

    **`keys` is passed in when comparing cohorts, and must be.** Learned
    per cohort it is learned from different rows: the roadmap block names a
    PO on 4 rows of 24, so `PO` falls under the bar there and `solo` is
    measured over two roles against the other cohort's three. That reported
    11 of 24 beside 147 of 149 — two numbers a reader will compare and
    which do not mean the same thing. One role set over the whole backlog,
    applied to every cohort.
    """
    keys = keys if keys is not None else role_keys(tickets)
    if not keys:
        return {"roles": [], "holders": {}, "solo": 0, "n": len(tickets),
                "top": None, "share": None}

    holders: collections.Counter[str] = collections.Counter()
    solo = 0
    for t in tickets:
        got = {k: (t.inline or {}).get(k) for k in keys}
        holders.update(v for v in got.values() if v)
        if all(got.values()) and len(set(got.values())) == 1:
            solo += 1
    total = sum(holders.values())
    top, count = (holders.most_common(1) or [(None, 0)])[0]
    return {"roles": keys, "holders": dict(holders.most_common()),
            "solo": solo, "n": len(tickets), "top": top,
            "share": round(count / total, 3) if total else None,
            "assignments": total}


@dataclass
class Cohort:
    """One side of a seam, with whatever is known about it."""

    value: str
    label: str
    n: int
    span: tuple[int, int] | None
    status: dict[str, int] = field(default_factory=dict)
    lang: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    #: Who the rows name, and how concentrated that is.
    owners: dict[str, Any] = field(default_factory=dict)

    @property
    def decided(self) -> int:
        return (self.verdicts.get("corroborated", 0)
                + self.verdicts.get("contradicted", 0))

    @property
    def corroboration(self) -> float | None:
        """Share of this cohort's tickets backed by code, or None untested."""
        if not self.verdicts:
            return None
        return round(self.verdicts.get("corroborated", 0) / self.n, 3)

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "n": self.n,
                "span": list(self.span) if self.span else None,
                "status": self.status, "lang": self.lang,
                "verdicts": self.verdicts, "corroboration": self.corroboration,
                "owners": self.owners}


def split(tickets: list[Ticket], seam: Seam,
          verdicts: list[Verdict] | None = None,
          langs: dict[str, str] | None = None) -> list[Cohort]:
    """The cohorts a seam divides the backlog into, largest first.

    `langs` maps uid to the language `translate` detected. It is passed in
    rather than read off the ticket because `translate` records what it
    found in its own artifact and never writes back to `tickets.json`, so
    `Ticket.lang` is "en" for every row even where it is not.
    """
    by_uid = {v.uid: v for v in (verdicts or [])}
    langs = langs or {}
    # One role set, learned over the whole backlog, so each cohort's
    # ownership figure is measured against the same roles as the others.
    keys = role_keys(tickets)
    groups: dict[str, list[Ticket]] = collections.defaultdict(list)
    for t in _ordered(tickets):
        groups[value(t, seam.field)].append(t)

    out: list[Cohort] = []
    for v, members in groups.items():
        rows = [t.source_row for t in members if t.source_row is not None]
        out.append(Cohort(
            value=v,
            label=v or f"(no {seam.field})",
            n=len(members),
            span=(min(rows), max(rows)) if rows else None,
            status=dict(collections.Counter(t.status for t in members)),
            lang=dict(collections.Counter(
                langs.get(t.uid, t.lang) for t in members)),
            verdicts=dict(collections.Counter(
                by_uid[t.uid].verdict for t in members if t.uid in by_uid)),
            owners=ownership(members, keys),
        ))
    out.sort(key=lambda c: -c.n)
    return out


def render(seam: Seam, cohorts: list[Cohort], all_n: int) -> str:
    lines = ["=" * 72, "BACKLOG COHORTS - is this one backlog or two?", "=" * 72, ""]
    lines.append(f"  seam: {seam.field} - {seam.groups} values in "
                 f"{seam.runs} contiguous runs (contiguity {seam.contiguity})")
    lines.append("  a field whose values block up this way records when rows were")
    lines.append("  written, not what the work is")
    lines.append("")

    width = max(len(c.label) for c in cohorts)
    for c in cohorts:
        span = f"rows {c.span[0]}-{c.span[1]}" if c.span else "no row numbers"
        lines.append(f"  {c.label:<{width}}  {c.n:>4} tickets   {span}")
        top = sorted(c.status.items(), key=lambda kv: -kv[1])[:3]
        lines.append(f"  {'':<{width}}  status: "
                     + ", ".join(f"{k} {v}" for k, v in top))
        if len(c.lang) > 1 or "en" not in c.lang:
            lines.append(f"  {'':<{width}}  language: "
                         + ", ".join(f"{k} {v}" for k, v in sorted(c.lang.items())))
        o = c.owners or {}
        if o.get("top"):
            lines.append(f"  {'':<{width}}  one person on every named role: "
                         f"{o['solo']} of {c.n} tickets"
                         f" (busiest: {o['top']}, {o['share']:.0%} of this "
                         f"cohort's role assignments)")
        if c.verdicts:
            rate = c.corroboration
            lines.append(f"  {'':<{width}}  corroborated "
                         f"{c.verdicts.get('corroborated', 0)} ({rate:.0%}), "
                         f"unverified {c.verdicts.get('unverified', 0)}")
        lines.append("")

    tested = [c for c in cohorts if c.verdicts]
    if len(tested) > 1:
        rates = sorted(c.corroboration for c in tested)
        pooled = (sum(c.verdicts.get("corroborated", 0) for c in tested)
                  / sum(c.n for c in tested))
        lines += ["-" * 72, "WHY ONE NUMBER WILL NOT DO", ""]
        lines.append(f"  pooled corroboration {pooled:.0%}, but the cohorts run "
                     f"{rates[0]:.0%} to {rates[-1]:.0%}")
        lines.append("  the pooled figure describes neither cohort, and reads as a")
        lines.append("  retrieval failure where it is a difference in what the rows are")
        lines.append("")
    lines.append(f"  {all_n} tickets in total; no row is dropped or merged.")
    return "\n".join(lines)
