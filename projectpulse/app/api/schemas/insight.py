"""The contract between the intelligence layer and everything that displays it.

Frozen early and deliberately: the React route, the narration layer and the
validator can all be built against this without waiting for each other, and none
of them needs to know how a finding was produced.

Three properties the shape enforces rather than merely documents:

**A finding carries its own evidence.** `evidence` is not optional and not a
convenience - a finding that cannot name the source rows it came from should not
have been emitted. The list is built from `_raw_data_id` values copied through
raw -> tool -> domain, never re-derived.

**Numbers live in `facts`, prose lives in `headline`.** The headline that reaches
the model contains `{{tokens}}`; the server substitutes them from `facts` after
validation. So the same number cannot be formatted one way in the headline and
another in the evidence panel, and a model cannot change a digit because it never
sees one.

**Confidence is structural, not a vibe.** `evidence_basis` says how the claim was
established - a stated dependency edge, an inferred one, or a shared project - so
the UI can rank and a reader can discount without having to trust a score.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.api.schemas.base import Response

Severity = Literal["critical", "high", "medium", "low", "info"]
NarrationSource = Literal["template", "model"]
EvidenceBasis = Literal[
    "same_entity", "dependency_edge", "dependency_path", "same_project", "none"
]
Precision = Literal["exact", "bounded"]


class EvidenceRef(Response):
    """A pointer back to the record a claim came from.

    `raw_data_id` is a foreign key by convention into the `_raw_*` table named by
    `raw_table`. It is stamped by the extractor and copied unchanged by every
    downstream hop - if it is missing, the chain is broken and the finding should
    not be shown.
    """

    raw_data_id: int | None = None
    raw_table: str | None = None
    #: The request or file location, verbatim. Shown to a PM as-is.
    url: str | None = None
    #: Human breadcrumb, e.g. "Activities!row5".
    remark: str | None = None
    label: str | None = None


class TimeInterval(Response):
    """When something happened, honestly.

    `lower == upper` only for events from a changelog. Anything derived from
    comparing two spreadsheet snapshots is a genuine interval, and collapsing it
    to a midpoint would invent precision the source never had.
    """

    lower: datetime
    upper: datetime
    precision: Precision

    @property
    def is_certain(self) -> bool:
        return self.lower == self.upper


class ChainStep(Response):
    """One end of a causal chain."""

    entity_id: str
    entity_label: str
    field: str
    old_value: str | None = None
    new_value: str | None = None
    occurred: TimeInterval


class CausalLink(Response):
    """Why we believe one change explains another."""

    template_id: str
    template_name: str
    #: The PM question this hypothesis answers.
    question: str
    cause: ChainStep
    effect: ChainStep
    evidence_basis: EvidenceBasis
    #: The gap between cause and effect, as a range. Equal bounds only when both
    #: ends came from a changelog.
    lag_days_min: float
    lag_days_max: float
    ordering_basis: Literal["exact", "bounded_disjoint"]


class RuleTrace(Response):
    """Why a rule fired, in terms a PM can challenge."""

    rule_id: str
    #: Each condition with the value it actually compared against.
    conditions: list[str] = Field(default_factory=list)
    #: Why this threshold was chosen.
    rationale: str = ""


class Finding(Response):
    """One thing worth a PM's attention, with everything needed to defend it."""

    id: str
    category: str
    severity: Severity
    #: Fully substituted prose. No `{{tokens}}` survive into a served bundle.
    headline: str
    recommendation: str = ""
    #: Every number this finding states, by token name. The single source for
    #: substitution, so a digit cannot drift from the data that produced it.
    facts: dict[str, str] = Field(default_factory=dict)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    causal_link: CausalLink | None = None
    rule_trace: RuleTrace | None = None
    evidence_basis: EvidenceBasis = "none"

    @property
    def is_defensible(self) -> bool:
        """A finding with no evidence and no rule trace cannot be shown."""
        return bool(self.evidence or self.rule_trace)


class DataQuality(Response):
    """What the analysis could not use.

    Surfaced beside every bundle rather than buried, because a health score
    computed on 60% of a project is worse than no score at all.
    """

    rows_rejected: int = 0
    changes_low_confidence: int = 0
    changes_total: int = 0
    baseline_coverage: float = 0.0
    edges_stated: int = 0
    edges_inferred: int = 0
    edges_dropped: int = 0
    depends_on_inferred_edges: bool = False


class InsightBundle(Response):
    """Everything the insight screen renders, for one project at one moment."""

    project_id: str
    generated_at: datetime
    #: The scan time the analysis reflects, which is not the same as when it ran.
    as_of: datetime

    findings: list[Finding] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)

    #: The narrative summary. Always present - the deterministic template is the
    #: fallback, so a bundle is never empty because a model was unavailable.
    narrative: str = ""
    narration_source: NarrationSource = "template"
    #: Set when a model draft was rejected by the validator and the template was
    #: served instead. Visible so a silent downgrade cannot go unnoticed.
    narration_fallback_reason: str | None = None

    #: The flat scalars every rule evaluated against. Shipped so the UI can show
    #: a rule trace against real values without a second request.
    context: dict = Field(default_factory=dict)

    @property
    def top_severity(self) -> Severity:
        for level in ("critical", "high", "medium", "low", "info"):
            if any(f.severity == level for f in self.findings):
                return level  # type: ignore[return-value]
        return "info"

    def by_severity(self) -> list[Finding]:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        return sorted(self.findings, key=lambda f: order.get(f.severity, 9))
