"""The one-command path from a project's inputs to a readable page.

The point of the module is that the *order* stops being folklore. These
tests pin the dependencies that are real but invisible, and the two
promises the pipeline makes to anybody running it on a new project: it
never spends money, and a missing input is a named gap rather than a
crash.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink import pipeline as PL

FULL = dict(run="runs/x", export="b.xlsx", repo="repo", docs="docs",
            project="P", project_id="p:1", done_status="Release it")


def names(plan: PL.Plan) -> list[str]:
    return [s.name for s in plan.steps]


def skipped(plan: PL.Plan) -> dict[str, str]:
    return dict(plan.skipped)


# --------------------------------------------------------------------------
# Order
# --------------------------------------------------------------------------

def test_the_plan_is_in_dependency_order():
    n = names(PL.build_plan(**FULL))
    assert n.index("tickets") < n.index("retrieve")
    assert n.index("corpus") < n.index("features")
    assert n.index("features") < n.index("retrieve"), \
        "the doc matcher needs the feature map before retrieval runs"
    assert n.index("progress") < n.index("reconcile")
    assert n.index("retrieve") < n.index("reconcile"), \
        "reconcile joins work items to tickets through the candidate sets"


def test_every_free_stage_is_present_when_every_input_is():
    n = names(PL.build_plan(**FULL))
    for stage in ("diagnose", "tickets", "corpus", "features", "progress",
                  "retrieve", "gates", "effort", "shadow", "reconcile"):
        assert stage in n


def test_no_paid_stage_is_ever_planned():
    """The promise that makes this safe to hand to somebody else.

    A pipeline that spends money when you asked it to read a repository
    is a trap, so `translate`, `adjudicate` and `explain` are named in the
    output and never invoked.
    """
    argv = [a for s in PL.build_plan(**FULL).steps for a in s.argv]
    for paid in ("translate", "adjudicate", "explain"):
        assert paid not in argv


def test_reporting_stages_can_be_left_out():
    with_reports = names(PL.build_plan(**FULL))
    without = names(PL.build_plan(**FULL, reports=False))
    assert "map" in with_reports and "map" not in without
    assert set(without) < set(with_reports)


# --------------------------------------------------------------------------
# A missing input is a gap, not a crash
# --------------------------------------------------------------------------

def test_no_docs_tree_leaves_the_tracker_half_running():
    plan = PL.build_plan(**{**FULL, "docs": None})
    assert "retrieve" in names(plan) and "tickets" in names(plan)
    assert "features" not in names(plan) and "reconcile" not in names(plan)
    assert "no --docs" in skipped(plan)["features/progress"]


def test_no_repo_says_so_rather_than_planning_a_corpus():
    plan = PL.build_plan(**{**FULL, "repo": None})
    assert "corpus" not in names(plan)
    assert "no --repo" in skipped(plan)["corpus"]


def test_docs_without_a_repo_cannot_resolve_anything():
    plan = PL.build_plan(**{**FULL, "repo": None})
    assert "needs --repo" in skipped(plan)["features"]


def test_a_missing_done_status_skips_reconcile_and_says_why():
    """Which tracker status means finished is a decision, not a fact."""
    plan = PL.build_plan(**{**FULL, "done_status": None})
    assert "reconcile" not in names(plan)
    assert "will not guess" in skipped(plan)["reconcile"]


def test_nothing_supplied_plans_nothing_and_explains_itself():
    plan = PL.build_plan(run="r", export=None, repo=None, docs=None,
                         project=None, project_id=None, done_status=None)
    assert plan.steps == []
    assert len(plan.skipped) >= 3


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def test_a_required_failure_stops_the_run():
    plan = PL.build_plan(**FULL)
    seen: list[str] = []

    def invoke(argv):
        seen.append(argv[2])
        return 1 if argv[2] == "tickets" else 0

    failures = PL.run_plan(plan, invoke, lambda s: None)
    assert ("tickets", 1) in failures
    assert "retrieve" not in seen, "a stage whose input never arrived must not run"


def test_a_reporting_failure_does_not_stop_the_run():
    plan = PL.build_plan(**FULL)
    seen: list[str] = []

    def invoke(argv):
        seen.append(argv[2])
        return 1 if argv[2] == "gates" else 0

    failures = PL.run_plan(plan, invoke, lambda s: None)
    assert ("gates", 1) in failures
    assert "reconcile" in seen, "gates feeds nothing, so it cannot block anything"


def test_a_stage_that_raises_is_caught_and_reported():
    plan = PL.build_plan(**FULL)

    def invoke(argv):
        if argv[2] == "corpus":
            raise RuntimeError("analyser exploded")
        return 0

    failures = PL.run_plan(plan, invoke, lambda s: None)
    assert failures and failures[0][0] == "corpus"


def test_a_subcommand_calling_sys_exit_is_treated_as_its_code():
    plan = PL.build_plan(**FULL)

    def invoke(argv):
        if argv[2] == "shadow":
            raise SystemExit(2)
        return 0

    failures = PL.run_plan(plan, invoke, lambda s: None)
    assert ("shadow", 2) in failures


# --------------------------------------------------------------------------
# Handing it to the product
# --------------------------------------------------------------------------

def test_staging_copies_what_exists_and_names_what_does_not(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    for name in ("run", "tickets", "corpus", "progress"):
        (run / f"{name}.json").write_text("{}", encoding="utf-8")

    copied, missing = PL.stage_for_product(run, tmp_path / "into")
    assert set(copied) == {"run.json", "tickets.json", "corpus.json",
                           "progress.json"}
    assert "verdicts.json" in missing
    assert (tmp_path / "into" / "tickets.json").exists()


def test_the_feature_map_is_not_shipped_to_the_product():
    """3.8 MB only the retrieval and reconciliation stages read."""
    assert "features" not in PL.PRODUCT_ARTIFACTS


def test_staging_into_a_directory_that_does_not_exist_creates_it(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text("{}", encoding="utf-8")
    copied, _ = PL.stage_for_product(run, tmp_path / "a" / "b")
    assert copied == ["run.json"]
