# ProjectPulseAI — System Architecture & Application Structure

> Companion to `ProjectPulseAI_Product_Design_v2.md`. That document says **what** the
> product is and **why**. This one says **how it is built**: layers, schema, contracts,
> repository structure, and what actually shipped.
>
> **Status:** v2, revised 2026-09-23 against the running application. v1 (2026-09-06) was
> a design written before the code; where the two disagree, the code won and this
> document has been changed to match it.
>
> **What v2 changed, so a reader of v1 knows where to look:**
>
> | | |
> |---|---|
> | §0, §2 | the diagrams now show what exists: two Jira ingress paths, no retrieval producer, traceability joined on |
> | §7a **new** | why a hosted server cannot collect from this Jira, and the push path that answers it |
> | §8a **new** | `tracelink` — the out-of-process code-vs-backlog pipeline, and why it lives outside the deployed artifact |
> | §11, §12 | the real front end (a React bundle *and* seven hand-written pages) and the real tree |
> | §13 | the primary input is a real 190-issue Jira backlog, and the six defects it found |
> | §15 | Fly + Neon, the constraints that follow from 512 MB with no volume, and how data gets there |
> | §17 | a live risk register; the round-1 one is kept, marked resolved |
> | §16 | marked historical |
>
> Sections not listed above are unchanged from v1 and still describe the system: the
> governing rule (§1), the stack (§2a), the three layers (§3), identity and provenance
> (§4), the data model (§5), **the precision model (§6)** — which is the core correctness
> rule and has never needed a correction — ingestion (§7), intelligence (§8), the
> `InsightBundle` contract (§9) and the narration contract (§10).

---

## 0. At a glance

```mermaid
flowchart LR
    subgraph IN[Sources]
      J[Jira REST<br/>changelogs]
      X[Excel / CSV<br/>snapshots]
      G[Code repository]
    end

    J & X --> DB[(Postgres / SQLite<br/>raw - tool - domain)]

    DB --> R[Rules<br/>what is at risk]
    DB --> S[Schedule<br/>what it delays]
    DB --> T[Temporal<br/>why it happened]

    R & S & T --> A[Assembler<br/>every number born here]
    A --> L[LLM<br/>writes sentences only]
    L --> UI[Insight card<br/>+ click to evidence]

    J -.export.-> TL[tracelink<br/>separate package]
    G --> TL
    TL -->|JSON run dir| UI

    style A fill:#0D2B5E,color:#fff
    style L fill:#F47920,color:#fff
    style TL fill:#0F766E,color:#fff
```

Read left to right, the product is one sentence: **collect the data, compute the
finding several ways, then let a language model put it into words it is not allowed to
change.**

The two dark boxes are the whole thesis. Everything left of **Assembler** is
deterministic - rules, graph traversal, arithmetic - and produces every number and
date in the product. The **LLM** receives those findings and writes prose around them;
it cannot introduce a figure, because figures reach it as placeholders that the server
fills in afterwards. Reverse that order and this becomes a chatbot with a database.

The teal box is the later addition and it answers a different question from the other
three: not *is this project late* but *does the code agree with the backlog*. It is a
separate Python package, outside the repository that deploys, and it writes plain JSON
that the app reads as data (§8a). It is drawn joined to the picture because a reader of
the Traceability page cannot tell the difference; it is drawn apart because nothing in
the app imports it, and the app runs with it absent.

| The producers | Answers | How | State |
|---|---|---|---|
| Rules | *What is at risk?* | editable decision tables, with a trace of what fired | shipped |
| Schedule | *What will it impact?* | forward pass over the task graph | shipped |
| Temporal | *Why is it happening?* | ordered state changes, only where time permits the claim | shipped |
| Traceability | *Does the code back the claim?* | retrieval + adjudication over repo and backlog | shipped, out of process |
| Retrieval | *Has this happened before?* | pgvector over past cases | **cut** - see §8.4 |

Retrieval is listed because the original design had four producers and the fourth never
shipped. `intelligence/confidence.py` states in its own docstring that precedent is not
in the formula; the band is coverage x freshness and says so. An architecture document
that quietly drops a component it once promised is worse than one that records the cut.

---

## 1. Purpose and the one governing rule

Everything in this document is downstream of a single rule, restated from design §6:

> **Deterministic where possible, generative only for explanation.**
> Anything a rule or a graph traversal can decide, it decides.
> The model's job is to make that decision readable, not to make it.

Three corollaries drive nearly every structural decision below, and each should be
readable directly off the code:

1. **Numbers and dates are born in exactly one place** — `intelligence/assembler.py`.
   The language model never emits a digit (§10).
2. **Every displayed finding resolves to a source record** — enforced by carrying a raw-data
   pointer through every layer (§4), not by convention or by asking the model to cite.
3. **A claim the data cannot support is dropped, not hedged.** In particular, a causal
   ordering we cannot prove is removed from the output rather than shown with a caveat (§6).

If a future change violates one of these, it is a change to the product thesis, not an
implementation detail.

---

## 2. System context

```mermaid
flowchart TB
    subgraph SRC[Sources]
        J[Jira REST API<br/>changelogs = exact timestamps]
        X[Excel / CSV upload<br/>worklog / todo / schedule<br/>snapshots only]
        O[OneDrive / Graph<br/>watched workbooks]
    end

    subgraph ING[Ingestion - one pipeline, three triggers]
        C[Collector<br/>writes immutable JSON]
        P[POST /api/jira/ingest<br/>someone else's fetch]
        E[Extractor<br/>raw JSON to vendor shape]
        V[Convertor<br/>vendor to domain]
        DF[Snapshot differ<br/>scan N-1 vs N]
    end

    subgraph STORE[Postgres in production, SQLite on a laptop]
        R[(_raw_*<br/>immutable, url + fetched_at)]
        T[(_tool_*<br/>vendor shape)]
        DOM[(domain tables<br/>vendor-neutral)]
        SC[(state_changes<br/>exact or bounded)]
    end

    subgraph INTEL[Intelligence]
        RU[Rule engine<br/>ZEN + built-in evaluator]
        SCH[Schedule<br/>NetworkX graph, forecast, what-if]
        TMP[Temporal<br/>template matcher]
    end

    ML[Duration classifier<br/>advisory, optional install]

    ASM[assembler.py<br/>ALL numbers born here]
    NAR[Narration<br/>tokens only, never digits]
    VAL[Validator<br/>8 stages, hard gate]
    FB[Deterministic fallback<br/>Python renderer]

    TLR[(tracelink run dir<br/>JSON on disk)]

    UI[One FastAPI process<br/>React bundle + hand-written pages]

    J --> C
    J -.blocked from a data centre.-> P
    X --> C
    O --> C
    C --> R
    P --> R
    R --> E --> T --> V --> DOM
    T --> DF --> SC
    DOM --> RU & SCH & TMP
    SC --> TMP
    ML -.advisory feature.-> RU
    RU & SCH & TMP --> ASM
    ASM --> NAR --> VAL --> UI
    VAL -.on failure.-> FB --> UI
    R -.evidence pointer.-> UI
    TLR --> UI

    style ASM fill:#0D2B5E,color:#fff
    style VAL fill:#DC2626,color:#fff
    style SC fill:#F47920,color:#fff
```

**There is no trust boundary, because there is one origin.** The built React bundle is
committed and served by the same FastAPI process that serves the API
(`app.mount("/static", ...)`), so there is no CORS configuration anywhere in the codebase.
The two-origin Cloudflare Pages topology in the original §15 was the plan and was not
the outcome.

**Three things in this picture were not in the original design and are load-bearing now:**

1. `POST /api/jira/ingest` - the dotted path. The Jira this was built against refuses a
   request from a data centre, so the hosted server cannot collect for itself. Only the
   *fetch* moves; everything downstream is the same code (§7a).
2. The tracelink run directory - JSON files on disk, read as data, never imported (§8a).
3. Hand-written pages beside the React bundle. `/gantt`, `/jira`, `/traceability`,
   `/imports`, `/settings`, `/llm`, `/usage` are plain HTML served by the same app
   (§11).

---

## 2a. Technology stack — every dependency, and why it is there

> The full list is `projectpulse/pyproject.toml` and `projectpulse/web/package.json`;
> this section is the reasoning behind it. Versions are what round 1 was verified
> against, not floors — the floors are in the manifests.

### 2a.1 The rule that shapes the whole list

Judges run this code on their own machine in a 30-minute window. So the dependency list
is split in two, and the split is enforced rather than documented:

- **Hard dependencies** — nine packages. Without any one of them the app does not start.
- **Optional extras** — everything else. Each one, when absent, leaves a working path
  behind **and says so**: a missing LLM SDK produces the deterministic template with the
  reason attached; a missing `python-docx` disables one export; a missing `scikit-learn`
  makes the duration classifier `None` and no caller has to care.

There is no third category. Nothing is "recommended" or "should really be installed" —
either the app needs it to boot, or its absence is a named, tested code path.

### 2a.2 The web server stack

| Concern | Choice | Verified at | Why this |
|---|---|---|---|
| Language | **Python 3.13** (`>=3.11`) | 3.13-slim in the image | The ML, spreadsheet and graph ecosystems are all here. 3.14 is avoided *only* by the `ml` extra — see §8.5. |
| Web framework | **FastAPI** | 0.141.1 | The `response_model=` on every route is not decoration: the `InsightBundle` contract (§9) is Pydantic models, so the API schema and the contract are one artifact, and `openapi-typescript` generates the frontend's types from it (§2a.6). A hand-written schema would drift from the bundle within a week. |
| ASGI server | **uvicorn[standard]** | 0.52.4 | `[standard]` pulls `httptools` and `uvloop` where the platform has them. Run directly — **no gunicorn, no worker pool, no nginx.** |
| ASGI toolkit | **Starlette** (via FastAPI) | bundled | `StaticFiles`, `FileResponse`, `Response`. Never imported directly; everything goes through FastAPI's re-exports. |
| Form/multipart parsing | **python-multipart** | 0.0.32 | Not optional despite looking it. FastAPI's `File`/`Form` need it **at import time**, so its absence fails app construction, not just `POST /api/sources/upload`. |
| Validation / serialisation | **Pydantic v2** | 2.13.5 | Every bundle in §9 and §10 is a Pydantic model. Validation at the API boundary is the same code that documents it. |
| HTTP client (tests) | **httpx** (`dev` extra) | — | Backs `fastapi.testclient`. There is no HTTP client in the hard dependency set — outbound calls are the LLM SDKs' own, plus stdlib `urllib` for the FPT gateway and link fetching. |

#### The process model, in one line

> **One `uvicorn` process runs one `FastAPI` app (`app.api.main:app`), which serves the
> JSON API, the built React bundle, three hand-written HTML pages and every static asset
> from the same origin.**

That is the entire server topology. There is no reverse proxy, no static host, no
separate frontend server, no worker pool, no cache tier and no message broker. The
consequences are worth stating because each one removed a class of work:

- **No CORS, anywhere.** Frontend and API are the same origin, so the day-one CORS item
  in §2 and §15 does not exist. *(Those sections describe the two-origin Cloudflare Pages
  + Tunnel plan, which is not what shipped — see the notes there.)*
- **No `202 + poll` requirement** for `POST /api/sources/upload` or a sync, because there
  is no proxy timeout between the browser and the app to exceed.
- **No asset-path drift.** Vite emits content-hashed filenames and writes the references
  itself, so the "published page loads unstyled" failure the hand-written pages produced
  cannot recur inside the bundle.
- **One thing to restart, one place to read logs.**

#### What is actually served, and from where

Three different front-end shapes share the one mount point. This is history rather than
design — the hand-written pages came first — but the split is stable and worth knowing:

| Kind | Files | Routes |
|---|---|---|
| **Built React SPA** | `app/api/static/app/index.html` + `assets/index-*.js` (≈430 KB) + `assets/index-*.css` (≈38 KB) | `/`, `/insight`, `/portfolio`, `/programs`, `/projects`, `/team`, `/risk`, `/reports`, `/agent`, `/programs/dashboard`, `/project/dashboard` |
| **Hand-written HTML pages** | `static/gantt.html` (16 KB), `static/settings.html` (29 KB) | `/gantt`, `/settings` |
| **Shared assets** | `static/shell.css`, `static/gantt.css`, `static/gantt.js`, `static/theme.js` | mounted at `/static` |

