"""Which source ids are one delivery project.

Invariant 7: Jira and Excel each create their own `projects` row for the same
piece of delivery, so analysing either alone throws away every cross-source
claim. `analyze_project(..., also=[...])` re-points them at one canonical id -
and until now the pairing was a literal `also = ["jira:Project:1:HRMS"]` sitting
in the API route, which meant the portfolio view had no way to know that two
rows were one project and would have listed HRMS twice.

This is that pairing, in one place. A real deployment reads it from scope config
alongside the watched sheets; a constant is the honest interim, and having it
here rather than in a route is what lets more than one caller agree about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeliveryProject:
    """One project as a delivery manager thinks of it, whatever fed it."""

    #: The id every other source id is re-pointed at.
    canonical_id: str
    name: str
    #: The same delivery project as other source systems call it.
    also: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return (self.canonical_id, *self.also)


#: The demo portfolio. One project, two sources - which is the case the
#: precision model exists for, so it is the right default to ship.
PORTFOLIO: tuple[DeliveryProject, ...] = (
    DeliveryProject(
        canonical_id="excel:Project:1:HRMS",
        name="HRMS Platform",
        also=("jira:Project:1:HRMS",),
    ),
)


def find(canonical_id: str) -> DeliveryProject | None:
    return next((p for p in PORTFOLIO if p.canonical_id == canonical_id), None)


def also_for(canonical_id: str) -> list[str]:
    """The other source ids for this project, for `analyze_project(also=...)`.

    An unknown id gets an empty list rather than an error: a caller asking
    about a project nobody has paired is asking a legitimate question, and the
    answer is "just this one source".
    """
    project = find(canonical_id)
    return list(project.also) if project else []
