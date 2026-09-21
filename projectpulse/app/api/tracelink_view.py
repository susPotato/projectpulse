"""Read a `tracelink` run directory and shape it for the Traceability page.

Traceability lives in its own repository and produces plain JSON artifacts.
This module reads those files as **data** — it does not import `tracelink` —
so the two projects stay independent and ProjectPulse keeps working whether
or not the traceability pipeline is installed on the same machine.

**Traceability is a property of a delivery project, not of the server.** Every
other screen here is scoped by canonical project id (invariant 7), so this one
is too: a run declares which project it is about in its `run.json`, and the
page asks for a project rather than for "the run". A project with no run says
so plainly instead of showing another project's findings.

Point it at a directory of runs:

    TRACELINK_RUNS=../../traceability/runs python -m scripts.demo

`TRACELINK_RUN` (singular, one run directory) is still honoured for a
single-project setup.

A missing or half-finished run is the normal state while that pipeline is
being developed, so every absence is reported as a named gap rather than an
exception — the same rule as the rest of this API: say which thing is
missing and the command that produces it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Which stage writes each artifact, and the command that produces it. Used to
# tell a reader what to run rather than just that a file was not there.
PRODUCED_BY = {
    "manifest": "tracelink tickets <export.xlsx> --project-id <canonical id>",
    "tickets": "tracelink tickets <export.xlsx>",
    "corpus": "tracelink corpus <repo>",
    "candidates": "tracelink retrieve",
    "verdicts": "tracelink adjudicate --all",
    "shadow": "tracelink shadow --describe",
    "explain": "tracelink explain",
    "translations": "tracelink translate",
    "grounding": "tracelink verify",
    "links": "tracelink couple",
    "diagnosis": "tracelink diagnose <export.xlsx>",
    # The delivery half: the team's own documents read as a record of work,
    # reconciled against the tracker and the code. Optional like everything
    # else here — a project with no documentation tree simply has none of
    # these, and the page says which command would produce them.
    "progress": "tracelink progress --docs <docs dir>",
    "reconciliation": "tracelink reconcile --done-status '<status>'",
    "gates": "tracelink gates",
}


def run_dir() -> Path | None:
    """The single run named by `TRACELINK_RUN`, if that is how it is set up."""
    raw = os.environ.get("TRACELINK_RUN")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def runs_root() -> Path | None:
    raw = os.environ.get("TRACELINK_RUNS")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def _manifest(run: Path) -> dict[str, Any]:
    payload, problem = _read(run, "manifest")
    return payload if not problem and isinstance(payload, dict) else {}


def available_runs() -> list[dict[str, Any]]:
    """Every run this server can see, newest-looking first.

    A run with no `project_id` is still listed - it is a real run somebody
    produced, and hiding it would make "why is my run not showing up?"
    unanswerable from the page.
    """
    found: dict[str, Path] = {}
    root = runs_root()
    if root:
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "tickets.json").exists():
                found[str(child)] = child
    single = run_dir()
    if single:
        found.setdefault(str(single), single)

    out = []
    for path in found.values():
        m = _manifest(path)
        out.append({
            "run": str(path),
            "name": path.name,
            "project_id": m.get("project_id"),
            "project_name": m.get("project_name"),
            "tickets": m.get("tickets"),
        })
    return out


def run_for_project(project_id: str | None) -> tuple[Path | None, list[dict[str, Any]]]:
    """Resolve a project id to its run. Returns (run, all_available)."""
    runs = available_runs()
    if project_id:
        for r in runs:
            if r["project_id"] == project_id:
                return Path(r["run"]), runs
        return None, runs
    # No project asked for: only answer when there is exactly one run, so a
    # multi-project server never silently shows whichever sorted first.
    if len(runs) == 1:
        return Path(runs[0]["run"]), runs
    return None, runs


def _read(run: Path, name: str) -> tuple[Any, str | None]:
    """Return (payload, problem). Never raises on a missing or broken file."""
    p = run / ("run.json" if name == "manifest" else f"{name}.json")
    if not p.exists():
        return None, f"{p.name} not written yet — run: {PRODUCED_BY[name]}"
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"{name}.json could not be read ({exc})"
    if "payload" not in body:
        return None, f"{name}.json is not a tracelink artifact (no payload)"
    return body["payload"], None


# Files that are never interesting as a traceability target. Tests and
# generated data are untracked in every repository; saying so 40 times is
# noise, not a finding. Mirrors tracelink's own shadow-scope rule.
_UNINTERESTING_ROLES = {"test", "data", "binary", "hubdoc"}


def index_by_file(
    corpus_files: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The inverse of the ticket view: for each file, the tickets that claim it.

    Built from the same two facts the ticket view uses, read the other way
    round - a file is `cited` when a settled verdict pointed at it, and
    `retrieved` when only the keyword matcher proposed it. The distinction
    matters more here than anywhere else on the page: a list of "tickets
    touching this file" that silently mixes the two would make a noisy
    keyword hit look like a tracked requirement.
    """
    by_path: dict[str, dict[str, Any]] = {}
    for f in corpus_files:
        if f["role"] in _UNINTERESTING_ROLES:
            continue
        by_path[f["path"]] = {
            "path": f["path"],
            "role": f["role"],
            "language": f.get("language", ""),
            "size": f.get("size", 0),
            "tickets": [],
        }

    for r in rows:
        verdict = r.get("verdict")
        cited_here = set()
        if verdict and verdict.get("verdict") != "unverified":
            for e in verdict.get("evidence", []):
                cited_here.add(str(e.get("file", "")).replace("\\", "/"))
        for path in r["candidates"]:
            entry = by_path.get(path)
            if entry is None:
                # A candidate outside the walked corpus (an excluded role, or a
                # path the analyser skipped). Keep it: hiding a file a ticket
                # points at would make the view disagree with the ticket view.
                entry = by_path.setdefault(path, {
                    "path": path, "role": "other", "language": "",
                    "size": 0, "tickets": [],
                })
            entry["tickets"].append({
                "uid": r["uid"],
                "summary": r["summary"],
                "status": r["status"],
                "verdict": (verdict or {}).get("verdict"),
                "conflict": bool((verdict or {}).get("status_conflict")),
                "cited": path in cited_here,
            })

    out = []
    for entry in by_path.values():
        cited = sum(1 for t in entry["tickets"] if t["cited"])
        entry["cited"] = cited
        entry["coverage"] = ("cited" if cited
                             else "retrieved" if entry["tickets"]
                             else "unclaimed")
        # Tickets whose verdict rested on this file first — they are the ones
        # a reader can act on.
        entry["tickets"].sort(key=lambda t: (not t["cited"], not t["conflict"],
                                             t["summary"]))
        out.append(entry)
    out.sort(key=lambda e: e["path"])
    return out


