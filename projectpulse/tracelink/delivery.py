"""Stage 4e — what the work record says about effort, and about output.

`progress` reads the tasks. This reads what they add up to: how long the
work took, when it actually happened against when it was planned, and
whether the things the finished tasks say they produced are there.

**A recorded interval is not effort.** Everything here measures the gap
between two timestamps somebody typed. On the project this was built
against the median is ten minutes and three tasks record one — including
"write the Architecture ADR". That may be an AI-assisted team working at a
pace the format was not designed for, or timestamps filled in afterwards,
and no arithmetic here can tell those apart. So the distribution is
reported and the reader judges, with one exception: two intervals that
overlap for the same owner cannot both be elapsed work, and that is a
statement about the record rather than about the people, so it is flagged.

The second half is narrower and sharper. A completed task names what it
produced; some of those names are files. Checking them is the same
resolution the rest of the pipeline does, but split by what kind of thing
is missing, because "no test file" and "no source file" are different
findings with different owners — and a Definition of Done that requires
passing tests is worth nothing if the tests it counts do not exist.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from dataclasses import dataclass, field

from tracelink.artifacts import ProgressReport, WorkItem
from tracelink.corpus import looks_like_test

STAMP = "%Y-%m-%d %H:%M"
# A day and month written the way a plan column writes them: "24/08".
PLANNED_DAY = re.compile(r"(\d{1,2})[/-](\d{1,2})")

KIND_TEST = "test"
KIND_DOC = "doc"
KIND_CODE = "code"
KIND_SYMBOL = "symbol"


def _parse(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(value, STAMP)
    except (ValueError, TypeError):
        return None


@dataclass
class TaskEffort:
    wid: str
    group: str
    owner: str
    started: str
    ended: str
    minutes: float
    #: Structural problems with the *record*, never with the work.
    flags: list[str] = field(default_factory=list)


@dataclass
class Overlap:
    owner: str
    first: str
    second: str
    minutes: float


@dataclass
class Slip:
    """A plan row whose actual start is not on the day it was planned for."""

    group: str
    title: str
    planned: str
    started: str
    days: int


@dataclass
class Unmet:
    """Something a finished task says it produced, which is not there."""

    wid: str
    name: str
    kind: str
    owner: str
    doc: str
    line: int


@dataclass
class DeliveryReport:
    tasks: list[TaskEffort] = field(default_factory=list)
    overlaps: list[Overlap] = field(default_factory=list)
    slips: list[Slip] = field(default_factory=list)
    unmet: list[Unmet] = field(default_factory=list)
    by_day: dict[str, int] = field(default_factory=dict)
    idle_days: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> list[float]:
        return sorted(t.minutes for t in self.tasks)


# --------------------------------------------------------------------------
# Effort
# --------------------------------------------------------------------------

def _intervals(items: list[WorkItem]) -> list[tuple[WorkItem, dt.datetime, dt.datetime]]:
    out = []
    for i in items:
        a, b = _parse(i.started), _parse(i.ended)
        if a and b:
            out.append((i, a, b))
    return out


def find_overlaps(pairs) -> list[Overlap]:
    """Two intervals that overlap for one owner.

    The only claim here that does not depend on judgement. A person or a
    team cannot spend the same minute on two tasks, so where the record
    says they did, at least one of those intervals is not elapsed work —
    it is an estimate, a backfill, or two people under one name. Which of
    those it is, the record cannot say; that it is one of them, it can.
    """
    by_owner: dict[str, list] = collections.defaultdict(list)
    for item, a, b in pairs:
        if item.owner:
            by_owner[item.owner].append((a, b, item.wid))

    out: list[Overlap] = []
    for owner, spans in by_owner.items():
        spans.sort()
        for i in range(len(spans) - 1):
            a1, b1, w1 = spans[i]
            for a2, b2, w2 in spans[i + 1:]:
                if a2 >= b1:
                    break
                shared = (min(b1, b2) - a2).total_seconds() / 60
                if shared > 0:
                    out.append(Overlap(owner=owner, first=w1, second=w2,
                                       minutes=round(shared, 1)))
    return sorted(out, key=lambda o: -o.minutes)


def _calendar(pairs) -> tuple[dict[str, int], list[str]]:
    """Activity per day, and the days inside the span with none.

    A gap in the middle of a delivery window is a fact worth surfacing:
    either nobody worked, or nobody recorded it.
    """
    counts: collections.Counter = collections.Counter()
    for _, a, b in pairs:
        counts[a.date().isoformat()] += 1
        if b.date() != a.date():
            counts[b.date().isoformat()] += 1
    if not counts:
        return {}, []
    days = sorted(counts)
    first = dt.date.fromisoformat(days[0])
    last = dt.date.fromisoformat(days[-1])
    idle = []
    cursor = first
    while cursor <= last:
        if cursor.isoformat() not in counts:
            idle.append(cursor.isoformat())
        cursor += dt.timedelta(days=1)
    return dict(sorted(counts.items())), idle


def find_slips(report: ProgressReport) -> list[Slip]:
    """Plan rows whose work began on a different day than planned.

    The plan column carries a day and a month and no year, so the year is
    taken from the actual start. That is the only reading available and it
    is right except across a new year, where it is reported as a large
    number rather than silently wrong.
    """
    out: list[Slip] = []
    for s in report.schedule:
        started = _parse(s.started)
        m = PLANNED_DAY.search(s.planned or "")
        if not started or not m:
            continue
        day, month = int(m.group(1)), int(m.group(2))
        try:
            planned = dt.date(started.year, month, day)
        except ValueError:
            continue
        delta = (started.date() - planned).days
        if delta:
            out.append(Slip(group=s.group.split(" / ")[-1], title=s.title[:70],
                            planned=s.planned, started=s.started, days=delta))
    return sorted(out, key=lambda s: -abs(s.days))


# --------------------------------------------------------------------------
# Output: what a finished task says it produced
# --------------------------------------------------------------------------

def classify(name: str, kind: str, known_docs: set[str] | None = None) -> str:
    if kind != "path":
        return KIND_SYMBOL
    if looks_like_test(name):
        return KIND_TEST
    if (known_docs and name in known_docs) or name.lower().endswith(
            (".md", ".rst", ".txt", ".adoc")):
        return KIND_DOC
    return KIND_CODE


def find_unmet(report: ProgressReport,
               known_docs: set[str] | None = None) -> list[Unmet]:
    """Deliverables of *completed* tasks that did not resolve.

    Only completed ones: an open task has not claimed to have produced
    anything yet, so its missing output is the plan, not a finding. The
    same distinction `drift` makes between a grounded document and a
    design.
    """
    out: list[Unmet] = []
    for item in report.items:
        if not item.done:
            continue
        for c in item.deliverables:
            if c.resolved or c.kind == "docref":
                continue
            out.append(Unmet(wid=item.wid, name=c.name,
                             kind=classify(c.name, c.kind, known_docs),
                             owner=item.owner, doc=item.doc, line=item.line))
    order = {KIND_TEST: 0, KIND_CODE: 1, KIND_SYMBOL: 2, KIND_DOC: 3}
    return sorted(out, key=lambda u: (order.get(u.kind, 9), u.wid))


# --------------------------------------------------------------------------

def build(report: ProgressReport,
          known_docs: set[str] | None = None) -> DeliveryReport:
    pairs = _intervals(report.items)
    overlaps = find_overlaps(pairs)
    overlapping = {o.first for o in overlaps} | {o.second for o in overlaps}

    tasks = []
    for item, a, b in pairs:
        minutes = (b - a).total_seconds() / 60
        flags = []
        if minutes < 0:
            flags.append("ends-before-start")
        if item.wid in overlapping:
            flags.append("overlaps another task by the same owner")
        tasks.append(TaskEffort(wid=item.wid, group=item.group.split(" / ")[-1],
                                owner=item.owner, started=item.started,
                                ended=item.ended, minutes=round(minutes, 1),
                                flags=flags))

    by_day, idle = _calendar(pairs)
    return DeliveryReport(tasks=tasks, overlaps=overlaps,
                          slips=find_slips(report),
                          unmet=find_unmet(report, known_docs),
                          by_day=by_day, idle_days=idle)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def summary(d: DeliveryReport) -> dict:
    m = d.minutes
    kinds = collections.Counter(u.kind for u in d.unmet)
    return {
        "timed_tasks": len(m),
        "median_minutes": percentile(m, 0.5),
        "p25_minutes": percentile(m, 0.25),
        "p75_minutes": percentile(m, 0.75),
        "total_hours": round(sum(m) / 60, 1),
        "under_five_minutes": sum(1 for x in m if x < 5),
        "overlapping_tasks": len({o.first for o in d.overlaps}
                                 | {o.second for o in d.overlaps}),
        "idle_days": len(d.idle_days),
        "slipped_rows": len(d.slips),
        "unmet": dict(kinds),
    }
