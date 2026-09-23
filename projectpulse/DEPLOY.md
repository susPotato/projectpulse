# Deploying ProjectPulseAI

The app is a normal FastAPI service. Everything expensive to retrofit is already
in place: the scheduler takes a Postgres advisory lock per source, the engine
sets `pool_pre_ping`, psycopg has a 5s connect timeout, and the transport seam
means moving off a synced folder is a new class rather than an edit.

**Status: not deployed.** These files were written and verified as far as they
can be on a Windows machine with no Docker daemon running - `scripts.serve` is
exercised locally (see below), the Dockerfile and `fly.toml` are not. Expect to
fix something on the first `fly deploy`.

---

## What can and cannot be live

| | |
|---|---|
| Insight, Schedule, Calculation, the Gantt, evidence, rule traces, chains | **Live.** All of it, on real generated history. |
| The `.docx` report and the `.xlsx` templates | **Live.** |
| Narration by a language model | **Live.** On in `fly.toml`, against Anthropic, with `ANTHROPIC_API_KEY` as a Fly secret. |
| A model *proposing risks* from issue text (`PULSE_RISK_DRAFTS`) | **Live.** A separate permission from narration - see `app/config.py`. |
| Per-feature model choice, the key store and the usage board (`/llm`, `/usage`) | **Live.** Read-only from a browser until `PULSE_ADMIN_TOKEN` is set - see §4. |
| Narration via the **FPT gateway** (`PULSE_NARRATION_PROVIDER=fpt`) | **Not deployed, but supported.** No `FPT_API_KEY` is set on the app - see the note below before switching to it. |
| The **Sync** button and `sync run` | **Not live.** See below. |

### Will the FPT gateway answer from a deployed host?

Almost certainly yes, and here is the evidence rather than an assumption.
`token-api.fpt.ai` resolves to **Cloudflare anycast addresses**
(`104.26.12.64`, `104.26.13.64`, `172.67.74.9`), and an unauthenticated request
from here comes back **`401 Unauthorized`, not `403`**. A 401 means the request
reached the origin's auth layer and was turned away for having no credential -
if the gateway were allowlisted to the FPT corporate network, the edge would
have refused it before that. So it is a public endpoint gated by API key, not
by source address, and `FPT_API_KEY` is all a Fly machine should need - though
note that no such secret is set today, and the deployed app runs on Anthropic.

⚠️ **The residual risk is Cloudflare's bot rules, not an IP allowlist.** We
already know this edge rejects `Python-urllib/3.x` with `403 "error code:
1010"`, which is why the adapter sends a real `User-Agent`. Those rules can be
stricter for a datacenter ASN than for a corporate one, so a Fly machine could
still be challenged where this laptop was not. **Test it in one command after
the first deploy** - it is cheap, and the failure is a fallback rather than an
outage:

```bash
fly ssh console -C "python -m scripts.probe_fpt --model DeepSeek-V4-Flash"
```

If it is challenged, narration falls back to the deterministic template with a
reason - the demo does not break - and the fix is an allowlist request for the
egress IP rather than a code change.

The Sync path reads `settings.data_root`, which on a laptop is a OneDrive-synced
folder. A server has none, and Microsoft Graph needs FPT tenant admin consent -
a lead-time item, which is exactly why the synced folder was chosen for the demo.

This costs the deployment less than it sounds. The demo timeline is *generated*
by `scripts.replay` from code and then ingested through the real reader, differ
and identity resolver, so the deployed app has a genuine observed history rather
than seeded rows. What is missing is only *re-*ingestion of new edits.

---

## Why not Cloudflare Pages

`arch.mintteas.org` is already a Pages project, and it stays static. Pages
serves assets; FastAPI needs Python, and Workers cannot run `psycopg`. The
`/insight` and `/explain` pages there are frozen snapshots written by
`scripts.publish`, and they should stay that way - they are the fallback if a
live deploy is down during judging.

Put the app on a subdomain instead: `app.mintteas.org`. The apex is deliberately
free.

---

## One-time setup

### 1. A database

Neon or Supabase. Both give a free Postgres with pgvector available, which
matters when retrieval lands.

Take the connection string and **change the scheme**:

```
postgres://user:pass@host/db              # what the dashboard gives you
postgresql+psycopg://user:pass@host/db    # what SQLAlchemy needs here
```

