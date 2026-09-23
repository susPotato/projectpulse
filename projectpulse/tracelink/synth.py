"""Generate a backlog and a codebase together, so the answer is known.

Hand labels are the only way to know whether this pipeline works on *real*
data, and nothing here replaces them. What this does replace is the wait:
you cannot tune a matcher you cannot score, and until somebody spends an
afternoon labelling, there is no score at all.

So: build both sides from one plan. Every ticket is generated *from* the
file that implements it, which makes the mapping ground truth by
construction rather than by opinion — and it costs nothing and needs no
model.

**What this can and cannot tell you.** It measures the matcher against the
difficulties we thought to build in. It cannot tell you that real tickets
resemble these, and a matcher tuned only on this would be tuned on our own
assumptions. Treat a regression here as real and an improvement here as a
hypothesis.

The difficulty tiers are the point — a generator that only produced easy
cases would report a flattering number:

``named``    the title contains the file's own name. Should always be found.
``symbol``   the title names a class inside the file, never the filename.
``prose``    the title uses a synonym; only the description names anything.
``shared``   several tickets share a title head, as a real backlog does.
``foreign``  the ticket is not in English.
``decoy``    describes work that is not in the repository at all. The right
             answer is *nothing*, and a matcher that answers anyway is wrong.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

# Feature areas, each with a file stem, a class name, and the words a person
# would use who did not know the filename. Kept small and legible on purpose:
# a reader has to be able to check the generator by eye.
AREAS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("billing", "invoice", "InvoiceEngine", ("charge", "receipt", "payment run")),
    ("billing", "refund", "RefundProcessor", ("money back", "reversal")),
    ("auth", "session", "SessionStore", ("sign in", "login lifetime")),
    ("auth", "password", "PasswordPolicy", ("credential rules", "complexity")),
    ("ui", "dashboard", "DashboardView", ("landing screen", "overview page")),
    ("ui", "toast", "ToastBanner", ("popup message", "transient notice")),
    ("ui", "sidebar", "SidebarNav", ("left rail", "navigation panel")),
    ("core", "scheduler", "TaskScheduler", ("cron", "timed jobs")),
    ("core", "exporter", "CsvExporter", ("download data", "extract to file")),
    ("core", "importer", "SheetImporter", ("upload spreadsheet", "ingest")),
    ("integrations", "slack", "SlackClient", ("chat notifications",)),
    ("integrations", "outlook", "OutlookMailer", ("email sending",)),
)

FOREIGN = (
    ("Thêm chức năng {what}", "Bổ sung xử lý cho {what} trong hệ thống"),
    ("Cập nhật {what}", "Cần cập nhật lại {what} theo yêu cầu mới"),
)

DECOYS = (
    "Quarterly capacity planning workshop",
    "Vendor contract renewal review",
    "Team onboarding handbook refresh",
    "Budget reforecast for next half",
)


@dataclass
class SynthCase:
    uid_hint: str
    summary: str
    description: str
    component: str
    status: str
    difficulty: str
    #: Files that genuinely implement this ticket. Empty for a decoy.
    truth: list[str] = field(default_factory=list)


@dataclass
class SynthPlan:
    cases: list[SynthCase]
    files: dict[str, str]          # path -> source
    hub_path: str


def build_plan(seed: int = 0, n_decoys: int = 4, shared_head: int = 4) -> SynthPlan:
    rng = random.Random(seed)
    files: dict[str, str] = {}
    cases: list[SynthCase] = []

    # A utility every file imports. The matcher must never use it to link two
    # tickets, and `couple` must exclude it by fan-in.
    hub_path = "core/logging_util.py"
    files[hub_path] = (
        "class LogWriter:\n"
        "    def write(self, message):\n"
        "        return message\n"
    )

    for pkg, stem, cls, synonyms in AREAS:
        path = f"{pkg}/{stem}.py"
        files[path] = (
            f"from core.logging_util import LogWriter\n\n\n"
            f"class {cls}:\n"
            f"    \"\"\"Implements {stem} behaviour for the {pkg} area.\"\"\"\n\n"
            f"    def __init__(self):\n"
            f"        self.log = LogWriter()\n\n"
            f"    def run(self, payload):\n"
            f"        self.log.write('{stem}')\n"
            f"        return payload\n"
        )

        # named — the filename is in the title.
        cases.append(SynthCase(
            uid_hint=f"{stem}-named",
            summary=f"{stem.capitalize()} handling for {pkg}",
            description=f"Implement the {stem} path end to end.",
            component=pkg, status="Release it", difficulty="named",
            truth=[path]))

        # symbol — only the class name appears, never the file name.
        cases.append(SynthCase(
            uid_hint=f"{stem}-symbol",
            summary=f"{cls} lifecycle and error paths",
            description=f"Cover construction and failure handling in {cls}.",
            component=pkg, status="In Progress", difficulty="symbol",
            truth=[path]))

        # prose — a person's words, with the real name only in the body.
        syn = rng.choice(synonyms)
        cases.append(SynthCase(
            uid_hint=f"{stem}-prose",
            summary=f"Improve {syn}",
            description=(f"Users report problems with {syn}. "
                         f"The work lives in the {pkg} area."),
            component=pkg, status="To Do", difficulty="prose",
            truth=[path]))

    # shared — several tickets under one head, the collision a real backlog has.
    head_area = AREAS[0][0]
    for i in range(shared_head):
        pkg, stem, cls, _ = AREAS[i % len(AREAS)]
        cases.append(SynthCase(
            uid_hint=f"shared-{i}",
            summary=f"Platform — {stem} rollout step {i + 1}",
            description=f"Roll out {stem} changes for {pkg}.",
            component=head_area, status="Release it", difficulty="shared",
            truth=[f"{pkg}/{stem}.py"]))

    # foreign — non-English, same answer as a named case.
    for i, (title, body) in enumerate(FOREIGN):
        pkg, stem, cls, _ = AREAS[i]
        cases.append(SynthCase(
            uid_hint=f"foreign-{i}",
            summary=title.format(what=stem),
            description=body.format(what=stem),
            component=pkg, status="In Progress", difficulty="foreign",
            truth=[f"{pkg}/{stem}.py"]))

    # decoy — nothing in the repository implements these.
    for i in range(n_decoys):
        cases.append(SynthCase(
            uid_hint=f"decoy-{i}",
            summary=DECOYS[i % len(DECOYS)],
            description="Process work with no software component.",
            component="Programme", status="To Do", difficulty="decoy",
            truth=[]))

    rng.shuffle(cases)
    return SynthPlan(cases=cases, files=files, hub_path=hub_path)


def write(plan: SynthPlan, root: Path) -> dict[str, Path]:
    """Write repo/, backlog.csv and the labels the plan already knows."""
    root = Path(root)
    repo = root / "repo"
    for rel, source in plan.files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")

    export = root / "backlog.csv"
    with export.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Key", "Summary", "Status", "Component/s", "Description"])
        for i, c in enumerate(plan.cases):
            w.writerow([f"SYN-{i + 1}", c.summary, c.status, c.component,
                        c.description])

    # uids are assigned by the reader in row order, so the mapping is exact.
    labels = [
        {"uid": f"T{i:04d}", "relevant_paths": c.truth,
         "verdict": None, "note": c.difficulty, "labeller": "synthetic"}
        for i, c in enumerate(plan.cases)
    ]
    labels_path = root / "labels.json"
    labels_path.write_text(json.dumps(
        {"schema_version": 1, "produced_by": "label",
         "meta": {"synthetic": True}, "payload": labels},
        indent=1), encoding="utf-8")

    return {"repo": repo, "export": export, "labels": labels_path}


def difficulty_of(plan: SynthPlan) -> dict[str, str]:
    return {f"T{i:04d}": c.difficulty for i, c in enumerate(plan.cases)}
