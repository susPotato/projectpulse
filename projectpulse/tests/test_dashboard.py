"""Guards for the dashboard canvas: the catalogue, tile CRUD, and the AI
generator's allow-list gate and fallback."""

from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.schemas.agent import ChatMessage
from app.api.schemas.dashboard import CustomChartDraft, CustomTileIn, LiveSource, TileIn
from app.dashboard.catalogue import (
    BY_KEY,
    CATALOGUE,
    DEFAULT_TEMPLATE,
    TEMPLATES,
    for_scope,
)
from app.dashboard.custom import (
    _local_revision,
    chat_turn,
    create_custom_tile,
    delete_custom_tile,
    diff_drafts,
    draft_chart,
    get_custom_tile,
    list_custom_tiles,
    summarize,
)
from app.dashboard.generator import generate_layout
from app.dashboard.service import (
    add_tile,
    apply_default,
    seed_default_dashboard,
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


def test_every_template_places_tiles_for_the_scope_it_names(session):
    """A template whose keys are all for the other scope would silently apply
    as an empty dashboard - which is exactly the blank canvas the seeding
    below exists to prevent, arriving by a different route."""
    for name in TEMPLATES:
        for scope, scope_id in (("program", PROGRAM), ("project", PROJECT)):
            keys = [k for k in TEMPLATES[name] if BY_KEY[k].scope == scope]
            if not keys:
                continue
            out = apply_template(session, scope, scope_id, name)
            assert out is not None
            assert len(out.tiles) == len(keys), f"{name} on {scope}"


def test_seeding_gives_an_empty_dashboard_a_starting_layout(session):
    """The gap this closes: `get_dashboard` auto-creates a *blank* canvas, so
    a freshly-replayed demo database opened the app's biggest feature on
    "Click Add Tiles"."""
    assert get_dashboard(session, "project", PROJECT).tiles == []
    assert seed_default_dashboard(session, "project", PROJECT, "project_delivery_review")
    assert len(get_dashboard(session, "project", PROJECT).tiles) > 0


def test_seeding_never_overwrites_a_layout_somebody_arranged(session):
    """Idempotent *and* non-destructive - `scripts.seed_extras` is re-runnable,
    and a PM's own arrangement outranks the demo default."""
    add_tile(session, "project", PROJECT, TileIn(tile_key="schedule_gantt", x=0, y=0, w=4, h=3))
    before = get_dashboard(session, "project", PROJECT).tiles

    assert seed_default_dashboard(session, "project", PROJECT, "project_delivery_review") is False

    after = get_dashboard(session, "project", PROJECT).tiles
    assert [t.tile_key for t in after] == [t.tile_key for t in before]


def test_apply_template_unknown_name_is_none(session):
    assert apply_template(session, "program", PROGRAM, "not-a-template") is None


def test_reset_blank_clears_existing_tiles(session):
    apply_template(session, "program", PROGRAM, "it_portfolio_dashboard")
    out = reset_blank(session, "program", PROGRAM)
    assert out.tiles == []
    assert out.source == "blank"


# --------------------------------------------------------------------------
# "Default setup": the one-click board per scope, and the pair of defaults
# behind it.
# --------------------------------------------------------------------------


def test_every_scope_has_a_default_and_it_names_a_real_template():
    """The button offers a default for both levels or neither. A scope missing
    from this map turns the button into a 400 on the only board a new scope
    ever opens on."""
    assert set(DEFAULT_TEMPLATE) == {"program", "project"}
    for scope, template in DEFAULT_TEMPLATE.items():
        assert template in TEMPLATES, template
        keys = TEMPLATES[template]
        assert keys, template
        # Every key in a default is for that scope - a default half-composed of
        # the other level's tiles applies as a partial board, which looks like
        # the feature half-working rather than like a mistake.
        assert all(BY_KEY[k].scope == scope for k in keys), template


def test_apply_default_places_the_scopes_whole_default(session):
    for scope, scope_id in (("program", PROGRAM), ("project", PROJECT)):
        out = apply_default(session, scope, scope_id)
        assert out is not None
        assert out.source == "template"
        assert [t.tile_key for t in out.tiles] == list(TEMPLATES[DEFAULT_TEMPLATE[scope]])


def test_apply_default_and_browse_templates_place_the_identical_board(session):
    """The two routes into the same decision must not drift. `apply_default`
    defers to `apply_template` precisely so this holds by construction."""
    for scope, scope_id in (("program", PROGRAM), ("project", PROJECT)):
        via_default = apply_default(session, scope, scope_id)
        via_template = apply_template(session, scope, scope_id, DEFAULT_TEMPLATE[scope])
        assert via_default is not None and via_template is not None
        assert [(t.tile_key, t.x, t.y, t.w, t.h) for t in via_default.tiles] == [
            (t.tile_key, t.x, t.y, t.w, t.h) for t in via_template.tiles
        ]


def test_apply_default_replaces_rather_than_appends(session):
    """It is a "New Dashboard" path, not an "Add Tiles" one - which is why the
    button that calls it confirms first when there is something to lose."""
    add_tile(session, "project", PROJECT, TileIn(tile_key="schedule_gantt", x=0, y=0, w=4, h=3))
    out = apply_default(session, "project", PROJECT)
    assert out is not None
    expected = list(TEMPLATES[DEFAULT_TEMPLATE["project"]])
    assert [t.tile_key for t in out.tiles] == expected
    assert len(out.tiles) == len(expected)


def test_apply_default_is_none_for_a_scope_with_no_default(session):
    """The route turns this into a 400. Returning an empty dashboard instead
    would look like a default that happens to be blank."""
    assert apply_default(session, "portfolio", PROGRAM) is None


def test_the_default_boards_fill_whole_grid_rows(session):
    """Not cosmetic. `auto_layout` wraps at 12 columns and never back-fills, so
    a tile whose width does not close its row leaves a permanent gap on the
    board every new scope opens on. Only a trailing row may be short."""
    from app.dashboard.service import GRID_COLS

    for scope, scope_id in (("program", PROGRAM), ("project", PROJECT)):
        out = apply_default(session, scope, scope_id)
        assert out is not None
        widths: dict[int, int] = {}
        for tile in out.tiles:
            widths[tile.y] = widths.get(tile.y, 0) + tile.w
        rows = sorted(widths)
        for y in rows[:-1]:
            assert widths[y] == GRID_COLS, f"{scope} row y={y} is {widths[y]} wide"
        assert widths[rows[-1]] <= GRID_COLS


def test_the_project_default_carries_the_program_relationship(session):
    """The two defaults are designed as a pair, and this is the hinge: a
    project board that cannot name its program has no way to show the one
    thing a project cannot compute for itself - what a sibling is taking from
    it (`app/scope.py`, `app/intelligence/contention.py`)."""
    assert "program_context" in TEMPLATES[DEFAULT_TEMPLATE["project"]]
    assert "resource_contention_split" in TEMPLATES[DEFAULT_TEMPLATE["program"]]


def test_every_new_catalogue_tile_reads_a_bundle_that_exists():
    """The catalogue's own rule (its module docstring): a tile is a slice of a
    bundle that already exists, never a trigger for new backend computation.
    This pins the `data_source` values to the routes actually served, so a
    future entry naming a bundle nobody computes fails here rather than
    rendering an empty tile."""
    served = {
        "insight": "/api/insight",
        "portfolio": "/api/portfolio",
        "programs": "/api/programs/{program_id}",
        "program": "/api/program",
        "risks": "/api/risks",
        "gantt": "/api/gantt",
        "forecast": "/api/forecast",
        "team": "/api/team",
        "traceability": "/api/traceability",
    }
    from app.api.main import app

    paths = set(app.openapi()["paths"])
    for spec in CATALOGUE:
        assert spec.data_source in served, spec.key
        assert served[spec.data_source] in paths, (spec.key, spec.data_source)


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


def test_pasted_data_tiles_never_carry_a_live_source(session):
    """No `live_source` on the way in means none on the way out - a tile
    built from pasted data has no tool to refresh it from."""
    out = create_custom_tile(
        session,
        CustomTileIn(name="Pasted", chart_type="bar", labels=["a"], values=[1]),
    )
    assert out.live_source is None


# --------------------------------------------------------------------------
# Live tiles: a tool-sourced draft's rows are not what got frozen at save -
# `get_custom_tile` re-reads them from the same tool every time instead.
# --------------------------------------------------------------------------

_LIVE_SOURCE = LiveSource(
    tool="get_team_effort",
    project_id=PROJECT,
    list_field="members",
    label_field="name",
    value_field="hours_logged",
)


def test_get_custom_tile_recomputes_live_rows_on_every_read(session, monkeypatch):
    monkeypatch.setattr(
        "app.dashboard.agent._execute_tool",
        lambda name, session, project_id: {
            "members": [{"name": "Tung Nguyen", "hours_logged": 16.0}]
        },
    )
    created = create_custom_tile(
        session,
        CustomTileIn(
            name="Hours Logged", chart_type="bar",
            labels=["Tung Nguyen"], values=[16.0], live_source=_LIVE_SOURCE,
        ),
    )
    assert created.values == [16.0]

    # The underlying number moves, as it would after the next sync - the
    # frozen 16.0 above is not what a second read should answer with.
    monkeypatch.setattr(
        "app.dashboard.agent._execute_tool",
        lambda name, session, project_id: {
            "members": [{"name": "Tung Nguyen", "hours_logged": 24.0}]
        },
    )
    refreshed = get_custom_tile(session, created.id)
    assert refreshed is not None
    assert refreshed.values == [24.0]
    assert refreshed.live_source == _LIVE_SOURCE


def test_get_custom_tile_falls_back_to_the_snapshot_when_the_live_read_fails(session, monkeypatch):
    """A live tile degrading to its last known values beats a 500 taking the
    rest of the dashboard down with it."""
    monkeypatch.setattr(
        "app.dashboard.agent._execute_tool",
        lambda name, session, project_id: {"members": [{"name": "A", "hours_logged": 5.0}]},
    )
    created = create_custom_tile(
        session,
        CustomTileIn(name="X", chart_type="bar", labels=["A"], values=[5.0], live_source=_LIVE_SOURCE),
    )

    def _boom(name, session, project_id):
        raise RuntimeError("database hiccup")

    monkeypatch.setattr("app.dashboard.agent._execute_tool", _boom)
    still_there = get_custom_tile(session, created.id)
    assert still_there is not None
    assert still_there.values == [5.0]


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


# --------------------------------------------------------------------------
# The tool-use tile agent (app/dashboard/agent.py): the opening turn can
# reach for a project's own real data instead of demanding a paste.
# --------------------------------------------------------------------------


def test_each_tool_returns_the_expected_shape_against_an_empty_project(session):
    from app.dashboard.agent import TOOLS, _execute_tool

    # Every tool is declared with a name that dispatches, and vice versa -
    # a renamed tool that _execute_tool forgot about would silently return
    # the "unknown tool" branch instead of failing at import time.
    names = {t["name"] for t in TOOLS}
    assert names == {
        "get_team_effort", "get_project_findings",
        "get_risk_register", "get_delivery_forecast",
    }

    team = _execute_tool("get_team_effort", session, PROJECT)
    assert team == {"members": []}

    findings = _execute_tool("get_project_findings", session, PROJECT)
    assert findings["findings"] == []
    assert findings["task_count"] in (None, 0)

    risks = _execute_tool("get_risk_register", session, PROJECT)
    assert risks == {"risks": []}

    forecast = _execute_tool("get_delivery_forecast", session, PROJECT)
    assert forecast["available"] is False
    assert "reason" in forecast

    assert _execute_tool("not_a_real_tool", session, PROJECT) == {
        "error": "unknown tool 'not_a_real_tool'"
    }


def test_agentic_turn_reports_a_real_provider_failure_instead_of_going_silent():
    """No SDK / no key / any client-construction failure degrades to
    (None, None) - chat_turn's contract is that this never raises, only
    falls back. But an empty api_key with no ambient credential still
    constructs a client (the SDK defers auth to request time), so the
    failure actually surfaces from the API call itself - and that one is
    worth telling the person about rather than silently discarding, so it
    comes back as (None, note) instead."""
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    draft, note = agentic_turn(
        _msgs("track each employee's effort and compare them"),
        session=None,
        project_id=PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key=""),
    )
    assert draft is None
    assert note is not None