Getting this wrong fails at import with a driver error, not a connection error:
SQLAlchemy resolves bare `postgres://` to psycopg2, which is not installed.

### 2. The app

```bash
cd projectpulse
fly launch --no-deploy --copy-config          # claims the app name, keeps fly.toml
fly secrets set DATABASE_URL="postgresql+psycopg://..."
fly deploy
```

First boot creates the schema, notices the database is empty, and replays the
demo timeline. Watch it:

```bash
fly logs
# [serve] database: postgresql+psycopg://...
# [serve] schema ready
# [serve] database is empty; replaying the demo timeline
# [serve] seeded
# [serve] listening on 0.0.0.0:8080
```

Seeding runs **only on an empty database**. A restart does not append a second
copy of the timeline.

### 3. The domain

```bash
fly certs add app.mintteas.org
```

Then add the CNAME in Cloudflare. Note from CLAUDE.md: the Cloudflare OAuth
login has `pages (write)` but **not** DNS write, so either add the record by
hand in the dashboard or make an API token with `Pages: Edit` + `Zone -> DNS:
Edit`.

Set the record to **DNS only** (grey cloud), not proxied - Fly terminates TLS
itself, and proxying on top produces a certificate loop that presents as a
redirect loop in the browser.

### 4. Narration, models and keys

`PULSE_NARRATION` and `PULSE_RISK_DRAFTS` live in `fly.toml`, because they are
booleans rather than credentials. **Do not also set them as secrets** - a secret
of the same name silently wins over `[env]`, which is how this app once ran
narration in production while the only configuration anybody could read said it
was off.

The credential is a secret:

```bash
fly secrets set ANTHROPIC_API_KEY=...
```

That is all a deployment needs. Each feature - narration, risk drafts, the
dashboard tile builder, the Agent tab - can then be pointed at its own vendor
and model on `/llm`, and what they spend shows up on `/usage`.

**The settings pages are read-only from a browser by default.** Writes are
restricted to loopback, and a request through Fly's proxy is not - deliberately,
because a form that accepts an API key should not be reachable from the
internet. To administer a deployed instance anyway, set two more secrets:

```bash
python -m scripts.secret          # prints both, and the line below
fly secrets set PULSE_SECRET_KEY="..." PULSE_ADMIN_TOKEN="..."
```

- `PULSE_SECRET_KEY` encrypts the API keys stored in the database, so a dump or
  a backup discloses ciphertext rather than a usable credential. Without it the
  key store refuses to save anything rather than falling back to plaintext.
- `PULSE_ADMIN_TOKEN` is what authorises a write from a browser. Leave it unset
  and the old loopback-only behaviour is exactly what you get.

Losing `PULSE_SECRET_KEY` does not lose the app - only the stored keys, which
report themselves as unreadable on `/llm` and have to be pasted in again.
Rotating it deliberately is `keys.rotate()`, via `PULSE_SECRET_KEY_PREVIOUS`.

### What `PULSE_ADMIN_TOKEN` actually gates

Most of this app is writable by anyone who can reach it, and that is a
deliberate choice - it is a demo, and a visitor rearranging their own dashboard
costs nothing. Two kinds of route are not:

| Route | Why |
|---|---|
| `POST /api/agent/chat` | calls a model on this deployment's key |
| `POST /api/custom-tiles/chat` | a tool loop - several billed calls per turn |
| `POST /api/custom-tiles/draft` | calls a model |
| `POST /api/dashboards/generate` | calls a model |
| `POST /api/risks/drafts` | calls a model |
| `POST /api/llm/features/{feature}/test`, `POST /api/settings/test` | call a model |
| `DELETE /api/projects/{id}` | no undo, and rebuilds nothing |
| everything under `/api/llm/*` and `/api/imports` that writes | settings, keys and stored workbooks |

**Loopback always passes**, so `python -m scripts.serve` on a laptop behaves as
it always has and the test suite needs no token. On a deployed host these
answer 403 until somebody pastes the admin token on `/llm`, which unlocks that
browser tab - the React app sends it on every write from there on.

Without `PULSE_ADMIN_TOKEN` set at all, the deployed app simply cannot run
these: the AI features are visible but decline, saying so. That is the safe
default, and it is what you get by doing nothing.

`tests/test_route_auth.py` reads the routing table and fails on any mutating
route that reaches a model without the gate, so the next one added is caught
by the suite rather than by the bill.

