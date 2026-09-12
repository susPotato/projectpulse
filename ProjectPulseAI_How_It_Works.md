# ProjectPulseAI — What it is, how the logic works, and where the AI is

> A plain-language walkthrough of the PiMSathon entry: what the competition is, how
> the application decides things, which parts are a language model, which parts are
> ordinary code, and what each is and is not allowed to do.
>
> Companion documents: `ProjectPulseAI_Product_Design_v2.md` (what and why),
> `ProjectPulseAI_Architecture.md` (layers, schema, contracts),
> `CLAUDE.md` (living session log and the invariant list),
> `projectpulse/README.md` (how to run it).

---

## 1. What PiMSathon is

**PiMSathon** is the hackathon this product was built for — an internal FPT
competition around **PiMS**, the project-information-management domain. Teams build
something that helps a *project manager* do their job, and are judged on PM
capability as much as on engineering.

The format shaped almost every design decision in this repository:

| Stage | When | What happens |
|---|---|---|
| Round 1 — code submission | Fri **2026-09-11** | The codebase is handed in. |
| Round 1 — live evaluation | Sat **2026-09-12** | ~30 minutes per team. Judges **run** the code and **read** it. |
| Round 2 | ~2 weeks later | Deeper build-out. |
| Final | October | Judged on PM capability and presentation as much as on the system. |

Three consequences worth naming, because they explain things in the code that would
otherwise look like over-engineering:

- **Judges run the code on their own machine.** So every optional dependency is
  genuinely optional — no API key, no Docker, no ML wheel, no Rust wheel is required
  for the product to work end to end. Each one degrades to a working path and *says*
  that it did.
- **Judges read the code.** So the module docstrings carry the reasoning, not just
  the API. They are written to be read by a person deciding whether to trust the
  system.
- **A PM is the audience.** So the product's central claim is not "we have AI", it is
  **"every number on this page can be traced back to a cell someone typed."**

The scenario data models an FPT-style portfolio: a program (*Digital Transformation
2026*) containing projects (HRMS, SAIN, and a deliberately-sparse example project),
fed from the two systems a real PM actually has — **Jira** and **Excel**.

---

## 2. The product in one sentence

> **Collect the data, compute the finding four ways, then let a language model put
> it into words it is not allowed to change.**

ProjectPulseAI ingests a PM's spreadsheets and Jira exports, computes delivery
findings deterministically, and presents them as an *insight bundle* — a headline, the
numbers behind it, the rule that fired, the evidence rows it rests on, and prose that
was written around those numbers rather than instead of them.

### The one governing rule

> **Deterministic where possible, generative only for explanation.**
> Anything a rule or a graph traversal can decide, it decides.
> The model's job is to make that decision readable, not to make it.

Reverse that order and this becomes a chatbot with a database.

---

## 3. The shape of the system

```
  Jira export        Excel sheets          (the two sources a PM really has)
       |                   |
       v                   v
  +--------------------------------------+
  |  INGESTION                           |   raw -> tool -> domain
  |  identity, diffing, time bounds      |   nothing is faked past the raw table
  +--------------------------------------+
       |
       v  tasks, edges, QA items, state_changes (each with a time INTERVAL)
  +--------------------------------------+
  |  INTELLIGENCE  (100% deterministic)  |
  |   1 graph      the dependency DAG    |
  |   2 schedule   forward pass, slip    |
  |   3 temporal   provable ordering     |
  |   4 contention shared-people cost    |
  |   5 context    flatten to ~30 numbers|
  |   6 rules      a readable table      |
  |   7 assembler  EVERY number is born  |
  +--------------------------------------+
       |
       |  findings carrying {{tokens}}, never digits
       v
  +--------------------------------------+
  |  NARRATION  (the only gated LLM use) |
  |  prompt has no digit in it           |
  |  8-stage validator on the way back   |
  |  numbers substituted AFTER validation|
  +--------------------------------------+
       |
       v
   Screens: Portfolio, Programs, Projects, Insight, Schedule/Gantt, Team,
            Risk, Dashboards, Reports, Agent
```

The single most important boundary is the one between **Assembler** and
**Narration**. Everything above it produces numbers. Everything below it produces
sentences. Nothing crosses in the wrong direction.

---

## 4. How the logic is handled, stage by stage

All of this is ordinary Python. No model is involved in any of it.

### 4.1 Ingestion — `app/ingest/`

Two collectors, one destination shape.

**Excel** (`ingest/sources/excel/`)

- `reader.py` — finds the header row rather than assuming row 1, hashes the file
  (sha256) to skip unchanged sheets, and quarantines rows it cannot read.
