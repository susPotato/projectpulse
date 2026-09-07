"""Deterministic prose. The safety net, and the offline demo.

Built before any model client, because a product whose thesis is determinism
cannot have a language model on its critical path. If the API key is missing, the
network is down, or a draft fails validation, the page still fills - with this.

Plain Python rather than Jinja: the structure is fixed and small, a template
engine would add a dependency and a second place for logic to hide, and the
output has to be diffable in a test. The cost is that this file looks repetitive,
which is the correct trade for prose a judge will read line by line.

**No number is computed here.** Every figure comes from `Finding.facts`, already
formatted by `assembler.format_fact`. This module only chooses which sentences to
emit and in what order, so the narrative and the findings above it can never
disagree.

The four sections are the four questions the product exists to answer, in the
order a PM asks them.
"""

from __future__ import annotations

from app.api.schemas.insight import Finding, InsightBundle

#: The section headings, in order. Exported because the insight page splits the
#: narrative on these exact strings - renaming one here would silently empty the
#: summary panel rather than raise, so `tests/test_api.py` pins the two together.
QUESTION_HEADINGS = (
    "What is at risk",
    "Why it is happening",
    "What it will impact",
    "What to do next",
    "What this analysis could not use",
)

#: Categories that answer each question, in the order they should be read.
_AT_RISK = ("schedule_risk", "milestone_risk", "quality_risk", "resource_risk")
_QUALITY = ("data_quality", "evidence_quality")

_SEVERITY_WORD = {
    "critical": "critical",
    "high": "significant",
    "medium": "moderate",
    "low": "minor",
    "info": "informational",
}


def _pick(bundle: InsightBundle, categories: tuple[str, ...]) -> list[Finding]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    return sorted(
        (f for f in bundle.findings if f.category in categories),
        key=lambda f: order.get(f.severity, 9),
    )


def _sentences(findings: list[Finding], limit: int = 3) -> list[str]:
    return [f.headline for f in findings[:limit]]


def _what_is_at_risk(bundle: InsightBundle) -> str:
    risks = _pick(bundle, _AT_RISK)
    if not risks:
        return (
            "Nothing in this project currently breaches a delivery threshold. "
            "The schedule is consistent with its own dependencies."
        )

    worst = _SEVERITY_WORD.get(risks[0].severity, risks[0].severity)
    lines = " ".join(_sentences(risks))
    return f"There is {worst} delivery risk in this project. {lines}"


#: Evidence bases, strongest first. The explanation should lead with the best
#: evidenced chain, not merely the first one assembled.
_BASIS_ORDER = ("same_entity", "dependency_edge", "dependency_path", "same_project")


def _why(bundle: InsightBundle) -> str:
    # Informational findings can carry a chain to illustrate themselves - the
    # "N findings trace to a stated dependency" summary does. Leading the
    # explanation with the summary instead of the chain it summarises tells a PM
    # how well evidenced the answer is without ever giving them the answer.
    causal = sorted(
        (
            f
            for f in bundle.findings
            if f.causal_link is not None and f.severity != "info"
        ),
        key=lambda f: _BASIS_ORDER.index(f.evidence_basis)
        if f.evidence_basis in _BASIS_ORDER
        else len(_BASIS_ORDER),
    )
    if not causal:
        return (
            "No cause can be established from the available data. Events can be "
            "listed, but no pair of them is both ordered and connected strongly "
            "enough to support a causal claim, so none is made."
        )

    best = causal[0]
    link = best.causal_link
    assert link is not None

    basis = {
        "dependency_edge": (
            "a dependency stated in the schedule sheet connects the two tasks"
        ),
        "dependency_path": (
            "the two tasks are connected through the dependency chain, though not "
            "directly"
        ),
        "same_entity": "both changes are on the same task",
        "same_project": (
            "the two belong to the same project, which is a weaker link than a "
            "stated dependency"
        ),
    }.get(link.evidence_basis, "the two are related")

    certainty = (
        "Both timestamps are exact."
        if link.ordering_basis == "exact"
        else (
            "The later event was observed by comparing two spreadsheet snapshots, "
            "so its timing is a range rather than a point - the order is still "
            "provable because the ranges do not overlap."
        )
    )

    return f"{best.headline} This is reported because {basis}. {certainty}"


def _impact(bundle: InsightBundle, already_said: set[str]) -> str:
    """What follows from the findings - without repeating them.

    `already_said` carries the headlines the risk section used. Restating a
    sentence two paragraphs later reads as padding and makes a reader wonder
    whether it is a second, separate problem.
    """
    milestone = [f for f in bundle.findings if f.category == "milestone_risk"]
    schedule = [f for f in bundle.findings if f.category == "schedule_risk"]

    fresh = [f for f in (milestone + schedule) if f.headline not in already_said]
    if not fresh:
        if bundle.data_quality.depends_on_inferred_edges:
            return (
                "The downstream impact is described above. Note that this "
                "projection depends on dependency edges inferred from the "
                "schedule's own dates rather than stated by a person; it should "
                "be confirmed before being quoted externally."
            )
        return "The downstream impact is described above."

    parts = [f.headline for f in fresh[:2]]
    tail = ""
    if bundle.data_quality.depends_on_inferred_edges:
        tail = (
            " Note that this projection depends on dependency edges inferred from "
            "the schedule's own dates rather than stated by a person; it should be "
            "confirmed before being quoted externally."
        )
    return " ".join(parts) + tail


def _next_steps(bundle: InsightBundle) -> str:
    actions: list[str] = []
    for finding in bundle.by_severity():
        if finding.recommendation and finding.recommendation not in actions:
            actions.append(finding.recommendation)
        if len(actions) == 3:
            break

    if not actions:
        return "No action is required from this analysis."
    return " ".join(f"{i}. {a}" for i, a in enumerate(actions, start=1))


def _data_quality(bundle: InsightBundle) -> str:
    notes = _pick(bundle, _QUALITY)
    if not notes:
        return ""
    return " ".join(_sentences(notes, limit=3))


def render_narrative(bundle: InsightBundle) -> str:
    """The full narrative, as four labelled sections plus a caveat block.

    Sections are labelled rather than run together because a PM scans for the one
    they need, and because a labelled structure is what the model is later asked
    to reproduce - making a drafted narrative and a fallback narrative comparable.
    """
    risk_findings = _pick(bundle, _AT_RISK)
    said = set(_sentences(risk_findings))

    bodies = [
        _what_is_at_risk(bundle),
        _why(bundle),
        _impact(bundle, said),
        _next_steps(bundle),
    ]
    sections = list(zip(QUESTION_HEADINGS, bodies))

    quality = _data_quality(bundle)
    if quality:
        sections.append((QUESTION_HEADINGS[4], quality))

    return "\n\n".join(f"{title}\n{body}" for title, body in sections)