Add the extra to the image first, or the SDK is not there to import - in
`Dockerfile`, change `pip install -e .` to `pip install -e ".[llm]"` (or
`.[llm-openai]` / `.[llm-gemini]`). Without it the page still fills, with
`narration_fallback_reason` saying the package is missing.

### 5. Code repositories, and the documents they carry

Settings > Sources has a **Code repository** card: register a git repository
against a delivery project and the server clones it and reads its
documentation tree. Two things have to be true on the host for that card to do
anything, and it reports which one is missing rather than failing silently.

**`git` is in the image.** The Dockerfile installs it. `tracelink.source`
shells out to git; there is no pure-Python path.

**The pipeline is in the image, as a committed copy.** `projectpulse/tracelink/`
is a copy of the traceability pipeline, which lives in a different repository
that this one cannot reach at build time. Keep it current when that pipeline
changes:

```bash
python -m scripts.vendor_tracelink            # report what differs
python -m scripts.vendor_tracelink --apply    # update it, then commit
```

`tests/test_vendored_tracelink.py` fails if the copy drifts, so a stale one is
a red suite rather than a deployed image quietly running last month's
pipeline. On a machine without that checkout the test skips and the committed
copy is used as-is.

**The code analyser is in the image.** `tracelink/corpus.py` reads a repository
with CodeWiki's dependency analyser and falls back to a Python-only AST walk
when it cannot import it. The fallback is correct on a laptop and wrong here,
and it is *silent* - it logs at INFO and the only evidence is
`analyzer: "python-ast"` inside `corpus.json`, on a server that otherwise looks
healthy. What it costs is every dependency edge and every non-Python symbol: on
the CoWorkLocal tree, 2084 symbols with 506 edges becomes names-only with none.

So the Dockerfile installs it, pinned to a commit and with `--no-deps`.
CodeWiki's declared dependencies are written for the whole product - litellm,
openai, pydantic-ai, fastapi, uvicorn, mermaid, networkx - and pull ~414 MB for
an analyser that needs ~33 MB of it, onto a 512 MB machine. The runtime
dependencies are therefore listed by hand, and that list includes `tiktoken`,
which is **not** in CodeWiki's own `pyproject.toml`: `codewiki/src/be/utils.py`
imports it and upstream only gets away with that because litellm happens to
pull it in.

`tests/test_codewiki_analyzer.py` guards both halves - it builds a real corpus
and asserts the analyser that ran was CodeWiki (skipped where CodeWiki is
absent, which is the honest state on a laptop), and it reads the Dockerfile to
check the install is still there, still pinned to a sha, and still names
`tiktoken`. That second test runs everywhere, including where the first skips.

**A private repository needs a token**, and it is a credential, so it is a
secret rather than `[env]`:

```bash
fly secrets set PULSE_SECRET_KEY="..."   # if not already set - it seals the token
```

The token itself is typed into the card and sealed into `project_repos` with
`PULSE_SECRET_KEY`, the same way a Jira token is. There is no
`TRACELINK_GIT_TOKEN` to set on the host: the credential is per repository and
comes from the row, lent to the pipeline for the length of one fetch. Leave a
repository's token blank and it is treated as public - and an unrelated
ambient token will not be sent to it.

**What is stored, and what is not.** The registration - URL, ref, documents
path, the commit that was read, the document count - is a row in Postgres, so
it survives a deploy. The clone is not: it goes to `/app/.pulse/repos`, which
is container-local and disposable, and a machine that loses it pays one
shallow clone on the next refresh. That is the right split, because a clone
can always be re-derived and an uploaded workbook cannot.

## Running the pipeline on the server

Registering a repository used to write a row nothing read: the Traceability
page could only show runs baked into the image at build time. It can now
produce them.

```
POST /api/traceability/export   # the backlog export, byte for byte (multipart)
POST /api/traceability/run      # clone, then run every free stage
GET  /api/traceability/run      # progress, and the tail of the output
```

The export is a **separate upload from `POST /api/sources/upload`**, which
takes the same file for a different purpose: it converts a Jira export into the
schedule and worklog contracts and discards the original. `governance` exists
because the keyed PM rows carry prose those contracts do not keep, so the
pipeline needs the file as exported. It lives in `ticket_exports`, one row per
project, in Postgres for the reason every other upload is - a Fly machine's
filesystem does not survive a deploy.