- `identity.py` — **which row is which, across scans.** This is the real risk in
  spreadsheet ingestion, not the diffing. A renamed task looks like a delete plus an
  insert, which manufactures two state changes that never happened, which can
  manufacture a causal chain that never happened. Policy, in order:
  1. a stable `Task ID` from the template we control → confidence **high**
  2. no id, but a close title match against last scan → confidence **low** (recorded,
     shown on the timeline, but the causal engine refuses to build on it)
  3. neither → the row is **rejected, visibly**, and stored with the reason

  A sheet with no id *column* at all is rejected outright rather than silently
  fallback-matched.
- `snapshot_diff.py` — two snapshots in, changes out. **Pure**: no session, no ORM, no
  clock. Three rules: *no baseline, no changes* (a first import emits zero, not
  hundreds); *normalize before comparing* (`3`, `3.0` and `" 3 "` are one value typed
  by three people); *only tracked fields* (a Notes column changing is not a delivery
  event).

**Jira** (`ingest/sources/jira/`)

- `replay.py` writes captured payloads into the raw table; `extractor.py` and
  `convertor.py` are the code a live connection would drive. **Invariant: fake the
  collector, never the raw table.** Swapping in real HTTP touches one file.
- `export_sheet.py` converts a real Jira "general_report" export into the app's own
  blank schedule template — so an upload from Jira goes down the *same* path as a
  hand-filled sheet, with no second ingestion path to keep in step. It handles the
  three things that make such an export hostile: the table does not start at row 1;
  one issue can span a block of rows (rows are collected by "has a Key", never by
  position or stride); and it carries ~400 columns, most empty, several containing the
  export page's own JavaScript. It fills what the export has, **leaves the rest empty
  rather than inventing it**, and `coverage()` reports what did not come across.

`ingest/runner.py` is **one sync path with two triggers** (the two-hourly poll and the
PM pressing "Update now"). It takes a Postgres advisory lock so a manual run joins an
in-flight one instead of double-writing, advances watermarks **only on success**, and
logs every run with its counts including refusals.

### 4.2 The precision model — the core correctness rule

This is the piece the whole "why is this happening" feature rests on.

| | Jira | Excel |
|---|---|---|
| what a sync gives | a changelog — each transition timestamped | a snapshot — only what is true *now* |
| state changes from | collection (free) | diffing scan N against scan N−1 |
| `precision` | `exact` (both bounds equal) | `bounded` — `[previous scan, this scan]` |

Every state change carries an **interval**, never a timestamp. The entire ordering
guard is four lines:

```python
def provably_before(a, b) -> bool:
    """a's LATEST possible time must precede b's EARLIEST."""
    return a.occurred_at < b.occurred_at_lower
```

Three traps it deliberately avoids:

- **Never compare `scan_id`.** Sync windows overlap, so two bounded events from
  different scans can still overlap. Same-scan is *sufficient* for unorderable, not
  *necessary*.
- **`exact` gets no shortcut.** A Jira event at 14:00 on day 2 inside an Excel window
  spanning days 1–3 is genuinely unorderable.
- **Adjacent scans share a boundary instant**, so two bounded changes in consecutive
  windows are never orderable. A provable ordering needs a scan strictly *between*
  them — which makes poll frequency a product decision, not an ops setting.

**Unprovable orderings are dropped, never downgraded to a hedge.** A caveat is
something a reader skips; an absent claim is not.

What the demo shows, exactly:

```
Excel only:  21 changes, 0 exact, 21 bounded
             orderable pairs: 0 of 420 possible
             no ordering is provable - no causal chain can be built

+ Jira:      27 changes, 6 exact, 21 bounded
             orderable pairs: 121 of 702 possible
             581 pairs remain unprovable and are dropped, not hedged
```

That first block is the honest state of a spreadsheet-only portfolio, and the product
says so rather than inventing a story.

### 4.3 Causal chains — `intelligence/temporal/`

A chain is emitted only when **all three** hold:

1. `ordering.py` proved the pair happened in that order,
2. `templates.py` says the pair matches a named hypothesis a PM actually asks about,
3. the dependency graph says the two entities are connected at all.

Any one alone is a bug: ordering alone gives coincidences, patterns alone give claims
about events that may not have happened in that order, links alone give a graph with
no time in it.

Four further rules, each *dropping* rather than hedging:

- Low-confidence identity is excluded outright (a chain built on a title-matched row
  would be fiction with a citation).
- **The lag is an interval, not a number.** Collapsing a bounded gap to a midpoint
  would invent precision the snapshot never had.
- The link *recorded* is the one actually found, not the one the template asked for.
- One chain per cause/effect pair; `group_chains` collapses them per cause so a
  finding cannot appear 48 times.

