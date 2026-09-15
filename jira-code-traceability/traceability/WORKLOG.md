# Jira ↔ Code Traceability — Work Log

Goal: given a codebase and a Jira backlog, recover links between them and adjudicate
**which side is wrong** — a ticket claiming work that isn't there, or code that no
ticket tracks.

Target: `pimsathon-main/` — the **CoWorkLocal** desktop app (PySide6, 144 Python files,
~45K LOC). Confirmed identical to the Jira project: `config.py:21` is
`Path.home() / ".cowork_local"`, the exact path quoted in a ticket.

---

## Pipeline

```
Stage 0  normalise        xlsx -> 173 tickets ; repo -> role-classified file index
Stage 1  retrieve         keyword/IDF matching -> ~3 candidate files per ticket   [FREE]
Stage 2  describe         CodeWiki -> capability statements with file::symbol anchors
Stage 3  adjudicate       LLM reads ticket + candidates -> 3-valued verdict
Stage 4  reverse pass     code capabilities with no ticket = shadow scope         [NOT BUILT]
```

### Verdict model

Proving a claim TRUE and proving it FALSE are **not symmetric**. Finding the code proves
implementation; not finding it proves nothing, because features hide under different
names (`Console` is implemented in `ui/terminal_panel.py`). Hence three verdicts, not two:

| Ticket says | Code shows | Verdict | Confidence |
|---|---|---|---|
| `Release it` | found | corroborated | high |
| `To Do` | found | **Jira stale** — status conflict | **high** (existence is provable) |
| `Release it` | not found | **unverified** — needs a human | low, always |
| *(none)* | found | shadow scope | medium |

`corroborated` means *"code exists that plausibly implements this"*, **not** *"this feature
works"*. CodeWiki reads code, it does not run it — a polished but unwired or buggy
function documents as working.

---

## Findings

### Stage 1 — retrieval (built, free)

| | |
|---|---:|
| tickets with a strong candidate set | **143 / 173 (82.7%)** |
| median candidate-set size | **3 files** (of 148) |
| English vs Vietnamese anchor rate | 75.8% vs 45.8% |

Three traps, each of which silently inflated the score before being fixed:

1. **Hub documents.** `assets/RULEBASE.md` (27 KB) matched 134/173 tickets. A catch-all doc
   mentions everything and therefore discriminates nothing — excluded by role.
2. **Rarity is relative to corpus size.** A `df <= 4` bar against 13 prose files means
   "appears in 31% of the corpus".
3. **Mixed token sources.** Folding 1,697 function names into the filename index inflated
   document frequency and pushed good stem matches below the rarity bar. Index separately.

An unfixed run reported **94.2%**; the honest figure after fixes is **82.7%**. Coverage
metrics on this kind of matcher inflate easily and quietly.

### Stage 2 — CodeWiki (partial, stopped on cost)

23 of ~60 docs generated for **~$12** (~$0.52/doc) before stopping. Two tiers:

| Tier | n | size | detail density | verdict |
|---|---:|---|---:|---|
| cluster (LLM-invented grouping of 10–54 files) | 12 | ~5 KB | **196 B/file** | thin — names packages, not files |
| deep sub-module | 11 | 9–21 KB | **4,850 B/file** | **genuinely good** |

Deep docs carry `file.py::Symbol` anchors, sequence diagrams with real call chains and
error branches, and capability bullets in exactly the requested form.

**CodeWiki reports incompleteness rather than papering over it** (3 confirmed cases):
a client-side UI lock correctly distinguished from real authentication; a `not-yet-active`
backend flagged; `RULEforCode.md` identified as an empty placeholder leaving the Code
agent unprotected. It catches *declared* incompleteness because the code says so — it
cannot catch code that looks complete but does not work.

**Hard limit:** clustering silently drops **52 of 144 files (36%)** — they appear in no
module and can never be documented. `task_executors.py`, wanted by 9 tickets, is one.
Finishing the run would not have fixed this.

### Stage 3 — adjudication (PoC, 12 tickets, $0.31)

Extrapolates to **~$4.50 for all 173**.

- Found a real status conflict: `File Edit Dialog` is `To Do` in Jira but
  `ui/file_edit_dialog.py::FileEditDialog` is fully implemented.
- **Did not confabulate**: all three "shipped, no evidence" traps returned
  `unverified`/low rather than inventing a verdict.
- **Caught a Stage 1 retrieval error**: handed `core/telemetry_shared.py` for a ticket
  about `AppContext` (bad keyword match on *"shared"*), it rejected the file as unrelated
  instead of rationalising a link.

**Doc ablation** (same 12 tickets, doc excerpts withheld): 9/12 verdicts identical,
2 differed only in confidence, **1 differed materially** — and it was the status-conflict
finding, which degraded to a plain `contradicted` with the conflict flag lost. Docs are
worth having, but their value is concentrated, not uniform.

---

## Decisions

- **LiSSA dropped from the pipeline.** Stage 1 replicates its retrieval step in `grep` and
  targets better, exploiting the Jira `Component` field and the compound-filename
  convention. Keep as a published baseline for comparison, not as infrastructure.
- **Doc generation stopped at 23.** The remaining ~$19 would buy 21% more ticket coverage
  and still miss 52 unclusterable files. Demand should be driven by tickets, not by
  completing a tree.
- **Next step: run Stage 3 across all 173 tickets on the docs already owned (~$4.50).**

---

## Environment notes (Windows)

Reproducing this on Windows hits four blockers, none documented upstream:

1. **Subscription mode is impossible.** `caw` imports `fcntl`, then `pty`/`termios`. The
   `fcntl` use is one advisory lock and is shimmable; the PTY is not. Use an API key.
2. **`PYTHONUTF8=1` is mandatory** — CodeWiki prints `✓` and crashes on a cp1252 console.
3. **`--include "*.py"` is unusable.** The glob is expanded before CodeWiki sees it, in
   Git Bash *and* PowerShell, via `python -m` *and* the `.exe`. Omit the flag.
4. **Base URL needs `/v1`.** CodeWiki drives Anthropic through the OpenAI-compatible
   client, so `https://api.anthropic.com` becomes `/chat/completions` → 404. Verified:
   `/v1/chat/completions` → 200.

Also: re-running into a populated output dir prompts interactively and aborts under a
background shell — pipe `echo y |`.

---

## Files

| Path | What |
|---|---|
| `extract_tickets.py` | pulls the 173 feature tickets (unkeyed sub-rows) from the xlsx |
| `evidence_index.py` | role-classified file index + IDF inverted indexes |
| `stage1_match.py` | three matchers (stem / sym / prose) → candidate sets |
| `stage3_adjudicate.py` | LLM adjudication, `--no-docs` runs the ablation |
| `codewiki-docs/` | the 23 generated docs + `module_tree.json` |
| `stage1_results.json` | per-ticket candidates with evidence and document frequency |
| `stage3_results.json` / `stage3_nodocs.json` | PoC verdicts, with and without docs |

## Open risks

- **No ground truth.** Verdicts look right on inspection; that is not measured accuracy.
  Hand-label ~20 tickets before quoting precision/recall anywhere.
- **`unverified` will be the majority verdict**, especially for the 28 tickets with no doc
  coverage and the Vietnamese ones. Honest, but thin to demo — lead with status conflicts.
- **Vietnamese tickets** should be translated at Stage 0; cross-lingual matching degrades
  at every stage.