**Why a shared `shell.css` and a vanilla `gantt.js` outlive the React rewrite.** The app
shell — rail, app bar, panel, board — is defined once and linked by *both* the React
`index.html` and the hand-written pages, because six mockups with their own `:root`
blocks is exactly how the project ended up with two conflicting token families. The Gantt
is one vanilla implementation used by both `/gantt` and the React app for the same
reason: porting it would create a second renderer, and two renderers eventually draw two
different pictures of one projection.

#### Page routes are real routes, not a hash router

Every screen has its own server route returning the same `index.html`; the React entry
picks the page from `location.pathname`. Two consequences:

- **URLs are linkable.** A judge can open `/insight` directly, and so can a bookmark.
- **There is no catch-all.** An unknown path 404s from FastAPI rather than booting the
  SPA into a client-side "not found". That is deliberate — a typo in an API path should
  not render a dashboard.

`base: "/static/app/"` in `vite.config.ts` is what makes this work: the bundle lives
under `/static/app/` but the routes are `/insight` and `/team`, so relative asset URLs
would otherwise resolve against the wrong directory.

**A guard that only covers two of eleven routes.** `_spa()` exists so a missing bundle
answers **503 with the command that fixes it** (`cd web && npm install && npm run build`)
instead of a `FileNotFoundError` 500. Only `/` and `/insight` call it; the other nine page
routes call `FileResponse(STATIC / "app" / "index.html")` directly.

This is a consistency wart, **not a live risk**: the bundle is committed to git, so every
clone, image build and deploy has it, and `npm run build` is `tsc --noEmit && vite build`
— a type error aborts *before* `emptyOutDir` wipes anything. The only window is a failure
inside the vite stage itself, which leaves the directory empty and would then 500 on nine
routes and 503 on two. Worth routing them all through `_spa()` the next time this file is
open; not worth a commit of its own.

#### Request lifecycle

```
browser ──► uvicorn (HTTP/1.1, :8080)
              │
              └─► FastAPI app.api.main:app
                    │
                    ├─ /static/*        StaticFiles      → file off disk, no Python
                    ├─ /insight, /team… FileResponse     → the built index.html
                    ├─ /gantt, /settings FileResponse    → a hand-written page
                    └─ /api/*
                         ├─ check_connection()           → 503 if the DB is unreachable
                         ├─ scope.canonical_pairing()    → invariant 7: one project, many source ids
                         ├─ session_scope()              → SQLAlchemy Session, per request
                         ├─ pipeline.<fn>(session, …)    → the deterministic bundle (§8)
                         └─ response_model=…             → Pydantic validates on the way out
```

Two behaviours in that chain are load-bearing and are guarded by tests:

- **`check_connection()` runs before the pipeline**, so a dead database is a **503 naming
  the cause**, not a stack trace. Paired with psycopg's 5-second connect timeout (§2a.3),
  a missing container reads as a missing container.
- **An empty result is a 404 carrying the fix** — `"no tasks for project 'x'. Build the
  demo timeline first: python -m scripts.replay"`. The three ways `/insight` can fail are
  distinguishable from the browser alone: 503 = database unreachable, 404 = no data, no
  JSON at all = the HTML was opened from disk where a relative `fetch` has no server.

#### Four ways it gets started

| Command | Host:port | Serves | For |
|---|---|---|---|
| `python -m scripts.serve` | `0.0.0.0:$PORT` (8080) | everything | **The container entry point.** Schema → seed-if-empty → uvicorn. |
| `python -m scripts.demo` | `127.0.0.1:8000` | everything | Local development and the judged walkthrough. Opens a browser. |
| `uvicorn app.api.main:app --reload` | as given | everything | Backend work. Serves the *committed* bundle, so frontend edits do not appear. |
| `npm run dev` (Vite) | `127.0.0.1:5173` | the SPA only, API proxied to `:8000` | Frontend work with HMR. The proxy is why the dev server needs no CORS either. |

**`scripts/serve.py` is Python, not an `entrypoint.sh`,** for two stated reasons: it can be
run and verified on the machine it was written on, and *"is the database empty"* is a
query rather than a guess. Its boot sequence:

1. Print the database URL — and **warn loudly if it is SQLite**, because a container
   filesystem does not survive a restart and the app would come back silently empty.
2. `create_all()` — the schema. There is no migration runner (§2a.3).
3. **Seed only if empty**, counting `Task` rows rather than `sync_runs` (a failed boot
   leaves a run row, which would make an empty database look seeded). `scripts.replay`
   then *generates* the demo workbooks from code and ingests them through the real
   reader, differ and identity resolver — invariant 4, so the deployed app has a genuine
   history rather than seeded domain rows.
4. **A failed seed logs and carries on.** An app serving "no data for project" is
   diagnosable from a browser; an app that exits on boot is a crash loop, and the cause is
   usually the database URL rather than anything a retry fixes.
5. `uvicorn.run("app.api.main:app", …)`, binding `0.0.0.0` because that is what a
   container needs, on `$PORT` because Fly, Railway and Render all inject it.

`scripts/_bootstrap.py` runs **before any `app.*` import** in every entry point, and the
import order that makes look wrong is deliberate: it re-execs into `.venv`'s interpreter
if the system Python was used, and it must set `DATABASE_URL` before `app.config.Settings`
freezes it at class-body execution. The library default is Postgres because that is the
deployment target; only the **CLI entry points** default to SQLite, so reading and running
the code needs nothing installed.

#### Ports, and why each is what it is

| Port | What | Note |
|---|---|---|
| **8080** | the app, in a container | `$PORT`; the value Fly/Railway/Render inject most often |
| **8000** | the app, `scripts.demo` | ⚠️ A stale uvicorn holding this port serves the *previous* build, so a new route 404s while old ones work. The bind error appears only on the server's stderr — silent from the browser. `Get-NetTCPConnection -LocalPort 8000` finds it. |
| **5433** | Postgres, host side | Not 5432, so a developer's existing local Postgres stays untouched |
| **5173** | Vite dev server | Proxies `/api` to `:8000` |

#### Runtime configuration — environment only

`app/config.py` is one frozen dataclass read from the environment at import. No config
file, no settings service.

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://pulse:pulse@localhost:5433/projectpulse` | SQLite URLs are fully supported |
| `PORT` / `HOST` | `8080` / `0.0.0.0` | Read by `scripts.serve` |
| `PULSE_DATA_ROOT` | `data/demo` | The watched (OneDrive-synced) folder |
| `PULSE_EXCEL_TRANSPORT` | `local` | `local` or `graph` — switching is a decision, not a side effect of installing the extra |
| `PULSE_SYNC_INTERVAL` | `120` (min) | Also the basis for the freshness half of §8.6 |
| `PULSE_NARRATION` | off | Off by default: the deterministic narrative is complete on its own, so the model is an improvement to opt into rather than a dependency to discover missing |
| `PULSE_NARRATION_PROVIDER` / `_MODEL` | `anthropic` / vendor default | Overridable from `/settings` without a restart |
| `PULSE_STATE_DIR` | `.pulse` | Where the API key lands, gitignored |
| `PULSE_ECHO_SQL` | off | SQLAlchemy statement logging |

#### Security posture, stated plainly

There is **no authentication, no session, no user model and no rate limit**. Anything
reachable is reachable by anyone who has the URL. What mitigates it today: the product is
read-only over ingested data; `/settings` writes only to a local file and is documented as
changeable only from the machine the app runs on; and the two genuinely dangerous routes —
the unauthenticated `/console` and its schema-dropping `/api/reset` — were **deleted
outright** before the judged window and now 404 in production. Auth is the first thing a
real deployment needs (§2a.10).

*(`app/api/main.py`'s module docstring described the deleted retriever console until
2026-09-12; it now documents the serving topology above — the routes, the three front-end
shapes, and the 503/404 failure contract — and is the short version of this subsection.)*

### 2a.3 Data layer

| Concern | Choice | Verified at | Why this |
|---|---|---|---|
| ORM | **SQLAlchemy 2.x** | 2.0.52 | Typed `Mapped[...]` declarative models, and the three physical layers (§3) are three sets of mapped classes rather than three sets of hand-written SQL. |
| Driver | **psycopg 3 (binary)** | 3.3.5 | `app/db.py` passes `connect_timeout=5` for Postgres URLs — psycopg's default is effectively minutes, and that failure mode is worse than an error: the app appears to start and only the request hangs. A test run did that for 8m44s before erroring. |
| Database (dev + judged) | **PostgreSQL 16 via `pgvector/pgvector:pg16`** | docker-compose, host port **5433** | 5433 so a developer's existing local Postgres is untouched. The pgvector image is chosen for §8.4 retrieval, which is **not built yet** — the image is there so enabling it is not also a database migration. |
| Database (production) | **Neon Postgres** | — | Managed, and the same `postgresql+psycopg://` URL, so nothing in the app knows the difference. |
| Database (no-Docker fallback) | **SQLite** | stdlib | `DATABASE_URL=sqlite:///pulse.db` runs the entire product except pgvector. This is the path a judge with no Docker takes, and `tests/conftest.py` points the test suite at temp SQLite so the ~800 tests need no database at all. ⚠️ SQLite only auto-increments `INTEGER PRIMARY KEY`, never `BIGINT` — hence `BigIntPK` in `app/models/base.py`. |
| Migrations | **none — `Base.metadata.create_all()` on boot**, plus hand-written one-shot scripts (`scripts/migrate_ids.py`, `scripts/migrate_programs.py`) | — | Deliberate for a one-developer, eight-week build. Alembic's value is a long-lived migration history across a team; what this project actually needed twice was a **re-keying** script that renames rows in place and is idempotent, which Alembic would not have written for us. ⚠️ The cost is real and is recorded in `CLAUDE.md`: a deploy that adds a column the app `SELECT`s takes production down for ~1 minute between `fly deploy` and the migration, because the health check hits a route that then errors. |

### 2a.4 Ingestion

| Concern | Choice | Verified at | Why this |
|---|---|---|---|
| Spreadsheet reading | **openpyxl** | 3.1.5 | `.xlsx` only, which is what the sources actually are. Used in **read-only mode**, so `reader.py` works from `iter_rows(values_only=True)` tuple indices — read-only cells are `EmptyCell` objects with no `.column`. |
| Spreadsheet writing | **openpyxl** | same | The blank template handed to a PM (`GET /api/template/schedule.xlsx`) is generated **from the sheet contract**, so the file handed out and the file the ingester understands are provably the same file — a test writes one and reads it back with the real reader. |
| Jira | **no client library** | — | Round 1 ingests a real Jira **export** (`export_sheet.py`) and replays captured JSON payloads (`replay.py`). A live connection would replace the transport only; `extractor.py` and `convertor.py` are already the code it would drive. Adding `jira` or `atlassian-python-api` now would be a dependency on an integration nobody has yet exercised. |
| OneDrive / SharePoint | **msal + requests** (`onedrive` extra) | — | MSAL's device-code flow signs in against Microsoft's own pre-consented public client, so `Files.Read` needs **no Azure app registration and no tenant admin consent** — which is what makes it demoable inside a corporate tenant. `LocalFolderSource` is the default and needs neither package nor sign-in. |

### 2a.5 Intelligence

