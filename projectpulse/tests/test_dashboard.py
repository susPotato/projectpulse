"""Guards for the dashboard canvas: the catalogue, tile CRUD, and the AI
generator's allow-list gate and fallback."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas.agent import ChatMessage
from app.api.schemas.dashboard import CustomChartDraft, CustomTileIn, TileIn
from app.dashboard.catalogue import CATALOGUE, for_scope
from app.dashboard.custom import (
    _local_revision,
    chat_turn,
    create_custom_tile,
    delete_custom_tile,
    diff_drafts,
    draft_chart,
    list_custom_tiles,
    summarize,
)
from app.dashboard.generator import generate_layout
from app.dashboard.service import (
    add_tile,
    apply_template,
    delete_tile,
    duplicate_tile,
    get_catalogue,
    get_dashboard,
    reset_blank,
    update_tile,
)
from app.models.base import Base

PROGRAM = "excel:Program:1:DEFAULT"
PROJECT = "excel:Project:1:HRMS"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def test_catalogue_only_has_program_and_project_scoped_tiles():
    bundle = get_catalogue()
    assert {t.scope for t in bundle.tiles} == {"program", "project"}
    assert len(bundle.tiles) == len(CATALOGUE)
    # Every tile key is unique - two entries sharing one would mean the
    # allow-list and the frontend registry could disagree about which tile a
    # key refers to.
    assert len({t.key for t in bundle.tiles}) == len(bundle.tiles)


def test_get_dashboard_auto_creates_a_blank_one(session):
    out = get_dashboard(session, "program", PROGRAM)
    assert out.tiles == []
    assert out.source == "blank"

    # Fetching again finds the same one rather than creating a second.
    again = get_dashboard(session, "program", PROGRAM)
    assert again.id == out.id


def test_add_update_duplicate_delete_tile(session):
    tile = add_tile(session, "project", PROJECT, TileIn(tile_key="milestones_at_risk", x=0, y=0, w=4, h=2))
    assert tile is not None
    assert tile.tile_key == "milestones_at_risk"
    assert tile.w == 4

    moved = update_tile(session, tile.id, TileIn(x=4, y=1))
    assert moved is not None
    assert (moved.x, moved.y, moved.w) == (4, 1, 4)  # w untouched by a partial update

    copy = duplicate_tile(session, tile.id)
    assert copy is not None
    assert copy.id != tile.id
    assert copy.tile_key == tile.tile_key

    assert delete_tile(session, tile.id) is True
    # `session.delete` only marks for deletion until the transaction boundary
    # flushes it (in production, `session_scope`'s commit - see `app/db.py`) -
    # match that here rather than asserting on an in-flight session state.
    session.flush()
    assert delete_tile(session, tile.id) is False  # already gone


def test_add_tile_rejects_a_missing_key(session):
    with pytest.raises(ValueError):
        add_tile(session, "project", PROJECT, TileIn())


def test_apply_template_only_places_tiles_for_this_scope(session):
    out = apply_template(session, "program", PROGRAM, "it_portfolio_dashboard")
    assert out is not None
    assert out.source == "template"
    assert len(out.tiles) > 0
    assert all(t.tile_key in {spec.key for spec in for_scope("program")} for t in out.tiles)
    # No overlap at the same (x, y) - the auto-layout packer's whole job.
    starts = [(t.x, t.y) for t in out.tiles]
    assert len(starts) == len(set(starts))


def test_apply_template_unknown_name_is_none(session):
    assert apply_template(session, "program", PROGRAM, "not-a-template") is None


def test_reset_blank_clears_existing_tiles(session):
    apply_template(session, "program", PROGRAM, "it_portfolio_dashboard")
    out = reset_blank(session, "program", PROGRAM)
    assert out.tiles == []
    assert out.source == "blank"


# --------------------------------------------------------------------------
# The AI generator: allow-list gate, and the fallback that keeps the feature
# working with the model switched off.
# --------------------------------------------------------------------------


def test_generate_layout_falls_back_when_no_drafter():
    keys, reason = generate_layout("anything", "program", PROGRAM, drafter=None)
    assert keys == [t.key for t in for_scope("program")]
    assert reason == "narration is switched off"


def test_generate_layout_keeps_only_known_keys_in_order():
    def fake_drafter(_system: str, _user: str) -> str:
        return "\nprogram_health\nnot-a-real-tile\nresource_conflict\nprogram_health\n"

    keys, reason = generate_layout("a brief", "program", PROGRAM, drafter=fake_drafter)
    # Duplicates and the unknown key dropped; order and case preserved.
    assert keys == ["program_health", "resource_conflict"]
    assert reason == ""


def test_generate_layout_falls_back_when_the_model_returns_nothing_recognizable():
    def fake_drafter(_system: str, _user: str) -> str:
        return "I cannot help with that."

    keys, reason = generate_layout("a brief", "project", PROJECT, drafter=fake_drafter)
    assert keys == [t.key for t in for_scope("project")]
    assert "no recognizable tile key" in reason


def test_generate_layout_falls_back_when_the_drafter_raises():
    def broken_drafter(_system: str, _user: str) -> str:
        raise RuntimeError("boom")

    keys, reason = generate_layout("a brief", "program", PROGRAM, drafter=broken_drafter)
    assert keys == [t.key for t in for_scope("program")]
    assert "the model call failed" in reason


def test_parse_tile_keys_caps_at_ten_and_drops_duplicates():
    from app.dashboard.generator import MAX_TILES, _parse_tile_keys

    fake_keys = {f"tile_{n}" for n in range(15)}
    raw = "\n".join([f"tile_{n}" for n in range(15)] + ["tile_0", "tile_1"])

    picked = _parse_tile_keys(raw, fake_keys)

    assert len(picked) == MAX_TILES == 10
    assert len(picked) == len(set(picked))
    assert picked == [f"tile_{n}" for n in range(10)]  # order preserved, first occurrence wins


# --------------------------------------------------------------------------
# Custom tiles: a person's own data, parsed - by a model when one is
# available, by a plain CSV/TSV reader when it isn't or its output doesn't
# validate. Never invented.
# --------------------------------------------------------------------------


def test_draft_chart_csv_fallback_with_no_drafter():
    draft = draft_chart("Jan, 10\nFeb, 14\nMar, 9", None, drafter=None)
    assert draft.source == "csv_fallback"
    assert draft.fallback_reason == "narration is switched off"
    assert draft.labels == ["Jan", "Feb", "Mar"]
    assert draft.values == [10.0, 14.0, 9.0]
    assert draft.chart_type == "bar"


def test_draft_chart_csv_fallback_handles_tabs_and_thousands_separators():
    draft = draft_chart("Q1\t1,200\nQ2\t980", None, drafter=None)
    assert draft.values == [1200.0, 980.0]


def test_draft_chart_raises_when_nothing_parses_and_no_drafter():
    with pytest.raises(ValueError):
        draft_chart("this is just a sentence with no structure", None, drafter=None)


def test_draft_chart_uses_the_models_own_json():
    def fake_drafter(_system: str, _user: str) -> str:
        return (
            'Sure, here you go:\n'
            '{"title": "Monthly Spend", "chart_type": "line", '
            '"labels": ["Jan", "Feb"], "values": [100, 150]}'
        )

    draft = draft_chart("some data", "a spend trend", drafter=fake_drafter)
    assert draft.source == "ai"
    assert draft.fallback_reason is None
    assert draft.title == "Monthly Spend"
    assert draft.chart_type == "line"
    assert draft.labels == ["Jan", "Feb"]
    assert draft.values == [100.0, 150.0]


def test_draft_chart_falls_back_to_csv_when_the_models_json_is_malformed():
    def bad_drafter(_system: str, _user: str) -> str:
        return "I'm not going to give you JSON today."

    draft = draft_chart("Jan, 10\nFeb, 14", None, drafter=bad_drafter)
    assert draft.source == "csv_fallback"
    assert "could not be parsed" in draft.fallback_reason


def test_draft_chart_rejects_a_model_response_with_mismatched_lengths():
    def bad_drafter(_system: str, _user: str) -> str:
        return '{"title": "X", "chart_type": "bar", "labels": ["a", "b"], "values": [1]}'

    # Falls through to the CSV parser rather than trusting a shape that
    # cannot be plotted - the same "drop rather than hedge" rule the causal
    # engine uses.
    draft = draft_chart("a, 1\nb, 2", None, drafter=bad_drafter)
    assert draft.source == "csv_fallback"


def test_draft_chart_rejects_an_unknown_chart_type():
    def bad_drafter(_system: str, _user: str) -> str:
        return '{"title": "X", "chart_type": "scatter3d", "labels": ["a"], "values": [1]}'

    draft = draft_chart("a, 1", None, drafter=bad_drafter)
    assert draft.source == "csv_fallback"


def test_create_list_and_delete_custom_tile(session):
    out = create_custom_tile(
        session,
        CustomTileIn(
            name="Monthly Spend", chart_type="line",
            labels=["Jan", "Feb"], values=[100, 150],
            source_note="pasted from the finance sheet",
        ),
    )
    assert out.id is not None
    assert out.chart_type == "line"

    listed = list_custom_tiles(session)
    assert [t.id for t in listed] == [out.id]

    assert delete_custom_tile(session, out.id) is True
    assert list_custom_tiles(session) == []


def test_create_custom_tile_rejects_mismatched_lengths(session):
    with pytest.raises(ValueError):
        create_custom_tile(
            session,
            CustomTileIn(name="Bad", chart_type="bar", labels=["a", "b"], values=[1]),
        )


# --------------------------------------------------------------------------
# The tile builder as a conversation: draft, look, say what is wrong, look
# again. Three properties are load-bearing and each has a test below - the
# deterministic path runs before any model, the transcript is computed by
# diffing rather than reported by the model, and a turn that cannot be
# applied keeps the draft that was on screen.
# --------------------------------------------------------------------------


def _msgs(*turns: str) -> list[ChatMessage]:
    """Alternating user/assistant, oldest first, ending on the user."""
    out: list[ChatMessage] = []
    for i, text in enumerate(turns):
        out.append(ChatMessage(role="user" if i % 2 == 0 else "assistant", content=text))
    return out


def _draft(**kw) -> CustomChartDraft:
    base = dict(
        title="Monthly spend",
        chart_type="bar",
        labels=["Jan", "Feb", "Mar"],
        values=[12000.0, 15500.0, 14200.0],
        source="ai",
    )
    base.update(kw)
    return CustomChartDraft(**base)


def _exploding_drafter(_system: str, _user: str) -> str:
    raise AssertionError("a model was called for a turn the fast path should own")


def test_the_opening_turn_drafts_from_pasted_data():
    turn = chat_turn(_msgs("monthly spend please"), None, "Jan, 10\nFeb, 14", drafter=None)
    assert turn.ok
    assert turn.draft.labels == ["Jan", "Feb"]
    assert turn.changes == []  # nothing to diff against yet
    assert "2 rows" in turn.reply


def test_the_opening_turn_treats_the_message_itself_as_the_data_when_none_was_pasted():
    turn = chat_turn(_msgs("Jan, 10\nFeb, 14"), None, None, drafter=None)
    assert turn.draft.labels == ["Jan", "Feb"]


def test_an_empty_message_is_refused_rather_than_drafted():
    with pytest.raises(ValueError):
        chat_turn(_msgs("   "), None, "Jan, 10", drafter=None)


def test_a_chart_type_switch_never_reaches_the_model():
    """The governing rule one layer in: an exact command is arithmetic, so it
    does not get to depend on a network call or on a model's mood."""
    before = _draft()
    turn = chat_turn(_msgs("make it a line chart"), before, None, drafter=_exploding_drafter)
    assert turn.ok
    assert turn.draft.chart_type == "line"
    assert turn.draft.source == "local_edit"
    assert [c.summary for c in turn.changes] == ["chart type bar -> line"]
    # and it changed nothing else
    assert turn.draft.labels == before.labels
    assert turn.draft.values == before.values


