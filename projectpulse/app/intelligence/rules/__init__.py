"""The rule layer: flat scalars in, structured findings out.

`tables.py` holds the rules in a neutral form; `engine.py` is the only module that
knows ZEN exists. That separation is deliberate insurance - if the ZEN wheel ever
fails on a machine we need, swapping to the built-in evaluator is a one-line
change rather than a rewrite of every rule.
"""

from app.intelligence.rules.engine import (
    RuleHit,
    RulesEngine,
    evaluate,
)
from app.intelligence.rules.tables import (
    DEFAULT_TABLE,
    Condition,
    Rule,
    RuleTable,
)

__all__ = [
    "Condition",
    "DEFAULT_TABLE",
    "Rule",
    "RuleHit",
    "RuleTable",
    "RulesEngine",
    "evaluate",
]