| Concern | Choice | Verified at | Why this |
|---|---|---|---|
| Dependency graph | **NetworkX** | 3.6.1 | The DAG, the cycle guard and the traversal in `schedule/graph.py`. A cycle would make the forward pass non-terminating, and NetworkX raises rather than returning a wrong number. Pure-Python and dependency-free, so it costs nothing in the image. |
| Rule engine | **GoRules ZEN** (`zen-engine`) | 2.0.2 | Rules as a decision table a delivery manager can read and edit, rather than `if` statements a developer owns (§8.1). **It is a Rust wheel**, so `intelligence/rules/engine.py` is the only module that imports it and carries a complete built-in Python evaluator; the two backends are checked against each other in `tests/test_rules.py`, and `backend_name` reports which one ran. A wheel that fails to build on a competition machine degrades the rule layer's *speed*, not its existence. |
| Arithmetic, dates, statistics | **the standard library** | — | `datetime`, `dataclasses`, `difflib.SequenceMatcher` for the identity resolver, `random` for the forecast resampling. **No NumPy, no pandas, no SciPy in the hard dependency set.** Every figure in the product is a count, a ratio of counts, or arithmetic on dates a human typed (§1), and none of that needs an array library — the forecast in `schedule/forecast.py` resamples measured `baseline_end − planned_end` observations rather than fitting a distribution, precisely so that nothing chooses the shape of the answer. |

### 2a.6 Frontend

| Concern | Choice | Verified at | Why this |
|---|---|---|---|
| Framework | **React 19** | 19.2.8 | Ten screens, shared bundle-shaped state. |
| Build | **Vite 8** + `@vitejs/plugin-react` | 8.2.2 | **The built output is committed** to `app/api/static/app/`. That is the load-bearing decision: the Docker image needs no Node and no `npm install`, and `python -m scripts.demo` serves a complete UI on a machine with no JavaScript toolchain at all. The cost is that a stale bundle is a real failure mode — `npm run build` before every deploy is in `DEPLOY.md` for that reason. |
| Language | **TypeScript 5.9** | 5.9.0 | `npm run build` is `tsc --noEmit && vite build`, so a type error fails the build rather than the page. |
| API types | **openapi-typescript** | 7.5.0 | `npm run types` regenerates `src/api-types.ts` from FastAPI's own OpenAPI schema. The frontend's idea of a bundle cannot drift from the server's, because it is not independently written. |
| Styling | **Tailwind CSS 4** via `@tailwindcss/vite` | 4.3.3 | No separate PostCSS config; the Vite plugin is the whole integration. |
| Dashboard layout | **react-grid-layout** | 2.2.4 | Drag, drop and resize on the Program/Project dashboards. The only UI dependency that is not React itself — charts are hand-written SVG, and there is **no chart library**, because every chart here renders numbers the server already computed and a library's own aggregation would be a second place a figure could be born (§1). |
| Smoke tests | **vite-node** | 6.0.0 | `npm run smoke` server-renders the pages against captured payloads (`npm run payloads` rebuilds all six from the real pipeline). ⚠️ SSR splits a text node around `{value}` with an HTML comment, so assert the halves, never the sentence a reader sees. |

### 2a.7 The AI layer

| Concern | Choice | Verified at | Why this |
|---|---|---|---|
| Narration vendors | **anthropic** / **openai** / **google-genai**, plus an **FPT gateway** over plain `urllib` | anthropic 1.4.0 · openai 3.8.0 · google-genai 2.22.0 | One extra per vendor, never one big `llm` extra: whichever is chosen, the others are dead weight in the image. Each adapter imports its SDK **on first call**, so `app.narration` stays importable with none of them installed. |
| The seam | **`(system, user) -> str`** | — | That signature is the entire vendor abstraction (§10). The validator, the token substitution, the retry-with-objections and the template fallback are identical whichever adapter runs — because none of them is trusted. A self-hosted model (Ollama, vLLM, LM Studio) is the `openai` adapter with an endpoint override and a dummy key; nothing else changes. |
| No orchestration framework | **LangChain, LlamaIndex and friends are deliberately absent** | — | Their value is chains, memory and tool-routing built *around* a model. Here the model writes sentences inside a fence that already exists, and the one tool-using surface (`dashboard/agent.py`) calls the Anthropic SDK's tool loop directly against four read-only wrappers. A framework would add a place for a prompt to be assembled that is not `narration/client.py` — which is the one file that guarantees no digit reaches the model. |
| Duration classifier | **scikit-learn 1.6.x + joblib + pandas** (`ml` extra) | — | The only place pandas appears, and it is inside the optional, advisory, band-only classifier that `app/intelligence/` is **forbidden by test to import** (§8.5). ⚠️ Pinned to 1.6.x because that is what pickled the published artefact; 1.9 removed the private `_RemainderColsList` it unpickles. 1.6.x publishes no 3.14 wheel, so the extra carries a `python_version < "3.14"` marker and **needs Python 3.12 or 3.13**. |
| Model download | **huggingface-hub** (`ml-fetch` extra) | — | Needed once, to fetch the artefact. Separate from `ml` so a machine that already has the file does not install it. |

### 2a.8 Documents out

| Concern | Choice | Why this |
|---|---|---|
| `.docx` status report | **python-docx** (`report` extra) | Installed in the production image despite being an extra, because `DEPLOY.md` lists the report as always-live and python-docx has no fallback — unlike an LLM SDK, its absence removes a feature rather than degrading one. |
| `.xlsx` report and templates | **openpyxl** | Already a hard dependency; no second writer. |
| Markdown report | **stdlib string building** | — |

All three render **the same bundles the screens render** and format no number of their
own, so a `.docx` handed to a steering committee cannot disagree with the page it came
from.

### 2a.9 Build, deploy and tooling

| Concern | Choice | Why this |
|---|---|---|
| Packaging | **setuptools** via `pyproject.toml`, installed `-e` | Editable *in the image too*, deliberately: it leaves the code at `/app` so `_bootstrap.REPO`, `settings.data_root` and `scripts.replay`'s subprocess cwd all point at a real writable tree. A normal install puts them under `site-packages`, where the demo generator would write spreadsheets into the interpreter's own directory. |
| Container | **`python:3.13-slim`**, single stage | No Node stage is needed because the Vite output is committed. Dependencies are their own layer so editing app code does not reinstall them. Runs as a non-root user (`pulse`, uid 10001). |
| Local orchestration | **Docker Compose** | `docker compose up -d` is the judged artifact: Postgres+pgvector, then the app built from the same Dockerfile the deploy target uses. |
| Hosting | **Fly.io** (`sin` region, `shared-cpu-1x`, 512 MB) + **Neon Postgres** | What actually shipped — `projectpulse.fly.dev`. Supersedes §15's Cloudflare plan. The container entry point is `python -m scripts.serve`, which creates the schema, replays the demo timeline **only if the database is empty**, then serves. `PORT` is read from the environment because Fly, Railway and Render all inject it. |
| Health check | `GET /api/portfolio` | ⚠️ It must be a route the product *actually serves*. This pointed at `/api/state` until that endpoint was deleted with the retriever console — the app kept serving every real page and Fly took both machines out of the pool anyway, because a 404 from a health check reads as "unhealthy", not "that path is gone". `/api/portfolio` is the landing page's own bundle, so a machine that passes the check can answer the first request a visitor makes. |
| Tests | **pytest** + **httpx** (`dev` extra) | ~800 tests, **no database required** — `tests/conftest.py` points at temp SQLite unless `DATABASE_URL` is already set. httpx backs `fastapi.testclient`. |
| Linting | **none checked in** | Honest gap. The invariants are guarded by *tests* instead, which is where the value was: `tests/test_ml.py` walks the AST of every module under `app/intelligence/` and fails if one imports `app.ml`, and `tests/test_scripts.py` fails if any CLI source contains a character a cp932 console cannot print. A linter would have caught neither. |

### 2a.10 What the stack does not include, on purpose

| Not used | Why |
|---|---|
| **A temporal graph database** (Graphiti, Zep) | It is LLM-driven and would have a model assign temporal validity intervals — i.e. produce dates. See §18. Dropped, not deferred. |
| **A charting library** | Every chart renders numbers the server already computed; a library's own aggregation would be a second place a figure could be born (§1). Charts are hand-written SVG. |
| **An LLM orchestration framework** | See §2a.7. It would add a second place a prompt can be assembled. |
| **NumPy / SciPy in the core** | Nothing in `app/intelligence/` needs an array library, and their presence would invite the fitted distributions `schedule/forecast.py` deliberately refuses. |
| **Alembic** | See §2a.3. What was needed twice was an idempotent re-keying script, not a migration chain. |
| **A background job runner** (Celery, APScheduler, arq) | The scheduler is the one piece not wired. `ingest/runner.py` already takes a Postgres **advisory lock** per source, so a second instance joins an in-flight run rather than double-writing — which is the hard half, and it needs no broker. |
| **Go / Apache DevLake itself** | DevLake is **reference-only** here (§3). The three-layer schema and the provenance mixins are ported from `pydevlake`; no DevLake code runs. |
| **Auth / a user model** | Round 1 has no multi-tenancy and no login. Settings can only be changed from the machine the app runs on, and the API key sits in a gitignored `.pulse/narration.json` — the same bargain as a `.env` file. This is a scope decision, and it is the first thing a real deployment would need. |

---

## 3. The three physical layers

Ported from Apache DevLake, which is reference-only here — we run no Go. The Python
plugin framework `pydevlake` is a closer template than the Go core and is worth reading
before writing `app/models/base.py`.

| Layer | Table naming | Content | Mutability |
|---|---|---|---|
| **Raw** | `_raw_<source>_<endpoint>` | The API response fragment or spreadsheet row exactly as received, plus `url`, `params`, `input`, `fetched_at` | **Immutable, append-only** |
| **Tool** | `_tool_<source>_<entity>` | Parsed into the vendor's own shape, natural composite PKs | Upserted |
| **Domain** | `projects`, `issues`, `milestones`, … | Vendor-neutral model, single string PK | Upserted |

Reference implementations:

- `devlake/backend/helpers/pluginhelper/api/api_rawdata.go:30-38` — the raw row struct.
- `devlake/backend/python/pydevlake/pydevlake/model.py:102-108` — the same thing in Python
  (`RawModel`: `id, params, data, url, input, created_at`), which is what we copy.
- `devlake/backend/python/pydevlake/pydevlake/model.py:111-127` — `RawDataOrigin` with
  `set_raw_origin()` / `set_tool_origin()`, the exact provenance-propagation methods we need.

**Why three layers and not two.** Re-normalising after a schema change must not require
re-fetching from Jira, and — more important for this product — the evidence panel shows the
*original record*, which only exists if something kept it. Skipping the tool layer is
tempting for speed but it is the layer that makes an Excel column rename survivable; keep
all three, they are cheap.

**Rule: fake the collector, never the raw table.** The round-1 demo uses a `jira_replay`
collector that writes pre-generated Jira-shaped JSON straight into `_raw_jira_issues`.
Everything downstream is the identical code path that a live connection would use. This is
an honest boundary and it is defensible under questioning; seeding domain rows directly
would not be.

---

## 4. Identity, audit columns, and the evidence pointer

### Deterministic string primary keys

Every domain row's PK is a pure function of its origin:

```
"<source>:<Entity>:<connection_id>:<pk0>[:<pk1>…]"
```

e.g. `jira:Issue:1:10023`, `excel:Task:2:WBS-114`, `jira:Milestone:1:8`.

Port of `devlake/backend/core/models/domainlayer/didgen/domain_id_generator.go:52`, and of
`pydevlake/model.py:148` (`ToolModel.domain_id()`).

This buys three things at once, which is why it is worth the ugliness:

- **Idempotent re-runs** — the same source record always regenerates the same id, so every
  write is an upsert and pressing "Update now" twice is a no-op. This will be demonstrated live.
- **Collision-free multi-source** — a Jira issue and an Excel task never fight over a key.
- **Evidence resolution without a join table** — the id names its own source system.

### The six audit columns

Every tool and domain table carries these via a mixin
(`pydevlake/model.py:111-140` is the template):

| Column | Meaning |
|---|---|
| `created_at` | first insert |
| `updated_at` | last upsert — the incremental-conversion watermark |
| `_raw_data_table` | which raw table this derives from, e.g. `_raw_jira_issues` |
| `_raw_data_params` | the scope slice, e.g. `{"connection_id":1,"board_id":8}` — **indexed**; the delete key for a full refresh |
| `_raw_data_id` | FK-by-convention to `_raw_*.id` — **the evidence pointer** |
| `_raw_data_remark` | debugging breadcrumb (row index, sheet name) |

`_raw_data_id` is copied unchanged raw → tool → domain. The convertor must **not** re-derive it.

