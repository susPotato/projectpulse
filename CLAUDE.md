# ProjectPulseAI — working context

Read this first. It is the handoff between sessions.

**Last updated:** 2026-09-07 (intelligence layer complete)

---

## 1. What this is

**ProjectPulseAI** — an AI delivery-intelligence layer for project managers. It answers
four questions: what is at risk, why is it happening, what will it impact, what should I
do next.

Built for **PiMSathon**, an internal FPT competition.

| | |
|---|---|
| **Round 1** | Code submission ~**Fri 2026-09-11**, live evaluation **Sat 2026-09-12** (~30 min/team). Judges **run and read** the code. |
| **Round 2** | ≈2 weeks after round 1. |
| **Final** | October 2026, offline. Judged on **PM/management capability and presentation**, not only the system. |
| **Team** | **Solo.** Never plan work that assumes more than one developer. |
| **Days left** | ~4 to round 1. |

Because judges *run* the code, a one-command runnable stack and readable structure are
worth more than an extra feature.

### The one governing rule

> **Deterministic where possible, generative only for explanation.**
> Rules, graph traversal and arithmetic produce every finding. The language model's
> only job is to put those findings into sentences it is not permitted to change.

---

## 2. Documents

| File | What it is |
|---|---|
| `ProjectPulseAI_Product_Design_v2.md` | Product narrative and pitch. Revised to v2.1. |
| `ProjectPulseAI_PiMSathon.pptx` | **The pitch deck, 16 slides.** ⚠️ Written before the intelligence layer and **not reconciled with it** — it promises health scores, a 89% confidence figure, org-memory retrieval and an LLM agent, none of which exist; and it omits the precision model, `propagated_days` and `evidence_basis`, which do. See section 8. |
| `ProjectPulseAI_Architecture.md` | **Design of record.** Schema, contracts, repo layout, build sequence, risk register. Start here for anything technical. |
| `projectpulse/README.md` | How to run what exists. |
| `Layout/*.html` | 6 static UI mockups (vanilla ES5, no build). Visual language only — **no screen exists for any AI surface**; the insight/evidence screen is new design work. |

Reference repos, **read-only, never run**: `devlake/` (schema + ETL patterns we ported),
`gpt2sp/` (issue-text corpus only — 5 columns, no timestamps, cannot drive anything).

---

## 3. Where things stand

### Done and verified

**Ingestion and the whole intelligence layer are complete.** End to end: spreadsheets
in, an `InsightBundle` out.

```
27 state changes: 6 exact, 21 bounded
203 of 702 pairs orderable
4 dependency edges: 3 stated, 1 inferred
9 findings, 3 causal chains (edge / path / project)
382 tests, ~7s, no Docker + a typechecked front end
```

- **Excel path** — header contract, sha256 skip, row-identity resolution, snapshot
  differ, reject quarantine. Produces `precision='bounded'` changes.
- **Jira path** — `jira_replay` reads captured Jira-shaped JSON into `_raw_jira_*`;
  extractor and convertor downstream are the code a live connection would drive.
  Produces `precision='exact'` changes.
- **`app/intelligence/temporal/ordering.py`** — `provably_before`, the interval
  arithmetic every causal claim rests on.
- **Dependency edges — the §5.4 gap is closed.** `Predecessor` column added to the
  schedule template; `excel/dependencies.py` (pure) resolves it into a DAG and
  populates `dependencies`. Two edge sources, kept distinct: `excel_predecessor`
  (a human stated it) and `wbs_implicit` (inferred, and only where the sheet's own
  dates support `predecessor.end <= successor.start`). Parses MS-Project notation
  (`WBS-114FS+2d`). Drops, with a reason into `raw_rejects`: dangling refs,
  self-edges, edges touching a `low` identity row, and any edge that closes a
  cycle — stated edges outrank inferred ones when a loop has to break.
- **Retriever console** — `python -m scripts.demo`, a local page at :8000 for editing
  the source files and watching the effect.
- **Excel transport seam** — `excel/transport.py`. The Excel counterpart of
  `jira_replay`: `SheetSource` / `LocalFolderSource` supply `(local_path,
  display_uri)`, so moving from a synced folder to Microsoft Graph is a new class,
  not an edit to `ingest.py`. ⚠️ **`logical_name` is half the scope key and must
  never be the local path** — a temp download would otherwise present as a sheet
  never seen before, losing the baseline and fabricating a change per row with no
  lower bound. `tests/test_transport.py` guards this in both directions.

