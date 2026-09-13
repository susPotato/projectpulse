"""A model reading issue text, and proposing risks a person then decides on.

The one place in this codebase where a model is allowed to say something that
was not computed. That is a real departure, so the terms are narrow and stated
here rather than spread across the call sites.

**It proposes; it never records.** Every row this writes carries
`status="Draft"` and `origin="ai_draft"`, and `list_risks` excludes both from
the register and from the 5x5 matrix. A draft is not a risk. It becomes one
when a person presses Accept, which is the same act as typing one - the
register stays a record of human judgement, which is the only thing that made
it trustworthy in the first place.

**Every proposal cites the rows it was read from, or it is discarded.** Not
softened, not shown with a caveat: dropped, in `_accept_proposal`. A claim
about a project that cannot point at a task is exactly the fabrication this
product exists not to produce, and there is no way to check one after the fact.
The citation is stored (`Risk.cited_task_ids`) so the Evidence tab can put the
task text next to the sentence and let a reader judge both.

**It reads text and nothing else.** The tasks handed over carry title, status
and description - no dates, no counts, no slip, no effort. Those are computed,
they are on the Insight page already, and a model asked to comment on them
would produce a second set of numbers that disagreed with the first. So the
prompt cannot restate a figure, because it was never given one.

**`app/intelligence/` must not import this module.** `Risk` already carries
that rule (see its docstring: a rule conditioning on an opinion is not
deterministic), and a model's opinion is no more admissible than a PM's. The
dependency runs one way - drafts read tasks, nothing reads drafts.

**Off unless asked for.** `settings.risk_drafts_enabled`, which is not
`narration_model_enabled`; see `app/config.py` for why they are separate.
Absent credentials, a missing SDK or a refusal all raise `DraftsUnavailable`,
and the route turns that into a reason on screen rather than an empty panel -
the same bargain `narrate` makes.
"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select

from app import scope
from app.models.domain import Risk, Task
from app.narration.client import NarrationUnavailable
from app.risks.matrix import IMPACTS, LIKELIHOODS

log = logging.getLogger(__name__)

#: What `status` a proposed risk carries until somebody accepts it. Chosen to
#: sit outside the register's own vocabulary ("Active", "Retired") so that a
#: query which forgets to filter shows an obviously foreign word rather than a
#: plausible one.
DRAFT_STATUS = "Draft"

#: The only value `Risk.origin` ever takes besides `None`.
ORIGIN_AI = "ai_draft"

#: How many tasks to send. Enough to see a pattern across a backlog, small
#: enough that the whole prompt stays reviewable by a person who wants to know
#: what was asked.
#:
#: Was 120, which timed out on the first real project it met: 120 tasks of
#: procedure text is ~26KB, and a reasoning model chewing that exceeded the
#: request timeout before writing a word. Sixty still spans a backlog - the
#: point is to see a pattern, and a pattern visible in 120 rows is visible in
#: 60 - and it roughly halves the time to first token.
MAX_TASKS = 60

#: How much of one description to send. Jira bodies run to procedures and
#: acceptance criteria; the first paragraphs carry the intent, and a limit per
#: task keeps one essay from crowding out a hundred other tasks.
#:
#: Lowered with `MAX_TASKS` and for the same reason. A risk that is only
#: visible in the fifth paragraph of an acceptance criterion is not one this
#: feature was ever going to find.
MAX_DESCRIPTION_CHARS = 400

#: How many proposals to keep. A model asked for risks will happily produce
#: thirty, and a register nobody can read is the same as an empty one.
MAX_DRAFTS = 8

SYSTEM = (
    "You read software project tasks and identify delivery risks that the "
    "task text itself reveals.\n\n"
    "Rules you must follow:\n"
    "- Only claim what the text supports. If the text is thin, say less.\n"
    "- Every risk must cite the id of at least one task you read it from.\n"
    "- Never state a number, date, percentage, count or duration. You have "
    "not been given any, and the ones on this project are computed elsewhere.\n"
    "- Do not restate what a task says. A risk is what could go wrong that "
    "the text implies and nobody has written down.\n"
    "- Prefer a few specific risks over many generic ones. 'Schedule may slip' "
    "is true of every project and worth nothing.\n\n"
    "Answer with JSON only: an array of objects with keys "
    '"title" (one sentence), "description" (two sentences at most, saying what '
    'could go wrong and why the text suggests it), "category" (one of the '
    'given categories), "likelihood", "impact", and "cites" (an array of task '
    "ids). No prose outside the JSON."
)

#: A fenced code block around the JSON, which several vendors add whatever the
#: prompt says. Stripped rather than treated as a parse failure: the content
#: inside is usually fine, and rejecting a good answer over its wrapper would
#: make the feature look broken.
FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class DraftsUnavailable(RuntimeError):
    """No proposals could be produced, with a reason fit to show a person."""


def _clean_text(value: str | None, limit: int) -> str:
    """One field of a task, flattened and bounded.

    Newlines go because the prompt is a line-per-field listing and a body with
    its own blank lines would look like the end of the task. Truncation is
    marked so the model can tell it is reading an excerpt, rather than
    concluding that a procedure has three steps because the fourth was cut.
    """
    if not value:
        return ""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit].rstrip() + " [...]"


def readable_tasks(session, project_ids: list[str]) -> list[Task]:
    """The tasks worth sending: the ones whose text says something.

    A task with no description is a title and a status, and a model handed
    nothing but titles writes generic risks - which is the failure mode this
    whole module is trying to avoid. So a description is required, and a
    project whose export carried none produces no drafts and an honest reason
    rather than a page of filler.
    """
    rows = session.scalars(
        select(Task)
        .where(Task.project_id.in_(project_ids))
        .order_by(Task.id)
    ).all()
    return [t for t in rows if _clean_text(t.description, MAX_DESCRIPTION_CHARS)]


def build_prompt(tasks: list[Task], categories: list[str]) -> str:
    """The user half of the request: the task text, and nothing else.

    Ids are the tasks' own row keys rather than positions in this list, because
    the model has to cite something that can be looked up afterwards - a
    citation of "task 4" is not checkable once the list is gone.
    """
    lines = [
        "Categories: " + ", ".join(categories),
        "Likelihood must be one of: " + ", ".join(LIKELIHOODS),
        "Impact must be one of: " + ", ".join(IMPACTS),
        "",
        "Tasks:",
    ]
    for task in tasks[:MAX_TASKS]:
        lines.append(f"- id: {_task_key(task)}")
        lines.append(f"  title: {_clean_text(task.title, 200)}")
        lines.append(f"  status: {task.original_status or task.status or 'unknown'}")
        lines.append(f"  text: {_clean_text(task.description, MAX_DESCRIPTION_CHARS)}")
    return "\n".join(lines)


def _task_key(task: Task) -> str:
    """What a citation names. The tail of the domain id - the `Task ID` the
    sheet carried - because that is the string a person can search for in their
    own tracker, and the full domain id is an internal shape nobody types."""
    return task.id.rsplit(":", 1)[-1]


def _parse(answer: str) -> list[dict]:
    """The model's JSON, or a refusal naming what came back instead."""
    text = (answer or "").strip()
    fenced = FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise DraftsUnavailable(
            f"the model did not answer with JSON ({exc})."
        ) from exc
    if isinstance(parsed, dict):
        # Some vendors wrap the array in an object whatever the prompt says.
        for key in ("risks", "items", "results", "data"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise DraftsUnavailable("the model's answer was not a list of risks.")
    return [p for p in parsed if isinstance(p, dict)]


def _one_of(value, allowed: tuple[str, ...]) -> str | None:
    """A vocabulary word, or nothing.

    Matched case-insensitively and otherwise dropped rather than coerced to a
    nearest neighbour. `pre_rating` is a lookup on this pair
    (`app/risks/matrix.py`), so a guessed likelihood does not stay a guess - it
    becomes a coloured badge on a heat-map, which reads as an assessment
    somebody made.
    """
    if not isinstance(value, str):
        return None
    wanted = value.strip().casefold()
    return next((a for a in allowed if a.casefold() == wanted), None)


def _cited(proposal: dict, known: dict[str, str]) -> list[str]:
    """The task ids this proposal names that actually exist, in order.

    An id the model invented is dropped silently *here* and the proposal is
    then discarded by the caller for having no citations left - which is the
    right outcome and the reason this returns a list rather than a boolean:
    a proposal citing three tasks of which one is real is still anchored, and
    one citing three inventions is not anchored at all.
    """
    raw = proposal.get("cites")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for item in raw:
        key = str(item).strip()
        full = known.get(key) or known.get(key.casefold())
        if full and full not in found:
            found.append(full)
    return found


def propose(
    session,
    project_id: str,
    drafter,
    categories: list[str],
) -> list[Risk]:
    """Read this project's task text and write the proposals as draft rows.

    Returns what was written. Raises `DraftsUnavailable` with a reason a person
    can act on - no credentials, no text to read, an unusable answer - rather
    than returning an empty list, because "the model found no risks" and "the
    model was never reached" must not look the same on a page.
    """
    project = scope.resolve(project_id)
    if project is None:
        raise DraftsUnavailable(f"unknown project {project_id!r}.")
    canonical = project.canonical_id
    project_ids = scope.source_ids_for(canonical)

    tasks = readable_tasks(session, project_ids)
    if not tasks:
        raise DraftsUnavailable(
            "no task on this project carries a description, so there is no text "
            "to read. A Jira export includes one only if the Description field "
            "is exported; a schedule sheet has a Description column."
        )

    #: Both spellings, so a model that lower-cases an id still cites a real row.
    known: dict[str, str] = {}
    for task in tasks:
        key = _task_key(task)
        known.setdefault(key, task.id)
        known.setdefault(key.casefold(), task.id)

    try:
        answer = drafter(SYSTEM, build_prompt(tasks, categories))
    except NarrationUnavailable as exc:
        raise DraftsUnavailable(str(exc)) from exc
    except Exception as exc:
        #: Everything the vendor SDK can raise, which is not only
        #: `NarrationUnavailable`. A timeout arrives as the SDK's own
        #: `APITimeoutError` and used to escape this function entirely, so the
        #: route answered 500 and the button span forever - the one outcome
        #: this module promises not to produce, because an optional feature
        #: being unreachable is a state to describe rather than a failure to
        #: raise. This one line is wholly vendor territory: anything thrown
        #: here means the model was not reached, which is exactly what
        #: `DraftsUnavailable` says.
        log.warning("risk drafts: model call failed", exc_info=True)
        raise DraftsUnavailable(
            f"the model could not be reached: {type(exc).__name__}. "
            "If this was a timeout, the export is large - try again, or read a "
            "project with fewer described tasks."
        ) from exc

    proposals = _parse(answer)
    if not proposals:
        raise DraftsUnavailable("the model proposed nothing from this text.")

    written: list[Risk] = []
    for proposal in proposals[:MAX_DRAFTS]:
        risk = _accept_proposal(proposal, canonical, known)
        if risk is not None:
            session.add(risk)
            written.append(risk)

    if not written:
        raise DraftsUnavailable(
            "every proposal cited a task that does not exist on this project, "
            "so none was kept. A risk that cannot point at a row is not "
            "checkable, and an unverifiable one is worse than none."
        )

    session.flush()
    return written


def _accept_proposal(proposal: dict, project_id: str, known: dict[str, str]):
    """One proposal as a draft row, or `None` if it does not survive the gate.

    Title and at least one real citation are required; everything else is
    optional and simply absent when the model left it out or got it wrong.
    That asymmetry is deliberate - a missing category is a blank field a person
    fills in, while a missing citation means nobody can check the claim at all.
    """
    title = proposal.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    cites = _cited(proposal, known)
    if not cites:
        return None

    description = proposal.get("description")
    category = proposal.get("category")

    return Risk(
        project_id=project_id,
        title=title.strip()[:500],
        status=DRAFT_STATUS,
        origin=ORIGIN_AI,
        cited_task_ids=",".join(cites),
        description=description.strip() if isinstance(description, str) else None,
        category=category.strip()[:50] if isinstance(category, str) else None,
        pre_likelihood=_one_of(proposal.get("likelihood"), LIKELIHOODS),
        pre_impact=_one_of(proposal.get("impact"), IMPACTS),
        #: No `risk_no`. Numbering is the register's own sequence and a draft is
        #: not in the register yet - handing it a number would leave a gap in
        #: the sequence for every draft anybody dismissed.
    )