### The evidence panel query

This is the click-through that makes the product credible, and it is one join:

```sql
SELECT r.url, r.data, r.created_at AS fetched_at, r.params
FROM   issues i
JOIN   _raw_jira_issues r ON r.id = i._raw_data_id
WHERE  i.id = 'jira:Issue:1:10023';
```

Because `_raw_data_table` is stored per row, `api/routers/evidence.py` dispatches on it
generically rather than hardcoding one source.

**Known limitation, stated deliberately:** provenance is at *record* granularity, not field
granularity — one `_raw_data_id` per row. If the evidence panel later needs "which API call
produced this specific field", that needs per-field evidence rows. DevLake does not do this
either. Out of scope for round 1; note it rather than discover it later.

---

## 5. Data model

### 5.1 Entities we inherit from DevLake's domain layer

Shape ported (not the code) from `devlake/backend/core/models/domainlayer/ticket/`:
`issues`, `issue_changelogs`, `issue_comments`, `issue_labels`, `issue_assignees`,
`issue_relationships`, `boards`, `sprints`, `worklogs`, `accounts`, `users`.

Two conventions from there worth keeping verbatim:

- **`X` + `original_X` pairs** on every enum-mapped field (`status` / `original_status`).
  Never discard the vendor's raw value — the evidence panel shows the original, the rules
  read the normalized one.
- **Changelogs are one row per (event, field)**, with an id/label pair on each side
  (`from_value` / `original_from_value`).

### 5.2 Entities that are ours to build

DevLake has no equivalent of these. They are the delivery-management half of the model and
they are where the product lives:

```sql
CREATE TABLE programs (
    id              varchar(255) PRIMARY KEY,
    name            text NOT NULL,
    owner           text,
    status          varchar(50),
    start_date      date,
    end_date        date,
    description     text
    -- + 6 audit columns
);

CREATE TABLE projects (
    id              varchar(255) PRIMARY KEY,
    program_id      varchar(255) REFERENCES programs(id),
    name            text NOT NULL,
    project_manager text,
    phase           varchar(50),
    status          varchar(50),
    health_score    numeric(5,2),
    last_updated    timestamptz
);

CREATE TABLE milestones (
    id              varchar(255) PRIMARY KEY,
    project_id      varchar(255) REFERENCES projects(id),
    name            text NOT NULL,
    planned_date    date,
    baseline_date   date,          -- for schedule variance
    actual_date     date,
    status          varchar(50)
);

CREATE TABLE tasks (
    id              varchar(255) PRIMARY KEY,
    project_id      varchar(255) REFERENCES projects(id),
    milestone_id    varchar(255) REFERENCES milestones(id),
    title           text,
    status          varchar(50),
    original_status varchar(100),
    assignee        text,
    start_date      date,
    due_date        date,
    baseline_end    date,
    progress        numeric(5,2),
    duration_days   numeric(6,2)
);

-- The DAG the schedule engine traverses. See §5.4 — nothing populates this today.
CREATE TABLE dependencies (
    id              varchar(255) PRIMARY KEY,
    project_id      varchar(255) REFERENCES projects(id),
    predecessor_id  varchar(255) NOT NULL,
    successor_id    varchar(255) NOT NULL,
    dep_type        varchar(20) DEFAULT 'FS',   -- FS | SS | FF | SF
    lag_days        integer DEFAULT 0,
    source          varchar(50),                -- 'excel_predecessor' | 'jira_issuelink' | 'wbs_implicit'
    UNIQUE (predecessor_id, successor_id)
);

CREATE TABLE risks (
    id              varchar(255) PRIMARY KEY,
    project_id      varchar(255) REFERENCES projects(id),
    title           text, category varchar(50),
    severity        varchar(20), probability varchar(20), impact varchar(20),
    status          varchar(20), source varchar(50),
    detected_at     timestamptz,
    is_ai_detected  boolean DEFAULT false
);

CREATE TABLE resources (
    id                 varchar(255) PRIMARY KEY,
    project_id         varchar(255) REFERENCES projects(id),
    resource_name      text, role text,
    allocation_percent numeric(5,2),
    availability       numeric(5,2),
    period_start       date, period_end date
);

CREATE TABLE qa_items (
    id          varchar(255) PRIMARY KEY,
    project_id  varchar(255) REFERENCES projects(id),
    test_case   text, status varchar(50), priority varchar(20),
    blocked_by  varchar(255), aging_days integer
);

CREATE TABLE action_items (
    id          varchar(255) PRIMARY KEY,
    project_id  varchar(255) REFERENCES projects(id),
    insight_id  varchar(255),
    title       text, owner text, due_date date,
    priority    varchar(20), status varchar(20)
);

CREATE TABLE historical_cases (
    id           varchar(255) PRIMARY KEY,
    project_type varchar(50), issue_type varchar(50),
    root_cause   text, actions_taken text, outcome text,
    tags         text[],
    embedding    vector(768)
);
```

### 5.3 Analysis and operational tables

```sql
CREATE TABLE insights (
    id            varchar(255) PRIMARY KEY,
    project_id    varchar(255) REFERENCES projects(id),
    risk_type     varchar(50), severity varchar(20),
    as_of         timestamptz NOT NULL,
    bundle        jsonb NOT NULL,     -- the frozen InsightBundle
    narration     jsonb,              -- validated model output, tokens substituted
    narration_src varchar(20),        -- 'llm' | 'fallback'
    confidence    varchar(10),        -- HIGH | MEDIUM | LOW
    created_at    timestamptz DEFAULT now()
);

CREATE TABLE insight_evidence (
    insight_id     varchar(255) REFERENCES insights(id),
    evidence_ref   varchar(64),       -- 'ev_1' as used inside the bundle
    entity_table   varchar(64),
    entity_id      varchar(255),
    raw_data_table varchar(255),
    raw_data_id    bigint,
    PRIMARY KEY (insight_id, evidence_ref)
);

CREATE TABLE rule_traces (
    id          varchar(255) PRIMARY KEY,
    insight_id  varchar(255) REFERENCES insights(id),
    table_name  varchar(100),         -- which decision table
    table_hash  varchar(64),          -- so a trace is replayable against the exact rules
    context     jsonb,                -- the flat RuleContext fed to ZEN
    trace       jsonb,                -- ZEN's node-by-node trace
    fired       jsonb                 -- [{rule_id, condition, output}]
);

CREATE TABLE insight_feedback (
    id         bigserial PRIMARY KEY,
    insight_id varchar(255) REFERENCES insights(id),
    verdict    varchar(20),           -- accepted | rejected | edited
    edited     jsonb, note text,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE project_health_snapshot (
    project_id   varchar(255) REFERENCES projects(id),
    as_of        date,
    health_score numeric(5,2),
    schedule     numeric(5,2), quality numeric(5,2),
    resource     numeric(5,2), risk numeric(5,2),
    PRIMARY KEY (project_id, as_of)
);

-- Per-(task, scope) watermark. Port of devlake/backend/core/models/subtask_state.go
CREATE TABLE sync_state (
    task            varchar(100),
    scope           varchar(255),      -- json params identifying the slice
    prev_started_at timestamptz,
    prev_config     text,              -- hash; a change forces a full sync
    last_success_at timestamptz,
    PRIMARY KEY (task, scope)
);

CREATE TABLE sync_runs (
    id           bigserial PRIMARY KEY,
    source       varchar(50),
    trigger      varchar(20),          -- 'scheduled' | 'manual'  <- the ONLY difference
    started_at   timestamptz, finished_at timestamptz,
    status       varchar(20),          -- running | success | failed
    rows_ok      integer, rows_rejected integer,
    error        text
);

CREATE TABLE sheet_scans (
    id         bigserial PRIMARY KEY,
    source     varchar(50),
    file_path  text, sheet_name text,
    sha256     varchar(64),
    scanned_at timestamptz NOT NULL,
    row_count  integer
);

CREATE TABLE raw_rejects (
    id         bigserial PRIMARY KEY,
    sync_run_id bigint REFERENCES sync_runs(id),
    file_path  text, sheet_name text, row_index integer,
    raw_row    jsonb, reason text
);
```

`raw_rejects` is not bookkeeping — it is a product feature. A PM must see
*"12 rows in Team A's worklog did not parse"* rather than silently receive a health score
computed on 60% of the data.

### 5.4 The dependency-edge gap — decide this on day 1

**No source we have today contains dependency edges.** Verified:

- `Layout/fpt-pm-project-schedule.html` `ACTIVITIES[]` — `{name, phase, start, baseline, planned, done, playing}`, no `deps`.
- `Layout/fpt-pm-schedule.html` `PROJECTS[].milestones` — a name→`{status,date}` map, no edges.
- GPT2SP CSVs — `issuekey,title,description,storypoint,split_mark` only.
- Jira `issuelinks` exists but is sparsely populated in most enterprise projects.

Critical-path math needs a DAG. Without one, "impact analysis" collapses to *"this task is
late, therefore its milestone is late"* — true, but thin, and it hollows out one of the four
product pillars. Resolution, in priority order:

1. **Add a `Predecessor` column to the Excel schedule template.** We own that template; this
   is the cheapest real fix and it is available immediately.