- **Intelligence layer** — `intelligence/`, built in dependency order and each stage pure
  except the pipeline:
  - `temporal/templates.py` — six named hypotheses. A chain needs a **pattern**, an
    **ordering** and a **link**; any one alone gives coincidences. Every template names
    the weakest link it accepts, and the strongest link actually found is what gets
    recorded.
  - `temporal/chains.py` — matches ordered pairs, reports lag as an **interval**, groups
    by cause so one blocked environment is one finding rather than eight sentences.
  - `schedule/graph.py` + `impact.py` — NetworkX DAG and a forward pass. The headline
    number is **`propagated_days`**: slip the dependency chain implies that the sheet does
    not yet show. Runs twice (all edges / stated only) so a finding can admit its date
    rests on an inference.
  - `context.py` — ~35 flat scalars. All aggregation happens here so ZEN only compares
    numbers.
  - `rules/` — 10 rules in a neutral form that compiles to ZEN's JDM **and** runs on a
    built-in evaluator. Both backends are tested against each other.
  - `assembler.py` — the only place a number is formatted. Rules emit `{{tokens}}`; a
    finding that cannot be fully substituted is dropped, never shipped with a literal.
  - `pipeline.py` — the only module with a session.
- **`api/schemas/insight.py`** — `InsightBundle`, frozen. Served at `GET /api/insight`.
- **`narration/`** — deterministic `fallback.py` (never a blank page) and `validator.py`,
  the 8-stage gate. The load-bearing stage is `no_literal_digits`: validation runs
  *before* substitution, so a model cannot change a number, only fail to produce one.
- **`excel/convertor.py`** — tool rows → `tasks`/`qa_items`. Was missing; without it the
  DAG joined ids with no dates on them.
- **The insight screen** — `app/api/static/insight.html`, served at `GET /insight`.
  Severity-ranked findings, each expanding to its rule trace (with the values it
  compared), its causal chain (cause → effect, basis badge, lag as a range), and its
  evidence rows. **No build step** — see §8 for why that deviates from the plan.
- **`intelligence/explain.py`** + `scripts.sync explain` + the **Calculation tab** — every
  number derived as **input → algorithm → output**. `derive()` returns a `Derivation`;
  each `Calc` step carries a question, the formula in words, the formula with real values
  substituted, and the result, so a reader can redo any line on paper. The CLI and
  `/api/explain` render the same `Derivation`, so the terminal and the page cannot
  disagree. ⚠️ An earlier ten-column table was accurate and **unreadable** — it showed
  *what* each figure was and never *how*. Don't go back to it.

  The tab also carries two charts: a **per-task timeline** (the plan's span, plus the
  overrun beyond it) and a **slip cascade** down the driving path, with a step-through
  that reveals the arithmetic one operation at a time. The two fills were validated with
  the `dataviz` skill's `validate_palette.js` against this page's panel surface in both
  modes — all six checks pass. ⚠️ **Re-run it if you change either hex**, and keep the
  dark values declared under *both* the media query and the `data-theme` scope.
- **Schedule view** — `api/schemas/gantt.py`, `pipeline.gantt_project()`, and the chart
  itself in **`api/static/gantt.js` + `gantt.css`**, shared by the Schedule *and*
  Calculation tabs so the two cannot draw different pictures of one projection.
  Read-only Gantt on one shared time window: baseline as a recessive strip, the plan in
  blue, the projected overrun in the reserved critical colour, milestone diamonds that
  turn red when at risk, dependency arrows with the driving path highlighted, and a point
  marker for a task that has a due date and no start.
  ⚠️ **`ROW`/`GROUP` in `gantt.js` must equal `--g-row`/`--g-group` in `gantt.css`** — the
  labels are HTML and the bars are SVG, so a mismatch points every label at the wrong bar
  and looks like a data bug. Guarded by a test.
- **Milestones are now real rows.** `excel/convertor.py` derives one per distinct value
  in the sheet's `Milestone` column, dated from **the latest of its tasks' dates**, and
  sets `Task.milestone_id` — a real foreign key. This deleted `pipeline._milestone_names`,
  a label workaround that carried milestone text alongside because nothing populated the
  table; row keys are only unique per sheet, so it could trade names between projects.
- **`api/static/shell.css`** — shared tokens and the tab bar. Three pages were each
  declaring their own `:root`; one file stops that recurring and makes the round-2 token
  reconciliation a single edit. `insight.html` keeps the FPT palette and aliases the
  shell's names onto it.