### 4.4 Schedule — `intelligence/schedule/`

- `graph.py` builds the dependency DAG, **twice**: once with every edge, once with only
  edges a human explicitly stated. `stated_only` is not a debug flag — a critical path
  computed partly from inferred `wbs_implicit` edges is a weaker claim, and a PM taking
  a date to a steering committee is entitled to know which they are looking at. Only
  `FS` edges are traversed; `SS`/`FF`/`SF` are parsed and stored so an MS-Project paste
  survives, but nothing reasons about them yet.
- `impact.py` — the forward pass. The useful output is **not** "how late is this task"
  (a PM can read that off their own sheet); it is **`propagated_days`: how late a task
  will be that nobody has written down yet.**

  ```
  earliest start = max(own start, each predecessor's projected finish + lag)
  projected end  = earliest start + the duration the plan already implies
  ```

  No estimate, no model, no judgement.
- `forecast.py` — a **range** of finish dates. A Monte Carlo over assumed task-duration
  distributions is the textbook answer and is **forbidden here**, because choosing the
  distribution means the modeller chooses the shape of the answer. Instead the sample
  is *measured*: every task carrying both a `baseline_end` and a `planned_end` gives one
  observation of how far this plan actually moves, and those observations are resampled
  with replacement onto tasks that can still move, re-running the forward pass per
  trial. Nothing fits a distribution.
- `whatif.py` — recovery scenarios. Cheap because the forward pass is a **pure function
  of (tasks, edges)**: copy, change one thing, re-run, diff. Two moves the pass actually
  honours — `compress` (shorten a task) and `overlap` (negative lag, MS-Project's
  `FS-10d`). **A simulation is not a mutation**: nothing is written, so the observed
  sheet and the precision model are untouched.

### 4.5 Resource contention — `intelligence/contention.py`

The one legitimate cross-project claim the product can make. Two projects sharing a
person is *observable*; two projects being "related" is not.

```
S = supply    effort-days person p can deliver in window w
D = sum(di)   demand on p across every project in w
E = D - S     the excess. No contention when E <= 0.
```

- **The quantity is effort, not delay.** Organisations absorb overload as overtime,
  then descope, then quality shortcuts, and only last a visible slip. So delay in days
  is reported as a *scenario* with its absorption assumption named beside it, never as
  a finding. (A PM told "SAIN slips 13 days" who then watches the team work overtime
  for a fortnight and hit the date has been given a tool that is wrong on its first
  demo.)
- **Apportionment, never replication.** Each victim takes a *share* of `E`. Handing
  every victim the whole excess would turn 15 effort-days of overload into 30, and
  adding a project that demands nothing would double the reported delay.
  `ContentionResult.check` asserts conservation.

### 4.6 Units — `app/units.py`

Every conversion between hours, person-days, person-months and allocation percent
happens in one module. Nowhere else may hold a factor — a person-month read as a
person-day is a 20× error that looks entirely ordinary in a chart. A quantity carries
its unit (`Effort` has no default); a conversion that cannot name its input unit
raises rather than guessing; and factors are **program-scoped**, so a client estimating
at 20 days per 人月 and a vendor at 22 are never silently pooled.

### 4.7 Scope — `app/scope.py`

Jira and Excel each create their own `projects` row for the same piece of delivery.
`scope.resolve()` / `source_ids_for()` re-point them at one canonical id, so a risk
filed under the Excel-side id is recognised as the same project as the Jira-side id.
Programs are keyed source-neutrally (`program:Program:0:<KEY>`) — a program is the
thing the delivery is *for*, not an artifact of whichever collector ran first. A
project whose program nobody stated belongs to **no** program and is reported as
unassigned, rather than quietly filed under an invented one.

### 4.8 Context — `intelligence/context.py`

Everything the rules engine is allowed to see, flattened to ~30 **scalars**. All
aggregation happens here, in Python, where it is readable and testable. Two properties
it must preserve:

- **Every scalar is derived, never estimated.** Each field is a count, a ratio of
  counts, or arithmetic on dates a human typed. Nothing here is a model output.
- **Names are stable.** These keys are the rule table's vocabulary; renaming one would
  silently stop a rule firing, so the record is built from a declared dataclass and
  `as_record()` is the only way to produce it.

### 4.9 The rules engine — `intelligence/rules/`

Rules are declared **as data**, which compiles to ZEN's JDM for the Rust engine and is
*also* directly interpretable by a built-in Python evaluator. Both backends are checked
against each other over the same records in `tests/test_rules.py`; `backend_name`
reports which one ran, because a silent fallback nobody notices is its own kind of
failure. If the ZEN wheel fails to build on a judge's machine, the rule layer does not
go with it.

