"""Minimal 5-field cron expression support for Schedule Task.

``minute hour day-of-month month day-of-week`` with ``*``, lists (``1,15``),
ranges (``8-18``) and steps (``*/15``, ``8-18/2``). Day-of-week: 0 or 7 =
Sunday. Standard cron OR-semantics between day-of-month and day-of-week when
both are restricted. No external dependency, minute granularity — plenty for a
desktop scheduler that ticks every 30s.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Set

_SEARCH_DAYS = 366 * 2   # give up after two years (an expression that never fires)


class CronError(ValueError):
    pass


def _parse_field(spec: str, lo: int, hi: int) -> Set[int]:
    values: Set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_str = part.split("/", 1)
            try:
                step = int(step_str)
            except ValueError as exc:
                raise CronError(f"Bad step in cron field: {spec!r}") from exc
            if step < 1:
                raise CronError(f"Step must be >= 1 in: {spec!r}")
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError as exc:
                raise CronError(f"Bad range in cron field: {spec!r}") from exc
        else:
            try:
                start = end = int(part)
            except ValueError as exc:
                raise CronError(f"Bad value in cron field: {spec!r}") from exc
        if start > end or start < lo or end > hi + (1 if hi == 6 else 0):
            # dow allows 7 (=Sunday), normalized below
            raise CronError(f"Out-of-range cron field: {spec!r}")
        for v in range(start, end + 1, step):
            values.add(0 if (hi == 6 and v == 7) else v)
    if not values:
        raise CronError(f"Empty cron field: {spec!r}")
    return values


class Cron:
    def __init__(self, expression: str):
        fields = (expression or "").split()
        if len(fields) != 5:
            raise CronError("Cron expression needs exactly 5 fields: "
                            "minute hour day-of-month month day-of-week")
        self.minutes = _parse_field(fields[0], 0, 59)
        self.hours = _parse_field(fields[1], 0, 23)
        self.dom = _parse_field(fields[2], 1, 31)
        self.months = _parse_field(fields[3], 1, 12)
        self.dow = _parse_field(fields[4], 0, 6)
        self._dom_star = fields[2].strip() == "*"
        self._dow_star = fields[4].strip() == "*"

    def _day_matches(self, dt: datetime) -> bool:
        if dt.month not in self.months:
            return False
        cron_dow = (dt.weekday() + 1) % 7    # Python Mon=0 → cron Sun=0
        dom_ok = dt.day in self.dom
        dow_ok = cron_dow in self.dow
        if self._dom_star and self._dow_star:
            return True
        if self._dom_star:
            return dow_ok
        if self._dow_star:
            return dom_ok
        return dom_ok or dow_ok              # both restricted → standard OR

    def next_after(self, after: datetime) -> Optional[datetime]:
        """The first matching time strictly after ``after`` (or None if the
        expression never fires within two years)."""
        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        day = after.replace(hour=0, minute=0, second=0, microsecond=0)
        for offset in range(_SEARCH_DAYS):
            probe_day = day + timedelta(days=offset)
            if not self._day_matches(probe_day):
                continue
            for h in hours:
                for m in minutes:
                    candidate = probe_day.replace(hour=h, minute=m)
                    if candidate > after:
                        return candidate
        return None


def validate(expression: str) -> Optional[str]:
    """None if the expression parses, else a human error message."""
    try:
        Cron(expression)
        return None
    except CronError as exc:
        return str(exc)