def test_a_rename_never_reaches_the_model():
    turn = chat_turn(_msgs("rename it to Q3 Spend"), _draft(), None, drafter=_exploding_drafter)
    assert turn.draft.title == "Q3 Spend"
    assert [c.field for c in turn.changes] == ["title"]


def test_asking_for_the_chart_type_it_already_is_is_answered_without_a_model():
    turn = chat_turn(_msgs("bar chart"), _draft(), None, drafter=_exploding_drafter)
    assert turn.ok
    assert turn.changes == []
    assert turn.reply == "That left the chart unchanged."


def test_a_compound_ask_is_not_half_applied_by_the_fast_path():
    """"make it a line chart and drop February" must go to the model: applying
    only the half the regex understands would silently ignore the rest."""
    assert _local_revision("make it a line chart and drop February", _draft()) is None


def test_a_revision_uses_the_models_json():
    def drafter(_system: str, user: str) -> str:
        assert "Monthly spend" in user  # it is shown the draft it must revise
        return '{"title": "Monthly spend", "chart_type": "bar", "labels": ["Jan", "Mar"], "values": [12000, 14200]}'

    turn = chat_turn(_msgs("drop February"), _draft(), None, drafter=drafter)
    assert turn.ok
    assert turn.draft.labels == ["Jan", "Mar"]
    assert [c.summary for c in turn.changes] == ["removed Feb"]


