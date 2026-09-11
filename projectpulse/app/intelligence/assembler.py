"""Where every number in the product is born, and the only place it is formatted.

This is invariant 1 made executable. Rules and templates emit prose containing
`{{tokens}}` and never a digit; this module substitutes them from the same context
record the rule fired on. That single choice removes a whole class of bug: a
headline saying "11 days" and an evidence panel saying "12 days" cannot happen,
because both read the same key from the same dict, formatted by the same function.

It is also what makes the language model safe to use. The model is handed
tokenised text and is not permitted to change it; substitution happens *after*
validation, so a model that hallucinated a number would have had to invent a token
name, which `substitute` reports as unresolved rather than passing through.

An unresolved token is a hard failure, not a cosmetic one. A PM must never see a
literal ``{{max_propagated_days}}``, so `build_bundle` drops a finding it cannot
fully substitute and records why.

Pure - no session, no ORM, no clock. `as_of` and `generated_at` are passed in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime

from app.api.schemas.insight import (
    CausalLink,
    ChainStep,
    DataQuality,
    EvidenceRef,
    Finding,
    InsightBundle,
    RuleTrace,
    TimeInterval,
)
from app.ingest.sources.excel.identity import ANON_PREFIX
from app.intelligence.context import DeliveryContext
from app.intelligence.rules.engine import RuleHit
from app.intelligence.rules.tables import TOKEN
from app.intelligence.schedule.impact import ImpactReport
from app.intelligence.temporal.chains import CausalChain, ChainGroup, group_chains

log = logging.getLogger(__name__)

#: Fields whose value is a proportion and should be shown as a percentage.
_RATIO_SUFFIXES = ("_ratio", "_coverage")


def entity_label(entity_id: str, *, title: str | None = None) -> str:
    """`excel:Task:1:WBS-108` -> `WBS-108`.

    Domain ids are self-describing by design, which makes them unreadable on a
    slide. The last component is what a human typed.

    Except when nobody typed one. A row with no `Task ID` gets a synthesised
    key (`~anon-<16 hex>`), and that key is correct - it is what holds the row's
    identity across scans - but it is not a name. Printed as a label it looks
    exactly like a data bug, and on the Team page it looked like one for a
    while: a real task rendered as `~anon-ef0576ffa2b2a0f2` beside four
    colleagues with proper WBS codes.

    So `title` is the fallback, used only for those keys: the row still has the
    words the person wrote in the summary column, and those are the best label
    available. Callers with nothing to offer get the key, which is still better
    than an empty cell - the row exists and hiding it would be worse.
    """
    tail = _row_key(entity_id)
    if title and tail.startswith(ANON_PREFIX):
        return title
    return tail


def _row_key(entity_id: str) -> str:
    """The last component of a domain id - the key a person typed.

    Not `split(":", 3)[-1]`, which joined every key component back together.
    Excel entities are namespaced by project as well as by row key
    (`excel:Task:1:excel%3AProject%3A1%3AHRMS:WBS-108`, so two projects
    numbering their tasks the same way cannot collide), and that spelling put
    the encoded project id into the headline of every causal finding:
    `excel%3AProject%3A1%3AHRMS:WBS-108 moved from ...`.

    The row key is always last, however many namespacing parts precede it -
    and `domain_id` escapes a colon inside a component, so the last
    colon-separated part is exactly one component and never half a key.
    """
    return entity_id.rsplit(":", 1)[-1]


def format_fact(name: str, value: object) -> str:
    """One value, one formatting rule, applied everywhere.

    Kept deliberately small. Anything needing a different presentation should be
    a different context field, so the rule that reads it can be reviewed.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (datetime, date)):
        return value.isoformat()[:10]
    if isinstance(value, float):
        if name.endswith(_RATIO_SUFFIXES):
            return _percent(value)
        return f"{value:.1f}".rstrip("0").rstrip(".")
    return str(value)


def _percent(value: float) -> str:
    """A ratio as a percentage, without rounding away the fact that it is not zero.

    `round(0.005 * 100)` is 0 in Python - banker's rounding sends exactly .5 to
    even - so one blocked item in two hundred would print as "0%", which a reader
    takes to mean *none*. The symmetric case is worse: 199 of 200 printing as
    "100%" says the queue is entirely blocked when one test is still moving.

    So the two boundaries are never crossed by rounding. Everything between them
    rounds normally.
    """
    percent = round(value * 100)
    if percent == 0 and value > 0:
        return "<1%"
    if percent == 100 and value < 1:
        return ">99%"
    return f"{percent}%"


