"""Show the arithmetic as input, algorithm, output.

Every number this product reports is arithmetic on dates a human typed. That claim
is only worth making if someone can check it - and an earlier version of this
module failed that test in practice: it printed a ten-column table that showed
*what* each figure was and never *how* it got there. A reader could not follow it.

So each task is now derived in three parts:

**Input** - the cells that came off the spreadsheet, and nothing else. If a value
is not here, it did not come from the sheet.

**Algorithm** - one numbered step per operation, each carrying four things: the
question it answers, the formula in words, the same formula with the real values
substituted, and the result. A reader can redo any single line on paper.

**Output** - the figures the findings quote, with the one that matters named:
slip the dependency chain implies that nobody has written down.

Nothing here computes anything new. It reads the same `ImpactReport` the findings
were built from, so the page and a finding cannot disagree - if they ever did, the
bug would be in this formatting, never in two rival calculations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.intelligence.assembler import entity_label
from app.intelligence.schedule.graph import ScheduleGraph
from app.intelligence.schedule.impact import ImpactReport

#: How a value reads when the sheet never supplied it. Never "0" - zero means
#: "no slip", absent means "cannot say", and conflating them reports a project
#: with no baseline as permanently on time.
ABSENT = "not in the sheet"


def _d(value: date | None) -> str:
    return value.isoformat() if value else "-"


def _n(value: int | None) -> str:
    return "-" if value is None else str(value)


def _days(value: int | None) -> str:
    return "-" if value is None else f"{value} day{'' if abs(value) == 1 else 's'}"


@dataclass(frozen=True)
class Operand:
    """One labelled value, on the way in or out."""

    label: str
    value: str
    note: str = ""


@dataclass(frozen=True)
class Calc:
    """One arithmetic step, written so a reader can redo it on paper."""

    number: int
    #: What this step is for, in plain words.
    question: str
    #: The operation with names, e.g. "planned finish - start date".
    formula: str
    #: The same operation with the real values in it.
    substituted: str
    result: str
    #: Why the step is done this way, where that is not obvious.
    note: str = ""


@dataclass(frozen=True)
class Derivation:
    """One task, derived end to end."""

    entity_id: str
    label: str
    title: str | None
    inputs: tuple[Operand, ...]
    steps: tuple[Calc, ...]
    outputs: tuple[Operand, ...]
    is_inconsistent: bool
    propagated_days: int | None


def derive(
    schedule: ScheduleGraph, impact: ImpactReport, entity_id: str
) -> Derivation | None:
    """Build the full input/algorithm/output derivation for one task."""
    task = schedule.tasks.get(entity_id)
    projection = impact.projections.get(entity_id)
    if task is None or projection is None:
        return None

    # ---- Input: only what the spreadsheet actually supplied ----------------
    inputs = [
        Operand("start date", _d(task.start_date) if task.start_date else ABSENT),
        Operand(
            "planned finish",
            _d(task.planned_end) if task.planned_end else ABSENT,
            "what the sheet says today",
        ),
        Operand(
            "baseline finish",
            _d(task.baseline_end) if task.baseline_end else ABSENT,
            "what was originally committed",
        ),
    ]

    predecessors = schedule.predecessors(entity_id)
    if not predecessors:
        inputs.append(
            Operand("predecessors", "none", "nothing upstream constrains this task")
        )
    for pred in predecessors:
        source = schedule.graph.edges[pred, entity_id].get("source") or "unknown"
        stated = (
            "a person wrote this in the Predecessor column"
            if source == "excel_predecessor"
            else "inferred from the sheet's own dates"
        )
        lag = schedule.lag(pred, entity_id)
        inputs.append(
            Operand(
                "predecessor",
                f"{entity_label(pred)}"
                + (f", {lag:+d} day lag" if lag else ""),
                stated,
            )
        )

    # ---- Algorithm --------------------------------------------------------
    steps: list[Calc] = []
    duration = (
        (task.planned_end - task.start_date).days
        if task.start_date and task.planned_end
        else None
    )

    if duration is not None:
        steps.append(
            Calc(
                number=len(steps) + 1,
                question="How long does the plan say this takes?",
                formula="planned finish - start date",
                substituted=f"{_d(task.planned_end)} - {_d(task.start_date)}",
                result=_days(duration),
                note="Taken from the sheet's own two dates - never estimated.",
            )
        )

    driver = projection.driving_predecessor
    earliest = task.start_date

    if predecessors:
        # Show every candidate, so the `max` is visibly a choice between known
        # numbers rather than something the reader has to trust.
        candidates: list[str] = []
        if task.start_date:
            candidates.append(f"its own planned start {_d(task.start_date)}")
        for pred in predecessors:
            upstream = impact.projections[pred].projected_end
            lag = schedule.lag(pred, entity_id)
            if upstream is None:
                continue
            when = upstream + timedelta(days=lag)
            candidates.append(
                f"{entity_label(pred)} finishes {_d(upstream)}"
                + (f" {lag:+d}d lag" if lag else "")
                + f" -> {_d(when)}"
            )
            if earliest is None or when > earliest:
                earliest = when

        steps.append(
            Calc(
                number=len(steps) + 1,
                question="When can it actually start?",
                formula="the latest of: its own start, and each predecessor's "
                "projected finish plus lag",
                substituted="; ".join(candidates) if candidates else "no dated inputs",
                result=_d(earliest),
                note="A task cannot begin before the work it depends on is done.",
            )
        )

    if earliest is not None and duration is not None:
        steps.append(
            Calc(
                number=len(steps) + 1,
                question="So when does it finish?",
                formula="earliest start + duration",
                substituted=f"{_d(earliest)} + {_days(duration)}",
                result=_d(earliest + timedelta(days=duration)),
            )
        )

    # The clamp is a real rule and has to be visible, or the projected finish
    # looks like it was computed wrongly.
    raw = (
        earliest + timedelta(days=duration)
        if earliest is not None and duration is not None
        else None
    )
    if raw is not None and task.planned_end is not None and raw < task.planned_end:
        steps.append(
            Calc(
                number=len(steps) + 1,
                question="Can it finish earlier than the plan says?",
                formula="no - keep the later of the two",
                substituted=f"max({_d(raw)}, plan {_d(task.planned_end)})",
                result=_d(task.planned_end),
                note="A plan is a commitment, not an estimate. Upstream finishing "
                "early does not move this date in.",
            )
        )

    if projection.projected_end is not None and task.planned_end is not None:
        steps.append(
            Calc(
                number=len(steps) + 1,
                question="Does the sheet's date survive this?",
                formula="projected finish - planned finish",
                substituted=f"{_d(projection.projected_end)} - {_d(task.planned_end)}",
                result=_days(projection.propagated_days),
                note=(
                    "Above zero means the sheet's date cannot hold."
                    if (projection.propagated_days or 0) > 0
                    else "Zero means the sheet agrees with its own dependencies."
                ),
            )
        )

    # ---- Output -----------------------------------------------------------
    outputs = [
        Operand("projected finish", _d(projection.projected_end)),
        Operand(
            "hidden slip",
            _days(projection.propagated_days),
            "the number a PM cannot get from their own spreadsheet",
        ),
        Operand(
            "slip already recorded",
            _days(projection.recorded_slip_days),
            "what a human had already typed against the baseline",
        ),
        Operand(
            "total variance",
            _days(projection.variance_days),
            "projected finish against the original commitment",
        ),
    ]

    return Derivation(
        entity_id=entity_id,
        label=entity_label(entity_id),
        title=task.title,
        inputs=tuple(inputs),
        steps=tuple(steps),
        outputs=tuple(outputs),
        is_inconsistent=projection.is_inconsistent,
        propagated_days=projection.propagated_days,
    )


def derivations(schedule: ScheduleGraph, impact: ImpactReport) -> list[Derivation]:
    """Every task, in topological order - causes before effects."""
    out = []
    for entity_id in schedule.topological_order():
        found = derive(schedule, impact, entity_id)
        if found is not None:
            out.append(found)
    return out


# --------------------------------------------------------------------------
# Terminal rendering
# --------------------------------------------------------------------------

#: Overview columns. The table is a contents page now, not the explanation.
HEADERS = (
    ("task", 10),
    ("start", 11),
    ("dur", 6),
    ("constrained by", 16),
    ("projected", 11),
    ("sheet says", 11),
    ("hidden", 8),
)


@dataclass(frozen=True)
class Row:
    """One overview line."""

    task: str
    start: str
    duration: str
    driver: str
    projected: str
    planned: str
    propagated: str


def rows(schedule: ScheduleGraph, impact: ImpactReport) -> list[Row]:
    out: list[Row] = []
    for entity_id in schedule.topological_order():
        task = schedule.tasks[entity_id]
        projection = impact.projections[entity_id]
        duration = (
            (task.planned_end - task.start_date).days
            if task.start_date and task.planned_end
            else None
        )
        driver = projection.driving_predecessor
        out.append(
            Row(
                task=entity_label(entity_id),
                start=_d(task.start_date),
                duration="-" if duration is None else f"{duration}d",
                driver=(
                    "own plan"
                    if driver is None
                    else f"{entity_label(driver)} {schedule.lag(driver, entity_id):+d}d"
                ),
                projected=_d(projection.projected_end),
                planned=_d(projection.planned_end),
                propagated=_n(projection.propagated_days),
            )
        )
    return out


def table(schedule: ScheduleGraph, impact: ImpactReport) -> str:
    """The overview, as a fixed-width table."""
    header = "  ".join(name.ljust(width) for name, width in HEADERS)
    lines = [header, "-" * len(header)]

    def cell(value: str, width: int) -> str:
        # A synthetic identity key (`~anon-<16 hex>`) is far wider than any real
        # Task ID and would shunt every later column out of line.
        return value[: width - 1] + "~" if len(value) > width else value.ljust(width)

    for row in rows(schedule, impact):
        values = (
            row.task,
            row.start,
            row.duration,
            row.driver,
            row.projected,
            row.planned,
            row.propagated,
        )
        lines.append(
            "  ".join(cell(v, w) for v, (_, w) in zip(values, HEADERS))
        )
    return "\n".join(lines)


def working(schedule: ScheduleGraph, impact: ImpactReport, entity_id: str) -> str:
    """One task as input / algorithm / output, for the terminal."""
    found = derive(schedule, impact, entity_id)
    if found is None:
        return f"{entity_id}: not in this project"

    out: list[str] = [f"{found.label} - {found.title or ''}".rstrip()]

    out.append("")
    out.append("  INPUT   (what the spreadsheet says)")
    width = max((len(o.label) for o in found.inputs), default=0)
    for operand in found.inputs:
        line = f"    {operand.label.ljust(width)}  {operand.value}"
        if operand.note:
            line += f"   [{operand.note}]"
        out.append(line)

    out.append("")
    out.append("  ALGORITHM")
    for step in found.steps:
        out.append(f"    {step.number}. {step.question}")
        out.append(f"       formula   {step.formula}")
        out.append(f"       values    {step.substituted}")
        out.append(f"       result    {step.result}")
        if step.note:
            out.append(f"       why       {step.note}")
        out.append("")

    out.append("  OUTPUT")
    width = max((len(o.label) for o in found.outputs), default=0)
    for operand in found.outputs:
        line = f"    {operand.label.ljust(width)}  {operand.value}"
        if operand.note:
            line += f"   [{operand.note}]"
        out.append(line)

    return "\n".join(out)
