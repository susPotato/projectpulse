"""Running the pipeline on this server, rather than only reading its output.

The interesting failures here are not in the stages - those are `tracelink`'s
own and tested there. They are in the seam: a command assembled without the
one argument that lets the page find the run, a second run started on a
machine that cannot take it, and a worker that loses the output explaining why
a stage stopped. So that is what this covers.

`_worker` is exercised against real subprocesses rather than a mock. It exists
to turn a child process's stdout and exit code into something a page can show,
and a mocked Popen would be testing the mock.
"""

from __future__ import annotations

import sys
import time

import pytest

from app import traceability_run as TR


@pytest.fixture(autouse=True)
def _no_run_in_flight():
    """Each test starts with no run recorded, and leaves none behind.

    Module-level state is what `state()` reads, so a test that starts a run
    would otherwise decide whether the next one sees a busy server.
    """
    TR._current = None
    yield
    TR._current = None


def _drain(record, timeout=30.0):
    """Wait for a record the worker is still writing to."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if record.get("status") != "running":
            return record
        time.sleep(0.02)
    raise AssertionError(f"worker did not finish within {timeout}s: {record}")


def _record():
    from collections import deque

    return {"status": "running", "log": deque(maxlen=TR.LOG_LINES), "stage": "",
            "command": ""}


def _work(record, *argv):
    """Drive `_worker` with a single command, named the way a run names it.

    The worker takes a list of (name, argv) pairs because a run is the free
    pipeline followed, optionally, by the stages that call a model. Every test
    below is about one command's behaviour, so the list shape is noise in them
    and lives here instead.
    """
    TR._worker([("pipeline", list(argv))], {}, record)


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------

def _commands(**over):
    from pathlib import Path

    args = dict(run_dir=Path("/runs/x"), export=Path("/tmp/e.xlsx"),
                repo=Path("/tmp/repo"), docs=Path("/tmp/repo/docs"),
                project_id="excel:Project:1:HRMS", project_filter="",
                done_status="", verdicts=False)
    args.update(over)
    return TR._commands(**args)


def _argv(**over):
    """The free pipeline's own command line.

    A run is a list of commands now - the free pipeline, then optionally the
    stages that call a model - and every assertion below is about the first
    of them, which is the one that exists unconditionally.
    """
    commands = _commands(**over)
    assert commands[0][0] == "pipeline"
    return commands[0][1]


def test_the_command_carries_the_project_id():
    """Without `--project-id` the run completes and the page cannot find it.

    `tracelink_view.run_for_project` matches a run to a project by the id in
    its manifest. A run produced without one is a directory full of correct
    artifacts that every page reports as "Not traced" - the most expensive
    way available to produce nothing.
    """
    argv = _argv()
    assert "--project-id" in argv
    assert argv[argv.index("--project-id") + 1] == "excel:Project:1:HRMS"


def test_the_command_runs_the_pipeline_not_a_single_stage():
    argv = _argv()
    assert "-m" in argv and argv[argv.index("-m") + 1] == "tracelink"
    assert "pipeline" in argv
    # `--run` is a global option, so it has to come before the subcommand or
    # argparse hands it to the wrong parser.
    assert argv.index("--run") < argv.index("pipeline")


def test_reporting_only_stages_are_skipped():
    """`map` and `drift` print and write nothing any page reads."""
    assert "--quiet" in _argv()


def test_optional_arguments_are_omitted_when_empty():
    """An empty `--project` is not the same as no `--project`.

    Passed as an empty string it filters the export down to the rows whose
    project name is "", which is none of them - a run about nothing, reported
    as a success.
    """
    bare = _argv()
    assert "--project" not in bare
    assert "--done-status" not in bare

    filled = _argv(project_filter="CoWorkLocal", done_status="Done,Closed")
    assert filled[filled.index("--project") + 1] == "CoWorkLocal"
    assert filled[filled.index("--done-status") + 1] == "Done,Closed"


def test_the_stages_that_cost_money_are_off_unless_asked_for():
    """The default is the free pipeline and nothing else.

    `translate`, `adjudicate` and `explain` call a model once per ticket.
    Reaching one of them from a button nobody warned about is how a person
    finds out what this costs by being billed for it, so the flag is opt-in
    and `build_plan` upstream stays free-only.
    """
    assert [name for name, _ in _commands()] == ["pipeline"]


def test_asking_for_verdicts_adds_them_after_the_pipeline_in_order():
    """`adjudicate` reads what `pipeline` wrote, so the order is a dependency."""
    names = [name for name, _ in _commands(verdicts=True)]
    assert names == ["pipeline", "adjudicate", "explain", "verify"]


def test_adjudication_covers_every_ticket_not_the_first_twelve():
    """The CLI defaults to `--limit 12`, which is right at a prompt and wrong
    here: a page reporting twelve verdicts and 161 unanalysed tickets is a
    worse answer than the cache already holds."""
    argv = dict(_commands(verdicts=True))["adjudicate"]
    assert "--all" in argv
    assert "--limit" not in argv
    # The prose fallback for a ticket whose candidate set comes back empty.
    # Compared as a `Path`, because the separator differs by platform and this
    # is an assertion about which directory, not about how it is spelled.
    from pathlib import Path

    assert Path(argv[argv.index("--docs") + 1]) == Path("/tmp/repo/docs")


def test_every_command_names_the_run_before_its_subcommand():
    """`--run` is a global option. After the subcommand argparse hands it to
    the wrong parser, which is a run written somewhere nobody reads."""
    for name, argv in _commands(verdicts=True):
        assert "--run" in argv, name
        subcommand = argv.index(name)
        assert argv.index("--run") < subcommand, name


def test_the_verdict_flag_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv(TR.VERDICTS_ENV, raising=False)
    assert TR.verdict_stages_enabled() is False
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv(TR.VERDICTS_ENV, truthy)
        assert TR.verdict_stages_enabled() is True, truthy
    monkeypatch.setenv(TR.VERDICTS_ENV, "0")
    assert TR.verdict_stages_enabled() is False


def test_a_failing_command_stops_the_ones_after_it():
    """Carrying on would report a later stage's complaint about a missing
    artifact instead of the real failure, which is the harder to act on."""
    record = _record()
    TR._worker(
        [("pipeline", [sys.executable, "-c", "import sys; sys.exit(2)"]),
         ("adjudicate", [sys.executable, "-c", "print('should not run')"])],
        {}, record,
    )
    assert record["status"] == "failed"
    assert record["exit_code"] == 2
    assert not any("should not run" in line for line in record["log"])
    # Names the command that stopped, not just "the pipeline" - with four of
    # them a bare exit code does not say which.
    assert "pipeline" in record["error"]


def test_every_command_runs_when_each_one_succeeds():
    record = _record()
    TR._worker(
        [("pipeline", [sys.executable, "-c", "print('one')"]),
         ("adjudicate", [sys.executable, "-c", "print('two')"])],
        {}, record,
    )
    assert record["status"] == "done"
    assert record["exit_code"] == 0
    assert {"one", "two"} <= set(record["log"])
    # Cleared at the end: a finished run still naming a command reads as stuck.
    assert record["command"] == ""


@pytest.mark.parametrize("project_id,expected", [
    ("excel:Project:1:HRMS", "excel-project-1-hrms"),
    ("excel:Project:upload:工数管理", "excel-project-upload"),
    (":::", "run"),
])
def test_run_directories_are_named_without_path_metacharacters(project_id, expected):
    """A colon is legal in a POSIX path and is not on Windows.

    The same code makes and reads these directories, so a name that only works
    on the container is a run a developer cannot reproduce locally.
    """
    assert TR._slug(project_id) == expected


# --------------------------------------------------------------------------
# The worker
# --------------------------------------------------------------------------

def test_the_worker_keeps_output_and_reports_success():
    record = _record()
    _work(record, sys.executable, "-c", "print('=== corpus'); print('172 files')")
    assert record["status"] == "done"
    assert record["exit_code"] == 0
    assert "172 files" in list(record["log"])
    # The banner is consumed as progress and the stage cleared when the run
    # ends - a finished run that still says "corpus" reads as a stuck one.
    assert record["stage"] == ""


def test_a_failing_stage_is_reported_with_its_output():
    """The exit code alone does not say which stage stopped or what it wanted."""
    record = _record()
    _work(record, sys.executable, "-c",
          "import sys; print('no docs tree at docs/'); sys.exit(3)")
    assert record["status"] == "failed"
    assert record["exit_code"] == 3
    assert "no docs tree at docs/" in list(record["log"])
    assert record["error"]


def test_stderr_is_kept_too():
    """A stage that dies writes to stderr, and that is the useful half."""
    record = _record()
    _work(record, sys.executable, "-c",
          "import sys; print('Traceback (most recent call last)', file=sys.stderr); "
          "sys.exit(1)")
    assert any("Traceback" in line for line in record["log"])


def test_a_command_that_cannot_start_is_a_failure_not_an_exception():
    """The worker runs in a thread; an exception there is lost.

    Reported on the record instead, because the only way anybody learns about
    this is by polling for it.
    """
    record = _record()
    _work(record, "definitely-not-a-real-binary-93f2")
    assert record["status"] == "failed"
    assert "could not start" in record["error"]


def test_only_the_tail_of_a_long_run_is_kept():
    """Twelve stages on a real backlog produce far more than a page wants."""
    record = _record()
    _work(record, sys.executable, "-c",
          f"[print(i) for i in range({TR.LOG_LINES + 500})]")
    log = list(record["log"])
    assert len(log) == TR.LOG_LINES
    # The tail, not the head: a failing stage names what it wanted at the end.
    assert log[-1] == str(TR.LOG_LINES + 499)


# --------------------------------------------------------------------------
# Starting a run
# --------------------------------------------------------------------------

def test_a_run_without_a_backlog_is_refused(tmp_path):
    with pytest.raises(TR.RunUnavailable) as exc:
        TR.start(project_id="p", repo_tree=tmp_path, docs_dir=tmp_path,
                 export_bytes=b"")
    assert "backlog export" in str(exc.value)


def test_a_second_run_is_refused_while_one_is_in_flight(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "runs"))
    # A command that outlives the assertion, so the first run is genuinely
    # still running when the second is attempted.
    monkeypatch.setattr(
        TR, "_commands",
        lambda **kw: [("pipeline",
                      [sys.executable, "-c", "import time; time.sleep(5)"])],
    )

    first = TR.start(project_id="a", repo_tree=tmp_path, docs_dir=tmp_path,
                     export_bytes=b"x")
    assert first["status"] == "running"

    with pytest.raises(TR.RunBusy) as exc:
        TR.start(project_id="b", repo_tree=tmp_path, docs_dir=tmp_path,
                 export_bytes=b"x")
    assert "'a'" in str(exc.value)


def test_state_reports_a_finished_run(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "runs"))
    monkeypatch.setattr(
        TR, "_commands",
        lambda **kw: [("pipeline", [sys.executable, "-c", "print('done')"])],
    )

    TR.start(project_id="excel:Project:1:HRMS", repo_tree=tmp_path,
             docs_dir=tmp_path, export_bytes=b"x", export_name="backlog.xlsx")
    _drain(TR._current)

    payload = TR.state()
    assert payload["running"] is False
    assert payload["run"]["status"] == "done"
    assert payload["run"]["project_id"] == "excel:Project:1:HRMS"
    assert payload["run"]["export_name"] == "backlog.xlsx"
    # Serialisable: this goes through a JSON response, and a deque does not.
    assert isinstance(payload["run"]["log"], list)


def test_state_is_empty_before_any_run():
    assert TR.state() == {"running": False, "run": None}


def test_the_export_is_cleaned_up_after_the_run(tmp_path, monkeypatch):
    """The temp workbook outlives the call but must not outlive the run."""
    monkeypatch.setenv("TRACELINK_RUNS", str(tmp_path / "runs"))
    seen: list[str] = []

    def _capture(**kw):
        seen.append(str(kw["export"]))
        return [("pipeline", [sys.executable, "-c", "print('ok')"])]

    monkeypatch.setattr(TR, "_commands", _capture)

    TR.start(project_id="p", repo_tree=tmp_path, docs_dir=tmp_path,
             export_bytes=b"x")
    _drain(TR._current)

    from pathlib import Path
    assert seen, "the export path was never passed to the command"
    assert not Path(seen[0]).exists(), f"{seen[0]} was left behind"


def test_the_run_directory_is_created_under_the_runs_root(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setenv("TRACELINK_RUNS", str(root))
    monkeypatch.setattr(
        TR, "_commands",
        lambda **kw: [("pipeline", [sys.executable, "-c", "print('ok')"])],
    )

    started = TR.start(project_id="excel:Project:1:HRMS", repo_tree=tmp_path,
                       docs_dir=tmp_path, export_bytes=b"x")
    _drain(TR._current)

    assert started["run"] == str(root / "excel-project-1-hrms")
    assert (root / "excel-project-1-hrms").is_dir()


# --------------------------------------------------------------------------
# Where runs live
# --------------------------------------------------------------------------

def test_the_runner_and_the_page_agree_on_where_runs_live(tmp_path, monkeypatch):
    """Writing somewhere the page does not read is a silent, expensive bug.

    The run completes, the pipeline reports success, minutes of CPU are
    spent - and the page still says "Not traced", because the runner used its
    fallback and the viewer only honoured the variable. Locking them together
    here because nothing else would notice: both halves are individually
    correct.
    """
    from app.api import tracelink_view

    configured = tmp_path / "runs"
    configured.mkdir()
    monkeypatch.setenv("TRACELINK_RUNS", str(configured))
    assert TR.runs_root() == tracelink_view.runs_root()

    monkeypatch.delenv("TRACELINK_RUNS", raising=False)
    assert TR.runs_root() == tracelink_view.runs_root(), (
        "with no TRACELINK_RUNS the runner writes to its fallback and the "
        "page reads from its own - a run that completes and cannot be seen."
    )