- **`WORKLOG.md`** — live view of what is being worked on, plus a function index
  regenerated by `python -m scripts.index_code` from the AST so it cannot drift.

### Not built yet

Retrieval/pgvector, the LLM client (`narration/client.py` — the fallback and the gate it
must pass are both ready), APScheduler, the five remaining screens (still static mockups).

### Deployed

- Architecture page: **https://arch.mintteas.org** (Cloudflare Pages project
  `projectpulse-arch`, source `site/index.html`, config `wrangler.toml`).
  Redeploy: `npx wrangler pages deploy` from the repo root.
- **Insight + Calculation snapshots**, same project, same domain:
  - https://arch.mintteas.org/insight
  - https://arch.mintteas.org/explain

  Built by `python -m scripts.publish`, which writes `site/insight.html`,
  `site/explain.html`, `site/shell.css`, `site/_headers` and the frozen JSON at
  `site/api/insight` / `site/api/explain`. **It never touches `site/index.html`.**

  ⚠️ **These are snapshots, not the app.** Pages serves static assets; FastAPI needs
  Python and a database, and Workers cannot run `psycopg`. The pages are the real
  screens with real frozen data — every finding, rule trace, chain, forward-pass row and
  piece of working — but Sync and the retriever console need the app running locally. The
  tab bar says so, with the freeze date.

  The JSON is deliberately **extensionless** (`api/insight`, not `api/insight.json`)
  because the pages fetch `/api/insight` verbatim; rewriting the fetch paths would mean
  publishing a page that is not the page the tests exercise. `_headers` supplies the
  content type Pages cannot infer.
- `mintteas.org` apex is deliberately **free** for the demo app later.

### Hosting — decided, don't re-litigate

The principle: **cut features, never foundations.** Anything expensive to retrofit
gets built right now; anything additive waits.

| | Decision |
|---|---|
| Scheduler on N instances | **Already safe.** `runner.py` takes `pg_try_advisory_lock` per source; a manual run joins an in-flight one. |
| Connection health | `pool_pre_ping=True` set — managed Postgres kills idle connections and fails over. |
| Transport | Seam built (`excel/transport.py`). Graph API is a new `SheetSource`, nothing else moves. |
| Alembic | **Skipped for round 1** — purely additive later (`init` + one autogenerated baseline, no model changes). Trigger to add it: the first deploy holding data you cannot regenerate. |
| Multi-tenancy | **Deliberately not built.** The one genuinely expensive retrofit, but this is one internal FPT portfolio; `connection_id` already namespaces sources and `Program`/`Project` give the hierarchy. Cross-BU would be a funded project, not a retrofit. |
| Where it runs | FastAPI needs a real host (Fly/Railway/Render) — Cloudflare Pages is static-only and Workers can't run `psycopg`. Neon or Supabase for Postgres+pgvector. |
| pgvector | In the image, never enabled. No `CREATE EXTENSION vector` yet — add it to `create_all()` when retrieval lands. |

**The real blocker on going live is not engineering:** `data_root` is an OneDrive-synced
folder, and a server has none. Graph API needs FPT tenant admin consent — a lead-time
item to start early, which is exactly why the synced folder was chosen for the demo.

**Nice consequence for the pitch:** hosting *improves* the precision model. Graph change
notifications collapse the scan interval from 2h to seconds, and since a scan must fall
between cause and effect (§5), tighter intervals mean **more pairs become orderable**.
The architecture gets more truthful closer to the source instead of needing more inference.
- Cloudflare OAuth login has `pages (write)` but **not** DNS write — the CNAME for
  `arch` was added by hand in the dashboard. For scripted deploys later, make an API
  token with `Pages: Edit` + `Zone → DNS: Edit`.

---

## 4. Invariants — do not break these

These are product thesis, not implementation detail. Changing one is a product
decision, not a refactor.

1. **Numbers and dates are born in exactly one place.** Today that is the ingestion
   convertors; once it exists it is `intelligence/assembler.py`. The language model
   emits `{{tokens}}`, never a digit; the server substitutes after validation.
2. **Every finding resolves to a source record.** `_raw_data_id` is stamped by the
   extractor and **copied, never re-derived**, through tool → domain. That chain is the
   evidence panel.
