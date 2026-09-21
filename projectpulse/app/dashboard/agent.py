"""The tile-building agent, with tools: real project data instead of a paste box.

`custom.draft_chart` / `custom.chat_turn`'s plain path only ever parses data
already on the wire - pasted text, the CSV fallback, or a single-shot
revision prompt with no memory beyond the transcript. That stays the right
default (no session, no scope, works from the Custom Tile dialog on any
dashboard, and demos with no API key at all - see `custom.py`'s module
docstring). But a request like "track each employee's effort and compare
them" names a report over data the app already has, and asking a person to
paste hours they would have to go find in Jira first is the wrong answer
when this product's whole job is knowing that number already. And a request
the chart format genuinely cannot express - "give each bar its own color" on
a single-measure bar chart - deserves an answer that says so, not a silent
no-op.

So this is a second path: give the model a small set of **read-only** tools,
each a thin wrapper over a function `app/intelligence/pipeline.py` already
exports (`team_project`, `analyze_project`, `forecast_project`) or
`app/risks/` already computes. A tool call is a lookup against a
deterministic bundle, never a second calculation - the same governing rule
(CLAUDE.md invariant 1) survives contact with tool use because of what the
tool is, not because of an extra check bolted on afterward. The model still
only chooses what to look at and how to chart it; a final JSON answer still
goes through the exact same `_parse_model_json` / `_validate_draft` gate
`custom.py` uses for every other turn.

Tried on **every** turn - `custom.chat_turn` calls this after its own
deterministic `_local_revision` returns nothing (which stays first always:
an exact whole-message command needs no model and cannot drift a number on
its way through one), and only when nobody pasted raw data for this turn.
`current_draft`, when given, means this is a revision: the model sees what
is on screen now and either returns a full replacement chart or explains in
plain text why the request cannot become one - see `agentic_turn`'s return
shape. Any failure at any point (no key, no SDK, an unparseable and
non-explanatory final answer, exhausted tool rounds) returns `(None, None)`
and the caller falls back further, same as before this path existed.

Anthropic-only, deliberately, unlike `narration/providers.py`'s multi-vendor
`Drafter`. Tool calling is a genuinely different wire shape per vendor, this
app is configured against Anthropic today, and building a second abstraction
for providers nobody has asked to use it on is exactly the premature
generality CLAUDE.md's own style notes argue against.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.api.schemas.agent import ChatMessage
from app.api.schemas.dashboard import CustomChartDraft, LiveSource
from app.dashboard.custom import _draft_json, _parse_model_json, _validate_draft
from app.llm import usage
from app.narration.providers import ModelConfig, _base, _import, _key

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4

DRAFT_SYSTEM_PROMPT = """You are helping a project manager build one chart tile \
for their dashboard. You have tools that return real, already-computed data \
from their current project - use them rather than asking the person to \
paste anything, and never invent a number that did not come from a tool \
result.

Call as many tools as you need to answer their request, then respond with \
ONLY a single JSON object - no prose, no markdown fences - shaped exactly \
like this:

{"title": "...", "chart_type": "bar" | "line" | "pie", "labels": ["...", ...], "values": [1.0, ...]}

`labels` and `values` must be the same length and drawn only from tool \
results - never invent, extrapolate, or round to a "nicer" number. `title` \
is a short chart title only (four to six words) - put caveats, data-quality \
notes, or anything else worth saying in plain text BEFORE the JSON object, \
never inside the title string itself. Prefer "line" for a time series, \
"bar" for comparing categories, "pie" only for parts of one whole that sum \
to something meaningful.

If, after checking whatever tools are relevant, nothing answers the \
request, do not return JSON at all - reply in plain text, one or two short \
sentences, saying so and suggesting what you could chart instead."""

REVISE_SYSTEM_PROMPT = """You are helping a project manager refine one chart \
tile on their dashboard. You have tools that return real, already-computed \
data from their current project, if the revision calls for data you have \
not already seen - use them rather than guessing.

You will be given the chart they are looking at now and the conversation so \
far. If you can make the change, respond with ONLY a single JSON object - no \
prose, no markdown fences - shaped exactly like this:

{"title": "...", "chart_type": "bar" | "line" | "pie", "labels": ["...", ...], "values": [1.0, ...]}

Return the WHOLE chart every time, including the parts they did not ask you \
to change - carry those through untouched. `labels` and `values` must be \
the same length and drawn only from tool results, the current chart, or a \
number the person stated themselves - never invent, extrapolate, or round \
to a "nicer" number.