2. **Derive implicit finish-to-start edges from WBS sequence** where the template has no
   predecessor — the project-schedule mockup already encodes this (each activity's `start`
   equals the previous activity's baseline end). Mark these `source='wbs_implicit'` so the
   evidence panel can be honest about where the edge came from.
3. **Ingest Jira `issuelinks`** where present — a bonus, never the primary source.

---

## 6. `state_changes` and the precision model

This is the correctness core of the root-cause feature and the most defensible engineering
in the system. It exists because our two source classes carry fundamentally different time
information.

| | Jira | Excel |
|---|---|---|
| What a sync yields | a **changelog** — each transition with its own timestamp | a **snapshot** — only what is true now |
| `state_changes` come from | collection (free) | **diffing scan N against scan N-1** |
| Timing | exact | *"sometime since the last scan"* — an interval |

```sql
CREATE TABLE state_changes (
    id                  varchar(255) PRIMARY KEY,
    entity_type         varchar(50)  NOT NULL,   -- task | milestone | qa_item | resource | risk
    entity_id           varchar(255) NOT NULL,
    field               varchar(100) NOT NULL,
    old_value           text,
    new_value           text,

    -- Bi-temporality. Read the CHECKs before touching these.
    occurred_at         timestamptz NOT NULL,    -- UPPER bound of when it happened
    occurred_at_lower   timestamptz NOT NULL,    -- LOWER bound
    precision           varchar(10) NOT NULL,    -- 'exact' | 'bounded'
    ingested_at         timestamptz NOT NULL DEFAULT now(),

    scan_id             bigint REFERENCES sheet_scans(id),
    identity_confidence varchar(10) DEFAULT 'high',  -- high | low  (see §7.3)
    source_ref          varchar(255),

    CONSTRAINT ck_interval CHECK (occurred_at_lower <= occurred_at),
    CONSTRAINT ck_exact    CHECK (precision <> 'exact' OR occurred_at_lower = occurred_at)
);
CREATE INDEX ix_sc_entity ON state_changes (entity_type, entity_id, occurred_at);
```

**The convention, stated once so two readings are impossible:**

- `precision='exact'` (Jira changelog) → `occurred_at_lower = occurred_at`, the real timestamp.
- `precision='bounded'` (Excel diff) → `occurred_at` is the **observing scan's** time,
  `occurred_at_lower` is the **previous scan's** time. The change happened somewhere in between.

### The ordering guard

```python
def provably_before(a: Event, b: Event) -> bool:
    """a's LATEST possible time must precede b's EARLIEST possible time."""
    return a.occurred_at < b.occurred_at_lower
```

Three points that are easy to get wrong and expensive to get wrong:

1. **It is interval arithmetic, never a `scan_id` comparison.** Sync windows deliberately
   overlap (§7.2), so two bounded events from *different* scans can still have overlapping
   intervals. Comparing scan ids would wrongly admit them.
2. **`precision='exact'` gets no special case.** A Jira event at 14:00 on day 2 and an Excel
   event bounded across days 1–3 are *not* orderable. The formula above already handles this
   correctly; an "optimisation" that short-circuits on exact events reintroduces the bug.
3. **Unprovable links are dropped, not downgraded.** `chains.py` emits
   `ordering_basis ∈ {'exact', 'bounded_disjoint'}` and discards anything else *before* the
   bundle is assembled. The model never sees an unprovable link, because if it sees one it
   will narrate it.

Property test (`tests/test_ordering.py`, Hypothesis): for any two events sharing a `scan_id`,
`provably_before` returns `False` in **both** directions.

**Why this matters commercially, not just technically.** Design §4.3's entire claim is about
ordering — *"the environment delay preceded the QA backlog growth."* A system that asserts
that ordering from two spreadsheet snapshots taken at the same moment is guessing. Saying
"we only claim causality where the timestamps permit it" is the single strongest answer
available to a judge asking how this differs from an LLM wrapper.

---

## 7. Ingestion

### 7.1 One path, two triggers

```python
def run_sync(source_id: str, trigger: Literal['scheduled', 'manual']) -> int:
    """Returns sync_run id. Identical work regardless of trigger."""
```

The scheduled 2-hourly job and the "Update now" button call the *same function*. `trigger`
is written to `sync_runs` and changes nothing else. Two code paths would drift, and drift
breaks the idempotency guarantee that design §13.1 requires.

**Concurrency.** A Postgres advisory lock per source:

```python
locked = session.execute(
    text("SELECT pg_try_advisory_lock(hashtext(:s))"), {"s": source_id}
).scalar()
if not locked:
    return existing_running_run_id(source_id)   # join it, do not start a second
```

The button then reports *"sync already running"* rather than launching a second writer
against the same rows.

**Long syncs and the tunnel.** `POST /sync/run` returns **202 + `job_id`** immediately and the
client polls `GET /sync/status/{job_id}`. A full sync will exceed the Cloudflare proxy
timeout, and a button that appears broken during a live demo is an avoidable loss.

### 7.2 Incremental watermarks

Ported from `devlake/backend/core/models/subtask_state.go` and its manager:

- State is keyed `(task, scope)`, so board 8 and board 9 advance independently.
- A **full sync** is forced when: the user asks, the task has never run, or `prev_config`
  changed. Config is hashed — a threshold or column-map change must not be applied to a
  partial dataset.
- `since = prev_started_at` (the *start* of the last successful run, not its end). The window
  deliberately overlaps; upserts absorb the duplicates.
- **The watermark advances only on success.** Any exception aborts before the commit, so the
  next run re-processes the same window. At-least-once plus idempotent writes is the whole
  safety argument.

### 7.3 Excel — the risk is identity, not diffing

Diffing two dataframes is an afternoon. The real hazard: hand-maintained sheets have no
stable key. Rows get reordered, inserted and renamed, and **a renamed task looks like
delete + insert, which manufactures a false state change, which can manufacture a false
causal chain.** That single bug would discredit the entire root-cause feature.

Mitigations, in order:

1. **Require a `Task ID` column** in the template we control. A sheet without it is rejected
   with a *visible* sync error — never silently fallback-matched.
2. **Fallback fuzzy matcher writes `identity_confidence='low'`**, and `chains.py` excludes
   low-confidence changes from causal chains entirely. They still appear on the timeline;
   they never become a causal link.
3. **Per-sheet sha256** in `sheet_scans`; unchanged sheets skip parsing. This is what makes
   2-hourly polling free, and most scans will find nothing changed.
4. **Pinned `column_map` per scope config**; unknown headers fail loudly. Header drift with
   hand-maintained files is a certainty, not a risk.
5. **Parse by header name, never column index.** Include a `_schema_version` cell.

### 7.4 OneDrive access

```
FileSource (ABC)
├── LocalFolderSource   # OneDrive sync client on the host; read the folder
└── GraphDriveSource    # MS Graph /drives/{id}/root/delta
```

Graph's **delta endpoint** returns only what changed since the last token — exactly the
2-hourly poll done cheaply — and gives stable item ids so renames don't create duplicates.
The risk is that registering an Azure AD app in the FPT tenant needs **admin consent**, which
can outlast the competition. `LocalFolderSource` needs zero auth, works immediately, and
uses content-hash + path as identity. Build both behind the interface; demo whichever clears.

---

## 7a. Jira ingress - two paths into the same pipeline

This section exists because of a network fact, not a design preference, and it is the
most-asked question about the deployment.

The Jira this product was built against - `insight.fsoft.com.vn/jiradc` - sits behind
Cloudflare bot scoring that judges the **class of the IP**, not an allowlist. Measured
both ways against `/rest/api/2/myself`:

| Caller | Result |
|---|---|
| A work laptop on the corporate network | `401` - reached Jira, wrong credential |
| The hosted app on Fly | `403` plus a challenge page - never reached Jira |

A 401 is the server talking. A 403 with a challenge is the edge talking. No token fixes
the second one, so **the hosted instance can never collect for itself** and no amount of
credential handling would change that.

### The split

```
local  :  live.collect  -> _raw_jira_*  -> extract -> convert -> pair
web    :  laptop fetch  -> POST /api/jira/ingest -> _raw_jira_* -> extract -> convert -> pair
                           ^ the only thing that moved
```

**Only the fetch moves.** The payload posted is the JSON Jira sent, unaltered, and the
receiving end writes it into the same raw tables `live.collect` writes and then runs the
ordinary extractor, convertor and pairing. What lands on production is indistinguishable
from a collection that happened there - same evidence trail, same tool rows, same
`precision='exact'` state changes. When the block is lifted the Collect button starts
working and this path becomes unnecessary rather than becoming load-bearing.

Three ways to drive the push, in order of who runs them:

| Path | Who | What it is |
|---|---|---|
| `scripts/jira_push.py` | whoever collected | reads this machine's raw tables and posts them in batches of 50 |
| `GET /api/jira/sync-tool?project_key=X` | a PM with a browser | a one-file PowerShell script, pre-filled with server, site and key, no Python and no install |
| `POST /api/jira/ingest` | anything | the endpoint both of the above use |

**Batching is not an optimisation.** The receiving server does real work per batch -
extract, convert, pair - so one post of two thousand issues is a request that times out
having written half of them.

### What production deliberately does not hold

The production `jira_connections` row carries **no token**. A connection with no
credential is a *link*: it binds a Jira project key to a delivery project, which is all
`/api/jira/ingest` needs. Storing a credential on a server that can never use it is a
liability with no corresponding capability.

### Who is allowed to push

`app/admin.py`. Default-deny: with no `PULSE_ADMIN_TOKEN` set, a remote write is refused,
and loopback passes without a token so a laptop behaves as it always has. The token is
compared with `hmac.compare_digest` and never stored by the app - it lives wherever
platform secrets live, which on Fly means it can be rotated but never read back.

That last property has an operational consequence worth writing down: **recovering access
to a deployed instance means rotating the token, not retrieving it.** And
`/api/jira/sync-tool` embeds the current token in the file it hands out, so a rotation
invalidates every script anybody downloaded earlier. Both are correct behaviours; both
surprise people.

---

## 8. Intelligence layer

Four producers, each independently inspectable, each emitting structured findings — never prose.

### 8.1 Rule engine (GoRules ZEN)

**`intelligence/rules/engine.py` is the only module in the codebase that imports `zen`.**
That is deliberate: if the wheel spike fails, the fallback is ~150 lines implementing our own
JSON condition schema emitting the same trace shape — a file swap, not a rewrite.
*Timebox the spike to 2 hours on day 1:* install `zen-engine`, evaluate one table, dump a
trace, confirm it carries node ids, inputs and outputs.

**Shape mismatch to plan around.** ZEN decision tables evaluate **one flat record**. Several
of our rules are aggregates (*"QA backlog grew 40% in 14 days"*). All aggregation happens in
`intelligence/context.py`, which produces a flat `RuleContext` of ~30 scalars; ZEN decides
only on scalars. Fighting ZEN to aggregate costs a day and yields nothing.

> ⚠️ **Shipped as `intelligence/rules/tables.py`, not a directory of JSON.** Same four
> tables, same editability through the settings surface; one module rather than four
> files because nothing ever needed to ship a table without shipping the code that
> reads it. The paragraph below is otherwise accurate.

Decision tables live in `intelligence/rules/tables/*.json` — `schedule_risk`, `qa_risk`,
`resource_risk`, `dependency_risk` — and are editable without a deploy, which is design §19's
mitigation for the opaque-health-score risk.

### 8.2 Schedule engine

`schedule/graph.py` builds a NetworkX `DiGraph` from `tasks` + `dependencies` + `milestones`;
`impact.py` runs the forward pass and emits `delay_days`, `affected_milestones` and
`critical_path_tasks`. There is no separate `critical_path.py` — the pass lives in
`impact.py`, and `forecast.py` and `whatif.py` joined the package later: the delivery
forecast that resamples this project's own drift and **refuses with a reason** when the
data cannot support a range, and the what-if that re-runs the graph against a changed
assumption.

**Every date and day-count in the product originates here or in §8.3 — never in §9.**

### 8.3 Temporal engine — a template matcher, not a causal discoverer

The failure mode is not difficulty, it is scope creep into general causal discovery, which
cannot be finished in five days and cannot be defended in thirty minutes.

`temporal/templates.py` declares ~6 named hypotheses, each
`(cause_selector, effect_selector, min_lag_hours, max_lag_days)`:

| Template | Cause | Effect |
|---|---|---|
| `env_delay_blocks_qa` | environment/infra milestone slip | blocked QA count growth |
| `dependency_slip_hits_milestone` | predecessor task slip | successor milestone at risk |
| `capacity_drop_slows_velocity` | allocation drop | throughput decline |
| `scope_add_drifts_delivery` | task count growth after baseline | scope drift |
| `blocker_aging_grows_backlog` | blocker aging past threshold | test backlog growth |
| `defect_spike_delays_uat` | defect count spike | UAT milestone slip |

`chains.py` finds event pairs matching a template, verifies ordering with §6's guard, and
emits the chain with its `ordering_basis`. Two days of work, and far more defensible than a
general engine: *"we assert only the causal patterns a PM told us to look for, and only where
the timestamps permit the claim."*

### 8.4 Retrieval — **cut, not deferred**

> ⚠️ **This producer was never built, and nothing pretends otherwise.** There is no
> pgvector, no `intelligence/retrieval/`, and no `historical_cases` in the running
> system. `intelligence/confidence.py` says so in its own docstring: precedent is not
> in the formula, and the band is coverage × freshness. The paragraph below is the
> plan, and its own advice — *do not fake it with hardcoded "3 similar cases"* — is
> what was followed.


pgvector cosine over `historical_cases.embedding` and document chunks. If it slips, **do not
fake it with hardcoded "3 similar cases"** — drop `precedent` from the confidence computation
and render the band as coverage × freshness with precedent shown as *"no precedent data yet."*
An honestly two-input band is stronger than a third input a judge discovers is a constant.
Thirty hand-authored `historical_cases` rows is half a day and is genuinely real.

### 8.5 The ML signal — advisory only

`app/ml/duration.py` (planned as `intelligence/signals/duration_classifier.py`) loads
`omaradly/jira-task-duration-classifier` — a ~950 KB scikit-learn TF-IDF + LogisticRegression
joblib (MIT), predicting three ordinal buckets (≤3d / 3–15d / >15d) at 0.80 accuracy
(long-running F1 0.74). It loads in-process; no model server, no GPU, no torch.

It returns `{bucket, ordinal, model_version}` — **never a probability, never a day count.**

**Three known distortions**, all planned around rather than hidden:

1. Trained on Apache public Jira, so its features include `votes_votes` /
   `watches_watch_count` / `project_category_name`, which enterprise Jira and Redmine do not
   have. Those arrive as zeros — a silent distribution shift, not an error.
2. Trained on an artificially balanced set (33,857 per class), so the class priors are wrong
   for a real portfolio. Usable for *ranking*, not as a percentage shown to a PM.
3. `created_year` / `created_month` are input features and 2026 is outside the training range.
   Verify this early; it skews every prediction quietly.

**Structural barrier, enforced not merely agreed:** `intelligence/schedule/` must not import
`app.ml`. `tests/test_ml.py` asserts it by walking the AST of every module under
`app/intelligence/` — the check outlived the planned filename. Its only legitimate use is as a
boolean feature inside a rule — `predicted_bucket == 'long' AND remaining_days < 5` → estimate
risk — with the trace displaying *"ML signal (advisory; 0.80 acc; uncalibrated; trained on
balanced Apache-Jira data)."* If a single delay-day ever traces back to it, the "the model
never produces a number" claim collapses.