The full default table today — every row is something a delivery manager can read and
argue with:

| Rule id | Category | Severity | Fires when |
|---|---|---|---|
| `status_not_maintained` | data quality | — | `tasks_in_progress >= 5` and `tasks_done == 0` |
| `tasks_overdue` | schedule risk | medium | `tasks_overdue >= 1` |
| `due_soon_none_finished` | schedule risk | — | `tasks_due_soon >= 3` and `tasks_done == 0` |
| `work_not_moving` | schedule risk | — | `tasks_stale >= 3` and `tasks_done == 0` |
| `deadline_cluster` | schedule risk | medium | `tasks_on_busiest_due_date >= 4` |
| `single_owner_project` | resource risk | — | `distinct_owners == 1` and `task_count >= 5` |
| `schedule_inconsistent_major` | schedule risk | high | `max_propagated_days >= 5` |
| `schedule_inconsistent_minor` | schedule risk | — | `1 <= max_propagated_days < 5` |
| `milestone_at_risk` | milestone risk | high | `milestones_at_risk >= 1` |
| `qa_queue_stalled` | quality | — | `qa_blocked_ratio > 0.6` and `qa_count >= 5` |
| `qa_queue_building` | quality | — | `0.3 < qa_blocked_ratio <= 0.6` and `qa_count >= 5` |
| `root_cause_available` | root cause | info | `chains_dependency_backed >= 1` |
| `conclusion_rests_on_inferred_edges` | evidence quality | medium | `depends_on_inferred_edges == True` |
| `identity_confidence_low` | data quality | medium | `low_confidence_ratio > 0.2` |
| `rows_rejected` | data quality | low | `rows_rejected >= 1` |
| `baseline_missing` | data quality | — | `baseline_coverage < 0.5` and `task_count >= 3` |
| `contention_breaches_overtime` | resource risk | — | program context, and the overtime limit is breached |
| `contention_material` | resource risk | — | `contention_pressure_days >= 3`, limit not breached |
| `contention_minor` | resource risk | — | `0 < contention_pressure_days < 3` |

The first six exist because **a single Jira export can still fill a page**: they need
only status, owner and due date, which is all such an export carries. The last three
are the only rules that need a program's worth of data.

Two hard constraints on the table:

- **A rule row may only compare numbers.** The operator set is deliberately tiny
  (`>`, `>=`, `<`, `<=`, `==`, `!=`). Anything needing a traversal belongs in
  `context.py` as a named scalar — the moment a rule contains a graph walk it stops
  being reviewable by the person whose judgement it encodes.
- **Headlines contain `{{tokens}}`, never digits.** A token naming a field the context
  does not produce is caught by `validate_table` at construction, not discovered by a
  PM on a page. A rule referencing an unknown field raises rather than quietly never
  firing.

Every hit carries a **trace**: the rule id, the thresholds it compared, and the actual
values it compared them against. The trace is built in Python from the same `Condition`
objects both backends share, so the explanation is identical whichever engine ran. It
is not debug output — a PM being told their milestone is at risk is entitled to see it.

### 4.10 The assembler — `intelligence/assembler.py`

**Where every number in the product is born, and the only place it is formatted.**

Rules and templates emit prose containing `{{tokens}}`; this module substitutes them
from the same context record the rule fired on. A headline saying "11 days" and an
evidence panel saying "12 days" cannot happen, because both read the same key from the
same dict through the same formatter. An unresolved token is a **hard failure** — the
finding is dropped and the reason recorded, because a PM must never see a literal
`{{max_propagated_days}}`.

It is also what makes the language model safe to use: the model is handed **tokenised**
text, and substitution happens *after* validation, so a model that hallucinated a
number would have had to invent a token name — which `substitute` reports as
unresolved rather than passing through.

### 4.11 Confidence — `intelligence/confidence.py`

The delivery outlook is rendered as a **band** (high / medium / low), never a
percentage — the pitch deck's "89% confidence" and a mocked "61% → 91%" were both
rejected as numbers nothing could defend. The score is `coverage × freshness`, with
freshness decaying linearly from 4 hours (as fresh as polling gets) to 168 hours.
**Precedent is deliberately not in the formula**: it needs retrieval that is not built,
and the design's rule is to drop the input and say so rather than multiply by a silent
`1.0`. `precedent_available` carries that fact onto the bundle.

### 4.12 Explain — `intelligence/explain.py`