class _FakeAgentResponse:
    def __init__(self, *, text=None, tool_calls=None):
        content = []
        if tool_calls:
            content.extend(tool_calls)
        if text is not None:
            content.append(types.SimpleNamespace(type="text", text=text))
        self.content = content
        self.stop_reason = "tool_use" if tool_calls else "end_turn"


def _install_fake_agent_anthropic(monkeypatch, responses: list):
    """One fake response per call to `.messages.create`, in order."""
    calls = list(responses)

    class _FakeMessages:
        def create(self, **_kw):
            return calls.pop(0)

    class _FakeClient:
        def __init__(self, **_kw):
            self.messages = _FakeMessages()

    fake_module = types.SimpleNamespace(Anthropic=lambda **kw: _FakeClient(**kw))
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)


def test_agentic_turn_explains_when_the_request_cannot_become_a_chart(monkeypatch, session):
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    explanation = "A bar chart is one hue by design - there's no per-bar color field to set."
    _install_fake_agent_anthropic(
        monkeypatch, [_FakeAgentResponse(text=explanation)]
    )

    draft, note = agentic_turn(
        _msgs("give each bar its own color"),
        session,
        PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key="x"),
        current_draft=_draft(),
    )

    assert draft is None
    assert note == explanation


def test_agentic_turn_returns_a_draft_from_the_models_final_json(monkeypatch, session):
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    body = (
        '{"title": "Hours Logged", "chart_type": "bar", '
        '"labels": ["A", "B"], "values": [1, 2]}'
    )
    _install_fake_agent_anthropic(monkeypatch, [_FakeAgentResponse(text=body)])

    draft, note = agentic_turn(
        _msgs("chart hours logged"),
        session, PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key="x"),
    )

    assert note is None
    assert draft is not None
    assert draft.title == "Hours Logged"


