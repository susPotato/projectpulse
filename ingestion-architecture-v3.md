# ProjectPulseAI — Document ingestion and analysis

**Design document.** How schedules, estimation documents and Jira data become a
queryable, computable, auditable delivery model.

This is a plan, not an implementation. Schemas are given as field contracts,
algorithms as stated rules and formulas. Code belongs in the repository.

---

## 0. Governing rule

> The LLM extracts and narrates. It never stores, and it never calculates.

Everything between extraction and narration is typed data and deterministic
code. Every number in the UI was produced by a function reading rows in a
database, and every row traces back to a byte range in an uploaded file.

### Invariants

These are the contract. Anything that breaks one is a defect, not a tradeoff.

| # | Invariant | Enforced by |
|---|---|---|
| I1 | The same canonical facts produce the same output, byte for byte | no LLM in the compute path; deterministic tie-breaks everywhere |
| I2 | Every canonical fact cites at least one parsed block | validation rule, checked at commit |
| I3 | Raw bytes are immutable; removal happens only through the redaction path (§1.5) | append-only writes; the redaction path tombstones and purges, never rewrites |
| I4 | Every effort quantity carries an explicit unit | unit is a required field with no default |
| I5 | Conserved quantities stay conserved | apportionment, not replication (§9) |
| I6 | A result that did not converge says so | residual is part of the result object |

I1 has a consequence people miss: any ordering used in a computation needs a
deterministic tiebreak. "Order projects by start date" is not deterministic when
two projects start the same day. Every sort key ends with the entity id.

**I1 is scoped from canonical downward, deliberately.** Extraction runs on a
hosted model and is not reproducible byte for byte (§5). Everything above
canonical is re-derivable but not guaranteed identical, and the invariant must
never be read as a claim about the model.

---

## 1. Storage layers

Four layers. Data flows downward only; each layer keeps a pointer upward.

```
raw/        immutable uploaded bytes, content-addressed
  ↓
parsed/     structured blocks with position (sheet!cell, page+bbox, field)
  ↓
extracted/  typed domain records, unvalidated, with confidence
  ↓
canonical/  validated, entity-resolved, versioned facts
            — the only layer the analysis engine reads
```

### 1.1 Raw

| Field | Purpose |
|---|---|
| `sha256` | content address; identical bytes are stored once *within a program* |
| `storage_uri` | pointer to the bytes; contents never mutate |
| `filename`, `mime_type`, `byte_size` | as uploaded |
| `declared_kind` | what the user said it is |
| `detected_kind` | what the probe concluded (§3) |
| `program_id`, `project_hint` | scoping |
| `uploaded_by`, `uploaded_at` | audit |

**Dedupe on bytes, not on the upload event.** The same file can legitimately be
uploaded twice — as a different kind, or against a different project. Store the
bytes once; record each upload as its own ingestion. Telling a user "you already
uploaded this" is a UI affordance, not a reason to discard the event.

Scope the content address per program. A shared client template uploaded by two
customers must not resolve to one blob — dedupe is an optimisation, and it is not
worth a cross-tenant read path.

### 1.2 Parsed

A parsed block is a fragment of a document with a position you can point at.
Parsers are pure: `bytes → list[ParsedBlock]`. No database, no network, no LLM.
That makes them testable and re-runnable, which is what lets you re-derive
history after a parser fix instead of asking for the file again.

| Field | Purpose |
|---|---|
| `document_id` | provenance upward |
| `parser_version` | e.g. `xlsx-schedule/2.1`; re-derivation key |
| `block_type` | table, row, cell, paragraph, issue |
| `ordinal` | stable order within the document |
| `locator` | position — the thing that makes the system trustworthy |
| `content` | text representation |
| `payload` | typed values when available |

Locator shape, one per source type:

| Source | Fields |
|---|---|
| Excel | `sheet`, `range`, `row`, `col` |
| PDF | `page`, `bbox` |
| Jira | `issue_key`, `field` |
| Text / Markdown | `char_start`, `char_end`, `line` |

### 1.3 Extracted

Output of extraction, before validation. Kept separately so you can see what the
model produced when something goes wrong, without it having touched anything
real.

| Field | Purpose |
|---|---|
| `extractor` | `llm/estimation@v3` or `parser/xlsx-schedule@v2` |
| `record_type` | task, estimate, milestone, assumption, allocation |
| `payload` | conforms to the record type's schema |
| `source_blocks` | provenance → parsed blocks |
| `confidence` | 0–1; null for deterministic parsers |
| `status` | pending, valid, rejected, needs_review |
| `validation_errors` | why, if rejected |

**Provenance needs real integrity.** An array-of-ids column cannot carry a
foreign key in Postgres, so nothing stops a dangling reference — and I2 is then
a convention rather than a guarantee. Use a join table
(`record_id`, `block_id`) so the database enforces it.

### 1.4 Canonical

Facts are versioned, never overwritten.

| Field | Purpose |
|---|---|
| `entity_type`, `entity_id` | stable identity across versions |
| `version`, `valid_from`, `valid_to` | transaction time: `valid_from` is commit time, `valid_to = null` means current |
| `payload` | the fact |
| `source_record` | provenance → extracted record |
| `resolution_method` | which strategy matched (§7) |
| `is_baseline` | part of the agreed baseline |

A partial index on `(project_id, entity_type) where valid_to is null` and a
`current_facts` view over the same predicate keep the rest of the code simple.

