# ProjectPulseAI

Delivery intelligence for project managers: a deterministic analysis system with a
language interface. Rules, graph traversal and arithmetic produce every finding;
the language model only writes the sentences.

Design: [`../ProjectPulseAI_Product_Design_v2.md`](../ProjectPulseAI_Product_Design_v2.md) ·
Architecture: [`../ProjectPulseAI_Architecture.md`](../ProjectPulseAI_Architecture.md)

**Status:** ingestion complete for both sources, and the ordering guard that makes
causal claims possible. Rules, schedule math, narration and API to follow.

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
were refused. It polls every two seconds, so saving a workbook in Excel makes it say
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
python -m pytest        # 56 tests, no database required
```

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

1. `temporal/templates.py` + `chains.py` - match ordered pairs against named
   hypotheses (environment delay -> QA blocked, and five more), so a chain is a
   pattern a PM asked for rather than any two events that happen to be orderable.
2. Rules (ZEN) and the schedule engine over the ingested data.
3. `InsightBundle`, narration, validator, and the React insight route.
