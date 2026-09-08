"""Guards for the narration cache: a model is asked at most once per fact set.

Needs a real database because the cache is a table, not a process-memory
dict - deliberately, since a Fly deployment stops idle machines and runs more
than one. SQLite in memory is enough; nothing here needs Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas.insight import DataQuality, Finding, InsightBundle
from app.models.base import Base
from app.narration.cache import cached_narrate


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def _bundle(**kw) -> InsightBundle:
    base = dict(
        project_id="excel:Project:1:HRMS",
        generated_at=datetime(2026, 3, 22, tzinfo=timezone.utc),
        as_of=datetime(2026, 3, 22, tzinfo=timezone.utc),
        findings=[
            Finding(
                id="f",
                category="schedule_risk",
                severity="high",
                headline="The plan cannot hold.",
                recommendation="Re-baseline.",
            )
        ],
    )
    base.update(kw)
    return InsightBundle(**base)


class CountingDrafter:
    """A `Drafter` that counts how many times it was actually called."""

    def __init__(self, text: str = "What is at risk\nAll clear.\n\nWhy it is happening\nN/A.\n\nWhat it will impact\nNothing.\n\nWhat to do next\nNothing."):
        self.calls = 0
        self.text = text

    def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        return self.text


def test_a_second_call_with_identical_facts_does_not_ask_the_model(session):
    drafter = CountingDrafter()
    bundle = _bundle()

    first = cached_narrate(session, bundle, project_id=bundle.project_id, drafter=drafter)
    second = cached_narrate(session, bundle, project_id=bundle.project_id, drafter=drafter)

    assert drafter.calls == 1
    assert first.source == "model"
    assert second.source == "model"
    assert second.fallback_reason is None
    assert second.narrative == first.narrative


def test_changed_facts_ask_the_model_again(session):
    drafter = CountingDrafter()

    cached_narrate(session, _bundle(), project_id="excel:Project:1:HRMS", drafter=drafter)
    changed = _bundle(
        findings=[
            Finding(
                id="f2",
                category="quality_risk",
                severity="critical",
                headline="QA is blocked.",
                recommendation="Unblock it.",
            )
        ]
    )
    cached_narrate(session, changed, project_id="excel:Project:1:HRMS", drafter=drafter)

    assert drafter.calls == 2


def test_different_projects_do_not_share_a_cache_entry(session):
    drafter = CountingDrafter()
    bundle = _bundle()

    cached_narrate(session, bundle, project_id="excel:Project:1:HRMS", drafter=drafter)
    cached_narrate(session, bundle, project_id="excel:Project:1:SAIN", drafter=drafter)

    assert drafter.calls == 2


def test_no_drafter_skips_the_cache_entirely(session):
    bundle = _bundle()

    outcome = cached_narrate(session, bundle, project_id=bundle.project_id, drafter=None)

    assert outcome.source == "template"
    # Nothing should have been written for a call that never asked a model.
    from app.models.narration import NarrationCacheEntry

    assert session.query(NarrationCacheEntry).count() == 0


def test_a_fallback_is_never_cached(session):
    def refusing_drafter(system: str, user: str) -> str:
        raise RuntimeError("the vendor refused")

    bundle = _bundle()
    first = cached_narrate(session, bundle, project_id=bundle.project_id, drafter=refusing_drafter)
    assert first.source == "template"
    assert first.fallback_reason is not None

    # A fixed drafter on the very next call must be tried, not skipped -
    # a cached failure would make a transient error stick.
    working = CountingDrafter()
    second = cached_narrate(session, bundle, project_id=bundle.project_id, drafter=working)

    assert working.calls == 1
    assert second.source == "model"
