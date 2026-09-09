# ProjectPulseAI — working context

Read this first. It is the handoff between sessions.

**Last updated:** 2026-09-08 (live deploy, delivery confidence, risk register, OneDrive connector, Agent chat tab)

---

## 0. This session — 2026-09-08, read this before anything else in the file below

Everything in this section happened after the rest of the file was written. Where it
contradicts something further down (mainly §3's "Not built yet" and §8's "What to do
next"), **this section is current** — those are being updated too, but if anything was
missed, trust this one.

**The app is live.** `https://projectpulse.fly.dev` and `https://app.mintteas.org` both
serve the real app — Postgres on Neon, Fly app `projectpulse` (org `potato-coffee`,
region `sin`), TLS cert issued, DNS is two `DNS only` (grey-cloud) records in Cloudflare
per `DEPLOY.md`. `fly.toml`'s `min_machines_running = 0` means an idle app fully stops;
the first request after a quiet spell costs a ~15-35s cold start (machine boot + DB
connect + first narration call). Bump `min_machines_running` to 1 before a judged demo
window if that cold start is a risk.

**Narration is live and cached, not just wired.** `PULSE_NARRATION=1`,
`PULSE_NARRATION_PROVIDER=gemini`, and a real `GOOGLE_API_KEY` are set as Fly secrets —
§8 item 2 ("make one live call") is done, checked directly against the deployed app, not
just the local test server the old note describes. `app/narration/cache.py` persists a
model's phrasing in Postgres keyed by a hash of the deterministic template narrative
(the fully-substituted fallback prose *is* the fingerprint of every fact the model saw),
so a model is asked at most once per unique set of facts — first `/api/insight` call
after a data change costs ~15-20s, every one after that is sub-second, correct across
Fly's multiple/ephemeral machines. Without this the page was unusably slow in production
(every request re-asked Gemini).

**Four features landed, all with tests, all verified live in Docker and on Fly:**

- **`intelligence/confidence.py` + `InsightBundle.delivery_confidence`** — a
  deterministic `coverage x freshness` band (never a percentage) for how much to trust
  the delivery-outlook figure, matching the architecture doc's own §8.6 design that was
  never built. Precedent is honestly absent (retrieval still doesn't exist) rather than
  faked. Rendered as a chip on the Insight page next to the outlook panel.
- **A full CRUD risk register** — `app/risks/` (a new `Risk` table, deliberately with no
  `RawDataOrigin`/provenance and walled off from `app/intelligence/`, since a PM's own
  judgement is the data here, not something derived), `api/schemas/risk.py`, the `/risk`
  page. Matches `Layout/fpt-pm-risk.html`'s probability x impact matrix. The rating badge
  is a pure lookup of (likelihood, impact) — `app/risks/matrix.py` — never independently
  typed, so it can't disagree with the pair behind it.
- **A real Microsoft Graph OneDrive source** —
  `app/ingest/sources/excel/graph_auth.py` + `graph_source.py`. This closes §3's "the
  real blocker on going live" note: MSAL's device-code flow against Microsoft's own
  pre-consented public client (`14d82eec-204b-4c2f-b7e8-296a70dab67e`, the "Microsoft
  Graph PowerShell" client id — the same well-known-public-client trick the Azure CLI
  uses) needs **no Azure app registration and no FPT tenant admin consent** for
  `Files.Read`. The signed-in session is cached in Postgres
  (`app/models/onedrive.py`), not on disk, because Fly's filesystem is ephemeral and a
  deployment runs more than one machine. **Off by default** — `PULSE_EXCEL_TRANSPORT`
  stays `local` until someone runs `python -m scripts.sync onedrive login` (against
  whichever `DATABASE_URL` the live app reads — run it against Neon, not local
  Postgres, for the deployed app to see the sign-in) and sets
  `PULSE_EXCEL_TRANSPORT=graph` / `PULSE_ONEDRIVE_FOLDER` as Fly secrets. Nobody has
  signed in yet as of this note.
- **`app/agent/` — a free-form Agent chat tab (`/agent`).** Deliberately the one
  generative surface in the app with none of narration's guarantees: no fact-checking
  against project data, no tool use, no file/command access. The system prompt says so
  and tells the model to say so too rather than guess at live figures. Gemini only for
  now (the one vendor actually configured) — extending to Anthropic/OpenAI is the same
  shape of change `narration/providers.py` already demonstrates. Two small extras came
  from surveying `pimsathon-main` (see below): `link_fetch.py` reads a pasted URL
  (HTML/text/`.xlsx`/`.docx`, stdlib + `openpyxl` only, no new hard dependency; PDF is
  honestly unsupported rather than silently ignored) and folds it into the latest turn
  before the model sees it; a row of PM-oriented preset prompts (status update,
  explaining a risk, mitigations, a steering agenda) fills the box instead of sending —
  rewritten for a PM's work, not copied from that repo's dev-workflow personas.

**On `pimsathon-main`** (a sibling folder next to this repo, `Downloads/hackathon/
pimsathon-main/`): it is a different, unrelated FPT internal product — "Cowork-Local
BamBOO", a PySide6 desktop AI-agent app (chat, a Co4E workflow engine, GraphRAG,
command-execution sandboxing). Surveyed twice, once quickly and once thoroughly after
being asked "is that all it has to offer" — the honest answer both times was that almost
none of it fits, because its whole design center (let an agent act on a machine safely)
is the opposite of this app's (rules and arithmetic produce every finding). Three things
were genuinely reusable and are already taken: the MS365/OneDrive auth pattern, the
`link_fetch.py` idea, and the preset-prompts idea. `core/cron.py` (a small,
dependency-free 5-field cron parser) is flagged but **not** taken — worth a look only if
§8 item 7 (a scheduler) actually gets built.

**Fixed, not just noted:**
- The FK-ordering bug DEPLOY.md predicted ("expect to fix something on the first `fly
  deploy`") was real: `excel/source.py`'s `run_excel_sync` called `ensure_project`
  *after* `ingest_sheet`, which queues `Dependency` rows referencing the project.
  `ensure_project`'s own existence-check autoflushes the session, flushing those rows
  before the `Project` row exists — Postgres enforces the FK, SQLite (what the whole
  test suite runs on) does not, so 502 passing tests never caught it. Fixed by moving
  `ensure_project` before `ingest_sheet`.
- The retriever console (`app/api/static/index.html`) polled `/api/state` every 2
  seconds — fine on a laptop, wasteful now that the same page is served live (keeps an
  idle Fly machine awake, hits Neon every couple seconds if a tab is left open). Slowed
  to 15s.
- A real API key was twice typed into `.env.example` (the committed template) instead
  of `.env` (gitignored) — caught before either reached git. **A `.git/hooks/pre-commit`
  hook is now installed** (local to this machine only — hooks aren't tracked by git)
  that blocks any commit where a `*.env.example` has a non-empty `KEY=`/`TOKEN=`/
  `SECRET=` value. Verified against both a fake leak (blocked) and a clean file (passed).

**Environment state worth knowing:**
- `.env` / `.env.example` at `projectpulse/` now also need nothing new for the features
  above — same `GOOGLE_API_KEY` powers narration and the Agent tab.
- Fly secrets set: `DATABASE_URL` (Neon), `GOOGLE_API_KEY`, `PULSE_NARRATION=1`,
  `PULSE_NARRATION_PROVIDER=gemini`. `PULSE_EXCEL_TRANSPORT` / `PULSE_ONEDRIVE_FOLDER`
  are **not** set — OneDrive stays off until someone signs in.
- `docker-compose.yml` now has an `app` service alongside `db` — `docker compose up -d
  --build` runs the whole stack, not just Postgres.
- 578+ backend tests passing (`pytest -q` from `projectpulse/`), frontend typechecks
  and builds clean, `npm run smoke` passes.
- Everything above is committed to `main` locally; whether it has been *pushed* to
  `origin/main` (github.com/susPotato/projectpulse) depends on when this was read —
  check `git status` / `git log origin/main..HEAD`.

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
| `PiMSatho_Overview.xlsx` | **The PM's own requirements spec — read this before planning anything.** 9 sheets: 170-row Function List, tile catalogues (Program ~116 / Project ~150), 29 UI mockups across 3 Layout sheets, a 186-row Risk List, a 6-slide `Approach` vision deck, and 5 `Demo AI Sample` screens of the intended AI flow. Crucially it carries its **own P0/P1/P2 prioritisation with our deadline** (`P1: ~11/9`). Text is Vietnamese — dump it to a UTF-8 file, a cp932 console cannot print it. |
| `ProjectPulseAI_Architecture.md` | **Design of record.** Schema, contracts, repo layout, build sequence, risk register. Start here for anything technical. |
| `projectpulse/README.md` | How to run what exists. |
| `projectpulse/DEPLOY.md` | **How to put the app on a real host.** Dockerfile + fly.toml are written and unverified - no Docker daemon on the dev machine. Says exactly what can and cannot be live. |
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
596 tests, ~20s, no Docker + a typechecked front end
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
- **`intelligence/schedule/whatif.py` — recovery scenarios (the design's what-if).** Cheap
  only because `project_schedule` is a pure function of (tasks, edges): a scenario is copy,
  change one thing, re-run, diff. `_apply` builds new frozen dataclasses, so a
  **simulation is never a mutation** — which is how the app answers "what if" while
  staying read-only. Served at `GET /api/scenarios`.
  - **Two moves, both chosen because the forward pass honours them**: shorten a task, or
    give a dependency negative lag (`FS-10d`). ⚠️ **Do not express overlap as `dep_type`
    SS** — the engine reasons about finish-to-start only, so it would re-run to an
    identical answer and report zero days recovered: a wrong number that looks computed.
  - ⚠️ **Two figures, not one.** `days_earlier` is measured against doing nothing;
    `days_late` against the **original** commitment. Compressing a task moves
    `project_end_planned` too, so a scenario compared against its own plan reports "on
    time" for having moved the goalposts. The first version said "recovers 37 of 34 days".
  - ⚠️ **A healthy plan yields no scenarios**, and equivalent outcomes collapse to the
    simplest. Any plan can be made shorter, so an on-time project was being offered ways
    to finish early — true arithmetic, wrong feature.
  - **Feasibility is not modelled and must not be implied.** `resources` is empty by
    decision, so the panel carries a "what this cannot tell you" block.
- **`/team` — workload, effort and activity.** Workload on one shared window, QA hours by
  owner, and what moved per week split by *precision*. ⚠️ **`api/schemas/team.py` records
  what is deliberately absent and why** — an effort burn-down needs planned effort (no
  sheet has that column) and a date on each logged hour (`log_date` is read by the worklog
  contract and **dropped by the convertor**, the third instance of that gap); productivity
  needs both halves; the bars are calendar span, not capacity. The page says all of this
  in a panel rather than drawing it anyway.
  - A real property of these sheets: **schedule owners and QA owners are disjoint sets**,
    so a member row is one or the other. The page says "appears only on the worklog"
    rather than drawing an empty bar. Asserted.
- **`/settings` is tabbed — Narration / Sources / Rule table.** The Program settings the
  spec asks for. Sources lists watched sheets and the project pairing; **Rule table shows
  all ten rules with their thresholds and rationales**, tokens unsubstituted because the
  screen is about the rule, not today's numbers. Read-only by decision.
- ⚠️ **`--viz-plan` / `--viz-over` live at `:root` in `shell.css`, not inside `.gantt`.**
  Scoped to the component they resolved to nothing on any other chart, and bars rendered
  **invisible** — labels floating on an empty panel, with every test passing. One meaning
  per hue across the app. Re-run the dataviz validator if either hex changes, and keep the
  dark values under both the media query and the `data-theme` scope.
- **`/portfolio` — the Program screen.** `pipeline.portfolio()` folds `analyze_project`
  **per project** rather than aggregating a shortcut, so the program view cannot disagree
  with the project view. **Bands, not scores**: `critical` / `watch` / `healthy` /
  `no_data`, with `worst_severity` naming the finding that set it. ⚠️ **`no_data` is its
  own band and never green** — colouring unknown healthy is the failure this product
  argues against.
- **`app/scope.py` — which source ids are one delivery project.** Invariant 7 used to be
  a literal `also = ["jira:Project:1:HRMS"]` inside the API route; a portfolio built from
  the `projects` table would have listed HRMS **twice**. One definition, guarded by a test.
  A real deployment reads this from scope config.
- **Insight has within-project views** (Overview / Risk / Evidence) — the design's second
  tab row, as state rather than URLs so there is still one source of truth about which
  URLs exist. Only views with something behind them; Schedule and Quality have their own
  screens and are not repeated as half-versions.
- **`api/schemas/insight.py`** — `InsightBundle`, frozen. Served at `GET /api/insight`.
- **`narration/`** — deterministic `fallback.py` (never a blank page), `validator.py`, the
  8-stage gate, and `client.py`, the model. The load-bearing stage is `no_literal_digits`:
  validation runs *before* substitution, so a model cannot change a number, only fail to
  produce one.

  `client.py` is the enforcement of that. The model is handed
  `Finding.headline_template` — the prose as its rule author wrote it, tokens intact —
  never `Finding.headline`, which has the figures substituted in. ⚠️ **Do not try to
  reverse the substitution** to recover the template: searching a finished headline for
  each value breaks the moment one value is a substring of another, and this is the one
  rule that cannot rest on a heuristic. The templates are carried on the finding for
  exactly this reason.

  `build_brief` then holds *itself* to the rule: `_assert_no_quantity` runs
  `validator.contains_quantity` over the finished prompt and raises rather than send one
  with a figure in it. That is why findings in the brief are labelled **A, B, C and not
  1, 2, 3** — a numbered list is the obvious way to write it, and every digit in a brief
  is a digit a model can copy with no way to know that one was only a bullet. Facts are
  namespaced per finding (`fa_`, `fb_`) because two findings routinely carry the same
  field name. A rejected draft is retried once with the validator's objections attached,
  which is what running all eight stages after a failure was always for.

- **`narration/providers.py` — the vendor is a one-word setting.** Claude, GPT and
  Gemini each reduced to a `Drafter`: `(system, user) -> str`. `client.py` names no
  vendor, so the validator, the substitution, the retry-with-objections and the template
  fallback are identical whichever answers — `test_the_fence_treats_every_vendor_identically`
  guards that, because "Claude is reliable, skip a stage for it" is the tempting future
  edit. Pick with `PULSE_NARRATION_PROVIDER` or `--provider`; override the model id with
  `PULSE_NARRATION_MODEL` or `--llm-model`.

  Each SDK is a separate optional extra (`llm`, `llm-openai`, `llm-gemini`) imported on
  first call, so all three can be absent. Off by default (`PULSE_NARRATION=1` or
  `--model`). Every failure — no package, no key, dead socket, refusal, content filter,
  truncation, empty completion, rejected draft — lands on the template with
  `narration_fallback_reason` set, so a judge needs no package and no key and the demo
  never depends on a network call.

  ✅ **Two of the three adapters are proven end to end.** OpenAI and Gemini each complete
  a real HTTP round trip against a local server speaking that vendor's wire format —
  request, response parsing, the eight-stage gate, substitution — and return
  `narration_source: model`. Gemini's test asserts the observed request shape
  (`/v1beta/models/<model>:generateContent`, `systemInstruction` sent as a system
  instruction and not a user turn). Sibling tests prove a model that writes a digit is
  still refused, whichever vendor it is. **Anthropic's response parsing is still
  unexercised** and no commercial endpoint has been called — there are no credentials
  here.

  **That test is also the on-premise story.** `/v1/chat/completions` is what vLLM,
  Ollama, LM Studio and an internal FPT gateway all serve, so a self-hosted open model
  (26B-100B) needs **no code change** - set `OPENAI_BASE_URL` and a dummy
  `OPENAI_API_KEY`. Nothing leaves the building. ⚠️ **The OpenAI and Gemini model ids in
  `DEFAULT_MODELS` are placeholders** — confirm against the vendor's current list.
- **`excel/convertor.py`** — tool rows → `tasks`/`qa_items`. Was missing; without it the
  DAG joined ids with no dates on them.
- **The insight screen** — served at `GET /insight`. **Opens on the delivery-outlook
  figure**: the sheet's finish date, the date its own dependencies imply, and the gap
  (2026-05-29 -> 2026-07-02, +34 days) — then the driving path in forward-pass order, then
  a pressure row, then the findings. It renders the *same* `ExplainBundle` the Calculation
  tab does rather than recomputing, and the projection is fetched separately and allowed
  to fail: no outlook panel rather than no findings. ⚠️ **The hero hides itself when
  `project_slip_days <= 0`** — a healthy project showing "+0 days" in 34px type reads as
  alarming. Asserted by `web/scripts/smoke.tsx`.
- ⚠️ **`design/` is a mockup canvas, not the app.** Six dark-mode screens proposing a
  fuller UI (portfolio, project dashboard, the AI-analysis and recovery flow). Nothing
  there is implemented except the outlook panel above; do not read it as shipped.
- **The older static insight page** — `app/api/static/insight.html`.
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
- **The app shell is the design's**: a left icon rail plus an app bar with one real
  action, on all five pages. ⚠️ **`shell.css` must be linked by `web/index.html`** — the
  built pages had duplicated the chrome in Tailwind and needed no stylesheet until the
  rail did; without it `.rail`'s `<svg>` falls back to the CSS default 300x150, pushes the
  page off screen, and renders **blank** while `pytest` and `npm run smoke` both pass
  (server-side rendering never loads CSS). Only `scripts.shots` catches it.
  ⚠️ **The rail is duplicated in four files** — `Shell.tsx` and the three hand-written
  pages — and `tests/test_api.py` asserts they match. ⚠️ **Component classes like
  `.action` are scoped to themselves, not `.appbar .action`**: the built pages compose
  their bar from Tailwind, so an ancestor selector leaves icons unsized.
- **The visual language is the design's, and lives in two files that must change
  together** — `api/static/shell.css` (the three hand-written pages) and
  `web/src/components/Shell.tsx` (the two built ones). Underline tabs, an app bar
  (identity / what the data reflects / one action), `.microlabel`, `.panel`, and a
  12-column `.board`. ⚠️ **Restyle both or the app splits into two looks.**
  ⚠️ **`Panel`'s span classes are spelled out in a lookup table** — Tailwind scans source
  text, so an interpolated `md:col-span-${n}` emits no CSS and the board silently
  collapses to one column.
  ✅ **Both former traps are now guarded.** `_snapshot_nav` matches the console tab by
  **`href="/"`**, not by label text, so labels are free to change; a tab bar with tabs but
  no `/` link fails the build. And the span classes are checked twice — a source scan for
  interpolated `col-span-${...}` (comments stripped) plus a check that every class in the
  `SPAN` table is present in the *built* stylesheet.
- **Insight is a board**: outlook panel (7 cols) beside the driving path (5), then the
  **AI-analysis panel** (detected / chain / evidence / rule trace), then **Recovery
  scenarios**, then the findings. The AI panel leads with the **best-evidenced** cause rather than the most
  severe, and is composed from the same components the finding cards use.
- **Effort is real data now, and the burn chart is real with it.** The worklog
  sheet gained two columns: `Estimate` (→ `QaItem.estimate_hours`) and `Date`
  (→ `QaItem.log_date`, which the contract had parsed and the convertor had
  dropped — the third instance of that bug after `Task.phase` and
  `QaItem.assignee`). We own the template, so adding a column is the same
  legitimate move that closed the dependency-edge gap by adding `Predecessor`.
  - **What was deliberately not done:** the log dates were **not** back-derived
    from scan windows. A scan interval bounds when we *noticed* an edit, never
    when the work happened, and turning one into the other would manufacture
    precision the source never had — the exact failure §5 exists to prevent.
  - `intelligence/effort.py` (pure) reconstructs cumulative logged effort
    **backwards from the current total**: a row's creation is not a state
    change, so summing forwards starts at zero and never reaches today's
    figure. Guarded by `test_the_last_point_always_equals_the_snapshot_total`.
  - The chart is **one axis**. The blocked count and the rows added ride along
    as markers on the point that observed them, never as a second series with
    a second y-scale.
  - **`SheetScan.changed` earns its keep twice.** It was added so intervals stay
    tight (§5); it turns out to be what makes a flat stretch in the burn mean
    "nobody logged an hour" rather than "we stopped looking".
  - The demo story now has two halves that agree: the schedule shows a date
    slipping, the burn shows work stopping at the same moment, and both trace
    to one blocked environment.
    `test_the_burn_stalls_at_the_moment_the_qa_queue_blocks` fails if the
    timeline in `gen_demo_data` ever pulls them apart.
  - The Team page's "what these sheets cannot show" panel kept both of its
    entries rather than losing them: a *sloping* planned line is still refused
    (`estimate_hours` is untracked, so we have never seen the plan change), and
    so is productivity-as-output-per-effort (`progress` is self-reported).
- **`entity_label` no longer prints an identity key as a name.** A row with no
  `Task ID` gets `~anon-<16 hex>`, which is correct — it is what tracks the row
  across scans — and it rendered as a Team/Schedule row label between four
  colleagues with real WBS codes. It now falls back to the row's title where the
  caller has one. The derivation surfaces (`explain`, the Calculation tab) still
  show the key on purpose: those pages are about mechanism.
- **`web/scripts/capture-payloads.mjs` + `npm run payloads`.** `smoke.tsx` had
  claimed since it was written that its fixtures "are captured from the running
  API by `capture-payloads`" — a script that did not exist. The JSON was
  refreshed by hand, so every schema change surfaced as an `undefined` in a
  stack trace. It builds each bundle by calling the pipeline directly, so no
  server is needed and it cannot capture a stale build.
- **`api/static/shell.css`** — shared tokens and the tab bar. Three pages were each
  declaring their own `:root`; one file stops that recurring and makes the round-2 token
  reconciliation a single edit. `insight.html` keeps the FPT palette and aliases the
  shell's names onto it.
- **Exports — the two files the product hands back.** `app/exports/`.
  - `template.py` — the blank `.xlsx` a PM fills in, generated from
    `SheetContract.template_headers` rather than written by hand. `gen_demo_data`
    now reads the same tuple, so the demo file and the downloadable template are one
    sheet by construction. ⚠️ **The test that matters is the round-trip**
    (`test_a_generated_template_is_readable_by_the_real_ingester`): a template that
    opens in Excel and that our own reader refuses fails *after* a PM has filled it
    in. ⚠️ **No example rows** — an example row comes back as a real task and the
    identity resolver cannot know it was decorative. Guidance goes on a `Notes` tab,
    which the reader never opens.
  - **`document.py` — the report itself, once, in a form no file format has an
    opinion about.** Headings / paragraphs / notes / bullets / tables, built from the
    bundles, and **the only place a report formats a number** — every renderer below
    it receives strings, so a renderer that cannot see a float cannot round one. That
    is invariant 1 held across three file formats instead of one, and
    `test_no_renderer_reformats_a_ratio` pins it in all three at once. Adding
    `.pptx` for the October final is a new walker and no new content.
    ⚠️ **Do not add content to a renderer.** The moment one of them decides
    something the other two do not, the `.docx` and the `.xlsx` beside it start
    disagreeing — which is the whole failure this split exists to prevent.
  - `report.py` (`.docx`, optional `report` extra), `workbook.py` (`.xlsx`, needs no
    extra — openpyxl is already core), `markdown.py` (`.md`). The workbook puts
    **every table on its own sheet** with a frozen header and an autofilter, which is
    the only reason to choose that format: a forty-row projection sorts by implied
    slip in one click.
  - **Sections and presets.** Ten sections, three audiences (`weekly` / `steering` /
    `exec`). ⚠️ **`data_quality` is in every preset on purpose** and
    `test_no_preset_drops_the_data_quality_section` says so — it is the section a
    preset is most tempted to drop for an executive. ⚠️ **`evidence` is a sub-toggle,
    not a section**: it switches source rows on *under each finding*, so it is never
    in `resolved_sections`, and a UI that checks membership to mean "was it built"
    reports it as empty while the rows are visible beside it. That bug shipped once
    and `section.requires === "findings"` is the guard.
  - **`/reports` — the builder.** Preset, per-section tick boxes, a live preview and
    the three downloads. ⚠️ **The preview renders the same `ReportDoc` the file
    renderers walk**, so it is the file rather than a picture of it. The catalogue is
    served by `GET /api/report/options` straight from `document.py`, so a section
    added to the exporter appears in the UI with no front-end change.
  - Served at `GET /api/template/{schedule|worklog}.xlsx`,
    `GET /api/report.{docx|xlsx|md}` and `GET /api/report/{options,preview}`;
    CLI `sync template` / `sync report --format {docx,xlsx,md} --template <preset>`.
    **The blank templates are linked from `/reports`** — they had routes and nothing
    pointing at them for a long time.
- **`app/ml/duration.py` — the advisory duration classifier.** Wraps
  `omaradly/jira-task-duration-classifier` (sklearn pipeline: TF-IDF + one-hot +
  scaled numerics + logistic regression) which sorts a task into `Short` /
  `Standard` / `Long-running`. This fits invariant 5 exactly, because **the model's
  own output is a band and not a number.** The pipeline also returns probabilities;
  those are reduced to a `low`/`medium`/`high` word before leaving the module, so
  there is no float a caller could render.
  - **Invariant 5 is enforced by a test, not a comment.** `tests/test_ml.py` walks
    the AST of every module under `app/intelligence/` and fails if one imports
    `app.ml` — and checks the reverse direction too, so the dependency cannot be
    inverted instead.
  - ⚠️ **The inputs are partial, and that is the honest reason it is advisory.** The
    model wants Jira metadata (description, priority, issue type, labels, votes); a
    spreadsheet row has a title, dates and an owner. `DurationAdvice.basis` reports
    "2 of 10 inputs known" so a band from a title alone can be discounted.
  - ⚠️ **The artefact downloads and does not load on this venv, and cannot be made
    to.** Pickled by scikit-learn **1.6.1**; unpickling under 1.9 dies on
    `sklearn.compose._column_transformer._RemainderColsList`, a private class that no
    longer exists. Installing 1.6.1 to match is impossible here — **it ships no wheel
    for Python 3.14**, so pip compiles from source and there is no C toolchain (and
    3.14 is the only Python on the machine). **The classifier needs Python 3.12 or
    3.13.** `try_load()` returns that whole diagnosis as a sentence, so nobody has to
    rediscover the trail.
  - ⚠️ **Do not "fix" this by retraining locally.** Considered and rejected: the
    repo's `training/model_training.py` reads `final_cleaned.csv`, which is **not
    published** — what ships is a 100-row `final_cleaned_sample.csv`. A model refitted
    on 100 rows across three classes would carry the same name and far less meaning.
    Nor should you shim the missing private class: silently-altered behaviour in an
    advisory feature is worse than its absence.
  - The feature frame is transcribed from the model's own `api/main.py` (including
    `issue_priority` being a *string* join `issuetype__priority`, not an ordinal).
    `test_the_adapter_round_trips_a_real_pipeline` fits a genuine sklearn pipeline
    over those exact columns and predicts through our loader — which proves our
    contract and cannot prove theirs.
  - `python -m scripts.fetch_model` downloads it (note: the repo path is
    `models/<file>`, not the bare filename — that was a 404); `sync advise` prints the
    bands. `models/` is gitignored. **The product is complete without this**, which is
    the whole reason it was built as an optional advisory feature.
- **`WORKLOG.md`** — live view of what is being worked on, plus a function index
  regenerated by `python -m scripts.index_code` from the AST so it cannot drift.

### Not built yet

Retrieval/pgvector, APScheduler, the five remaining screens (still static mockups).

⚠️ **Superseded by §0:** the LLM client has now completed live calls — Gemini is
deployed and cached in production. See §0 before trusting anything else in this
subsection too far without checking there first.

### Deployed

**See §0 for the current live deploy** (`projectpulse.fly.dev` / `app.mintteas.org`,
Fly + Neon) — the real app, not a snapshot. What follows below is the earlier,
separate static-snapshot deploy on Cloudflare Pages, which is unaffected and still
serves as the offline fallback DEPLOY.md describes.

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

### What `PiMSatho_Overview.xlsx` says, and where we stand against it

**Their vision slide is our architecture.** "AI helps PMs understand what is wrong, why,
what the impact is, and what to do next" — the same four questions the narrative is
built around — over `SEE > UNDERSTAND > DETECT > EXPLAIN > RECOMMEND > ACT > LEARN`. And
in their own words: **"Not another dashboard. Not another chatbot. Not another reporting
tool."** The tile sheet is blunter still: *"Gantt đầy đủ | Drop/P2 | tốn effort nhưng
không tạo khác biệt"* — a full Gantt is explicitly **not** the differentiator.

Their **P0** list against what exists:

| P0 tile | Their note | Us |
|---|---|---|
| AI Management Brief | "Entry point cực mạnh" | ✅ the narrative |
| AI Detected Risks | "Core AI capability" | ✅ findings + severity |
| **AI Root Cause Analysis** | **"Differentiator lớn nhất"** | ✅ **and stronger than asked** — chains carry an ordering proof, an evidence basis, and we *drop* what we cannot prove |
| AI Impact Analysis | "Nối risk với delivery impact" | ✅ `propagated_days` forward pass |
| AI Recommended Actions | "Biến AI insight thành decision" | ✅ per-finding recommendation |
| Milestones at Risk | "Dữ liệu tốt để AI reason" | ✅ `milestone_risk` |
| QA Status / Blocking QA | "IT-specific" | ✅ `qa_blocked_ratio` rules |
| Quality Health | "IT-specific" | ✅ `quality_risk` |
| Program Health · Project Portfolio · Health Heatmap | "Cho BGK hiểu tổng thể trong 3 giây" | ❌ **the one real P0 gap — we are single-project, they want portfolio** |

The `Demo AI Sample` screens show the intended flow as
**AI DETECTED → MAIN REASON → SUPPORTING SIGNALS → Find Recovery Plan**. We implement the
first three, and our supporting signals are source rows rather than counts. The fourth is
what-if, which their own sheet rates *"What-if Simulation | Phase 2 | wow nhất nhưng khó
explain accuracy"* — and explaining accuracy is exactly what a pure, arithmetic forward
pass does, so it is the one place we can do the wow feature *credibly*.

⚠️ **Three places the mockups contradict the thesis. Decide them deliberately.**
1. **"Delivery confidence 61% → 91%"** and the **health score "68"** are invented numbers,
   which §4.1 forbids. The same mockup also shows **ETA `Sep 18 → Sep 12`** — a date our
   forward pass produces honestly. Prefer the date, or a real ratio ("recovers 20 of the
   34 days"). A status **band** (Healthy / Watch / Critical) from the rules is compatible;
   a score is not.
2. **"Apply This Plan to Jira"** is a write path. §8 "Why the app stays read-only" argues
   against it, and writing back changes the precision model. A product decision, not a
   refactor.
3. **Activity CRUD** (add / edit / delete / reorder) across the Schedule sheet — same
   issue.

**Lucky alignment worth knowing:** their Schedule screen columns are
*Start / Baseline Completion / Planned Completion / Phase* — exactly
`start_date` / `baseline_end` / `due_date` / `phase`, which is the contract already built.

---

## 4. Invariants — do not break these

These are product thesis, not implementation detail. Changing one is a product
decision, not a refactor.

1. **Numbers and dates are born in exactly one place.** Today that is the ingestion
   convertors; once it exists it is `intelligence/assembler.py`. The language model
   emits `{{tokens}}`, never a digit; the server substitutes after validation.
   **The model is never *shown* a digit either** — `narration/client.py` builds its
   prompt from tokenised templates and refuses to send one containing a figure. Both
   halves matter: validation stops a model changing a number, and the brief stops it
   copying one, which would be indistinguishable on the page from a computed one.
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
| **Building one SQLite database deleted another** | `scripts.replay` unlinked `--db` (default `pulse.db`) regardless of what `DATABASE_URL` actually pointed at, so seeding any other SQLite file destroyed the default one. It ate a populated `pulse.db` the first time `scripts.serve` seeded a different file. **Fixed** — it now derives the file from the target URL and removes only that. Guarded by `test_replay_only_deletes_the_database_it_builds`. |
| A judge's console cannot print a docstring | Already in this table, but it caught `scripts/serve.py` on the first run: `argparse(description=__doc__)` prints module docstrings as `--help`, so a `WARNING` emoji in a docstring is a `UnicodeEncodeError` on cp932. **All CLI source must be ASCII** — `tests/test_scripts.py` is what noticed. |
| The duration model downloads and will not load | Pickled by scikit-learn 1.6.1; 1.9 removed `_RemainderColsList`. Pinning 1.6.x is impossible on **Python 3.14** (no wheel, no compiler). Needs a 3.12/3.13 environment. The `ml` extra carries a `python_version < "3.14"` marker so the install stays clean instead of failing on meson, and `try_load()` explains the rest. |
| **`scripts.replay` says a file is open in another process** | A running server holds `pulse.db` open, so the rebuild cannot delete it - the stale-uvicorn trap in a new disguise. It used to surface as a raw `PermissionError: WinError 32`, which reads like a corrupt database; `replay.py` now names the cause and prints the fix. |
| `npm run smoke` fails with `undefined` deep in a React stack | The captured payload in `web/scripts/*.json` predates a field the page now reads. Run **`npm run payloads`** first - it rebuilds all six from the pipeline. The script did not exist for a long time even though `smoke.tsx` named it. |
| A figure interpolated into JSX is **not found** by a smoke assertion | Server-side rendering splits a text node around `{value}` with an HTML comment, so `"planned - 125h of estimates"` never appears contiguously. Assert the halves (`"planned - "`, `"h of estimates"`), not the sentence a reader sees. |
| **`scripts.shots` photographs the Schedule page as its own "chart component did not load"** | A flake in the screenshot tool, not a broken build - `node --check app/api/static/gantt.js` will pass. Per-shot throwaway profiles fixed most of it and it still happens occasionally when several pages are shot in a row. Re-shoot that page **alone** (`--page /gantt`) before believing it. |
| Windows file locks on `.venv` | `rm -rf .venv` can fail; move it aside instead. |
| **A pushed commit will not `import app.db` — a model file is missing** | `.gitignore` had a bare `models/`, meant for the ML artefact dir. That pattern matches a directory of that name **at any depth**, so it also swallowed `app/models/`. Git ignores only *untracked* files, so every table committed before that line kept working while each new one was dropped from the commit **with no warning** — `app/models/narration.py` and `app/models/onedrive.py` were both lost this way and `origin/main` could not start at all. Fixed by anchoring it to `/models/`. ⚠️ **Any new `app/models/*.py` is the case to check**, and `git status --porcelain --ignored` is what shows it. |

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
| `/reports` | **Reports** — pick an audience and sections, preview the document, download `.docx` / `.xlsx` / `.md`, and take the blank input templates |

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

python -m pytest                      # 596 tests, ~20s, no DB needed
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

# The same thing with a model phrasing the narrative instead of the template.
# Needs one of the llm extras and credentials; prints `narration: template`
# plus a reason if either is missing, and changes no finding and no figure.
python -m scripts.sync insight --narrative --model
python -m scripts.sync insight --narrative --model --provider openai
python -m scripts.sync insight --narrative --model --provider gemini --llm-model gemini-2.5-flash

# Show the arithmetic behind every number, so it can be checked by hand.
python -m scripts.sync explain
python -m scripts.sync explain --task WBS-114 --scalars

# Freeze a public snapshot of the two screens into site/ (does not deploy).
python -m scripts.publish
cd .. && npx wrangler pages deploy    # publishes to arch.mintteas.org

# The two files the product hands back.
python -m scripts.sync template --out templates       # blank .xlsx a PM fills in
python -m scripts.sync report --also jira:Project:1:HRMS --out status.docx

# The same report for a different audience, in a different format. The format
# follows the --out extension unless --format overrides it, and --section
# (repeatable) overrides the preset entirely.
python -m scripts.sync report --template exec --out brief.md
python -m scripts.sync report --template steering --out status.xlsx
python -m scripts.sync report --section summary --section projection --out short.md

# The advisory duration band per task. Prints how to get the model if absent.
python -m scripts.fetch_model          # downloads the artefact (needs ml-fetch)
python -m scripts.sync advise

# What the container runs: schema, seed only if empty, then uvicorn.
python -m scripts.serve --check        # everything but binding a port
python -m scripts.serve

# The UI check. Photographs all five pages with headless Chrome/Edge, starting
# the app itself. Run this before calling a front-end change done: a page can
# answer 200 with a collapsed layout, and curl cannot tell you.
python -m scripts.shots                # dark, into shots/ (gitignored)
python -m scripts.shots --light --page /insight

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

1. **Reconcile the pitch deck with the product.** ⚠️ **This is now the biggest risk, and
   it is not in the code.** Judges read the deck *and* run the code. The deck promises
   health scores, an 89% confidence figure, org-memory retrieval and an LLM agent — none
   of which exist — and omits the precision model, `propagated_days` and `evidence_basis`,
   which do and are the best things in it. An afternoon's work, and worth more than any
   remaining feature.
2. ✅ **Done — see §0.** Made a live call with Gemini, in production, cached so it costs
   once per fact-set rather than once per request.
3. ✅ **Done — see §0.** Deployed to Fly + Neon, both `projectpulse.fly.dev` and
   `app.mintteas.org` verified live. The FK-ordering bug this item predicted
   ("expect to fix something on the first `fly deploy`") was real — see §0 for the fix.
4. ✅ **Done, and then some.** The `.xlsx` template was *not* linked — that suspicion
   was right. It is now, from **`/reports`**, the report builder: pick an audience,
   tick sections, read the live preview, download `.docx` / `.xlsx` / `.md`, and take
   the blank input templates from the same panel. See §3 for the design (one
   `ReportDoc`, three renderers, and why no renderer is allowed to format a number).
5. **Decide the duration classifier's fate.** It is wired, tested and inert on 3.14.
   Three options, in order of how much they cost: leave it advisory-and-absent (the
   product is complete without it, and this is the round-1 answer); run it on a
   3.12/3.13 side environment; or run the model's own FastAPI service as a sidecar and
   make our adapter an HTTP client instead of a pickle loader — which would drop
   scikit-learn, pandas and joblib from this project entirely.
6. **Retrieval / pgvector.** Needs `CREATE EXTENSION vector` added to `create_all()`.
   First on the cut list — skip it if round 1 gets tight.
7. **APScheduler.** `runner.py` already holds the advisory lock, so a second instance
   joins rather than double-writes. Mostly wiring.
8. **Round 2: port the other five screens.** They are ~3,500 lines of ES5 across two
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
npm run payloads  # re-capture the payloads smoke renders against (run after any schema change)
npm run smoke     # render every view against those payloads and assert the output
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

retrieval/precedent → the duration classifier (it is advisory; the product stands
without it) → the LLM (ship fallback prose) → the rule-table editor (show tables
read-only) → Excel fuzzy matching (require `Task ID`).

**The exports are not on the cut list.** A PM circulating a `.docx` is the artefact the
whole analysis exists to produce, and the blank template is how a second project starts.

**Never cut:** the evidence panel, the precision model, the rule trace. They are the
product.

### The final check before calling front-end work done

`pytest` and `npm run smoke` prove the data and the render; neither looks at the page.
**`python -m scripts.shots`** does — it photographs all seven pages and starts the app
itself. It **checks each page's status first**, so the stale-server trap is an error and
not a picture, and gives each shot a throwaway browser profile — six launches sharing the
default one contend and one page comes back as its own error page. Compare against `design/`. This is not ceremony: it is what caught raw
`file://` paths wrapping across two lines in the evidence panel, and a hint that had
been wrong for two entries. ⚠️ **Heights are per page on purpose** — a short page in a
very tall window makes Chrome repeat the paint, which looks exactly like a
duplicate-render bug.

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