**Say which time axis this is.** As written it is transaction time — when the
system learned the fact. That answers "what did the dashboard say last Tuesday",
which is the question a PM asks after a correction lands. It does not answer
"when was this true in the plan", so a week-4 upload correcting week-2 dates
records `valid_from` at week 4. If as-of-plan history is needed later, add the
second axis then; do not blur the two into one column.

**Baseline is a property of the upload, not of the version number.** Marking
`is_baseline = (version == 1)` is wrong: a task that first appears in week 3's
upload also gets version 1, so newly added scope enters the baseline it is
supposed to be measured against — and `scope_growth` then reports zero for
exactly the scope it exists to catch. Baseline is set when a document is
designated as the baseline upload, and nowhere else.

### 1.5 Redaction and retention

Append-only is a correctness property, not a retention policy. A Toyota-tier
client will require deletion terms, and the personal data sitting in schedules
and worklogs carries a statutory removal route: under APPI art. 35 a person may
demand cessation of use or erasure of their retained personal data, and the
PPC's own guidance treats a departed employee's data as within scope once the
purpose it was collected for is exhausted. "Never deleted" is not a promise this
system can make.

Removal therefore runs through one path, never a `DELETE` at a call site:

| Kept | Purged |
|---|---|
| `sha256`, the ingestion event, parser and extractor versions | the bytes at `storage_uri` |
| fact versions, provenance edges, the redaction record | parsed block `content` and `payload` |

Signals derived from purged blocks are marked `underivable` rather than silently
recomputed from what survives. A number that can no longer be re-derived must say
so — the same rule as I6. Retention window and legal hold are program
configuration, set before the first client upload.

---

## 2. Pipeline

```
upload → hash → detect kind → route → parse → extract → validate
       → resolve entities → version → recompute signals → invalidate tiles
```

Run as a job, not inside the HTTP request. A 3 MB schedule takes long enough
that the user needs a progress view.

| Stage | Input | Output | Fails how |
|---|---|---|---|
| hash | bytes | content address | never |
| detect | headers, filename, declaration | kind + confidence | ambiguous → flag, don't guess |
| parse | bytes | parsed blocks | structural → reject document |
| extract | selected blocks | typed records | over-production → caught by the groundedness rule (§6) |
| validate | records | violations | errors block, warnings annotate |
| resolve | records + current facts | entity ids | unresolved → review queue |
| commit | resolved records | fact versions | atomic per document |

**Atomicity, precisely.** One transaction per document, but "all or nothing"
applies to the *valid set*. Records held for review are excluded by design, not
by failure. The distinction matters when a PM asks why a document shows as
ingested with 40 of 45 lines live.

---

## 3. Detection and routing

Don't trust the extension. Don't trust the user's declaration alone. Combine
both with a cheap structural probe over the header row.

Score each candidate kind by header overlap, then apply:

| Condition | Action |
|---|---|
| best score below floor | `unknown`, route to manual classification |
| declaration and detection agree | proceed |
| they disagree, detection weak | ingest as declared, flag for review |
| they disagree, detection strong | hold at manual classification, do not parse |

**Normalize the score against the smaller set.** Dividing the overlap by the
size of the reference header set makes kinds incomparable, because the sets are
different sizes. If the schedule set has nine entries (English plus Japanese)
and the Jira set has five, an English-only schedule tops out around 0.56 while a
Jira export matching three headers scores 0.60 — and the schedule loses to the
wrong kind. Score each kind against a fixed set of required headers instead.
`min(|reference|, |observed|)` fixes the comparability problem but introduces its
own: the denominator shrinks with the input, so a two-column document matching
one header scores 0.5 and a one-column document scores 1.0. If you do use `min`,
floor the denominator at the size of the kind's required-header set.

Never silently reinterpret a client's document. Silent reinterpretation is the
kind of bug that destroys trust in one demo. But neither should a strong
disagreement be parsed as declared and shipped with a flag on it: that runs the
wrong parser and produces wrong numbers, just annotated ones. Hold it, the same
as `unknown`.

---

## 4. Parsing

One parser per `(kind, format)` pair, each version-tagged.

### Excel

Notes from real client workbooks:

- **Find the header row.** Never assume row 1. Client schedules carry logos,
  revision history and approval stamps above the table.
- **Merged cells hold their value only in the top-left cell.** Every other cell
  in the range reads as empty. Expand merged ranges before reading.
- **Formula values are cached, not computed.** Reading with `data_only` returns
  what Excel last stored. A workbook produced by a tool rather than by Excel has
  no cache, and every rollup silently reads as empty. Estimation sheets are
  mostly `=SUM()`. Detect this explicitly: if a column that should be numeric is
  entirely empty under cached-value reading, raise rather than ingest zeros.
- **Row count is unreliable.** The reported last row counts formatting-only
  cells, so the scan can run far past the data. Stop on a run of empty rows.
- **Indentation encodes WBS hierarchy** in MS Project exports. Read the cell
  indent level, or the outline column if present.
- **Dates arrive in five shapes**: datetime, Excel serial number, `2026/04/01`,
  `R8.4.1` (Japanese era), `4月1日`. Normalize in one place against an explicit
  list of accepted formats, and reject rather than guess. Note the epoch: the
  1900 system contains a nonexistent 29 Feb 1900, and Mac-origin files may use
  1904.
- **Effort units are ambiguous.** `8` might be hours, days or person-days. Look
  for a unit in the header, then in a nearby cell. Never default. Where neither
  yields one, emit the block flagged `unit_unknown` and let the review queue do
  the asking — parsers are pure (§1.2) and cannot prompt anyone.

### Jira

Use the REST API rather than CSV — you get typed fields, changelogs and
worklogs. The changelog is what gives you status transition timing, which is how
`blocked_time` is computed.