Renders each task's arithmetic as **input → algorithm → output**: the cells that came
off the spreadsheet; one numbered step per operation carrying the question it answers,
the formula in words, the same formula with real values substituted, and the result;
then the figures the findings quote. A reader can redo any line on paper. It computes
nothing new — it reads the same `ImpactReport` the findings were built from, so the
page and a finding cannot disagree. A value the sheet never supplied reads as
*"not in the sheet"*, never as `0`: zero means "no slip", absent means "cannot say",
and conflating them reports a project with no baseline as permanently on time.

### 4.13 Effort / burn — `intelligence/effort.py`

A burn chart is the one PM visual that is almost always fiction — a self-reported
"remaining" figure plotted against an ideal line that was never a commitment. This one
is built from observations: every point is a scan of the worklog sheet, and every rise
between two points is an `hours_spent` change the differ detected, bounded to the
interval between those scans. The planned line is **one value, not a series**, because
the plan's history was never observed; scope growth is reported as a count of rows
added, which *was* observed.

### 4.14 The pipeline — `intelligence/pipeline.py`

The only module in `intelligence/` that touches a database session. Everything it calls
is pure, which is why the hard parts test in milliseconds. Order matters — each stage
needs the one before it:

1. **Load** tasks, edges, QA items, changes
2. **Graph** the dependencies, twice (all edges / stated only)
3. **Project** the schedule forward
4. **Chains** — provably-ordered pairs matched against named hypotheses
5. **Context** — flatten to ~30 scalars
6. **Rules** — evaluate (the only stage a delivery manager edits)
7. **Assemble** — substitute numbers, attach evidence
8. **Narrate** — and this stage *cannot fail*

`portfolio()` runs `analyze_project()` separately per project and folds the results.
It never takes a shortcut across projects, which is why the program view can never
disagree with what you see drilling into one project — it is the same computation,
looped.

---

## 5. Where the AI is

There are **six** generative or ML surfaces. They are not equally trusted, and the
differences are structural rather than a matter of prompting.

### 5.1 Narration — the gated one (`app/narration/`)

**Job:** turn findings the engine already computed into readable prose.
**Trust model:** the model is assumed *careless*, not malicious.

Three fences, in order:

1. **The prompt contains no digit.** `client.py` builds the brief from tokenised
   templates and **refuses to send** one containing a figure. This matters as much as
   validation: a model that copied a number it was shown would be indistinguishable on
   the page from one that computed it.
2. **An 8-stage validator on the way back** (`validator.py`). All stages run even after
   one fails, so a prompt can be fixed with every objection visible at once:

   | Stage | Rejects |
   |---|---|
   | `shape` | empty, truncated or runaway drafts |
   | **`no_literal_digits`** | **any digit outside a token — the load-bearing one** |
   | `known_tokens` | a token naming a fact that does not exist |
   | `required_tokens` | quietly dropping a number the finding must state |
   | `known_entities` | task ids the bundle never mentioned |
   | `no_unsupported_causation` | "because" with no causal link behind it |
   | `no_overclaimed_certainty` | turning a projection into a promise |
   | `no_model_artifacts` | meta-commentary, fences, leaked instructions |

3. **Substitution happens after validation.** A model that invented a figure would have
   had to write it as a literal — and a literal digit is exactly what stage 2 refuses.
   **A model can therefore never change a number, only fail to produce one.**

**Vendors:** Anthropic, OpenAI, Gemini and an FPT gateway, each reduced to one
signature — `(system, user) -> str`. Nothing in the fence changes when the vendor does;
the validator, the substitution, the retry-with-objections and the template fallback
are identical whichever adapter runs, because none of them is trusted at all. A missing
SDK is not an error — each adapter imports its package on first call, so the package
stays importable with none of the four installed. A self-hosted model (Ollama, vLLM,
LM Studio) works through the `openai` adapter with an endpoint override.

**When it is off, absent or broken:** `fallback.py` renders the deterministic template
and the reason is recorded beside it. A missing package, a missing key, a rejected
draft or a dead socket all land there. **No finding and no figure changes** — only the
phrasing. This is why the whole product demos with no API key at all.

### 5.2 The AI Dashboard Generator (`app/dashboard/generator.py`)

**Job:** "Describe the dashboard you want" → a board.
**Leash:** the model picks tile keys from `catalogue.CATALOGUE`, a **closed set** of
**36 tiles** (12 program-scope, 24 project-scope) the app already knows how to render.
A key it invents that is not in the catalogue is **dropped** — the same discipline as
the validator's `known_entities` stage. Max 10 tiles. There is nothing here for a model
to invent, because it is choosing and ordering, not computing. It is explicitly told it
has no access to live data.

Non-AI alternatives on the same button row: a blank canvas, five fixed templates
(`program_delivery_control`, `project_delivery_control`, `it_portfolio_dashboard`,
`sprint_delivery_report`, `project_delivery_review`), and — the interesting one —
**fit** (below).

