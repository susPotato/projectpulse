"""A range of finish dates, resampled from how this plan has already drifted.

The forward pass (`impact.py`) answers "when does the chain finish if nothing
else moves". Nothing else moving is not a neutral assumption - it is the one
assumption a delivery plan has never once satisfied. This module answers the
next question: **given how much this project's plan has already drifted from its
own commitments, what range should the finish date be quoted as?**

### Where the uncertainty comes from, and where it must not come from

A Monte Carlo over assumed task-duration distributions is the textbook answer
and it is forbidden here. It requires a distribution per task - lognormal,
PERT, triangular, whatever - and no sheet carries one. Choosing it means the
shape of the output is chosen by the modeller, which is exactly the invented
number section 4.1 forbids and exactly what the pitch deck's "89% confidence"
was.

So the sample is **measured, not assumed**: for every task carrying both a
`baseline_end` (what was committed) and a `planned_end` (what the plan says
today), the difference is one observation of how far this project's plan moves.
Those observations are then **resampled with replacement** onto the tasks that
could still move, and the forward pass is re-run per trial. No distribution is
fitted. The empirical one is used directly, which is why nothing here decides
the shape of the answer.

Every input is a date a human wrote. The only thing this module adds is
counting.

### The one assumption, stated rather than buried

Drift observed *so far* is resampled as drift *still to come*: a task that has
already moved twelve days is given another draw, not excused from one. That is
the assumption "this project keeps drifting the way it has been drifting", and
it is an assumption - the honest kind, because it is a property of the method
rather than a number nobody can trace.

It is also the conservative direction. The alternative - assuming a task that
has already slipped has finished slipping - is what produces plans that are
late again next month, and it cannot be supported by anything in the sheet
either. Every surface that shows this range says so in the caveat panel, and
`ForecastSurface`'s tests check that the sentence is still there.

### It refuses, and that is the feature

Three guards, and each returns a reason rather than a number:

- **too few observations** - a percentile over three points is arithmetic
  theatre;
- **no variance** - if every task drifted by the same amount, every trial
  returns the same date, and one date presented as a distribution is a lie that
  looks like a measurement;
- **nothing left to move** - a finished project has no forecast, only a result.

`ForecastUnavailable` carries the reason to the screen. Refusing out loud is
worth more here than any range: it is the same discipline as
`provably_before` dropping an ordering it cannot prove, applied to the one
figure a steering committee will actually repeat.

### It is deterministic

Seeded from the observations themselves, so the same data always gives the same
forecast. A range that shifts on every refresh with no new data destroys
confidence in the whole screen, and the page, the CLI and the `.docx` must agree
to the day.

Pure like the rest of `intelligence/`: no session, no ORM, no clock.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Sequence

from app.intelligence.schedule.graph import EdgeRecord, TaskNode, build_graph
from app.intelligence.schedule.impact import project_schedule

#: Below this, a percentile is arithmetic theatre. Six is already few - the
#: bundle carries `observations` so every surface has to say how few, and the
#: threshold exists to stop the genuinely meaningless case rather than to
#: pretend the merely-thin one is fine.
MIN_OBSERVATIONS = 5

#: Trials per forecast. Large enough that P95 is stable to the day on a sample
#: this size, small enough that the whole thing runs in well under a second -
#: it is recomputed on every page load, like every other figure here.
TRIALS = 2000

#: The percentiles reported. P50 is the middle of what was observed; P95 is the
#: pessimistic tail. Deliberately not P99: with a handful of observations the
#: 99th percentile is a single resampled point wearing a confident label.
PERCENTILES = (50, 80, 95)

#: Statuses that mean a task can no longer drift. Matched case-insensitively;
#: anything unrecognised is treated as still open, because assuming a task is
#: finished is the error that shortens a forecast.
_DONE = {"done", "closed", "complete", "completed", "resolved"}


@dataclass(frozen=True)
class Observation:
    """One measured drift: what a task committed to, against what it plans now."""

    entity_id: str
    label: str
    committed: date
    planned: date

    @property
    def days(self) -> int:
        return (self.planned - self.committed).days


@dataclass(frozen=True)
class ForecastPoint:
    """One percentile of the resampled finish date."""

    percentile: int
    finish: date
    #: Days past the *committed* finish, never past the current plan. Measuring
    #: against the plan would let a slipping project report a shrinking number
    #: every time it re-baselined - the same trap `whatif.days_late` avoids.
    days_late: int


@dataclass(frozen=True)
class Forecast:
    """The range, and everything needed to argue with it."""

    available: bool
    #: Why not, when `available` is False. Rendered verbatim.
    reason: str = ""
    committed_end: date | None = None
    projected_end: date | None = None
    points: tuple[ForecastPoint, ...] = ()
    #: How many measured drifts the range rests on. **Never omit this on a
    #: surface that shows `points`** - it is the whole basis for judging them.
    observations: int = 0
    #: The drifts themselves, so a reader can check the sample rather than
    #: trust it.
    sample: tuple[Observation, ...] = ()
    #: How many tasks were resampled onto.
    open_tasks: int = 0
    trials: int = 0

    def point(self, percentile: int) -> ForecastPoint | None:
        return next((p for p in self.points if p.percentile == percentile), None)


def _unavailable(reason: str, **kw) -> Forecast:
    return Forecast(available=False, reason=reason, **kw)


def observed_drift(tasks: Sequence[TaskNode]) -> list[Observation]:
    """How far each task's plan has moved from what it committed to.

    Only tasks carrying **both** dates. A task with no baseline cannot show
    variance, and treating its absence as a zero would quietly pull every
    percentile toward the current plan - a project that kept no baselines would
    forecast itself as certain.
    """
    out: list[Observation] = []
    for task in tasks:
        committed, planned = task.baseline_end, task.planned_end
        if committed is None or planned is None:
            continue
        out.append(
            Observation(
                entity_id=task.entity_id,
                label=task.title or task.entity_id,
                committed=committed,
                planned=planned,
            )
        )
    return out


def _is_open(task: TaskNode) -> bool:
    """Whether this task could still move. Unknown counts as open."""
    return (task.status or "").strip().lower() not in _DONE


def _seed(sample: Sequence[Observation]) -> int:
    """Deterministic from the data, so the same facts give the same range.

    Hashed rather than summed: two different samples can share a total, and a
    forecast that silently stops changing when the data does is worse than one
    that never changed at all.
    """
    material = "|".join(f"{o.entity_id}:{o.days}" for o in sorted(sample, key=lambda o: o.entity_id))
    return int.from_bytes(hashlib.sha256(material.encode()).digest()[:8], "big")


def _percentile(values: Sequence[int], percentile: int) -> int:
    """Nearest-rank on a sorted sample.

    Nearest-rank rather than interpolated: every value here is a whole number of
    days that some trial actually produced, and interpolating invents a finish
    date that no trial ever returned.
    """
    ordered = sorted(values)
    rank = max(1, -(-percentile * len(ordered) // 100))  # ceil, integer-only
    return ordered[min(rank, len(ordered)) - 1]


def forecast_project(
    tasks: Sequence[TaskNode],
    edges: Sequence[EdgeRecord],
    *,
    trials: int = TRIALS,
) -> Forecast:
    """Resample this project's own drift onto the work that is still open.

    Returns an unavailable `Forecast` carrying a reason whenever the data cannot
    support a range - see the module docstring. Callers must render the reason;
    a blank panel says "broken", and this is a deliberate refusal.
    """
    baseline = project_schedule(build_graph(tasks, edges))
    committed = baseline.project_end_planned
    projected = baseline.project_end_projected

    sample = observed_drift(tasks)
    common = dict(
        committed_end=committed,
        projected_end=projected,
        observations=len(sample),
        sample=tuple(sample),
    )

    if len(sample) < MIN_OBSERVATIONS:
        return _unavailable(
            f"Only {len(sample)} task(s) carry both a baseline and a planned "
            f"date, and a range needs at least {MIN_OBSERVATIONS} to rest on. "
            "Fill in the Baseline Completion column to enable this.",
            **common,
        )

    spread = {observation.days for observation in sample}
    if len(spread) < 2:
        only = next(iter(spread))
        return _unavailable(
            f"Every task with a baseline has drifted by exactly {only} day(s), "
            "so there is no observed variation to resample. A single repeated "
            "value would produce one date dressed up as a range.",
            **common,
        )

    open_tasks = [task for task in tasks if _is_open(task)]
    if not open_tasks:
        return _unavailable(
            "Every task is closed, so nothing is left to move. This project has "
            "a result rather than a forecast.",
            **common,
        )

    drifts = [observation.days for observation in sample]
    rng = random.Random(_seed(sample))
    by_id = {task.entity_id: task for task in tasks}
    open_ids = [task.entity_id for task in open_tasks]

    finishes: list[date] = []
    for _ in range(trials):
        # One draw per open task, then re-run the same forward pass the rest of
        # the product uses. Re-running rather than adding the drift to the
        # finish date is the point: a drift on an upstream task propagates
        # through the chain, and only the graph knows how far.
        trial_tasks = list(tasks)
        for index, task in enumerate(trial_tasks):
            if task.entity_id not in by_id or not _is_open(task):
                continue
            if task.planned_end is None:
                continue
            trial_tasks[index] = replace(
                task, planned_end=task.planned_end + timedelta(days=rng.choice(drifts))
            )

        outcome = project_schedule(build_graph(trial_tasks, edges))
        if outcome.project_end_projected is not None:
            finishes.append(outcome.project_end_projected)

    if not finishes:
        return _unavailable(
            "No trial produced a finish date - the schedule has no dated chain "
            "to project.",
            **common,
        )

    # Percentiles are taken over the day offsets rather than the dates so the
    # arithmetic is integer throughout, then mapped back.
    origin = min(finishes)
    offsets = [(finish - origin).days for finish in finishes]

    points = tuple(
        ForecastPoint(
            percentile=percentile,
            finish=origin + timedelta(days=_percentile(offsets, percentile)),
            days_late=(
                (origin + timedelta(days=_percentile(offsets, percentile))) - committed
            ).days
            if committed is not None
            else 0,
        )
        for percentile in PERCENTILES
    )

    return Forecast(
        available=True,
        points=points,
        open_tasks=len(open_tasks),
        trials=len(finishes),
        **common,
    )
