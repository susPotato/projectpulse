# Jira ↔ Code Traceability

A side experiment, self-contained in this folder: given a codebase and a Jira
backlog, recover the links between them and adjudicate **which side is wrong** —
a ticket claiming work that isn't there, or code no ticket tracks.

**Start with [`traceability/WORKLOG.md`](traceability/WORKLOG.md)** — findings, costs,
decisions, and the ablation that tested whether the generated docs were worth paying for.

## Layout

| Path | What |
|---|---|
| `traceability/` | the pipeline: 4 scripts, 23 generated docs, results, worklog |
| `pimsathon-main/` | the codebase under analysis (CoWorkLocal, 144 Python files) |
| `Jira Cowork Local_0913.xlsx` | the backlog — 173 feature tickets hide as unkeyed sub-rows |

## Status

| Stage | | |
|---|---|---|
| 1 — retrieve | ✅ built, free | 143/173 tickets (82.7%) get a candidate set, median 3 files of 148 |
| 2 — describe | ⚠️ partial, ~$12 | 23 of ~60 docs; stopped deliberately (see worklog) |
| 3 — adjudicate | ✅ PoC, $0.31 | 12 tickets; ~$4.50 would cover all 173 |
| 4 — shadow scope | ❌ not built | code capabilities with no ticket |

Nothing here is wired into ProjectPulseAI — it's a separate evaluation of whether
this approach works at all.
