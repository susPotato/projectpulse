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
        # -- Snapshot rules -------------------------------------------------
        #
        # For a project whose source carries no baseline, no dependency edges
        # and no effort - a Jira export being the case these exist for. Every
        # condition below reads a count over the rows as they stand, so none
        # needs a second observation to fire.
        #
        # They earn their place because the alternative is silence. A project
        # ingested from Jira alone trips none of the schedule rules above: with
        # no edges there is no chain, so `max_propagated_days` is 0 and the page
        # reports nothing at all - absence of data rendered as absence of risk,
        # which is the one thing this product exists not to do.
        Rule(
            id="status_not_maintained",
            # Half the work claiming to be underway with none of it finished is
            # a statement about the *board*, not about the work.
            when=(
                Condition("tasks_in_progress", ">=", 5),
                Condition("tasks_done", "==", 0),
            ),
            category="data_quality",
            severity="medium",
            headline=(
                "{{tasks_in_progress}} of {{task_count}} task(s) report in "
                "progress and none is complete."
            ),
            recommendation=(
                "Check the board is being kept up to date before reading "
                "anything else here - every other figure on this page is "
                "computed from these statuses."
            ),
            rationale=(
                "A team cannot genuinely have this much in flight at once with "
                "nothing finished. The likeliest explanation is that status is "
                "set when work is picked up and not moved again, which makes "
                "'in progress' mean 'not started' - and that is worth knowing "
                "before trusting a completion figure derived from it."
            ),
        ),
        Rule(
            id="tasks_overdue",
            when=(Condition("tasks_overdue", ">=", 1),),
            category="schedule_risk",
            # `medium`, not `high`, and the reason is worth keeping.
            #
            # `as_of` is the latest change we observed, and it falls back to the
            # wall clock for a project nothing has ever been observed to change
            # - which is every project on its first upload. So "past due" can be
            # measuring the age of the document rather than the health of the
            # work: the demo sheets describe March, and read in September every
            # one of their tasks is trivially overdue.
            #
            # The finding is still true and still worth making - those tasks
            # were due and are not done - but `high` bands the project critical
            # on its own, and a whole portfolio going red because a spreadsheet
            # is old is exactly the false alarm that teaches people to ignore
            # the colour. Whether overdue is a crisis depends on what those
            # tasks are, which this rule cannot know, so it flags rather than
            # judges.
            severity="medium",
            headline=(
                "{{tasks_overdue}} task(s) are past their own due date and not "
                "marked done."
            ),
            recommendation=(
                "Re-date them or close them. A due date in the past is either a "
                "slip nobody has recorded or a task nobody has closed, and the "
                "two need different actions."
            ),
            rationale=(
                "Measured against the scan this analysis reflects, not against "
                "today, and it needs no dependency graph - which is what makes "
                "it the one schedule claim available for a source that carries "
                "no baseline and no edges."
            ),
        ),
        Rule(
            id="due_soon_none_finished",
            when=(
                Condition("tasks_due_soon", ">=", 3),
                Condition("tasks_done", "==", 0),
            ),
            category="schedule_risk",
            severity="medium",
            headline=(
                "{{tasks_due_soon}} task(s) fall due within a fortnight and "
                "nothing has been completed yet."
            ),
            recommendation=(
                "Confirm which of them will actually land, and move the rest "
                "now rather than on the day."
            ),
            rationale=(
                "A fortnight is the horizon a PM can still act inside - long "
                "enough to move a person or cut scope. With no completion to "
                "date behind it, a cluster of dates in that window is a "
                "forecast nobody has evidence for."
            ),
        ),
        Rule(
            id="work_not_moving",
            when=(
                Condition("tasks_stale", ">=", 3),
                Condition("tasks_done", "==", 0),
            ),
            category="evidence_quality",
            severity="medium",
            headline=(
                "{{tasks_stale}} open task(s) have not been touched in the "
                "source system for a week or more - the stalest for "
                "{{stalest_task_days}} days."
            ),
            recommendation=(
                "Find out whether the work stopped or only the updating did. "
                "Both are worth knowing and they need different conversations."
            ),
            rationale=(
                "Read from the tracker's own 'last updated', which is testimony "
                "rather than evidence - we did not watch it happen. It is still "
                "the only movement signal a single export carries, because there "
                "is no earlier scan to diff against. Paired with nothing "
                "completed, a stalled board is the likeliest reading."
            ),
        ),
        Rule(
            id="deadline_cluster",
            when=(Condition("tasks_on_busiest_due_date", ">=", 4),),
            category="schedule_risk",
            severity="medium",
            headline=(
                "{{tasks_on_busiest_due_date}} open task(s) share a single due "
                "date ({{busiest_due_date}})."
            ),
            recommendation=(
                "Sequence them or move the ones that do not have to land that "
                "day. Whatever happens to that date happens to all of them at "
                "once."
            ),
            rationale=(
                "Needs no baseline and no dependency graph, which is what makes "
                "it available for a source that carries neither. A pile-up on "
                "one day is usually a deadline everybody was handed rather than "
                "a sequence anybody worked out - and it concentrates risk: the "
                "date cannot slip a little, it can only slip for everything."
            ),
        ),
        Rule(
            id="single_owner_project",
            when=(
                Condition("distinct_owners", "==", 1),
                Condition("task_count", ">=", 5),
            ),
            category="resource_risk",
            severity="medium",
            headline=(
                "All {{task_count}} task(s) are assigned to one person "
                "({{distinct_owners}} distinct owner)."
            ),
            recommendation=(
                "Confirm this is real rather than an unassigned backlog filed "
                "under whoever raised it - the two look identical here and only "
                "one of them is a delivery risk."
            ),
            rationale=(
                "Stated so it is not mistaken for a clean bill of health. "
                "Cross-project contention is computed across projects sharing a "
                "person, so a single-owner project contributes nothing to a "
                "program rollup: its resource band is unknown, not healthy."
            ),
        ),
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
        # -- Cross-project contention (Channel 1) -------------------------
        #
        # Every rule here requires `has_program_context`. A project analysed
        # outside its program has *unknown* contention, and a rule that fired
        # on a zero would be asserting "no contention" from an absence of
        # evidence - which is the confident-zero failure mode, applied to the
        # one output the cross-project model exists to produce.
        #
        # The magnitudes are stated in effort-days, never in delay. Delay is a
        # scenario that depends on how the organisation absorbs overload, and
        # the absorption assumption travels with it (see `contention.py`).
        Rule(
            id="contention_breaches_overtime",
            when=(
                Condition("has_program_context", "==", True),
                Condition("contention_breaches_overtime_limit", "==", True),
            ),
            category="resource_risk",
            severity="critical",
            headline=(
                "This project is short {{contention_pressure_days}} effort-days "
                "of shared people, and holding the date needs one of them at "
                "{{contention_overtime_hours}} overtime hours in a single month - "
                "past the {{contention_overtime_limit_hours}}-hour monthly "
                "三六協定 ceiling."
            ),
            recommendation=(
                "Re-sequence the shared work or add capacity in the role. This "
                "one cannot be absorbed by asking the team to work harder: the "
                "ceiling is statutory and carries penalties."
            ),
            rationale=(
                "Overload is absorbed by overtime long before a date visibly "
                "moves, so the honest question is not whether the plan slips but "
                "whether the hours it needs are legal. In Japan that is a hard, "
                "checkable number rather than a cultural expectation."
            ),
        ),
        Rule(
            id="contention_material",
            when=(
                Condition("has_program_context", "==", True),
                Condition("contention_pressure_days", ">=", 3),
                Condition("contention_breaches_overtime_limit", "==", False),
            ),
            category="resource_risk",
            severity="high",
            headline=(
                "{{contention_people}} person(s) are committed to this project and "
                "to others beyond their capacity: {{contention_pressure_days}} "
                "effort-days of this project's work has no one to do it."
            ),
            recommendation=(
                "Agree with the other project's manager which work moves. Left "
                "alone the resolution happens by whoever asks loudest, not by "
                "which project can least afford to wait."
            ),
            rationale=(
                "The shortfall is this project's apportioned share of the excess "
                "demand, not the whole of it - so it can be added to the other "
                "projects' shares without double-counting the same overload."
            ),
        ),
        Rule(
            id="contention_minor",
            when=(
                Condition("has_program_context", "==", True),
                Condition("contention_pressure_days", ">", 0),
                Condition("contention_pressure_days", "<", 3),
            ),
            category="resource_risk",
            severity="low",
            headline=(
                "{{contention_pressure_days}} effort-days of shared-resource "
                "pressure across {{contention_people}} person(s)."
            ),
            recommendation=(
                "Worth knowing, not worth escalating - watch it if the window "
                "tightens."
            ),
            rationale=(
                "Under a few effort-days is inside the noise of a resource plan "
                "written in whole percentages."
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
