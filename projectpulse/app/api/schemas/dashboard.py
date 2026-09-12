"""The dashboard canvas: the tile catalogue, and saved layouts.

Same `In`/`Out`/`Bundle` split as `app/api/schemas/risk.py`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.api.schemas.agent import ChatMessage
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
    #: Set only by `POST /api/dashboards/fit`: which signals the project's data
    #: carries, how many tiles that filled, and which tiles each absent signal
    #: is holding back.
    #:
    #: Returned rather than left implicit because the interesting half of
    #: fitting a board is what was *left off*: a person who uploads a second
    #: export tomorrow should be able to see that it unlocks three tiles without
    #: having to press the button again to find out.
    fit: dict[str, Any] | None = None
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


class LiveSource(Response):
    """How to recompute a tool-sourced tile's rows live, instead of trusting
    the snapshot taken when it was drafted.

    Never asserted by the model - `app/dashboard/agent.py` infers this by
    matching a draft's own `labels`/`values` against the raw JSON one of its
    tools actually returned, so a chart carries this only when the match is
    exact. `tool` is one of `agent.TOOLS`'s names; `list_field` is which list
    in that tool's JSON result the rows came from, `label_field` and
    `value_field` which two keys of each row became a label and a value.
    """

    tool: str
    project_id: str
    list_field: str
    label_field: str
    value_field: str


class CustomChartDraft(Response):
    """A proposed `{title, chart_type, labels, values}` - not saved yet. The
    person reviews and edits this before it becomes a `CustomTileOut`."""

    title: str
    chart_type: ChartType
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    #: How the draft was produced - shown so a fallback parse (no model, or
    #: the model's JSON didn't validate) is never presented as the model's
    #: own read of the data. `local_edit` is a refinement applied by
    #: `custom._local_revision` without asking a model at all.
    source: Literal["ai", "csv_fallback", "local_edit"]
    #: Set only when the model was not what produced this: why not.
    fallback_reason: str | None = None
    #: Set only when every row was read straight from one tool call this
    #: draft came from - `None` otherwise, including for a plain "ai" draft
    #: parsed from pasted data, which has no tool behind it to replay.
    live_source: LiveSource | None = None


class DraftChange(Response):
    """One difference between the draft a person was looking at and the one
    they got back.

    Computed by comparing the two drafts - never reported by the model. That
    is the whole point: a revision regenerates the entire chart, so a request
    to rename the tile can come back having also moved a value, and prose
    from the model saying "renamed it" would hide that. The transcript line
    the person reads is composed from these, so what the chat says and what
    the preview shows cannot disagree.
    """

    field: Literal["title", "chart_type", "labels", "values"]
    #: Already formatted for display - one clause, e.g. `chart type bar ->
    #: line`. Built in `custom.diff_drafts`, the only place that words these.
    summary: str


class CustomChartDraftIn(Response):
    """The draft as it arrives from the browser, echoed back with each turn.

    A near-copy of `CustomChartDraft` on purpose, and the `In`/`Out` split
    this module's docstring already names. Reusing the response model as a
    request field would be shorter by five lines and would make FastAPI emit
    two schemas for it (`-Input` / `-Output`, since `base.Response` marks
    defaulted fields required on the way out) - the only such split in the
    whole API. It also draws a line worth drawing: this one is untrusted
    input a person's browser sent, the other is what the server computed.
    """

    title: str
    chart_type: ChartType
    labels: list[str] = Field(default_factory=list)
    values: list[float] = Field(default_factory=list)
    source: Literal["ai", "csv_fallback", "local_edit"] = "ai"
    fallback_reason: str | None = None
    live_source: LiveSource | None = None


class TileChatRequest(Response):
    """One turn of the tile-builder conversation. Stateless server-side: the
    whole exchange rides on the wire, the same as `agent.ChatRequest`."""

    messages: list[ChatMessage] = Field(default_factory=list)
    #: What the person is currently looking at, echoed back so a revision is
    #: applied to the draft on their screen rather than to one the server
    #: guessed at. `None` on the opening turn.
    draft: CustomChartDraftIn | None = None
    #: The data they pasted, if any - kept out of the message list so it stays
    #: the source of truth for every later turn, not just the first.
    raw_data: str | None = None
    #: The project this conversation is happening in front of, if any - the
    #: Agent page and the canvas's Custom Tile dialog both know their ambient
    #: project. Only meaningful on the opening turn, and only when nobody
    #: pasted data: it is what lets the agent tool-path fetch this project's
    #: own already-computed numbers (team effort, findings, risks, forecast)
    #: instead of asking a person to paste something the app already knows.
    scope_id: str | None = None


class TileChatResponse(Response):
    #: Always populated. A revision that cannot be applied returns the
    #: PREVIOUS draft rather than nothing, so the preview never blanks out
    #: mid-conversation - `narration/fallback.py`'s "never a blank page" rule
    #: applied to this surface.
    draft: CustomChartDraft
    changes: list[DraftChange] = Field(default_factory=list)
    #: The assistant's line in the transcript, composed from `changes`.
    reply: str = ""
    #: False when the turn changed nothing because it could not be applied -
    #: `error` then says why, and `draft` is unchanged.
    ok: bool = True
    error: str | None = None


class CustomTileIn(Response):
    name: str
    chart_type: ChartType
    labels: list[str]
    values: list[float]
    source_note: str | None = None
    #: Carried straight from the draft that was on screen when Save was
    #: pressed - see `LiveSource`. `labels`/`values` above still travel too,
    #: as the snapshot to fall back on if the live read ever fails.
    live_source: LiveSource | None = None


class CustomTileOut(Response):
    id: int
    name: str
    chart_type: ChartType
    #: The rows to draw right now - recomputed from `live_source` on every
    #: read when one is set, otherwise the frozen snapshot. Either way this
    #: is what a renderer should use; nothing downstream needs to know which.
    labels: list[str]
    values: list[float]
    source_note: str | None = None
    live_source: LiveSource | None = None


class CustomTileListBundle(Response):
    tiles: list[CustomTileOut] = Field(default_factory=list)