def test_agentic_turn_executes_a_tool_call_before_answering(monkeypatch, session):
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    tool_call = types.SimpleNamespace(type="tool_use", id="t1", name="get_team_effort", input={})
    final = (
        '{"title": "Team Effort", "chart_type": "bar", '
        '"labels": ["A", "B"], "values": [1, 2]}'
    )
    _install_fake_agent_anthropic(
        monkeypatch,
        [_FakeAgentResponse(tool_calls=[tool_call]), _FakeAgentResponse(text=final)],
    )

    draft, note = agentic_turn(
        _msgs("compare team effort"),
        session, PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key="x"),
    )

    assert note is None
    assert draft is not None and draft.title == "Team Effort"


def test_agentic_turn_infers_a_live_source_when_the_draft_matches_the_tool_exactly(
    monkeypatch, session
):
    """The model's own JSON is never trusted for this on its own -
    `_infer_live_source` checks it against the tool's raw result field by
    field, and only a chart that matches exactly gets marked live."""
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    monkeypatch.setattr(
        "app.dashboard.agent._execute_tool",
        lambda name, session, project_id: {
            "members": [
                {"name": "Tung Nguyen", "hours_logged": 16.0, "hours_planned": 20.0},
                {"name": "My Nguyen", "hours_logged": 11.0, "hours_planned": 15.0},
            ]
        },
    )
    tool_call = types.SimpleNamespace(type="tool_use", id="t1", name="get_team_effort", input={})
    final = (
        '{"title": "Hours Logged", "chart_type": "bar", '
        '"labels": ["Tung Nguyen", "My Nguyen"], "values": [16.0, 11.0]}'
    )
    _install_fake_agent_anthropic(
        monkeypatch,
        [_FakeAgentResponse(tool_calls=[tool_call]), _FakeAgentResponse(text=final)],
    )

    draft, note = agentic_turn(
        _msgs("chart hours logged per member"),
        session, PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key="x"),
    )

    assert note is None
    assert draft is not None
    assert draft.live_source is not None
    assert draft.live_source.tool == "get_team_effort"
    assert draft.live_source.project_id == PROJECT
    assert draft.live_source.list_field == "members"
    assert draft.live_source.label_field == "name"
    assert draft.live_source.value_field == "hours_logged"


