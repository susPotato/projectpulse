"""Effort units and the working-day calendar - one owner, no exceptions.

Every conversion between hours, person-days, person-months and allocation
percent happens here. Nowhere else may hold a factor.

Why this module exists at all, rather than an `* 8` at each call site: a
person-month read as a person-day is a 20x error that looks entirely ordinary
in a chart, and single-value plausibility checks only catch the inflating
direction. The defence is not a cleverer check, it is having exactly one place
where the factor is written down, and making every converted value say which
factor it used.

Three things follow from that, and all three are the point:

**A quantity carries its unit.** :class:`Effort` has no default unit. A
conversion that cannot name its input unit raises rather than guessing, because
a guessed unit is the failure mode above.

**Factors are program-scoped, not global.** One client estimating at 20 days
per 人月 and a vendor estimating at 22 must not be silently pooled, so the
factors hang off a :class:`ProgramUnits` a caller has to name. There is a
default, and it is the one the demo program uses; there is no *implicit* one.

**Percent and effort-days are two denominations of the same quantity.** A
resource plan says "50%", capacity math wants effort-days, and the bridge is
`percent x working days in window`. That bridge lives in
:func:`ProgramUnits.percent_to_effort_days` so it cannot be written twice and
disagree with itself.

The working-day calendar is here for the same reason. A five-day slip across
Golden Week is five calendar days and two working days; the product reports
working days (see the open decision in the design doc), so the calendar that
answers "how many working days in this window" must be the same one that
answers "how late is this task".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Iterable

#: The unit vocabulary. Anything outside this is rejected rather than coerced -
#: a unit string we do not recognise is exactly the case where guessing is
#: worse than failing.
HOURS = "hours"
PERSON_DAYS = "person-days"
PERSON_MONTHS = "person-months"
EFFORT_DAYS = "effort-days"
PERCENT = "percent"

#: `effort-days` and `person-days` are the same dimension under different
#: names: the design doc denominates signals in effort-days and estimation
#: workbooks are written in person-days. Treating them as one unit rather than
#: converting between them keeps the identity factor out of the table below,
#: where a 1.0 would look like a decision somebody could edit.
_EFFORT_DAY_ALIASES = frozenset({PERSON_DAYS, EFFORT_DAYS})

#: How a unit string a human typed maps onto the vocabulary. Deliberately
#: explicit and deliberately short: an unlisted spelling reaches
#: `normalize_unit` and raises, which is how a new spelling gets noticed and
#: added here rather than silently parsed as something else.
_SPELLINGS: dict[str, str] = {
    "h": HOURS,
    "hr": HOURS,
    "hrs": HOURS,
    "hour": HOURS,
    "hours": HOURS,
    "工数": HOURS,
    "時間": HOURS,
    "d": PERSON_DAYS,
    "day": PERSON_DAYS,
    "days": PERSON_DAYS,
    "pd": PERSON_DAYS,
    "person-day": PERSON_DAYS,
    "person-days": PERSON_DAYS,
    "person day": PERSON_DAYS,
    "person days": PERSON_DAYS,
    "man-day": PERSON_DAYS,
    "man-days": PERSON_DAYS,
    "人日": PERSON_DAYS,
    "effort-day": EFFORT_DAYS,
    "effort-days": EFFORT_DAYS,
    "ed": EFFORT_DAYS,
    "pm": PERSON_MONTHS,
    "mm": PERSON_MONTHS,
    "person-month": PERSON_MONTHS,
    "person-months": PERSON_MONTHS,
    "man-month": PERSON_MONTHS,
    "man-months": PERSON_MONTHS,
    "人月": PERSON_MONTHS,
    "%": PERCENT,
    "percent": PERCENT,
    "pct": PERCENT,
}


class UnitError(ValueError):
    """A unit was missing, unrecognised, or wrong for the operation asked for.

    Its own class because the callers that matter - the validator, the
    convertors - need to hold a record at `needs_review` for a unit problem
    while still letting a genuine programming error surface as one.
    """


def normalize_unit(raw: str | None) -> str:
    """Map a unit as written onto the vocabulary, or raise.

    `None` and `""` raise rather than defaulting. That is the whole rule: the
    design's invariant I4 is "every effort quantity carries an explicit unit",
    and a default here would be the one line that repeals it.
    """
    if raw is None:
        raise UnitError(
            "effort quantities must carry an explicit unit; got None. "
            "A parser that cannot find a unit should flag the row, not guess."
        )
    text = str(raw).strip().casefold()
    if not text:
        raise UnitError("effort quantities must carry an explicit unit; got ''")
    # Strip a bracketed or parenthesised unit as it appears in a header cell:
    # "Effort (person-days)" arrives here as "(person-days)" once the caller
    # has taken the parenthesised part.
    text = text.strip("()[]（）【】 ")
    if text in _SPELLINGS:
        return _SPELLINGS[text]
    raise UnitError(
        f"unrecognised effort unit {raw!r}. Known spellings: "
        f"{sorted(set(_SPELLINGS))}. Add the spelling to app.units._SPELLINGS "
        "rather than coercing it at the call site."
    )


# --------------------------------------------------------------------------
# Working-day calendar
# --------------------------------------------------------------------------

#: Japanese public holidays over the demo's horizon, as dates rather than as
#: rules. The rules are genuinely awkward - Golden Week's substitute holidays,
#: the equinoxes being astronomical, Happy Monday moving three of them - and a
#: wrong rule is worse than a short table, because it is wrong silently and in
#: a direction nobody checks. Extend the table; do not compute it.
#:
#: 2026 and 2027 are listed because the demo timeline and every forecast the
#: product draws sit inside them.
JP_HOLIDAYS_2026_2027: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 1),  # 元日
        date(2026, 1, 12),  # 成人の日
        date(2026, 2, 11),  # 建国記念の日
        date(2026, 2, 23),  # 天皇誕生日
        date(2026, 3, 20),  # 春分の日
        date(2026, 4, 29),  # 昭和の日
        date(2026, 5, 3),  # 憲法記念日
        date(2026, 5, 4),  # みどりの日
        date(2026, 5, 5),  # こどもの日
        date(2026, 5, 6),  # 振替休日 (3 May falls on a Sunday)
        date(2026, 7, 20),  # 海の日
        date(2026, 8, 11),  # 山の日
        date(2026, 9, 21),  # 敬老の日
        date(2026, 9, 22),  # 国民の休日
        date(2026, 9, 23),  # 秋分の日
        date(2026, 10, 12),  # スポーツの日
        date(2026, 11, 3),  # 文化の日
        date(2026, 11, 23),  # 勤労感謝の日
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 11),
        date(2027, 2, 11),
        date(2027, 2, 23),
        date(2027, 3, 21),
        date(2027, 3, 22),  # 振替休日
        date(2027, 4, 29),
        date(2027, 5, 3),
        date(2027, 5, 4),
        date(2027, 5, 5),
        date(2027, 7, 19),
        date(2027, 8, 11),
        date(2027, 9, 20),
        date(2027, 9, 23),
        date(2027, 10, 11),
        date(2027, 11, 3),
        date(2027, 11, 23),
    }
)


@dataclass(frozen=True)
class Calendar:
    """Which days count as working days.

    `weekend` is a set of `date.weekday()` values so a six-day week - not
    unheard of on an offshore delivery - is expressible without a second class.
    """

    holidays: frozenset[date] = field(default_factory=frozenset)
    weekend: frozenset[int] = frozenset({5, 6})
    name: str = "default"

    def is_working_day(self, day: date) -> bool:
        return day.weekday() not in self.weekend and day not in self.holidays

    def working_days(self, start: date, end: date) -> int:
        """Working days in the inclusive window `[start, end]`.

        Inclusive on both ends because that is how a resource plan reads: an
        allocation from 1 April to 30 April covers both days. An end before the
        start is an empty window, not a negative count - the callers that can
        produce one (a task whose dates a human typed backwards) are better
        served by zero than by arithmetic that flips sign.
        """
        if end < start:
            return 0
        return sum(
            1
            for day in _days_between(start, end)
            if self.is_working_day(day)
        )

    def overlap_working_days(
        self, a_start: date, a_end: date, b_start: date, b_end: date
    ) -> int:
        """Working days two inclusive windows share.

        The design doc's one stated rule for attributing demand across
        mismatched planning granularities is pro-rata by working-day overlap,
        and this is that overlap. Here rather than at the call site so the
        Channel 1 arithmetic and any future window alignment cannot disagree
        about what "overlap" means.
        """
        start = max(a_start, b_start)
        end = min(a_end, b_end)
        return self.working_days(start, end)


def _days_between(start: date, end: date) -> Iterable[date]:
    day = start
    step = timedelta(days=1)
    while day <= end:
        yield day
        day += step


#: The calendar every program gets unless it names another. Japanese holidays
#: because the client this product is built for is Japanese and the alternative
#: default - "weekends only" - understates Golden Week by four days in the
#: middle of the demo's own horizon.
JP_CALENDAR = Calendar(holidays=JP_HOLIDAYS_2026_2027, weekend=frozenset({5, 6}), name="jp")

#: For tests and for a program that has genuinely told us it works a plain
#: five-day week with no public holidays.
WEEKDAY_CALENDAR = Calendar(holidays=frozenset(), weekend=frozenset({5, 6}), name="weekday")


# --------------------------------------------------------------------------
# Program-scoped factors
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProgramUnits:
    """The conversion factors and calendar one program works in.

    Every field is a decision somebody made about a client, not a constant of
    nature, which is why they live on an object a caller has to name rather
    than at module scope. The defaults are the design doc's stated defaults for
    an unresolved decision, and each one is reported alongside any number
    derived from it - a converted value that cannot say which factor produced
    it is not auditable.
    """

    #: Hours in one person-day. 8 unless a program says otherwise.
    hours_per_person_day: float = 8.0
    #: Person-days in one person-month. 20 is the common Japanese convention;
    #: some firms use 22, and the two must never be pooled.
    person_days_per_person_month: float = 20.0
    #: What fraction of a nominally-allocated day is actually available for
    #: project work. Explicit rather than an implicit 1.0: line management,
    #: interviews, training and 勉強会 are real and all push the same way, so a
    #: 1.0 here systematically under-detects contention.
    availability: float = 0.8
    #: Concurrent project count at which a productivity penalty starts to
    #: apply, and the penalty per project beyond it. An engineer across three
    #: projects delivers less in total than across one; the effect is real and
    #: modelling it as 1.0 is a choice, not neutrality.
    concurrency_free_projects: int = 2
    concurrency_penalty_per_project: float = 0.1
    calendar: Calendar = JP_CALENDAR
    #: Which program these factors belong to. `None` is the process-wide
    #: default set, used where no program has been resolved yet.
    program_id: str | None = None

    def __post_init__(self) -> None:
        if self.hours_per_person_day <= 0:
            raise UnitError("hours_per_person_day must be positive")
        if self.person_days_per_person_month <= 0:
            raise UnitError("person_days_per_person_month must be positive")
        if not 0 < self.availability <= 1:
            raise UnitError(
                f"availability must be in (0, 1]; got {self.availability}. "
                "A factor above 1 would claim a person is more than fully "
                "available, which is the overstatement this field exists to "
                "correct."
            )

    # -- conversion ------------------------------------------------------

    def to_effort_days(self, value: float, unit: str) -> "Effort":
        """Convert any effort quantity into effort-days, recording the factor.

        Effort-days are the denomination every signal in the product is
        reported in, so this is the funnel. The returned :class:`Effort` names
        the factor it used, which is what makes a number on a screen defensible
        rather than merely plausible.
        """
        canonical = normalize_unit(unit)
        if canonical == PERCENT:
            raise UnitError(
                "percent is an allocation rate, not an effort quantity; it "
                "needs a window. Use percent_to_effort_days()."
            )
        if canonical in _EFFORT_DAY_ALIASES:
            return Effort(value=float(value), unit=EFFORT_DAYS, factor=1.0, factor_name="identity")
        if canonical == HOURS:
            return Effort(
                value=float(value) / self.hours_per_person_day,
                unit=EFFORT_DAYS,
                factor=self.hours_per_person_day,
                factor_name="hours_per_person_day",
            )
        if canonical == PERSON_MONTHS:
            return Effort(
                value=float(value) * self.person_days_per_person_month,
                unit=EFFORT_DAYS,
                factor=self.person_days_per_person_month,
                factor_name="person_days_per_person_month",
            )
        raise UnitError(f"no conversion from {canonical!r} to {EFFORT_DAYS}")

    def percent_to_effort_days(
        self, allocation_percent: float, working_days: int
    ) -> "Effort":
        """The §8 bridge: `percent x working days in window = effort-days`.

        `allocation_load` is reported in percent and `capacity_gap` in
        effort-days, and they are two denominations of the same quantity. This
        is the single place the two meet, so a rollup computing one from the
        other cannot disagree with a tile computing it directly.

        Note what is *not* applied here: availability. A resource plan's "50%"
        is a nominal commitment, and this converts the nominal figure faithfully.
        Discounting supply for availability is :meth:`supply_effort_days`'s job,
        and keeping them apart is what lets a screen show demand as planned
        beside supply as realistic.
        """
        if working_days < 0:
            raise UnitError("working_days must not be negative")
        return Effort(
            value=float(allocation_percent) / 100.0 * working_days,
            unit=EFFORT_DAYS,
            factor=float(working_days),
            factor_name="working_days_in_window",
        )

    def supply_effort_days(
        self, working_days: int, *, concurrent_projects: int = 1
    ) -> "Effort":
        """One person's realistic capacity in a window, in effort-days.

        Two discounts, both in the same direction and both explicit:

        * `availability` - the non-project load that a resource plan omits.
        * a concurrency penalty past `concurrency_free_projects` - the same
          engineer across three projects delivers less in total than across
          one, so conserving effort across assignments would overstate supply.

        Named factors rather than one blended number because a PM who disputes
        the figure needs to see which assumption to argue with.
        """
        if working_days < 0:
            raise UnitError("working_days must not be negative")
        penalty = max(0, int(concurrent_projects) - self.concurrency_free_projects)
        factor = self.availability * max(
            0.0, 1.0 - penalty * self.concurrency_penalty_per_project
        )
        return Effort(
            value=working_days * factor,
            unit=EFFORT_DAYS,
            factor=factor,
            factor_name=(
                "availability"
                if penalty == 0
                else f"availability x concurrency({concurrent_projects})"
            ),
        )

    def for_program(self, program_id: str | None) -> "ProgramUnits":
        return replace(self, program_id=program_id)


@dataclass(frozen=True)
class Effort:
    """An effort quantity that knows its unit and how it got there.

    `factor` and `factor_name` are not decoration. The design's rule is that
    every converted value records the factor used, so that a number which later
    looks wrong can be traced to the assumption that produced it instead of to
    a guess about which of four call sites did the multiplication.
    """

    value: float
    unit: str
    factor: float = 1.0
    factor_name: str = "identity"

    def rounded(self, places: int = 2) -> float:
        return round(self.value, places)

    def __str__(self) -> str:  # pragma: no cover - display aid
        return f"{self.value:.2f} {self.unit}"


#: The factor set used where no program has been resolved. A real program's
#: factors come from `units_for()`; this is what the process falls back to, and
#: it is the demo program's own configuration.
DEFAULT_UNITS = ProgramUnits()

#: Per-program overrides, keyed by program id. A dict rather than a database
#: table on purpose for now: these are decisions made once per client at
#: configuration time, they belong under review, and a table editable in a
#: browser is one with no history - the same reasoning that keeps the rule
#: table read-only.
_PROGRAM_OVERRIDES: dict[str, ProgramUnits] = {}


def units_for(program_id: str | None) -> ProgramUnits:
    """The factor set a program works in.

    An unknown program gets the defaults, tagged with its id, rather than an
    error: a program nobody has configured is the normal case, and every number
    derived for it still reports which factors it used.
    """
    if program_id is None:
        return DEFAULT_UNITS
    override = _PROGRAM_OVERRIDES.get(program_id)
    if override is not None:
        return override
    return DEFAULT_UNITS.for_program(program_id)


def configure_program(program_id: str, units: ProgramUnits) -> ProgramUnits:
    """Register a program's factors. Used at configuration time and by tests."""
    resolved = units.for_program(program_id)
    _PROGRAM_OVERRIDES[program_id] = resolved
    return resolved


def working_days(start: date, end: date, *, program_id: str | None = None) -> int:
    """Working days in `[start, end]` on a program's own calendar."""
    return units_for(program_id).calendar.working_days(start, end)
