"""End to end over the free stages, and the invariants *between* them.

Every other test file covers one stage. This one runs the whole free
pipeline over a small synthetic project and checks that the artifacts agree
with each other — that a candidate path is a real file, that a
reconciliation row is a real work item, that a resolved claim points at a
symbol the corpus actually has. Those are exactly the properties a
single-stage test cannot see, and they are the ones that break when two
stages are edited a day apart.

Hermetic on purpose: a repository, a docs tree and a backlog built in
`tmp_path`, and the Python-AST analyser rather than CodeWiki, so the run is
deterministic and takes no network, no key and no seconds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink import corpus as C, docmap as DM, features as F, gates as GA
from tracelink import index as I, reconcile as RC, retrieve as RE
from tracelink.adapters.progress_markdown import read_progress
from tracelink.artifacts import Ticket
from tracelink.config import PipelineConfig

# ---- the project under test ----------------------------------------------

BILLING = '''\
"""Billing."""


class Billing:
    def charge_card(self, amount):
        return amount

    def refund(self, amount):
        return -amount
'''

REPORTING = '''\
def build_report(rows):
    return list(rows)
'''

CONFIG = '''\
SETTINGS = {"api_token": "s3cr3t-live-value"}
'''

GUIDE = """\
# Widget Programme

## Billing

| # | Function | Notes |
|---|----------|-------|
| 1.1 | `charge_card()` | take payment |
| 1.2 | `refund()` | give it back |

## Reporting

- `build_report()` renders the figures
"""

PLAN = """\
# Delivery

## Phase One

- [x] **AA-01 (Alice)**: card charging ➔ `charge_card()`
  *Start: `2026-03-02 09:00` | End: `2026-03-02 11:30`*
- [x] **AA-02 (Alice)**: reporting ➔ `build_report()`
  *Start: `2026-03-03 09:00` | End: `2026-03-03 10:00`*
- [ ] **AA-03 (Bob)**: settlement ➔ `settle_batch()`
- [ ] **AA-04 (Bob)**: ledger ➔ `application/ledger.py`

## Gates