def test_agentic_turn_does_not_infer_a_live_source_it_cannot_reproduce(monkeypatch, session):
    """A findings-by-severity count is the model's own aggregation, not a
    field read straight from the tool - `get_project_findings`'s rows carry
    no numeric field at all, so nothing can match and this stays a
    snapshot rather than a live recipe that would silently drift."""
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    monkeypatch.setattr(
        "app.dashboard.agent._execute_tool",
        lambda name, session, project_id: {
            "findings": [
                {"severity": "high", "category": "schedule_risk", "headline": "..."},
                {"severity": "medium", "category": "quality_risk", "headline": "..."},
            ],
            "milestones_at_risk": 1,
            "qa_blocked": 0,
            "qa_count": 5,
            "task_count": 10,
        },
    )
    tool_call = types.SimpleNamespace(
        type="tool_use", id="t1", name="get_project_findings", input={}
    )
    final = (
        '{"title": "Findings by Severity", "chart_type": "bar", '
        '"labels": ["high", "medium"], "values": [1, 1]}'
    )
    _install_fake_agent_anthropic(
        monkeypatch,
        [_FakeAgentResponse(tool_calls=[tool_call]), _FakeAgentResponse(text=final)],
    )

    draft, note = agentic_turn(
        _msgs("chart findings by severity"),
        session, PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key="x"),
    )

    assert note is None
    assert draft is not None
    assert draft.live_source is None


