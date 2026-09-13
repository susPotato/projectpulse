"""The one lane where a model may say something nobody computed.

These are mostly refusals. The feature's value is not that it produces
sentences - any model does that - but that a sentence which cannot be checked
never reaches a page, and that a suggestion never becomes a record without a
person. Each test below pins one of those.

No network: a `Drafter` is `(system, user) -> str`, so a stub function is a
complete model as far as this code is concerned. That seam is why the fence
around it can be tested at all.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.db import session_scope
from app.models.domain import Project, Risk, Task
from app.risks.drafts import (
    DRAFT_STATUS,
    ORIGIN_AI,
    DraftsUnavailable,
    build_prompt,
    propose,
    readable_tasks,
)
from app.risks.service import CATEGORIES, accept_draft, dismiss_draft, list_drafts, list_risks

PROJECT = "excel:Project:1:DRAFTTEST"


@pytest.fixture(autouse=True)
def _project():
    """One project with two tasks, one of which carries text.

    Registered with `scope` as well as inserted: `propose` resolves the project
    through the registry (so a caller holding a paired source id still lands on
    one project), and a bare `Project` row is invisible to it.
    """
    from sqlalchemy import delete

    from app import scope
    from app.models.uploads import RegisteredProject

    scope.register(PROJECT, "Draft test")
    with session_scope() as session:
        session.merge(Project(id=PROJECT, name="Draft test"))
        session.merge(
            Task(
                id=f"excel:Task:1:{PROJECT}:D-1",
                project_id=PROJECT,
                title="Code runner with sandboxed execution",
                status="IN_PROGRESS",
                original_status="In Progress",
                description="Run generated code in a sandbox. No timeout is specified.",
                # Set so the prompt test can assert these never reach the model.
                due_date=date(2026, 11, 27),
                baseline_end=date(2026, 10, 13),
                progress=41,
            )
        )
        session.merge(
            Task(
                id=f"excel:Task:1:{PROJECT}:D-2",
                project_id=PROJECT,
                title="A task nobody described",
                status="TODO",
            )
        )
    yield
    with session_scope() as session:
        session.execute(delete(Risk).where(Risk.project_id == PROJECT))
        session.execute(delete(Task).where(Task.project_id == PROJECT))
        session.execute(
            delete(RegisteredProject).where(RegisteredProject.canonical_id == PROJECT)
        )


def _drafter(payload):
    """A model that answers with exactly this, ignoring the prompt."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return lambda system, user: text


def _propose(payload):
    with session_scope() as session:
        return propose(session, PROJECT, _drafter(payload), list(CATEGORIES))


def test_only_tasks_with_text_are_sent():
    """A model handed nothing but titles writes generic risks, which is the
    failure this whole module is trying to avoid."""
    with session_scope() as session:
        tasks = readable_tasks(session, [PROJECT])
    assert [t.id.rsplit(":", 1)[-1] for t in tasks] == ["D-1"]


def test_a_project_with_no_text_is_refused_with_a_reason():
    """Not an empty list: "nothing to read" and "the model found nothing" are
    different states and must not render the same."""
    from sqlalchemy import delete

    with session_scope() as session:
        session.execute(delete(Task).where(Task.project_id == PROJECT))

    with pytest.raises(DraftsUnavailable, match="no task on this project carries"):
        _propose([{"title": "x", "cites": ["D-1"]}])


def test_the_prompt_carries_text_and_no_computed_field():
    """The model is given what people wrote and nothing this app worked out.

    Asked to comment on a date or a percentage it would produce a second set of
    figures beside the ones on the Insight page, and the two would disagree the
    first time a sheet changed. So the task's due date, baseline and progress
    are set in the fixture and asserted *absent* here - a weaker test that
    banned the word "progress" only caught the status "In Progress", which is
    text the model is supposed to see.
    """
    with session_scope() as session:
        tasks = readable_tasks(session, [PROJECT])
        prompt = build_prompt(tasks, list(CATEGORIES))

    assert "2026-11-27" not in prompt  # due date
    assert "2026-10-13" not in prompt  # baseline
    assert "41" not in prompt  # progress

    # What it does carry: one block per task, of exactly these four keys.
    keys = {line.strip().split(":", 1)[0] for line in prompt.splitlines() if "  " in line}
    assert keys <= {"id", "title", "status", "text", "- id"}

    assert "sandbox" in prompt.casefold()  # the text itself did arrive


def test_a_proposal_citing_nothing_is_discarded():
    """The load-bearing refusal. A claim that cannot point at a row is not
    checkable, and an unverifiable claim is worse than no claim."""
    with pytest.raises(DraftsUnavailable, match="cited a task that does not exist"):
        _propose([{"title": "Something might go wrong", "cites": []}])


