"""Per-workspace mode resolution: each workspace keeps its own routing /
auto-run mode, falling back to the global default when unset.

Exercises AppContext.project_routing_mode / set_project_routing_mode /
project_confirm_commands / set_project_auto_run against a temp projects dir.
"""
from __future__ import annotations

import copy

import pytest

from cowork_local.config import DEFAULT_CONFIG, AppConfig
from cowork_local.core import projects as projects_mod
from cowork_local.core.projects import Project, new_project, save_project
from cowork_local.state import AppContext


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    # Redirect the projects store to a temp dir so load/save hit tmp, not $HOME.
    monkeypatch.setattr(projects_mod, "PROJECTS_DIR", tmp_path / "projects")
    data = copy.deepcopy(DEFAULT_CONFIG)
    cfg = AppConfig(data=data, path=tmp_path / "config.json")
    return AppContext(cfg)


def _mk(ctx, name):
    return new_project(name, directory=projects_mod.PROJECTS_DIR)


def test_defaults_follow_global_when_no_override(ctx):
    a = _mk(ctx, "Alpha")
    ctx.active_project_id = a.project_id
    # Global default switch_mode is "off".
    assert ctx.project_routing_mode("cowork") == "off"
    # Change the GLOBAL default → project with no override follows it.
    ctx.config.data["routing"]["switch_mode"] = "auto"
    assert ctx.project_routing_mode("cowork") == "auto"


def test_per_workspace_routing_is_isolated(ctx):
    a = _mk(ctx, "Alpha")
    b = _mk(ctx, "Beta")

    ctx.active_project_id = a.project_id
    ctx.set_project_routing_mode("cowork", "auto")
    assert ctx.project_routing_mode("cowork") == "auto"

    # Switching to workspace B must NOT see A's override (falls back to global).
    ctx.active_project_id = b.project_id
    assert ctx.project_routing_mode("cowork") == "off"

    # B sets its own, independently.
    ctx.set_project_routing_mode("cowork", "manual")
    assert ctx.project_routing_mode("cowork") == "manual"

    # A is unchanged.
    ctx.active_project_id = a.project_id
    assert ctx.project_routing_mode("cowork") == "auto"


def test_per_surface_isolated_within_a_workspace(ctx):
    a = _mk(ctx, "Alpha")
    ctx.active_project_id = a.project_id
    ctx.set_project_routing_mode("cowork", "auto")
    ctx.set_project_routing_mode("ai_edit", "manual")
    # co4e untouched → global default.
    assert ctx.project_routing_mode("cowork") == "auto"
    assert ctx.project_routing_mode("ai_edit") == "manual"
    assert ctx.project_routing_mode("co4e") == "off"


def test_routing_mode_persists_to_disk(ctx):
    a = _mk(ctx, "Alpha")
    ctx.active_project_id = a.project_id
    ctx.set_project_routing_mode("co4e", "auto")
    # Reload the project from disk — the override survived.
    reloaded = projects_mod.load_project(a.project_id, projects_mod.PROJECTS_DIR)
    assert reloaded.routing_modes.get("co4e") == "auto"


def test_auto_run_per_workspace(ctx):
    a = _mk(ctx, "Alpha")
    b = _mk(ctx, "Beta")

    # Global default: cowork_confirm_commands is False → auto-run (no confirm).
    ctx.active_project_id = a.project_id
    assert ctx.project_confirm_commands() is False
    assert ctx.project_auto_run() is True

    # A: require confirm (auto_run=False). B stays on the global default.
    ctx.set_project_auto_run(False)
    assert ctx.project_confirm_commands() is True

    ctx.active_project_id = b.project_id
    assert ctx.project_confirm_commands() is False  # B unaffected by A


def test_auto_run_none_follows_global(ctx):
    a = _mk(ctx, "Alpha")
    ctx.active_project_id = a.project_id
    # Turn the GLOBAL confirm setting on; project override is None → follows it.
    ctx.config.data["agent_security"]["cowork_confirm_commands"] = True
    assert ctx.project_confirm_commands() is True
    # Explicit per-project auto-run overrides the global.
    ctx.set_project_auto_run(True)   # auto-approve
    assert ctx.project_confirm_commands() is False


def test_no_active_project_uses_global(ctx):
    # active_project_id points at a non-existent project → global fallback.
    ctx.active_project_id = "does-not-exist"
    ctx.config.data["routing"]["switch_mode"] = "manual"
    assert ctx.project_routing_mode("cowork") == "manual"
    # Setting a mode with no real project writes the GLOBAL setting.
    ctx.set_project_routing_mode("cowork", "auto")
    assert ctx.config.routing_mode_for("cowork") == "auto"


def test_project_dataclass_defaults():
    # New fields have safe defaults and round-trip through asdict/load.
    p = Project(project_id="x", name="X")
    assert p.routing_modes == {}
    assert p.auto_run is None