def test_agentic_turn_does_not_infer_a_live_source_from_two_distinct_tools(monkeypatch, session):
    """Which of two tools produced which row is not recoverable from the
    final JSON alone, so a chart built from more than one tool call this
    turn is never marked live, however well its numbers happen to match."""
    from app.dashboard.agent import agentic_turn
    from app.narration.providers import ModelConfig

    def fake_tool(name, session, project_id):
        if name == "get_team_effort":
            return {"members": [{"name": "A", "hours_logged": 5.0}]}
        return {"risks": [{"title": "A", "category": "schedule", "rating": "high"}]}

    monkeypatch.setattr("app.dashboard.agent._execute_tool", fake_tool)
    calls = [
        types.SimpleNamespace(type="tool_use", id="t1", name="get_team_effort", input={}),
        types.SimpleNamespace(type="tool_use", id="t2", name="get_risk_register", input={}),
    ]
    final = (
        '{"title": "Mixed", "chart_type": "bar", "labels": ["A"], "values": [5.0]}'
    )
    _install_fake_agent_anthropic(
        monkeypatch,
        [_FakeAgentResponse(tool_calls=calls), _FakeAgentResponse(text=final)],
    )

    draft, note = agentic_turn(
        _msgs("chart effort and risk together"),
        session, PROJECT,
        cfg=ModelConfig(model="claude-opus-5", api_key="x"),
    )

    assert note is None
    assert draft is not None
    assert draft.live_source is None


