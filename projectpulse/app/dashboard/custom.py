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

`chat_turn` then makes that a conversation rather than one shot: draft, look
at it, say what is wrong, look again. Three rules hold it to the same
standard as the one-shot path:

* **The deterministic path goes first.** "make it a line chart" and "rename
  it to Q3 spend" are exact operations, so `_local_revision` applies them
  with no model and no network - the governing rule ("deterministic where
  possible") reaching one layer further in. Only what it cannot match is
  worth a call.
* **The server diffs, the model does not narrate.** A revision regenerates
  the whole chart, so a rename can come back having also moved a value.
  `diff_drafts` compares the two drafts and `summarize` words the result, so
  the transcript line cannot claim something the preview does not show.
* **A failed turn keeps the previous draft.** The preview never blanks out
  because a model returned nonsense; the turn is reported as not applied.
"""

from __future__ import annotations

import csv
import io
import json
import re

from sqlalchemy import select

from app.api.schemas.agent import ChatMessage
from app.api.schemas.dashboard import (
    ChartType,
    CustomChartDraft,
    CustomTileIn,
    CustomTileOut,
    DraftChange,
    TileChatResponse,
)
from app.models.dashboard import CustomTile
from app.narration.client import Drafter
from app.narration.providers import ModelConfig

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


# ---------------------------------------------------------------------------
# The conversation: refine a draft by talking to it.
# ---------------------------------------------------------------------------

REVISE_SYSTEM_PROMPT = """You are helping a project manager refine one small \
chart. You will be given the chart they are looking at now, the data it came \
from, and what they have asked for.

Respond with ONLY a single JSON object - no prose, no markdown fences - \
shaped exactly like this:

{"title": "...", "chart_type": "bar" | "line" | "pie", "labels": ["...", ...], "values": [1.0, ...]}

Return the WHOLE chart every time, including the parts they did not ask you \
to change - carry those through untouched.

`labels` and `values` must be the same length. Every value must come from \
the data given, from the current chart, or be a number the person stated \
themselves in the conversation. Never invent a data point, never \
extrapolate, never forecast, and never round to a "nicer" number - if they \
ask for something the data cannot support, return the chart unchanged."""


def _draft_json(draft: CustomChartDraft) -> str:
    return json.dumps(
        {
            "title": draft.title,
            "chart_type": draft.chart_type,
            "labels": draft.labels,
            "values": draft.values,
        }
    )


def _revise_user_prompt(
    messages: list[ChatMessage], draft: CustomChartDraft, raw_data: str | None
) -> str:
    parts = ["The chart they are looking at now:\n" + _draft_json(draft)]
    if raw_data and raw_data.strip():
        parts.append("\nThe data it was built from:\n" + raw_data.strip())
    transcript = "\n".join(
        ("PM: " if m.role == "user" else "You: ") + m.content.strip()
        for m in messages
        if m.content.strip()
    )
    parts.append("\nThe conversation so far:\n" + transcript)
    return "\n".join(parts)


#: Whole-message patterns only. A message that is *exactly* one of these is
#: unambiguous, so it needs no model. Anything longer ("make it a line chart
#: and drop February") deliberately falls through to the model rather than
#: being half-applied here, which would silently ignore the rest of the ask.
_CHART_TYPE_ONLY = re.compile(
    r"^(?:please\s+)?"
    r"(?:(?:make|change|switch|turn|set|show)\s+(?:it|this|the\s+chart)?\s*)?"
    r"(?:(?:in)?to\s+|as\s+)?"
    r"(?:a\s+|an\s+)?"
    r"(bar|line|pie)"
    r"(?:\s+chart|\s+graph)?"
    r"\s*[.!]?\s*$",
    re.IGNORECASE,
)
#: A rename needs an explicit target - "to X", "call it X", "title: X".
#: Without that requirement "rename this please" reads as a rename to
#: "please", which is worse than not matching at all: the fast path would
#: confidently apply a title nobody asked for and never consult the model.
_RENAME_ONLY = re.compile(
    r"^(?:please\s+)?(?:rename|retitle)\s+(?:it|this|the\s+tile|the\s+chart)?"
    r"\s*to\s+(.+?)\s*[.!]?\s*$"
    r"|^(?:please\s+)?call\s+(?:it|this|the\s+tile|the\s+chart)\s+(.+?)\s*[.!]?\s*$"
    r"|^title\s*[:=]\s*(.+?)\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _local_revision(message: str, draft: CustomChartDraft) -> CustomChartDraft | None:
    """Apply an unambiguous whole-message command with no model at all.

    Returns None when the message is anything else, which is most of the
    time - this is a fast path for the two refinements people actually
    repeat, not an attempt at parsing English."""
    text = message.strip()
    if not text:
        return None

    match = _CHART_TYPE_ONLY.match(text)
    if match:
        chart_type = match.group(1).lower()
        if chart_type == draft.chart_type:
            # Matched, but it is already that chart. Hand back the draft
            # itself rather than None: the ask was understood, so the honest
            # answer is "that changed nothing", not a model call that will
            # arrive at the same place more slowly.
            return draft
        return draft.model_copy(
            update={
                "chart_type": chart_type,
                "source": "local_edit",
                "fallback_reason": None,
            }
        )

    match = _RENAME_ONLY.match(text)
    if match:
        groups = [g for g in match.groups() if g]
        title = (groups[0] if groups else "").strip().strip('"').strip("'")
        if title:
            return draft.model_copy(
                update={
                    "title": title,
                    "source": "local_edit",
                    "fallback_reason": None,
                }
            )
        return None

    return None


def _join(labels: list[str]) -> str:
    shown = labels[:3]
    text = ", ".join(shown)
    extra = len(labels) - len(shown)
    return text + " and " + str(extra) + " more" if extra > 0 else text


def _values_changed(before: CustomChartDraft, after: CustomChartDraft) -> int:
    """How many rows present in both drafts carry a different value.

    Positional when the label lists match exactly, which is the common case
    and the only one where duplicate labels are unambiguous; by label
    otherwise, so a reorder is not miscounted as every value moving."""
    if before.labels == after.labels:
        return sum(1 for old, new in zip(before.values, after.values) if old != new)
    old_by_label = dict(zip(before.labels, before.values))
    return sum(
        1
        for label, value in zip(after.labels, after.values)
        if label in old_by_label and old_by_label[label] != value
    )


def diff_drafts(before: CustomChartDraft, after: CustomChartDraft) -> list[DraftChange]:
    """What actually changed between two drafts, by comparison.

    The only place a revision is put into words. Nothing here asks the model
    what it did - see `DraftChange` for why that distinction is the point."""
    changes: list[DraftChange] = []

    if before.title != after.title:
        changes.append(DraftChange(field="title", summary='renamed to "' + after.title + '"'))

    if before.chart_type != after.chart_type:
        changes.append(
            DraftChange(
                field="chart_type",
                summary="chart type " + before.chart_type + " -> " + after.chart_type,
            )
        )

    added = [label for label in after.labels if label not in before.labels]
    removed = [label for label in before.labels if label not in after.labels]
    if added:
        changes.append(DraftChange(field="labels", summary="added " + _join(added)))
    if removed:
        changes.append(DraftChange(field="labels", summary="removed " + _join(removed)))
    if not added and not removed and before.labels != after.labels:
        changes.append(
            DraftChange(
                field="labels", summary="reordered " + str(len(after.labels)) + " rows"
            )
        )

    moved = _values_changed(before, after)
    if moved:
        word = "1 value" if moved == 1 else str(moved) + " values"
        changes.append(DraftChange(field="values", summary="changed " + word))

    return changes


def summarize(changes: list[DraftChange]) -> str:
    """The assistant's transcript line, built from the computed diff."""
    if not changes:
        return "That left the chart unchanged."
    return "Done - " + "; ".join(change.summary for change in changes) + "."


def _opening_reply(draft: CustomChartDraft) -> str:
    rows = "1 row" if len(draft.labels) == 1 else str(len(draft.labels)) + " rows"
    lead = 'Drafted "' + draft.title + '" as a ' + draft.chart_type + " chart from " + rows + "."
    if draft.source == "csv_fallback":
        reason = " (" + draft.fallback_reason + ")" if draft.fallback_reason else ""
        return "Read your data as a plain table" + reason + ", so check the numbers. " + lead
    return lead + " Tell me what to change."


def _last_user_message(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user":
            return message.content
    return ""


def chat_turn(
    messages: list[ChatMessage],
    draft: CustomChartDraft | None,
    raw_data: str | None,
    *,
    drafter: Drafter | None,
    session=None,
    project_id: str | None = None,
    agent_cfg: ModelConfig | None = None,
) -> TileChatResponse:
    """One turn: draft a chart if there isn't one, otherwise revise it.

    Raises `ValueError` only on the opening turn, when there is nothing to
    build a chart from - the route turns that into a 400. Every later turn
    returns a response carrying at worst the draft it was given, because
    blanking someone's chart is never the right answer to a sentence the
    model could not parse.

    `session` / `project_id` / `agent_cfg` are optional and only ever used
    together - see `app/dashboard/agent.py`. Any one missing (no project in
    view, no Anthropic key, tool use itself failing) falls straight through
    to the plain paths below, unchanged. Tried on every turn now, not only
    the opening one: a revision can need fresh data just as much as a first
    draft can, and a request the schema cannot express deserves the model's
    own explanation rather than a scripted "unchanged"."""
    asked = _last_user_message(messages).strip()
    if not asked:
        raise ValueError("say what you want the tile to show")

    agentic_available = session is not None and project_id and agent_cfg is not None

    if draft is None:
        # Nothing on screen yet - this is the one-shot path, reused whole.
        # Their message is the hint when they also pasted data, and is itself
        # the data when they did not.
        has_data = bool(raw_data and raw_data.strip())
        agentic_note: str | None = None

        if not has_data and agentic_available:
            from app.dashboard.agent import agentic_turn

            agentic, agentic_note = agentic_turn(
                messages, session, project_id, cfg=agent_cfg
            )
            if agentic is not None:
                return TileChatResponse(
                    draft=agentic, changes=[], reply=_opening_reply(agentic), ok=True
                )
            # A note with no chart still needs *some* draft on the opening
            # turn (the response schema requires one) - try the plain path
            # next, and only surface the note if that fails too, below.

        try:
            opening = draft_chart(
                raw_data if has_data else asked,
                asked if has_data else None,
                drafter=drafter,
            )
        except ValueError:
            if agentic_note:
                raise ValueError(agentic_note) from None
            raise
        return TileChatResponse(
            draft=opening, changes=[], reply=_opening_reply(opening), ok=True
        )

    # Deterministic first: an exact command needs no model, and cannot drift
    # a number on its way through one.
    revised = _local_revision(asked, draft)

    if revised is None and agentic_available:
        from app.dashboard.agent import agentic_turn

        candidate, note = agentic_turn(
            messages, session, project_id, cfg=agent_cfg, current_draft=draft
        )
        if candidate is not None:
            revised = candidate
        elif note:
            # The model looked - tools included - and answered in plain text
            # instead of silently leaving the chart unchanged. That is a real
            # answer, not a failure.
            return TileChatResponse(draft=draft, changes=[], reply=note, ok=True)

    if revised is None and drafter is not None:
        try:
            raw = drafter(
                REVISE_SYSTEM_PROMPT, _revise_user_prompt(messages, draft, raw_data)
            )
            parsed = _parse_model_json(raw)
            candidate = _validate_draft(parsed) if parsed is not None else None
        except Exception:  # noqa: BLE001 - any provider failure is a failed turn
            candidate = None
        if candidate is not None:
            revised = candidate

    if revised is None:
        reason = (
            "I could not turn that into a change to this chart. Try naming the "
            "row you mean, or edit the numbers directly below."
            if drafter is not None
            else "Narration is off, so I can only switch the chart type or "
            "rename the tile. Edit the rows directly below, or turn narration "
            "on in Settings."
        )
        return TileChatResponse(draft=draft, changes=[], reply=reason, ok=False, error=reason)

    changes = diff_drafts(draft, revised)
    return TileChatResponse(
        draft=revised, changes=changes, reply=summarize(changes), ok=True
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
