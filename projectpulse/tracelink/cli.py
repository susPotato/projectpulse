"""Command line: one run directory, one artifact per stage.

    python -m tracelink pipeline --export <x.xlsx> --repo <dir> --docs <dir>
    python -m tracelink tickets  <export.xlsx> --run runs/demo [--project X]
    python -m tracelink synth    --out synth/    --run runs/synth
    python -m tracelink diagnose <export.xlsx> --run runs/demo
    python -m tracelink translate              --run runs/demo
    python -m tracelink corpus   <repo>        --run runs/demo
    python -m tracelink features --docs <dir>  --run runs/demo
    python -m tracelink retrieve               --run runs/demo
    python -m tracelink progress --docs <dir>  --run runs/demo
    python -m tracelink effort --docs <dir>    --run runs/demo
    python -m tracelink map                    --run runs/demo
    python -m tracelink reconcile              --run runs/demo
    python -m tracelink drift                  --run runs/demo
    python -m tracelink label                  --run runs/demo [-n 25]
    python -m tracelink score                  --run runs/demo
    python -m tracelink adjudicate             --run runs/demo [--all]
    python -m tracelink shadow                 --run runs/demo [--describe]
    python -m tracelink explain                --run runs/demo
    python -m tracelink couple                 --run runs/demo
    python -m tracelink verify                 --run runs/demo

Stages are independent: re-running `retrieve` after a config change costs
nothing and does not touch tickets or corpus.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from tracelink import artifacts as A
from tracelink.config import PipelineConfig
from tracelink.index import Index


def _paths(run: Path) -> dict[str, Path]:
    return {
        "tickets": run / "tickets.json",
        "corpus": run / "corpus.json",
        "features": run / "features.json",
        "progress": run / "progress.json",
        "reconciliation": run / "reconciliation.json",
        "gates": run / "gates.json",
        "delivery": run / "delivery.json",
        "index": run / "index.json",
        "candidates": run / "candidates.json",
        "labels": run / "labels.json",
        "config": run / "config.json",
        "manifest": run / "run.json",
        "explain": run / "explain.json",
        "verdicts": run / "verdicts.json",
        "shadow": run / "shadow.json",
        "links": run / "links.json",
        "diagnosis": run / "diagnosis.json",
        "translations": run / "translations.json",
        "grounding": run / "grounding.json",
        "costs_report": run / "cost_report.json",
        "cohorts": run / "cohorts.json",
        "governance": run / "governance.json",
        "cache": run / "cache",
    }


def cmd_tickets(args) -> int:
    from tracelink.adapters.tickets_tabular import read_tickets

    tickets, shape = read_tickets(args.export, project=args.project,
                                  convention=args.convention)
    print("sheet shape detected:")
    print("  " + shape.describe())
    if shape.projects and not args.project:
        top = sorted(shape.projects.items(), key=lambda kv: -kv[1])
        if len(top) > 1:
            print(f"  note: {len(top)} projects present {dict(top[:4])} — "
                  f"pass --project to filter")
    if not tickets:
        print("\nno tickets read. Check --project and --convention.", file=sys.stderr)
        return 1

    paths = _paths(Path(args.run))
    A.save(paths["tickets"], "tickets", tickets, source=str(args.export),
           convention=shape.convention, project=args.project)

    # A tiny manifest so a consumer can learn which delivery project this run
    # is about without parsing the whole ticket set. ProjectPulse scans a
    # directory of runs on every request; reading 70 KB per run to find one
    # string would make that page slower the more runs exist.
    A.save(paths["manifest"], "run", {
        "project_id": args.project_id,
        "project_name": args.project or (args.project_id or "").split(":")[-1],
        "source": str(args.export),
        "tickets": len(tickets),
    })
    if not args.project_id:
        print("\n  No --project-id given, so this run is not attached to a delivery"
              "\n  project and ProjectPulse will not show it on a project page.")
    print(f"\n{len(tickets)} tickets -> {paths['tickets']}")
    return 0


def cmd_synth(args) -> int:
    from tracelink import synth as SY

    plan = SY.build_plan(seed=args.seed)
    out = SY.write(plan, Path(args.out))
    run = Path(args.run)

    # The labels go straight into the run directory, so `score` works with no
    # further step. They are marked synthetic in their own metadata; nothing
    # downstream can mistake them for somebody's judgement.
    labels_dst = _paths(run)["labels"]
    labels_dst.parent.mkdir(parents=True, exist_ok=True)
    labels_dst.write_text(out["labels"].read_text(encoding="utf-8"),
                          encoding="utf-8")

    import collections
    mix = collections.Counter(c.difficulty for c in plan.cases)
    print(f"{len(plan.cases)} tickets over {len(plan.files)} files")
    for k, n in sorted(mix.items()):
        print(f"  {k:<9} {n:>3}")
    print()
    print(f"  repo    {out['repo']}")
    print(f"  backlog {out['export']}")
    print(f"  labels  {labels_dst}   (ground truth, by construction)")
    print()
    print("Next, all free:")
    print(f"  tracelink --run {run} tickets {out['export']}")
    print(f"  tracelink --run {run} corpus {out['repo']}")
    print(f"  tracelink --run {run} retrieve")
    print(f"  tracelink --run {run} score")
    return 0


def cmd_diagnose(args) -> int:
    from tracelink import diagnose as DG
    from tracelink.adapters.tickets_tabular import read_raw

    keyed = None if args.rows == "all" else (args.rows == "keyed")
    rows, headers, shape = read_raw(args.export, project=args.project, keyed=keyed)
    if not rows:
        print("no rows matched. Check --project and --rows.", file=sys.stderr)
        return 1

    print("sheet shape detected:")
    print("  " + shape.describe())
    print()

    d = DG.diagnose(rows, headers)
    summary_col = next((h for h in headers if h.lower() == "summary"), None)
    if summary_col:
        d.title_convention = DG.title_convention(
            [str(r[summary_col]) for r in rows if r.get(summary_col)])

    print(DG.render(d))
    out = _paths(Path(args.run))["diagnosis"]
    A.save(out, "diagnosis", d.to_dict(), source=str(args.export),
           rows_scope=args.rows, project=args.project)
    print(f"\n-> {out}")
    return 0


def cmd_translate(args) -> int:
    from tracelink import adjudicate as AD, translate as TR

    paths = _paths(Path(args.run))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))

    mix = TR.census(tickets)
    print("language mix (by script, not by guess):")
    for code, n in sorted(mix.items(), key=lambda kv: -kv[1]):
        print(f"  {code:<5} {n:>4}")
    todo = [t for t in tickets if TR.needs_translation(t)]
    if not todo:
        print("\nEvery ticket is already English. Nothing to do, nothing spent.")
        return 0

    pin, pout = AD.PRICING.get(args.model, (0.0, 0.0))
    print(f"\nmodel {args.model} (${pin}/${pout} per Mtok), "
          f"{len(todo)} tickets need translating")
    if args.dry_run:
        sizes = [len(TR.build_prompt(t)) for t in todo]
        avg = sum(sizes) / len(sizes) / 4
        print(f"  mean prompt ~{avg:,.0f} input tokens")
        print(f"  rough estimate: ${len(todo) * (avg / 1e6 * pin + 400 / 1e6 * pout):.2f}")
        print("  dry run - no API calls made")
        return 0

    if not AD.api_key_present():
        # A warning, not a refusal. Every response is cached by content, so a
        # re-run after a config change can be entirely cache hits and needs
        # no credentials at all — refusing up front made the cheapest and
        # commonest way to work impossible. Uncached calls fail individually
        # and are counted and reported.
        print("no credentials set: only already-cached results are available. "
              "Set ANTHROPIC_API_KEY, or run `ant auth login`, to make new calls.",
              file=sys.stderr)

    caller = AD.StructuredCaller(model=args.model, cache_dir=paths["cache"],
                                 effort=args.effort)

    def show(t, row, u):
        src = "cache" if u.cached_calls else "api"
        print(f"  [{src}] {row['detected_language'][:8]:<9} {row['summary_en'][:52]}")

    out, usage = TR.translate(todo, caller, on_result=show)
    A.save(paths["translations"], "translations", out, model=args.model)

    kept = sorted({term for r in out.values() for term in r.get("preserved_terms", [])})
    if kept:
        print("\nterms left untranslated so they still match code:")
        print("  " + ", ".join(kept[:24]))
    print(f"\ncost this run: ${usage.cost(args.model):.4f}")
    print(f"-> {paths['translations']}")
    print("\nRun `retrieve` again to match on the English text.")
    return 0


def cmd_corpus(args) -> int:
    from tracelink import corpus as C
    from tracelink import source as SRC

    cfg = PipelineConfig()
    # A path is read in place; a URL is cloned. Either way the run records
    # which revision it read, so "is this still current?" has an answer.
    try:
        repo, rev = SRC.resolve(args.repo, ref=args.ref)
    except SRC.SourceError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if rev.known:
        print(f"source: {rev.describe()}")
    c = C.build(str(repo), cfg.corpus)
    counts: dict[str, int] = {}
    for f in c.files:
        counts[f.role] = counts.get(f.role, 0) + 1
    langs: dict[str, int] = {}
    for f in c.files:
        if f.language:
            langs[f.language] = langs.get(f.language, 0) + 1

    print(f"analyzer: {c.analyzer}")
    print(f"files: {len(c.files)}   symbols: {len(c.symbols)}   "
          f"edges: {len(c.edges)}")
    print("  roles: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print("  langs: " + (", ".join(f"{k}={v}" for k, v in sorted(langs.items())) or "-"))
    if c.analyzer == "python-ast":
        print("  ! CodeWiki unavailable — symbols are Python-only. Non-Python\n"
              "    files are still indexed by path and prose.")

    p = _paths(Path(args.run))["corpus"]
    A.save(p, "corpus", {"files": c.files, "symbols": c.symbols,
                         "root": c.root, "analyzer": c.analyzer,
                         "edges": c.edges}, revision=rev.as_dict())
    print(f"-> {p}")
    return 0


def cmd_features(args) -> int:
    from tracelink import features as F

    paths = _paths(Path(args.run))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
    fmap = F.build(args.docs, corpus, PipelineConfig().docs,
                   json_docs=not args.no_json)

    if not fmap.features:
        print(f"no markdown found under {args.docs}")
        return 1

    s = F.summary(fmap)
    print(f"{s['docs']} documents -> {s['features']} feature rows, "
          f"{s['claims']} distinct code claims, "
          f"{s['claims_resolved']} of them present in the corpus "
          f"({s['claims_resolved'] / s['claims']:.0%}); "
          f"{s['files_reached']} of {len(corpus.files)} files reached")
    print()
    print(f"  {'document':<52}{'modality':<12}{'rows':>6}{'claims':>8}{'here':>6}")
    order = {"grounded": 0, "process": 1, "ungrounded": 2}
    for d in sorted(fmap.docs, key=lambda d: (order.get(d.modality, 3), -d.claims)):
        print(f"  {d.doc[:52]:<52}{d.modality:<12}{d.features:>6}"
              f"{d.claims:>8}{d.resolved:>6}")
        if args.why:
            for sig in d.signals:
                print(f"      · {sig}")

    sk = [r for r in F.skew(fmap) if r["near_matches"] >= 3]
    if sk:
        print("\nVERSION SKEW — documents that name functions this build *almost* has.")
        print("A design names files that do not exist; a later build names functions")
        print("that were since renamed. These look like the second:")
        for r in sk[:6]:
            print(f"  {r['doc'][:56]:<56} {r['near_matches']:>3} of "
                  f"{r['missing']} misses are near-matches "
                  f"({r['near_fraction']:.0%})")
            for e in r["examples"][:2]:
                print(f"      doc says {e['doc_says']:<28} code has "
                      f"{e['code_has']} ({e['in']})")

    A.save(paths["features"], "features",
           {"root": fmap.root, "features": fmap.features, "docs": fmap.docs},
           docs_dir=str(args.docs))
    print(f"\n-> {paths['features']}")
    return 0


def cmd_map(args) -> int:
    from tracelink import docmap as DM

    paths = _paths(Path(args.run))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
    fmap = A.rebuild_features(A.load_payload(paths["features"], "features"))
    dm = DM.build(fmap, corpus)

    if not any(s.line for s in corpus.symbols):
        print("! this corpus has no line numbers — re-run `corpus` to get them.\n"
              "  Until then every link below resolves to a file, not a region.\n")

    if args.file:
        matches = [f for f in dm.files if args.file in f.path]
        if not matches:
            print(f"no file matching {args.file!r}")
            return 1
        for fc in matches[: args.limit]:
            print(f"\n{fc.path}  ({fc.symbol_lines} lines in named symbols, "
                  f"{fc.fraction:.0%} documented)")
            found = DM.sections_for(dm, fc.path)
            if not found:
                print("    nothing in the documentation set mentions this file")
                continue
            for sec, regions in found[: args.sections]:
                flag = "" if sec.modality == "grounded" else f"  [{sec.modality}]"
                print(f"    {sec.doc}:{sec.line}{flag}")
                print(f"      {sec.title}")
                for r in regions:
                    syms = ", ".join(r.symbols[:4])
                    if len(r.symbols) > 4:
                        syms += f", +{len(r.symbols) - 4}"
                    print(f"        lines {r.start}-{r.end}  {syms}")
        return 0

    if args.section:
        hits = [s for s in dm.sections if args.section.lower() in s.key.lower()]
        if not hits:
            print(f"no section matching {args.section!r}")
            return 1
        for sec in hits[: args.limit]:
            print(f"\n{sec.doc}:{sec.line}   [{sec.modality}]")
            print(f"  {sec.title}")
            print(f"  {sec.rows} rows, {sec.resolved}/{sec.claims} claims resolved")
            if not sec.regions:
                print("    pins no code in this repository")
            for r in sorted(sec.regions, key=lambda r: (r.path, r.start)):
                syms = ", ".join(r.symbols[:5])
                if len(r.symbols) > 5:
                    syms += f", +{len(r.symbols) - 5}"
                where = f"{r.path}:{r.start}-{r.end}" if r.start else r.path
                print(f"    {where}   {syms}")
        return 0

    s = DM.summary(dm)
    print(f"{s['sections']} documented sections, {s['sections_pinned_to_code']} of "
          f"which pin at least one line of code "
          f"({s['sections_from_grounded_docs']} from grounded documents)")
    print(f"{s['files_documented']} of {len(dm.files)} source files are reached by "
          f"some section.")
    print(f"Of those, the median file has "
          f"{s['median_symbol_precision_of_documented_files']:.0%} of its symbols "
          f"named individually, while "
          f"{s['median_line_coverage_of_documented_files']:.0%} of its lines fall "
          f"inside some section — the gap is class-level mentions, which cover a "
          f"whole file and locate nothing in it.")
    if s["ambiguous_claims"]:
        print(f"{s['ambiguous_claims']} resolved claims name more than "
              f"{PipelineConfig().docs.max_pin_targets} symbols each and are not "
              f"allowed to pin a line (`__init__`, `refresh`, `run`).")

    if args.undocumented:
        rows = sorted(dm.undocumented, key=lambda f: -f.symbol_lines)
        print(f"\nCODE NO SECTION REACHES — {len(rows)} files.")
        print("Not the same question as `shadow` (code no ticket claims) or")
        print("`drift` (claims no code supports): this is code nobody wrote down.\n")
        for fc in rows[: args.limit]:
            print(f"  {fc.symbol_lines:>6} symbol lines   {fc.path}")
        if len(rows) > args.limit:
            print(f"  … {len(rows) - args.limit} more")
        return 0

    print("\nMOST PRECISELY PINNED SECTIONS")
    print("Ranked by symbols located, not lines covered: a section naming one")
    print("god-class covers 1,670 lines and locates nothing inside them.\n")
    best = sorted((s for s in dm.sections if s.regions),
                  key=lambda s: (-s.symbols_pinned, -s.lines_covered))
    for sec in best[: args.limit]:
        flag = "" if sec.modality == "grounded" else f" [{sec.modality}]"
        print(f"  {sec.symbols_pinned:>3} symbols, {len(sec.files):>2} files  "
              f"{sec.doc}:{sec.line}{flag}")
        print(f"       {sec.title[:92]}")
        for r in sorted(sec.regions, key=lambda r: -len(r.symbols))[:3]:
            print(f"       -> {r.path}:{r.start}-{r.end}"
                  f"  ({', '.join(r.symbols[:3])})")
    print("\n  --file PATH        which sections document this file, line by line")
    print("  --section TEXT     which code this section accounts for")
    print("  --undocumented     source files no section reaches")
    return 0


def cmd_progress(args) -> int:
    import collections

    from tracelink.adapters.progress_markdown import read_progress

    paths = _paths(Path(args.run))
    report = read_progress(args.docs)
    if not (report.items or report.gates or report.register):
        print(f"no work records found under {args.docs}.\n"
              f"That is a real answer: these documents may describe code "
              f"(try `features`) and not delivery.")
        return 1

    # Deliverables and affected files are Claims, so the corpus checks them
    # with exactly the code that checks a feature's claims. Without a corpus
    # they stay unresolved rather than being quietly dropped.
    if paths["corpus"].exists():
        from tracelink.features import resolve_claims

        corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
        # A task's deliverable is often another document — "write the ADR" ->
        # `docs/architecture/ADR-001.md`. Without the docs tree to check
        # against, those count as code this repository is missing, and the
        # task is then reported as claimed-done-with-nothing-behind-it. Four
        # of them here, every one a document that exists.
        root = Path(args.docs)
        known_docs = {q.relative_to(root).as_posix() for q in root.rglob("*")
                      if q.is_file()} | {q.name for q in root.rglob("*") if q.is_file()}
        resolve_claims([c for i in report.items for c in i.deliverables]
                       + [c for d in report.register for c in d.anchors],
                       corpus, known_docs)

    done = sum(1 for i in report.items if i.done)
    timed = sum(1 for i in report.items if i.started and i.ended)
    signed = sum(1 for g in report.gates if g.signed_off)
    print(f"{len(report.items)} work items ({done} done, "
          f"{len(report.items) - done} open), {timed} with real start and end times")
    print(f"{len(report.schedule)} plan rows, {len(report.gates)} quality gates "
          f"({signed} signed off), {len(report.register)} register entries")
    if report.unparsed:
        reasons = collections.Counter(u["reason"] for u in report.unparsed)
        declined = reasons.get("no-id", 0)
        broke = sum(n for r, n in reasons.items() if r != "no-id")
        # Two different things, and collapsing them hides the second: a
        # template correctly refused is not a line the parser failed on.
        print(f"{declined} lines declined for carrying no identifier "
              f"(templates, checklists); {broke} could not be read"
              + (f" ({', '.join(f'{n} {r}' for r, n in reasons.most_common() if r != 'no-id')})"
                 if broke else "")
              + "   (--audit to see them)")

    if args.audit:
        print("\nNOT READ — every line that looked like work and was declined.")
        print("Nobody has labelled this extraction, so this list is the only")
        print("thing standing between a quiet loss and a wrong report.\n")
        for u in report.unparsed[: args.limit]:
            print(f"  {u['reason']:<13} {u['doc']}:{u['line']}  {u['text'][:78]}")
        if len(report.unparsed) > args.limit:
            print(f"  … {len(report.unparsed) - args.limit} more")
        return 0

    if report.items:
        print("\nBY GROUP")
        groups = collections.defaultdict(lambda: [0, 0])
        for i in report.items:
            groups[i.group.split(" / ")[-1] or "(none)"][0 if i.done else 1] += 1
        for g, (d, o) in sorted(groups.items(), key=lambda kv: -sum(kv[1])):
            flag = "   <- all open" if d == 0 and o else ""
            print(f"  {d:>3} done  {o:>3} open   {g[:64]}{flag}")

    unsigned = [g for g in report.gates if not g.signed_off]
    if unsigned:
        print(f"\nGATES NOT SIGNED OFF — {len(unsigned)} of {len(report.gates)}")
        for g in unsigned[:10]:
            print(f"  {g.name[:52]:<52} {g.command[:44]}")
            print(f"    {g.doc}:{g.line}")

    open_with_code = [i for i in report.items
                      if not i.done and any(c.resolved for c in i.deliverables)]
    if open_with_code:
        print(f"\nOPEN WORK THAT ALREADY TOUCHES REAL CODE — {len(open_with_code)}")
        for i in open_with_code[:10]:
            anchors = ", ".join(c.name for c in i.deliverables if c.resolved)
            print(f"  {i.wid:<12} {i.title[:56]}")
            print(f"    {anchors[:88]}")

    A.save(paths["progress"], "progress", {
        "root": report.root, "items": report.items, "schedule": report.schedule,
        "gates": report.gates, "register": report.register,
        "unparsed": report.unparsed,
    }, docs_dir=str(args.docs))
    print(f"\n-> {paths['progress']}")
    return 0


def cmd_reconcile(args) -> int:
    from tracelink import reconcile as RC

    paths = _paths(Path(args.run))
    for need in ("progress", "tickets", "candidates"):
        if not paths[need].exists():
            print(f"missing {paths[need]}. Run `{need}` first.", file=sys.stderr)
            return 1

    progress = A.rebuild_progress(A.load_payload(paths["progress"], "progress"))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    candidates = A.rebuild_candidates(A.load_payload(paths["candidates"], "candidates"))
    fmap = (A.rebuild_features(A.load_payload(paths["features"], "features"))
            if paths["features"].exists() else None)

    done_status = {s for s in (args.done_status or "").split(",") if s.strip()}
    if done_status:
        print(f"treating these tracker statuses as finished: "
              f"{', '.join(sorted(done_status))}")
    else:
        print("no --done-status given, so what the tracker means by any status\n"
              "is unknown and every row will say so. This is a decision, not a\n"
              "fact the export contains — pass it explicitly.")

    rows = RC.reconcile(progress, tickets, candidates, fmap=fmap,
                        done_status=done_status)
    print(f"\n{len(rows)} work items reconciled against {len(tickets)} tickets "
          f"and {len(set(p for r in rows for p in r.paths))} files")
    for kind, caption in (
            ("direct", "DIRECT — the item's own code anchor found these tickets"),
            ("area", "AREA-LEVEL — joined through the item's doc section, so the "
                     "tracker\n              column is about this part of the "
                     "system, not this task")):
        counts = RC.summary(rows, kind)
        if not counts:
            continue
        print(f"\n  {caption}")
        for label, n in counts.items():
            print(f"    {n:>4}  {label}")
    if any(r.confidence == "area" for r in rows):
        print("\n  Every `absent` row is area-level by construction: an item joins"
              "\n  directly only when a deliverable resolved, and `absent` means"
              "\n  none did. There is no stronger join available for those rows.")

    shown = [r for r in rows if r.label not in ("agreed", "unknown")]
    if args.label:
        shown = [r for r in rows if r.label == args.label]
    if shown:
        print(f"\nDISAGREEMENTS, worst first")
        for r in shown[: args.limit]:
            print(f"\n  [{r.label}]  {r.wid}  {r.title[:56]}")
            print(f"    tracker={r.tracker}  docs={r.docs}  code={r.code}"
                  f"   (joined via {r.joined_via})")
            print(f"    {r.doc}:{r.line}")
            if r.anchors:
                print(f"    code found: {', '.join(r.anchors[:4])}")
            if r.missing:
                print(f"    code missing: {', '.join(r.missing[:4])}")
            if r.tickets:
                print(f"    tickets: {', '.join(r.tickets[:6])}")
        if len(shown) > args.limit:
            print(f"\n  … {len(shown) - args.limit} more")

    # An axis that came out the same on every row decided nothing, and a
    # label naming two agreeing sources is worth one source less than it
    # sounds. Say so rather than let the label carry the implication.
    flat = RC.degenerate_axes(rows)
    width = RC.join_width(rows)
    if flat:
        print("\nAXES THAT DECIDED NOTHING")
        for axis, value in flat.items():
            print(f"  {axis} is {value!r} on all {len(rows)} rows, so it "
                  f"separated nothing.")
        if "tracker" in flat:
            print(f"  A work item reaches {width.get('median')} tickets at the "
                  f"median and {width.get('max')} at the")
            print("  widest, and the tracker counts as done if any one of them "
                  "is. So labels that name")
            print("  the tracker as a second agreeing source - "
                  "`both-sides-wrong`, `agreed` - actually")
            print("  rest on the documents and the code alone.")

    A.save(paths["reconciliation"], "reconciliation", rows,
           done_status=sorted(done_status),
           degenerate_axes=flat, join_width=width)
    print(f"\n-> {paths['reconciliation']}")
    return 0


def cmd_pipeline(args) -> int:
    from tracelink import pipeline as PL

    run = Path(args.run)
    plan = PL.build_plan(
        run=str(run), export=args.export, repo=args.repo, docs=args.docs,
        project=args.project, project_id=args.project_id,
        done_status=args.done_status, reports=not args.quiet,
        ref=getattr(args, "ref", ""))

    print(f"{len(plan.steps)} free stages for {run}")
    for s in plan.steps:
        print(f"  {s.name}")
    for name, why in plan.skipped:
        print(f"  - {name}: skipped, {why}")
    if args.dry_run:
        print()
        print("dry run - nothing was executed")
        return 0

    failures = PL.run_plan(plan, invoke=main, out=print)

    print()
    print("=" * 60)
    if failures:
        print("STAGES THAT DID NOT COMPLETE")
        for name, code in failures:
            print(f"  {name} (exit {code})")
    else:
        print(f"every free stage completed -> {run}")

    print()
    print("NOT RUN, BECAUSE THEY COST MONEY")
    print("  Each estimates first, and every response is cached by content.")
    if args.docs:
        print(f"  tracelink --run {run} translate --dry-run")
    print(f"  tracelink --run {run} adjudicate --dry-run --all"
          + (f" --docs {args.docs}" if args.docs else ""))
    print(f"  tracelink --run {run} verify        # free, after adjudicate")
    print(f"  tracelink --run {run} couple        # free, after adjudicate")

    if args.into:
        copied, missing = PL.stage_for_product(run, Path(args.into))
        print()
        print(f"STAGED FOR THE PRODUCT -> {args.into}")
        print(f"  {len(copied)} artifacts copied")
        if missing:
            print(f"  not yet produced: {', '.join(missing)}")
            print("  The page names each absence and the command that fixes it,")
            print("  so it is readable now and completes as stages are run.")
        print()
        print("  One run per project id: a second directory declaring the same")
        print("  id shadows the first rather than appearing beside it.")
    return 1 if any(f for f in failures) else 0


def cmd_effort(args) -> int:
    from tracelink import delivery as DV

    paths = _paths(Path(args.run))
    report = A.rebuild_progress(A.load_payload(paths["progress"], "progress"))
    known_docs = set()
    if args.docs:
        root = Path(args.docs)
        known_docs = {q.relative_to(root).as_posix() for q in root.rglob("*")
                      if q.is_file()} | {q.name for q in root.rglob("*")
                                         if q.is_file()}
    d = DV.build(report, known_docs)
    s = DV.summary(d)

    print(f"{s['timed_tasks']} of {len(report.items)} tasks record both a start "
          f"and an end.")
    print(f"  median {s['median_minutes']:.0f} min   "
          f"quartiles {s['p25_minutes']:.0f}-{s['p75_minutes']:.0f}   "
          f"total {s['total_hours']} h")
    print()
    print("  A recorded interval is not effort. These are the gaps between two")
    print("  timestamps somebody typed, and nothing here can tell a fast team")
    print("  from a backfilled sheet. The distribution is the finding; read it.")
    if s["under_five_minutes"]:
        print(f"  {s['under_five_minutes']} tasks record under five minutes.")
        for t in sorted(d.tasks, key=lambda t: t.minutes)[:5]:
            print(f"    {t.minutes:6.0f} min  {t.wid}  {t.owner}")

    if d.overlaps:
        print()
        print(f"IMPOSSIBLE AS ELAPSED WORK — {s['overlapping_tasks']} tasks")
        print("One owner cannot spend the same minute twice, so at least one of")
        print("each pair is an estimate or a backfill.")
        for o in d.overlaps[: args.limit]:
            print(f"  {o.owner:<12} {o.first} and {o.second} overlap by "
                  f"{o.minutes:.0f} min")
        if len(d.overlaps) > args.limit:
            print(f"  … {len(d.overlaps) - args.limit} more")

    if d.by_day:
        print()
        print("WHEN THE WORK HAPPENED")
        for day, n in d.by_day.items():
            print(f"  {day}  {'#' * min(n, 40)} {n}")
        if d.idle_days:
            print(f"  no activity recorded on: {', '.join(d.idle_days)}")

    if d.slips:
        print()
        print(f"PLANNED AGAINST ACTUAL — {len(d.slips)} rows started on another day")
        for sl in d.slips[: args.limit]:
            when = "late" if sl.days > 0 else "early"
            print(f"  {abs(sl.days):>2} days {when:<5} planned {sl.planned:<18} "
                  f"started {sl.started}")
            print(f"       {sl.group[:40]}  {sl.title[:50]}")

    if d.unmet:
        print()
        print(f"CLAIMED BUT NOT THERE — {len(d.unmet)} outputs of *completed* tasks")
        print("An open task has not claimed anything yet; these have.")
        for kind, label in ((DV.KIND_TEST, "tests"), (DV.KIND_CODE, "source files"),
                            (DV.KIND_SYMBOL, "symbols"), (DV.KIND_DOC, "documents")):
            rows = [u for u in d.unmet if u.kind == kind]
            if not rows:
                continue
            print()
            print(f"  {len(rows)} {label}:")
            for u in rows[: args.limit]:
                print(f"    {u.wid:<10} {u.name[:60]}")
            if len(rows) > args.limit:
                print(f"    … {len(rows) - args.limit} more")
        tests = [u for u in d.unmet if u.kind == DV.KIND_TEST]
        if tests:
            print()
            print(f"  The {len(tests)} missing tests are the ones to read twice: a")
            print("  Definition of Done that requires passing tests is worth")
            print("  nothing if the tests it counts are not in the repository.")

    A.save(paths["delivery"], "delivery", {
        "tasks": d.tasks, "overlaps": d.overlaps, "slips": d.slips,
        "unmet": d.unmet, "by_day": d.by_day, "idle_days": d.idle_days,
    })
    print()
    print(f"-> {paths['delivery']}")
    return 0


def cmd_gates(args) -> int:
    from tracelink import gates as G

    paths = _paths(Path(args.run))
    progress = A.rebuild_progress(A.load_payload(paths["progress"], "progress"))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
    if not progress.gates:
        print("no quality gates found in the documentation.")
        return 1

    results = G.run(progress.gates, corpus)
    s = G.summary(results)
    print(f"{len(results)} gates the documentation defines: "
          f"{s['pass']} pass, {s['fail']} fail, {s['unchecked']} not checkable here")
    print()
    print("The criteria are re-implemented against the corpus, not shell-executed:")
    print("the commands name scripts this repository does not contain, and running")
    print("a command out of a document is executing a file nobody here wrote.")
    print()

    for r in results:
        mark = {"pass": "PASS", "fail": "FAIL", "unchecked": "  ? "}[r.status]
        print(f"  [{mark}] {r.name[:56]}")
        if r.criterion:
            print(f"         criterion: {r.criterion[:76]}")
        if r.command:
            print(f"         the doc runs: {r.command}")
        print(f"         {r.detail}")
        for o in r.offenders[: args.limit]:
            print(f"           {o}")
        if len(r.offenders) > args.limit:
            print(f"           … {len(r.offenders) - args.limit} more")
        print()

    unsigned_failing = [r for r in results if r.status == "fail" and not r.signed_off]
    if unsigned_failing:
        print(f"{len(unsigned_failing)} gate(s) fail and were never signed off — "
              f"the document and the code agree.")
    if s["contradicts_signoff"]:
        print(f"{s['contradicts_signoff']} gate(s) are signed off in the document "
              f"and fail when measured.")

    A.save(paths["gates"], "gates", results)
    print()
    print(f"-> {paths['gates']}")
    return 0


def cmd_drift(args) -> int:
    from tracelink import features as F
    from tracelink.artifacts import MODALITY_GROUNDED, MODALITY_UNGROUNDED

    paths = _paths(Path(args.run))
    fmap = A.rebuild_features(A.load_payload(paths["features"], "features"))

    modalities = ((MODALITY_GROUNDED, MODALITY_UNGROUNDED) if args.all
                  else (MODALITY_GROUNDED,))
    rows = F.drift(fmap, modalities)
    if not rows:
        print("no documented capability is missing from the code"
              + ("" if args.all else " (in grounded documents; --all for the rest)"))
        return 0

    print(f"{len(rows)} name(s) the documentation gives that this repository "
          f"does not define.")
    print("This is the mirror of `shadow`: there, code no ticket claims; here,")
    print("a claim no code supports.\n")
    for r in rows[: args.limit]:
        w = r["where"][0]
        near = ""
        print(f"  {r['name']:<34} {r['kind']:<7} {r['mentions']:>3}x  "
              f"{w['doc']}:{w['line']}{near}")
        if args.verbose:
            print(f"      {w['heading']} / {w['label']}")
    if len(rows) > args.limit:
        print(f"  … {len(rows) - args.limit} more")
    return 0


def cmd_retrieve(args) -> int:
    from tracelink import index as I, retrieve as R, evaluate as E

    run = Path(args.run)
    paths = _paths(run)
    cfg = PipelineConfig()

    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))

    # Match on English when we have it. Silent when no translation stage has
    # run - an all-English backlog must not be made to look incomplete.
    if paths["translations"].exists():
        from tracelink import translate as TR
        n = TR.apply(tickets, A.load_payload(paths["translations"], "translations"))
        print(f"using English translations for {n} ticket(s)")

    idx = I.build(corpus, cfg)

    # Documentation is optional and stays optional: with no features.json
    # the pipeline behaves exactly as it did before this stage existed.
    fmap = None
    if paths["features"].exists() and not args.no_docs:
        fmap = A.rebuild_features(A.load_payload(paths["features"], "features"))
        I.add_features(idx, fmap)
        n_grounded = sum(1 for f in fmap.features if f.modality == "grounded")
        print(f"using {len(fmap.features)} documented feature rows "
              f"({n_grounded} from documents measured as describing this build)")

    A.save(paths["index"], "index", idx.to_json())

    results = R.retrieve(tickets, idx, cfg, fmap=fmap)
    hubdocs = R.detect_hubdocs(results, cfg)
    if hubdocs:
        print("hub documents excluded (match too much of the backlog to "
              "discriminate):")
        for h in sorted(hubdocs):
            print("   ", h)
        results = R.drop_paths(results, hubdocs)

    A.save(paths["candidates"], "candidates", results,
           hubdocs=sorted(hubdocs), n_indexed=idx.n_indexed)
    A.save(paths["config"], "config", cfg.to_dict())

    cov = E.coverage(results)
    print("\nCOVERAGE (how often we answered — not accuracy)")
    for k, v in cov.items():
        print(f"  {k:<28} {v}")
    print("\nMATCHER CONTRIBUTION")
    for m, d in E.per_matcher(results).items():
        print(f"  {m:<6} {d['tickets']:>4} tickets   sole evidence for {d['sole_evidence']}")
    print(f"\n-> {paths['candidates']}")
    return 0


def cmd_label(args) -> int:
    from tracelink import label as L

    run = Path(args.run)
    paths = _paths(run)
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    results = {r.uid: r for r in
               A.rebuild_candidates(A.load_payload(paths["candidates"], "candidates"))}
    corpus_root = Path(A.load_payload(paths["corpus"], "corpus")["root"])

    labels = L.load_labels(paths["labels"])
    sample = L.stratified_sample(tickets, results, args.n, seed=args.seed)
    sample = [t for t in sample if t.uid not in labels]
    if args.dry_run:
        print(f"{len(labels)} already labelled; would ask about {len(sample)}:")
        for t in sample:
            r = results.get(t.uid)
            n = len(r.strong_paths()) if r else 0
            print(f"  {t.uid}  {n} candidates  {t.summary[:58]}")
        return 0

    L.prompt_session(sample, results, labels, paths["labels"],
                     corpus_root, labeller=args.labeller)
    return 0


def cmd_adjudicate(args) -> int:
    from tracelink import adjudicate as AD, describe as D, report as RP

    paths = _paths(Path(args.run))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
    candidates = {r.uid: r for r in
                  A.rebuild_candidates(A.load_payload(paths["candidates"], "candidates"))}

    # The adjudicator is shown the translation alongside the original, so it
    # is not silently doing the translation itself inside the verdict call.
    if paths["translations"].exists():
        from tracelink import translate as TR
        TR.apply(tickets, A.load_payload(paths["translations"], "translations"))

    describers: list = []
    if paths["features"].exists() and not args.no_docs:
        fmap = A.rebuild_features(A.load_payload(paths["features"], "features"))
        describers.append(D.DocFeature(fmap))
        # Prose fallback for the same tree, minus the documents `features`
        # measured as not describing this corpus. Left in, they would be
        # excerpted as "architecture documentation" with no label saying
        # which build they are about.
        skip = {d.doc for d in fmap.docs if d.modality != "grounded"}
        if args.docs:
            describers.append(D.ExistingDocs(args.docs, skip_docs=skip))
    elif args.docs:
        describers.append(D.ExistingDocs(args.docs))
    describers.append(D.RawSource(max_files=args.max_files, window=args.window))
    bundle = D.Bundle(*describers)

    chosen = tickets if args.all else tickets[: args.limit]
    if args.uid:
        wanted = set(args.uid.split(","))
        chosen = [t for t in tickets if t.uid in wanted]

    pin, pout = AD.PRICING.get(args.model, (0.0, 0.0))
    print(f"model {args.model} (${pin}/${pout} per Mtok), effort={args.effort}, "
          f"{len(chosen)} tickets")
    if args.dry_run:
        n_with = sum(1 for t in chosen if candidates.get(t.uid, None)
                     and candidates[t.uid].strong_paths())
        print(f"  {n_with} have candidates, {len(chosen) - n_with} do not")
        sizes = []
        for t in chosen:
            tc = candidates.get(t.uid)
            if tc and tc.candidates:
                sizes.append(len(AD.build_prompt(t, bundle.describe_all(tc, corpus))))
        if sizes:
            # ~4 chars per token is close enough to warn before spending.
            avg_in = sum(sizes) / len(sizes) / 4
            est = len(chosen) * (avg_in / 1e6 * pin + 700 / 1e6 * pout)
            print(f"  mean prompt ~{avg_in:,.0f} input tokens")
            print(f"  rough estimate for {len(chosen)} tickets: ${est:.2f}")
        print("  dry run — no API calls made")
        return 0

    if not AD.api_key_present():
        # A warning, not a refusal. Every response is cached by content, so a
        # re-run after a config change can be entirely cache hits and needs
        # no credentials at all — refusing up front made the cheapest and
        # commonest way to work impossible. Uncached calls fail individually
        # and are counted and reported.
        print("no credentials set: only already-cached results are available. "
              "Set ANTHROPIC_API_KEY, or run `ant auth login`, to make new calls.",
              file=sys.stderr)

    adj = AD.Adjudicator(model=args.model, cache_dir=paths["cache"],
                         effort=args.effort)

    def show(t, v, u):
        mark = "!" if v.status_conflict else " "
        src = "cache" if u.cached_calls else "api"
        print(f"{mark} {t.uid} {v.verdict:<13} {v.confidence:<6} [{src}] "
              f"{t.summary[:46]}")

    verdicts, usage = AD.run(chosen, candidates, corpus, bundle, adj,
                             on_result=show, workers=args.workers)

    # Merge, never replace. `--limit` and `--uid` are the normal way to work
    # (spend a little, look, spend more), and a partial run that overwrote
    # the file would silently throw away every verdict outside its slice.
    # The model responses survive in the cache either way, but the artifact
    # other stages read would be wrong until somebody noticed.
    merged: dict[str, object] = {}
    if paths["verdicts"].exists():
        for row in A.load_payload(paths["verdicts"], "verdicts"):
            merged[row["uid"]] = row
    replaced = sum(1 for v in verdicts if v.uid in merged)
    for v in verdicts:
        # A cache hit costs nothing *now*, which is true and is not the same
        # as the verdict having been free to produce. Overwriting the stored
        # figure with 0 made the ledger drift to zero the more the pipeline
        # was re-run — so the original spend is carried forward.
        prior = merged.get(v.uid)
        if not v.cost_usd and isinstance(prior, dict) and prior.get("cost_usd"):
            v.cost_usd = prior["cost_usd"]
        merged[v.uid] = v
    ordered = [merged[t.uid] for t in tickets if t.uid in merged]

    A.save(paths["verdicts"], "verdicts", ordered, model=args.model,
           effort=args.effort, describers=[d.name for d in describers])
    if len(ordered) > len(verdicts):
        print(f"\nmerged into {len(ordered)} verdicts "
              f"({len(verdicts) - replaced} new, {replaced} updated)")

    if usage.failures:
        # Loudly, and with a non-zero exit below: a silent "$0.0000" here
        # reads exactly like a fully-cached run, which is the good outcome.
        print(f"\n!! {usage.failures} of {len(chosen)} call(s) FAILED."
              f"\n   First error: {usage.first_error[:260]}", file=sys.stderr)

    print(f"\napi calls {usage.api_calls}, cache hits {usage.cached_calls}, "
          f"tokens in={usage.input_tokens} out={usage.output_tokens} "
          f"cache_read={usage.cache_read}")
    print(f"cost this run: ${usage.cost(args.model):.4f}")
    if usage.api_calls and not usage.cache_read:
        print("  (no prompt-cache reads — the shared system prompt is below the "
              "model's minimum cacheable prefix)")
    print(f"-> {paths['verdicts']}")

    print()
    print(RP.build(tickets, verdicts).render())
    return 1 if usage.failures and not verdicts else 0


def cmd_shadow(args) -> int:
    from tracelink import adjudicate as AD, shadow as SH

    paths = _paths(Path(args.run))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
    candidates = A.rebuild_candidates(A.load_payload(paths["candidates"], "candidates"))

    verdicts = None
    if paths["verdicts"].exists():
        verdicts = A.rebuild_verdicts(A.load_payload(paths["verdicts"], "verdicts"))
    else:
        print("no verdicts.json — grading against retrieval only, so 'cited' will\n"
              "be empty and shadow scope will read wider than it is. Run\n"
              "`adjudicate` first for the real picture.\n")

    files = SH.classify_coverage(corpus, candidates, verdicts)
    counts = SH.summarise(files)
    groups = SH.group_by_directory(SH.significant(files, args.min_symbols))

    described = None
    if args.describe:
        chosen = groups[: args.limit]
        pin, pout = AD.PRICING.get(args.model, (0.0, 0.0))
        print(f"model {args.model} (${pin}/${pout} per Mtok), "
              f"{len(chosen)} untracked areas")
        if args.dry_run:
            sizes = [len(SH.build_prompt(g, corpus, args.max_files, args.window))
                     for g in chosen]
            if sizes:
                avg = sum(sizes) / len(sizes) / 4
                est = len(chosen) * (avg / 1e6 * pin + 400 / 1e6 * pout)
                print(f"  mean prompt ~{avg:,.0f} input tokens")
                print(f"  rough estimate: ${est:.2f}")
            print("  dry run — no API calls made\n")
        elif not AD.api_key_present():
            print("no credentials. Set ANTHROPIC_API_KEY, or run `ant auth login`.",
                  file=sys.stderr)
            return 1
        else:
            caller = AD.StructuredCaller(model=args.model, cache_dir=paths["cache"],
                                         effort=args.effort)

            def show(g, row, u):
                mark = "!" if row.get("user_facing") else " "
                src = "cache" if u.cached_calls else "api"
                print(f"{mark} {g.directory + '/':<34} [{src}] {row['capability'][:44]}")

            described, usage = SH.describe_groups(
                chosen, corpus, caller, max_files=args.max_files,
                window=args.window, on_result=show)
            print(f"\ncost this run: ${usage.cost(args.model):.4f}\n")

    A.save(paths["shadow"], "shadow",
           {"counts": counts,
            "groups": [{"directory": g.directory, "n_symbols": g.n_symbols,
                        "files": [f.path for f in g.files]} for g in groups],
            "described": described})
    print(SH.render(counts, groups, described, top=args.limit))
    print(f"\n-> {paths['shadow']}")
    return 0


def cmd_explain(args) -> int:
    from tracelink import adjudicate as AD, explain as EX

    paths = _paths(Path(args.run))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    verdicts = (A.rebuild_verdicts(A.load_payload(paths["verdicts"], "verdicts"))
                if paths["verdicts"].exists() else [])
    if not verdicts:
        print("no verdicts.json - explain restates verdicts, so run "
              "`adjudicate` first.", file=sys.stderr)
        return 1

    groups = EX.group_by_component(tickets)
    pin, pout = AD.PRICING.get(args.model, (0.0, 0.0))
    print(f"model {args.model} (${pin}/${pout} per Mtok), "
          f"{len(groups)} feature areas")

    if args.dry_run:
        by_uid = {v.uid: v for v in verdicts}
        sizes = [len(EX.build_prompt(a, rows, by_uid)) for a, rows in groups.items()]
        avg = sum(sizes) / len(sizes) / 4
        print(f"  mean prompt ~{avg:,.0f} input tokens")
        print(f"  rough estimate: ${len(groups) * (avg / 1e6 * pin + 350 / 1e6 * pout):.2f}")
        print("  dry run - no API calls made")
        return 0

    if not AD.api_key_present():
        # A warning, not a refusal. Every response is cached by content, so a
        # re-run after a config change can be entirely cache hits and needs
        # no credentials at all — refusing up front made the cheapest and
        # commonest way to work impossible. Uncached calls fail individually
        # and are counted and reported.
        print("no credentials set: only already-cached results are available. "
              "Set ANTHROPIC_API_KEY, or run `ant auth login`, to make new calls.",
              file=sys.stderr)

    caller = AD.StructuredCaller(model=args.model, cache_dir=paths["cache"],
                                 effort=args.effort)

    def show(area, row, u):
        src = "cache" if u.cached_calls else "api"
        print(f"  {area[:26]:<28} [{src}] {row['headline'][:44]}")

    rows, usage = EX.explain(tickets, verdicts, caller, on_result=show)
    A.save(paths["explain"], "explain", rows, model=args.model)
    print(f"\ncost this run: ${usage.cost(args.model):.4f}")
    print(f"-> {paths['explain']}")
    return 0


def cmd_couple(args) -> int:
    from tracelink import couple as CP

    paths = _paths(Path(args.run))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    corpus = A.rebuild_corpus(A.load_payload(paths["corpus"], "corpus"))
    verdicts = (A.rebuild_verdicts(A.load_payload(paths["verdicts"], "verdicts"))
                if paths["verdicts"].exists() else [])
    if not verdicts:
        print("no verdicts.json - links are inferred from cited code, so run "
              "`adjudicate` first.", file=sys.stderr)
        return 1
    if not corpus.edges:
        print("this corpus has no dependency edges (the Python-AST fallback "
              "does not produce them), so only shared-file links are possible.")

    cfg = CP.CoupleConfig()
    links, stats = CP.infer(tickets, verdicts, corpus, cfg)
    A.save(paths["links"], "links", [l.as_dict() for l in links], **stats)

    by_uid = {t.uid: t for t in tickets}
    print("INFERRED LINKS (from code, not from the tracker)")
    for k, v in stats.items():
        if k != "hub_reasons":
            print(f"  {k:<26} {v}")
    if stats["hub_reasons"]:
        print("\n  excluded as shared infrastructure:")
        for f, reason in list(stats["hub_reasons"].items())[:10]:
            print(f"    {f:<40} {reason}")

    print("\nSTRONGEST LINKS")
    for l in links[: args.show]:
        a = by_uid.get(l.src), by_uid.get(l.dst)
        arrow = "->" if l.kind == CP.KIND_DEPENDS else "<->"
        print(f"  [{l.confidence:<6}] {(a[0].summary if a[0] else l.src)[:34]:<36}"
              f" {arrow} {(a[1].summary if a[1] else l.dst)[:34]}")
        print(f"            via {', '.join(l.via[:2])}")
    print(f"\n-> {paths['links']}")
    return 0


def cmd_verify(args) -> int:
    from tracelink import verify as VF

    paths = _paths(Path(args.run))
    if not paths["verdicts"].exists():
        print("no verdicts.json - there is nothing to check yet.", file=sys.stderr)
        return 1
    verdicts = A.rebuild_verdicts(A.load_payload(paths["verdicts"], "verdicts"))
    root = A.load_payload(paths["corpus"], "corpus")["root"]

    groundings, stats = VF.verify(verdicts, root)
    A.save(paths["grounding"], "grounding",
           [g.as_dict() for g in groundings], **stats)
    print(VF.render(groundings, stats))
    print(f"\n-> {paths['grounding']}")
    # A verdict resting on a name that is not in the file is a defect in the
    # output, not a warning about it.
    return 1 if stats["ungrounded"] else 0


def cmd_governance(args) -> int:
    from tracelink import governance as GV
    from tracelink.adapters.tickets_tabular import read_raw

    paths = _paths(Path(args.run))
    from tracelink.adapters.tickets_tabular import column_named

    everything, names, shape = read_raw(args.export, project=args.project)
    wanted = ({t for t in args.process_type.split(",") if t.strip()}
              if args.process_type else None)
    rows, how = GV.select(everything,
                          type_column=column_named(names, "issue_type"),
                          types=wanted)
    if rows is None:
        # No type column, so fall back to what this used to do unconditionally.
        # Said out loud, because "17 process tickets" and "190 process tickets"
        # look equally plausible in a report that does not say how it chose.
        rows, _, shape = read_raw(args.export, project=args.project, keyed=True)
        how = f"{how}, so the rows carrying a key were taken instead"
    print(f"process tickets selected because {how}: "
          f"{len(rows)} of {len(everything)}")
    if not rows:
        print("no process tickets in this export - nothing to report.")
        return 0

    docs = []
    if args.docs:
        root = Path(args.docs)
        docs = [p.relative_to(root).as_posix() for p in root.rglob("*")
                if p.is_file()]

    done = set(args.done_status.split(",")) if args.done_status else None
    summary = GV.build(rows, docs, done)
    print(GV.render(summary))
    A.save(paths["governance"], "governance", summary.as_dict(),
           source=str(args.export), sheet=shape.sheet,
           done_status=sorted(done) if done else None)
    print()
    print(f"-> {paths['governance']}")
    return 0


def cmd_cohorts(args) -> int:
    from tracelink import cohorts as CH

    paths = _paths(Path(args.run))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    verdicts = (A.rebuild_verdicts(A.load_payload(paths["verdicts"], "verdicts"))
                if paths["verdicts"].exists() else [])

    found = CH.seams(tickets)
    if args.field:
        picked = [s for s in found if s.field == args.field]
        if not picked:
            print(f"{args.field!r} is not a field this backlog can be split on. "
                  f"Tried: {', '.join(s.field for s in found)}", file=sys.stderr)
            return 1
        seam = picked[0]
    else:
        seam = next((s for s in found if s.is_seam), None)
        if seam is None:
            print("no seam: every candidate field is interleaved through the "
                  "sheet, so this reads as one backlog. Nothing to split.")
            for s in found:
                print(f"  {s.field:<12} {s.groups} values in {s.runs} runs "
                      f"(contiguity {s.contiguity})")
            return 0

    langs = {}
    if paths["translations"].exists():
        for uid, row in (A.load_payload(paths["translations"],
                                        "translations") or {}).items():
            if isinstance(row, dict) and row.get("detected_language"):
                langs[uid] = row["detected_language"]
    cohorts = CH.split(tickets, seam, verdicts, langs)
    print(CH.render(seam, cohorts, len(tickets)))
    A.save(paths["cohorts"], "cohorts",
           {"seam": {"field": seam.field, "groups": seam.groups,
                     "runs": seam.runs, "contiguity": seam.contiguity},
            "cohorts": [c.as_dict() for c in cohorts]})
    print()
    print(f"-> {paths['cohorts']}")
    return 0


def cmd_stale(args) -> int:
    import datetime

    from tracelink import freshness as FR

    run = Path(args.run)
    for name in args.accept or []:
        if name not in FR.DERIVES_FROM:
            print(f"--accept: {name!r} is not a derived artifact. One of: "
                  f"{', '.join(sorted(FR.DERIVES_FROM))}", file=sys.stderr)
            return 1
        if not FR._path(run, name).exists():
            print(f"--accept: {run / name}.json does not exist", file=sys.stderr)
            return 1
        when = datetime.date.today().isoformat()
        got = FR.accept(run, name, when)
        print(f"accepted {name} as current against {', '.join(got)} ({when})")
    rows = FR.check(run)
    print(FR.render(rows, run))

    # Artifacts can agree with each other and all be about code that has
    # since moved. That is the failure a weekly refresh actually has, and
    # no amount of comparing artifacts to each other can see it.
    drift = FR.source_moved(run)
    if drift:
        print()
        print("THE CODE HAS MOVED SINCE THIS RUN")
        print(f"  {drift}")
        print("  Re-run from `corpus` onward to analyse what is there now.")
    # A run holding artifacts that disagree is a defect in the run, not a
    # warning about it — same rule `verify` keeps for ungrounded citations.
    return 1 if rows else 0


def cmd_cost(args) -> int:
    from tracelink import cost as CO

    declared = {}
    for item in args.declare or []:
        stage, _, usd = item.partition("=")
        if not usd:
            print(f"--declare wants stage=amount, got {item!r}", file=sys.stderr)
            return 1
        declared[stage.strip()] = float(usd)
    discarded = {}
    for item in args.discarded or []:
        stage, _, usd = item.partition("=")
        if not usd:
            print(f"--discarded wants stage=amount, got {item!r}", file=sys.stderr)
            return 1
        discarded[stage.strip()] = float(usd)
    led = CO.build(Path(args.run), docs_cost=args.docs_cost,
                   docs_note=args.docs_note, declared=declared,
                   discarded=discarded)
    print(CO.render(led))
    A.save(_paths(Path(args.run))["costs_report"], "cost-report", led.to_dict())
    return 0


def cmd_report(args) -> int:
    from tracelink import report as RP

    paths = _paths(Path(args.run))
    tickets = A.rebuild_tickets(A.load_payload(paths["tickets"], "tickets"))
    verdicts = A.rebuild_verdicts(A.load_payload(paths["verdicts"], "verdicts"))
    print(RP.build(tickets, verdicts).render())
    return 0


def cmd_score(args) -> int:
    from tracelink import evaluate as E, label as L

    paths = _paths(Path(args.run))
    results = {r.uid: r for r in
               A.rebuild_candidates(A.load_payload(paths["candidates"], "candidates"))}
    labels = L.load_labels(paths["labels"])
    if not labels:
        print("no labels yet — run `label` first. Coverage without ground\n"
              "truth is not a score.", file=sys.stderr)
        return 1

    s = E.score(labels, results)
    print("RETRIEVAL ACCURACY (labelled tickets only)")
    print(s.render())

    if args.baseline:
        prev = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        print("\nVS BASELINE")
        print(E.compare(E.Scores(**prev), s))
    if args.save_baseline:
        Path(args.save_baseline).write_text(
            json.dumps(s.__dict__, indent=1), encoding="utf-8")
        print(f"\nbaseline saved -> {args.save_baseline}")
    return 0


def _force_utf8_stdout() -> None:
    """Never let a Vietnamese heading kill a command that already succeeded.

    The Windows console defaults to cp1252, and this pipeline's whole point
    is reading backlogs and documentation that are not in Latin-1 — so
    `drift` finished its work, started printing, and died on `ầ` with a
    UnicodeEncodeError after the useful output had already scrolled past.
    Replacement characters in a terminal are a cosmetic problem; a
    traceback instead of a report is not.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):     # pragma: no cover - not a tty
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="tracelink", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="runs/default", help="run directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tickets", help="read a backlog export")
    p.add_argument("export")
    p.add_argument("--project", default=None)
    p.add_argument("--convention", choices=("unkeyed-subrows", "flat"), default=None)
    p.add_argument("--project-id", default=None,
                   help="canonical delivery-project id this backlog belongs to, "
                        "e.g. excel:Project:upload:cowork-local")
    p.set_defaults(fn=cmd_tickets)

    p = sub.add_parser("synth", help="generate a backlog and repo with known answers")
    p.add_argument("--out", required=True, help="directory to write repo/ and backlog.csv")
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(fn=cmd_synth)

    p = sub.add_parser("diagnose", help="what structure does this export contain?")
    p.add_argument("export")
    p.add_argument("--project", default=None)
    p.add_argument("--rows", choices=("all", "keyed", "unkeyed"), default="unkeyed",
                   help="which rows to profile (default: the unkeyed feature rows)")
    p.set_defaults(fn=cmd_diagnose)

    p = sub.add_parser("translate", help="put non-English tickets into English")
    p.add_argument("--model", default="claude-opus-5")
    p.add_argument("--effort", default="low",
                   choices=("low", "medium", "high", "xhigh", "max"))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_translate)

    p = sub.add_parser("corpus", help="analyse a code repository")
    p.add_argument("repo", help="a checkout to read, or a git URL to clone")
    p.add_argument("--ref", default="",
                   help="branch or tag to clone, when repo is a URL")
    p.set_defaults(fn=cmd_corpus)

    p = sub.add_parser("features",
                       help="read the team's own docs as a feature map and "
                            "check every claim against the code")
    p.add_argument("--docs", required=True, help="directory of markdown, read recursively")
    p.add_argument("--why", action="store_true",
                   help="show the measurement behind each document's modality")
    p.add_argument("--no-json", action="store_true",
                   help="markdown only — the ablation for the structured sources")
    p.set_defaults(fn=cmd_features)

    p = sub.add_parser("progress",
                       help="read the team's docs as a record of work: tasks, "
                            "gates, register")
    p.add_argument("--docs", required=True, help="directory of markdown, read recursively")
    p.add_argument("--audit", action="store_true",
                   help="list every task-shaped line the adapter declined to read")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_progress)

    p = sub.add_parser("reconcile",
                       help="tracker vs the team's docs vs the code: who is wrong")
    p.add_argument("--done-status", default=None,
                   help="comma-separated tracker statuses that mean finished. "
                        "A decision, not a fact — it is recorded in the output")
    p.add_argument("--label", default=None, help="show only this label")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(fn=cmd_reconcile)

    p = sub.add_parser("map",
                       help="which doc section accounts for which lines of which file")
    p.add_argument("--file", default=None,
                   help="a path or fragment: show the sections documenting it")
    p.add_argument("--section", default=None,
                   help="a heading fragment: show the code it accounts for")
    p.add_argument("--undocumented", action="store_true",
                   help="source files no section reaches")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sections", type=int, default=6,
                   help="max sections to show per file")
    p.set_defaults(fn=cmd_map)

    p = sub.add_parser("pipeline",
                       help="every free stage, in order, from a project's inputs")
    p.add_argument("--export", default=None, help="the backlog export")
    p.add_argument("--repo", default=None,
                   help="the code repository: a checkout, or a git URL")
    p.add_argument("--ref", default="",
                   help="branch or tag to clone, when --repo is a URL")
    p.add_argument("--docs", default=None, help="the documentation tree")
    p.add_argument("--project", default=None, help="project name inside the export")
    p.add_argument("--project-id", default=None,
                   help="canonical delivery-project id, so the page can find it")
    p.add_argument("--done-status", default=None,
                   help="tracker status(es) meaning finished, for `reconcile`")
    p.add_argument("--into", default=None,
                   help="also copy the artifacts the product page reads into "
                        "this directory")
    p.add_argument("--quiet", action="store_true",
                   help="skip the reporting-only stages (map, drift)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan and run nothing")
    p.set_defaults(fn=cmd_pipeline)

    p = sub.add_parser("effort",
                       help="how long the recorded work took, and what it "
                            "claimed to produce")
    p.add_argument("--docs", default=None,
                   help="docs tree, so a deliverable that is another document "
                        "is not counted as missing code")
    p.add_argument("--limit", type=int, default=8)
    p.set_defaults(fn=cmd_effort)

    p = sub.add_parser("gates",
                       help="check the repository against the gates its own "
                            "documents set")
    p.add_argument("--limit", type=int, default=6,
                   help="offenders to list per gate")
    p.set_defaults(fn=cmd_gates)

    p = sub.add_parser("drift",
                       help="documented capabilities with no code behind them")
    p.add_argument("--all", action="store_true",
                   help="include documents measured as not describing this build")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("retrieve", help="propose candidate files per ticket")
    p.add_argument("--no-docs", action="store_true",
                   help="ignore features.json — the ablation, and the baseline "
                        "any claim about what documentation is worth is measured against")
    p.set_defaults(fn=cmd_retrieve)

    p = sub.add_parser("label", help="record ground truth interactively")
    p.add_argument("-n", type=int, default=25)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--labeller", default="")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_label)

    p = sub.add_parser("adjudicate", help="ask the model for a verdict per ticket")
    p.add_argument("--model", default="claude-opus-5")
    p.add_argument("--effort", default="medium",
                   choices=("low", "medium", "high", "xhigh", "max"))
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--all", action="store_true", help="every ticket, ignoring --limit")
    p.add_argument("--uid", default=None, help="comma-separated ticket uids")
    p.add_argument("--docs", default=None,
                   help="directory of existing .md docs, for the prose fallback")
    p.add_argument("--no-docs", action="store_true",
                   help="ignore features.json (ablation)")
    p.add_argument("--max-files", type=int, default=3)
    p.add_argument("--window", type=int, default=160)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_adjudicate)

    p = sub.add_parser("shadow", help="find code no ticket accounts for")
    p.add_argument("--describe", action="store_true",
                   help="ask the model to name each untracked capability (costs money)")
    p.add_argument("--model", default="claude-opus-5")
    p.add_argument("--effort", default="medium",
                   choices=("low", "medium", "high", "xhigh", "max"))
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--min-symbols", type=int, default=2)
    p.add_argument("--max-files", type=int, default=3)
    p.add_argument("--window", type=int, default=120)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_shadow)

    p = sub.add_parser("explain", help="translate each feature area into plain language")
    p.add_argument("--model", default="claude-opus-5")
    p.add_argument("--effort", default="medium",
                   choices=("low", "medium", "high", "xhigh", "max"))
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_explain)

    p = sub.add_parser("couple", help="infer which tickets depend on which")
    p.add_argument("--show", type=int, default=12)
    p.set_defaults(fn=cmd_couple)

    p = sub.add_parser("cost", help="what the whole analysis cost, external inputs included")
    p.add_argument("--docs-cost", type=float, default=None,
                   help="what the architecture docs cost to generate elsewhere")
    p.add_argument("--docs-note", default="",
                   help="how that figure was arrived at")
    p.add_argument("--declare", action="append", metavar="STAGE=USD",
                   help="a spend whose recorded figure was lost (e.g. verdicts=8.75)")
    p.add_argument("--discarded", action="append", metavar="STAGE=USD",
                   help="spend on a failed or superseded run, which no surviving "
                        "artifact records (e.g. explain=0.2162)")
    p.set_defaults(fn=cmd_cost)

    p = sub.add_parser("governance", help="process tickets, and what can honestly be checked")
    p.add_argument("export")
    p.add_argument("--project", default=None)
    p.add_argument("--docs", default=None,
                   help="documentation tree a deliverable may resolve into")
    p.add_argument("--done-status", default=None,
                   help="comma-separated statuses that mean finished")
    p.add_argument("--process-type", default=None,
                   help="comma-separated issue types that are process work, "
                        "e.g. 'PM Task,Product'. Without this, an issue type "
                        "naming management (PM, Planning, Governance…) is "
                        "taken as process work and delivery types are not")
    p.set_defaults(fn=cmd_governance)

    p = sub.add_parser("cohorts", help="is this one backlog or two? split it on the seam")
    p.add_argument("--field", default=None,
                   help="split on this field rather than the most contiguous one")
    p.set_defaults(fn=cmd_cohorts)

    p = sub.add_parser("stale", help="artifacts left behind by a change to an earlier stage")
    p.add_argument("--accept", action="append", metavar="ARTIFACT",
                   help="record an artifact as current against today's inputs, "
                        "for one you have checked and will not rebuild")
    p.set_defaults(fn=cmd_stale)

    p = sub.add_parser("verify", help="check every citation against the source")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("report", help="re-render the report from saved verdicts")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("score", help="measure retrieval against labels")
    p.add_argument("--baseline", default=None)
    p.add_argument("--save-baseline", default=None)
    p.set_defaults(fn=cmd_score)

    args = ap.parse_args(argv)
    return args.fn(args)
