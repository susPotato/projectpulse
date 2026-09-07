"""End-to-end: real workbooks, real ingestion, real analysis, one bundle.

Every other test file mocks something. This one mocks nothing - it writes the
same spreadsheets `scripts.gen_demo_data` writes, runs the actual reader,
identity resolver, differ, dependency resolver and convertor over them, and then
asks the intelligence layer what it found. If the layers disagree about a
contract, this is what notices.

It also pins the two properties the demo depends on and that no unit test can
see, because both are about the *timeline* rather than any single component:

* an unchanged scan still tightens the next change's lower bound, and
* a cause and its effect observed in the same scan are not orderable, so the
  cascade only becomes provable when the steps are spread across scans.

In-memory SQLite, so it still needs no Docker.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

import app.ingest.sources.excel.source  # noqa: F401 - registers the source
from app.ingest.runner import run_sync
from app.intelligence.pipeline import analyze_project
from app.models.base import Base
from app.models.domain import Dependency, QaItem, StateChange, Task
from app.models.sync import SheetScan
from scripts.gen_demo_data import (
    SCHEDULE_HEADERS,
    WORKLOG_HEADERS,
    _schedule_rows,
    _worklog_rows,
    _write,
)

PROJECT = "excel:Project:1:HRMS"


def write_step(root, step: int) -> None:
    """Exactly what `scripts.gen_demo_data` writes, at one point in the story."""
    _write(
        root / "hrms_schedule.xlsx",
        "Activities",
        SCHEDULE_HEADERS,
        _schedule_rows(step),
        "HRMS Portal V2 - Delivery Schedule",
        extra_header="Comments" if step >= 2 else None,
    )
    _write(
        root / "hrms_worklog.xlsx",
        "Worklog",
        WORKLOG_HEADERS,
        _worklog_rows(step),
        "HRMS Portal V2 - QA Worklog",
    )


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def sync(session, root, at: str) -> None:
    import app.ingest.sources.excel.source as excel

    def run(sess, *, connection_id, now, sync_run_id=None):
        return excel.run_excel_sync(
            sess,
            connection_id=connection_id,
            now=now,
            sync_run_id=sync_run_id,
            data_root=root,
        )

    from app.ingest.runner import SOURCE_REGISTRY

    original = SOURCE_REGISTRY["excel"]
    SOURCE_REGISTRY["excel"] = run
    try:
        run_sync(
            session,
            "excel",
            now=datetime.fromisoformat(at).replace(tzinfo=timezone.utc),
        )
    finally:
        SOURCE_REGISTRY["excel"] = original


@pytest.fixture()
def replayed(session, tmp_path):
    """The demo timeline, spread so causes precede effects across scans."""
    write_step(tmp_path, 0)
    sync(session, tmp_path, "2026-03-02T09:00")

    write_step(tmp_path, 1)  # the environment slips
    sync(session, tmp_path, "2026-03-06T09:00")

    sync(session, tmp_path, "2026-03-10T09:00")  # nothing changed

    write_step(tmp_path, 2)  # downstream reacts
    sync(session, tmp_path, "2026-03-14T09:00")

    sync(session, tmp_path, "2026-03-18T09:00")  # nothing changed

    write_step(tmp_path, 3)  # the QA backlog grows
    sync(session, tmp_path, "2026-03-22T09:00")
    return session


# --------------------------------------------------------------------------
# Ingestion produced what the intelligence layer needs
# --------------------------------------------------------------------------


def test_the_domain_layer_is_populated(replayed):
    """Without the convertor the DAG joins ids that have no dates on them."""
    tasks = replayed.scalars(select(Task).where(Task.project_id == PROJECT)).all()

    assert len(tasks) == 6
    assert all(t.due_date is not None for t in tasks)
    assert all(t.raw_data_id is not None for t in tasks)


def test_dependency_edges_survive_to_the_domain_layer(replayed):
    edges = replayed.scalars(select(Dependency)).all()
    sources = {e.source for e in edges}

    assert len(edges) == 4
    assert sources == {"excel_predecessor", "wbs_implicit"}


def test_an_unchanged_scan_is_still_recorded(replayed):
    """It costs one row and it is what makes the next change orderable."""
    scans = replayed.scalars(
        select(SheetScan).where(SheetScan.scope == "hrms_schedule.xlsx#Activities")
    ).all()

    assert any(not s.changed for s in scans)
    assert sum(1 for s in scans if s.changed) >= 3


def test_qa_items_reach_the_domain_layer_as_blocked(replayed):
    blocked = replayed.scalar(
        select(func.count()).select_from(QaItem).where(QaItem.status == "BLOCKED")
    )

    assert blocked == 17


# --------------------------------------------------------------------------
# The cascade the product exists to find
# --------------------------------------------------------------------------


def test_the_bundle_finds_the_full_cascade(replayed):
    """Environment slip -> integration blocked -> QA queue stalls.

    Each link is found by a different template with a different evidence basis,
    which is the point: the system reports how it knows, not just what it thinks.
    """
    bundle = analyze_project(replayed, project_id=PROJECT)

    templates = {
        f.causal_link.template_id for f in bundle.findings if f.causal_link
    }
    assert "dependency_slip_hits_successor" in templates

    bases = {f.evidence_basis for f in bundle.findings if f.causal_link}
    assert "dependency_edge" in bases


def test_the_headline_chain_is_edge_backed_and_ranked_first(replayed):
    bundle = analyze_project(replayed, project_id=PROJECT)
    causal = [f for f in bundle.by_severity() if f.causal_link]

    assert causal[0].evidence_basis == "dependency_edge"
    assert causal[0].causal_link.cause.entity_label == "WBS-108"
    assert causal[0].causal_link.effect.entity_label == "WBS-114"


def test_a_bounded_chain_reports_its_lag_as_a_range(replayed):
    """Snapshot data cannot produce a single lag, so it must not claim one."""
    bundle = analyze_project(replayed, project_id=PROJECT)
    link = next(f.causal_link for f in bundle.findings if f.causal_link)

    assert link.ordering_basis == "bounded_disjoint"
    assert link.lag_days_max > link.lag_days_min


def test_the_schedule_engine_finds_slip_the_sheet_does_not_show(replayed):
    bundle = analyze_project(replayed, project_id=PROJECT)

    assert bundle.context["max_propagated_days"] > 0
    assert bundle.context["tasks_inconsistent"] > 0


# --------------------------------------------------------------------------
# Contracts the UI and the narration depend on
# --------------------------------------------------------------------------


def test_no_finding_ships_an_unsubstituted_token(replayed):
    """A literal {{token}} discredits every number on the page."""
    bundle = analyze_project(replayed, project_id=PROJECT)

    for finding in bundle.findings:
        assert "{{" not in finding.headline
        assert "{{" not in finding.recommendation


def test_every_finding_is_defensible(replayed):
    """Evidence or a rule trace - a finding with neither cannot be shown."""
    bundle = analyze_project(replayed, project_id=PROJECT)

    assert bundle.findings
    for finding in bundle.findings:
        assert finding.is_defensible, finding.id


def test_evidence_resolves_to_a_real_source_row(replayed):
    bundle = analyze_project(replayed, project_id=PROJECT)
    refs = [ref for f in bundle.findings for ref in f.evidence]

    assert refs
    for ref in refs:
        assert ref.raw_data_id is not None
        assert ref.raw_table
        assert ref.url


def test_data_quality_reports_what_was_refused(replayed):
    """A health score computed on 60% of a project is worse than none."""
    bundle = analyze_project(replayed, project_id=PROJECT)

    assert bundle.data_quality.rows_rejected > 0
    assert bundle.data_quality.edges_inferred == 1
    assert bundle.data_quality.edges_stated == 3


def test_the_narrative_is_always_present(replayed):
    """The deterministic fallback means a bundle is never an empty page."""
    bundle = analyze_project(replayed, project_id=PROJECT)

    assert bundle.narration_source == "template"
    for heading in ("What is at risk", "Why it is happening", "What to do next"):
        assert heading in bundle.narrative


def test_the_bundle_serialises(replayed):
    """It is an API contract; if it cannot serialise, the UI cannot render it."""
    bundle = analyze_project(replayed, project_id=PROJECT)
    payload = bundle.model_dump(mode="json")

    assert payload["project_id"] == PROJECT
    assert payload["findings"]


# --------------------------------------------------------------------------
# The timeline property the whole precision model rests on
# --------------------------------------------------------------------------


def test_a_cause_and_effect_in_one_scan_prove_nothing(session, tmp_path):
    """The demo has to be spread across scans, and this is why.

    Moving the environment slip and the downstream reaction in a single edit
    produces a story any human would read as causal - and the system correctly
    refuses to, because both changes share one time window.
    """
    write_step(tmp_path, 0)
    sync(session, tmp_path, "2026-03-02T09:00")

    write_step(tmp_path, 2)  # cause and effect together, in one scan
    sync(session, tmp_path, "2026-03-06T09:00")

    bundle = analyze_project(session, project_id=PROJECT)
    schedule_chains = [
        f
        for f in bundle.findings
        if f.causal_link
        and f.causal_link.template_id == "dependency_slip_hits_successor"
    ]

    assert schedule_chains == []
    assert session.scalar(select(func.count()).select_from(StateChange)) > 0


def test_another_project_s_rejects_are_not_reported_as_ours(replayed):
    """A PM must not see another team's data-quality problem as their own."""
    from app.models.sync import RawReject

    before = analyze_project(replayed, project_id=PROJECT).data_quality.rows_rejected

    replayed.add(
        RawReject(
            project_id="excel:Project:2:OTHER",
            file_path="other.xlsx",
            sheet_name="Activities",
            row_index=4,
            raw_row={},
            reason="someone else's broken row",
        )
    )
    replayed.flush()

    after = analyze_project(replayed, project_id=PROJECT).data_quality.rows_rejected

    assert before > 0
    assert after == before


def test_tasks_carry_a_real_milestone_foreign_key(replayed):
    """The label workaround is gone; `milestone_id` resolves to a row.

    Row keys are only unique within a sheet, so carrying a bare milestone label
    alongside meant two projects both using `WBS-101` could trade names. A real
    foreign key cannot do that.
    """
    from app.models.domain import Milestone

    milestones = replayed.scalars(
        select(Milestone).where(Milestone.project_id == PROJECT)
    ).all()
    assert milestones, "the convertor created no milestones"

    ids = {m.id for m in milestones}
    linked = replayed.scalars(
        select(Task).where(Task.project_id == PROJECT, Task.milestone_id.is_not(None))
    ).all()

    assert linked
    for task in linked:
        assert task.milestone_id in ids, f"{task.id} points at a missing milestone"


def test_a_milestone_is_dated_from_the_last_task_beneath_it(replayed):
    """A milestone is reached when the last thing under it is done."""
    from app.models.domain import Milestone

    env = replayed.scalars(
        select(Milestone).where(Milestone.name == "Environment Setup")
    ).one()
    tasks = replayed.scalars(
        select(Task).where(Task.milestone_id == env.id)
    ).all()

    assert env.planned_date == max(t.due_date for t in tasks if t.due_date)
