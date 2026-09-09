"""The dashboard canvas: the tile catalogue, and saved layouts.

Same `In`/`Out`/`Bundle` split as `app/api/schemas/risk.py`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.api.schemas.base import Response

Scope = Literal["program", "project"]
Preview = Literal["stat", "list", "heatmap", "brief", "chart"]


class TileSpecOut(Response):
    """One entry in the catalogue - what "+ Add Tiles" lists."""

    key: str
    label: str
    category: str
    description: str
    scope: Scope
    default_w: int
    default_h: int
    preview: Preview


class CatalogueBundle(Response):
    tiles: list[TileSpecOut] = Field(default_factory=list)
    templates: dict[str, list[str]] = Field(default_factory=dict)


class TileIn(Response):
    """Create or move/resize one tile. All-optional so a PATCH can send only
    the fields that changed - the same partial-update convention `RiskIn`
    uses via `model_dump(exclude_unset=True)`."""

    tile_key: str | None = None
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    settings: dict[str, Any] | None = None


class TileOut(Response):
    id: int
    tile_key: str
    x: int
    y: int
    w: int
    h: int
    settings: dict[str, Any] | None = None


class DashboardIn(Response):
    scope_type: Scope | None = None
    scope_id: str | None = None
    name: str | None = None


class DashboardOut(Response):
    id: int
    scope_type: Scope
    scope_id: str
    name: str
    source: str
    ai_prompt: str | None = None
    tiles: list[TileOut] = Field(default_factory=list)
    #: Set only by `POST /api/dashboards/generate`: why the model's own tile
    #: picks were NOT used, when that happened - narration off, the call
    #: failed, or it returned nothing recognizable. `None` means either this
    #: is not a generated dashboard, or the model's picks were used as-is.
    fallback_reason: str | None = None


class GenerateRequest(Response):
    scope_type: Scope
    scope_id: str
    prompt: str


ChartType = Literal["bar", "line", "pie"]


class CustomTileDraftRequest(Response):
    #: Pasted CSV/TSV, a list, prose with numbers in it - whatever the person
    #: has. Parsed, never trusted as already-structured.
    raw_data: str
    #: "monthly spend", "as a pie chart" - steers the parse; optional.
    hint: str | None = None


class CustomChartDraft(Response):
    """A proposed `{title, chart_type, labels, values}` - not saved yet. The
    person reviews and edits this before it becomes a `CustomTileOut`."""

    title: str
    chart_type: ChartType
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    #: How the draft was produced - shown so a fallback parse (no model, or
    #: the model's JSON didn't validate) is never presented as the model's
    #: own read of the data.
    source: Literal["ai", "csv_fallback"]
    #: Set only when `source == "csv_fallback"`: why the model wasn't used.
    fallback_reason: str | None = None


class CustomTileIn(Response):
    name: str
    chart_type: ChartType
    labels: list[str]
    values: list[float]
    source_note: str | None = None


class CustomTileOut(Response):
    id: int
    name: str
    chart_type: ChartType
    labels: list[str]
    values: list[float]
    source_note: str | None = None


class CustomTileListBundle(Response):
    tiles: list[CustomTileOut] = Field(default_factory=list)
