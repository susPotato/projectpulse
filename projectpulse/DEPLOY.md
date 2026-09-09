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
| Narration by a language model | Live once a key is set. Off by default. |
| Narration via the **FPT gateway** (`PULSE_NARRATION_PROVIDER=fpt`) | **Should be live** - see the note below before relying on it. |
| The **Sync** button and `sync run` | **Not live.** See below. |

### Will the FPT gateway answer from a deployed host?

Almost certainly yes, and here is the evidence rather than an assumption.
`token-api.fpt.ai` resolves to **Cloudflare anycast addresses**
(`104.26.12.64`, `104.26.13.64`, `172.67.74.9`), and an unauthenticated request
from here comes back **`401 Unauthorized`, not `403`**. A 401 means the request
reached the origin's auth layer and was turned away for having no credential -
if the gateway were allowlisted to the FPT corporate network, the edge would
have refused it before that. So it is a public endpoint gated by API key, not
by source address, and `FPT_API_KEY` is all a Fly machine should need.

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

### 4. Optional: narration

The `/settings` page is **read-only when deployed** - writes are restricted to
loopback, and a request through Fly's proxy is not. That is deliberate: a
settings form that accepts an API key should not be reachable from the internet.
Configure it with secrets instead.


```bash
fly secrets set ANTHROPIC_API_KEY=...
fly secrets set PULSE_NARRATION=1
```

Add the extra to the image first, or the SDK is not there to import - in
`Dockerfile`, change `pip install -e .` to `pip install -e ".[llm]"` (or
`.[llm-openai]` / `.[llm-gemini]`). Without it the page still fills, with
`narration_fallback_reason` saying the package is missing.

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