The mitigation for all three distortions is the same, and it is design §18.4's argument
rather than a violation of it: **refitting TF-IDF + LogisticRegression on a client's own Jira
takes minutes on a CPU.** The thing §18.4 warns about — a model that will not transfer
between organisations — is cheap to retrain here.

### 8.6 Confidence

`coverage × freshness × precedent → HIGH | MEDIUM | LOW`, with all three inputs shown on hover.

- **coverage** — rules fired ÷ rules evaluated (from the trace)
- **freshness** — hours since the oldest contributing source's last success, read from `sync_state`
- **precedent** — count and top similarity of matched historical cases

A band, never a percentage. Design §10 already establishes why: a model-produced number is
uncalibrated and PMs anchor on it hard.

---

## 8a. Traceability - a second pipeline, out of process

The three producers in §8 all answer *is this project in trouble*. This one answers a
question no tracker can: **does the code agree with the backlog?** A ticket marked
released whose feature is nowhere in the repository, and a subsystem in the repository
that no ticket has ever mentioned, are both defects in the record rather than in the
software, and both are invisible to every other screen in this product.

### It is outside the deployed artifact, and that is the design

`tracelink` is a standalone Python package under `traceability/`, in the surrounding
workspace repository rather than in the one that ships (§12). It has its own tests, its
own CLI (`python -m tracelink`) and its own README. ProjectPulse **does not import it**. `app/api/tracelink_view.py` reads the JSON files a run leaves on disk, as data.

The reasons are worth stating because the coupling is tempting:

- The pipeline spends money. The app must never be one mis-click from a paid call.
- The pipeline needs a *checkout of the code being analysed*. A 512 MB web dyno with no
  persistent volume cannot hold one.
- A run takes minutes to hours. Nothing in a request cycle can wait for it.
- The app must work with the pipeline absent, and does: every missing artifact is
  reported on the page as a named gap **with the command that produces it**, which is the
  same rule the rest of this API follows.

### The stages

Free first, always; nothing costs money until it is asked to, and every paid call is
cached by content so re-running after a change pays only for what changed.

| | Stage | What it does | Cost |
|---|---|---|---|
| 1 | `diagnose` | what shape is this export, before anything assumes one | free |
| 2 | `tickets` | backlog rows -> normalised tickets, with the canonical project id | free |
| 3 | `corpus` | every file by role; AST symbols; import edges | free |
| 4 | `features` / `progress` | the team's own docs, claim by claim | free |
| 5 | `retrieve` | candidate files per ticket, three IDF-weighted matchers | free |
| 6 | `adjudicate` | a model reads ticket and candidates and returns a verdict with citations | **paid** |
| 7 | `verify` | every citation checked against the source; a hallucinated line number is caught here | free |
| 8 | `couple` / `shadow` / `cohorts` | structural cross-checks on the verdicts | free |
| 9 | `explain` | the narrative for the findings that survived | **paid** |
| 10 | `governance` / `gates` / `reconcile` / `cost` / `stale` | process tickets, quality gates, tracker-vs-code disagreement, spend, freshness | free |

`python -m tracelink --run <dir> pipeline ...` runs every free stage in dependency order
and then **names the paid ones with the command for each** rather than running them. A
pipeline that bills you for asking it to read a repository is a trap.

`--into ../projectpulse/traceability_runs/<name>` copies the artifacts the product page
reads to where it will find them. `TRACELINK_RUNS` (a directory of runs) or
`TRACELINK_RUN` (one run) points the app at them; the Dockerfile copies
`traceability_runs/` into the image, which is how production serves findings without the
pipeline being installed there.

### A run is a property of a project, not of the server

A run declares in its `run.json` which delivery project it is about, and the page asks
for a project id - invariant 7, the same as every other screen. A project with no run
says so plainly rather than showing another project's findings.

### What it found, which is the point

On CoWorkLocal - 190 Jira issues against the `pimsathon-main` repository - 151 tickets
were adjudicated, 74 corroborated, and **9 tickets marked `Cancelled` are in fact built
and present in the code**. That last finding was reached independently by `reconcile`
(structural) and `explain` (narrative), which is the only reason it is stated as a
finding rather than a suspicion. Total model spend for the run: $8.36.

---

## 9. The `InsightBundle` contract

**Freeze this on day 1**, as a Pydantic model in `app/api/schemas/insight.py` with a
hand-authored fixture at `tests/fixtures/bundle_env_delay.json`. Even working solo this is
the highest-leverage half hour in the plan: it lets the React screen and the narration layer
be finished and tested before a single real row exists, and it turns a five-day serial build
into something with slack in it.

```json
{
  "insight_id": "ins_01J7X...",
  "project": {"id": "jira:Board:1:HRMS", "name": "HRMS Portal V2", "phase": "Development"},
  "as_of": "2026-09-06T08:00:00Z",
  "finding": {"risk_type": "SCHEDULE", "severity": "HIGH"},

  "rules_fired": [
    {"rule_id": "schedule.milestone_slip", "table": "schedule_risk.json",
     "condition": "days_late > 10",
     "inputs": {"days_late": 12, "milestone": "Environment Setup"},
     "output": {"severity": "HIGH"},
     "evidence_ids": ["ev_1", "ev_2"]}
  ],

  "causal_chain": [
    {"step": 1, "event_id": "sc_881",
     "label": "Environment Setup milestone moved 2026-03-04 to 2026-03-16",
     "occurred_at": "2026-03-04T09:12:00Z", "occurred_at_lower": "2026-03-04T09:12:00Z",
     "precision": "exact", "ordering_basis": "exact", "evidence_ids": ["ev_1"]},
    {"step": 2, "event_id": "sc_902",
     "label": "QA blocked test count rose 4 to 17",
     "occurred_at": "2026-03-18T00:00:00Z", "occurred_at_lower": "2026-03-16T00:00:00Z",
     "precision": "bounded", "ordering_basis": "bounded_disjoint", "evidence_ids": ["ev_3"]}
  ],

  "computed_impact": {
    "delay_days": 12,
    "affected_milestones": [
      {"name": "UAT", "planned_date": "2026-05-14", "projected_date": "2026-05-26"}
    ],
    "critical_path_tasks": ["excel:Task:2:WBS-114", "excel:Task:2:WBS-118"],
    "blocked_task_count": 17
  },

  "precedent": [
    {"case_id": "hc_12", "title": "UAT env unavailable, Q3 2025",
     "similarity": 0.81, "actions_taken": ["escalated infra owner", "added QA capacity"],
     "outcome": "recovered 8 of 14 days"}
  ],

  "confidence": {
    "band": "HIGH",
    "coverage":  {"rules_fired": 4, "rules_evaluated": 5},
    "freshness": {"oldest_source": "excel_worklog", "hours_since_sync": 6},
    "precedent": {"matches": 3, "top_similarity": 0.81}
  },

  "allowed_numbers":  [12, 17, 4, 5, 3],
  "allowed_dates":    ["2026-03-04", "2026-03-16", "2026-05-14", "2026-05-26"],
  "allowed_entities": ["Environment Setup", "UAT", "HRMS Portal V2", "QA"],
  "vocabulary": {
    "delay_days":     "{{delay_days}}",
    "uat_projected":  "{{affected_milestones.0.projected_date}}",
    "blocked_tasks":  "{{blocked_task_count}}"
  }
}
```

**Invariant:** `intelligence/assembler.py` is the single place numbers are born, and
`narration/` never touches the database.

---

## 10. Narration contract

### Input

The bundle above, and nothing else. It **never** receives raw Jira JSON, raw Excel rows, free
text beyond `label`, un-fired rules, any numeric without a `vocabulary` token, or — critically
— any causal link with an unprovable ordering. Send one and the model will narrate it.

### Output — strict JSON, one object

```json
{
  "headline": "<=90 chars",
  "root_cause": "2-4 sentences",
  "impact_summary": "2-3 sentences",
  "qualitative_impacts": [
    {"dimension": "quality|cost|stakeholder_confidence", "statement": "...", "label": "judgement"}
  ],
  "recommended_actions": [
    {"rank": 1, "action": "...", "owner_role": "...", "rationale_ref": "schedule.milestone_slip"}
  ],
  "uncertainty_note": "string|null"
}
```

### Prohibitions — enforced, not requested

1. **No literal digits in prose.** Numbers appear only as `{{token}}` from `vocabulary`; the
   server substitutes *after* validation. This is the highest-leverage rule in the design: it
   makes a wrong number **structurally impossible** rather than merely detectable.
2. **No dates in any form** — ISO, month names, "mid-May", "next quarter", "Q2".
3. **No entity names** outside `allowed_entities`.
4. **No causal claim** between events that are not an adjacent pair in `causal_chain`.
5. **No probability or confidence language** — confidence is the computed band.
6. **No claim of writing to a source system** ("I've updated the Jira ticket").
7. **No adding a step** to the chain.

### Validator — `narration/validator.py`, a hard gate before display

| Stage | Check | On fail |
|---|---|---|
| `v1_schema` | parses against the Pydantic model | reject |
| `v2_no_digits` | `re.search(r'\d', prose)` after stripping `{{…}}` | reject |
| `v3_no_dates` | ISO / month-name / `Q[1-4]` / relative-time regex | reject |
| `v4_tokens` | every `{{token}}` exists in `vocabulary` | reject |
| `v5_entities` | capitalised multiword spans ∉ `allowed_entities` ∪ stopword allowlist | reject |
| `v6_causal_edges` | causal connectives ("because", "led to", "caused by", "as a result of") join only adjacent chain pairs | reject |
| `v7_refs` | every `rationale_ref` resolves to a real `rule_id` / `event_id` | reject |
| `v8_substitute` | substitute tokens, then **re-run v3 on the substituted text** | reject |

On failure: one repair retry with the violation list appended. On second failure, render
`narration/fallback.py` — a Jinja template over the same bundle producing flat, plain,
correct prose. Log the bundle and the rejected output either way.

**Build `fallback.py` before `client.py`.** It is the safety net, it is the offline
insurance if the venue network dies mid-demo, and it makes the LLM an enhancement rather
than a dependency. That is a real consideration on a competition day, not a theoretical one.

**Second guard, at the display layer:** the React insight card renders numbers from
`computed_impact` into their own components, never by parsing the narrated string. A
validator bug still cannot put a fabricated number into a number slot.

---

## 11. Frontend architecture

**Two front ends in one process, on purpose.** A built React bundle serves the pages
whose data comes from the domain model; seven hand-written HTML pages serve the ones
whose data does not. Both are served by the same FastAPI app at the same origin, share
`shell.css` for tokens and `rail.js` for navigation, and a person clicking between them
cannot tell which is which.

| | Pages | Why this half |
|---|---|---|
| React bundle (`web/`) | `/` and `/programs`, `/projects`, `/portfolio`, `/team`, `/risk`, `/reports`, `/agent`, `/insight`, `/programs/dashboard`, `/project/dashboard` | They read the domain model through typed bundles and share heavy components - the dashboard canvas, the tile builder, the project picker |
| Hand-written (`app/api/static/`) | `/gantt`, `/jira`, `/traceability`, `/imports`, `/settings`, `/llm`, `/usage` | Each reads something that is **not** the domain model - a tracker connection, a run directory on disk, a key store, a usage ledger. Putting them in the bundle would make it depend on subsystems that can legitimately be absent |

`/traceability` is the clearest case: its data comes from a different repository's run
directory. If it were a bundle route, building the app would couple to a pipeline that
need not be installed.

### No router, and no build step for half the app

