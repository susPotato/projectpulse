"""The tile-building agent, with tools: real project data instead of a paste box.

`custom.draft_chart` only ever parses data already on the wire - pasted text,
or the CSV fallback. That is the right default (no session, no scope, works
from the Custom Tile dialog on any dashboard, and demos with no API key at
all - see `custom.py`'s module docstring). But a request like "track each
employee's effort and compare them" names a report over data the app already
has, and asking a person to paste hours they would have to go find in Jira
first is the wrong answer when this product's whole job is knowing that
number already.

So this is a second path, tried first on the opening turn when nobody pasted
anything and the project this conversation is happening in front of is
known: give the model a small set of **read-only** tools, each a thin
wrapper over a function `app/intelligence/pipeline.py` already exports
(`team_project`, `analyze_project`, `forecast_project`) or `app/risks/`
already computes. A tool call is a lookup against a deterministic bundle,
never a second calculation - the same governing rule (CLAUDE.md invariant 1)
survives contact with tool use because of what the tool is, not because of
an extra check bolted on afterward. The model still only chooses what to
look at and how to chart it; the final answer still goes through the exact
same `_parse_model_json` / `_validate_draft` gate `custom.py` uses for every
other turn; a model that never calls a tool, or never lands on valid JSON,
returns `None` and the caller falls back to `custom.py`'s "paste it instead"
message.

Anthropic-only, deliberately, unlike `narration/providers.py`'s multi-vendor
`Drafter`. Tool calling is a genuinely different wire shape per vendor, this
app is configured against Anthropic today, and building a second abstraction
for providers nobody has asked to use it on is exactly the premature
generality CLAUDE.md's own style notes argue against.
"""

from __future__ import annotations

import json
from typing import Any

from app.api.schemas.agent import ChatMessage
from app.api.schemas.dashboard import CustomChartDraft
from app.dashboard.custom import _parse_model_json, _validate_draft
from app.narration.providers import ModelConfig, _base, _import, _key

MAX_TOOL_ROUNDS = 4

SYSTEM_PROMPT = """You are helping a project manager build one chart tile for \
their dashboard. You have tools that return real, already-computed data from \
their current project - use them rather than asking the person to paste \
anything, and never invent a number that did not come from a tool result.

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
to something meaningful. If no tool call turned \
up data that answers the request, call another tool or ask a clarifying \
question in plain text instead of guessing at a chart."""

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


def agentic_draft(
    messages: list[ChatMessage], session, project_id: str, *, cfg: ModelConfig
) -> CustomChartDraft | None:
    """Run the tool loop for one opening turn. `None` means the caller should
    fall back to `custom.py`'s plain paste-and-parse path - a provider error,
    an exhausted round budget, or a final answer that never validates."""
    try:
        anthropic = _import("anthropic", "anthropic")
        client = anthropic.Anthropic(timeout=cfg.timeout_seconds, **_key(cfg), **_base(cfg))
    except Exception:  # noqa: BLE001 - no SDK, no key, any of it: fall back quietly
        return None

    conversation: list[dict] = [
        {"role": m.role, "content": m.content} for m in messages
    ]

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = client.messages.create(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=conversation,
            )

            if response.stop_reason == "tool_use":
                conversation.append(
                    {"role": "assistant", "content": response.content}
                )
                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    data = _execute_tool(block.name, session, project_id)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(data),
                        }
                    )
                conversation.append({"role": "user", "content": results})
                continue

            text = "".join(b.text for b in response.content if b.type == "text")
            parsed = _parse_model_json(text)
            return _validate_draft(parsed) if parsed is not None else None
    except Exception:  # noqa: BLE001 - any provider failure falls back, never 500s
        return None

    return None  # exhausted MAX_TOOL_ROUNDS without a final answer