def test_a_proposal_citing_an_invented_task_is_discarded():
    """A model that names a plausible id nobody has is the same problem wearing
    a disguise."""
    with pytest.raises(DraftsUnavailable, match="cited a task that does not exist"):
        _propose([{"title": "Invented", "cites": ["NOPE-999"]}])


def test_a_proposal_keeps_only_the_citations_that_are_real():
    """Half-anchored is still anchored: the real citation survives and the
    invention is dropped, rather than the whole proposal going."""
    written = _propose(
        [{"title": "Sandbox has no timeout", "cites": ["D-1", "NOPE-999"]}]
    )
    assert written[0].cited_task_ids == f"excel:Task:1:{PROJECT}:D-1"


def test_a_draft_is_not_in_the_register_or_the_matrix():
    """The whole point. A suggestion nobody accepted must not reach a document
    that gets exported and sent to a customer."""
    _propose([{"title": "Sandbox has no timeout", "cites": ["D-1"],
               "likelihood": "Possible", "impact": "Major"}])

    with session_scope() as session:
        register = list_risks(session, project_ids=[PROJECT])
        drafts = list_drafts(session, project_ids=[PROJECT])

    assert register.risks == []
    assert len(drafts) == 1
    assert drafts[0].status == DRAFT_STATUS
    assert drafts[0].origin == ORIGIN_AI
    # And it contributes to no heat-map cell.
    assert all(cell.risk_count == 0 for cell in register.matrix)


def test_a_likelihood_outside_the_vocabulary_is_dropped_not_guessed():
    """`pre_rating` is a lookup on this pair, so a guessed likelihood does not
    stay a guess - it becomes a coloured badge that reads as an assessment."""
    written = _propose(
        [{"title": "T", "cites": ["D-1"], "likelihood": "quite likely", "impact": "Major"}]
    )
    assert written[0].pre_likelihood is None
    assert written[0].pre_impact == "Major"


def test_accepting_moves_it_into_the_register_and_keeps_its_provenance():
    """Agreeing with a sentence does not change where it came from."""
    _propose([{"title": "Sandbox has no timeout", "cites": ["D-1"]}])

    with session_scope() as session:
        draft = list_drafts(session, project_ids=[PROJECT])[0]
        accepted = accept_draft(session, draft.id)

    assert accepted is not None
    assert accepted.status == "Active"
    assert accepted.origin == ORIGIN_AI
    assert accepted.risk_no  # the register numbers its rows; a draft had none

    with session_scope() as session:
        assert len(list_risks(session, project_ids=[PROJECT]).risks) == 1
        assert list_drafts(session, project_ids=[PROJECT]) == []


def test_accepting_the_same_draft_twice_is_a_404_not_a_second_risk():
    """A stale page pressing a button twice, which is not an error worth a
    500 and must not duplicate the row."""
    _propose([{"title": "T", "cites": ["D-1"]}])
    with session_scope() as session:
        draft_id = list_drafts(session, project_ids=[PROJECT])[0].id
        assert accept_draft(session, draft_id) is not None
        assert accept_draft(session, draft_id) is None


def test_dismissing_removes_it_entirely():
    """A rejected suggestion is not a record of anything, and a pile of them
    would make the same bad idea reappear every time somebody looked."""
    _propose([{"title": "T", "cites": ["D-1"]}])
    with session_scope() as session:
        draft_id = list_drafts(session, project_ids=[PROJECT])[0].id
        assert dismiss_draft(session, draft_id) is True
        assert list_drafts(session, project_ids=[PROJECT]) == []


def test_dismiss_refuses_a_risk_that_is_not_a_draft():
    """The route must not become a second delete for the register."""
    _propose([{"title": "T", "cites": ["D-1"]}])
    with session_scope() as session:
        draft_id = list_drafts(session, project_ids=[PROJECT])[0].id
        accept_draft(session, draft_id)
        assert dismiss_draft(session, draft_id) is False


def test_a_fenced_answer_is_read_rather_than_rejected():
    """Several vendors wrap JSON in a code fence whatever the prompt says, and
    rejecting a good answer over its wrapper makes the feature look broken."""
    written = _propose('```json\n[{"title": "T", "cites": ["D-1"]}]\n```')
    assert len(written) == 1


def test_an_answer_that_is_not_json_is_refused_with_what_came_back():
    with pytest.raises(DraftsUnavailable, match="did not answer with JSON"):
        _propose("I'm sorry, I can't help with that.")


def test_an_array_wrapped_in_an_object_is_still_read():
    """Vendors do this too, and the content inside is usually fine."""
    written = _propose({"risks": [{"title": "T", "cites": ["D-1"]}]})
    assert len(written) == 1


