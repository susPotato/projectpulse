"""Channel 1: what it costs when two projects need the same person.

The one legitimate cross-project claim this product can make from data it
actually has. Two projects sharing a person is observable; two projects being
"related" is not.

Pure, like everything else under `intelligence/` bar the pipeline: the caller
loads allocations and hands them over. That is what makes the arithmetic below
testable against the design document's own worked examples, which is the only
reason to trust it.

---

**The quantity is effort, not delay.** For person *p* in window *w*:

    S  = supply, effort-days p can actually deliver in w
    D  = Σ dᵢ, demand on p in w across every project
    E  = D − S, the excess. No contention when E ≤ 0.

`E` is conserved, and each victim's share `sᵢ` sums back to it. The signal is
`contention_pressure`, in effort-days.

Delay in days is a *scenario*, not a finding, and it is reported only with the
assumption that produces it named beside it. The observed order in which an
organisation absorbs overload is overtime, then descope, then quality
shortcuts, and only last a visible slip - so a PM told "SAIN slips 13 days"
watches the team work 残業 for a fortnight and hit the date, and the tool is
wrong on its first demo. :class:`Absorption` carries that assumption.

**Apportionment, never replication.** Each victim takes a *share* of the
excess. Handing every victim the whole of it - the obvious implementation -
manufactures consequence out of nothing: three projects sharing an overloaded
person would produce two victims each absorbing the full excess, so 15
effort-days of overload becomes 30 effort-days of damage, and adding a project
that demands nothing doubles the reported delay. :func:`apportion` conserves
`E` by construction and :func:`ContentionResult.check` asserts it.

**Two modes, genuinely different computations.** Mode A apportions by demand
share and protects nobody; it is the default, because a written priority order
is usually stale and the live one is political. Mode B walks a trustworthy
order and lets the waterfall decide who the victims are. Mode B is *not* Mode A
with the top project lifted out: with A > B > C each demanding 10 against a
supply of 20, strict priority gives B its full 10 and C nothing, while
protecting only A and sharing the rest takes 5 from each of B and C. An order
trustworthy enough to protect A ranks B against C too.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

from app.units import EFFORT_DAYS, Effort, ProgramUnits, units_for

#: Mode names, as stored on a result so a screen can say which ran.
MODE_SHARE = "proportional"
MODE_PRIORITY = "priority"

#: 36協定 ceilings, as monthly overtime hours. Hard limits under the 2019
#: labour-standards reform rather than cultural norms, which is what makes an
#: absorption scenario checkable instead of speculative: a 特別条項 relaxes the
#: 45-hour month but stays bounded by 720 hours a year, under 100 in any single
#: month, and an 80-hour average across any 2-6 months.
OVERTIME_MONTHLY_LIMIT = 45.0
OVERTIME_MONTHLY_SPECIAL_LIMIT = 100.0


def normalize_person(name: str) -> str:
    """A person's name reduced to a comparison key.

    `山田太郎` in a merged schedule cell, `Yamada Taro` in an estimation
    workbook and a Jira `accountId` are one human and no natural key connects
    them - that is a person alias table's job, and this is not it. What this
    *does* fix is the narrower and far more common case: the same spelling
    arriving with different width, case or spacing, which a raw `group by
    resource_name` treats as two people and so silently halves their load.

    NFKC folds full-width ASCII to half-width, which is what you want for
    `Ｔｒａｎ　Ｑｕｏｃ　Ｂ`. It pushes half-width katakana the other way,
    which is fine, and worth knowing when debugging.
    """
    folded = unicodedata.normalize("NFKC", name or "")
    return " ".join(folded.split()).casefold()


@dataclass(frozen=True)
class Allocation:
    """One person's committed share of one project over one window.

    `window_source` records whether the dates were stated in a resource plan or
    derived from the project's own schedule, for the same reason entity
    resolution records which strategy matched: when a PM disputes a contention
    finding, the first question is which dates it used, and "derived" is a
    different conversation from "you told us".
    """

    person: str
    project_id: str
    project_name: str
    allocation_percent: float
    window_start: date
    window_end: date
    role: str | None = None
    #: 'stated' | 'derived_from_schedule'
    window_source: str = "stated"

    @property
    def key(self) -> str:
        return normalize_person(self.person)


@dataclass(frozen=True)
class Window:
    """The period contention is assessed over. Inclusive on both ends."""

    start: date
    end: date

    def label(self) -> str:
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


@dataclass(frozen=True)
class Demand:
    """One project's claim on a person inside the assessed window."""

    project_id: str
    project_name: str
    effort_days: float
    allocation_percent: float
    #: Working days of this allocation that fall inside the assessed window.
    overlap_days: int
    window_source: str = "stated"