**The API changed and the old approach no longer works.** The legacy search
endpoint has been removed from Jira Cloud; the replacement uses token-based
pagination rather than an offset. Two consequences that affect correctness, not
just plumbing:

1. **Changelog expansion on search is capped at 40 entries per issue**, down
   from 100 on the old endpoint. Truncation is silent. Any long-lived
   integration issue exceeds 40 transitions easily, so `blocked_time` and
   `rework_ratio` come back plausible and wrong — the exact failure class §13
   is about. Fetch changelogs through the dedicated bulk changelog endpoint
   instead of expanding them on search.
2. **Sort order differs between the two paths.** Search-expanded changelogs come
   back newest-first; the paginated changelog endpoint returns oldest-first.
   Normalize on read.

Server and Data Center instances are unaffected by both. Story-point field ids
vary per instance and must be resolved at configuration time, not hardcoded.

---

## 5. LLM extraction

Used only where structure runs out: estimation prose, assumption lists, status
notes, meeting minutes. Never for tables a parser can read.

**Constrain the input.** Send the parsed blocks already identified as relevant,
with their locators, so the model can cite them back. Never the whole document.

**Constrain the output.** Define the record schema, pass it as a JSON schema,
and use structured-output or tool-call mode rather than asking for JSON in
prose.

**Make refusal cheap.** State explicitly that an empty list is a correct answer,
and that returning null beats inventing an identifier. Models over-produce when
the schema contains a list.

Record types and their required fields:

| Record | Required | Notes |
|---|---|---|
| `EstimateLine` | effort value, **effort unit**, description, source blocks | wbs id nullable; never inferred |
| `Assumption` | statement, category, source blocks | testable predicate optional |
| `UnitNote` | statement, source block | any document text defining what units mean |

Batch blocks into chunks that respect table boundaries — splitting a table
mid-way produces orphaned rows with no header context. Run chunks concurrently,
merge afterward.

**On reproducibility.** Zero temperature reduces variance but is not a
determinism guarantee on hosted models. I1 is scoped from canonical downward for
exactly this reason (§0).

Note that validation is not review. A well-formed `EstimateLine` with a unit, a
positive value and provenance passes every error rule in §6, so with no gate it
auto-promotes to canonical with no human in the loop — and re-running extraction
on identical bytes then yields different canonical facts. That is I1 failing at
the exact layer §0 scopes it from, which makes the scoping sentence in §0 false
as written rather than merely narrow.

**Gate on what the record carries, not on where it came from.** Any model-sourced
record carrying an effort quantity — today that is `EstimateLine` — is held at
`needs_confirmation` and reaches canonical only on an explicit accept.
`Assumption` and `UnitNote` put no number into the compute path and auto-promote
as before. Every canonical fact records `origin` (deterministic or model) and,
where confirmed, who accepted it, so the reproducibility claim is checkable
instead of asserted.

The scoping in §0 is then true as written: nothing enters canonical carrying a
number that either a parser produced or a human accepted.

---

## 6. Validation

Most of the code lives here, and it should. Validation is cheap to write,
catches real errors, and is the difference between a demo and a tool.

| Rule | Severity | Rationale |
|---|---|---|
| provenance present | error | untraceable facts can't be audited (I2) |
| extracted values present in a cited block | error | provenance alone does not stop invention (§2) |
| effort unit stated | error | the whole model is denominated in effort-days (I4) |
| effort value positive | error | non-positive effort is a parse artifact |
| dates ordered (start ≤ finish) | error | breaks schedule math silently |
| WBS id resolvable | error | the `scope.py` bug class, one level down |
| person resolvable to a resource | error | capacity math needs a real person |
| roll-up sums match | warning | usually a parse miss, occasionally a real doc error |
| effort magnitude plausible | warning | catches unit confusion in one direction |
| duplicate WBS id in one document | warning | common in hand-maintained workbooks |

`error` → record held at `needs_review`, does not reach canonical.
`warning` → record proceeds, flag surfaces in the UI.
`needs_confirmation` → passes every rule, still waits for a human (§5).

The groundedness rule is what §2 means by catching over-production. Provenance by
itself does not: a model can cite a perfectly real block and still emit a number
that is not in it. Require each effort value and date value to be present in the
cited block's `payload` or `content` — numeric match with the unit applied, not
string containment — and reject when it is not.

### Two tolerance decisions that need stating

**Roll-up tolerance is not purely relative.** A 2% relative band fails on small
parents: a parent of 4 person-days whose children are each rounded to the
nearest half-day can drift 0.5 PD legitimately, which is 12.5%. Use
`max(2% × declared, 0.5 person-days)` so the rule is tight where precision
exists and forgiving where rounding dominates.

**Magnitude checks only catch unit errors in one direction.** A cap of "more
than 2000 hours on one line" catches inflation. The dangerous error is the
opposite: a person-month value read as person-days understates by 20×, and 3
person-days looks entirely ordinary. No single-value check can see this. The
only reliable detector is cross-source: compare the estimate line against
schedule duration and against Jira time spent for the same WBS, and flag when
they disagree by more than an order of magnitude.

---

## 7. Entity resolution

Week 2's upload must update week 1's tasks, not duplicate them. Resolve in
strict order, stopping at the first hit.

| Step | Method | Deterministic |
|---|---|---|
| 1 | Natural key — Jira issue key, WBS id, schedule UID | yes |
| 2 | Stable composite — project, normalized name, parent WBS | yes |
| 3 | Fuzzy — name similarity **and** overlapping dates **and** same assignee | threshold-dependent |
| 4 | Ask — review queue with a side-by-side diff | human |

