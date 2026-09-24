"""What a PM reads first: who is late, what is late, and where the code disagrees.

Written after reviewing the CoWorkLocal report (2026-09-24), which answered
none of those questions: its findings were all about the state of the dates,
its projection read "0 days" beside thirty-five tasks weeks past due, and its
one HIGH finding called a typo'd row a dependency problem on a project with no
dependencies.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.api.schemas.explain import ExplainBundle, ForwardStep
from app.api.schemas.team import Member, MemberTask, TeamBundle
from app.exports.document import (
    _late_work_blocks,
    _people_blocks,
    _projection_blocks,
    resolve_sections,
)
from app.intelligence.schedule.graph import TaskNode

AS_OF = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _t(key, due, closed=False, title=""):
    return MemberTask(entity_id=f"jira:Task:2:{key}", label=key, title=title or key,
                      planned_end=due, closed=closed)


def _team():
    return TeamBundle(project_id="p", members=[
        Member(name="Quan", tasks=[
            _t("CW-1", date(2026, 8, 31)), _t("CW-2", date(2026, 8, 31)),
            _t("CW-3", date(2026, 9, 1)), _t("CW-9", date(2026, 7, 1), closed=True),
            _t("CW-10", date(2026, 7, 1), closed=True),
            _t("CW-11", date(2026, 7, 1), closed=True),
            _t("CW-12", date(2026, 7, 1), closed=True),
        ]),
        Member(name="Hoach", tasks=[
            _t("CW-4", date(2026, 10, 2)), _t("CW-5", None),
        ]),
    ])


def _text(blocks) -> str:
    out = []
    for b in blocks:
        out.append(b.text)
        out += list(b.items)
        out += [" | ".join(r) for r in b.rows]
    return "\n".join(out)


def test_late_work_lists_overdue_oldest_first_with_owner_and_days():
    blocks = _late_work_blocks(_team(), AS_OF)
    text = _text(blocks)

    assert "3 open task(s) are past their due date as at 2026-09-23" in text
    assert "the oldest by 23 days" in text
    past = next(b for b in blocks if b.kind == "table" and b.text == "Past due")
    assert [r[0] for r in past.rows] == ["CW-1", "CW-2", "CW-3"]
    assert past.rows[0][2] == "Quan" and past.rows[0][4] == "23"
    soon = next(b for b in blocks if b.kind == "table"
                and b.text == "Due in the next fortnight")
    assert [r[0] for r in soon.rows] == ["CW-4"]
    assert "1 open task(s) have no due date" in text


def test_people_names_who_is_underwater_and_who_holds_the_project():
    blocks = _people_blocks(_team(), AS_OF)
    table = blocks[0]
    assert table.rows[0][:3] == ("Quan", "3", "3")
    text = _text(blocks)
    assert "Underwater - every open task past its date: Quan." in text
    # 7 of 9 tasks.
    assert "Quan holds 78% of all tasks" in text
    assert "counted in tasks, not hours" in text


def test_a_project_with_no_dependencies_does_not_show_a_projection_table():
    """Each task's projected finish would be its own due date - open tasks
    "finishing" on dates already behind the report."""
    explain = ExplainBundle(
        project_id="p",
        steps=[ForwardStep(entity_id="jira:Task:2:CW-1", label="CW-1",
                           planned_end=date(2026, 8, 31),
                           projected_end=date(2026, 8, 31))],
        project_slip_days=0,
        scalars={"tasks_overdue": 35, "worst_overdue_days": 23},
    )
    blocks = _projection_blocks(explain)
    text = _text(blocks)

    assert not any(b.kind == "table" for b in blocks)
    assert "open task(s) already past their own date" in text
    assert "the oldest is 23 days late" in text
    assert "records no dependencies" in text


def test_the_presets_a_pm_uses_carry_people_late_work_and_the_code_check():
    for preset in ("weekly", "steering"):
        chosen = resolve_sections(preset_id=preset)
        assert {"late_work", "people", "traceability"} <= set(chosen)
        # Straight after the summary: the first thing read after the prose.
        assert chosen.index("late_work") == 1


def test_trace_facts_join_verdicts_to_tracker_status_and_find_the_late_area(
        monkeypatch):
    from app.api import tracelink_view
    from app.intelligence.tracefacts import trace_facts

    def row(key, verdict, files=(), candidates=()):
        return {"key": key, "candidates": list(candidates),
                "verdict": {"verdict": verdict,
                            "evidence": [{"file": f} for f in files]}}

    payload = {"project_id": "p", "tickets": [
        row("CW-1", "contradicted"),
        row("CW-2", "unverified"),
        row("CW-3", "corroborated", files=["config.py", "core/chat/send.py"]),
        row("CW-4", "unverified", candidates=["core/chat/view.py"]),
        row("CW-5", "unverified", files=["ui/panel.py"]),
        {"key": "CW-6", "verdict": None},
    ]}
    monkeypatch.setattr(tracelink_view, "run_for_project", lambda pid: ("run", []))
    monkeypatch.setattr(tracelink_view, "collect", lambda run: payload)

    def node(key, status, due):
        return TaskNode(entity_id=f"jira:Task:2:{key}", project_id="p",
                        status=status, planned_end=due)

    tasks = [node("CW-1", "DONE", date(2026, 8, 1)),
             node("CW-2", "DONE", date(2026, 8, 1)),
             node("CW-3", "IN_PROGRESS", date(2026, 8, 31)),
             node("CW-4", "TODO", date(2026, 8, 31)),
             node("CW-5", "TODO", date(2026, 10, 1)),
             node("CW-6", "TODO", date(2026, 8, 31))]
    facts = trace_facts("p", tasks, date(2026, 9, 23))

    assert facts == {
        "available": True,
        "done_contradicted": 1,
        "done_unverified": 1,
        "open_built": 1,
        # CW-3 (cited) and CW-4 (candidate) are overdue in core/chat; CW-5 is
        # not yet due, CW-6 was never adjudicated.
        "worst_area": "core/chat",
        "worst_area_overdue": 2,
    }


def test_no_run_means_no_trace_facts_rather_than_a_clean_match(monkeypatch):
    from app.api import tracelink_view
    from app.intelligence.tracefacts import trace_facts

    monkeypatch.setattr(tracelink_view, "run_for_project", lambda pid: (None, []))
    assert trace_facts("p", [], date(2026, 9, 23)) == {"available": False}