@dataclass(frozen=True)
class Shortfall:
    """What one project does not get, and the delay that would follow.

    `effort_days` is `sᵢ` - the conserved, additive quantity, and the one the
    impact edge stores.

    `delay_days` is `Δᵢ`, and it is **not additive**: not across victims, where
    summing it re-creates the replication error this module exists to remove,
    and not across windows either, because a window's Δ assumes the shortfall
    lands in a later window that has room. Under sustained contention the
    shortfalls queue and compound, so per-window Δ understates.
    """

    project_id: str
    project_name: str
    effort_days: float
    delay_days: float
    demand_days: float


@dataclass(frozen=True)
class Absorption:
    """How the organisation would absorb a shortfall, stated not assumed.

    Exists so a delay number can never be rendered without the assumption that
    produced it. `overtime_hours` is what holding the date would cost the person
    if they absorbed the whole shortfall themselves, and `breaches_limit` says
    whether that is even legal - which turns "B might slip" into "B holds only
    if Tanaka goes to 52 overtime hours this month", a sentence a PM can act on.
    """

    assumption: str
    overtime_hours: float = 0.0
    breaches_monthly_limit: bool = False
    breaches_special_limit: bool = False

    def describe(self) -> str:
        if self.overtime_hours <= 0:
            return self.assumption
        text = (
            f"{self.assumption}: {self.overtime_hours:.0f} overtime hours in the window"
        )
        if self.breaches_special_limit:
            return text + f" - past the {OVERTIME_MONTHLY_SPECIAL_LIMIT:.0f}h 特別条項 ceiling"
        if self.breaches_monthly_limit:
            return text + f" - past the {OVERTIME_MONTHLY_LIMIT:.0f}h 36協定 limit"
        return text


@dataclass(frozen=True)
class ContentionResult:
    """One person, one window: the excess and who absorbs it."""

    person: str
    window: Window
    mode: str
    supply_days: float
    demand_days: float
    excess_days: float
    working_days: int
    shortfalls: tuple[Shortfall, ...] = field(default_factory=tuple)
    absorption: Absorption | None = None
    #: Set when supply is zero: every demand is a shortfall, and no rate exists
    #: to turn effort into duration, so Δ is deliberately absent rather than
    #: infinite.
    no_supply: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def contended(self) -> bool:
        return self.excess_days > 0

    @property
    def projects(self) -> tuple[str, ...]:
        return tuple(s.project_name for s in self.shortfalls)

    def pressure_for(self, project_id: str) -> float:
        """`contention_pressure` for one project, in effort-days."""
        return sum(
            s.effort_days for s in self.shortfalls if s.project_id == project_id
        )

    def check(self) -> None:
        """Assert the invariants the formulas are supposed to hold.

        Called by the pipeline rather than left as a comment. `Σ sᵢ = E` is what
        makes the quantity conserved, and `Δ ≤ L` is what keeps a delay inside
        the window it was computed for - an earlier formulation could produce 40
        working days of delay inside a 20-day window and needed a clamp to stay
        bounded. The waterfall removes the need for the clamp, so if either of
        these fires the allocation step is wrong and the number must not ship.
        """
        if not self.shortfalls:
            return
        total = sum(s.effort_days for s in self.shortfalls)
        assert abs(total - self.excess_days) < 1e-6, (
            f"shortfalls sum to {total}, excess is {self.excess_days} - "
            "apportionment is not conserving the excess (replication bug)"
        )
        if not self.no_supply:
            for s in self.shortfalls:
                assert s.delay_days <= self.working_days + 1e-6, (
                    f"{s.project_name} delay {s.delay_days} exceeds the "
                    f"{self.working_days}-working-day window"
                )