### 5.3 Fit-to-data (`app/dashboard/fit.py`) — *not AI at all*

Worth listing here because it solves the problem people usually reach for AI to solve.
A fixed template is a guess about the data: `project_delivery_review` assumes a
baseline, a dependency graph and a worklog, and a project ingested from a single Jira
export has none of the three. Applied there it lays out thirteen tiles of which most
render empty — and **a dashboard that is sparse because the inputs are sparse looks
exactly like one that is sparse because the project is fine.** Those are opposite
situations.

So tiles declare the **signals** they need — `tasks`, `baseline`, `edges`, `effort`,
`history`, `risks`, `program`, `siblings` — and the board is the set whose needs are
met. The probe is a handful of `SELECT COUNT(*)`-shaped reads, not the pipeline, so
pressing the button is instant. It deliberately does **not** hide a tile whose numbers
are currently zero: "no risks logged yet" is a true and useful statement about a
project that has a register; "this source cannot express a dependency" is a different
statement, and only the second is a reason to leave a tile off. The test is the *shape*
of what was ingested, never whether the news is good.

### 5.4 Custom tiles (`app/dashboard/custom.py`)

**Job:** a person pastes their own data and gets a chart.
**Leash:** the model is a **parser**, not a calculator. It is asked for strict JSON
(`{title, chart_type, labels, values}`) over data *the person supplied*, and the output
is validated before anything touches the database. It computes no number.

Three rules keep the conversational path honest:

- **The deterministic path goes first.** "make it a line chart", "rename it to Q3
  spend" are exact operations, applied by `_local_revision` with no model and no
  network. Only what it cannot match is worth a call.
- **A CSV/TSV fallback** (two columns, `label,value`) runs when there is no model, or
  after, when the model's output does not parse — so "paste a two-column table" keeps
  working with narration switched off entirely.
- **The server diffs; the model does not narrate.** A revision regenerates the whole
  chart, so a rename can come back having also moved a value. `diff_drafts` compares
  the two drafts and the server words the result, so a transcript line cannot claim
  something the preview does not show.

### 5.5 The tile-building agent, with tools (`app/dashboard/agent.py`)

**Job:** "track each employee's effort and compare them" — a report over data the app
already has, where asking a person to paste hours they would first have to go find in
Jira is the wrong answer from a product whose whole job is knowing that number.
**Leash:** a small set of **read-only tools**, each a thin wrapper over a function the
pipeline already exports (`team_project`, `analyze_project`, `forecast_project`) or
that `app/risks/` already computes.

The key property: **a tool call is a lookup against a deterministic bundle, never a
second calculation.** The governing rule survives contact with tool use because of
*what the tool is*, not because of an extra check bolted on afterwards. The model
chooses what to look at and how to chart it; the final JSON still passes the same
parse-and-validate gate every other tile path uses. It runs only after the
deterministic local revision finds nothing, and only when nobody pasted raw data for
that turn.

### 5.6 Agent chat (`app/agent/`) — the one ungated surface, on purpose

A free-form multi-turn chat, dispatched through whichever vendor narration is already
configured with. **No tool use, no file access, no command execution** — the tab says
so in its own banner.

It is deliberately the *opposite* kind of feature, and is isolated to make that
visible: a different page, a different tab, never the same trust claim as Insight or
Risk. Its system prompt tells it explicitly that it has **not** been given the
project's live data and must never invent figures, dates or statuses as though it had
— and to point the user at Insight or Risk for anything only those pages can answer.
Pasted URLs are fetched best-effort (`link_fetch.py`, stdlib only; PDF is honestly
unsupported rather than silently wrong) and handed in as marked blocks.

### 5.7 The ML duration classifier (`app/ml/duration.py`) — advisory, structurally

Wraps `omaradly/jira-task-duration-classifier` (TF-IDF over issue text + one-hot
categoricals + scaled numerics → logistic regression). It is the only *predictive*
model in the system, and three things make "advisory" a property of the code rather
than a promise in a comment:

- **It returns a band, never a number** — `Short` / `Standard` / `Long-running`. The
  pipeline's class probabilities are reduced to a coarse confidence band before leaving
  the module, because a probability is a number a model invented, and a number a model
  invented must never reach a page whose entire claim is that its figures are
  arithmetic on dates a human typed.
- **`intelligence/schedule/` may never import it.** `tests/test_ml.py` walks the AST of
  every module under `app/intelligence/` and fails if one does. Mixing an estimate into
  the forward pass would make `propagated_days` unfalsifiable.
