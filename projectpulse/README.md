# ProjectPulseAI

Delivery intelligence for project managers: a deterministic analysis system with a
language interface. Rules, graph traversal and arithmetic produce every finding;
the language model only writes the sentences.

Design: [`../ProjectPulseAI_Product_Design_v2.md`](../ProjectPulseAI_Product_Design_v2.md) ·
Architecture: [`../ProjectPulseAI_Architecture.md`](../ProjectPulseAI_Architecture.md)

**Status:** complete end to end - spreadsheets in, an `InsightBundle` out. Ingestion
for both sources, the ordering guard that makes causal claims possible, the schedule
engine, the rule table, the narration layer and four screens. Still to come: retrieval,
a scheduler, and the five remaining screens.

---

## Run it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows
# .venv/bin/python -m pip install -e ".[dev]"           # macOS / Linux

docker compose up -d                                    # postgres + pgvector on :5433
python -m scripts.sync init                             # create the schema
```

### The console

```bash
python -m scripts.demo        # http://127.0.0.1:8000
```

A small local page for watching the retriever work: which sheets are being watched
and whether they have been edited since the last scan, what state changes came out,
how precisely each is dated, how many pairs can actually be ordered, and which rows
were refused. It polls every 15 seconds, so saving a workbook in Excel makes it say
"edited - sync to ingest" without a reload.

"Run whole tour" replays the four scans below in one click. The more convincing use
is to edit `data/demo/hrms_schedule.xlsx` yourself and press Sync.

### Or from the command line

Replay the demo timeline. Scan times are simulated with `--now`, because what
bounds every spreadsheet change is *when we looked*:

```bash
python -m scripts.gen_jira_data                    # dummy Jira payloads

python -m scripts.gen_demo_data --step 0
python -m scripts.sync run --source excel      --now 2026-03-02T09:00   # baseline: 0 changes

python -m scripts.sync run --source jira_replay --now 2026-03-04T12:00  # 6 exact changes

python -m scripts.gen_demo_data --step 1
python -m scripts.sync run --source excel      --now 2026-03-06T09:00   # schedule slips

python -m scripts.gen_demo_data --step 2
python -m scripts.sync run --source excel      --now 2026-03-18T09:00   # QA backlog grows

python -m scripts.sync order                       # what can actually be ordered
python -m scripts.sync changes                     # what moved
python -m scripts.sync rejects                     # what we refused, and why
```

To run without Docker, point at SQLite — enough for the differ, not for pgvector:

```bash
DATABASE_URL=sqlite:///pulse.db python -m scripts.sync init
```

```bash
python -m pytest        # 458 tests, no database required
```

The narrative is written by a deterministic template. To have a language model
phrase it instead - which changes no finding and no figure, because the draft is
validated before any number is substituted into it:

```bash
python -m pip install -e ".[llm]"          # Claude   (or .[llm-openai] / .[llm-gemini])
python -m scripts.sync insight --narrative --model
python -m scripts.sync insight --narrative --model --provider openai
```

Claude, GPT and Gemini sit behind one seam - `(system, user) -> str` - so the
validator, the token substitution and the template fallback are the same
whichever answers. With no package and no credentials the template is served and
the reason printed, so this is safe to leave off and safe to turn on.

Running a self-hosted open model? Set the provider to `openai`, put your
server's address in **Endpoint** (`http://localhost:11434/v1` for Ollama,
whatever vLLM or LM Studio prints) and any dummy key. Nothing else changes:
the same validator, the same substitution, the same fallback.

Or do it in the app: **http://127.0.0.1:8000/settings** — pick the provider,
paste a key, press **Test it**. It runs a real narration through the real
validator and tells you which wrote the summary and, if it fell back, why.

Keys are stored **encrypted** in the database, sealed with `PULSE_SECRET_KEY`,
so a dump or a backup discloses ciphertext rather than a usable credential. An
environment variable (`ANTHROPIC_API_KEY` and friends) is read by the vendor's
own SDK and remains the better path for a deployment. Settings can only be
changed from the machine the app runs on, unless `PULSE_ADMIN_TOKEN` is set.

Also settable without the page: `PULSE_NARRATION=1`, `PULSE_NARRATION_PROVIDER`,
`PULSE_NARRATION_MODEL`, `PULSE_RISK_DRAFTS=1`.

### One model per feature, and what they cost

Four things here call a model, and they need not share one: narration rephrases
findings, risk drafts reads a backlog, the dashboard tile builder runs a tool
loop, and the Agent tab holds a conversation. **http://127.0.0.1:8000/llm**
sets each one's vendor and model independently, and holds the keys and the
per-model rates.

**http://127.0.0.1:8000/usage** is what they have spent - tokens and cost by
feature, by model and by day, including failed calls, because a refusal still
burns input tokens. Models with no published rate are counted and flagged as
unpriced rather than shown as free.

```bash
python -m scripts.secret    # a PULSE_SECRET_KEY and a PULSE_ADMIN_TOKEN
```

### Files it hands back

```bash
python -m scripts.sync template --out templates    # blank .xlsx for a PM to fill in
python -m scripts.sync report --out status.docx    # .docx status report
```

Also at `GET /api/template/{schedule|worklog}.xlsx` and `GET /api/report.docx`.