def rollup_by_parent(run: Path) -> dict[str, Any]:
    """Traceability folded onto the tracker keys the Schedule page draws.

    The Gantt's rows are the export's *keyed* issues. The features live in
    the unkeyed rows beneath them, and each one records the key it sat under.
    Folding verdicts onto that key is what lets a schedule bar say how much of
    the work it represents the code actually backs up.

    This can be lopsided, and when it is, that is the finding rather than a
    defect: in the CoWorkLocal export every one of the 173 features sits
    under a single `Product` issue, so one bar carries the entire backlog and
    the other sixteen carry none. Reporting `0` against those sixteen is
    correct - they are planning tasks, and no code should back them.
    """
    tickets, t_problem = _read(run, "tickets")
    verdicts, _ = _read(run, "verdicts")
    if t_problem:
        return {"parents": {}, "problem": t_problem}

    ground, _ = _read(run, "grounding")
    grounded = {g["uid"] for g in (ground or [])
                if g.get("status") == "grounded"}

    by_uid = {v["uid"]: v for v in (verdicts or [])}
    parents: dict[str, dict[str, Any]] = {}
    for t in tickets:
        key = t.get("parent") or "(none)"
        row = parents.setdefault(key, {
            "features": 0, "corroborated": 0, "contradicted": 0,
            "unverified": 0, "not_adjudicated": 0, "conflicts": 0,
            "grounded": 0,
            "statuses": {},
        })
        row["features"] += 1
        status = t.get("status") or "(blank)"
        row["statuses"][status] = row["statuses"].get(status, 0) + 1
        v = by_uid.get(t["uid"])
        if not v:
            row["not_adjudicated"] += 1
            continue
        row[v["verdict"]] += 1
        # Scoped to corroborated on purpose. An `unverified` verdict can
        # still cite files — as counter-evidence, or to say what it looked
        # at — so an unscoped count read "73 are backed by code (151 with
        # every cited name checked)", which is arithmetic nonsense next to
        # itself. This number qualifies the 73, so it must be a subset of it.
        if v["verdict"] == "corroborated" and t["uid"] in grounded:
            row["grounded"] += 1
        if v.get("status_conflict"):
            row["conflicts"] += 1
    return {"parents": parents, "problem": None}