- **`basis` reports how many real features were available.** The model was trained on
  Jira metadata (description, priority, issue type, labels, votes, watches); a
  spreadsheet row has a title, dates and an owner, so an Excel-sourced prediction rests
  almost entirely on title text and the reader is told so.

Fully optional: no `scikit-learn`, no artefact, no feature — and it needs Python 3.12 or
3.13, because the published artefact was pickled by scikit-learn 1.6.x which has no
3.14 wheel. On 3.14 it downloads and reports itself unloadable, naming the reason.

---

## 6. AI vs. rule-based, side by side

| | **Rule-based / deterministic code** | **AI (LLM)** | **ML classifier** |
|---|---|---|---|
| Where | `app/ingest/`, `app/intelligence/`, `app/risks/`, `app/units.py`, `app/scope.py`, `dashboard/fit.py`, `app/exports/` | `app/narration/`, `dashboard/generator.py`, `dashboard/custom.py`, `dashboard/agent.py`, `app/agent/` | `app/ml/duration.py` |
| Produces | every number, date, band, severity, chain, rating and finding | sentences; a tile ordering; a parse of pasted data; chat replies | one of three duration bands |
| May invent a number | **no — structurally impossible** | **no — the prompt has no digit, and the validator refuses literals** | n/a — it cannot emit one |
| Fails how | loudly: a rejected row, a dropped finding, a raised error | silently and safely: falls back to the deterministic template with a recorded reason | absent |
| Required to run the app | **yes** | **no** | **no** |
| Reviewable by a PM | yes — the rule table is rows of thresholds, and every hit carries its trace | the prose only | the band only |

### What the deterministic side can do

- Decide what is at risk (19 rules, editable, each firing with a trace)
- Compute how late a task will be that nobody has written down yet (`propagated_days`)
- Quote a finish date as a **range** resampled from how this plan has already drifted
- Prove — or refuse to claim — that one event caused another
- Simulate recovery scenarios (compress, overlap) without mutating anything
- Price the cost of two projects sharing a person, in conserved effort-days
- Rate a risk off a probability × impact matrix (one grid, not one per renderer)
- Show the full arithmetic behind any number, step by step, on `/api/explain`
- Select a dashboard from what a project actually has
- Render `.docx`, `.xlsx` and Markdown reports that format no number of their own
- Hand out a blank `.xlsx` template generated **from the sheet contract**, so the file
  a PM fills in and the file the ingester reads are provably the same file

### What the AI side can do

- Phrase the findings — and only re-phrase; the draft is dropped if it drifts
- Turn "show me delivery health across the program" into a board of real tiles
- Turn a messy paste into a chart
- Look up real project data through read-only tools and chart it
- Hold an open conversation on the Agent tab, clearly labelled as such
- Sort a task into a coarse duration band, clearly labelled as advisory

### What the AI side cannot do, anywhere

- Emit a figure, date or percentage that reaches a page
- Invent a task id, a tile key or a token
- Assert causation that the ordering guard did not prove
- Write to the database, run a command or touch a file
- Change any finding, severity, band or rating
- Be required for the product to work

---

## 7. Turning the AI off entirely

This is a supported, tested mode, not a degraded one:

| Surface | With no model configured |
|---|---|
| Insight narrative | deterministic template from `fallback.py`, with the reason shown |
| Dashboard generate | the AI path is unavailable; blank / template / **fit** all work |
| Custom tile | CSV/TSV fallback parser — paste two columns |
| Tile revisions | `_local_revision` handles the exact commands with no network |
| Agent tab | reports itself unavailable rather than erroring |
| Duration bands | simply absent |
| **Everything else** | **identical — no finding and no figure changes** |

Provider, model, endpoint and key are set at `/settings` (or via `PULSE_NARRATION=1`,
`PULSE_NARRATION_PROVIDER`, `PULSE_NARRATION_MODEL`). The settings page's **Test it**
button runs a real narration through the real validator and reports which path wrote
the summary and, if it fell back, why. The key lives in plaintext in
`.pulse/narration.json` (gitignored) — the same bargain as a `.env` file — an
environment variable takes priority when it is blank, and settings can only be changed
from the machine the app runs on.

---

## 8. The screens, and which bundle each reads

