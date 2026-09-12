"""Program-level configuration, read-only.

The design's Program settings, and the honest version of the spec's
"rule-table editor": the cut list already allows showing the tables read-only,
and showing them is most of the value. A delivery manager who can *read* the
thresholds can challenge them, which is the whole point of the rule trace.

Read-only is a decision, not a stub. A rule table edited in a browser is a rule
table with no review and no history, and every finding in the product is
defended by pointing at these thresholds.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.api.schemas.base import Response


class WatchedSource(Response):
    """One sheet or payload folder the retriever looks at."""

    kind: str
    #: `file.xlsx#Sheet` - the scope key the differ bounds changes against.
    scope: str
    path: str
    exists: bool = False
    last_scan: datetime | None = None
    rows: int = 0
    project_id: str = ""


class ScopeEntry(Response):
    """Which source ids are one delivery project (invariant 7), and its program.

    The program is carried because it is now *declared* in `app/scope.py` rather
    than derived from whichever collector wrote the row - so this screen is where
    a reader checks the declaration. Empty means the project belongs to no
    program, which is a legitimate state for one registered by upload.
    """

    canonical_id: str
    name: str
    also: list[str] = Field(default_factory=list)
    program_id: str = ""
    program_name: str = ""


class RuleRow(Response):
    """One row of the decision table, as a reader would challenge it."""

    id: str
    category: str
    severity: str
    #: `max_propagated_days >= 5`, in the order they must all hold.
    conditions: list[str] = Field(default_factory=list)
    #: Prose with `{{tokens}}` still in it - deliberately unsubstituted here,
    #: because this screen is about the rule and not about today's numbers.
    headline: str = ""
    recommendation: str = ""
    #: Why this threshold. The sentence a PM argues with.
    rationale: str = ""


class ProgramBundle(Response):
    program_name: str = ""
    data_root: str = ""
    #: Where the retriever reads from. Absent files are a note, not a failure.
    sources: list[WatchedSource] = Field(default_factory=list)
    scope: list[ScopeEntry] = Field(default_factory=list)
    rules: list[RuleRow] = Field(default_factory=list)
    rule_table: str = ""