Name normalization: Unicode NFKC, strip leading numbering, collapse whitespace,
lowercase. NFKC folds full-width ASCII to half-width, which is what you want for
`ＷＢＳ１２３`; note it also pushes half-width katakana the other way, which is
fine but worth knowing when debugging.

Step 3 requires all three conditions, not any. Fuzzy name matching alone merges
"Unit test — CXPI driver" across two unrelated projects.

**Always record which strategy matched.** When a PM reports that the tool
duplicated their tasks, the resolution method on each row tells you within
seconds whether the cause is a changed WBS id or a fuzzy threshold. Without it
you are reverse-engineering from data.

This is the same problem `app/scope.py` already solves at project granularity.
The two should share a module.

### Source precedence

Entity resolution says which rows are the same thing. It does not say which value
wins when two sources describe that thing differently in the same week — and with
no rule, the later commit wins, which makes the answer depend on upload order.
That is an I1 violation arriving through the side door.

State precedence per field, not per document:

| Field | Wins | Why |
|---|---|---|
| status, actuals, transitions | Jira | system of record for execution |
| baseline dates, WBS structure | the designated baseline upload | designated, never inferred |
| effort estimate and unit | estimation workbook | where the number is authored |
| assignee | Jira, falling back to schedule resource | closest to reality |

Where sources disagree beyond tolerance, emit a `source_conflict` signal rather
than resolving silently. A schedule and a Jira board disagreeing about the same
task is itself a finding, and often the most useful one on the page.

### Person identity is the hard case

§10 runs entirely on shared people, so person resolution is load-bearing rather
than a side rule. `山田太郎` in a merged schedule cell, `Yamada Taro` in an
estimation workbook and a Jira `accountId` are one human, and no natural key
connects them. Maintain an explicit person alias table — id, display names in
both scripts, Jira account, employee code where available — populated at
configuration time and editable by the program. Treat it as reference data the
program owns, not as an extraction output.

---

## 8. Units — one owner, no exceptions

§13 names silent unit conversion as the most damaging failure class. The design
should therefore give conversion exactly one home, and it currently doesn't —
the factors are implied in at least four places: the roll-up validator converting
children to person-days, `effort_variance` converting Jira seconds, capacity
demand and supply in effort-days, and `allocation_load` in percent.

Define one module that owns:

| Constant | Value | Source of truth |
|---|---|---|
| hours per person-day | 8 | program configuration |
| person-days per person-month | 20 (some firms use 22) | program configuration, per client |
| working-day calendar | per region | holiday calendar, including Japanese public holidays |

Every conversion goes through it, every converted value records the factor used,
and the factors are program-scoped rather than global. A Yazaki estimate at 20
days per 人月 and a vendor estimate at 22 must not be silently pooled.

`allocation_load` in percent and `capacity_gap` in effort-days are two
denominations of the same quantity. Fix the bridge here —
`percent × working days in window = effort-days` — rather than at each call site.

**`schedule_slip` must declare calendar or working days.** A five-day slip
across Golden Week is five calendar days and two working days. Pick working
days, say so, and use the same calendar as capacity.

---

## 9. Signals

Every observation the analysis engine reasons about is a signal: typed,
magnitude-carrying, scoped to a project and optionally to a person or milestone,
computed deterministically from canonical facts.

| Field | Purpose |
|---|---|
| `signal_type`, `magnitude`, `unit` | the observation |
| `project_id`, `person_id`, `milestone_id` | scope |
| `window_start`, `window_end` | the period observed |
| `derived_from` | fact version ids |
| `computation` | function name and version |
| `materiality` | 0–1, display threshold |

### Starting set

| Signal | Unit | Definition | Window |
|---|---|---|---|
| `effort_variance` | effort-days | time spent − estimate, per WBS | cumulative |
| `scope_growth` | effort-days | estimated effort of work created after baseline | since baseline |
| `rework_ratio` | ratio | effort on reopened and bug issues ÷ total effort | **rolling, see below** |
| `schedule_slip` | working days | current finish − baseline finish, per task | point-in-time |
| `float_consumed` | working days | baseline float − current float to next milestone | point-in-time |
| `allocation_load` | percent | Σ allocation per person per period | per period |
| `capacity_gap` | effort-days | demand − supply per person per period | per period |
| `throughput_delta` | ratio | recent velocity ÷ velocity required for remaining scope | **rolling, see below** |
| `blocked_time` | working days | time in blocked status, from changelog | rolling |
| `assumption_breach` | boolean | a tested predicate is now false | point-in-time |

Two definitions need pinning down or they misbehave:

**`rework_ratio` must be windowed.** A cumulative denominator grows monotonically,
so the ratio becomes inertial — late-project rework barely moves it, which is
exactly when you want it to move. Use a rolling window, same length as velocity.

**`throughput_delta` is undefined near the deadline.** Required velocity is
remaining scope ÷ remaining time, which diverges as remaining time approaches
zero and is meaningless past it. Floor the denominator at one period and report
"past deadline" as a distinct state rather than a large number.

**Worklog-derived signals need a coverage gate.** `effort_variance` and
`rework_ratio` assume worklogs reflect work. In offshore setups time is often
logged weekly in round numbers, or backfilled to match the plan — in which case
variance measures timesheet compliance and trends toward zero. Compute a coverage
ratio per project per window (issues with worklogs ÷ issues with transitions) and
suppress both signals below a threshold rather than reporting a confident zero.