def substitute(text: str, facts: dict[str, str]) -> tuple[str, list[str]]:
    """Replace `{{tokens}}`, reporting any that could not be resolved.

    Returns ``(text, unresolved)``. Callers must treat a non-empty `unresolved`
    as a failure: a literal `{{token}}` reaching a PM destroys confidence in
    every number on the page, not just that one.
    """
    unresolved: list[str] = []

    def replace(match) -> str:
        name = match.group(1)
        if name not in facts:
            unresolved.append(name)
            return match.group(0)
        return facts[name]

    return TOKEN.sub(replace, text), unresolved


def facts_for(record: dict, names: set[str]) -> dict[str, str]:
    """Format exactly the fields a piece of prose refers to."""
    return {
        name: format_fact(name, record[name]) for name in names if name in record
    }


def _interval(change) -> TimeInterval:
    return TimeInterval(
        lower=change.occurred_at_lower,
        upper=change.occurred_at,
        precision=change.precision,
    )


def _step(change) -> ChainStep:
    return ChainStep(
        entity_id=change.entity_id,
        entity_label=entity_label(change.entity_id),
        field=change.field,
        old_value=change.old_value,
        new_value=change.new_value,
        occurred=_interval(change),
    )


def causal_link_of(chain: CausalChain) -> CausalLink:
    return CausalLink(
        template_id=chain.template_id,
        template_name=chain.template_name,
        question=chain.question,
        cause=_step(chain.cause),
        effect=_step(chain.effect),
        evidence_basis=chain.link.value,
        lag_days_min=round(chain.lag_days_min, 2),
        lag_days_max=round(chain.lag_days_max, 2),
        ordering_basis=chain.basis.value,
    )


def _chain_headline(group: ChainGroup) -> tuple[str, dict[str, str]]:
    """Deterministic prose for one cause and everything it explains.

    Written here rather than in a rule table because the numbers come from the
    chains themselves rather than from the context record - but it obeys the same
    law: tokens in the text, digits only in `facts`.

    A group of one reads as a single sentence; a group of many leads with the
    count, because that is the shape of the problem a PM escalates.
    """
    chain = group.representative
    facts = {
        "cause": entity_label(chain.cause.entity_id),
        "effect": entity_label(chain.effect.entity_id),
        "cause_field": chain.cause.field,
        "cause_from": chain.cause.old_value or "-",
        "cause_to": chain.cause.new_value or "-",
        "effect_from": chain.effect.old_value or "-",
        "effect_to": chain.effect.new_value or "-",
        "lag_min": format_fact("lag_min", chain.lag_days_min),
        "lag_max": format_fact("lag_max", chain.lag_days_max),
        "effect_count": format_fact("effect_count", group.effect_count),
    }

    when = (
        "{{lag_min}} days later"
        if chain.lag_is_certain
        else "between {{lag_min}} and {{lag_max}} days later"
    )

    if group.effect_count > 1:
        headline = (
            "{{cause}} moved from {{cause_from}} to {{cause_to}}; "
            f"{when} {{{{effect_count}}}} other items moved in step, "
            "starting with {{effect}}."
        )
    else:
        headline = (
            "{{cause}} moved from {{cause_from}} to {{cause_to}}; "
            f"{when} {{{{effect}}}} moved from {{{{effect_from}}}} to "
            "{{effect_to}}."
        )
    return headline, facts


def _chain_severity(group: ChainGroup) -> str:
    """How loudly to report a group.

    Driven by how well evidenced it is rather than how large it is - the first
    question a PM asks of a finding is how we know, not how big. Breadth only
    promotes a weakly-linked group one step, because a wide coincidence is still
    a coincidence.
    """
    chain = group.representative
    magnitude = abs(chain.effect_magnitude or 0)
    if chain.link.value == "dependency_edge":
        return "high" if magnitude >= 5 else "medium"
    if chain.link.value == "dependency_path":
        return "medium"
    return "medium" if group.effect_count >= 5 else "low"


