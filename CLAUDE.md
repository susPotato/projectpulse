# ProjectPulseAI — working context

Read this first. It is the handoff between sessions.

**Last updated:** 2026-09-07

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
| `ProjectPulseAI_Architecture.md` | **Design of record.** Schema, contracts, repo layout, build sequence, risk register. Start here for anything technical. |
| `projectpulse/README.md` | How to run what exists. |
| `Layout/*.html` | 6 static UI mockups (vanilla ES5, no build). Visual language only — **no screen exists for any AI surface**; the insight/evidence screen is new design work. |

Reference repos, **read-only, never run**: `devlake/` (schema + ETL patterns we ported),
`gpt2sp/` (issue-text corpus only — 5 columns, no timestamps, cannot drive anything).

---

## 3. Where things stand

### Done and verified

**Ingestion is complete for both sources**, plus the ordering guard.

```
27 state changes: 6 exact, 21 bounded
121 of 702 pairs orderable
56 tests, ~0.05s, no database required
```

- **Excel path** — header contract, sha256 skip, row-identity resolution, snapshot
  differ, reject quarantine. Produces `precision='bounded'` changes.
- **Jira path** — `jira_replay` reads captured Jira-shaped JSON into `_raw_jira_*`;
  extractor and convertor downstream are the code a live connection would drive.
  Produces `precision='exact'` changes.
- **`app/intelligence/temporal/ordering.py`** — `provably_before`, the interval
  arithmetic every causal claim rests on.
- **Retriever console** — `python -m scripts.demo`, a local page at :8000 for editing
  the source files and watching the effect.

### Not built yet

Rules engine (ZEN), schedule engine (NetworkX), retrieval/pgvector, causal chain
templates, `InsightBundle`, narration + validator, the React insight route, APScheduler.

### Deployed

- Architecture page: **https://arch.mintteas.org** (Cloudflare Pages project
  `projectpulse-arch`, source `site/index.html`, config `wrangler.toml`).
  Redeploy: `npx wrangler pages deploy` from the repo root.
- `mintteas.org` apex is deliberately **free** for the demo app later.
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
   transport. Seeding domain rows directly would bypass the differ, the identity
   resolver and the precision model, and prove nothing.
5. **The ML duration classifier is advisory only** (not yet wired). It returns a bucket,
   never a number, and `intelligence/schedule/` must never import it.
6. **Graphiti is dropped, not deferred** — it is LLM-driven and would have a model
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

---

## 6. Gotchas already hit — don't rediscover these

| Symptom | Cause / fix |
|---|---|
| `NOT NULL constraint failed: sync_runs.id` on SQLite | SQLite only auto-increments `INTEGER PRIMARY KEY`, never `BIGINT`. Use `BigIntPK` from `app/models/base.py`. |
| `'EmptyCell' object has no attribute 'column'` | openpyxl read-only mode. `reader.py` reads `iter_rows(values_only=True)` and works from tuple indices. |
| `Object of type datetime is not JSON serializable` | Excel date cells. Tool payloads and rejects are stored via `normalized_payload()`. |
| **A rename produced delete + insert** | `identity.py` excluded `~anon-` keys from the match pool, so an id-less row could never match itself after a rename. **Fixed**; guarded by `test_a_rename_is_matched_not_re_keyed`. This is the bug the module exists to prevent — if it recurs, causal chains become fiction. |
| `pip install --upgrade pip` corrupted a fresh venv | Skip the pip self-upgrade. |
| Windows file locks on `.venv` | `rm -rf .venv` can fail; move it aside instead. |

---

## 7. Commands

```bash
cd projectpulse

# Console: edit data/demo files, watch the effect. http://127.0.0.1:8000
python -m scripts.demo

python -m pytest                      # 56 tests, no DB needed
docker compose up -d                  # postgres+pgvector on :5433 (Docker Desktop must be running)
python -m scripts.sync init

# Replay the March timeline. --now matters: scan times bound every Excel change.
python -m scripts.gen_jira_data
python -m scripts.gen_demo_data --step 0
python -m scripts.sync run --source excel       --now 2026-03-02T09:00
python -m scripts.sync run --source jira_replay --now 2026-03-04T12:00
python -m scripts.gen_demo_data --step 1
python -m scripts.sync run --source excel       --now 2026-03-06T09:00
python -m scripts.gen_demo_data --step 2
python -m scripts.sync run --source excel       --now 2026-03-18T09:00

python -m scripts.sync order          # what can actually be ordered
python -m scripts.sync changes rejects runs
```

Venv is `projectpulse/.venv` (Python 3.14.5, SQLAlchemy 2.0.52). Default
`DATABASE_URL` is Postgres on :5433; export `sqlite:///pulse.db` to run without Docker.

**Note:** a uvicorn console server may still be running on :8000 from the previous
session. Kill it if the port is taken.

---

## 8. What to do next, in order

1. **`intelligence/temporal/templates.py` + `chains.py`.** 121 pairs are orderable, but
   *orderable is not causal*. Match ordered pairs against ~6 named hypotheses
   (environment delay → QA blocked, dependency slip → milestone risk, capacity drop →
   velocity drop, scope add → drift, blocker aging → backlog, defect spike → UAT delay).
   A chain must be a pattern a PM asked for, not any two events in the right order.
   Exclude `identity_confidence='low'` changes from chains.
2. **`intelligence/context.py` + `rules/engine.py` (ZEN).** Aggregate to ~30 flat scalars
   *before* ZEN — it evaluates one flat record, and fighting it to aggregate costs a day.
   `engine.py` must be the only module importing `zen`, so a failed wheel spike is a file
   swap, not a rewrite. Timebox the spike to 2h.
3. **`intelligence/schedule/`** — NetworkX critical path → `delay_days`,
   `affected_milestones`. ⚠️ **Blocked on a real gap: no source has dependency edges.**
   The `dependencies` table exists but nothing populates it. Fix by adding a
   `Predecessor` column to the Excel template we control, plus WBS-implicit edges.
   Decide this before specifying the schedule engine.
4. **`InsightBundle`** (`api/schemas/insight.py`) — freeze it early; it unblocks the UI
   and narration independently of the data.
5. **`narration/`** — build `fallback.py` (Jinja) *before* `client.py`. It is the safety
   net and the offline demo insurance. Then `validator.py`, the 8-stage gate.
6. **React insight route** — Vite + TS, ONE route for round 1. The other five mockups
   stay static HTML and get ported in round 2. Reconcile the mockups' two conflicting
   `:root` token sets first (Family A is Tailwind slate, Family B is gray;
   `fpt-pm-dashboard.html:186` references an undeclared `--red`).

### Cut order if time runs short

Decide now, not at 2am on day 5: retrieval/precedent → the LLM (ship fallback prose) →
the rule-table editor (show tables read-only) → Excel fuzzy matching (require `Task ID`).

**Never cut:** the evidence panel, the precision model, the rule trace. They are the product.

---

## 9. Style notes

- `snapshot_diff.py`, `identity.py` and `ordering.py` are **pure** — no session, no ORM,
  no clock. Keep them that way; it is why the riskiest logic is testable in 0.05s.
- Comments explain *why*, especially where a subtle failure is being prevented.
- Docstrings on modules state what the module is responsible for and what breaks if it
  is wrong.
