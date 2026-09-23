"""The staleness check, and the map it stands on.

The map is the part that can rot: `freshness.DERIVES_FROM` is written by
hand and the CLI is what actually reads the files, so the first test here
compares the two and fails when a stage starts reading something the map
does not know about.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tracelink import freshness as FR

CLI = Path(__file__).resolve().parent.parent / "tracelink" / "cli.py"


def _cli_dependencies() -> dict[str, set[str]]:
    """What each cmd_ function loads and saves, read out of the source."""
    src = CLI.read_text(encoding="utf-8")
    parts = re.split(r"\ndef (cmd_\w+)\(", src)
    out: dict[str, set[str]] = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        reads = set(re.findall(r'load_payload\(paths\["(\w+)"\]', body))
        writes = set(re.findall(r'A\.save\(\s*paths\["(\w+)"\]', body))
        for w in writes:
            out.setdefault(w, set()).update(reads - {w})
    return out


def test_the_map_matches_what_the_commands_read():
    actual = _cli_dependencies()
    for artifact, inputs in actual.items():
        if artifact not in FR.DERIVES_FROM:
            continue
        declared = set(FR.DERIVES_FROM[artifact])
        assert inputs <= declared, (
            f"{artifact} reads {sorted(inputs - declared)}, which "
            f"freshness.DERIVES_FROM does not list")


def test_every_derived_artifact_knows_how_to_rebuild_itself():
    assert set(FR.DERIVES_FROM) == set(FR.REBUILD)


def test_every_input_is_a_real_artifact_name():
    known = set(FR.DERIVES_FROM) | {"tickets", "corpus"}
    for artifact, inputs in FR.DERIVES_FROM.items():
        for i in inputs:
            assert i in known, f"{artifact} derives from unknown {i!r}"


def test_the_graph_has_no_cycle():
    seen: dict[str, int] = {}

    def visit(name: str, stack: tuple[str, ...]) -> None:
        assert name not in stack, f"cycle: {' -> '.join((*stack, name))}"
        if seen.get(name):
            return
        for i in FR.DERIVES_FROM.get(name, ()):
            visit(i, (*stack, name))
        seen[name] = 1

    for name in FR.DERIVES_FROM:
        visit(name, ())


# The check itself


def _run(tmp_path: Path, order: list[str]) -> Path:
    """Write each named artifact in order, with increasing mtimes."""
    import os
    import time

    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    for n, name in enumerate(order):
        p = FR._path(run, name)
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (time.time() + n, time.time() + n))
    return run


def test_a_run_written_in_dependency_order_is_fresh(tmp_path):
    run = _run(tmp_path, ["tickets", "corpus", "features", "candidates",
                          "verdicts", "explain"])
    assert FR.check(run) == []


def test_an_input_rewritten_after_its_output_is_stale(tmp_path):
    run = _run(tmp_path, ["tickets", "corpus", "candidates", "verdicts",
                          "explain", "verdicts"])
    stale = FR.check(run)
    assert [s.artifact for s in stale] == ["explain"]
    assert stale[0].newer == ["verdicts"]
    assert stale[0].paid is True
    assert stale[0].command == "explain"


def test_an_artifact_that_was_never_written_is_not_reported(tmp_path):
    run = _run(tmp_path, ["tickets", "corpus", "candidates", "verdicts"])
    assert [s.artifact for s in FR.check(run)] == []


def test_the_real_drift_this_was_written_for(tmp_path):
    """corpus fixed, verdicts re-run, explain and cost_report left behind."""
    run = _run(tmp_path, ["tickets", "explain", "cost_report", "corpus",
                          "features", "candidates", "verdicts"])
    stale = {s.artifact for s in FR.check(run)}
    assert "explain" in stale and "cost_report" in stale


def test_the_free_and_paid_halves_are_separated(tmp_path):
    run = _run(tmp_path, ["tickets", "corpus", "candidates", "verdicts",
                          "explain", "grounding", "corpus"])
    stale = FR.check(run)
    paid = {s.artifact for s in stale if s.paid}
    free = {s.artifact for s in stale if not s.paid}
    assert "verdicts" in paid
    assert "grounding" in free and "candidates" in free


def test_render_names_a_command_for_every_stale_artifact(tmp_path):
    run = _run(tmp_path, ["tickets", "corpus", "candidates", "verdicts",
                          "explain", "verdicts"])
    text = FR.render(FR.check(run), run)
    assert "COSTS MONEY" in text
    assert "explain" in text


def test_render_says_so_when_nothing_is_stale(tmp_path):
    run = _run(tmp_path, ["tickets", "corpus", "candidates"])
    text = FR.render(FR.check(run), run)
    assert "none" in text


# Content fingerprints — the reason mtime alone was not enough


def _save(run: Path, name: str, payload) -> Path:
    from tracelink import artifacts as A

    return A.save(FR._path(run, name), name, payload)


def test_save_records_what_an_artifact_was_built_from(tmp_path):
    import json

    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "features", {"rows": 1})
    meta = json.loads(FR._path(run, "features").read_text(encoding="utf-8"))["meta"]
    assert set(meta["inputs"]) == {"corpus"}
    assert meta["inputs"]["corpus"] == FR.fingerprint(FR._path(run, "corpus"))


def test_an_input_rebuilt_to_identical_content_is_not_a_change(tmp_path):
    """The cascade this module was rewritten to stop.

    Re-running a free stage to the same output used to make every artifact
    below it — including the paid ones — look stale.
    """
    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "features", {"rows": 1})
    assert FR.check(run) == []

    _save(run, "corpus", {"files": []})          # same content, new timestamp
    assert FR.check(run) == []


def test_an_input_rebuilt_to_different_content_is_a_change(tmp_path):
    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "features", {"rows": 1})
    _save(run, "corpus", {"files": ["app.py"]})

    stale = FR.check(run)
    assert [s.artifact for s in stale] == ["features"]
    assert stale[0].basis == "content"


def test_a_change_is_caught_even_when_an_intermediate_stage_is_unchanged(tmp_path):
    """`verdicts` notices a `corpus` change although `features` — which sits
    between them and which `verdicts` also reads — rebuilt to the same
    payload. It does not rely on propagation: `DERIVES_FROM` lists `corpus`
    as a direct input of `verdicts`, so it is compared against `corpus`."""
    run = tmp_path / "run"
    for name in ("tickets", "corpus", "features", "candidates", "verdicts"):
        _save(run, name, {"n": 1})
    assert FR.check(run) == []

    _save(run, "corpus", {"n": 2})
    _save(run, "features", {"n": 1})             # same payload, new stamp
    assert "verdicts" in {s.artifact for s in FR.check(run)}


def test_a_copied_run_still_passes_on_content(tmp_path):
    """mtime cannot survive a copy; payload fingerprints can."""
    import os
    import shutil
    import time

    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "features", {"rows": 1})
    copy = tmp_path / "copy"
    shutil.copytree(run, copy)
    # A copy can land in any order, so give corpus the newer timestamp —
    # the case that would fool a timestamp comparison.
    os.utime(FR._path(copy, "corpus"), (time.time() + 60, time.time() + 60))
    assert FR.check(copy) == []


def test_an_artifact_without_a_stamp_falls_back_to_timestamps(tmp_path):
    import json

    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "features", {"rows": 1})
    # Strip the stamp, as artifacts written before this existed have none.
    p = FR._path(run, "features")
    body = json.loads(p.read_text(encoding="utf-8"))
    body["meta"].pop("inputs")
    p.write_text(json.dumps(body), encoding="utf-8")
    _save(run, "corpus", {"files": []})
    # Two writes can land in the same filesystem tick, and this is the one
    # test that depends on the order being visible.
    import os
    import time

    now = time.time()
    os.utime(p, (now, now))
    os.utime(FR._path(run, "corpus"), (now + 60, now + 60))

    stale = FR.check(run)
    assert [s.artifact for s in stale] == ["features"]
    assert stale[0].basis == "mtime"
    assert "timestamp" in FR.render(stale, run)


# --accept: the one way to silence a row, and its limits


def test_accepting_an_artifact_records_its_current_inputs(tmp_path):
    import json
    import os
    import time

    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "verdicts", {"rows": 1})
    # Strip the stamp and force the timestamps, reproducing an artifact
    # written before stages recorded their inputs.
    p = FR._path(run, "verdicts")
    body = json.loads(p.read_text(encoding="utf-8"))
    body["meta"].pop("inputs")
    p.write_text(json.dumps(body), encoding="utf-8")
    now = time.time()
    os.utime(p, (now, now))
    os.utime(FR._path(run, "corpus"), (now + 60, now + 60))
    assert [s.artifact for s in FR.check(run)] == ["verdicts"]

    FR.accept(run, "verdicts", "2026-09-22")
    assert FR.check(run) == []

    meta = json.loads(p.read_text(encoding="utf-8"))["meta"]
    assert meta["inputs_accepted"] == "2026-09-22"
    assert meta["inputs"]["corpus"] == FR.fingerprint(FR._path(run, "corpus"))


def test_accepting_does_not_silence_a_later_real_change(tmp_path):
    """The point: accept means 'current now', not 'never check again'."""
    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "verdicts", {"rows": 1})
    FR.accept(run, "verdicts", "2026-09-22")
    assert FR.check(run) == []

    _save(run, "corpus", {"files": ["app.py"]})
    stale = FR.check(run)
    assert [s.artifact for s in stale] == ["verdicts"]
    assert stale[0].basis == "content"


def test_accepting_preserves_the_rest_of_the_meta(tmp_path):
    import json

    from tracelink import artifacts as A

    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    A.save(FR._path(run, "verdicts"), "verdicts", [], model="claude-opus-5")
    FR.accept(run, "verdicts", "2026-09-22")
    meta = json.loads(FR._path(run, "verdicts").read_text(encoding="utf-8"))["meta"]
    assert meta["model"] == "claude-opus-5"


def test_accepting_keeps_the_artifact_readable(tmp_path):
    from tracelink import artifacts as A

    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    A.save(FR._path(run, "verdicts"), "verdicts", [{"uid": "T1"}])
    FR.accept(run, "verdicts", "2026-09-22")
    assert A.load_payload(FR._path(run, "verdicts"), "verdicts") == [{"uid": "T1"}]


def test_meta_churn_does_not_invalidate_anything_downstream(tmp_path):
    """Accepting an artifact, or restamping it, changes its meta and must
    not make the artifacts built on it look stale. Hashing the whole file
    instead of the payload got this wrong."""
    run = tmp_path / "run"
    for name in ("tickets", "corpus", "candidates", "features", "verdicts",
                 "grounding", "links", "shadow"):
        _save(run, name, {"n": 1})
    assert FR.check(run) == []

    FR.accept(run, "verdicts", "2026-09-22")
    assert FR.check(run) == []


def test_a_payload_change_is_still_caught_when_meta_is_identical(tmp_path):
    run = tmp_path / "run"
    _save(run, "corpus", {"files": []})
    _save(run, "features", {"rows": 1})
    _save(run, "corpus", {"files": ["app.py"]})
    assert [s.artifact for s in FR.check(run)] == ["features"]