#: Ranked worst-first. The order is "what does it cost to be wrong, times how
#: well evidenced is it" — so a deterministic check over the corpus outranks a
#: model verdict, and a model verdict with a citation that checks out outranks
#: one without. Volume does not earn a place: 35 area-level rows sit below 2
#: contradictions, because a reader who starts with the 35 never reaches the 2.
FINDING_KINDS = [
    "contradicted",
    "status-conflict",
    "gate-failing",
    "missing-test",
    "documented-not-built",
    "unsupported-citation",
    "unclaimed-code",
]


def _findings(rows: list[dict[str, Any]], files: list[dict[str, Any]],
              delivery: dict[str, Any]) -> list[dict[str, Any]]:
    """One ranked list of things that want a human, across every stage.

    The page had five views and no answer to "what do I look at first".
    Each view is honest on its own and none of them can be read against the
    others: a contradiction found by the adjudicator, a gate the team set
    and failed, and a task whose deliverable was never written are the same
    kind of problem to a reader and three different screens here.

    Every row carries where to look and how strong the evidence is, because
    the strengths genuinely differ — `gate-failing` is arithmetic over the
    corpus, `documented-not-built` is area-level (see `confidence` in the
    reconciliation), and a verdict is a model's reading of an excerpt.
    Presenting them in one list without that column would flatten the
    difference away, which is the one thing this pipeline keeps refusing
    to do.
    """
    out: list[dict[str, Any]] = []

    def add(kind, title, detail, where, evidence, **extra):
        out.append({"kind": kind, "title": title, "detail": detail,
                    "where": where, "evidence": evidence, **extra})

    for r in rows:
        v = r.get("verdict") or {}
        if not v:
            continue
        ground = (r.get("grounding") or {}).get("status")
        cite = next((e for e in v.get("evidence", []) if e.get("file")), {})
        where = cite.get("file", "")
        if cite.get("symbol"):
            where += f"::{cite['symbol']}"

        if v.get("verdict") == "contradicted":
            add("contradicted",
                f"[{r['status']}] {r['summary']}",
                v.get("reasoning", ""), where,
                "cited" if ground == "grounded" else (ground or "uncited"),
                uid=r["uid"], confidence=v.get("confidence"))
        elif v.get("status_conflict"):
            add("status-conflict",
                f"[{r['status']}] {r['summary']}",
                v.get("reasoning", ""), where,
                "cited" if ground == "grounded" else (ground or "uncited"),
                uid=r["uid"], confidence=v.get("confidence"))

        # A verdict resting on a citation that is not there.
        #
        # `no-symbol` is excluded: a citation may legitimately name a call
        # site rather than a definition, and 99 of the 539 checks here are
        # that. `no-file` and `absent` are the two that mean the evidence
        # does not exist.
        #
        # Every `no-file` in this run names a *documentation* file —
        # `architecture/security-policy.md`, `refactor/plan.md`. That is a
        # side effect of the pipeline's own change: showing the model the
        # team's documents taught it to cite one as though it were code.
        # Worth its own line rather than folding into the count.
        for chk in (r.get("grounding") or {}).get("checks", []):
            if chk.get("status") not in ("no-file", "absent"):
                continue
            cited_file = chk.get("file", "")
            doc_like = cited_file.lower().endswith((".md", ".rst", ".txt"))
            add("unsupported-citation",
                f"[{v.get('verdict')}] {r['summary']}",
                ("cites a documentation file as if it were code"
                 if doc_like else "cites something that is not in the corpus")
                + f" — {cited_file}"
                + (f"::{chk['symbol']}" if chk.get("symbol") else ""),
                cited_file, "failed check", uid=r["uid"],
                cited_a_document=doc_like)

    for g in delivery.get("gates", []):
        if g.get("status") == "fail":
            add("gate-failing", g.get("name", ""), g.get("detail", ""),
                f"{g.get('doc', '')}:{g.get('line', '')}", "measured",
                command=g.get("command", ""),
                signed_off=bool(g.get("signed_off")))

    for f in delivery.get("findings", []):
        if f.get("label") != "both-sides-wrong":
            continue
        add("documented-not-built", f"{f['wid']}  {f.get('title', '')}",
            "marked done in the documents; its deliverables are not in the code"
            + (f" — {', '.join(f.get('missing', [])[:3])}" if f.get("missing") else ""),
            f"{f.get('doc', '')}:{f.get('line', '')}",
            "direct" if f.get("joined_via") == "claim" else "area",
            wid=f["wid"])

    for u in delivery.get("unmet_tests", []):
        add("missing-test", f"{u['wid']}  {u['name']}",
            "a completed task says it produced this test; it is not there",
            f"{u.get('doc', '')}:{u.get('line', '')}", "measured", wid=u["wid"])

    unclaimed = [f for f in files if f["coverage"] == "unclaimed"]
    if unclaimed:
        biggest = sorted(unclaimed, key=lambda f: -f.get("size", 0))[:5]
        add("unclaimed-code",
            f"{len(unclaimed)} files no ticket accounts for",
            ", ".join(f["path"] for f in biggest), "", "measured")

    order = {k: i for i, k in enumerate(FINDING_KINDS)}
    out.sort(key=lambda f: (order.get(f["kind"], 99), f.get("title", "")))
    return out


