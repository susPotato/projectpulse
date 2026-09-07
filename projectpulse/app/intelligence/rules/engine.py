"""Evaluate the rule table. The only module in the system that imports ZEN.

That isolation is the point. ZEN is a Rust wheel, and a wheel that fails to build
on the machine we need on the day would otherwise take the rule layer with it -
so this module carries a complete built-in evaluator and falls back to it
automatically. `backend_name` reports which ran, because a silent fallback that
nobody notices is its own kind of failure.

**Both backends must agree.** They are checked against each other in
`tests/test_rules.py` over the same records. ZEN is the faster and editable path;
the Python evaluator is the definition of correct.

**The trace is built in Python either way.** It is not debug output - a PM being
told their milestone is at risk is entitled to see the rule id, the thresholds it
compared, and the actual values it compared them against. Deriving it from the
same `Condition` objects both backends share keeps the explanation identical
whichever one ran.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from app.intelligence.rules.tables import (
    DEFAULT_TABLE,
    Rule,
    RuleTable,
    validate_table,
)

log = logging.getLogger(__name__)

Backend = Literal["auto", "zen", "python"]


@dataclass(frozen=True)
class RuleHit:
    """One rule that fired, and why.

    `headline` and `recommendation` still contain `{{tokens}}`. Substitution is
    `assembler.py`'s job and happens exactly once, so a number can never be
    formatted two different ways in two different places.
    """

    rule_id: str
    category: str
    severity: str
    headline: str
    recommendation: str
    rationale: str
    #: Each condition as evaluated, with the value it saw. The rule trace.
    trace: tuple[str, ...] = field(default=())


class RuleTableError(RuntimeError):
    """The table itself is wrong - raised at construction, never at evaluation."""


class RulesEngine:
    """Compiled rule table, ready to evaluate flat context records."""

    def __init__(
        self,
        table: RuleTable = DEFAULT_TABLE,
        *,
        known_fields: set[str] | None = None,
        backend: Backend = "auto",
    ) -> None:
        self.table = table
        self._decision = None
        self._backend = "python"

        if known_fields is not None:
            problems = validate_table(table, known_fields)
            if problems:
                raise RuleTableError(
                    "rule table is not valid:\n  " + "\n  ".join(problems)
                )

        if backend in ("auto", "zen"):
            self._decision = self._compile_zen()
            if self._decision is not None:
                self._backend = "zen"
            elif backend == "zen":
                raise RuleTableError("ZEN backend requested but unavailable")

    @property
    def backend_name(self) -> str:
        return self._backend

    # -- ZEN ---------------------------------------------------------------

    def _compile_zen(self):
        try:
            import zen
        except Exception as exc:  # pragma: no cover - environment dependent
            log.info("ZEN unavailable (%s); using the built-in evaluator", exc)
            return None

        try:
            engine = zen.ZenEngine()
            return engine.create_decision(json.dumps(to_jdm(self.table)))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("ZEN table failed to compile (%s); falling back", exc)
            return None

    def _fired_zen(self, record: dict) -> list[str]:
        result = self._decision.evaluate(record)
        rows = result.get("result") or []
        if isinstance(rows, dict):
            rows = [rows]
        return [row["rule_id"] for row in rows if row.get("rule_id")]

    # -- Built-in ----------------------------------------------------------

    def _fired_python(self, record: dict) -> list[str]:
        return [rule.id for rule in self.table if rule.holds(record)]

    # -- Public ------------------------------------------------------------

    def evaluate(self, record: dict) -> list[RuleHit]:
        """Every rule that fires, in table order."""
        if self._decision is not None:
            fired = self._fired_zen(record)
        else:
            fired = self._fired_python(record)

        hits: list[RuleHit] = []
        for rule_id in fired:
            rule = self.table.by_id(rule_id)
            if rule is None:  # pragma: no cover - only if a table is swapped mid-run
                continue
            hits.append(
                RuleHit(
                    rule_id=rule.id,
                    category=rule.category,
                    severity=rule.severity,
                    headline=rule.headline,
                    recommendation=rule.recommendation,
                    rationale=rule.rationale,
                    trace=_trace(rule, record),
                )
            )
        return hits


def _trace(rule: Rule, record: dict) -> tuple[str, ...]:
    """The conditions that fired, each showing the value it actually saw."""
    return tuple(condition.describe(record) for condition in rule.when)


# --------------------------------------------------------------------------
# JDM compilation
# --------------------------------------------------------------------------


def _cell(op: str, value: Any) -> str:
    """One decision-table cell: a unary test against the input field."""
    if isinstance(value, bool):
        # Booleans have to be compared explicitly; a bare `true` is read as a
        # value, not a test.
        return f"{op} {'true' if value else 'false'}"
    if isinstance(value, str):
        return f'{op} "{value}"'
    return f"{op} {value}"


def to_jdm(table: RuleTable) -> dict:
    """Compile the neutral table to ZEN's JSON Decision Model.

    One input column per distinct field. A rule that does not constrain a field
    leaves that cell empty, which ZEN reads as "always true" - so each row states
    only what it actually cares about.
    """
    fields: list[str] = []
    for rule in table:
        for condition in rule.when:
            if condition.field not in fields:
                fields.append(condition.field)

    inputs = [
        {"id": f"i_{name}", "field": name, "name": name} for name in fields
    ]

    rows = []
    for rule in table:
        # Several conditions on one field (a band, like 1 <= x < 5) cannot share a
        # cell, so they are joined with `and` inside it.
        by_field: dict[str, list[str]] = {}
        for condition in rule.when:
            by_field.setdefault(condition.field, []).append(
                _cell(condition.op, condition.value)
            )

        row: dict[str, str] = {"_id": rule.id, "o_rule_id": f'"{rule.id}"'}
        for name in fields:
            cells = by_field.get(name)
            row[f"i_{name}"] = " and ".join(cells) if cells else ""
        rows.append(row)

    return {
        "nodes": [
            {
                "id": "input",
                "type": "inputNode",
                "name": "context",
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "table",
                "type": "decisionTableNode",
                "name": table.name,
                "position": {"x": 200, "y": 0},
                "content": {
                    # Collect: a project can be in trouble several ways at once,
                    # and reporting only the first would hide the rest.
                    "hitPolicy": "collect",
                    "inputs": inputs,
                    "outputs": [
                        {"id": "o_rule_id", "field": "rule_id", "name": "rule_id"}
                    ],
                    "rules": rows,
                },
            },
            {
                "id": "output",
                "type": "outputNode",
                "name": "findings",
                "position": {"x": 400, "y": 0},
            },
        ],
        "edges": [
            {"id": "e1", "sourceId": "input", "targetId": "table", "type": "edge"},
            {"id": "e2", "sourceId": "table", "targetId": "output", "type": "edge"},
        ],
    }


def evaluate(record: dict, table: RuleTable = DEFAULT_TABLE) -> list[RuleHit]:
    """Convenience for one-off evaluation. Prefer reusing a `RulesEngine`."""
    return RulesEngine(table).evaluate(record)