def test_the_transcript_reports_a_value_the_model_moved_without_being_asked():
    """The reason `diff_drafts` exists. A revision regenerates the whole
    chart, so a rename can come back with a value quietly altered - and prose
    from the model saying "renamed it" would hide exactly that."""

    def sneaky(_system: str, _user: str) -> str:
        return '{"title": "Q3 Spend", "chart_type": "bar", "labels": ["Jan", "Feb", "Mar"], "values": [12000, 15500, 99999]}'

    turn = chat_turn(_msgs("rename this please"), _draft(), None, drafter=sneaky)
    summaries = [c.summary for c in turn.changes]
    assert 'renamed to "Q3 Spend"' in summaries
    assert "changed 1 value" in summaries
    assert "changed 1 value" in turn.reply


def test_a_failed_revision_keeps_the_draft_that_was_on_screen():
    """Never a blank page: the preview must survive a model that returns
    nonsense, and the turn must say it did nothing."""

    def useless(_system: str, _user: str) -> str:
        return "I would rather not."

    before = _draft()
    turn = chat_turn(_msgs("sort it descending"), before, None, drafter=useless)
    assert turn.ok is False
    assert turn.error is not None
    assert turn.draft.labels == before.labels
    assert turn.draft.values == before.values
    assert turn.changes == []


