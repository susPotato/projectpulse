"""Schedule reasoning: the dependency DAG, and what a slip does to it.

Split in two on purpose. `graph.py` turns rows into a NetworkX DAG and answers
*what is connected to what*; `impact.py` answers *what does that cost*. The causal
engine needs only the first, so it does not pay for the second.

Nothing in this package may import the ML duration classifier. It returns a
bucket, never a number, and every date here is arithmetic on dates a human wrote.
"""

from app.intelligence.schedule.graph import (
    DependencyLinks,
    EdgeRecord,
    ScheduleGraph,
    TaskNode,
    build_graph,
)
from app.intelligence.schedule.impact import (
    ImpactReport,
    TaskProjection,
    project_schedule,
)

__all__ = [
    "DependencyLinks",
    "EdgeRecord",
    "ImpactReport",
    "ScheduleGraph",
    "TaskNode",
    "TaskProjection",
    "build_graph",
    "project_schedule",
]