def _delivery(run: Path, gaps: list[str]) -> dict[str, Any]:
    """The delivery half of a run, summarised for one panel.

    The tracker is not the only record of what a team did. On the project
    this was built against, the backlog holds 173 feature tickets and knows
    nothing at all about the ten-EPIC refactor its own `docs/` tree records
    in 63 dated tasks — probing every ticket for `R01`…`R10`, `EPIC` or a
    team name scores zero. A traceability page that reads only the tracker
    is therefore missing half the delivery, which is the reason this block
    exists.

    Summarised rather than passed through: `progress.json` is 150 KB and
    the page needs counts, the worst rows, and the locators to go and look.

    Two things are deliberately kept rather than smoothed away, because
    both are findings and both read as bugs if they arrive unexplained:

    * a reconciliation row's `confidence`. An item joins the tracker
      directly only when one of its deliverables resolves in the code, so
      every row whose code is *absent* is joined at area level, always.
      That is exactly the set of rows a reader cares most about, and
      presenting them beside the direct hits overstates them.
    * a gate that is `unchecked`. "No PySide6 under `domain/`" is satisfied
      trivially when there is no `domain/`; reporting that as a pass tells
      a reader the architecture holds when it was never built.
    """
    present = {name: _read(run, name)[0]
               for name in ("progress", "reconciliation", "gates")}
    if not any(v is not None for v in present.values()):
        # This project does not keep a documentation tree, or nobody has
        # read it yet. Not a gap: three "not written yet" notes on every
        # run that will never have one is noise, and a page that cries
        # about absent optional stages teaches people to skip the list.
        return {}
    for name, value in present.items():
        if value is None:
            gaps.append(f"{name}.json not written yet — run: {PRODUCED_BY[name]}")
    progress, recon, gates = (present["progress"], present["reconciliation"],
                              present["gates"])

    items = (progress or {}).get("items", [])
    schedule = (progress or {}).get("schedule", [])
    unread = [u for u in (progress or {}).get("unparsed", [])
              if u.get("reason") != "no-id"]

    by_label: dict[str, int] = {}
    by_confidence: dict[str, dict[str, int]] = {"direct": {}, "area": {}}
    for r in recon or []:
        label = r.get("label", "unknown")
        by_label[label] = by_label.get(label, 0) + 1
        kind = "direct" if r.get("joined_via") == "claim" else "area"
        by_confidence[kind][label] = by_confidence[kind].get(label, 0) + 1

    interesting = sorted(
        (r for r in (recon or []) if r.get("label") not in ("agreed", "unknown")),
        key=lambda r: (r.get("label", ""), r.get("wid", "")))
    # Not truncated here: the digest reads this list and a cap would make
    # it silently under-report. Reconciliation is one row per work item, so
    # it is bounded by the task count anyway. The page caps what it draws.

    return {
        "items": len(items),
        "done": sum(1 for i in items if i.get("done")),
        "open": sum(1 for i in items if not i.get("done")),
        "timed": sum(1 for i in items if i.get("started") and i.get("ended")),
        "schedule": len(schedule),
        "slipped": sum(1 for s in schedule
                       if s.get("planned") and s.get("started")),
        "unread": len(unread),
        "groups": _group_progress(items),
        "labels": by_label,
        "confidence": by_confidence,
        "findings": interesting,
        # The digest wants these by name rather than by re-deriving them.
        "unmet_tests": [u for u in _unmet_tests(progress)],
        "gates": gates or [],
        "gates_failing": sum(1 for g in (gates or []) if g.get("status") == "fail"),
        "gates_unchecked": sum(1 for g in (gates or [])
                               if g.get("status") == "unchecked"),
        "gates_contradicting": sum(
            1 for g in (gates or [])
            if g.get("status") == "fail" and g.get("signed_off")),
    }