def test_chat_turn_surfaces_the_agents_explanation_as_an_ok_reply_on_a_revision(monkeypatch, session):
    """The gap this closes: a request the schema cannot express used to come
    back as a scripted 'unchanged' - now the model's own reasoning, when it
    has any, becomes the reply instead."""
    from app.narration.providers import ModelConfig

    explanation = "There's no per-bar color field on this chart type."
    _install_fake_agent_anthropic(monkeypatch, [_FakeAgentResponse(text=explanation)])

    result = chat_turn(
        _msgs("give each bar its own color"),
        _draft(),
        None,
        drafter=None,
        session=session,
        project_id=PROJECT,
        agent_cfg=ModelConfig(model="claude-opus-5", api_key="x"),
    )

    assert result.ok is True
    assert result.reply == explanation
    assert result.draft == _draft()  # unchanged, not blanked


def test_chat_turn_falls_back_to_the_plain_error_when_the_agent_finds_nothing(session):
    """No agent_cfg (e.g. narration off, or a non-Anthropic provider) means
    the opening turn behaves exactly as it did before this feature existed."""
    with pytest.raises(ValueError):
        chat_turn(
            _msgs("track each employee's effort and compare them"),
            None, None, drafter=None,
            session=session, project_id=PROJECT, agent_cfg=None,
        )

# --------------------------------------------------------------------------
# Fitting a board to the data, rather than assuming it.
# --------------------------------------------------------------------------


def test_every_tile_declares_what_it_needs_or_declares_nothing():
    """A `requires` naming a signal outside the vocabulary would silently never
    be satisfied, and the tile would vanish from every fitted board with no
    error anywhere."""
    from typing import get_args

    from app.dashboard.catalogue import CATALOGUE, Signal

    known = set(get_args(Signal))
    for tile in CATALOGUE:
        unknown = set(tile.requires) - known
        assert not unknown, f"{tile.key} requires unknown signal(s) {unknown}"


def test_a_tile_with_no_requirement_is_always_fitted():
    """The narrative and summary tiles degrade rather than break - a brief falls
    back to the deterministic template, a summary says "nothing ingested yet" -
    and both are worth showing on a board that would otherwise be empty."""
    from app.dashboard.catalogue import for_scope
    from app.dashboard.fit import fitted_tiles

    fitted = {t.key for t in fitted_tiles("project", set())}
    always = {t.key for t in for_scope("project") if not t.requires}

    assert always, "no tile is unconditionally available"
    assert always <= fitted


def test_a_tile_is_left_off_only_when_its_input_is_absent():
    """Not when its numbers are zero. "No risks logged yet" is a true statement
    about a project with a register; "this source cannot express a dependency"
    is a different one, and only the second is a reason to hide a tile."""
    from app.dashboard.fit import fitted_tiles

    without = {t.key for t in fitted_tiles("project", {"tasks"})}
    with_baseline = {t.key for t in fitted_tiles("project", {"tasks", "baseline"})}

    assert "schedule_variance" not in without
    assert "schedule_variance" in with_baseline
    # A richer set never removes a tile.
    assert without < with_baseline


def test_more_signals_never_means_fewer_tiles():
    """Monotonicity, over every subset that matters. A board cannot shrink
    because a project gained data."""
    from itertools import combinations
    from typing import get_args

    from app.dashboard.catalogue import Signal
    from app.dashboard.fit import fitted_tiles

    signals = list(get_args(Signal))
    for scope in ("project", "program"):
        for size in range(len(signals)):
            for subset in combinations(signals, size):
                smaller = {t.key for t in fitted_tiles(scope, set(subset))}
                for extra_signal in signals:
                    if extra_signal in subset:
                        continue
                    bigger = {t.key for t in fitted_tiles(scope, {*subset, extra_signal})}
                    assert smaller <= bigger, (scope, subset, extra_signal)