**Materiality needs a definition, not just a column.** Magnitude alone is not
material — three days of slip against thirty days of float is noise, and against
one day of float it's the headline. Define materiality relative to what the
target has left: `magnitude ÷ remaining float` for schedule effects,
`magnitude ÷ remaining effort` for effort effects, clamped to 1. Note the
circularity: materiality of a float effect depends on remaining float, which
Channel 2 itself changes. Evaluate it against the converged float, not against
the value at the iteration where the effect was raised.

**Only signals carrying a person or milestone may cross a project boundary.**
That constraint is what keeps the cross-project model honest — you have real
data for shared people and shared gates, and for nothing else.

**Person-scoped signals are employee data.** `allocation_load` and `capacity_gap`
rendered next to a name are performance and working-hour information regardless
of intent, and in Japan they sit directly beside the working-hour statute (§10).
Default every view to team or role, put person-level behind explicit permission,
and keep historical per-person load out of any view a line manager does not own.

---

## 10. Cross-project analysis

`analyze_project()` takes a program context and the portfolio iterates to a
fixed point. The program layer is a context object passed into the same
per-project function, not a second computation — which is why drilling into a
project shows the same numbers the rollup used.

There are exactly two legitimate cross-project channels.

### Channel 1 — shared people

For person *p* and window *w*:

```
S   = supply, effort-days available to p in w
D   = Σ dᵢ, total demand on p in w across projects
E   = D − S,  the excess (no contention if E ≤ 0)
```

Two modes, and they are genuinely different computations rather than one formula
with a parameter. Let `Dv` be the aggregate victim demand and `L` the working
days in the window.

**Mode A — no priority order. This is the default,** because written priority is
usually stale and the live order is political. No project is protected, every
contender is a victim, and the shortfall is apportioned by demand share. It
degrades gracefully when priority is unknown and it is symmetric, so no PM is
singled out by an arbitrary tiebreak.

```
per victim  sᵢ = E · dᵢ / Dv      Σ sᵢ = E,  and Dv = D in this mode
duration    Δ  = E · L / Dv       identical for every victim, ≤ L
```

The per-project rate cancels — `sᵢ ÷ (dᵢ / L)` reduces to `E · L / Dv` — which is
why every victim shows the same duration.

**Mode B — a trustworthy priority order exists.** Walk the order, giving each
project `aᵢ = min(dᵢ, supply remaining)`. The first project to receive less than
it asked for, and every project below it, are the victims.

```
per victim  sᵢ = dᵢ − aᵢ          Σ sᵢ = E  (allocation exhausts S when D > S)
duration    Δᵢ = sᵢ · L / dᵢ      per victim, no longer identical, ≤ L
```

Mode B is not Mode A with the top project lifted out. An earlier draft protected
only the highest-priority project and apportioned by share among the rest, which
uses the top of the ordering and throws away everything below it: with A > B > C
each demanding 10 against a supply of 20, strict priority gives B its full 10 and
C nothing, while the hybrid takes 5 from each of B and C. An order trustworthy
enough to protect A is trustworthy enough to rank B against C.

`E` is the conserved quantity and `sᵢ` is what the impact edge stores. **Δ is
derived for display and is not additive** — not across victims, where summing it
re-creates exactly the replication error this section exists to remove, and not
across windows either. A window's Δ is the delay *if* the shortfall lands in a
later window with free capacity; under sustained contention the shortfalls queue
and compound, so per-window Δ understates. Assert `Δ ≤ L`, and name the
single-window assumption in the UI next to the number.

**Worked check, Mode A (the default).** Window of 20 working days, supply 20
effort-days, projects A and B each demanding 15. `D` = 30, `E` = 10, `Dv` = 30.
Each loses `10 × 15 / 30` = 5 effort-days and so receives 10 of its 15; at a
planned rate of 0.75 effort-days per day that is 6.7 days. The formula gives
`10 × 20 / 30` = 6.7. ✔

**Worked check, Mode B.** Same window, priority A > B. A takes its full 15, B
receives 5 of its 15, so `sB` = 10; at B's rate of 0.75 that is 13.3 days, and
`10 × 20 / 15` = 13.3. ✔ A is not a victim and shows zero. ✔

**The overload case, and why the clamp is gone.** L = 20, S = 20, A ahead of B, A
demanding 25 and B demanding 5. The waterfall gives A 20 and B 0, so `sA` = 5 and
`sB` = 5, summing to `E` = 10. `ΔA` = 5 × 20 / 25 = 4 days and
`ΔB` = 5 × 20 / 5 = 20 days, both inside the window. An earlier draft protected
A's full 25, which pushed the arithmetic past physical possibility — 40 working
days of delay inside a 20-day window — and needed a `min(E, Dv)` clamp to stay
bounded. It also left A absorbing an overload the model never attributed to it.
The waterfall removes both: `Σ sᵢ = E` holds in Mode A and Mode B alike, so the
clamp never binds under either. Keep `Σ sᵢ ≤ Dv` as an assertion rather than a
computation; if it fires, the allocation step is wrong.

**Two corrections against earlier drafts.**

*Replication.* Giving every victim the full excess breaks I5. With three projects
sharing an overloaded person, two victims each absorbed the entire excess — 30
effort-days of consequence from 15 effort-days of overload. Adding a project to
the person's plate doubled the induced delay without adding any demand.

*Wrong denominator.* Normalizing the excess by total supply rather than by victim
demand understates the delay by the factor `Dv / S`, which is below 1 whenever
victims collectively demand less than the full supply — nearly always. In the
Mode B check above it returns 10 days instead of 13.3.

Guards:

- `S = 0` — a person with no availability in the window. Every demand is a
  shortfall; report the contention without computing a rate.
- `dᵢ = 0` — a victim with no demand takes no shortfall. Skip it rather than
  divide.
- `Dv = 0` — under the earlier protected-top rule this was reachable and silently
  produced 0 ÷ 0 whenever the sole demander was the protected project. Under the
  waterfall that project is itself the first victim, so `Dv = 0` alongside
  `E > 0` is now unreachable and should be asserted against, not handled.

### Absorption — what the shortfall becomes

The shortfall `E` is an effort quantity. Calling it schedule delay assumes delay is how the
organisation absorbs overload, and usually it is not. The observed order is
overtime first, then descope, then quality shortcuts, and only last a visible
slip. A PM told "B slips 13.3 days" will watch the team work 残業 for two weeks
and hit the date, and the tool is wrong on its first demo.

So `contention_pressure`, in effort-days, is the signal. Delay in days is a
scenario, rendered only with its absorption assumption named beside it.

Overtime is worth modelling explicitly because in Japan the ceiling is hard and
checkable rather than cultural. Under the 2019 labour-standards reform the 36協定
limit is 45 hours a month and 360 a year; a 特別条項 relaxes it but stays bounded
by 720 hours a year, under 100 hours in any single month including holiday work,
an 80-hour average across any 2–6 months, and at most six months above 45 — with
penalties attached. Carry each person's month-to-date overtime and state the
finding as capacity plus legality:

> B holds its date only if Tanaka absorbs 10 effort-days, which puts him at 52
> overtime hours this month — his second month above 45 this year.

That is actionable, grounded in a number the client already tracks, and it does
not require the tool to guess how the team will respond.

### Supply is systematically overstated

`S` read from a resource plan is optimistic in three ways, all in the same
direction — so the failure mode is under-detecting contention, which demos well
and loses trust later.

- **Non-project load.** Line management, interviews, training, audit and QMS
  preparation, 勉強会. Wheelwright and Clark's PreQuip case is the canonical
  illustration: 960 engineering months a year looked sufficient until non-project
  development work was counted, at which point the portfolio was overcommitted.
- **Concurrency penalty.** The formula above conserves effort by construction,
  which is false of people — the same engineer across three projects delivers
  less in total than across one. Wheelwright and Clark's curve peaks at two
  concurrent assignments and falls below the single-assignment level at three; a
  survey of 64 high-technology firms reached the same two-project conclusion, and
  engineers spread across several projects commonly spend only a quarter to a
  third of their time on value-adding work. Model it as a factor on `S` keyed to
  concurrent project count, program-configurable.
- **Nominal versus productive allocation.** "50% allocated" is rarely 50%
  delivered.

Make availability an explicit program-configuration factor rather than an
implicit 1.0, and once actuals exist, measure it back instead of assuming it.

The widely quoted "multitasking costs 40% of productive time" is a laboratory
result about switching between tasks on a seconds-to-minutes scale, not a finding
about project staffing. It is the wrong citation for this factor; the concurrency
work above is the right one.

### Detect per person, act per role

Contention is real at person granularity because that is where the data is. Every
remedy available to a PM or to HR is at role granularity — hire a tester, move a
BRSE, rebalance inside the RL78 pool. Roll person-level findings up to role for
anything that recommends an action, or the output has no reachable response.

### Channel 2 — shared milestone float

Float on a shared program milestone behaves as a pool among the projects feeding
the same gate through the same dependency chain. This is a modelling
simplification — in strict CPM, parallel paths each carry their own float — and
should be labelled as one in the UI. It is a defensible one: in construction
scheduling, where float has been litigated for decades, the mainstream position
when the contract is silent is that float belongs to the project as a shared
resource consumed first-come-first-served, which is AACE's recommended practice
29R-03. Expect feeding PMs to argue that the padding in their own path is theirs
regardless. Lead with consumption `cᵢ`, which is observable from schedule
versions and hard to dispute, and treat pressure as the derived view.

The asymmetry with Channel 1 is deliberate — allocations have owners, pooled
float does not — and it should be said in the UI, because the first PM to notice
will ask why people are apportioned by priority and float is not.

With baseline float *F*, per-project consumption *cᵢ* and total *C = Σ cᵢ*:

```
overrun   O  = C − F              (nothing to do if O ≤ 0)
pressure  pᵢ = (cᵢ / C) · O
```

Again by share, so `Σ pᵢ = O`. Projects that consumed no float receive none,
which falls out of the formula rather than needing a special case. The earlier
draft gave every feeding project the full overrun, so four feeders on a three-day
overrun produced twelve days of pressure.

**`cᵢ` is measured along a path, not summed over tasks.** §9 computes
`float_consumed` per task, and tasks on one path share the same float — adding
them up double-counts the same slack and inflates `C`, which inflates every `pᵢ`.
Take `cᵢ` as the consumption along the project's longest path into the gate,
which is exactly what the CPM step produces. This is the within-project instance
of the simplification this section opens with, and it carries the same label in
the UI.

Guards: `C = 0` with `F ≥ 0` is already caught by `O ≤ 0`. `C = 0` with `F < 0`
is not — it means the baseline was infeasible at that gate before anyone consumed
anything, which is a finding in its own right and must be surfaced rather than
divided by zero.

### The missing traversal

Both channels produce an effect on the tasks a contended person or gate touches.
Turning that into project or milestone impact requires knowing whether those
tasks are on the critical path. If they are not, the shortfall consumes float and
the milestone does not move at all. §9 computes per-task `schedule_slip` and this
section computes float, but nothing in the document traverses the dependency
network between them.