def _unmet_tests(progress) -> list[dict[str, Any]]:
    """Test files a *completed* task says it produced, that are not there.

    Read from `progress.json` rather than `delivery.json` so the digest
    does not depend on a stage that may not have run; the classification
    rule is the same one `tracelink` uses, kept deliberately simple here
    because a wrong answer only ever adds a row a reader can dismiss.
    """
    out = []
    for item in (progress or {}).get("items", []):
        if not item.get("done"):
            continue
        for c in item.get("deliverables", []):
            name = c.get("name", "")
            if c.get("paths") or c.get("kind") == "docref":
                continue
            parts = name.lower().split("/")
            if not (any(p in ("test", "tests", "spec", "specs") for p in parts[:-1])
                    or parts[-1].startswith("test_")):
                continue
            out.append({"wid": item["wid"], "name": name,
                        "doc": item.get("doc", ""), "line": item.get("line", 0)})
    return out


def _group_progress(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Done and open per group, worst first — an epic wholly open goes top."""
    groups: dict[str, dict[str, Any]] = {}
    for i in items:
        name = (i.get("group") or "").split(" / ")[-1] or "(ungrouped)"
        g = groups.setdefault(name, {"group": name, "done": 0, "open": 0})
        g["done" if i.get("done") else "open"] += 1
    return sorted(groups.values(),
                  key=lambda g: (g["done"] > 0, -g["open"], g["group"]))


def collect(run: Path) -> dict[str, Any]:
    """Everything the page needs, in one object."""
    gaps: list[str] = []

    def take(name: str, default):
        payload, problem = _read(run, name)
        if problem:
            gaps.append(problem)
            return default
        return payload

    tickets = take("tickets", [])
    candidates = take("candidates", [])
    verdicts = take("verdicts", [])
    shadow = take("shadow", {})
    explain = take("explain", [])
    links = take("links", [])
    diagnosis = take("diagnosis", {})
    # Citation checks. Deterministic and free, so if they are missing the
    # run simply has not had `verify` run over it yet.
    grounding = {g["uid"]: g for g in (take("grounding", []) or [])}

    corpus_payload, corpus_problem = _read(run, "corpus")
    if corpus_problem:
        gaps.append(corpus_problem)
        corpus = None
    else:
        corpus = {
            "files": len(corpus_payload["files"]),
            "symbols": len(corpus_payload["symbols"]),
            "analyzer": corpus_payload.get("analyzer", ""),
            "root": corpus_payload["root"],
        }

    # Inferred relationships, indexed from each ticket's own point of view so
    # the page never has to work out which end it is looking from.
    related: dict[str, list[dict[str, Any]]] = {}
    for l in links:
        related.setdefault(l["src"], []).append({
            **l, "other": l["dst"],
            "direction": "to" if l["kind"] == "depends-on" else "with"})
        related.setdefault(l["dst"], []).append({
            **l, "other": l["src"],
            "direction": "from" if l["kind"] == "depends-on" else "with"})

    # Translations live in their own artifact, the way every other stage's
    # output does. Merge them here rather than rewriting tickets.json, so the
    # source read stays the source read and a re-translation never has to
    # touch it.
    translations = take("translations", {}) or {}

    by_uid = {c["uid"]: c for c in candidates}
    verdict_by_uid = {v["uid"]: v for v in verdicts}

    rows = []
    for t in tickets:
        c = by_uid.get(t["uid"], {})
        strong: list[str] = []
        why: dict[str, list[str]] = {}
        for cc in c.get("candidates", []):
            hits = sorted({f"{e['matcher']}:{e['anchor']}"
                           for e in cc["evidence"] if e["strong"]})
            if hits:
                strong.append(cc["path"])
                why[cc["path"]] = hits
        rows.append({
            "uid": t["uid"],
            "summary": t["summary"],
            "description": (t.get("description") or "")[:900],
            "status": t.get("status", ""),
            "component": t.get("component", ""),
            # How to find this row again. `key` is the tracker's own id and is
            # often absent - in this export the real feature rows are exactly
            # the keyless ones - so the sheet position is the locator that
            # always works.
            "key": t.get("key"),
            "parent": t.get("parent"),
            "source_row": t.get("source_row"),
            "source_sheet": t.get("source_sheet", ""),
            # Fields the export buried in free text. Recovered by the adapter
            # because `tracelink diagnose` found them there.
            "inline": t.get("inline") or {},
            # The original is what the tracker says and stays the headline;
            # the English rendering is shown beneath it, never instead.
            "lang": (translations.get(t["uid"], {}).get("script")
                     or t.get("lang", "en")),
            "summary_en": (translations.get(t["uid"], {}).get("summary_en")
                           or t.get("summary_en", "")),
            "description_en": ((translations.get(t["uid"], {}).get("description_en")
                                or t.get("description_en") or "")[:900]),
            "candidates": sorted(strong),
            "why": why,
            "head_collision": c.get("head_collision", False),
            "verdict": verdict_by_uid.get(t["uid"]),
            "related": related.get(t["uid"], []),
            "grounding": grounding.get(t["uid"]),
        })

    totals = {
        "tickets": len(rows),
        "with_candidates": sum(1 for r in rows if r["candidates"]),
        "adjudicated": sum(1 for r in rows if r["verdict"]),
        "conflicts": sum(1 for r in rows
                         if r["verdict"] and r["verdict"].get("status_conflict")),
        "cost": round(sum((r["verdict"] or {}).get("cost_usd", 0) for r in rows), 4),
    }
    for kind in ("corroborated", "contradicted", "unverified"):
        totals[kind] = sum(1 for r in rows
                           if r["verdict"] and r["verdict"]["verdict"] == kind)
    # Same scoping as the rollup: the tile reads "citations checked N/M"
    # beside the verdict mix, so N has to mean the same kind of thing.
    totals["grounded"] = sum(
        1 for r in rows
        if (r.get("grounding") or {}).get("status") == "grounded"
        and (r.get("verdict") or {}).get("verdict") == "corroborated")
    totals["cited"] = sum(1 for r in rows
                          if (r.get("grounding") or {}).get("checks"))
    totals["ungrounded"] = sum(
        1 for r in rows
        if (r.get("grounding") or {}).get("status") in ("ungrounded", "partly-grounded"))

    files = index_by_file(corpus_payload["files"] if not corpus_problem else [], rows)
    totals["files_cited"] = sum(1 for f in files if f["coverage"] == "cited")
    totals["files_retrieved"] = sum(1 for f in files if f["coverage"] == "retrieved")
    totals["files_unclaimed"] = sum(1 for f in files if f["coverage"] == "unclaimed")

    delivery = _delivery(run, gaps)
    manifest = _manifest(run)
    return {
        "delivery": delivery,
        "findings": _findings(rows, files, delivery),
        "run": str(run),
        "project_id": manifest.get("project_id"),
        "project_name": manifest.get("project_name"),
        "gaps": gaps,
        "corpus": corpus,
        "totals": totals,
        "tickets": rows,
        "files": files,
        "shadow": shadow,
        "explain": explain,
        "links": links,
        "diagnosis": diagnosis,
        "has_grounding": bool(grounding),
    }