`main.tsx` picks the page from `window.location.pathname` against a literal map. A router
would add a dependency and a second source of truth about which URLs exist - the rail in
`Shell.tsx` already declares them. The server serves the same `index.html` for every
bundle route.

The hand-written pages have no build step at all, which is a feature until it is a bug:
**they are not fingerprinted by a bundler, so a browser will happily serve a cached
`gantt.js` for a week.** This cost two rounds of "the fix did not deploy" before it was
diagnosed. The fix is in `app/api/main.py`:

- `_Revalidating(StaticFiles)` sends `immutable` for `/static/app/assets/` (Vite already
  content-hashes those names) and `no-cache` for everything else.
- `_page(name)` rewrites `href`/`src="/static/x.js"` to `?v=<sha256[:10]>` as the page is
  served, so a hand-written asset gets the same content-addressed cache behaviour as a
  built one without a bundler.

`no-cache` does not mean "do not cache" - it means "revalidate before use". Getting that
distinction wrong is why the first attempt still served stale files.

### Token reconciliation - the step that had to come first

The mockups contained **two conflicting design systems**, and a merge without this step
breaks pages:

| Token | Family A (`home`, `programs`, `dashboard`) | Family B (`risk`, `schedule`, `project-schedule`) |
|---|---|---|
| `--text` | `#1A2B42` | `#1A1F36` |
| `--muted` | `#64748B` | `#6B7280` |
| `--light` | `#94A3B8` | `#9CA3AF` |
| `--border` | `#E3E8EF` | `#E5E7EB` |
| `--bg` | `#F5F7FA` | `#F8F9FB` / `#F3F5F7` |
| `--red` | `#DC2626` | `#DC2626` / `#EF4444` |
| `--amber` | `#D97706` | `#F59E0B` / `#D97706` |

Family A is Tailwind **slate**, Family B is **gray**. Constant across all six and
therefore safe: `--navy:#0D2B5E`, `--blue:#1565C0`, `--orange:#F47920`, `--green:#16A34A`,
Inter at 14px.

The reconciliation landed in `app/api/static/shell.css` as one `:root`, shared by both
halves, and it carries a dark palette the mockups never had - a judge on a dark-themed
laptop should not get white-on-white. Each hand-written page sets `<html data-theme>`
from an inline script in `<head>` *before* first paint; `theme.js` only paints the icon
and handles the click, so there is no flash of the wrong palette.

### Charts are hand-written SVG

No charting library, for the reason in §2a.10: a library's own aggregation would be a
second place a number could be born. `gantt.js` is the largest of them and the one that
has needed the most care, because every visual decision it makes is a claim:

- A green point rather than a bar when a task finished but never recorded a start. The
  alternative - drawing a bar from the finish to the finish - is a zero-width lie.
- A merged row for tasks sharing dates carries the group's `actual_end`, or a finished
  group silently renders as an unfinished one.
- Search, sort and a collapsed backlog, because 190 rows is not a chart anybody reads.
  The input is replaced in place (`card.replaceChild`) with a 120 ms debounce so typing
  does not lose focus on every keystroke.

The mockups are desktop-fixed-width with **no media queries at all**, and the app still
matches that. Responsive remains undone.

---

## 12. Repository structure

**Two git repositories, one nested inside the other**, because §8a's pipeline must stay
independent of the product. The outer one is the analysis workspace: it holds the
pipeline, the codebase being analysed, and the backlog exports. The inner one is the
product, and it is the only one that deploys.

```
hackathon/                        <- OUTER repo: the analysis workspace
├── pimsathon-main/                the CoWorkLocal desktop app - the code under analysis
├── Jira Cowork Local_*.xlsx       backlog exports
├── traceability/                  the tracelink package (§8a) - bottom of this tree
└── hackathon/                     <- INNER repo: ProjectPulse, the deployed artifact
```

The inner repo:

```
hackathon/
├── ProjectPulseAI_Architecture.md        this file
├── ProjectPulseAI_Product_Design_v2.md   what the product is and why
├── ingestion-architecture-v3.md          the ingestion design in full
├── CLAUDE.md                             working context, newest section at the top
└── projectpulse/
    ├── Dockerfile  docker-compose.yml  fly.toml  pyproject.toml
    ├── DEPLOY.md  README.md  WORKLOG.md
    ├── app/
    │   ├── admin.py              who may write to a deployed instance (§7a)
    │   ├── config.py  db.py  ids.py  scope.py  units.py
    │   ├── projects.py           create / remove a delivery project, pairing included
    │   ├── imports.py            uploaded sheets, re-sync, removal
    │   ├── api/
    │   │   ├── main.py           every route; ~3,000 lines, no routers package
    │   │   ├── tracelink_view.py reads a run directory as data - never imports tracelink
    │   │   ├── schemas/          one module per bundle: gantt, team, risk, report, ...
    │   │   └── static/           the seven hand-written pages + shell.css, rail.js,
    │   │                         theme.js, gantt.js, sync-tool.ps1.tmpl, app/ (Vite out)
    │   ├── models/               flat modules, not packages: domain.py, tool.py, raw.py,
    │   │                         jira.py, llm.py, dashboard.py, uploads.py, sync.py, ...
    │   ├── ingest/
    │   │   ├── runner.py         advisory lock; watermark advanced only on success
    │   │   ├── programs.py       program resolution through the project's pairing
    │   │   ├── status.py         THE status vocabulary - one map, every source
    │   │   └── sources/
    │   │       ├── jira/         connect live replay extractor convertor store
    │   │       │                 export_sheet source
    │   │       └── excel/        reader snapshot_diff identity convertor dependencies
    │   │                         source transport graph_source graph_auth ingest
    │   ├── intelligence/
    │   │   ├── assembler.py      ALL numbers born here
    │   │   ├── context.py  confidence.py  effort.py  contention.py  explain.py
    │   │   ├── pipeline.py       the member/task rollup the Team page reads
    │   │   ├── rules/            engine.py (only zen importer) + tables.py
    │   │   ├── schedule/         graph.py impact.py forecast.py whatif.py
    │   │   └── temporal/         ordering.py templates.py chains.py
    │   ├── ml/duration.py        the advisory classifier, optional install
    │   ├── narration/            prompt+client, providers, validator, fallback, cache, store
    │   ├── llm/                  keys (Fernet) features pricing usage
    │   ├── dashboard/            catalogue, generator, fit, custom tiles, service
    │   ├── risks/                matrix, drafts, service
    │   ├── exports/              report, document (.docx), workbook (.xlsx), markdown
    │   └── agent/                chat, brief, link_fetch
    ├── web/                      Vite + React + TS; src/pages/*.tsx, src/components/*.tsx
    ├── scripts/                  25 CLIs - see below
    ├── tests/                    46 modules, 1,186 tests, no database required
    ├── traceability_runs/        run snapshots baked into the image
    └── data/                     demo upload fixtures
```

And the pipeline, beside it in the outer repo:

```
traceability/
├── tracelink/                    the package: cli, pipeline, and one module per stage
├── tests/                        24 modules, 464 tests
├── runs/                         run directories, gitignored
└── codewiki-docs/                the analysed repo's own generated docs, as an input
```

### The scripts, grouped by what they are for

`scripts/` is not a junk drawer; it is where every operation that must be repeatable but
should not be a button lives.

| Group | Scripts |
|---|---|
| Run it | `serve.py` (schema, seed-if-empty, serve - the container entry point), `demo.py`, `replay.py` |
| Make data | `gen_demo_data.py`, `gen_demo_jira.py`, `gen_demo_upload.py`, `gen_jira_data.py`, `gen_portfolio_data.py`, `seed_extras.py`, `from_jira_export.py` |
| Jira operations | `jira_push.py` (§7a), `jira_rebuild.py` |
| Surgery on a live database | `drop_source.py`, `carry_milestones.py`, `restore_milestones.py`, `migrate_ids.py`, `migrate_programs.py`, `migrate_task_columns.py` |
| Everything else | `sync.py` (the ingestion CLI), `secret.py`, `fetch_model.py`, `probe_fpt.py`, `publish.py`, `shots.py`, `index_code.py` |

**The surgery scripts each exist because a general operation was wrong for a specific
case**, and their docstrings say which. `drop_source.py` exists because `projects.remove`
deletes a delivery project *and every source paired onto it* - correct when the project
is going away, and exactly wrong when one source has superseded another. Running the
general one would have taken 380 tasks and 540 state changes with it.

`restore_milestones.py` exists because the first version of that surgery destroyed 29
milestones that no source system can rebuild - Jira's `components` field is empty on all
194 issues, so the feature grouping lived only in the spreadsheet. It was recovered from
a file copy. `--keep-milestones` was added to `drop_source.py` the same day.

---

## 13. The data the app runs on

**The primary input is now a real backlog, not the simulator.** CoWorkLocal - 190 Jira
issues collected over HTTP from `insight.fsoft.com.vn/jiradc`, with 523 changelog
entries - replaced the spreadsheet that fed the project originally. The simulator is
still there and still seeds an empty database, but it is no longer what the demo shows.

| | |
|---|---:|
| tasks | 190 |
| issue types | Story 173 · PM Task 16 · Product 1 |
| exact state changes | 351 |
| tasks with a real finish date | 151 |
| tasks with a real start date | **2** |
| features (milestones) | 29, grouping 180 tasks |

### What real data broke, and why each break is worth recording

Every row below was a defect the synthetic data could not have found, because the
simulator produced well-formed inputs and a real board does not.

| What was wrong | Consequence | Fix |
|---|---|---|
| `STATUS_MAP` had never heard of `Release`, `Cancelled` or `Re-Open` - the three commonest states on this board | 152 of 190 tasks normalised to `OTHER`, counted as open, so 129 released tickets reported overdue | one shared vocabulary in `app/ingest/status.py`, used by every source |
| `convert_issues` scoped by `connection_id` alone | connection 1 held 4 HRMS rows beside 190 CoWorkLocal ones, so HRMS issues were filed into CoWorkLocal | scope by `project_key` too |
| Tasks keyed by numeric issue id | the Team page showed `1101143` where a person expects `COWORKLOCAL-10` | key by issue key; changelogs resolve id -> key to match |
| "Start = first move out of To Do" | 153 of 156 tickets go `To Do -> Release` in one step, so that event *is* the close: 151 tasks got start == finish and the chart drew zero-width bars | `STARTED = {IN_PROGRESS, BLOCKED}`. Two real starts, and the chart says so rather than inventing 151 |
| `rollup_by_parent` keyed on `parent` | `parent` is the spreadsheet's nesting and is empty on Jira rows, so all 190 folded into `(none)` and the page claimed "189 planning tasks" | fall back to the ticket's own key |
| "Named on the row" took every `Label: value` pair | email subjects were printed as people | `NOT_PEOPLE` filter on the label |

The pattern is one thing said six ways: **a vocabulary, a scope or a key that was only
ever tested against data we authored.** That is the argument for running a real export
through the whole pipeline before believing any number on any page.

### What is still wrong with the data, stated rather than smoothed over

- **173 of 190 tickets carry the same due date, `2026-08-31`.** That is a bulk field-set,
  not a schedule. Every "early" and "late" figure in the product is therefore measured
  against one keystroke. The arithmetic is right and the input is not.
- **The role field and the assignee field disagree on 151 tickets.** Jira's
  `customfield_10228` carries FSG/FNS group codes and a short username (`QuanDh14`);
  `assignee` carries a person (`Quan Do Hong`). Both are shown in places and they are not
  the same thing.
- **`reconcile` reports a degenerate axis** - `tracker 'done' on all 63 rows`. This
  survived the swap from spreadsheet to Jira because the cause is join width (median 30),
  not the source.

### The simulator, which is still the fallback

`scripts/gen_demo_data.py` and friends walk a virtual clock over 18 months across
1 program / 4 projects, ~250 tasks, ~40 milestones, applying scripted perturbations. The
decisive detail is unchanged: it emits **the artifacts a real source would emit** -
Jira-shaped changelog JSON into `_raw_jira_*`, dated `.xlsx` files consumed by the real
reader - not seeded domain rows. Nobody can claim the answer is hardcoded, and the Excel
differ, the sha256 skip, the identity resolver and the bounded-precision path all get
genuinely exercised.