def build_bundle(
    *,
    project_id: str,
    as_of: datetime,
    generated_at: datetime,
    context: DeliveryContext,
    hits: Sequence[RuleHit] = (),
    chains: Sequence[CausalChain] = (),
    impact: ImpactReport | None = None,
    evidence_for: Callable[[int], EvidenceRef] | None = None,
    narrative: str = "",
    narration_source: str = "template",
    narration_fallback_reason: str | None = None,
    max_chain_findings: int = 5,
) -> InsightBundle:
    """Turn everything the intelligence layer produced into one served object.

    `evidence_for` resolves a `_raw_data_id` into a displayable reference. It is
    injected rather than imported so this module stays pure and the evidence
    panel can be backed by a database, a cache, or a test stub.
    """
    record = context.as_record()
    findings: list[Finding] = []

    strongest = chains[0] if chains else None

    for hit in hits:
        names = set(TOKEN.findall(hit.headline)) | set(
            TOKEN.findall(hit.recommendation)
        )
        facts = facts_for(record, names)
        headline, missing_a = substitute(hit.headline, facts)
        recommendation, missing_b = substitute(hit.recommendation, facts)

        if missing_a or missing_b:
            # Never ship a literal {{token}}. Dropping the finding is the lesser
            # harm; a visible placeholder discredits every number on the page.
            log.error(
                "rule %s dropped: unresolved tokens %s",
                hit.rule_id,
                sorted(set(missing_a + missing_b)),
            )
            continue

        # A root-cause rule is about the chains, so it carries the best one.
        link = (
            causal_link_of(strongest)
            if strongest is not None and hit.category == "root_cause"
            else None
        )

        findings.append(
            Finding(
                id=hit.rule_id,
                category=hit.category,
                severity=hit.severity,
                headline=headline,
                recommendation=recommendation,
                # The pre-substitution text, kept for the narration client. It
                # is the only form of this prose a model may be shown.
                headline_template=hit.headline,
                recommendation_template=hit.recommendation,
                facts=facts,
                evidence=_evidence_for_rule(hit, strongest, evidence_for, impact),
                causal_link=link,
                rule_trace=RuleTrace(
                    rule_id=hit.rule_id,
                    conditions=list(hit.trace),
                    rationale=hit.rationale,
                ),
                evidence_basis=link.evidence_basis if link else "none",
            )
        )

    # One finding per cause, not per cause/effect pair. Eight identical sentences
    # about one blocked environment is technically correct and unreadable.
    for index, group in enumerate(group_chains(chains, limit=max_chain_findings)):
        chain = group.representative
        headline_template, facts = _chain_headline(group)
        headline, missing = substitute(headline_template, facts)
        if missing:  # pragma: no cover - facts are built from the same template
            log.error("chain %s dropped: unresolved tokens %s", group.template_id, missing)
            continue

        findings.append(
            Finding(
                id=f"chain:{group.template_id}:{index}",
                category="root_cause",
                severity=_chain_severity(group),
                headline=headline,
                recommendation=group.question,
                headline_template=headline_template,
                # The question is prose about a hypothesis, never a figure, so
                # it is already its own template.
                recommendation_template=group.question,
                facts=facts,
                evidence=[
                    ref
                    for ref in (
                        _resolve(raw_id, evidence_for) for raw_id in chain.evidence_ids()
                    )
                    if ref is not None
                ],
                causal_link=causal_link_of(chain),
                evidence_basis=group.link.value,
            )
        )

    bundle = InsightBundle(
        project_id=project_id,
        generated_at=generated_at,
        as_of=as_of,
        findings=findings,
        data_quality=DataQuality(
            rows_rejected=context.rows_rejected,
            changes_low_confidence=context.changes_low_confidence,
            changes_total=context.changes_total,
            baseline_coverage=context.baseline_coverage,
            edges_stated=context.edges_stated,
            edges_inferred=context.edges_inferred,
            edges_dropped=context.edges_dropped,
            depends_on_inferred_edges=context.depends_on_inferred_edges,
        ),
        narrative=narrative,
        narration_source=narration_source,  # type: ignore[arg-type]
        narration_fallback_reason=narration_fallback_reason,
        context=record,
    )
    return bundle


def _resolve(
    raw_id: int, evidence_for: Callable[[int], EvidenceRef] | None
) -> EvidenceRef | None:
    if evidence_for is None:
        return EvidenceRef(raw_data_id=raw_id)
    return evidence_for(raw_id)


#: How many source rows to attach to one aggregate finding. Enough to show the
#: claim is real, few enough that the panel stays readable.
_MAX_EVIDENCE = 5


def _evidence_for_rule(
    hit: RuleHit,
    strongest: CausalChain | None,
    evidence_for: Callable[[int], EvidenceRef] | None,
    impact: ImpactReport | None = None,
) -> list[EvidenceRef]:
    """The source rows behind an aggregate finding.

    A rule fires on counts, so its evidence is whatever those counts were counted
    from: the tasks that cannot hold their dates, or the chain that explains them.
    Where neither applies the rule trace is the evidence, which is why
    `Finding.is_defensible` accepts either - but a schedule claim with no rows
    behind it is exactly the kind of finding a PM cannot check, so it gets them.
    """
    raw_ids: list[int] = []

    if impact is not None and hit.category in ("schedule_risk", "milestone_risk"):
        raw_ids = [
            projection.raw_data_id
            for projection in impact.inconsistent()
            if projection.raw_data_id is not None
        ][:_MAX_EVIDENCE]

    if not raw_ids and strongest is not None and hit.category in (
        "root_cause",
        "schedule_risk",
    ):
        raw_ids = strongest.evidence_ids()

    return [
        ref
        for ref in (_resolve(raw_id, evidence_for) for raw_id in raw_ids)
        if ref is not None
    ]