def test_a_revision_that_does_not_validate_is_refused_not_plotted():
    def mismatched(_system: str, _user: str) -> str:
        return '{"title": "X", "chart_type": "bar", "labels": ["a", "b"], "values": [1]}'

    turn = chat_turn(_msgs("change something"), _draft(), None, drafter=mismatched)
    assert turn.ok is False
    assert turn.draft.title == "Monthly spend"


def test_a_provider_that_raises_is_a_failed_turn_not_a_500():
    def dead(_system: str, _user: str) -> str:
        raise RuntimeError("socket closed")

    turn = chat_turn(_msgs("do something clever"), _draft(), None, drafter=dead)
    assert turn.ok is False
    assert turn.draft.title == "Monthly spend"


def test_with_narration_off_a_revision_says_what_it_can_still_do():
    turn = chat_turn(_msgs("sort it descending"), _draft(), None, drafter=None)
    assert turn.ok is False
    assert "chart type" in turn.error and "rename" in turn.error
    # ...but the two deterministic commands still work with no model at all.
    assert chat_turn(_msgs("pie chart"), _draft(), None, drafter=None).ok


def test_a_reorder_is_not_counted_as_every_value_changing():
    before = _draft()
    after = _draft(labels=["Mar", "Jan", "Feb"], values=[14200.0, 12000.0, 15500.0])
    summaries = [c.summary for c in diff_drafts(before, after)]
    assert summaries == ["reordered 3 rows"]


def test_the_diff_is_empty_for_two_identical_drafts():
    assert diff_drafts(_draft(), _draft()) == []
    assert summarize([]) == "That left the chart unchanged."