**Why not GPT2SP as the dataset.** Its 16 CSVs (23,313 rows) are exactly
`issuekey,title,description,storypoint,split_mark` - zero timestamps, zero status, zero
assignee, zero links. It cannot produce a single `state_changes` row, therefore it cannot
drive the rule engine, the temporal engine, or the schedule engine. It is useful for
issue *text* and as input to the duration classifier, and nothing else.

**Licensing:** TAWOS is research-use-only and GPT2SP repackages public Jira data. Fine
for prototyping and internal demonstration. The CoWorkLocal data is real internal project
data - it is in the database and the backups, and `pulse.db.bak*` is gitignored for that
reason. Treat a backup as a data export, because that is what it is.

---

## 14. The scenario the demo tells

**The original, from the simulator.** One project, one insight, end to end:

> Environment Setup milestone slipped 12 days (**Jira changelog - exact timestamp**) ->
> QA blocked-test count rose 4 -> 17 (**Excel worklog snapshot - bounded interval**) ->
> UAT projected 2026-05-14 -> 2026-05-26 (**NetworkX critical path**).

Chosen deliberately: it forces one exact-precision and one bounded-precision event into
the same chain, so §6 - the most defensible engineering in the system - is *visible on
screen* rather than buried in a table. The click path is: insight card -> causal chain ->
click step 2 -> evidence panel showing the actual spreadsheet row, its file and its scan
time -> rule trace showing which rules fired on which records.

**The one real data now supports, which is stronger.** It needs no perturbation script,
because nobody authored it:

> Nine CoWorkLocal tickets are marked **Cancelled** in Jira. The code that implements
> them is in the repository, cited file and line, and the citations were checked against
> the source rather than taken from the model.

It is stronger for three reasons. The data is real and the audience recognises it. The
finding is one the tracker cannot produce by itself and neither can the repository -
only the join. And it was reached twice independently, by `reconcile` structurally and by
`explain` narratively, which is the difference between a finding and a guess.

The honest caveat belongs on the slide with it: **173 of 190 tickets share one due date**
(§13), so this backlog cannot support a schedule claim. Say that before somebody asks,
and the traceability claim survives the question.

---

## 15. Deployment

**What shipped: Fly.io + Neon Postgres, single origin.** `projectpulse.fly.dev`, region
`sin`, `shared-cpu-1x` with 512 MB and **no persistent volume**. The Cloudflare Pages +
Tunnel topology in the original plan was not built and is not needed: the React bundle is
committed and served by the same process as the API, so there is no second origin, no
CORS, and no `202 + poll` requirement.

`docker compose up -d` remains exactly right and still works - Postgres+pgvector and the
app built from the same Dockerfile the deploy target uses. It is the artifact somebody
reads and runs in thirty minutes.

### The constraints that shape everything else

| Constraint | What it rules out |
|---|---|
| 512 MB, no volume | Cloning a repository on the server. This is why §8a's pipeline runs elsewhere and ships its output as JSON in the image |
| Outbound requests come from a data centre | Collecting from Jira at all (§7a) |
| The machine's disk is ephemeral | Any state that is not in Neon or in the image |
| Fly stores secret digests, not secrets | Reading `PULSE_ADMIN_TOKEN` back. Losing it means rotating it |

### Schema changes without a migration tool

There is no Alembic. `create_all()` runs on boot and `_ADDITIVE_COLUMNS` in `app/db.py`
adds nullable columns that the ORM knows about and the deployed table does not - so a
deploy that adds `tasks.actual_end` self-heals rather than needing a migration step
somebody has to remember at 2am.

This is deliberate and it is bounded: **additive and nullable only.** A rename or a type
change is not expressible this way and would need a real migration. What was actually
needed twice was an idempotent re-keying script, not a migration chain - see §12's
surgery scripts.

### The health check is a route the product really serves

`GET /api/portfolio`. It pointed at `/api/state` until that endpoint was deleted with the
retriever console, and Fly took both machines out of the pool while the app was serving
every real page correctly - because a 404 from a health check reads as *unhealthy*, not
as *that path is gone*. `/api/portfolio` is the landing page's own bundle, so a machine
that passes the check can answer the first request a visitor makes.

### Getting data onto production

Code and data deploy by different routes, and conflating them is how a demo goes wrong.

```bash
# 1. Code.
fly deploy

# 2. A connection with no credential - a link, not a login (§7a).
curl -X POST .../api/jira/connections -H "X-Pulse-Admin-Token: $TOK" ...

# 3. The data somebody else fetched.
python -m scripts.jira_push --to https://projectpulse.fly.dev \
       --project COWORKLOCAL --token "$TOK"

# 4. Check the count, because pairing sums rather than merges.
curl -s ".../api/portfolio" | ...     # 190, not 380
```

Step 4 is not optional. Invariant 7 pairs several source ids onto one delivery project
**on the assumption that the sources are complementary**. A spreadsheet and a Jira
collection describing the same 190 work items are not complementary, they are duplicates,
and pairing them reports 380 tasks. The fix is `carry_milestones` (move the grouping onto
the surviving source) then `drop_source --forget-upload` (empty the superseded one,
keeping the project row and its id, which everything else references).

`flyctl ssh console -C "..."` is how those run on a deployed instance. On Windows it
always exits with `Error: The handle is invalid.` after printing correct output - a local
pty artifact, not a remote failure. The stdout above it is the truth.

### Traceability findings are baked into the image, not loaded

`traceability_runs/` is copied into the image by the Dockerfile and `TRACELINK_RUNS`
points at `/app/traceability_runs`. So **production serves whichever run was committed at
build time**, not whatever is on a laptop. Putting a new run live means copying it into
`projectpulse/traceability_runs/` and deploying - a code deploy, not a data push.

---

## 16. Build sequence (solo, to 2026-09-11)

> **Historical.** This is the round-1 plan as written on 2026-09-06, kept as the record
> of what was committed to and in what order. It is not a description of the current
> system; §§0-15 are.

**Day 1 — contracts and skeleton.** DDL + Alembic migration. `models/base.py` mixin.
`ids.py`. **Freeze `InsightBundle`** + the fixture. `docker-compose.yml` up and green. ZEN
spike (2h, timeboxed). **Decide the dependency-edge source** (§5.4) — the schedule engine
cannot be specified until this is settled.

**Day 2 — data in.** `gen_demo_portfolio.py` emitting raw artifacts. `jira_replay` collector +
extractor + convertor. Excel reader + `snapshot_diff.py` + `identity.py` → `state_changes`
with correct precision. `runner.py` with the lock and watermark. *Gate: `state_changes` holds
both exact and bounded rows with valid intervals.*

**Day 3 — deterministic intelligence.** `context.py` → flat scalars. `rules/engine.py` +
tables + `trace.py`. `schedule/` graph, critical path, impact. *Gate: a rule fires with a
persisted trace, and `delay_days` is computed from the graph.*

**Day 4 — causality and language.** `temporal/ordering.py` with the property test *first*.
`templates.py` + `chains.py`. `assembler.py` → a real bundle. `narration/fallback.py`, then
`validator.py`, then `client.py` — **in that order**. *Gate: a validated insight renders from
real data with the LLM switched off.*

**Day 5 — surface and submit.** Vite + React scaffold, `tokens.css`, `AppShell`,
`InsightPage` and its components against the live API. Evidence panel click-through. README +
one-command runbook. Rehearse the walkthrough twice.

**Cut order if a day slips** (decide now, not at 2am on day 5): retrieval/precedent →
the LLM (ship fallback prose) → the rule-table editor (show tables read-only) → Excel
identity fuzzy-matching (require `Task ID`). **Never cut** the evidence panel, the precision
model, or the trace — they are the product.

**Round 2 (≈2 weeks):** port the five remaining mockups to React; TAWOS as a second
connection; APScheduler; Graph API if consent clears; the GoRules JDM editor for live
threshold tuning; the feedback loop writing to `historical_cases`.

**October final:** judged on PM capability and presentation as much as the system. The
engineering work is largely done by then; the work is the narrative — why deterministic-first
is the right architecture for PM trust, and what the rejected-rule feedback loop means for
an organisation adopting it.

---

## 17. Risk register

Supersedes design §19. The original round-1 register is kept below, marked, because
which risks *did not* materialise is information.

### Live risks

| Risk | Likelihood | Status / mitigation |
|---|---|---|
| **A demo number is measured against a field nobody set** | **Certain, present** | 173 of 190 tickets share due date `2026-08-31`. The arithmetic is correct; the input is a bulk edit. Say it before showing an early/late figure (§13) |
| **Two identities for one person** | **Certain, present** | The role field and `assignee` disagree on 151 tickets. Unresolved - the pages show both in different places |
| **Pairing sums duplicate sources instead of merging them** | Certain when it happens | Invariant 7 assumes sources are complementary. Check the task count after every data push; `drop_source.py` is the remedy (§15) |
| **Cloning a repository on a 512 MB dyno with no volume** | High, if attempted | Not attempted. `tracelink` runs off-server and ships JSON in the image (§8a). **A GitHub sync inside the web process would break it**, and that work is not started |
| **A traceability run on the laptop is mistaken for the one in production** | Medium | The image carries the committed run. Compare a live figure, not a local one, before believing the page is current (§15) |
| **A rotated admin token silently breaks downloaded sync tools** | Medium | `/api/jira/sync-tool` embeds the token in the file. After a rotation every previously downloaded script must be re-downloaded (§7a) |
| **A database backup treated as a file rather than as a data export** | Medium | `pulse.db.bak*` holds real ticket text and the Fernet-encrypted Jira token. Gitignored; never commit one |
| **Deriving a fact from a changelog that does not contain it** | Medium, recurring | The "start = first move out of To Do" bug shipped once and manufactured 151 dates. Any new derivation needs the same question: *is the event I am reading actually the event I mean?* |
| **PMs distrust an opaque health score** | Medium | Trace on every score, rules visible, evidence click-through on every claim |
| **Second system of record emerges** | Low | Read-mostly. Write back only PM-confirmed actions; never overwrite the source tool |

### Round-1 register, resolved

| Risk | What happened |
|---|---|
| Causal engine scope-creeps into general discovery | Did not. Six named templates, as planned |
| Excel row identity churn manufactures false state changes | Held. `Task ID` required; fuzzy matches marked `identity_confidence='low'` and excluded from chains |
| No dependency edges exist in any source | Materialised as expected; `Predecessor` in our own template plus WBS-implicit edges |
| ZEN wheel/aggregation shape mismatch | Mitigated better than planned - `rules/engine.py` carries a complete built-in evaluator and reports which backend ran; both are checked against each other |
| Narration contradicts computed values | Held. Tokens never digits, 8-stage validator, server-side substitution |
| Classifier miscalibration read as a real probability | Held, and the classifier is optional - it does not install on Python 3.14 and the product says so rather than crashing |
| Solo build overruns the 5 days | It did, and the cut order held: retrieval/precedent was cut and `confidence.py` says so in its own docstring (§0) |
| Venue network fails during the demo | Held. `fallback.py` means the product works with the LLM off |

---

## 18. Why there is no temporal graph database here

Design v2 named **Graphiti** for the bi-temporal event graph. This architecture drops it, and
the reasoning belongs on the record because it will be asked.

Graphiti is an **LLM-driven** graph builder: it uses a model to extract entities and edges
from text and to assign the temporal validity intervals on those edges. Those intervals *are*
dates produced by a language model — which is precisely what design §4.4 forbids
(*"Never let the model produce a date"*) and what §6's governing rule exists to prevent. It
also requires operating Neo4j or FalkorDB alongside Postgres.

`state_changes` plus NetworkX already **is** a bi-temporal event graph: it is deterministic,
it is replayable, every edge traces to a source record, and it can be defended line by line.
Design §19 listed "graph ingestion LLM cost scales with portfolio size" as a risk to be
mitigated by restricting Graphiti to flagged projects; that cost is really a symptom, and the
correct mitigation is not to adopt it.

This is a strength to state out loud in the pitch, not an omission to explain away.