def demands_in_window(
    allocations: Sequence[Allocation],
    window: Window,
    units: ProgramUnits,
) -> list[Demand]:
    """Each project's demand on one person inside `window`, in effort-days.

    **Windows are the half that used to be missing.** A conflict detected by
    summing `allocation_percent` across a person's rows and testing `> 100`
    ignores the dates entirely, so two 60% allocations in non-overlapping
    quarters read as a 120% conflict that does not exist, while a genuinely
    simultaneous 50% and 40% reads as clean. Demand is attributed pro-rata by
    working-day overlap - the design's one stated rule for reconciling projects
    that plan at different granularities - so both cases come out right.

    Zero-overlap allocations are dropped rather than carried as zero: a project
    making no claim in this window is not a contender in it, and keeping it
    would put a project with no demand into the victim list.
    """
    out: list[Demand] = []
    for alloc in allocations:
        overlap = units.calendar.overlap_working_days(
            alloc.window_start, alloc.window_end, window.start, window.end
        )
        if overlap <= 0:
            continue
        effort = units.percent_to_effort_days(alloc.allocation_percent, overlap)
        if effort.value <= 0:
            continue
        out.append(
            Demand(
                project_id=alloc.project_id,
                project_name=alloc.project_name,
                effort_days=effort.value,
                allocation_percent=alloc.allocation_percent,
                overlap_days=overlap,
                window_source=alloc.window_source,
            )
        )
    # Deterministic order, ending in the project id: invariant I1 requires a
    # tiebreak on every sort used in a computation, and demand ties are common
    # (two projects at the same percent over the same window).
    out.sort(key=lambda d: (-d.effort_days, d.project_id))
    return out


def apportion(
    *,
    person: str,
    window: Window,
    demands: Sequence[Demand],
    units: ProgramUnits,
    priority: Sequence[str] | None = None,
    month_to_date_overtime_hours: float = 0.0,
) -> ContentionResult:
    """The excess on one person in one window, shared among the projects losing it.

    `priority` is a project-id order, best first, and passing it selects Mode B.
    Omit it - the default - for Mode A, which protects nobody and apportions by
    demand share. Mode A degrades gracefully when priority is unknown and is
    symmetric, so no PM is singled out by an arbitrary tiebreak.
    """
    working = units.calendar.working_days(window.start, window.end)
    concurrent = len({d.project_id for d in demands})
    supply = units.supply_effort_days(working, concurrent_projects=concurrent)
    total_demand = sum(d.effort_days for d in demands)
    excess = total_demand - supply.value

    notes: list[str] = []
    if any(d.window_source != "stated" for d in demands):
        notes.append(
            "some allocation windows were derived from the project schedule, "
            "not stated in a resource plan"
        )
    notes.append(
        f"supply discounted by {supply.factor:.2f} ({supply.factor_name})"
    )

    base = ContentionResult(
        person=person,
        window=window,
        mode=MODE_PRIORITY if priority else MODE_SHARE,
        supply_days=round(supply.value, 4),
        demand_days=round(total_demand, 4),
        excess_days=round(max(0.0, excess), 4),
        working_days=working,
        notes=tuple(notes),
    )

    if excess <= 1e-9:
        return base

    # `S = 0`: a person with no availability in the window. Every demand is a
    # shortfall, and there is no rate with which to turn effort into duration,
    # so report the contention and leave Δ at zero rather than dividing.
    if supply.value <= 0:
        shortfalls = tuple(
            Shortfall(
                project_id=d.project_id,
                project_name=d.project_name,
                effort_days=round(d.effort_days, 4),
                delay_days=0.0,
                demand_days=round(d.effort_days, 4),
            )
            for d in demands
            if d.effort_days > 0
        )
        return _with(
            base,
            shortfalls=shortfalls,
            no_supply=True,
            notes=base.notes + ("no availability in this window; duration not computed",),
        )

    if priority:
        shortfalls = _waterfall(demands, supply.value, priority)
    else:
        shortfalls = _by_share(demands, excess, working)

    result = _with(
        base,
        shortfalls=shortfalls,
        absorption=_absorb(
            sum(s.effort_days for s in shortfalls),
            units,
            month_to_date_overtime_hours,
        ),
    )
    result.check()
    return result


def _by_share(
    demands: Sequence[Demand], excess: float, working: int
) -> tuple[Shortfall, ...]:
    """Mode A: nobody protected, shortfall apportioned by demand share.

        sᵢ = E · dᵢ / Dv      Δ = E · L / Dv

    Every victim shows the *same* duration, which is not a bug: the per-project
    rate cancels, since `sᵢ ÷ (dᵢ / L)` reduces to `E · L / Dv`.
    """
    victims = [d for d in demands if d.effort_days > 0]
    dv = sum(d.effort_days for d in victims)
    # `Dv = 0` alongside `E > 0` is unreachable - an excess means somebody
    # demanded something - so this is an assertion, not a handled case.
    assert dv > 0, "excess with no demand is impossible; the caller built demands wrong"

    duration = excess * working / dv

    # The shares are rounded for display, and rounding does not conserve: three
    # victims at 3.3333 sum to 9.9999, not to an excess of 10. That matters twice
    # over - the conservation assertion is written against the values that ship,
    # and a PM totalling the column on screen must get the excess back. So the
    # last victim absorbs the rounding remainder. Deterministic because `victims`
    # is already ordered with the project id as its final tiebreak (I1).
    shortfalls: list[Shortfall] = []
    running = 0.0
    for index, d in enumerate(victims):
        if index == len(victims) - 1:
            share = round(excess - running, 4)
        else:
            share = round(excess * d.effort_days / dv, 4)
            running += share
        shortfalls.append(
            Shortfall(
                project_id=d.project_id,
                project_name=d.project_name,
                effort_days=share,
                delay_days=round(duration, 2),
                demand_days=round(d.effort_days, 4),
            )
        )
    return tuple(shortfalls)


