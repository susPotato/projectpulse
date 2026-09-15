"""Public-holiday lookup by country for Schedule Task's "skip holidays".

Prefers the ``holidays`` PyPI package (200+ countries, correct lunar-calendar
dates for Tết etc.). If it isn't installed or the country code is unknown,
falls back to a small built-in fixed-date table (VN/JP national days only) so
the feature degrades gracefully instead of crashing offline installs.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, Optional, Set, Tuple

# Country codes offered in the task editor (any ISO code typed in still works
# when the `holidays` package is installed).
COMMON_COUNTRIES = ("VN", "JP", "US", "KR", "CN", "SG", "DE", "FR", "GB", "IN")

# Fixed-date fallback (month, day) — used only when the holidays package is
# unavailable. Lunar holidays (Tết, Hùng Kings…) can't be fixed dates, so the
# fallback intentionally covers solar-calendar national days only.
_FALLBACK: Dict[str, Set[Tuple[int, int]]] = {
    "VN": {(1, 1), (4, 30), (5, 1), (9, 2)},
    "JP": {(1, 1), (2, 11), (2, 23), (4, 29), (5, 3), (5, 4), (5, 5),
           (8, 11), (11, 3), (11, 23)},
}

_cache: Dict[Tuple[str, int], object] = {}


def _package_calendar(country: str, year: int):
    """A holidays-package calendar for (country, year), cached; None when the
    package is missing or the country code is unknown to it."""
    key = (country, year)
    if key in _cache:
        return _cache[key]
    cal = None
    try:
        import holidays as _holidays

        cal = _holidays.country_holidays(country, years=[year, year + 1])
    except Exception:   # noqa: BLE001 — package missing / unknown country code
        cal = None
    _cache[key] = cal
    return cal


def is_holiday(d: date, country: Optional[str]) -> bool:
    country = (country or "").strip().upper()
    if not country:
        return False
    cal = _package_calendar(country, d.year)
    if cal is not None:
        return d in cal
    return (d.month, d.day) in _FALLBACK.get(country, set())