3. **Unprovable orderings are dropped, not hedged.** See §5.
4. **Fake the collector, never the raw table.** `jira_replay` substitutes only the
   transport, and `excel/transport.py` gives the Excel path the same seam. Seeding
   domain rows directly would bypass the differ, the identity resolver and the
   precision model, and prove nothing.
5. **The ML duration classifier is advisory only** (not yet wired). It returns a bucket,
   never a number, and `intelligence/schedule/` must never import it.
6. **A chain needs a pattern, an ordering *and* a link.** Any one alone is a bug:
   ordering alone gives coincidences, patterns alone give claims about events that may
   not have happened in that order, links alone give a graph with no time in it. The
   link actually found is recorded on the chain, so a project-level guess is never
   presented as a dependency-backed fact.
7. **One delivery project can have several source ids.** Jira and Excel each create their
   own `projects` row for the same project; `analyze_project(..., also=[...])` re-points
   them at one canonical id. Without it, a Jira event can never explain a spreadsheet
   observation — which is exactly the cross-source claim neither source can make alone.
8. **Graphiti is dropped, not deferred** — it is LLM-driven and would have a model
   assign temporal validity intervals, i.e. produce dates. See design §18.5.

---

## 5. The precision model — the core correctness rule

Our two sources carry different time information, and conflating them is how a system
starts asserting causes it cannot support.

| | Jira | Excel |
|---|---|---|
| What a sync gives | a changelog — each transition timestamped | a snapshot — only what is true now |
| `state_changes` from | collection (free) | diffing scan N against scan N−1 |
| `precision` | `exact`, both bounds equal | `bounded`, `[previous scan, this scan]` |

```python
def provably_before(a, b) -> bool:
    """a's LATEST possible time must precede b's EARLIEST."""
    return a.occurred_at < b.occurred_at_lower
```

**Three traps, all easy to fall into:**

- **Never compare `scan_id`.** Sync windows deliberately overlap, so two bounded events
  from different scans can still overlap. Same-scan is sufficient for unorderable, not
  necessary.
- **`precision='exact'` gets no shortcut.** A Jira event at 14:00 day 2 inside an Excel
  window spanning days 1–3 is genuinely unorderable. `tests/test_ordering.py` guards this.
- **A scan must fall *between* cause and effect** for the ordering to be provable. This
  is a real property of snapshot data — it means poll frequency is a product decision,
  not an ops setting.
- **Adjacent scans share a boundary instant, so two bounded changes in consecutive
  windows are *never* orderable.** `[scan1, scan2]` and `[scan2, scan3]` both permit the
  instant `scan2`. A provable ordering needs a scan strictly between them — which is why
  the demo timeline has two syncs with no edit before them, and why the whole cascade
  would prove nothing if the story moved in one step.
- **An unchanged scan must still be recorded.** It costs one row and it is evidence that
  nothing had happened yet at that moment. Returning early from the sha256 skip without
  writing a `SheetScan` — the obvious optimisation, and the original behaviour — widens
  every later interval back to the last *changed* scan. Recording it took orderable pairs
  from **121 to 203** on identical data. `SheetScan.changed` marks these.

---

## 6. Gotchas already hit — don't rediscover these