| When | Gate | Command | Criterion | Owner | Start | End | State |
|---|---|---|---|---|---|---|---|
| **05/03** | Size | `python check_loc.py --max-lines 20` | no file over 20 lines | Bob | `____-__-__ __:__` | `____-__-__ __:__` | [ ] |
| **06/03** | Secrets | `python audit.py --all` | no plaintext token in config | Cara | `____-__-__ __:__` | `____-__-__ __:__` | [ ] |
"""

TICKETS = [
    Ticket(uid="T1", summary="Billing — charge a card", status="Release it"),
    Ticket(uid="T2", summary="Reporting — monthly figures", status="In Progress"),
    Ticket(uid="T3", summary="Settlement — batch close", status="To Do"),
]


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    repo, docs = root / "repo", root / "docs"
    repo.mkdir()
    docs.mkdir()
    (repo / "billing.py").write_text(BILLING, encoding="utf-8")
    (repo / "reporting.py").write_text(REPORTING, encoding="utf-8")
    (repo / "config.py").write_text(CONFIG, encoding="utf-8")
    # One file over the size gate's bound, so the gate has something to find.
    (repo / "sprawl.py").write_text(
        "\n\n".join(f"def f{i}():\n    return {i}" for i in range(15)),
        encoding="utf-8")
    (docs / "guide.md").write_text(GUIDE, encoding="utf-8")
    (docs / "plan.md").write_text(PLAN, encoding="utf-8")
    return repo, docs


@pytest.fixture(scope="module")
def run(project):
    """The whole free pipeline, in order, exactly as the CLI composes it."""
    repo, docs = project
    cfg = PipelineConfig()
    # The AST fallback: deterministic, and it is the path any project
    # without CodeWiki installed takes anyway.
    cfg.corpus.prefer_codewiki = False

    corpus = C.build(repo, cfg.corpus)
    fmap = F.build(docs, corpus, cfg.docs)

    progress = read_progress(docs)
    F.resolve_claims([c for i in progress.items for c in i.deliverables], corpus)

    idx = I.add_features(I.build(corpus, cfg), fmap)
    candidates = RE.retrieve(TICKETS, idx, cfg, fmap=fmap)

    dm = DM.build(fmap, corpus, cfg.docs)
    rows = RC.reconcile(progress, TICKETS, candidates, fmap=fmap,
                        done_status={"release it"})
    results = GA.run(progress.gates, corpus)
    return dict(corpus=corpus, fmap=fmap, progress=progress, idx=idx,
                candidates=candidates, docmap=dm, recon=rows, gates=results)


# --------------------------------------------------------------------------
# Every stage produced something
# --------------------------------------------------------------------------

def test_each_stage_produced_its_artifact(run):
    assert run["corpus"].files and run["corpus"].symbols
    assert run["fmap"].features and run["fmap"].docs
    assert run["progress"].items and run["progress"].gates
    assert run["candidates"]
    assert run["docmap"].sections
    assert run["recon"]
    assert run["gates"]


# --------------------------------------------------------------------------
# The invariants between stages
# --------------------------------------------------------------------------

def test_every_candidate_path_is_a_real_file(run):
    paths = {f.path for f in run["corpus"].files}
    for tc in run["candidates"]:
        for c in tc.candidates:
            assert c.path in paths, c.path


def test_every_resolved_claim_points_into_the_corpus(run):
    paths = {f.path for f in run["corpus"].files}
    sids = {s.sid for s in run["corpus"].symbols}
    for f in run["fmap"].features:
        for c in f.claims:
            assert set(c.paths) <= paths, (c.name, c.paths)
            assert set(c.sids) <= sids, (c.name, c.sids)


def test_identifiers_are_unique_within_each_artifact(run):
    fids = [f.fid for f in run["fmap"].features]
    wids = [i.wid for i in run["progress"].items]
    assert len(fids) == len(set(fids))
    assert len(wids) == len(set(wids))


def test_reconciliation_covers_the_work_items_and_nothing_else(run):
    wids = {i.wid for i in run["progress"].items}
    assert {r.wid for r in run["recon"]} == wids
    assert len(run["recon"]) == len(run["progress"].items)


def test_reconciliation_only_cites_tickets_and_files_that_exist(run):
    uids = {t.uid for t in TICKETS}
    paths = {f.path for f in run["corpus"].files}
    for r in run["recon"]:
        assert set(r.tickets) <= uids, r.tickets
        assert set(r.paths) <= paths, r.paths


def test_every_documented_region_is_inside_its_file(run):
    lines = {}
    for s in run["corpus"].symbols:
        lines[s.path] = max(lines.get(s.path, 0), s.end_line or s.line)
    for sec in run["docmap"].sections:
        for reg in sec.regions:
            if reg.located:
                assert reg.start <= reg.end
                assert reg.end <= lines.get(reg.path, reg.end)


def test_one_gate_result_per_gate(run):
    assert len(run["gates"]) == len(run["progress"].gates)


def test_a_locator_still_points_at_what_it_claims(run, project):
    """Every artifact keeps doc and line so a person can go and look."""
    _, docs = project
    for item in run["progress"].items:
        text = (docs / item.doc).read_text(encoding="utf-8").splitlines()
        assert item.wid in text[item.line - 1]


# --------------------------------------------------------------------------
# The answers, end to end
# --------------------------------------------------------------------------

def test_the_shipped_task_reconciles_as_agreed(run):
    """Doc says done, its function is in the code, the ticket says released."""
    row = next(r for r in run["recon"] if r.wid == "AA-01")
    assert (row.docs, row.code, row.tracker) == ("done", "present", "done")
    assert row.label == "agreed"
    assert row.confidence == "direct"


def test_the_unbuilt_task_is_outstanding_and_area_joined(run):
    """And the join weakness is reported rather than smoothed over."""
    row = next(r for r in run["recon"] if r.wid == "AA-03")
    assert row.code == "absent"
    assert row.confidence == "area", "no anchor of its own, so it cannot be direct"


def test_a_gate_finds_the_credential_and_the_oversized_file(run):
    by_check = {r.check: r for r in run["gates"]}
    assert by_check["secrets"].status == "fail"
    assert any("s3cr3t" in o for o in by_check["secrets"].offenders)
    assert by_check["file_size"].status == "fail"


def test_the_map_pins_a_section_to_real_lines(run):
    sec = next(s for s in run["docmap"].sections if "Billing" in s.key)
    region = next(r for r in sec.regions if r.path == "billing.py")
    assert region.located and region.start > 0
    # The two analysers spell a method differently — CodeWiki qualifies it
    # as `Billing.charge_card`, the AST fallback emits `charge_card`. A
    # consumer must not depend on which one ran.
    assert any(s.rsplit(".", 1)[-1] == "charge_card" for s in region.symbols)


# --------------------------------------------------------------------------
# Degradation: the optional halves really are optional
# --------------------------------------------------------------------------

def test_the_pipeline_runs_with_no_documentation_at_all(project, tmp_path):
    """A repository with no docs tree must behave exactly as before 0c/0e."""
    repo, _ = project
    cfg = PipelineConfig()
    cfg.corpus.prefer_codewiki = False
    empty = tmp_path / "nothing"
    empty.mkdir()

    corpus = C.build(repo, cfg.corpus)
    fmap = F.build(empty, corpus, cfg.docs)
    progress = read_progress(empty)
    candidates = RE.retrieve(TICKETS, I.build(corpus, cfg), cfg)

    assert fmap.features == [] and progress.items == []
    assert candidates and all(
        e.matcher != "doc" for tc in candidates for c in tc.candidates
        for e in c.evidence)
    assert RC.reconcile(progress, TICKETS, candidates) == []
    assert GA.run(progress.gates, corpus) == []


def test_reconciliation_without_a_status_mapping_says_so(run):
    rows = RC.reconcile(run["progress"], TICKETS, run["candidates"],
                        fmap=run["fmap"], done_status=None)
    assert all(r.tracker == "unknown" for r in rows)
    assert all(r.label == "unknown" for r in rows)


def test_the_json_half_can_be_left_out(project):
    repo, docs = project
    cfg = PipelineConfig()
    cfg.corpus.prefer_codewiki = False
    corpus = C.build(repo, cfg.corpus)
    assert F.build(docs, corpus, cfg.docs, json_docs=False).features