def _waterfall(
    demands: Sequence[Demand], supply: float, priority: Sequence[str]
) -> tuple[Shortfall, ...]:
    """Mode B: walk a trustworthy order, `aᵢ = min(dᵢ, supply remaining)`.

        sᵢ = dᵢ − aᵢ          Δᵢ = sᵢ · L / dᵢ

    The first project to receive less than it asked for, and every project below
    it, are the victims. A project absent from `priority` sorts last, by id, so
    the order is total and the result deterministic (I1).
    """
    rank = {pid: i for i, pid in enumerate(priority)}
    ordered = sorted(
        (d for d in demands if d.effort_days > 0),
        key=lambda d: (rank.get(d.project_id, len(rank)), d.project_id),
    )

    remaining = supply
    shortfalls: list[Shortfall] = []
    for d in ordered:
        granted = min(d.effort_days, max(0.0, remaining))
        remaining -= granted
        missing = d.effort_days - granted
        if missing <= 1e-9:
            continue
        # Δᵢ = sᵢ · L / dᵢ, with L recovered from this project's own rate.
        # Using the project's demand as the denominator is what keeps Δ inside
        # the window; normalizing by total supply instead understates the delay
        # by `Dv / S`, which is below 1 almost always.
        rate = d.effort_days / d.overlap_days if d.overlap_days else 0.0
        shortfalls.append(
            Shortfall(
                project_id=d.project_id,
                project_name=d.project_name,
                effort_days=round(missing, 4),
                delay_days=round(missing / rate, 2) if rate > 0 else 0.0,
                demand_days=round(d.effort_days, 4),
            )
        )
    return tuple(shortfalls)


def _absorb(
    shortfall_days: float, units: ProgramUnits, month_to_date: float
) -> Absorption:
    """The default absorption assumption: overtime first, and what it costs.

    Overtime is the assumption worth modelling because in Japan the ceiling is
    checkable rather than cultural, so the finding can be stated as capacity
    *and* legality instead of as a guess about how the team will respond.
    """
    hours = shortfall_days * units.hours_per_person_day
    projected = month_to_date + hours
    return Absorption(
        assumption="overtime absorbs the shortfall before the date moves",
        overtime_hours=round(hours, 2),
        breaches_monthly_limit=projected > OVERTIME_MONTHLY_LIMIT,
        breaches_special_limit=projected > OVERTIME_MONTHLY_SPECIAL_LIMIT,
    )


def _with(result: ContentionResult, **changes) -> ContentionResult:
    from dataclasses import replace

    return replace(result, **changes)


def assess(
    allocations: Sequence[Allocation],
    window: Window,
    *,
    program_id: str | None = None,
    priority: Sequence[str] | None = None,
    overtime_by_person: dict[str, float] | None = None,
) -> list[ContentionResult]:
    """Every contended person in one window, worst first.

    The entry point the pipeline calls. Groups by normalized name so one person
    spelled two ways is one person, drops anybody working on a single project
    (contention needs two claimants by definition), and returns only those with
    a real excess.
    """
    units = units_for(program_id)
    overtime = overtime_by_person or {}

    by_person: dict[str, list[Allocation]] = {}
    display: dict[str, str] = {}
    for alloc in allocations:
        key = alloc.key
        if not key:
            continue
        by_person.setdefault(key, []).append(alloc)
        # First spelling seen wins as the label, so the screen shows a name a
        # human typed rather than the casefolded comparison key.
        display.setdefault(key, alloc.person)

    results: list[ContentionResult] = []
    for key, allocs in by_person.items():
        demands = demands_in_window(allocs, window, units)
        if len({d.project_id for d in demands}) < 2:
            continue
        result = apportion(
            person=display[key],
            window=window,
            demands=demands,
            units=units,
            priority=priority,
            month_to_date_overtime_hours=overtime.get(key, 0.0),
        )
        if result.contended:
            results.append(result)

    results.sort(key=lambda r: (-r.excess_days, normalize_person(r.person)))
    return results