| Symptom | Cause / fix |
|---|---|
| `NOT NULL constraint failed: sync_runs.id` on SQLite | SQLite only auto-increments `INTEGER PRIMARY KEY`, never `BIGINT`. Use `BigIntPK` from `app/models/base.py`. |
| `'EmptyCell' object has no attribute 'column'` | openpyxl read-only mode. `reader.py` reads `iter_rows(values_only=True)` and works from tuple indices. |
| `Object of type datetime is not JSON serializable` | Excel date cells. Tool payloads and rejects are stored via `normalized_payload()`. |
| **A rename produced delete + insert** | `identity.py` excluded `~anon-` keys from the match pool, so an id-less row could never match itself after a rename. **Fixed**; guarded by `test_a_rename_is_matched_not_re_keyed`. This is the bug the module exists to prevent — if it recurs, causal chains become fiction. |
| `pip install --upgrade pip` corrupted a fresh venv | Skip the pip self-upgrade. |
| Causal engine finds nothing on obviously-causal data | Almost always the timeline, not the code. Cause and effect landing in one scan window are unorderable by design. Spread the demo steps and add a no-change sync between them. |
| `/insight` says it cannot load `/api/insight` | Three causes, and the page now names which: **503** = database unreachable (`docker compose up -d`, or use SQLite); **404** = no data (`python -m scripts.replay`); no JSON at all = the `.html` was opened from disk, where a relative `fetch` has no server. |
| Anything DB-related hangs for minutes | psycopg's default connect timeout. `app/db.py` now passes `connect_timeout=5` for Postgres URLs, so a missing container errors in ~12s instead of 8m44s. Don't remove it. |
| `ModuleNotFoundError: No module named 'sqlalchemy'` running a script | The system Python was used; deps are in `.venv`. Now self-healing — `_bootstrap.ensure_venv()` re-execs. If you edit it, note the re-exec must pass `-m <module>` and set `PYTHONPATH`, or `sys.path[0]` becomes the script's directory and `import scripts` breaks. |
| A new route 404s but old ones work | A stale uvicorn is holding :8000 and serving the previous build. `Get-NetTCPConnection -LocalPort 8000` finds it. The bind error only appears on the server's stderr, so it is silent from the browser's side. |
| A published page loads **unstyled**, or with no chart | Assets were written to `site/x.css` while the pages request `/static/x.css`. **Cloudflare Pages answers an unmatched path with `index.html` and status 200**, so every `%{http_code}` check passed while the browser refused HTML as a stylesheet. `scripts/publish.py` now derives each destination from the reference (`_referenced_assets`) and `_verify` fails the build if one is missing. ⚠️ **When checking a deploy, check `%{content_type}`, never the status** — and remember the edge caches the bad answer, so re-check after `cf-cache-status: REVALIDATED`. |
| `PulseGantt is not defined` on a chart page | `gantt.js` is `defer`red so it runs **after** parsing, while the inline script runs **during** it and starts a fetch immediately. A cached response beats the deferred script — and the snapshot sets `Cache-Control: max-age=300`, so it hits on the second visit. Both pages now gate rendering on `documentReady()`. Don't remove it, and don't assume "the network is slower than parsing". |
| A render error reported as "Could not reach the server" | `render` sat inside the fetch promise chain, so its exception was caught by the fetch's own `.catch`. The network `.catch` must come **before** the render step in the chain; render gets its own handler and its own message. Guarded by `test_render_errors_are_not_reported_as_network_errors`. |
| A tab bar vanishes when data loads | The nav was inside the element `render()` clears (`main.textContent = ""`). It must sit outside `#main`; `test_the_nav_is_not_inside_the_element_the_page_clears` guards it. |
| `UnicodeEncodeError: 'cp932' codec can't encode` | **All CLI source must be ASCII.** This console is cp932; a judge's may be cp1252. `argparse(description=__doc__)` prints module docstrings as `--help`, so docstrings are as exposed as `print()`. Guarded by `test_no_cli_source_contains_a_character_the_console_cannot_print`. |
| A number displays as `0%` when it is not zero | Was a real bug: `round(0.005*100)` is 0 (banker's rounding). `assembler._percent` now emits `<1%` / `>99%` so a non-zero ratio never reads as none, and 199 of 200 never reads as all. |
| A test run **hangs** instead of failing | `app/db.py` builds its engine at import from `DATABASE_URL`, defaulting to Postgres on :5433. Any test importing `app.api.main` then waits on a connect timeout. `tests/conftest.py` points it at temp SQLite unless the var is already set — don't remove it. |
| A finding appears 48 times | Chains are per cause/effect *pair*. `group_chains` collapses them per cause; the assembler emits one finding per group and caps at `max_chain_findings`. |
| `ModuleNotFoundError: No module named 'psycopg'` on **any** import of `app.db` | The venv was missing a declared dependency, so the *default* Postgres URL could not connect and only the SQLite override worked. `db.py` builds the engine at import time, so this breaks `scripts.demo` and `sync init` too — i.e. the exact path a judge runs. Fixed by `pip install "psycopg[binary]>=3.2"`. **After any venv rebuild, check `pip list` against `pyproject.toml`.** |
| Windows file locks on `.venv` | `rm -rf .venv` can fail; move it aside instead. |

---

## 7. Commands

### The short way

```bash
cd projectpulse
python -m scripts.replay    # builds pulse.db and the whole March timeline
python -m scripts.demo      # serves three tabs on :8000
```

| Tab | What it is |
|---|---|
| `/` | **Retriever console** - edit `data/demo/*.xlsx`, press Sync, watch the effect |
| `/gantt` | **Schedule** - read-only Gantt: baseline / plan / projected-overrun bars, milestone diamonds, dependency arrows, driving path highlighted |
| `/insight` | **Insight** — findings by severity, each with its rule trace, causal chain and evidence |
| `/explain` | **Calculation** — input → algorithm → output per task, plus the same Gantt |

**Insight and Calculation are a built React app** (`web/`: Vite 8, React 19, TS,
Tailwind 4). Schedule and the console are still hand-written HTML.

⚠️ **A stale uvicorn on :8000 serves the old build.** New routes then 404 while the old
ones still work, which reads as a broken feature. `scripts.demo` logs the bind error to
stderr; kill the listener and restart.

Bare `python` is fine: every `scripts.*` entry point re-execs into `.venv` if it is not
already there (printing a line when it does) and defaults `DATABASE_URL` to
`sqlite:///pulse.db`. **No venv activation and no Docker needed.** `scripts/_bootstrap.py`
does both, and it must be imported *before* any `app.*` import — `app.config` freezes
`DATABASE_URL` when it loads, so a choice made after that line is dead code.

`scripts.replay` exists because the timeline below is eight interdependent commands and
dropping one of the two **no-edit syncs** silently removes every causal chain — the page
then fills with schedule findings and no "why", which is the worst kind of demo failure:
plausible and wrong.

For Postgres instead: `docker compose up -d` then `python -m scripts.replay --postgres`.
The *library* default is still Postgres (that is the deployment target); only the CLI
entry points default to SQLite, so reading and running the code needs nothing installed.

### The long way, step by step

```bash
cd projectpulse

# Console: edit data/demo files, watch the effect. http://127.0.0.1:8000
python -m scripts.demo

python -m pytest                      # 217 tests, ~13s, no DB needed
docker compose up -d                  # postgres+pgvector on :5433 (Docker Desktop must be running)
python -m scripts.sync init

# Replay the March timeline. --now matters twice over: scan times bound every
# Excel change, AND the gaps are what make the cascade provable. The two syncs
# with no gen before them are deliberate - see §5.
python -m scripts.gen_jira_data
python -m scripts.gen_demo_data --step 0
python -m scripts.sync run --source excel       --now 2026-03-02T09:00
python -m scripts.sync run --source jira_replay --now 2026-03-04T12:00
python -m scripts.gen_demo_data --step 1        # the environment slips
python -m scripts.sync run --source excel       --now 2026-03-06T09:00
python -m scripts.sync run --source excel       --now 2026-03-10T09:00   # nothing changed
python -m scripts.gen_demo_data --step 2        # downstream reacts
python -m scripts.sync run --source excel       --now 2026-03-14T09:00
python -m scripts.sync run --source excel       --now 2026-03-18T09:00   # nothing changed
python -m scripts.gen_demo_data --step 3        # the QA backlog grows
python -m scripts.sync run --source excel       --now 2026-03-22T09:00

python -m scripts.sync order          # what can actually be ordered
python -m scripts.sync changes rejects runs

# The product. Findings, rule traces, causal chains, evidence.
python -m scripts.sync insight --also jira:Project:1:HRMS --narrative

# Show the arithmetic behind every number, so it can be checked by hand.
python -m scripts.sync explain
python -m scripts.sync explain --task WBS-114 --scalars

# Freeze a public snapshot of the two screens into site/ (does not deploy).
python -m scripts.publish
cd .. && npx wrangler pages deploy    # publishes to arch.mintteas.org

python -m scripts.index_code           # regenerate the function index in WORKLOG.md
python -m scripts.index_code --check   # fail if it is stale
```

The insight screen is at **http://127.0.0.1:8000/insight** once `scripts.demo` is running.

Also served as JSON at `GET /api/insight` (the endpoint the React route will render).

Venv is `projectpulse/.venv` (Python 3.14.5, SQLAlchemy 2.0.52). Default
`DATABASE_URL` is Postgres on :5433; export `sqlite:///pulse.db` to run without Docker.

**Note:** a uvicorn console server may still be running on :8000 from the previous
session. Kill it if the port is taken.

---

## 8. What to do next, in order

The intelligence layer and the insight screen are done. What remains is polish.

1. **`narration/client.py`.** Everything it needs exists: `fallback.py` is the thing it
   must beat, `validator.py` is the gate it must pass, and the bundle carries
   `narration_fallback_reason` so a rejected draft is visible rather than silent. Hand the
   model tokenised text and the finding list; **never let it see a digit** — substitution
   happens after validation, which is what makes the digit rule enforceable.
2. **Retrieval / pgvector.** Needs `CREATE EXTENSION vector` added to `create_all()`.
   First on the cut list — skip it if round 1 gets tight.
3. **APScheduler.** `runner.py` already holds the advisory lock, so a second instance
   joins rather than double-writes. Mostly wiring.
4. **Round 2: port the other five screens.** They are ~3,500 lines of ES5 across two
   independently-authored token sets. Reconcile them first (Family A is the FPT brand set
   the insight screen now uses; Family B is Tailwind gray).

### The front end — decided, and reversed once

Insight and Calculation are **Vite + React 19 + TypeScript + Tailwind 4**, in `web/`.

They were hand-written static HTML first, on the reasoning that judges run the code and a
failed `npm install` would mean no interface. That reasoning was right about the risk and
wrong about the fix: **every UI bug in that period was a no-build-tool bug** — an asset
path that drifted from what the page requested, a broken asset cached for four hours, a
`defer` race that left `PulseGantt` undefined, and hand-rolled CSS that simply looked
bad. Three mechanisms had to be hand-built that a bundler ships as defaults.

The risk is answered by **committing the build output** (`app/api/static/app/`), so
`python -m scripts.demo` never needs npm. Judges get one Python command; the front end
gets a real toolchain.

```bash
cd web
npm install
npm run types     # regenerate src/api-types.ts from the live OpenAPI schema
npm run build     # typecheck + build into ../app/api/static/app  (COMMIT THIS)
npm run smoke     # render both views against real payloads and assert the output
npm run dev       # hot reload, proxying /api to :8000
```

⚠️ **The committed bundle can go stale.** Vite content-hashes filenames, so a stale
bundle is invisible — the page loads and is simply out of date.
`test_the_bundle_is_not_stale` compares mtimes and fails if `web/src` is newer.

**Types are generated, never hand-written.** `npm run types` reads the server's own
OpenAPI schema, so renaming a field in `api/schemas/` is a compile error rather than a
panel silently rendering `undefined`. `api/schemas/base.Response` sets
`json_schema_serialization_defaults_required` — without it pydantic marks defaulted
fields optional and the generated types force `?? []` over lists that can never be absent.

**The Gantt stays vanilla.** `static/gantt.js` is used by the React Calculation tab
(through `GanttChart.tsx`, a ref + effect) *and* the hand-written Schedule page. Porting
it would create a second implementation, and two eventually draw two different pictures of
one projection. If it ever needs React state, port it once and delete the vanilla copy.

### Cut order if time runs short

retrieval/precedent → the LLM (ship fallback prose) → the rule-table editor (show tables
read-only) → Excel fuzzy matching (require `Task ID`).

**Never cut:** the evidence panel, the precision model, the rule trace. They are the
product.

### Why the app stays read-only — decided

There is no CRUD, and that is a product decision rather than unfinished work. The
precision model exists **because we observe someone else's spreadsheet instead of owning
it**: become the system of record and every change is `precision='exact'`, `bounded`
intervals stop existing, `provably_before` never has to refuse anything, and the most
defensible engineering in the project becomes dead code. It would also put you against
Jira and MS Project on CRUD — which slides 4 and 7 of the deck explicitly argue against.

Read-only *views* over the observed data are free and worth building (the Schedule tab is
one). Write paths are not. `resources` stays empty on purpose: the template has no
allocation data, and inventing a sheet for it is the weakest of the five mockups.

---

## 9. Style notes

- **Everything in `intelligence/` is pure except `pipeline.py`** — no session, no ORM, no
  clock — as are `snapshot_diff.py`, `identity.py`, `ordering.py`, `dependencies.py` and
  `transport.py`'s callers. Keep it that way; it is why the riskiest logic in the product
  is testable in milliseconds and why `test_chains.py` needs no database. Modules speak in
  row keys or domain ids and let the caller do the mapping.
- **`rules/engine.py` is the only module that imports `zen`**, and it carries a full
  built-in evaluator. Both backends are checked against each other in `test_rules.py`. If
  the wheel ever fails, `backend="python"` is a one-line change, not a rewrite.
- **`assembler.py` is the only module that formats a number.** If you find yourself
  writing an f-string with a figure in it anywhere else, that is invariant 1 breaking.
- Comments explain *why*, especially where a subtle failure is being prevented.
- Docstrings on modules state what the module is responsible for and what breaks if it
  is wrong.