| Screen | Route | Reads |
|---|---|---|
| Portfolio | `/portfolio` | `GET /api/portfolio` — every project, folded |
| Programs | `/programs` | `/api/programs`, `/api/programs/{id}` |
| Projects | `/projects` | `/api/portfolio` + `/api/programs` |
| Insight | `/insight` | `GET /api/insight` — the `InsightBundle`: findings, evidence, rule traces, chains, narrative |
| Schedule / Gantt | `/gantt` | `/api/gantt`, `/api/forecast`, `/api/explain` |
| Team | `/team` | `/api/team` — allocation, contention, the burn chart from observed increments |
| Risk | `/risk` | `/api/risks` (+ create / edit / delete) |
| Program dashboard | `/programs/dashboard` | `/api/dashboards`, `/api/dashboard/catalogue` |
| Project dashboard | `/project/dashboard` | same, project scope |
| Reports | `/reports` | `/api/report/preview`, `/api/report.{md,xlsx,docx}` |
| Agent | `/agent` | `POST /api/agent/chat` |
| Settings | `/settings` | `/api/settings`, `/api/settings/test` |
| Upload | (on Sources) | `POST /api/sources/upload` — sheets and Jira exports |

Dashboard population routes, all against the same board: `/api/dashboards/blank`,
`/apply-template`, `/default`, **`/fit`** (deterministic), **`/generate`** (AI).

Reports render the **same bundles the screens render** and format no number of their
own, so a `.docx` handed to a steering committee cannot disagree with the page it came
from.

---

## 9. The invariants

These are product thesis, not implementation detail. Changing one is a product
decision, not a refactor.

1. **Numbers and dates are born in exactly one place** — `intelligence/assembler.py`.
   The model emits `{{tokens}}`, never a digit, and **is never shown a digit either.**
2. **Every finding resolves to a source record.** `_raw_data_id` is stamped by the
   extractor and **copied, never re-derived**, through raw → tool → domain. That chain
   is the evidence panel.
3. **Unprovable orderings are dropped, not hedged.**
4. **Fake the collector, never the raw table.**
5. **The ML duration classifier is advisory only** — a bucket, never a number, and
   `intelligence/schedule/` must never import it (enforced by an AST-walking test).
6. **A chain needs a pattern, an ordering *and* a link.**
7. **One delivery project can have several source ids** — reconciled in `app/scope.py`.
8. **Graphiti is dropped, not deferred** — it is LLM-driven and would have a model
   assign temporal validity intervals, i.e. produce dates.

---

## 10. Honest limits

- **Retrieval over past projects is not built.** So `precedent` is absent from the
  confidence formula rather than filled with a constant, and `precedent_available`
  says so on the bundle. It is first on the cut list.
- **Only `FS` dependency edges are traversed.** `SS`/`FF`/`SF` are stored, not reasoned
  about — silently treating an SS edge as FS would move dates by the length of a task.
- **No cross-project dependency edges exist in the schema.** Projects are siloed for
  computation and meet only at the program aggregation layer for display — plus the one
  deliberate exception, resource contention, which genuinely compares a person's
  allocations across their projects.
- **A single Jira export cannot light up most of the product**, and the app says so up
  front via `coverage()` rather than letting a blank dashboard imply it. No baseline
  means recorded slip is 0 — which becomes knowable from a *second* export later,
  because that is exactly what the differ compares.
- **The scheduler is not wired.** `ingest/runner.py` already takes the advisory lock it
  would need.

---

## 11. Running it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows
docker compose up -d                                   # postgres + pgvector on :5433
python -m scripts.sync init

python -m scripts.demo          # http://127.0.0.1:8000
python -m pytest -q             # ~800 tests, no database required
```

No Docker? `DATABASE_URL=sqlite:///pulse.db python -m scripts.sync init` — enough for
everything except pgvector.

Replay the demo timeline from the CLI, with scan times simulated by `--now` (what
bounds every spreadsheet change is *when we looked*):

```bash
python -m scripts.gen_jira_data
python -m scripts.gen_demo_data --step 0
python -m scripts.sync run --source excel       --now 2026-03-02T09:00   # baseline: 0 changes
python -m scripts.sync run --source jira_replay --now 2026-03-04T12:00   # 6 exact changes
python -m scripts.gen_demo_data --step 1
python -m scripts.sync run --source excel       --now 2026-03-06T09:00   # schedule slips
python -m scripts.gen_demo_data --step 2
python -m scripts.sync run --source excel       --now 2026-03-18T09:00   # QA backlog grows

python -m scripts.sync order      # what can actually be ordered
python -m scripts.sync changes    # what moved
python -m scripts.sync rejects    # what we refused, and why
```

Optional extras, each independently skippable:
`.[llm]` (Claude) / `.[llm-openai]` / `.[llm-gemini]` · `.[ml,ml-fetch]` (duration
bands, Python 3.12–3.13) · `.[report]` (`.docx`) · `.[onedrive]`.

Production: `projectpulse.fly.dev` (Fly.io + Neon Postgres). The container entry point
is `python -m scripts.serve`, which creates the schema, replays the demo timeline **only
if the database is empty**, then serves. See `projectpulse/DEPLOY.md`.
