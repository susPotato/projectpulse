"""The rules, in a form both ZEN and a plain Python loop can evaluate.

Why not write ZEN's JDM JSON directly? Because then ZEN would be load-bearing: a
wheel that fails to build on a competition machine would take the whole rule layer
with it. Rules declared as data compile *to* JDM for ZEN and are also directly
interpretable by `engine.py`'s built-in evaluator, so the dependency is a
performance and editability choice rather than a single point of failure.

The second reason matters more in the long run. A rule here is a row a delivery
manager can read and argue with:

    qa_blocked_ratio > 0.6  ->  "QA queue has stalled", high

That is reviewable by the person whose judgement it encodes. It stops being
reviewable the moment a rule contains a graph traversal, which is exactly why
`context.py` does all aggregation first.

**Headlines contain `{{tokens}}`, never digits.** Every number a finding states is
substituted by `assembler.py` from the same context record the rule fired on, so a
number can never drift from the data that produced it. A token naming a field that
does not exist is caught by `validate_table`, not discovered by a PM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

TOKEN = re.compile(r"\{\{(\w+)\}\}")

SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")

#: Comparison operators a rule row may use. Deliberately small - anything needing
#: more expressiveness belongs in `context.py` as a named scalar.
OPERATORS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    value: Any

    def __post_init__(self) -> None:
        if self.op not in OPERATORS:
            raise ValueError(f"unknown operator {self.op!r}; expected {sorted(OPERATORS)}")

    def holds(self, record: dict) -> bool:
        if self.field not in record:
            # A rule referring to a field the context does not produce is a bug in
            # the table, not a rule that quietly never fires.
            raise KeyError(f"rule references unknown context field {self.field!r}")
        return OPERATORS[self.op](record[self.field], self.value)

    def describe(self, record: dict | None = None) -> str:
        actual = f" (was {record[self.field]!r})" if record else ""
        return f"{self.field} {self.op} {self.value!r}{actual}"


@dataclass(frozen=True)
class Rule:
    """One row of the decision table."""

    id: str
    #: All conditions must hold. An OR is two rules, which is easier to read and
    #: far easier to explain when a PM asks why something fired.
    when: tuple[Condition, ...]
    category: str
    severity: str
    #: Prose with `{{tokens}}`. No digits.
    headline: str
    recommendation: str
    #: Why this threshold. Shown in the rule trace so a PM can challenge it.
    rationale: str

    def tokens(self) -> set[str]:
        return set(TOKEN.findall(self.headline)) | set(TOKEN.findall(self.recommendation))

    def holds(self, record: dict) -> bool:
        return all(condition.holds(record) for condition in self.when)


@dataclass(frozen=True)
class RuleTable:
    name: str
    rules: tuple[Rule, ...] = field(default=())

    def __iter__(self):
        return iter(self.rules)

    def by_id(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)


# --------------------------------------------------------------------------
# The default table.
# --------------------------------------------------------------------------

DEFAULT_TABLE = RuleTable(
    name="delivery_risk",
    rules=(
        Rule(
            id="schedule_inconsistent_major",
            when=(Condition("max_propagated_days", ">=", 5),),
            category="schedule_risk",
            severity="high",
            headline=(
                "The plan cannot hold: {{tasks_inconsistent}} task(s) are dated "
                "earlier than their own dependencies allow, by up to "
                "{{max_propagated_days}} days."
            ),
            recommendation=(
                "Re-baseline the affected tasks or compress the driving path "
                "before the next steering review."
            ),
            rationale=(
                "Five days is a working week - past that the inconsistency will "
                "surface as a missed commitment rather than a schedule edit."
            ),
        ),
        Rule(
            id="schedule_inconsistent_minor",
            when=(
                Condition("max_propagated_days", ">=", 1),
                Condition("max_propagated_days", "<", 5),
            ),
            category="schedule_risk",
            severity="medium",
            headline=(
                "{{tasks_inconsistent}} task(s) are dated slightly ahead of their "
                "dependencies, by up to {{max_propagated_days}} days."
            ),
            recommendation="Confirm the dates with the owners before it compounds.",
            rationale=(
                "Under a week is usually absorbed, but it is the earliest point "
                "the data can show the drift at all."
            ),
        ),
        Rule(
            id="milestone_at_risk",
            when=(Condition("milestones_at_risk", ">=", 1),),
            category="milestone_risk",
            severity="high",
            headline=(
                "{{milestones_at_risk}} milestone(s) sit behind work that cannot "
                "finish on its planned date."
            ),
            recommendation=(
                "Raise the milestone date now, or move scope out of it - the "
                "current date is not supported by the schedule underneath it."
            ),
            rationale=(
                "A milestone is a commitment to someone outside the team, so it "
                "is escalated a level above an ordinary task slip."
            ),
        ),
        Rule(
            id="qa_queue_stalled",
            when=(
                Condition("qa_blocked_ratio", ">", 0.6),
                Condition("qa_count", ">=", 5),
            ),
            category="quality_risk",
            severity="high",
            headline=(
                "QA has stalled: {{qa_blocked}} of {{qa_count}} test items are "
                "blocked."
            ),
            recommendation=(
                "Clear the blocking dependency before adding more test scope - "
                "additional cases will queue behind the same cause."
            ),
            rationale=(
                "Above roughly two thirds blocked, the queue is no longer "
                "absorbing work and testing has effectively stopped."
            ),
        ),
        Rule(
            id="qa_queue_building",
            when=(
                Condition("qa_blocked_ratio", ">", 0.3),
                Condition("qa_blocked_ratio", "<=", 0.6),
                Condition("qa_count", ">=", 5),
            ),
            category="quality_risk",
            severity="medium",
            headline=(
                "A QA backlog is forming: {{qa_blocked}} of {{qa_count}} items "
                "are blocked."
            ),
            recommendation="Identify the shared blocker while the queue is small.",
            rationale=(
                "A third blocked is where a queue stops being individual "
                "problems and starts being one."
            ),
        ),
        Rule(
            id="root_cause_available",
            when=(Condition("chains_dependency_backed", ">=", 1),),
            category="root_cause",
            severity="info",
            headline=(
                "{{chains_dependency_backed}} of {{chain_count}} finding(s) trace "
                "to a stated dependency, not just a coincidence in timing."
            ),
            recommendation="Open the evidence panel to see the source rows.",
            rationale=(
                "Surfaced as information rather than risk: it describes how well "
                "evidenced the other findings are, not a problem of its own."
            ),
        ),
        Rule(
            id="conclusion_rests_on_inferred_edges",
            when=(Condition("depends_on_inferred_edges", "==", True),),
            category="evidence_quality",
            severity="medium",
            headline=(
                "The schedule conclusion changes if inferred dependencies are "
                "removed - {{edges_inferred}} of {{edges_total}} edges were "
                "derived from dates rather than stated by a person."
            ),
            recommendation=(
                "Add the missing predecessors to the schedule sheet to convert "
                "this into a stated conclusion."
            ),
            rationale=(
                "An inferred edge is a reasonable guess, not a fact. A date "
                "presented to a steering committee should say which it rests on."
            ),
        ),
        Rule(
            id="identity_confidence_low",
            when=(Condition("low_confidence_ratio", ">", 0.2),),
            category="data_quality",
            severity="medium",
            headline=(
                "{{changes_low_confidence}} of {{changes_total}} observed changes "
                "came from rows matched by title rather than by ID, and are "
                "excluded from root-cause analysis."
            ),
            recommendation=(
                "Give every row a Task ID - untitled rows cannot be tracked "
                "across scans and their history is not trustworthy."
            ),
            rationale=(
                "The analysis silently ran on a subset. A PM is entitled to know "
                "how much of their sheet was usable."
            ),
        ),
        Rule(
            id="rows_rejected",
            when=(Condition("rows_rejected", ">=", 1),),
            category="data_quality",
            severity="low",
            headline="{{rows_rejected}} row(s) could not be read and were quarantined.",
            recommendation="Review the rejected rows - each names its own reason.",
            rationale=(
                "Silent partial data is how a PM ends up trusting a health score "
                "computed on 60% of their project."
            ),
        ),
        Rule(
            id="baseline_missing",
            when=(
                Condition("baseline_coverage", "<", 0.5),
                Condition("task_count", ">=", 3),
            ),
            category="data_quality",
            severity="medium",
            headline=(
                "Only {{tasks_with_baseline}} of {{task_count}} tasks have a "
                "baseline date, so schedule variance cannot be measured for the rest."
            ),
            recommendation="Record baseline dates when the plan is agreed.",
            rationale=(
                "Variance is meaningless without a commitment to compare "
                "against, and it is the first thing a hand-maintained sheet loses."
            ),
        ),
    ),
)


def validate_table(table: RuleTable, known_fields: set[str]) -> list[str]:
    """Problems that would make a rule silently wrong. Empty list means healthy.

    Called at engine construction, so a mistyped field name fails at startup
    rather than producing a rule that never fires or a headline with a literal
    ``{{typo}}`` in it where a PM can see it.
    """
    problems: list[str] = []
    seen: set[str] = set()

    for rule in table:
        if rule.id in seen:
            problems.append(f"duplicate rule id {rule.id!r}")
        seen.add(rule.id)

        if rule.severity not in SEVERITY_ORDER:
            problems.append(
                f"{rule.id}: unknown severity {rule.severity!r}; "
                f"expected one of {SEVERITY_ORDER}"
            )

        for condition in rule.when:
            if condition.field not in known_fields:
                problems.append(
                    f"{rule.id}: condition on unknown context field "
                    f"{condition.field!r}"
                )

        for token in rule.tokens():
            if token not in known_fields:
                problems.append(
                    f"{rule.id}: headline token {{{{{token}}}}} has no matching "
                    "context field, so it could never be substituted"
                )

        if re.search(r"\d", TOKEN.sub("", rule.headline)):
            problems.append(
                f"{rule.id}: headline contains a literal digit; every number must "
                "be a {{token}} so it cannot drift from the data"
            )

    return problems