@dataclass(frozen=True)
class ProgramContext:
    """The program a project is analysed inside.

    The design's shape for the program layer: *a context object passed into the
    same per-project function*, not a second computation over the same data.
    That is why drilling into a project shows the numbers the rollup used - the
    rollup is the project view folded up, and both read this.

    `None` for `program_id` is a project no program claims. Passing no context
    at all is also legal, and `analyze_project` then reports zero contention
    with `has_program_context` false, which a rule can test - a project analysed
    without its program is not a project with no contention, and the two must
    not render identically.

    Note what this deliberately does *not* iterate. Channel 1 depends on
    allocations and calendars, not on findings, so building it needs no fixed
    point: the apportionment is the same whatever the per-project analysis
    concludes. A fixed point becomes necessary only once an induced effect
    changes demand - Channel 2's float pool and the dependency traversal that
    turns a shortfall into milestone movement - and neither is computed here.
    """

    program_id: str | None = None
    program_name: str = ""
    units: ProgramUnits = field(default_factory=lambda: units_for(None))
    contention: tuple[ContentionResult, ...] = field(default_factory=tuple)
    #: Project id -> `contention_pressure` in effort-days.
    pressure: dict[str, float] = field(default_factory=dict)
    #: Project ids in this program, so a caller can tell "not in the program"
    #: from "in the program with no pressure".
    project_ids: frozenset[str] = field(default_factory=frozenset)

    def pressure_for(self, project_ids: Sequence[str]) -> float:
        """Pressure on one delivery project, summed over all of its source ids.

        Takes every source id rather than the canonical one because allocation
        rows may be filed under whichever id the collector that wrote them had -
        the same invariant-7 reading `scope.source_ids_for` exists for.
        """
        return round(sum(self.pressure.get(pid, 0.0) for pid in project_ids), 4)

    def results_for(self, project_ids: Sequence[str]) -> tuple[ContentionResult, ...]:
        wanted = set(project_ids)
        return tuple(
            r
            for r in self.contention
            if any(s.project_id in wanted for s in r.shortfalls)
        )


def month_periods(start: date, end: date) -> list[Window]:
    """`[start, end]` cut into calendar months.

    Contention has to be assessed **per period, not over one long span**, and
    the period has to be a month.

    Over one span the arithmetic quietly undoes the windowing it was supposed to
    fix: a person allocated 60% in January and 50% in March has no conflict, but
    pooled into a single January-to-April window their demands coexist and the
    excess reappears - the same false positive as summing percentages with no
    dates at all, just harder to see.

    A month specifically, because that is the period the answer is denominated
    in. The 36協定 overtime ceiling this model checks against is monthly, so a
    quarter-long window would compare a quarter's overtime against a month's
    limit and breach it on arithmetic alone.
    """
    if end < start:
        return []
    out: list[Window] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first = date(year, month, 1)
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        last = nxt - timedelta(days=1)
        out.append(Window(max(first, start), min(last, end)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def assess_periods(
    allocations: Sequence[Allocation],
    periods: Sequence[Window],
    *,
    program_id: str | None = None,
    priority: Sequence[str] | None = None,
    overtime_by_person: dict[str, float] | None = None,
) -> list[ContentionResult]:
    """Contention in each period, worst first across all of them.

    One result per contended person per period. `sᵢ` is additive across periods -
    it is an effort quantity - so a caller may total the pressure. `Δ` is not,
    for the reason on :class:`Shortfall`: under sustained contention shortfalls
    queue and compound, so summing per-period Δ understates rather than
    overstates, and it must be reported per period or as a worst case.
    """
    out: list[ContentionResult] = []
    for period in periods:
        out.extend(
            assess(
                allocations,
                period,
                program_id=program_id,
                priority=priority,
                overtime_by_person=overtime_by_person,
            )
        )
    out.sort(
        key=lambda r: (-r.excess_days, normalize_person(r.person), r.window.start)
    )
    return out


def pressure_by_project(results: Sequence[ContentionResult]) -> dict[str, float]:
    """Total `contention_pressure` per project, in effort-days.

    Additive because `sᵢ` is the conserved quantity - unlike Δ, which must never
    be summed. Keyed by project id so the per-project analysis can look up its
    own pressure without re-running the apportionment.
    """
    out: dict[str, float] = {}
    for result in results:
        for s in result.shortfalls:
            out[s.project_id] = round(out.get(s.project_id, 0.0) + s.effort_days, 4)
    return out


def as_effort(value: float) -> Effort:
    """Wrap a pressure figure as a unit-carrying quantity for display."""
    return Effort(value=value, unit=EFFORT_DAYS, factor=1.0, factor_name="identity")
