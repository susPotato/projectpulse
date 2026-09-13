"""What the Agent tab is told about the project being asked about.

The chat used to be told the opposite: its system prompt said "you have not
been given this project's live data", and every answer that touched a number
had to deflect to another page. That made it a general-purpose chatbot sitting
next to a product full of facts - the one surface where somebody would
reasonably ask "what's overdue?" and the one surface that could not say.

So this assembles a brief. Three rules shape what goes in it, and they are the
whole design:

**Only figures this app already computed.** Every number here comes from
`analyze_project`'s context or from the task rows themselves - the same values
`/api/insight` serves. The model is quoting, never deriving. That is the
difference between an answer that agrees with the Insight page and a second
opinion that quietly disagrees with it.

**What is missing is stated as loudly as what is present.** The limits section
is not a footnote: an export with no dependency edges cannot support a question
about the critical path, and a model that has been handed 190 tasks and no
edges will happily invent one unless it is told, in the brief, that there are
none. Absence has to be a fact in the context, not a gap in it.

**Bounded, and truncated visibly.** A project with 190 tasks does not get 190
rows - it gets the ones a question is actually about (overdue, due soon, the
dated ones) and a count of the rest, marked as truncated so the model can say
"there are more" instead of reasoning as though the list were complete.

Not a replacement for the Insight page and the prompt says so. It is what lets
the chat answer "which four are late" with the four rather than with directions
to another tab.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app import scope
from app.models.domain import Milestone, Project, Risk, Task

#: How many rows of any one list to name. Past this the brief says how many
#: more there are rather than listing them - a transcript is not a table, and a
#: model given 150 titles summarises them instead of answering the question.
MAX_ROWS = 25

#: Statuses that mean the work is not outstanding. Matches
#: `intelligence/context.py`'s own reading rather than restating it loosely -
#: a brief that called a DROPPED task open would have the chat contradict the
#: findings on the same screen.
CLOSED = {"DONE", "DROPPED"}


#: How much of one title to print. A Jira summary is usually a line, but this
#: export has one carrying a whole paragraph of acceptance criteria - unbounded,
#: a single row would crowd out the other twenty-four and the model would answer
#: about whichever task happened to be verbose.
MAX_TITLE = 90


def _line(task: Task) -> str:
    """One task, as the brief names it: the key a person can search for."""
    key = task.id.rsplit(":", 1)[-1]
    title = " ".join((task.title or "(untitled)").split())
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE].rstrip() + " [...]"
    bits = [f"{key} | {title}"]
    if task.original_status or task.status:
        bits.append(f"status={task.original_status or task.status}")
    if task.start_date:
        bits.append(f"start={task.start_date.isoformat()}")
    if task.due_date:
        bits.append(f"due={task.due_date.isoformat()}")
    if task.assignee:
        bits.append(f"owner={task.assignee}")
    return "  - " + "  ".join(bits)


def _section(title: str, rows: list[str], total: int | None = None) -> list[str]:
    """One block, with an honest tail when it was cut short."""
    if not rows:
        return []
    out = [f"{title}:", *rows]
    if total is not None and total > len(rows):
        out.append(f"  ... and {total - len(rows)} more not listed here.")
    return out


def build_brief(session, project_id: str, as_of: date | None = None) -> str | None:
    """Everything the model is told about one project, or `None` if unknown.

    `None` rather than an empty string so the route can tell "no project was
    selected" from "this project has nothing in it" - the first is a chat with
    no grounding and the second is a fact worth saying.
    """
    project = scope.resolve(project_id)
    if project is None:
        return None
    ids = scope.source_ids_for(project.canonical_id)

    row = session.get(Project, project.canonical_id)
    name = (row.name if row else None) or project.name or project.canonical_id

    tasks = list(session.scalars(select(Task).where(Task.project_id.in_(ids))))
    if not tasks:
        return (
            f"PROJECT: {name} ({project.canonical_id})\n"
            "This project has no tasks ingested. Nothing has been uploaded for "
            "it, or the upload was rejected. There is no data to answer "
            "questions about."
        )

    today = as_of or date.today()
    open_tasks = [t for t in tasks if (t.status or "") not in CLOSED]

    by_status: dict[str, int] = {}
    for task in tasks:
        label = task.original_status or task.status or "unknown"
        by_status[label] = by_status.get(label, 0) + 1

    overdue = sorted(
        (t for t in open_tasks if t.due_date and t.due_date < today),
        key=lambda t: t.due_date,
    )
    soon = sorted(
        (t for t in open_tasks if t.due_date and t.due_date >= today),
        key=lambda t: t.due_date,
    )
    undated = [t for t in tasks if not t.due_date and not t.start_date]

    owners = sorted({t.assignee for t in tasks if t.assignee})
    milestones = list(session.scalars(select(Milestone).where(Milestone.project_id.in_(ids))))
    risks = list(session.scalars(select(Risk).where(Risk.project_id.in_(ids))))
    drafts = [r for r in risks if r.status == "Draft"]

    lines: list[str] = [
        f"PROJECT: {name} ({project.canonical_id})",
        f"As at {today.isoformat()}. {len(tasks)} task(s) ingested.",
        "",
        "STATUS BREAKDOWN (the source system's own words):",
        *(f"  - {k}: {v}" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])),
        "",
        f"Open: {len(open_tasks)}.  Past due: {len(overdue)}.  "
        f"Dated but not yet due: {len(soon)}.  No date at all: {len(undated)}.",
        f"Distinct owners: {len(owners)}"
        + (f" ({', '.join(owners[:8])})" if owners else ""),
        "",
    ]

    lines += _section(
        "PAST DUE (open, due date already passed)",
        [_line(t) for t in overdue[:MAX_ROWS]],
        len(overdue),
    )
    lines += [""] if overdue else []
    lines += _section(
        "DATED AND STILL AHEAD",
        [_line(t) for t in soon[:MAX_ROWS]],
        len(soon),
    )
    lines += [""] if soon else []

    if milestones:
        lines += _section(
            "MILESTONES / GROUPING BANDS",
            [
                f"  - {m.name}"
                + (f"  planned={m.planned_date.isoformat()}" if m.planned_date else "")
                for m in milestones[:MAX_ROWS]
            ],
            len(milestones),
        )
        lines.append("")

    lines.append(
        f"RISK REGISTER: {len(risks) - len(drafts)} accepted risk(s), "
        f"{len(drafts)} model-proposed draft(s) awaiting a decision."
    )
    lines.append("")

    #: The limits, and they are not a footnote - see the module docstring. A
    #: model handed 190 tasks and no edges invents a critical path unless the
    #: absence is itself stated.
    limits = [
        "WHAT THIS PROJECT'S DATA CANNOT SUPPORT - do not answer past these:",
        f"  - {len(undated)} of {len(tasks)} task(s) have no start and no due "
        "date. They cannot be placed on any timeline and no schedule question "
        "can be answered about them.",
    ]
    with_baseline = sum(1 for t in tasks if t.baseline_end)
    if not with_baseline:
        limits.append(
            "  - No task has a baseline date, so there is no recorded slip and "
            "no schedule variance. Any 'behind by N days' claim would be "
            "invented."
        )
    edges = _edge_count(session, ids)
    if not edges:
        limits.append(
            "  - No dependency edges at all. There is no critical path, no "
            "driving path and no propagated delay for this project; every "
            "task's projected finish equals its planned one. Do not describe "
            "a sequence or a blocker as if the data stated one. If the user "
            "asks, say the links are not in the export."
        )
    if len(owners) <= 1:
        limits.append(
            "  - One distinct owner across the whole project, so there is no "
            "workload distribution to analyse."
        )
    if not any(t.progress is not None for t in tasks):
        limits.append("  - No task carries a progress percentage.")
    lines += limits

    lines += [
        "",
        "HOW TO USE THIS: quote these figures, never recompute them. If a "
        "question needs something not listed above, say it is not in the data "
        "rather than estimating. The Insight, Schedule and Risk pages are "
        "computed from this same snapshot, so your answers must agree with "
        "them.",
    ]
    return "\n".join(lines)


def _edge_count(session, project_ids: list[str]) -> int:
    """How many dependency edges this project has.

    Its own function because the model lives in a different module and this is
    the one fact in the brief that is not on `Task` - and getting it wrong in
    the safe direction (reporting none when there are some) would tell the
    model to refuse questions it could have answered.
    """
    from app.models.domain import Dependency

    return (
        session.query(Dependency)
        .filter(Dependency.project_id.in_(project_ids))
        .count()
    )