The template is generated from the sheet contract, so the file handed out and the
file the ingester understands are the same file - a test writes one and reads it
back with the real reader. The report renders the same bundles the screens
render and formats no number of its own.

### Advisory duration bands (optional)

```bash
python -m pip install -e ".[ml,ml-fetch]"
python -m scripts.fetch_model                      # ~a few MB, from Hugging Face
python -m scripts.sync advise
```

Wraps `omaradly/jira-task-duration-classifier`. It returns `Short` / `Standard` /
`Long-running` and **never a number of days**, and nothing under
`app/intelligence/` is permitted to import it - a test walks the imports to make
sure. Without the artefact the feature is simply absent.

**Needs Python 3.12 or 3.13.** The published artefact was pickled by
scikit-learn 1.6.x, which has no wheel for 3.14 - on a 3.14 venv the model
downloads and then reports itself unloadable, naming the reason. The product is
complete without it, which is why it is advisory and optional.

### Deploying

See [`DEPLOY.md`](DEPLOY.md). `Dockerfile` and `fly.toml` are checked in; the
container entry point is `python -m scripts.serve`, which creates the schema,
replays the demo timeline **only if the database is empty**, then serves.

---

## What the demo shows

Before Jira is ingested, `sync order` reports:

```
21 state change(s): 0 exact, 21 bounded
orderable pairs: 0 of 420 possible
no ordering is provable - no causal chain can be built
```

That is the honest state of a spreadsheet-only portfolio. Every change was observed
in one scan window, so nothing can be shown to precede anything else, and the system
can say *what* moved but must stay silent about *why*.

Add the Jira changelogs and the same command reports:

```
27 state change(s): 6 exact, 21 bounded
orderable pairs: 121 of 702 possible
581 pair(s) remain unprovable and are dropped, not hedged
```

The chain the demo is built around:

```
CAUSE    HRMS-108.planned_end   2026-03-04 -> 2026-03-16
         [Mar 04 09:12 .. Mar 04 09:12]   exact     (Jira changelog)
EFFECT   QA-001.blocked         No -> Yes
         [Mar 06 09:00 .. Mar 18 09:00]   bounded   (Excel diff)

cause -> effect : bounded_disjoint      <- provable, so the link is kept
effect -> cause : unprovable            <- correctly not reversible
```

Four things worth noticing, because each is a decision rather than an accident:

**Precision is not a caveat, it is the input to a rule.** The cause is a point; the
effect is a twelve-day window. The link survives only because the point falls
before the window opens.

**A scan has to fall between cause and effect.** Had the QA backlog grown before the
6 March scan, both changes would share a window and the link would be dropped. This
is a real property of snapshot data, not a limitation of the implementation.

**The first sync emitted zero changes.** No baseline, so nothing was observed to
change. A differ that reports a first import as hundreds of changes has
manufactured hundreds of candidate causes.

**One row was rejected, visibly.** A duplicated `Task ID` would have silently
overwritten the row above it. Rejections are counted, surfaced, and stored with the
offending row.

## Layout

```
app/
  ids.py                    <source>:<Entity>:<connection>:<pk> - deterministic keys
  models/
    base.py                 audit + provenance mixins; raw_data_id is the evidence pointer
    raw.py                  _raw_* - immutable, what the source actually returned
    tool.py                 _tool_* - vendor shape; also the differ's baseline
    domain.py               vendor-neutral model, incl. state_changes and its bounds
    sync.py                 watermarks, run log, sheet scans, rejects
  ingest/
    runner.py               one sync path, two triggers; advisory lock; watermarks
    sources/excel/
      reader.py             header contract, sha256 skip, quarantine
      identity.py           which row is which, across scans
      snapshot_diff.py      two snapshots -> changes (pure, no DB)
      ingest.py             persistence, and where time bounds are decided
    sources/jira/
      replay.py             captured payloads -> _raw_jira_* (the only faked part)
      extractor.py          raw JSON -> tool rows, still Jira's shape
      convertor.py          tool rows -> tasks + exact state changes
  intelligence/temporal/
    ordering.py             provably_before - the guard every causal claim rests on
scripts/
  gen_demo_data.py          writes the workbooks a PM would have produced
  gen_jira_data.py          writes Jira-shaped payloads, no Jira required
  sync.py                   CLI
tests/                      56 tests; differ, identity and ordering need no database
```

### Layer boundaries worth keeping

- `snapshot_diff.py` and `identity.py` are **pure** - no session, no ORM, no clock.
  The logic most likely to be subtly wrong is therefore testable without a database,
  which is why they carry most of the tests and run in a tenth of a second.
- **Only `ingest.py` and `convertor.py` decide time bounds.** Two places to audit.
- **Fake the collector, never the raw table.** `replay.py` writes captured JSON into
  `_raw_jira_*`; the extractor and convertor are the code a live connection drives,
  so swapping in real HTTP touches one file.

---

## Next

1. One live model call. `narration/client.py` is built and its failure path is
   verified, but no successful round trip has been made - there are no API
   credentials on the machine it was written on.
2. Retrieval over past projects (pgvector), so a finding can cite a precedent.
   First on the cut list.
3. A scheduler. `ingest/runner.py` already takes a Postgres advisory lock per
   source, so a second instance joins an in-flight run rather than double-writing.
4. The five remaining screens, still static mockups under `../Layout/`.
