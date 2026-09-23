"""One command from a project's inputs to a page somebody can read.

Recreating a run means twelve commands in an order with dependencies that
are real but invisible: `reconcile` needs `retrieve`'s candidate sets and
`progress`'s work items, `gates` needs a corpus, `effort` needs the docs
tree twice for different reasons. Nobody reconstructs that from a README
six months later, and a stage silently skipped produces a page that is
wrong rather than a page that is missing.

So the order lives here, as data, and each step names what it needs. The
steps are the real CLI commands re-entered through `main`, not a parallel
implementation — a shim would drift from the commands it stands for, and
the drift would show up as a run nobody could reproduce by hand.

Two rules it keeps from the rest of the pipeline:

* **Free by default.** Everything here runs without an API key. The paid
  stages — `translate`, `adjudicate`, `explain` — are named at the end
  with the command to run and never invoked, because a pipeline that
  spends money when you asked it to read a repository is a trap.
* **A missing input is a named gap, not a crash.** No docs tree means the
  documentation stages are skipped and said to be skipped; the tracker
  half still runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: The artifacts ProjectPulse's traceability page reads. `features.json` is
#: deliberately absent: 3.8 MB that only retrieval and reconciliation read,
#: and free to rebuild from the corpus and the docs tree.
PRODUCT_ARTIFACTS = [
    "run", "tickets", "corpus", "candidates", "verdicts", "grounding",
    "links", "explain", "shadow", "diagnosis", "translations",
    "progress", "reconciliation", "gates", "cohorts",
    "governance",
]


@dataclass
class Step:
    """One stage, and the condition under which it is worth running."""

    name: str
    argv: list[str]
    #: What this step needs that the caller may not have supplied.
    needs: str = ""
    #: True when a non-zero exit should stop the run rather than be noted.
    required: bool = True
    #: Stages that report to stdout and write nothing a later stage reads.
    reporting: bool = False


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def build_plan(run: str, export: str | None, repo: str | None,
               docs: str | None, project: str | None, project_id: str | None,
               done_status: str | None, reports: bool = True,
               ref: str = "") -> Plan:
    """Every free stage, in dependency order, for the inputs actually given."""
    plan = Plan()
    R = ["--run", run]

    def add(name, *argv, needs="", required=True, reporting=False):
        plan.steps.append(Step(name=name, argv=[*R, *argv], needs=needs,
                               required=required, reporting=reporting))

    if export:
        add("diagnose", "diagnose", export,
            *(["--project", project] if project else []),
            required=False, reporting=True)
        add("tickets", "tickets", export,
            *(["--project", project] if project else []),
            *(["--project-id", project_id] if project_id else []))
    else:
        plan.skipped.append(("tickets", "no --export, so there is no backlog"))

    if repo:
        # `repo` may be a checkout or a git URL; `--ref` only means anything
        # for the second. Passed through so one command refreshes a run
        # against a named branch without anybody cloning by hand.
        add("corpus", "corpus", repo, *(["--ref", ref] if ref else []))
    else:
        plan.skipped.append(("corpus", "no --repo, so there is no code side"))

    # The documentation half. Optional, and everything downstream of it
    # degrades to the tracker-only behaviour when it is absent.
    if docs and repo:
        add("features", "features", "--docs", docs)
        add("progress", "progress", "--docs", docs)
    elif docs:
        plan.skipped.append(("features", "needs --repo to resolve claims against"))
    else:
        plan.skipped.append(
            ("features/progress", "no --docs, so the documentation half is skipped"))

    if export and repo:
        add("retrieve", "retrieve")

    if docs and repo:
        add("gates", "gates", required=False)
        add("effort", "effort", "--docs", docs, required=False)
        if reports:
            add("map", "map", required=False, reporting=True)
            add("drift", "drift", required=False, reporting=True)

    # Not `reporting`: it writes an artifact the product page reads. Needs
    # only the export, because the seam is in the sheet, not in the code.
    if export:
        add("cohorts", "cohorts", required=False)
        # Governance reads the export directly: its rows are the keyed PM
        # tickets that `tickets` drops, so there is no artifact to read.
        add("governance", "governance", export,
            *(["--project", project] if project else []),
            *(["--docs", docs] if docs else []),
            *(["--done-status", done_status] if done_status else []),
            required=False)

    if export and repo:
        add("shadow", "shadow", required=False)

    if docs and repo and export:
        if done_status:
            add("reconcile", "reconcile", "--done-status", done_status,
                required=False)
        else:
            plan.skipped.append((
                "reconcile",
                "no --done-status, and which tracker status means finished is "
                "a decision this pipeline will not guess"))

    return plan


def run_plan(plan: Plan, invoke: Callable[[list[str]], int],
             out: Callable[[str], None]) -> list[tuple[str, int]]:
    """Run each step. Returns the steps that failed, in order."""
    failures: list[tuple[str, int]] = []
    for step in plan.steps:
        out(f"\n=== {step.name} " + "=" * max(0, 56 - len(step.name)))
        try:
            code = invoke(step.argv)
        except SystemExit as exc:          # argparse inside a subcommand
            code = int(exc.code or 0)
        except Exception as exc:           # noqa: BLE001 - a stage, not us
            out(f"  {step.name} raised: {exc}")
            code = 1
        if code:
            failures.append((step.name, code))
            if step.required:
                out(f"\n{step.name} failed and later stages depend on it; "
                    f"stopping here.")
                break
            out(f"  ({step.name} did not complete; it feeds nothing further)")
    return failures


def stage_for_product(run: Path, into: Path) -> tuple[list[str], list[str]]:
    """Copy the artifacts the page reads. Returns (copied, missing).

    The page names every absence itself, so a partial copy is a usable
    page rather than a broken one — which is why this reports what was
    missing instead of refusing.
    """
    import shutil

    into.mkdir(parents=True, exist_ok=True)
    copied, missing = [], []
    for name in PRODUCT_ARTIFACTS:
        src = run / f"{name}.json"
        if src.exists():
            shutil.copy2(src, into / src.name)
            copied.append(src.name)
        else:
            missing.append(f"{name}.json")
    return copied, missing
