"""Pure calendar-grid math for the Schedule Task tab's Calendar view (no Qt
dependency, so it's directly unit-testable) — month/week grids, period
navigation, and grouping tasks by their scheduled date.
"""
from __future__ import annotations

import calendar as _calendar_mod
from datetime import date, timedelta
from typing import Dict, List

from .tasks import parse_run_at

GRANULARITIES = ("week", "month", "year")


def month_grid(anchor: date) -> List[List[date]]:
    """Full Mon-Sun weeks covering ``anchor``'s month, including leading/
    trailing days from the adjacent months so every week is exactly 7 long."""
    cal = _calendar_mod.Calendar(firstweekday=0)
    weeks: List[List[date]] = []
    week: List[date] = []
    for d in cal.itermonthdates(anchor.year, anchor.month):
        week.append(d)
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        weeks.append(week)
    return weeks


def week_days(anchor: date) -> List[date]:
    """The 7 dates (Mon..Sun) of the week containing ``anchor``."""
    start = anchor - timedelta(days=anchor.weekday())
    return [start + timedelta(days=i) for i in range(7)]


def shift_period(anchor: date, granularity: str, direction: int) -> date:
    """New anchor after moving Prev(-1)/Next(+1) one ``granularity`` unit."""
    if granularity == "week":
        return anchor + timedelta(days=7 * direction)
    if granularity == "year":
        target_year = anchor.year + direction
        try:
            return anchor.replace(year=target_year)
        except ValueError:   # Feb 29 landing on a non-leap year
            return anchor.replace(year=target_year, day=28)
    # month
    month_index = anchor.month - 1 + direction
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor.day, _calendar_mod.monthrange(year, month)[1])
    return date(year, month, day)


def group_tasks_by_date(tasks: List[dict]) -> Dict[str, List[dict]]:
    """``{"YYYY-MM-DD": [task, ...]}`` from each task's schedule.run_at —
    tasks with no (or an unparseable) run_at are simply omitted, since they
    have no date to place on a calendar."""
    grouped: Dict[str, List[dict]] = {}
    for t in tasks:
        dt = parse_run_at((t.get("schedule") or {}).get("run_at"))
        if dt is None:
            continue
        grouped.setdefault(dt.date().isoformat(), []).append(t)
    return grouped


def month_task_counts(tasks_by_date: Dict[str, List[dict]], year: int) -> Dict[int, int]:
    """``{month(1-12): count}`` of tasks scheduled anywhere in ``year``."""
    counts = {m: 0 for m in range(1, 13)}
    for date_str, items in tasks_by_date.items():
        parts = date_str.split("-")
        if len(parts) != 3:
            continue
        y, m, _d = parts
        try:
            y_int, m_int = int(y), int(m)
        except ValueError:
            continue
        if y_int == year and 1 <= m_int <= 12:
            counts[m_int] += len(items)
    return counts