def test_missing_for_names_the_tiles_each_absent_signal_holds_back():
    """The interesting half of fitting a board is what was left off - a person
    who uploads a second export should be able to see what it buys them."""
    from app.dashboard.fit import missing_for

    held = missing_for("project", {"tasks"})

    assert "baseline" in held
    assert "Schedule Variance" in held["baseline"]
    # A signal that is present is not reported as holding anything back.
    assert "tasks" not in held


def test_fitting_a_scope_with_nothing_ingested_still_places_something(session):
    """A canvas saying "nothing has been ingested yet" is more use than a blank
    one, and it is the same reasoning `get_dashboard` uses when it auto-creates."""
    from app.dashboard.service import apply_fitted

    out = apply_fitted(session, "project", "excel:Project:1:NOTHING")

    assert out.source == "fitted"
    assert out.tiles, "an unfitted project got a blank board"
    assert out.fit is not None and out.fit["placed"] == len(out.tiles)


# Splitting management work from delivery work


def test_the_new_schedule_tiles_are_in_the_catalogue():
    from app.dashboard.catalogue import CATALOGUE

    keys = {t.key for t in CATALOGUE}
    assert "management_vs_delivery" in keys
    assert "what_the_tracker_records" in keys


def test_both_new_tiles_read_a_bundle_that_already_exists():
    """The catalogue's rule: a tile is a slice of a bundle that exists, and
    adding one must not introduce a backend computation."""
    from app.dashboard.catalogue import CATALOGUE

    for key in ("management_vs_delivery", "what_the_tracker_records"):
        spec = next(t for t in CATALOGUE if t.key == key)
        assert spec.data_source == "gantt"
        assert spec.requires == ("tasks",)


def test_the_gantt_row_carries_the_issue_type():
    """Without it the board cannot tell management work from delivery work,
    and pools two populations that report themselves in opposite directions."""
    from app.api.schemas.gantt import GanttRow

    assert "phase" in GanttRow.model_fields


def test_the_disagreements_tile_needs_a_traceability_run():
    """Every project ingested from a tracker alone has no code to compare
    against, and must not be offered a code-versus-documents tile."""
    from app.dashboard.catalogue import CATALOGUE

    spec = next(t for t in CATALOGUE if t.key == "source_disagreements")
    assert spec.data_source == "traceability"
    assert spec.requires == ("traceability",)


def test_a_project_with_no_run_does_not_get_the_disagreements_tile(session):
    from app.dashboard.catalogue import for_scope
    from app.dashboard.fit import project_signals

    signals = project_signals(session, ["excel:Project:1:NOSUCH"])
    assert "traceability" not in signals
    offered = {
        t.key
        for t in for_scope("project")
        if set(t.requires) <= signals
    }
    assert "source_disagreements" not in offered


# Forgetting a source from Settings


def test_a_watched_source_carries_what_the_delete_endpoint_needs(session):
    """The Settings table deletes by `file_name`, which is served rather
    than split back out of `scope` - a file name may contain the `#` that
    key is built with, and a screen guessing at it would delete the wrong
    row or none."""
    from app.intelligence.pipeline import program_config

    bundle = program_config(session)
    for src in bundle.sources:
        if src.kind == "jira_replay":
            continue
        assert src.file_name, src.scope
        assert src.scope.startswith(src.file_name)


def test_only_a_stored_upload_is_offered_as_removable(session):
    """`delete_import` removes an `uploaded_sheets` row. A demo sheet is
    compiled into the image, so a button on it would always fail."""
    from sqlalchemy import select

    from app.intelligence.pipeline import program_config
    from app.models.uploads import UploadedSheet

    stored = set(session.scalars(select(UploadedSheet.file_name)).all())
    for src in program_config(session).sources:
        if src.kind == "jira_replay":
            continue
        assert src.removable == (src.file_name in stored), src.scope
