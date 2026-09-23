"""Seeding the runs volume, and the one thing it must never do.

`TRACELINK_RUNS` points at a volume rather than at the image's own
`traceability_runs/`, because a Fly mount covers its destination: pointing the
volume at the image directory would hide the baked runs instead of merging
with them, and the Traceability page would report "Not traced" for a project
whose verdicts are in the image a few inches away.

So the baked runs are copied across when the volume is missing them. The rule
that matters is the second test here: a run this server *produced* must never
be replaced by the image's copy of the same name on a later deploy. That would
silently swap real findings for the demo's, and nothing on the page would say
so - the artifacts are well-formed either way.
"""

from __future__ import annotations

import json

import pytest

from scripts.serve import seed_runs_volume


@pytest.fixture
def baked(tmp_path, monkeypatch):
    """Stand in for the image's `traceability_runs/`."""
    source = tmp_path / "image_runs"
    (source / "demo").mkdir(parents=True)
    (source / "demo" / "run.json").write_text(
        json.dumps({"project_id": "demo", "origin": "image"}), encoding="utf-8",
    )
    (source / "demo" / "verdicts.json").write_text("{}", encoding="utf-8")

    import app.config

    monkeypatch.setattr(app.config, "REPO_ROOT", tmp_path)
    # `seed_runs_volume` imports REPO_ROOT from the module at call time, so
    # patching the attribute is enough - but the name it looks for is the
    # directory beside it.
    (tmp_path / "traceability_runs").mkdir(exist_ok=True)
    for child in source.rglob("*"):
        rel = child.relative_to(source)
        target = tmp_path / "traceability_runs" / rel
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.write_bytes(child.read_bytes())
    return tmp_path / "traceability_runs"


def test_baked_runs_are_copied_onto_an_empty_volume(baked, tmp_path, monkeypatch):
    volume = tmp_path / "data" / "runs"
    monkeypatch.setenv("TRACELINK_RUNS", str(volume))

    messages = seed_runs_volume()

    assert (volume / "demo" / "verdicts.json").exists()
    assert any("demo" in m for m in messages)


def test_a_run_produced_here_is_never_overwritten(baked, tmp_path, monkeypatch):
    """The defect this ordering exists to prevent.

    A blanket copy on every boot would replace a real run with the image's
    demo of the same name, and both are well-formed JSON - so the page would
    show the demo's verdicts under the real project's name and never mention
    that anything had been swapped.
    """
    volume = tmp_path / "data" / "runs"
    (volume / "demo").mkdir(parents=True)
    (volume / "demo" / "run.json").write_text(
        json.dumps({"project_id": "demo", "origin": "this server"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("TRACELINK_RUNS", str(volume))

    seed_runs_volume()

    kept = json.loads((volume / "demo" / "run.json").read_text(encoding="utf-8"))
    assert kept["origin"] == "this server"
    # And the image's other artifacts were not dribbled in beside it, which
    # would leave one run directory holding two runs' findings.
    assert not (volume / "demo" / "verdicts.json").exists()


def test_nothing_happens_without_a_configured_runs_directory(baked, monkeypatch):
    """A local run with no `TRACELINK_RUNS` reads the image's directory
    directly; there is nowhere to seed and nothing to do."""
    monkeypatch.delenv("TRACELINK_RUNS", raising=False)
    assert seed_runs_volume() == []


def test_seeding_into_the_source_itself_is_a_no_op(baked, monkeypatch):
    """Guards the deployment that points `TRACELINK_RUNS` back at the image.

    Without the check this would copy each run directory into itself.
    """
    monkeypatch.setenv("TRACELINK_RUNS", str(baked))
    assert seed_runs_volume() == []


def test_an_unwritable_volume_is_reported_rather_than_raised(baked, tmp_path,
                                                             monkeypatch):
    """A server that cannot seed its runs still serves every other page.

    This is the shape of the failure when the volume is not actually mounted,
    so the message has to name the next command rather than only the errno.
    """
    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "data" / "runs"))

    import pathlib

    def _no(*a, **k):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(pathlib.Path, "mkdir", _no)

    messages = seed_runs_volume()
    assert messages and "cannot write" in messages[0]
