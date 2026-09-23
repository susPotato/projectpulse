"""Stage 5 — say what a feature area's traceability means, in a PM's words.

Everything upstream speaks in artifacts: `corroborated`, `unverified`,
`core/task_executors.py::run_step`. That vocabulary is correct and it is the
right thing to store, but it is not what a delivery manager reads. This
stage turns one feature area's verdicts into four plain sentences.

**It is a translation, not a second opinion.** The model is given the
verdicts that were already reached and told to restate them; it is
explicitly forbidden from re-adjudicating, from upgrading `unverified` into
"probably done", and from naming a file that is not in front of it. If it
disagrees with the verdicts it must say so in `caveat` rather than quietly
substituting its own.

Cheap: one call per feature area, not per ticket - 29 calls for a backlog
where Stage 3 needed 173.
"""

from __future__ import annotations

import collections
import logging
from typing import Literal

from pydantic import BaseModel, Field

from tracelink.adjudicate import StructuredCaller, Usage
from tracelink.artifacts import Ticket, Verdict

logger = logging.getLogger(__name__)


class AreaExplanation(BaseModel):
    """Plain language for one feature area. No jargon, no file paths."""

    headline: str = Field(
        description="One sentence a delivery manager could paste into a status "
                    "report. Say what this area of the product is and how much "
                    "of it the code backs up. No file paths, no verdict words.")
    what_is_there: str = Field(
        description="1-2 sentences on what the code shows exists, in product "
                    "terms - features and behaviour, not modules.")
    what_is_unclear: str = Field(
        description="1-2 sentences on what could not be confirmed and why. If "
                    "everything was confirmed, say so plainly.")
    suggested_action: str = Field(
        description="One concrete next step for a human, or '' if none is "
                    "warranted. Never invent urgency.")
    caveat: str = Field(
        default="",
        description="Only if the evidence looks wrong or self-contradictory. "
                    "Empty otherwise - do not manufacture a reservation.")
    confidence: Literal["high", "medium", "low"]


SYSTEM = """You translate software traceability findings into language a delivery \
manager can act on.

You are given one feature area of a backlog: its tickets, the tracker status of each, \
and the verdict an earlier analysis reached about whether the code backs each one up.

The verdicts mean:
- corroborated - code exists that plausibly implements the ticket. NOT proof the \
feature works; the analysis read the code, it did not run it.
- contradicted - the code positively shows the ticket's claim is wrong.
- unverified - the code that was looked at did not settle it. This is NOT evidence the \
feature is missing; the analysis only saw a keyword-selected subset of the repository.
- status conflict - the tracker's status disagrees with the code, most often a ticket \
still open whose feature is clearly built.

Rules:
- **Restate, do not re-judge.** The verdicts are the input. Never upgrade "unverified" \
to "probably built", never soften "contradicted", and never decide a ticket yourself.
- Write for someone who does not know the codebase. No file paths, no function names, \
no module names, no words like "corroborated" or "adjudicated".
- Be specific about the product. "Scheduling and background tasks" beats "this area".
- Do not invent urgency, risk, or effort estimates. You cannot see dates or people.
- If most of an area is unverified, say that the analysis could not confirm it - not \
that the work is missing."""


def group_by_component(tickets: list[Ticket]) -> dict[str, list[Ticket]]:
    out: dict[str, list[Ticket]] = collections.defaultdict(list)
    for t in tickets:
        out[t.component or "(no component)"].append(t)
    return dict(out)


def build_prompt(area: str, tickets: list[Ticket], verdicts: dict[str, Verdict]) -> str:
    counts = collections.Counter()
    lines = []
    for t in tickets:
        v = verdicts.get(t.uid)
        counts[v.verdict if v else "not analysed"] += 1
        bits = [f"- [{t.status or 'no status'}] {t.summary}"]
        if v:
            bits.append(f"    verdict: {v.verdict} ({v.confidence})")
            if v.status_conflict:
                bits.append("    STATUS CONFLICT: the tracker and the code disagree")
            if v.reasoning:
                bits.append(f"    why: {v.reasoning.strip()}")
        else:
            bits.append("    verdict: not analysed")
        lines.append("\n".join(bits))

    tally = ", ".join(f"{n} {k}" for k, n in counts.most_common())
    return (
        f"## Feature area: {area}\n"
        f"{len(tickets)} tickets — {tally}\n\n"
        "## Tickets and verdicts\n"
        + "\n".join(lines)
        + "\n\nExplain this area for a delivery manager."
    )


def explain(
    tickets: list[Ticket],
    verdicts: list[Verdict],
    caller: StructuredCaller,
    only: set[str] | None = None,
    on_result=None,
) -> tuple[list[dict], Usage]:
    by_uid = {v.uid: v for v in verdicts}
    groups = group_by_component(tickets)
    total = Usage()
    out: list[dict] = []

    for area in sorted(groups, key=lambda a: -len(groups[a])):
        if only and area not in only:
            continue
        rows = groups[area]
        prompt = build_prompt(area, rows, by_uid)
        try:
            payload, usage = caller.call(SYSTEM, prompt, AreaExplanation, label=area)
        except Exception as exc:                       # noqa: BLE001
            logger.error("%s: %s", area, exc)
            continue
        total.add(usage)
        row = {
            "component": area,
            "tickets": len(rows),
            "cost_usd": round(usage.cost(caller.model), 6),
            **payload,
        }
        out.append(row)
        if on_result:
            on_result(area, row, usage)
    return out, total