For a product whose headline output is a cross-project impact chain, that is a
structural gap, and the error it produces is the same class §10 already corrects:
plausible magnitude, wrong target. A forward-pass CPM over the schedule's
predecessor edges is not a large piece of work, and it is its own step ahead of
the first signal rather than a preamble to the analysis step (§12, step 5).

### Convergence

Damping exists to stop oscillation, not to shrink the answer. **Apply it to the
context update, not to the magnitude:**

```
ctx(k+1) = (1 − λ) · ctx(k) + λ · target(k)
```

Scaling each induced magnitude by λ and then replacing the context wholesale
converges to λ × the true value — a systematic 40% understatement at λ = 0.6.
Under-relaxation as written above converges to the true fixed point; λ only
controls how fast and how smoothly it gets there.

Remaining decisions:

- **Iteration cap.** Three passes re-run every project's analysis three times.
  The residual after three passes depends on the feedback gain, which is a
  property of the portfolio and not known in advance — so measure it rather than
  quote a figure. I6 already requires reporting it; that is the number to trust.
- **Report the residual (I6).** A result that stopped four days short of
  convergence must not render identically to one that converged. Attach the final
  delta to the result object and show it.
- **Detect reinforcing loops every iteration**, not only when the cap is hit. A
  cycle that converges is still a finding worth surfacing.
- **Window alignment.** Projects plan at different granularities. State one rule
  — pro-rata by working-day overlap — and apply it when attributing demand to
  capacity windows.

Every cross-project effect is stored as an impact edge citing the fact versions
on both ends and carrying a magnitude. Edges below the materiality threshold do
not render.

---

## 11. Tiles, retrieval, evaluation

### Tile specs

Natural language produces a **spec**, not an answer. A deterministic executor
runs the spec. The model's entire job is `text → TileSpec`: a metric from a
closed list, a scope, a window, grouping, filters, a visualization type.

Why the extra layer earns its place:

- **Refresh without re-prompting.** New document lands, data version changes,
  tile recomputes. No model call.
- **Shareable and persistable.** A tile is an object on the canvas with an id,
  not a frozen chat answer.
- **Debuggable.** A wrong tile shows a wrong spec, and the user corrects the
  spec directly.
- **Safe.** The model never emits SQL. Query builders are hand-written and
  parameterized; the spec only selects among them.

Cache on `(program_id, spec, data version)`. Round-trip the spec back in words —
"effort variance for HRMS and SAIN, grouped by week, last 8 weeks" — so a misread
request is visible before anyone acts on the number.

For the narrative half, retrieve the parsed blocks behind the signals in the
tile and let the model explain over those. It reads facts and spans; it produces
no figures of its own.

### Retrieval index

Small and scoped. This is not a general document chatbot.

- Embed paragraph blocks only — prose, assumptions, status notes. Tables are
  queried through SQL, not similarity.
- **Filter to the tile's scope, not to a single project.** Hard-filtering every
  retrieval to one project makes cross-project causal chains unexplainable,
  which is the one output §10 exists to produce. The filter is the set of
  projects in scope.
- **Hybrid lexical plus vector, fused by reciprocal rank fusion.** Pure vector
  search is measurably worse on WBS ids, person names and technical terms —
  embeddings smear exact identifiers into a neighbourhood of near-matches.
  Rank fusion sidesteps the incompatible score scales and needs no per-corpus
  tuning.
- **Stock Postgres full-text search is not BM25.** Built-in ranking accounts for
  term frequency and document length but not inverse document frequency — and
  IDF is precisely what makes a rare WBS id outrank a noisy paragraph. Getting
  real BM25 means an extension; pick one and check it is supported on the
  managed host, as availability has been shifting.
- **Japanese needs a tokenizer.** The default parser does not segment Japanese,
  so 工数見積 will never match 見積 without bigram or morphological analysis.
  Decide this at the same time as the ranking, not after.
- pgvector is comfortable at this scale. The binding constraint is memory rather
  than row count, and embedding paragraph blocks only keeps the index far below
  it.

### Evaluation harness

Build it early — it is what turns prompt tuning from guessing into engineering.
Hand-labelled fixtures covering an English estimation workbook, a Japanese
schedule, and mixed-format notes, each with an expected extraction.

Track per extractor version: precision, recall, provenance validity rate,
unit-inference accuracy, cost per document.

**Use it for regression detection, not as an absolute quality gate.** Six
fixtures cannot distinguish 90% precision from 95% — on a twenty-line fixture,
a single miss moves recall by five points. The question the harness answers well
is "did this version get worse than the last one", and that is the question
worth asking every time.

---

## 12. Build order

Each step leaves something working.

| # | Step | What it buys |
|---|---|---|
| 1 | Raw storage, hashing, upload UI | re-upload detection alone is useful |
| 2 | Deterministic parsers, schedule and Jira | parsed blocks in a debug view; no AI yet, already more than most tools |
| 3 | Units module (§8) | before any number exists, so nothing has to be retrofitted |
| 4 | Canonical commit with versioning, natural keys, the person alias table, source precedence | baseline vs current; §10 is unreachable without person identity |
| 5 | Forward-pass CPM over predecessor edges | float exists, so materiality is computable before the first signal renders |
| 6 | Five signals: slip, effort variance, allocation load, scope growth, capacity gap | first real output |
| 7 | Context-passing analysis and fixpoint | cross-project contention; the first genuinely novel output |
| 8 | Tile specs wired to the canvas | replaces free-form answers |
| 9 | LLM extraction, confirmation gate and eval harness, same step | never one without the others |
| 10 | Fuzzy resolution and review queue | needed once documents return with edited names |
| 11 | Retrieval index | narrative explanation |
| 12 | Impact chains in the UI | the payoff |