If the request cannot be expressed in this format - it names something this \
schema has no field for (per-item color, a second measure alongside the \
first, an annotation), or no tool has data that would answer it - do not \
return JSON. Reply in plain text instead: one or two short sentences saying \
plainly why not, and naming a concrete alternative if there is one (a \
different chart_type, a tile already on the catalogue, or what data you \
would need). Never apologize at length or restate the request back to them."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_team_effort",
        "description": (
            "Hours logged vs. hours planned per person on this project, plus "
            "their open/blocked QA items - the real answer to 'compare "
            "employee productivity' or 'who is over/under their plan'."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_project_findings",
        "description": (
            "This project's current risk findings from the rule engine "
            "(severity, headline, category) plus headline counts: milestones "
            "at risk, QA blocked vs. total, task count."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_risk_register",
        "description": (
            "The PM's own logged risks for this project: title, category, "
            "and pre-treatment likelihood x impact rating."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_delivery_forecast",
        "description": (
            "A range of possible finish dates (P50/P80/P95, ...) resampled "
            "from this project's observed schedule drift, with how many days "
            "late each percentile would land."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _execute_tool(name: str, session, project_id: str) -> dict:
    """One tool call, dispatched to the same functions the REST API uses.

    Trimmed to what a chart needs, not the full bundle - a tool result this
    small is what keeps the model from needing several rounds to find the
    field it wants.
    """
    if name == "get_team_effort":
        from app.intelligence.pipeline import team_project

        bundle = team_project(session, project_id=project_id)
        return {
            "members": [
                {
                    "name": m.name,
                    "hours_logged": m.hours_logged,
                    "hours_planned": m.hours_planned,
                    "qa_items": m.qa_items,
                    "qa_blocked": m.qa_blocked,
                }
                for m in bundle.members
            ]
        }

    if name == "get_project_findings":
        from app.intelligence.pipeline import analyze_project

        bundle = analyze_project(session, project_id=project_id)
        return {
            "milestones_at_risk": bundle.context.get("milestones_at_risk"),
            "qa_blocked": bundle.context.get("qa_blocked"),
            "qa_count": bundle.context.get("qa_count"),
            "task_count": bundle.context.get("task_count"),
            "findings": [
                {"severity": f.severity, "category": f.category, "headline": f.headline}
                for f in bundle.findings
            ],
        }

    if name == "get_risk_register":
        from app.risks.service import list_risks

        bundle = list_risks(session, project_ids=[project_id])
        return {
            "risks": [
                {"title": r.title, "category": r.category, "rating": r.pre_rating}
                for r in bundle.risks
            ]
        }

    if name == "get_delivery_forecast":
        from app.intelligence.pipeline import forecast_project

        bundle = forecast_project(session, project_id=project_id)
        if not bundle.available:
            return {"available": False, "reason": bundle.reason}
        return {
            "available": True,
            "committed_end": str(bundle.committed_end) if bundle.committed_end else None,
            "points": [
                {"percentile": p.percentile, "finish": str(p.finish), "days_late": p.days_late}
                for p in bundle.points
            ],
        }

    return {"error": f"unknown tool {name!r}"}


def _rows_match(labels: list[str], values: list[float], by_label: dict) -> bool:
    for label, value in zip(labels, values):
        found = by_label.get(label)
        if found is None:
            return False
        try:
            if abs(float(found) - float(value)) > 1e-6:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _infer_live_source(
    tool_calls: list[tuple[str, dict]], draft: CustomChartDraft, project_id: str
) -> LiveSource | None:
    """If this chart's every row can be read straight back out of one tool
    call, say how - otherwise `None`, and it stays a frozen snapshot.

    Deliberately conservative rather than clever: this never asks *why* the
    model picked the numbers it did, it only checks whether some (list,
    label field, value field) triple in the tool's own JSON reproduces
    `draft.labels`/`draft.values` exactly. A findings count grouped by
    severity, a forecast percentile relabelled, anything the model computed
    rather than copied - none of that has a matching field to point at, so
    it correctly returns `None` rather than a recipe that would drift from
    what was actually approved. More than one distinct tool called this turn
    is the same story: which one produced which row is not recoverable from
    the final JSON alone, so it is not claimed.
    """
    names = {name for name, _ in tool_calls}
    if len(names) != 1:
        return None
    if not draft.labels or len(draft.labels) != len(draft.values):
        return None
    tool_name = next(iter(names))
    # The latest call to this tool this turn - a revision that re-calls the
    # same tool mid-conversation should be judged against what it just saw.
    data = next(result for name, result in reversed(tool_calls) if name == tool_name)

    for list_field, rows in data.items():
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            continue
        str_fields = [k for k, v in rows[0].items() if isinstance(v, str)]
        num_fields = [
            k
            for k, v in rows[0].items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        for label_field in str_fields:
            for value_field in num_fields:
                by_label = {str(r.get(label_field)): r.get(value_field) for r in rows}
                if _rows_match(draft.labels, draft.values, by_label):
                    return LiveSource(
                        tool=tool_name,
                        project_id=project_id,
                        list_field=list_field,
                        label_field=label_field,
                        value_field=value_field,
                    )
    return None


def agentic_turn(
    messages: list[ChatMessage],
    session,
    project_id: str,
    *,
    cfg: ModelConfig,
    current_draft: CustomChartDraft | None = None,
) -> tuple[CustomChartDraft | None, str | None]:
    """Run the tool loop for one turn - the opening draft when `current_draft`
    is `None`, a revision when it is not. Returns `(draft, note)`, exactly one
    populated:

    - `(draft, None)` - a full chart, validated, ready to show.
    - `(None, note)` - the model looked (tools included) and answered in
      plain text instead of JSON: a real answer, just not a chart. The
      caller shows `note` as the reply rather than treating it as a failure.
    - `(None, note)` - the model itself was unreachable (a real API error -
      bad key, no credit, rate limited, network) rather than declining to
      answer. Worth telling the person outright rather than quietly serving
      the generic "unchanged" fallback as if nothing had gone wrong.
    - `(None, None)` - no client at all (no key configured, SDK not
      installed) or a dead round budget: the caller falls back further.
    """
    try:
        anthropic = _import("anthropic", "anthropic")
        client = anthropic.Anthropic(timeout=cfg.timeout_seconds, **_key(cfg), **_base(cfg))
    except Exception as exc:  # noqa: BLE001 - no SDK, no key, any of it: fall back quietly
        log.warning("tile agent client unavailable: %s", exc)
        return None, None

    system = REVISE_SYSTEM_PROMPT if current_draft is not None else DRAFT_SYSTEM_PROMPT
    conversation: list[dict] = []
    if current_draft is not None:
        conversation.append(
            {"role": "user", "content": f"The chart on screen now:\n{_draft_json(current_draft)}"}
        )
        conversation.append({"role": "assistant", "content": "Understood - what would you like changed?"})
    conversation.extend({"role": m.role, "content": m.content} for m in messages)
    #: Every tool call this turn actually made, name and raw result - what
    #: `_infer_live_source` checks the final draft against. Turn-scoped, not
    #: carried from a previous revision: what mattered to *this* answer is
    #: only what was looked up to produce it.
    tool_calls: list[tuple[str, dict]] = []

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            # One row per round, not one per turn: a tool loop is several
            # billed calls and collapsing them would hide the round count,
            # which is the thing that makes this feature expensive.
            with usage.track("anthropic", cfg.model):
                response = client.messages.create(
                    model=cfg.model,
                    max_tokens=cfg.max_tokens,
                    system=system,
                    tools=TOOLS,
                    messages=conversation,
                )
                usage.report_anthropic(response)

            if response.stop_reason == "tool_use":
                conversation.append(
                    {"role": "assistant", "content": response.content}
                )
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    data = _execute_tool(block.name, session, project_id)
                    tool_calls.append((block.name, data))
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(data),
                        }
                    )
                conversation.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in response.content if b.type == "text").strip()
            parsed = _parse_model_json(text)
            draft = _validate_draft(parsed) if parsed is not None else None
            if draft is not None:
                live_source = _infer_live_source(tool_calls, draft, project_id)
                if live_source is not None:
                    draft = draft.model_copy(update={"live_source": live_source})
                return draft, None
            # Not valid JSON - real prose (the model explaining itself, per
            # the system prompt) is a usable answer on its own.
            return (None, text) if text else (None, None)
    except Exception as exc:  # noqa: BLE001 - never 500s, but this is worth saying plainly
        log.exception("tile agent call failed for project %s", project_id)
        return None, f"The AI agent hit an error talking to the model ({exc}) - try again in a moment."

    return None, None  # exhausted MAX_TOOL_ROUNDS without a final answer
