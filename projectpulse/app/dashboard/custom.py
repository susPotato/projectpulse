"""Custom tiles: parse a person's own pasted data into a chart, never invent one.

Two parsers, tried in order:

1. **AI** (`draft_chart`'s `drafter`, when given) - handles messy input: prose
   with numbers in it, an oddly-shaped table, inconsistent labels. Asked for
   strict JSON, validated before anything touches the database - the same
   discipline `app/dashboard/generator.py` uses for tile keys, applied here to
   a `{title, chart_type, labels, values}` shape instead of an allow-list.
2. **CSV/TSV fallback**, tried first if `drafter` is `None` and after if the
   model's output does not parse - two columns, `label,value` per line. Covers
   the common case (someone pasted a spreadsheet selection) with no network
   call and no key, so "paste a two-column table" keeps working with
   narration switched off.

Both are parsers over data the person supplied. Neither computes a number -
see `CustomTile`'s docstring for why this module is allowed to exist at all
next to a codebase whose rule is "the model never produces a number."
"""

from __future__ import annotations

import csv
import io
import json
import re

from sqlalchemy import select

from app.api.schemas.dashboard import (
    ChartType,
    CustomChartDraft,
    CustomTileIn,
    CustomTileOut,
)
from app.models.dashboard import CustomTile
from app.narration.client import Drafter

_CHART_TYPES: tuple[ChartType, ...] = ("bar", "line", "pie")

SYSTEM_PROMPT = """You turn data a person pasted in into a small chart. Read \
their data and, optionally, a hint about what they want, and respond with \
ONLY a single JSON object - no prose, no markdown fences - shaped exactly \
like this:

{"title": "...", "chart_type": "bar" | "line" | "pie", "labels": ["...", ...], "values": [1.0, ...]}

`labels` and `values` must be the same length and drawn only from the data \
given - never invent a data point, extrapolate, or round to a "nicer" \
number. If the data has a natural order (dates, sequence), keep it. Prefer \
"line" for a time series, "bar" for comparing categories, "pie" only for \
parts of one whole that sum to something meaningful."""


def _user_prompt(raw_data: str, hint: str | None) -> str:
    parts = [f"Data:\n{raw_data.strip()}"]
    if hint:
        parts.append(f"\nWhat they want: {hint.strip()}")
    return "\n".join(parts)


def _csv_fallback(raw_data: str) -> CustomChartDraft | None:
    """Two columns, `label,value` or `label\\tvalue`, one pair per line."""
    text = raw_data.strip()
    if not text:
        return None

    delimiter = "\t" if "\t" in text else ","
    labels: list[str] = []
    values: list[float] = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        cells = [c.strip() for c in row if c.strip() != ""]
        if len(cells) < 2:
            continue
        try:
            value = float(cells[-1].replace(",", ""))
        except ValueError:
            continue
        labels.append(cells[0])
        values.append(value)

    if not labels:
        return None

    return CustomChartDraft(
        title="Custom chart",
        chart_type="bar",
        labels=labels,
        values=values,
        source="csv_fallback",
        fallback_reason=None,
    )


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _parse_model_json(raw: str) -> dict | None:
    match = _JSON_OBJECT.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


def _validate_draft(data: dict) -> CustomChartDraft | None:
    title = data.get("title")
    chart_type = data.get("chart_type")
    labels = data.get("labels")
    values = data.get("values")

    if not isinstance(title, str) or not title.strip():
        return None
    if chart_type not in _CHART_TYPES:
        return None
    if not isinstance(labels, list) or not isinstance(values, list):
        return None
    if not labels or len(labels) != len(values):
        return None
    try:
        numeric_values = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if not all(isinstance(label, str) for label in labels):
        return None

    return CustomChartDraft(
        title=title.strip(),
        chart_type=chart_type,
        labels=[str(label) for label in labels],
        values=numeric_values,
        source="ai",
        fallback_reason=None,
    )


def draft_chart(
    raw_data: str, hint: str | None, *, drafter: Drafter | None
) -> CustomChartDraft:
    """Returns a draft, or raises `ValueError` when nothing could make sense
    of the data - the route turns that into a 400 the person can act on."""
    if drafter is not None:
        try:
            raw = drafter(SYSTEM_PROMPT, _user_prompt(raw_data, hint))
            parsed = _parse_model_json(raw)
            draft = _validate_draft(parsed) if parsed is not None else None
        except Exception:  # noqa: BLE001 - any provider failure degrades to the fallback
            draft = None

        if draft is not None:
            return draft

        fallback = _csv_fallback(raw_data)
        if fallback is not None:
            fallback.fallback_reason = (
                "the model's response could not be parsed as a chart"
            )
            return fallback
        raise ValueError(
            "could not turn this into a chart - try a simple two-column "
            "table (label, value), one pair per line"
        )

    fallback = _csv_fallback(raw_data)
    if fallback is not None:
        fallback.fallback_reason = "narration is switched off"
        return fallback
    raise ValueError(
        "narration is off, and this isn't a simple two-column table - "
        "paste 'label, value' pairs one per line, or turn narration on"
    )


def _to_out(tile: CustomTile) -> CustomTileOut:
    return CustomTileOut(
        id=tile.id,
        name=tile.name,
        chart_type=tile.chart_type,  # type: ignore[arg-type]
        labels=json.loads(tile.labels_json),
        values=json.loads(tile.values_json),
        source_note=tile.source_note,
    )


def create_custom_tile(session, data: CustomTileIn) -> CustomTileOut:
    if len(data.labels) != len(data.values) or not data.labels:
        raise ValueError("labels and values must be the same non-empty length")
    tile = CustomTile(
        name=data.name,
        chart_type=data.chart_type,
        labels_json=json.dumps(data.labels),
        values_json=json.dumps(data.values),
        source_note=data.source_note,
    )
    session.add(tile)
    session.flush()
    return _to_out(tile)


def list_custom_tiles(session) -> list[CustomTileOut]:
    rows = session.scalars(select(CustomTile).order_by(CustomTile.id.desc())).all()
    return [_to_out(t) for t in rows]


def get_custom_tile(session, tile_id: int) -> CustomTileOut | None:
    tile = session.get(CustomTile, tile_id)
    return _to_out(tile) if tile is not None else None


def delete_custom_tile(session, tile_id: int) -> bool:
    tile = session.get(CustomTile, tile_id)
    if tile is None:
        return False
    session.delete(tile)
    return True