**Only the free stages run.** `translate`, `adjudicate` and `explain` cost
money per ticket - the demo run was $9.26 for 173 verdicts on `claude-opus-5` -
and are deliberately not wired to a button. `tracelink` names them, with their
commands, at the end of every run.

Two things to set before relying on this:

**`TRACELINK_RUNS` must point at a volume**, or runs are not kept. The default
is `/app/traceability_runs`, which is inside the image: a run completes, the
page reads it, and the next idle-stop takes it away along with the adjudication
cache. `fly volumes create pulse_runs --size 1`, mount it, and point
`TRACELINK_RUNS` into it.

**`min_machines_running` must be 1**, or a run is killed part-way. With
`auto_stop_machines = 'stop'` and a minimum of zero, Fly stops the machine when
the last request finishes - and the run is a background thread, not a request,
so nothing holds the machine open. A half-finished run is not corrupt; the page
names each missing artifact. It is just wasted.

A run is one at a time per process, by refusal rather than by queue: a second
corpus build on one shared CPU makes both slow instead of either quick. The
run record is in memory, so a restart forgets a run in flight while its
subprocess carries on - the artifacts still land, and the page still reads
them.

⚠️ **The documentation tree is enforced, not assumed.** A repository with no
`docs/` (or whatever the registration names) is refused with a 400 listing the
directories it does have, and leaves no registration behind. This is
deliberate: the documentation stages are most of what a repository is
registered *for*, and a project registered without documents produces empty
pages that read as a broken product rather than as a missing input.

⚠️ **A large repository can exhaust a 512 MB machine.** The clone is shallow,
but `[[vm]] memory` in `fly.toml` is 512 MB on a shared CPU, and nothing here
caps repository size. Register a big one and expect the machine to be killed
mid-clone. Raise the memory before pointing this at a monorepo.

---

## What the deployed app keeps, and where

A Fly machine's filesystem **does not survive a deploy**, and a deployment can
run more than one machine. So nothing the app has to remember is written to
disk - it is all in Postgres:

| | where | survives a deploy |
|---|---|---|
| Ingested rows, findings, state changes | Postgres | yes |
| Risk register, dashboards, tile layouts, custom tiles | Postgres | yes |
| Narration cache | Postgres | yes |
| OneDrive sign-in | Postgres | yes |
| **Imported workbooks** (bytes + which tab + which project) | Postgres (`uploaded_sheets`) | yes |
| **Projects somebody registered** by importing a document | Postgres (`registered_projects`) | yes |
| **Narration settings** set in the browser | Postgres (`app_settings`) | yes |
| The demo's *generated* sheets (`data/demo/*.xlsx`) | container disk | **no**, and deliberately - they are reproducible from `scripts.gen_demo_data`, and their ingested rows are already in Postgres |

The last three used to be files under `PULSE_STATE_DIR` and `data_root`. That
worked on a laptop and failed quietly on a host: a document imported through
the browser **disappeared from every project picker on the next release**,
while its ingested rows stayed in Postgres with nothing pointing at them.

That means **the website is self-sufficient**: a project can be created,
imported, re-imported after an edit, risk-assessed and dashboarded entirely
from the browser, with no local copy of the app and no synced folder. The
transport that makes it work is `StoredSheetSource` - it reads a workbook out
of the database into a temp file and deletes it afterwards, which is exactly
the seam `excel/transport.py` was written for.

⚠️ **A deploy still adds tables.** `scripts.serve` calls `create_all()` on
boot, so `uploaded_sheets`, `registered_projects` and `app_settings` appear by
themselves. If you would rather not rely on that, `fly ssh console -C "python
-m scripts.sync init"` is the explicit form.

⚠️ **`PULSE_DATA_ROOT` still matters for the demo sheets.** After a deploy the
container has none, so a sync reports them "not present, skipped" - correct,
and not an error. Their data is already ingested. Only pressing a `/console`
guided-tour button regenerates them, which is the one thing that also
re-ingests (see below).

---

## Upgrading a database that already holds data

There is no Alembic here (see "What is deliberately not here"), so a change to
how rows are *keyed* comes with a script. There is one, and a database seeded
before 2026-09-11 needs it:

```bash
python -m scripts.migrate_ids            # report what would change
python -m scripts.migrate_ids --apply    # change it
```