def test_the_number_of_drafts_is_bounded():
    """A register nobody can read is the same as an empty one."""
    from app.risks.drafts import MAX_DRAFTS

    written = _propose(
        [{"title": f"Risk {n}", "cites": ["D-1"]} for n in range(MAX_DRAFTS + 5)]
    )
    assert len(written) == MAX_DRAFTS


def test_intelligence_does_not_import_the_drafts_module():
    """Pinned, because the rule is easy to break by accident and impossible to
    see afterwards: a deterministic engine that reads a model's opinion is not
    a deterministic engine. `Risk` already carries this rule for the register;
    a model's guess is no more admissible than a PM's."""
    from pathlib import Path

    engine = Path(__file__).resolve().parent.parent / "app" / "intelligence"
    for source in engine.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "risks.drafts" not in text, source
        assert "from app.risks" not in text, source


def test_a_vendor_timeout_becomes_a_reason_rather_than_escaping():
    """The failure that shipped: the Anthropic SDK raises its own
    `APITimeoutError`, not `NarrationUnavailable`, so it escaped `propose`
    entirely - the route answered 500 and the button span forever.

    An optional feature being unreachable is a state to describe, never an
    exception to leak, and the catch has to be wide enough to mean it.
    """

    class APITimeoutError(Exception):
        """Stands in for the vendor's own class, which is what matters: it is
        not one of ours and nothing here may assume it is."""

    def timing_out(system, user):
        raise APITimeoutError("Request timed out or interrupted.")

    with session_scope() as session:
        with pytest.raises(DraftsUnavailable, match="could not be reached"):
            propose(session, PROJECT, timing_out, list(CATEGORIES))


def test_the_prompt_is_bounded_even_on_a_backlog_of_hundreds():
    """The prompt has to stay inside a single request's patience. Pinned as a
    size rather than a task count, because that is the thing that actually
    timed out."""
    from app.risks.drafts import MAX_DESCRIPTION_CHARS, MAX_TASKS

    tasks = []
    for n in range(400):
        tasks.append(
            Task(
                id=f"excel:Task:1:{PROJECT}:BIG-{n}",
                project_id=PROJECT,
                title="A task with a long name " * 4,
                status="TODO",
                description="Procedure text. " * 200,
            )
        )
    prompt = build_prompt(tasks, list(CATEGORIES))

    assert prompt.count("- id:") == MAX_TASKS
    # Four fields per task, none longer than its own cap, plus a short preamble.
    assert len(prompt) < MAX_TASKS * (MAX_DESCRIPTION_CHARS + 500)


def _bundle(drafts, cited=(), reason=None):
    from app.api.schemas.risk import RiskDraftBundle

    return RiskDraftBundle(
        drafts=list(drafts), cited_tasks=list(cited), enabled=True, reason=reason
    )


def test_the_report_section_prints_every_proposal_with_what_it_cites():
    """A claim in a document that leaves the building has to carry its own
    check. The citation is what turns "a model said this" into something a
    reader can verify against their own tracker in a minute."""
    from app.api.schemas.risk import CitedTask
    from app.exports.document import _model_read_blocks

    _propose(
        [{"title": "Sandbox has no timeout", "cites": ["D-1"],
          "category": "Security", "likelihood": "Possible", "impact": "Major"}]
    )
    with session_scope() as session:
        drafts = list_drafts(session, project_ids=[PROJECT])

    cited = [
        CitedTask(
            task_id=f"excel:Task:1:{PROJECT}:D-1",
            label="D-1",
            title="Code runner with sandboxed execution",
            status="In Progress",
            text="Run generated code in a sandbox. No timeout is specified.",
        )
    ]
    blocks = _model_read_blocks(_bundle(drafts, cited))
    flat = "\n".join(
        str(b.text or "") + "\n" + "\n".join(" ".join(map(str, r)) for r in (b.rows or ()))
        for b in blocks
    )

    assert "Sandbox has no timeout" in flat
    # The readable key, not the internal id.
    assert "D-1" in flat and "excel:Task:1:" not in flat
    # And the text the model was actually given, so what is checked is what was read.
    assert "No timeout is specified" in flat
    # Said plainly that nobody has agreed to it.
    assert "not in the risk register" in flat.lower()


def test_an_accepted_proposal_leaves_the_model_section_for_the_register():
    """Accepting is what moves a row across. A proposal printed in both places
    would be counted twice by a reader."""
    from app.exports.document import _model_read_blocks

    _propose([{"title": "Sandbox has no timeout", "cites": ["D-1"]}])
    with session_scope() as session:
        accept_draft(session, list_drafts(session, project_ids=[PROJECT])[0].id)
        remaining = list_drafts(session, project_ids=[PROJECT])

    blocks = _model_read_blocks(_bundle(remaining, reason="Nothing proposed yet."))
    assert any("Nothing proposed yet." in str(b.text or "") for b in blocks)