Units moved ahead of canonical commit deliberately. It is the cheapest step in
the list and the most expensive one to add late.

Person identity moved into step 4 for a harder reason. Step 7 is the
differentiator and it runs entirely on shared people; with natural keys only,
"person resolvable to a resource" is an error-severity rule, so person-scoped
records sit in `needs_review` and steps 6 and 7 run starved. Either pull identity
forward, or state plainly that 6 and 7 are project-scoped until step 10.

CPM became its own step, ahead of signals rather than inside the analysis step,
because materiality (§9) is defined as magnitude ÷ remaining float. Without float
every signal in the next step carries a `materiality` column it cannot populate,
and the display threshold that keeps §13's "chain nobody believes" off the screen
has nothing to threshold against. It is also what `float_consumed` and Channel
2's `cᵢ` are measured on.

---

## 13. Failure modes to design against

**Silent unit conversion.** The most damaging class. A person-month read as a
person-day is a 20× error that looks entirely plausible in a chart. Unit is
required everywhere, never defaulted, and records without one are rejected.
Single-value magnitude checks catch only the inflating direction; cross-source
comparison is what catches the other.

**Silent truncation from an upstream API.** Changelog caps, paginated endpoints
that stop early, row limits. The result is a complete-looking number computed
from partial data. Any capped fetch must either fetch everything or mark the
derived signal as incomplete.

**Confident extraction from an ambiguous document.** Surface confidence in the
UI and exclude low-confidence records from anything that triggers an alert.

**Duplicate entities after re-upload.** Record the resolution method per row so
diagnosis takes seconds.

**A chain nobody believes.** Every impact edge cites fact versions at both ends
and carries a magnitude. Edges below materiality don't render. One wrong chain
shown to a PM costs more trust than ten correct ones earn.

**Upload order deciding truth.** Two sources describing the same entity in the
same week, no precedence rule, latest commit wins. Reproducible right up until
someone re-uploads in a different order (§7).

**Confident zeros from thin inputs.** A project with no worklogs reports an
`effort_variance` of zero, which reads as healthy. Coverage gates, not defaults.

**Numbers that don't reproduce.** Non-deterministic sort order, a config value
read at runtime, a converged-looking result that wasn't. I1 and I6 are cheap to
hold and expensive to recover.

**The model creeping into the calculation path.** Review this periodically. The
temptation to let it "just estimate" a missing number is constant, and the first
time it does, reproducibility is gone.

---

## Open decisions

| Question | Needed by | Default if unresolved |
|---|---|---|
| Person-days per person-month, per client | step 3 | 20, flagged in UI |
| Slip in working or calendar days | step 6 | working days |
| Is a trustworthy priority order available per program (§10) | step 7 | no — Mode A, proportional, nothing protected |
| Damping λ and iteration cap | step 7 | 0.6, three passes, residual shown |
| Lexical ranking extension and JP tokenizer | step 11 | blocks the retrieval step |
| Rolling window length for rework and velocity | step 6 | four weeks |
| Availability factor and concurrency penalty on supply | step 6 | 0.8 availability; penalty from 3 concurrent projects |
| Retention window and legal hold | step 1 | blocks any client contract |
| Absorption assumption shown beside delay scenarios | step 7 | overtime first, capped at 45 h/month |
| Worklog coverage threshold for suppressing effort signals | step 6 | 60% of issues |
| Scope of the confirmation gate on model-sourced records (§5) | step 9 | effort-bearing record types only |

---

## References

External claims only. Everything else in this document — the formula
corrections, the precedence table, the build order, the storage design — is
engineering judgment and carries no citation.

| Claim | Source |
|---|---|
| 36協定 ceilings: 45 h/month and 360 h/year; 特別条項 bounded by 720 h/year, under 100 h in any month including holiday work, 80 h average over 2–6 months, above 45 h at most 6 months, with penalties | 労働基準法, 2019 reform (large firms Apr 2019, SMEs Apr 2020). Summaries: biz.moneyforward.com/payroll/basic/90198/ · templex.jp/articles/36kyotei-zangyou-jikan-jougen |
| A former employee may demand erasure of retained personal data once the purpose it was collected for is exhausted, and the business is obliged to comply | 個人情報保護委員会 FAQ on 法第35条第5・6項 — ppc.go.jp/all_faq_index/faq1-q9-22/ |
| Value-adding time peaks at two concurrent assignments and drops below the single-assignment level at three; engineers spread across projects often spend 25–30% of time on value-adding work; a 64-firm survey reached the same two-project conclusion | Wheelwright & Clark, *Revolutionizing Product Development* (1992); McCollum & Sherman. Both reviewed in "Concurrent projects: how many can you handle?", *South African Journal of Industrial Engineering* — scielo.org.za/scielo.php?pid=S2224-78902015000300009 |
| PreQuip: 960 engineering months a year looked sufficient until non-project development work was counted | Wheelwright & Clark, "Creating Project Plans to Focus Product Development", *Harvard Business Review* (1992) |
| The "40% of productive time" multitasking figure is a task-switching laboratory result, not a staffing finding | Rubinstein, Meyer & Evans, *JEP: Human Perception and Performance* 27(4), 763–797 (2001); APA summary at apa.org/topics/research/multitasking |
| Where the contract is silent, float is treated as a shared project resource consumed first-come-first-served | AACE International Recommended Practice 29R-03, *Forensic Schedule Analysis* |