**What it is for.** Excel-derived ids (`tasks`, `qa_items`, `state_changes`,
`dependencies`) gained a project component, because every Excel project shares
one `connection_id` and a row key is unique only within its own sheet - so two
projects that both numbered their tasks `1, 2, 3` collided into one row, and
importing the second document silently took the first's tasks.

**When you need it.** Measured, not reasoned about:

| what someone does | does it duplicate? |
|---|---|
| Reads the site - dashboards, Insight, Risk, adding a risk, building a tile | **No.** Nothing syncs on a page load, and old-style ids read correctly. |
| Redeploys the app | **No.** `serve.py` skips seeding a non-empty database. |
| Presses **Sync Excel** on `/console` | **No** - on a deployed host. The container's `data_root` is empty after a redeploy, so every watched sheet answers "not present, skipped". |
| Uploads a document | **No.** The new sheet ingests; the absent demo sheets are skipped. |
| Presses a **guided tour** button on `/console`, then syncs | **Yes.** `/api/write-step` runs `gen_demo_data`, which writes the sheets fresh - and `.xlsx` output is not byte-identical between runs, so the sha256 always differs and the whole sheet re-ingests. |

On the demo data that last row took HRMS from **10 tasks to 16** and from
**9 findings to 6** - every task drawn twice on the Gantt. It is not cosmetic:
the schedule graph is computed over the doubled set, so the conclusions move.

So the migration is not urgent for ordinary use, and it is still worth doing
now, because nothing stops a visitor pressing a guided-tour button. If a sync
already duplicated things, run it anyway - it deletes the duplicate rather
than renaming onto it.

⚠️ **Separately, and already true before any of this: `/console` is
unauthenticated on a public deploy, and "Reset database" drops the schema.**
Anyone who opens the live site can wipe it, risk register included. That is
worth closing before a judged demo window - the buttons are useful locally and
have no business being reachable in production.

⚠️ **Do not reach for `scripts.replay` on a deployed database.** It rebuilds
from the sheets, which is correct for a local demo and destroys the only data
in there that no source system can regenerate: the risk register, the
dashboards people arranged, the custom tiles, the narration cache and the
OneDrive sign-in. The migration keeps all of it.

On Fly:

```bash
fly ssh console -C "python -m scripts.migrate_ids"          # look first
fly ssh console -C "python -m scripts.migrate_ids --apply"
```

⚠️ `fly ssh console` exits 1 with "Error: The handle is invalid" after every
command on the Windows/Git-Bash setup this was written on. That is a local pty
artifact, not a remote failure - **the command's own stdout above it is the
truth.** Check the task count per project afterwards; that is the number this
was about.

---

## Verifying a deploy

Check **`content_type`, never the status code.** This is written down because it
has already cost a day: Cloudflare Pages answers an unmatched path with
`index.html` and a 200, so every status check passed while the browser was
refusing HTML as a stylesheet.

```bash
BASE=https://app.mintteas.org
for path in / /insight /explain /gantt /api/insight /api/explain \
            /static/shell.css /static/gantt.js \
            /api/template/schedule.xlsx /api/report.docx; do
  curl -s -o /dev/null -w "%{http_code} %{content_type}  $path\n" "$BASE$path"
done
```

Expect `text/html` for the pages, `application/json` for the two APIs,
`text/css` and `text/javascript` for the assets, and the two Office types for
the downloads. Anything answering `text/html` that should not be is a routing
problem, not a missing file.

---

## Running the container logic without Docker

`scripts.serve` is the entry point the image runs, so it can be checked directly:

```bash
python -m scripts.serve --check                 # schema + seed decision, no server
python -m scripts.serve --check --no-seed        # schema only
python -m scripts.serve                         # actually serve, on $PORT or 8080
```

`--check` does everything the container does except bind a port. Against a
populated database it prints `database already holds data; not seeding`; against
an empty one it replays the timeline and then exits.

---

## What is deliberately not here

* **Alembic.** Skipped for round 1 and purely additive later. The trigger to add
  it is the first deploy holding data you cannot regenerate - which, while the
  data is a generated demo timeline, has not happened.
* **Multi-tenancy.** Not built, and the one genuinely expensive retrofit.
  `connection_id` namespaces sources and `Program`/`Project` give the hierarchy;
  cross-BU would be a funded project.
* **pgvector.** In the image on Neon/Supabase, never enabled. No
  `CREATE EXTENSION vector` until retrieval lands.
