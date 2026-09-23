# Jira ↔ Code Traceability — Work Log

Goal: given a codebase and a Jira backlog, recover links between them and adjudicate
**which side is wrong** — a ticket claiming work that isn't there, or code that no
ticket tracks.

Target: `pimsathon-main/` — the **CoWorkLocal** desktop app (PySide6, 144 Python files,
~45K LOC). Confirmed identical to the Jira project: `config.py:21` is
`Path.home() / ".cowork_local"`, the exact path quoted in a ticket.

---

## 2026-09-23 — process work selected by what it is, and the product switched to this run

The export changed underneath the pipeline. ProjectPulse stopped reading CoWorkLocal
from a spreadsheet and started reading it from Jira, so the same 190 work items arrive
with different columns, different status words and no `parent` nesting. Two things in
here assumed the spreadsheet's shape.

### What shipped

- `governance.py` — `MANAGEMENT_WORDS`, `is_management_type()`, `select()`,
  `acceptance_text()`. Process tickets were selected by the presence of a `Key`, which
  is a property of *that export*, not of process work. They are now selected by issue
  type, matched on words, and `select()` returns **the reason beside the rows** so the
  CLI can print why 16 of 190 were taken. When the export names no issue type at all it
  returns `(None, reason)` and the caller falls back to the keyed rows explicitly
  instead of silently.
- `adapters/tickets_tabular.py` — `column_named()`, aliases most-specific-first because
  this sheet has more than twenty headers containing the word "Type", and `repair()`
  applied on read so mojibake in a summary does not propagate into every citation.
- `cli.py` — `cmd_governance` reads every row and delegates the choice; `--process-type`
  for when the caller knows better than the heuristic.
- New tests in `tests/test_governance.py` and `tests/test_tickets_tabular.py`. 464 pass.

### The run itself

190 tickets, 151 adjudicated, 74 corroborated, **9 tickets marked `Cancelled` that are
in fact built and present in the code** — reached independently by `reconcile`
structurally and `explain` narratively, which is the difference between a finding and a
guess. Total spend $8.36; `explain` failed once mid-run on a credit balance and
succeeded later for $0.20.

### What did not change, and why that is informative

`reconcile` still reports the degenerate axis `tracker 'done' on all 63 rows`. It
survived the swap from spreadsheet to Jira because the cause is **join width** — median
30 — not the source. A defect that survives a total change of input is a defect in the
method, and this one is still open.

Also still open: 173 of the 190 tickets share the due date `2026-08-31`, which is a bulk
field-set and not a schedule, so nothing downstream should make a timing claim from this
backlog. The product says so on the page rather than quietly averaging it.

---

## 2026-09-22 (later) — the same documents read as a record of work, and a three-way reconciliation

Built to the plan in `docs/progress-adapter.md`, in its order, and the plan was
wrong in three places. Those are the interesting part.

### What shipped

- `adapters/_markdown.py` — the walker `featuremap_markdown` already had, extracted
  so both adapters share it. Behaviour-preserving by construction: same 2,723 rows,
  1,196 claims, 437 resolved before and after.
- `adapters/progress_markdown.py` — stage 0e. 63 work items (56 done, 7 open, 56
  with real start *and* end times), 30 plan rows, 6 gates, 50 register entries.
- `reconcile.py` + stage 5b — tracker vs documentation vs code.
- `features.resolve_claims()` — split out of `resolve()` so a work item's
  deliverables are checked by the same code as a feature's claims.
- 45 new tests; 240 pass.

### Where the plan was wrong

**1. The `unparsed` threshold measured the wrong thing.** The plan said
`len(unparsed) < 0.2 * records`. Measured: 68 unparsed against 143 records, 47%,
failing. But 64 of the 68 are `no-id` — rule R2 correctly refusing checklist
templates — and only 4 are the parser failing on a ragged row. Counting a deliberate
exclusion as a loss would have meant raising the threshold until it caught nothing.
The invariant now caps only the reasons that mean "could not read", and a *second*
test asserts no `no-id` line contains a token from a learned id family, which is the
one that would catch a real loss. The CLI reports the two separately for the same
reason.

**2. `Defect` was the wrong name.** The adapter detects "an id table with no
checkboxes". On this tree that is 7 real `BUG-*` rows and 43 rows of input contracts
and report tables. Knowing a register is a bug log means reading the language.
Renamed `RegisterEntry`, and `root_cause`/`prevention` became one `detail` dict
keyed by the document's own `**Label**:` — both preserved, neither interpreted.

**3. The R02 case does not reconcile to `agreed-outstanding`.** It comes out
`tracker-optimistic`: tracker=done, docs=open, code=absent. The prediction assumed
the tracker would be open, but the tracker has no concept of R02 at all, so the
tickets reaching those files are feature tickets marked `Release it`. The finding
stands and is sharper than predicted; the prediction is what changed.

### Bugs the real documents found that the fixtures did not

- **`COMMAND` matched the gap between two code spans.** In ``Khóa DTO `A`, `B` `` the
  text *between* the spans is itself backtick-delimited, so `, ` matched "a span
  containing a space" and **30 daily-plan rows were filed as quality gates** (36
  reported, 6 real). A command is now the whole span, anchored, and must carry a
  path separator, an extension or a flag.
- **`_id_in` truncated the search string before matching**, so a token could be cut
  at the window edge: `co4e_canvas` was reported as the identifier `co4e_can`.
- **An id column has to be unique.** Without that test, any column of
  digit-bearing identifiers qualifies — the Co4E split maps list
  `ui/co4e_canvas.py` on all 280 rows, `co4e` became an id family, and **829 rows of
  symbol tables were filed as defect records**. A value repeated on every row names
  the table, not the row.
- **`clean_label` strips leading numbers**, which is right for a heading and
  destructive for a date cell: `**23/08 (CN)**` became `/08 (CN)`, so a whole "when"
  column went untyped and its cells were printed as the names of the gates beside
  them.
- **Four labels of the truth table were unreachable**, found by the tests rather
  than by reading. Joining only on an item's own resolved deliverable means an item
  whose code is missing joins to nothing — and `both-sides-wrong`,
  `tracker-optimistic`, `doc-optimistic` and `agreed-outstanding` are exactly the
  rows where code is missing. Items now also join through their doc section;
  `joined_via` records which route, because a section join is area-level evidence
  and must not read as task-level.
- **`resolve_claims` was called once per feature**, rebuilding corpus-wide lookups
  2,716 times. Caught by timing the stage, not by a test.

### Rule 2 was swallowing the report — fixed the same day

I signed the previous section off with "57 `unknown` of 63, because most work items
name no code. That is rule 1 doing its job." **That explanation was wrong**, and
checking it took one query: 60 of 63 items name code, and only 3 rows are
`code=unknown`.

The real cause was rule 2, written per *document*. Every document in this set is
ungrounded, so all 56 completed tasks became `claimed-done-elsewhere`, which is not
in the label table and falls through to `unknown`. **54 of the 57 had a code state
we had already established** — 29 verifiably absent, 25 verifiably present. The
pipeline knew the answer and discarded it at the last step.

The fix is not to relax rule 2 but to apply the principle already written into
retrieval a session earlier: *strength is decided per claim, not per document.* A
task whose deliverable we looked up ourselves does not need the document to vouch
for it, so modality now decides only the cases with nothing to check.

```
before:  5 tracker-optimistic,  1 undertracked-doc, 57 unknown
after : 25 both-sides-wrong, 5 tracker-optimistic, 1 undertracked-doc,
        25 agreed, 7 unknown
```

One test had to change to make this pass — `test_an_ungrounded_document_cannot_
report_done`, written the day before, asserted exactly the behaviour being removed.
It is now two tests: a checked claim stands whatever document it came from, and an
*unverifiable* claim from an ungrounded document is still discounted. The old
assertion was not a regression to route around; it encoded the wrong rule.

**A second bug found while checking the first.** `cmd_progress` called
`resolve_claims` without `known_docs`, so a task whose deliverable is another
document — "write the ADR" → `docs/architecture/ADR-001.md` — had that counted as
code the repository is missing. Four items, every one a document that exists. Fixed
before the rule change, deliberately: otherwise the change would have promoted four
pieces of noise into confident `both-sides-wrong` findings.

The 25 `both-sides-wrong` were then checked rather than trusted. Between them they
name 32 missing files under `domain/` (7), `application/` (6), `presentation/` (5),
`infrastructure/` (3), `scripts/` (5) — none of which directories exist here. It is
the same finding the modality measurement made, now at task granularity. Residual
imprecision worth knowing: deliverable extraction takes every code span on the line,
so `PySide6` is listed as a missing deliverable of R01-T03 when it is a dependency
mentioned in passing.

### Both residual imprecisions closed, one by measurement and one by admission

**Deliverable versus mention — fixed.** The author already draws this distinction:
49 of 70 task lines use a produces-arrow, and on those lines 92 of 151 code spans
sit after it. `learn_arrow_convention` measures that per document and applies the
split only where it holds; pre-arrow spans go to a new `WorkItem.mentions` rather
than being dropped, because "this task touched `PySide6`" is true and only the word
*deliverable* was wrong.

The obvious alternative was rarity — a name claimed by many tasks is a shared
dependency — and measuring killed it: **148 of 158 claims are named by exactly one
item**, so there is no signal there at all. Worth the five minutes not to build it.

The fix moved the numbers in the honest direction, which was not the comfortable
one. `agreed` fell 25 → 14 and `both-sides-wrong` rose 25 → 36, because tasks that
previously resolved through a file they merely *read* (`providers/base.py`) now
resolve only through what they claimed to *produce* — and those are missing.

**Area-level joins — reported, not fixed, because they cannot be.** Checking the
join routes turned up something structural rather than incidental: an item joins
directly only when a deliverable resolved, and `code=absent` means none did. So
**every `absent` row is necessarily area-joined** — measured, 30 of 30 — which is
to say every `both-sides-wrong`, `tracker-optimistic`, `doc-optimistic` and
`agreed-outstanding` finding rests on area-level tracker evidence, always.

There is no stronger join available: a task claiming a file that does not exist
leaves nothing for a ticket to touch. So `Row.confidence` derives from the join
route and `reconcile` prints two blocks instead of one:

```
  DIRECT — the item's own code anchor found these tickets
      14  agreed
  AREA-LEVEL — joined through the item's doc section
      36  both-sides-wrong
       6  tracker-optimistic
       7  unknown
```

Uncomfortable but true: the strongest-sounding findings are the weakest-joined
ones, and a report that let them share a column with the direct hits was quietly
overstating itself.

### Three more directions, and the one that has to wait

Stepping back from refining the parser. The two real gaps were that **Stage 3 had
never been run with any of this** — no `verdicts.json`, zero cached responses —
and that **none of it reached the product**: ProjectPulse's traceability page
reads eleven artifacts and none of the new three were among them.

**The structured half of the docs tree** (`adapters/featuremap_json.py`). 300 KB
of records with line numbers sat unread because the adapters globbed `*.md`.
Ablated with `features --no-json`: rows 2,728 → 4,065, claims 1,193 → 1,648,
resolved 439 → 537, files reached 109 → 111 — and **median symbol precision fell
11% → 10%**, the opposite of what I predicted when proposing it. The structured
sources add breadth, not depth: 378 control rows naming one or two things each
spread coverage over more files without locating more of any one. Second time
I have been wrong about that metric in this direction.

Two fixes fell out of reading them: `ui/dashboard_tab.py:35` is a path plus a
locator, and the `:35` meant the manifest's 54 anchored rows matched nothing;
and `screens/*.png` is a sibling artifact, so the docref rule now knows every
file in the docs tree rather than only the parsed ones.

**`gates`** — evaluate the criteria the documents set, rather than read them.
Result on this repository: 0 pass, 2 fail, 4 not checkable. `sandbox_pw =
'quandh14'` is the exact credential the checklist blames for its own failing
tests, and 27 of 154 files break the team's 400-line rule. Commands are quoted,
never executed: they name scripts this repository does not contain, and running
a command out of a document is executing a file nobody here wrote.

The first version answered three gates that had not been asked, all by reading
operands out of text that was not the criterion — the *title* supplied
`Checkpoint` as a module name and produced a confident pass for "0 imports of
Checkpoint under tests/"; the *command* supplied `tests/` and `scripts/` as the
directories a rule was about. And `pass` matched as a credential word, so "five
scenarios pass" ran a secrets audit, while scanning whole lines instead of
key-value pairs reported **73** credentials, most of them words in comments.
Three, once tied to an assignment with comments stripped.

**The page** (ProjectPulse commit `eaf8f86`). A Delivery view over
`progress`/`reconciliation`/`gates`, with the direct/area split and the
unchecked-is-not-pass distinction carried through rather than flattened. The
block is absent and reports no gap for a project with no docs tree.

Wiring it turned up a latent bug in that repo worth recording here because this
pipeline's output triggers it: its snapshot README says adding a second run
directory is enough to make it appear in the picker. It is not —
`run_for_project` returns the first match in sorted order, so a second directory
declaring the same project id silently shadows the first, and whatever it lacks
disappears. Adding our run as `cowork-docs` would have hidden the demo run's
$10 of verdicts. The delivery artifacts were regenerated against the demo run's
own candidates instead, because `reconcile` joins through the candidate sets and
mixing runs reports agreement nobody measured.

### `effort` — the first real durations in the dataset

56 of 63 tasks carry both a start and an end, and the tracker has none at all.
Median 10 minutes, quartiles 5-150, 11 under five minutes — including R01-T01,
"write the Architecture ADR", at one minute.

The module reports that distribution and refuses to judge it: nothing here can
separate a fast AI-assisted team from a backfilled sheet, and a stage that
called a one-minute task a lie would be asserting something it cannot know. One
claim it does make, because it is about the record and not about anybody's day:
**two intervals cannot overlap for one owner.** Four tasks do, so at least one
of each pair is an estimate. Unowned tasks are never compared.

Calendar and slip come free with the timestamps: 21-28 August against a plan
through the 31st, nothing on the 24th, and 18 plan rows starting on a different
day than planned — one six days early, one three days late.

**The sharper half: what finished tasks say they produced.** 91 outputs of
completed tasks do not exist, split by kind. Five of them are test files —
`tests/e2e/test_smoke.py`, `tests/characterization/test_run_cowork.py` and
three more — against a Definition of Done that requires passing tests and a
checklist quoting "377 pass / 4 fail". Only completed tasks count: an open
task's missing output is the plan.

Building it turned up a bug older than any of this work. `corpus.TEST_HINTS`
held `test` but not **`tests`**, so `tests/fakes/fake_provider.py` was
classified as production source. Two files moved role once fixed, and the size
gate now reports production and test counts separately rather than quietly
picking which reading of "production" the criterion meant.

### Verification pass

Both suites green — 314 tracelink, 975 ProjectPulse. Beyond the unit tests:
a clean-room run of all twelve free stages (only `couple` declines, correctly,
for want of verdicts), twelve cross-stage invariants over the resulting
artifacts, every artifact type surviving save/load/rebuild, the schema guard
refusing a mismatched stage name, both ablations, and ProjectPulse reading the
run with no gaps and its verdicts intact. `tests/test_integration.py` makes the
invariant half permanent and hermetic.

## 2026-09-22 — Stage 3 finally ran, and the pipeline found what it was built for

$9.26, 173 verdicts, `claude-opus-5`. The dry run said $7.42; the estimator
guesses four characters per token and the doc-feature block is denser than
that, so it under-predicts by about a quarter. Worth fixing before anybody
budgets from it.

**132 of 173 verdicts (76%) saw the doc-feature block** — the describer mix is
`doc-feature+existing-docs+raw-source` 75, `doc-feature+raw-source` 57,
`existing-docs+raw-source` 18, `raw-source` 20, none 3. Everything built over
these sessions reached the model.

```
corroborated  76 (43.9%)   contradicted 2 (1.2%)   unverified 95 (54.9%)
confidence:   high 66   medium 103   low 4
```

**Two contradictions, both marked `Release it`:**

- *Task Scheduling — Holiday calendar*: the only candidate file says Cron and
  Repeat scheduling were removed, and there is no holiday, working-day or
  country field anywhere.
- *Integrity Sandbox — File integrity verification*: `integrity_sandbox.py` is
  shown in full and implements a Low-Integrity-token process sandbox. The name
  matches the ticket; the feature does not.

**Four status conflicts**, all corroborated with a citation that checks out:
TLS Trust and Outlook Notify are `In Progress` and complete; File Edit Dialog
and the Agent/Skill standardisation are `To Do` and built.

### `verify` caught a failure mode our own change introduced

539 citation checks: 428 `defined`, 99 `no-symbol` (a call site rather than a
definition — legitimate), 7 `present`, 1 `absent`, and **4 `no-file`, every one
of which names a markdown file**: `architecture/security-policy.md`,
`refactor/plan.md`, `architecture/co4e-split-map-node-property.md`. Showing the
adjudicator the team's documents taught it to cite a document as though it were
source. Three of the four sit under `unverified`, so the harm is small here,
but the mechanism is not small and it now has its own row in the digest.

### The product surface

Five honest views and no answer to "what do I look at first". `_findings()`
assembles one ranked digest across every stage — 54 rows on this run:

```
contradicted 2 · status-conflict 4 · gate-failing 2 · missing-test 5
documented-not-built 35 · unsupported-citation 5 · unclaimed-code 1
```

Ranked by what it costs to be wrong times how well evidenced it is, so a
deterministic check over the corpus outranks a model verdict and a verdict
with a citation that checks out outranks one without. **Volume earns nothing**:
35 area-level rows sit below 2 contradictions, because a reader who starts
with the 35 never reaches the 2. Every row carries where to look and how
strong its evidence is, because those strengths genuinely differ and
flattening them is the one thing this pipeline keeps refusing to do.

One bug found while wiring it: `_delivery` truncated its reconciliation list to
25 before the digest read it, so the digest under-reported 35 as 25. The cap
belongs in the drawing, not the data.

### Still open

- Hand labels. Still none, so none of the above is measured accuracy — the two
  contradictions read well and that is not the same thing.
- `explain` has not been run (paid).
- The doc side is still untranslated.
- The cost estimator under-predicts by ~25% on doc-heavy prompts.

---

### Earlier: still open `adjudicate --dry-run --all`
  estimates $7.33; there is no API key in this environment. Everything else is
  scaffolding for a question that has not yet been asked.
- Still no hand labels, so none of this is accuracy.
- The doc side is still untranslated.
- The doc side is still untranslated (see the previous entry).
- Still no hand labels, so none of this is accuracy.

---

## 2026-09-22 — pinning sections to line ranges, and two bugs that made a map look complete

`features` could say "this claim resolves". It could not say *this area of the
document is about these lines of this file*, which is the thing a person actually
wants and the thing that makes a link checkable by opening two files side by side.

### Symbols now carry line spans

`Symbol` gained `line`/`end_line`. Both analysers already had them and neither was
asked: CodeWiki's `Node` carries `start_line`/`end_line`, and the AST fallback has
`lineno`/`end_lineno`. Without them a trace bottoms out at `ui/chat_panel.py`, which
at 1,738 lines is a direction rather than a location. A corpus built before this
degrades to file level rather than disappearing, and there is a test for that.

### `docmap.py` and `tracelink map`

Aggregates claims up to the unit a person wrote (the deepest heading with rows
under it) and down to the unit a person reads (a merged line range), in both
directions:

```
$ tracelink map --section "Token Usage"
function_list.md:11   [ungrounded]
  … / 1. DASHBOARD / Token Usage & Cost — Thống Kê Token & Chi Phí
  10 rows, 8/10 claims resolved
    ui/dashboard_tab.py:212-306   _apply_budget, _chart_next, _chart_prev, +3
    ui/dashboard_tab.py:359-421   _ai_analyze, _apply_saving_strategy
    ui/monitoring_tab.py:826-850  _apply_budget, _refresh_budget
```

Pure arithmetic over `features.json` and `corpus.json` — no model, no embedding, no
keyword matching. The last line is the sort of thing it is for: the *Dashboard*
section also reaches into `monitoring_tab.py`, which carries its own duplicate
`_apply_budget`/`_refresh_budget`. `--file` runs it the other way; `--undocumented`
lists the 65 of 154 source files no section reaches at all, led by
`ui/accounts_tab.py` at 659 symbol lines.

### Two bugs that made the first version worthless

**`__init__` resolved to 166 constructors.** The resolver indexes leaf names, so a
document writing `` `__init__()` `` matched the constructor of every class in the
repository — and a report in which the StepConfigPanel refactor note "documents"
`ui/dashboard_tab.py:39-179`. 103 of 154 files looked documented. A claim resolving
to more than `max_pin_targets` symbols now still counts as resolved (the name *is* in
the code) but may not point at a line, and is counted as `ambiguous` so the reason
shows. 15 claims hit that bar; documented files fell to 89.

**Coverage by lines is not coverage.** A section writing `ChatPanel` covers lines
69-1738 — the whole file — while locating one of its 71 methods, and the first
report called `ui/dashboard_tab.py` "100% documented" on exactly that basis. Both
numbers are now printed: the median documented file has **12% of its symbols named
individually and 14% of its lines inside some section**. The default ranking changed
from lines covered to symbols located for the same reason.

### A gap the tests found

`Billing`, `Config`, `Session` — a one-word class name has no internal capital, so
the claim filter read it as an English word and dropped it. Accepting such spans
outright would have filled `drift` with `Notes` and `Function`. They are now
provisional (`symbol?`) and kept only if the corpus turns out to contain one, so
they can never become drift. Sections pinning at least one line went 99 → 192.

195 tests pass.

---

## 2026-09-21 — the team's own docs replace the generated ones, and turn out to describe a different build

`hackathon/docs/` — 39 markdown documents, an ADR set, governance policies, the
Vietnamese refactor reports, a 472-line nav-bar feature inventory, 44 screenshots —
is documentation for this exact product, written by the team. It had been sitting
inside the ProjectPulse repo, untracked, and the pipeline had never seen it.

Swapping it in for the $12 of generated CodeWiki pages was the intent. What the
swap actually produced was a finding.

### New stage: `features`

`adapters/featuremap_markdown.py` + `features.py` read a markdown tree **recursively**
into feature rows — heading path, label, the sentence a person wrote, and the symbols
and files that row names — then look up every name in the corpus. 39 documents →
**2,716 feature rows, 1,193 distinct code claims, 434 present (36%)**, reaching 108 of
172 files.

`describe.ExistingDocs` globbed one level, so on this tree it would have seen **3
files out of 39** and reported "no documentation" about a project with a hundred
pages of it. Fixed to `rglob`.

### The finding: 1 grounded document, 33 ungrounded, 5 process

The docs are **ahead of the code they ship with**. Two independent proofs:

- `architecture/` specifies a 4-tier layout — `presentation/`, `application/`,
  `infrastructure/`, `domain/`. `pimsathon-main` has `ui/`, `core/`, `providers/`,
  `security/`. ADR-001 is marked "Status: ACCEPTED / ENFORCED" and dated 2026-08-21;
  `co4e-split-map.json` assigns 363 symbols to files under those directories, none of
  which exist.
- `function_list.md` resolves 96 of 223 symbols, and the misses are *systematic*: the
  doc says `_submit_message()`, the code has `submit()`; `_build_job()` vs
  `build_job()`; `_refresh_cards()` vs `_refresh_budget()`. `_build_kanban`,
  `_build_calendar`, `_ai_create_task` have **zero occurrences** in the repository,
  not even a call site.

So `hackathon/docs` and `pimsathon-main` are two different snapshots. Using the docs
as ground truth would have reported a far more complete backlog than exists.

### Why modality had to be measured rather than declared

Shown to the adjudicator under "Architecture documentation excerpt", a refactor plan
is an argument that the feature is already built — confident, present tense, written
by the code owners. The verdict comes back corroborated and the citation is *real*, so
`verify` passes: the symbol exists, just in a document rather than in the repository.
Nothing downstream can catch it. The only place to stop it is before the prompt.

Three things had to be true for the measurement to work:

1. **Symbols and paths counted apart.** The split maps resolve 127/233 symbols and
   2/10 paths — they know today's code and file it under directories that do not
   exist. One averaged rate calls them grounded. Naming absent files is now
   disqualifying on its own.
2. **`ungrounded` must not claim to mean "a plan".** Resolution cannot separate a
   design from an accurate description of a different build. A second measurement
   usually can: near-matches among the misses. Cutoff 0.92, set from the data — at
   0.86 the arrivals were `nav_project` ~ `save_project` (0.870) and `run_node` ~
   `run_code` (0.875); real renames score 0.97+.
3. **Nothing classified by name.** Renaming the plan `current-implementation.md`
   leaves it ungrounded, and there is a test asserting exactly that.

### `doc`, the fourth matcher — measured, then nearly deleted

Ticket → documented feature row → the code that row names. `retrieve --no-docs` is
the ablation:

| | `--no-docs` | with docs |
|---|---:|---:|
| strong candidate set | 158 (91.3%) | **167 (96.5%)** |
| nothing at all | 7 | **3** |
| distinct candidate sets | 143 | **157** |
| median set size | 5 | 6 |

Fires for 132 tickets, sole strong witness for 9.

The first two versions were worthless and it took measuring to see it. At the
file-side rarity ceiling of 8 the matcher fired for **8 tickets of 173** — a token had
to appear in ≤8 of 2,716 rows. Raising it to the fraction (55) got 57 tickets and
moved coverage by 2. What actually made the stage pay was dropping the
two-shared-words bar when the shared word *is* the row's own function name: a one-line
table row rarely has two rare words, and "refund" against a row whose claim is
`refund()` is precisely the match worth making.

**I was wrong about why this mattered.** The pitch was that the doc matcher would fix
title-head collision — six `Dashboard — …` tickets collapsing onto one
`dashboard_tab.py`. Measured: the baseline already leaves **1 of 36** head-sharing
tickets colliding, because tail-tokenisation fixed it in the previous session. The
README's "Known limitations" describes the *demo*, not `tracelink`. Corrected in the
docstring rather than quietly dropped.

### Smaller things the run surfaced

- **Resolved *path* claims printed nothing.** `DocFeature` read `claim.sids`, which
  path claims do not have, so a row whose every claim was a filename arrived as a
  heading with an empty checklist under it.
- **Doc-to-doc cross-references counted as missing code.** `drift` reported
  `GammaTeam_decisions.md` as a capability the repository fails to implement. Path
  claims that name another document in the tree are now `docref` and excluded from
  claim counts, drift, and the resolution rate modality is decided on.
- **`per_matcher` had a hardcoded triple**, so the new matcher became the largest
  contributor in the run and the contribution table did not mention it.
- **The CLI died on Vietnamese output.** `drift` finished its work, started printing,
  and raised `UnicodeEncodeError` on `ầ` against a cp1252 console — after the useful
  output had scrolled past. stdout is now forced to UTF-8 with `errors="replace"`.

### Cost

The `features` stage is free and replaces the **$12** CodeWiki line item, which had
been the largest single cost in the run while changing roughly one verdict in twelve.
2,716 feature rows against 20 generated pages, and each row names the function the
team says implements it — which is what makes it checkable at all.

### Open, and deliberately not done

- **The doc side is not translated.** `translate` handles tickets; feature rows stay
  as written. The `doc` matcher is carried by bilingual headings and English symbol
  names; rows whose only discriminating words are Vietnamese prose are unreachable
  from an English ticket. It is a paid stage, so it needs an ablation, not a hunch.
- **Modality is per document, not per section.** The split maps are the honest case
  against that: their rows are individually classifiable and are not classified
  individually.
- **Still no hand labels**, so none of the above is accuracy.

---

## 2026-09-20 — rebuilt as `tracelink`, and the demo became a probe

The scripts proved the idea on one export and one repo. This session turned them
into a system meant for the *next* pair, and everything below was found by running
it rather than by reading it.

### The pipeline now

```
0a tickets     tabular export -> tickets           free   tickets.json
0b diagnose    what structure does this export have? free  diagnosis.json
0c corpus      repo -> files, symbols, import edges free   corpus.json
0d translate   non-English tickets -> English        $     translations.json
1  retrieve    tickets -> candidate files           free   candidates.json
2  describe    candidates -> anchored excerpts      free   (in memory)
3  adjudicate  ticket + code -> 3-valued verdict     $     verdicts.json
3b verify      every citation, against the source   free   grounding.json
4  shadow      code no ticket accounts for       free/$    shadow.json
5  explain     each feature area, in a PM's words    $     explain.json
6  couple      which tickets depend on which        free   links.json
   synth       generate a backlog + repo with known answers   free
```

Four stages can spend money (`translate`, `adjudicate`, `explain`, `shadow
--describe`); the rest never do. Every response is cached by content, so a
re-run after a config change is free and needs no credentials — 169 of 173
verdicts were served that way with no key set.

### What the demo export actually contains

The earlier reading of it was wrong in a way that mattered. The sheet is not 173
tickets under 17 parents:

- rows 4-139 are **16 `PM Task` rows**, all `[Planning Task] ...` — project ceremony
- row 148 is **`COWORKLOCAL-1`, type `Product`, summary "Management"**
- rows 149-329 are **173 unkeyed `Task` rows** — the whole product backlog, under that one issue

So the features have **no Jira keys**, and cannot be assigned, linked or transitioned
individually. `tracelink diagnose` reports the rest: only **8 of 421 columns** carry
anything; every relationship field is empty; `Planned Start` equals `Created` to the
minute on all 16 planning rows, so the schedule's left edge is a creation timestamp
rather than a plan; and `PO/BA/Developer` live inside the description text, where
147 of 173 rows name the same person for all three.

### Results on it

173 tickets. **73 corroborated, 1 contradicted, 99 unverified, 4 status
conflicts** — TLS Trust and Outlook Notify marked `In Progress` while complete,
File Edit Dialog and an Agent/Skill ticket marked `To Do` while built.

**Zero of 492 citations were fabricated.** `verify` checks every `file::symbol` a
verdict names against the source; 401 are definitions, 12 are call sites, none are
absent. That is the only claim on the page that was checked rather than produced.

### Bugs the work found, each with the measurement that found it

| bug | how it showed up |
|---|---|
| ticket text was lowercased before matching, so `InvoiceEngine` never matched the class `InvoiceEngine` | synthetic `symbol` tier scored **0/12**; after the fix, 12/12 and F1 .545 -> .769 |
| `--uid`/`--limit` runs overwrote `verdicts.json` with their own slice | 173 verdicts became 24 |
| a run where every call failed reported `0 api calls, $0.0000` | identical to a perfect cache run; now counted, printed to stderr, non-zero exit |
| credentials were required before the cache was consulted | a fully-cached re-run was impossible; now 169/173 served free with no key |
| Jira's `Rank` counted as a dependency field | would have hidden "no dependencies recorded" on every Jira export ever produced |
| `Detection (D) Ranking` matched the `rank` field by substring | whole-phrase matching only |
| a header matching 2 aliases but no title column returned 0 rows and blamed `--project` | now names the missing field and the headers it saw |
| `^\s*class X` anchored at a preceding blank line | cited line numbers pointed above the definition |
| four classes of false "hallucinated citation" | constants, qualified methods, artifact-node ids, prose headings — all real code |
| grounded count included `unverified` verdicts | read "73 are backed by code (151 verified)" |

### Generality, measured rather than claimed

Run on three other inputs this session: **ProjectPulse** (290 files, python + typescript
+ javascript), a **plan workbook** with a `Task ID | Activity | Phase` header, and an
older export of the same project. A design spec was correctly **refused**. The
diagnostic reached opposite conclusions on two backlogs — one with no dependencies,
one with `Predecessor` populated — which is the point.

Still English-only in its column aliases, stopwords and field vocabulary. A
non-English *header* fails loudly; non-English *tickets* are translated (24 here, $0.11,
Vietnamese retrieval 75% -> 88%, and it surfaced the 4th status conflict).

### Ground truth

`tracelink synth` builds a repo and a backlog from one plan, so the mapping is known by
construction: 46 tickets over 13 files across six difficulty tiers. First real score —
**precision .833, recall .714, F1 .769**, all 4 decoys correctly refused, and the score
is now a test that fails on regression.

It measures the matcher against difficulties we thought to build in. **Hand labels on
the real backlog are still zero**, and remain the only evidence about reality.

### Where it is surfaced

ProjectPulse's `/traceability` page, scoped by project id like every other
screen, plus a panel under the Schedule chart. Both read the run directory as
data; neither imports `tracelink`.

**Shipped to production 2026-09-20.** `projectpulse.fly.dev/traceability`,
with the findings, not empty. The run snapshots live in
`projectpulse/traceability_runs/` and are copied into the image, deliberately
*not* under `data/`: everything there is rebuilt in-container by
`scripts.replay` and these cannot be — producing one costs real money and
needs an API key the container does not have. `index.json` and the response
cache are left out, so 1.8 MB ships rather than 3.1 MB.

`TRACELINK_RUNS=/app/traceability_runs` in `fly.toml`. A project with no run
there reads "Not traced" rather than erroring, which is what makes shipping
this safe on a host that has never seen the pipeline.

**CoWork Local was missing from the production portfolio** — it only ever
existed in a local database — so `data/coworklocal_schedule.xlsx` (the 17
keyed Jira rows, already in schedule shape) went in through
`/api/sources/upload`: 17 rows, 0 rejected. `slugify("CoWork Local")` gives
`excel:Project:upload:cowork-local`, which is exactly the id the run declares,
so the project, its schedule and its traceability all resolve to one thing
rather than two. Live: portfolio 17 tasks, Gantt 17 rows (16 dated), rollup
173 features / 73 in code / 73 grounded / 4 conflicts.

⚠️ **Production is ahead of GitHub.** Both commits are local: `git push` was
refused by the environment's permission classifier. `fly deploy` ships the
working tree rather than a git ref, so the app and the repositories have
drifted — the same trap CLAUDE.md records from 2026-09-12. Push both repos
before anything else touches them.

### Cost, counted properly

`$10.75` of pipeline on top of `$12` of CodeWiki architecture docs — **$22.75
all in**, and the docs are the largest single line item while changing roughly
one verdict in twelve. That figure did not exist before this session because
two things hid it: external spend was never recorded anywhere, and a cached
re-run overwrote each verdict's `cost_usd` with the zero it cost *that* time,
so the ledger drifted toward zero the more the pipeline was used. The cache
now stores token counts, so the recorded cost survives a re-run.

---

## Pipeline  *(superseded — kept for the reasoning, not the facts)*

> Everything below this line predates the 2026-09-20 rebuild and is left as the
> record of how the thinking got here. Where it disagrees with the entry above,
> the entry above measured it. Specifically: Stage 4 **is** built, the export's
> shape is not 173-under-17, and the "clustering drops 36% of files" finding was
> wrong — see the struck-through note under Stage 2.

```
Stage 0  normalise        xlsx -> 173 tickets ; repo -> role-classified file index
Stage 1  retrieve         keyword/IDF matching -> ~3 candidate files per ticket   [FREE]
Stage 2  describe         CodeWiki -> capability statements with file::symbol anchors
Stage 3  adjudicate       LLM reads ticket + candidates -> 3-valued verdict
Stage 4  reverse pass     code capabilities with no ticket = shadow scope         [NOT BUILT]
```

### Verdict model

Proving a claim TRUE and proving it FALSE are **not symmetric**. Finding the code proves
implementation; not finding it proves nothing, because features hide under different
names (`Console` is implemented in `ui/terminal_panel.py`). Hence three verdicts, not two:

| Ticket says | Code shows | Verdict | Confidence |
|---|---|---|---|
| `Release it` | found | corroborated | high |
| `To Do` | found | **Jira stale** — status conflict | **high** (existence is provable) |
| `Release it` | not found | **unverified** — needs a human | low, always |
| *(none)* | found | shadow scope | medium |

`corroborated` means *"code exists that plausibly implements this"*, **not** *"this feature
works"*. CodeWiki reads code, it does not run it — a polished but unwired or buggy
function documents as working.

---

## Findings

### Stage 1 — retrieval (built, free)

| | |
|---|---:|
| tickets with a strong candidate set | **143 / 173 (82.7%)** |
| median candidate-set size | **3 files** (of 148) |
| English vs Vietnamese anchor rate | 75.8% vs 45.8% |

Three traps, each of which silently inflated the score before being fixed:

1. **Hub documents.** `assets/RULEBASE.md` (27 KB) matched 134/173 tickets. A catch-all doc
   mentions everything and therefore discriminates nothing — excluded by role.
2. **Rarity is relative to corpus size.** A `df <= 4` bar against 13 prose files means
   "appears in 31% of the corpus".
3. **Mixed token sources.** Folding 1,697 function names into the filename index inflated
   document frequency and pushed good stem matches below the rarity bar. Index separately.

An unfixed run reported **94.2%**; the honest figure after fixes is **82.7%**. Coverage
metrics on this kind of matcher inflate easily and quietly.

### Stage 2 — CodeWiki (partial, stopped on cost)

23 of ~60 docs generated for **~$12** (~$0.52/doc) before stopping. Two tiers:

| Tier | n | size | detail density | verdict |
|---|---:|---|---:|---|
| cluster (LLM-invented grouping of 10–54 files) | 12 | ~5 KB | **196 B/file** | thin — names packages, not files |
| deep sub-module | 11 | 9–21 KB | **4,850 B/file** | **genuinely good** |

Deep docs carry `file.py::Symbol` anchors, sequence diagrams with real call chains and
error branches, and capability bullets in exactly the requested form.

**CodeWiki reports incompleteness rather than papering over it** (3 confirmed cases):
a client-side UI lock correctly distinguished from real authentication; a `not-yet-active`
backend flagged; `RULEforCode.md` identified as an empty placeholder leaving the Code
agent unprotected. It catches *declared* incompleteness because the code says so — it
cannot catch code that looks complete but does not work.

**Hard limit — and the earlier diagnosis of it was wrong.** ~~Clustering silently drops
52 of 144 files (36%).~~ Re-measured by re-running CodeWiki's layers 1–2 locally (free,
no API key): **clustering drops nothing — all 167 leaf nodes reach `module_tree.json`.**
The loss happens one layer earlier, in leaf *selection*:

```
n_oop=166  n_func=631  ratio=0.208     OOP_MINORITY_RATIO = 0.2
```

`leaf_selection.py` admits free functions as leaf candidates only when classes are a
small minority. CoWorkLocal missed that bar by **0.008**, so every function-only module
became invisible to the doc tree — including `core/task_executors.py` (15 functions, 0
classes) and `core/chat_agent.py` (8 functions, 0 classes). Both are in the dependency
graph; they simply never became leaves. A deterministic local constant, not an LLM whim.

The first measurement was wrong because `module_tree.json` mixes `/` and `\` separators
and a naive comparison silently missed most matches. Same root cause as the
`Skipping invalid leaf node` warnings in `codewiki-run3.log`:
`read_code_components.py` does an exact dict lookup while the model writes forward
slashes, so on Windows sub-module component reads return "not found".

**What we actually bought for the ~$12:** 22 docs = 12 thin cluster docs + 10 deep
sub-docs, and all 10 deep ones sit in the three subsystems with the *least* ticket
demand. The run went depth-first. `UI_Workspace_&_Conversational_Interface` (41 tickets)
and `UI_Administration_&_Management_Panels` (28 tickets) have only the 196 B/file
cluster doc. Coverage: 44 tickets fully covered, 73 partial, 26 with nothing.

**Demand-driven generation needs no fork.** `documentation_generator.py:403` loads
`first_module_tree.json` verbatim when it exists and skips clustering entirely, and any
module whose `.md` already exists short-circuits at zero cost. Writing that file
ourselves selects exactly which docs get generated. (`--focus` does *not* do this — it
only appends "pay special attention to…" to the prompt.)

### Stage 3 — adjudication (PoC, 12 tickets, $0.31)

Extrapolates to **~$4.50 for all 173**.

- Found a real status conflict: `File Edit Dialog` is `To Do` in Jira but
  `ui/file_edit_dialog.py::FileEditDialog` is fully implemented.
- **Did not confabulate**: all three "shipped, no evidence" traps returned
  `unverified`/low rather than inventing a verdict.
- **Caught a Stage 1 retrieval error**: handed `core/telemetry_shared.py` for a ticket
  about `AppContext` (bad keyword match on *"shared"*), it rejected the file as unrelated
  instead of rationalising a link.

**Doc ablation** (same 12 tickets, doc excerpts withheld): 9/12 verdicts identical,
2 differed only in confidence, **1 differed materially** — and it was the status-conflict
finding, which degraded to a plain `contradicted` with the conflict flag lost. Docs are
worth having, but their value is concentrated, not uniform.

---

## Decisions

- **LiSSA dropped from the pipeline.** Stage 1 replicates its retrieval step in `grep` and
  targets better, exploiting the Jira `Component` field and the compound-filename
  convention. Keep as a published baseline for comparison, not as infrastructure.
- **Doc generation stopped at 23.** The remaining ~$19 would buy 21% more ticket coverage
  and still miss 52 unclusterable files. Demand should be driven by tickets, not by
  completing a tree.
- **Next step: run Stage 3 across all 173 tickets on the docs already owned (~$4.50).**

---

## Environment notes (Windows)

Reproducing this on Windows hits four blockers, none documented upstream:

1. **Subscription mode is impossible.** `caw` imports `fcntl`, then `pty`/`termios`. The
   `fcntl` use is one advisory lock and is shimmable; the PTY is not. Use an API key.
2. **`PYTHONUTF8=1` is mandatory** — CodeWiki prints `✓` and crashes on a cp1252 console.
3. **`--include "*.py"` is unusable.** The glob is expanded before CodeWiki sees it, in
   Git Bash *and* PowerShell, via `python -m` *and* the `.exe`. Omit the flag.
4. **Base URL needs `/v1`.** CodeWiki drives Anthropic through the OpenAI-compatible
   client, so `https://api.anthropic.com` becomes `/chat/completions` → 404. Verified:
   `/v1/chat/completions` → 200.

Also: re-running into a populated output dir prompts interactively and aborts under a
background shell — pipe `echo y |`.

---

## Files

| Path | What |
|---|---|
| `extract_tickets.py` | pulls the 173 feature tickets (unkeyed sub-rows) from the xlsx |
| `evidence_index.py` | role-classified file index + IDF inverted indexes |
| `stage1_match.py` | three matchers (stem / sym / prose) → candidate sets |
| `stage3_adjudicate.py` | LLM adjudication, `--no-docs` runs the ablation |
| `codewiki-docs/` | the 23 generated docs + `module_tree.json` |
| `stage1_results.json` | per-ticket candidates with evidence and document frequency |
| `stage3_results.json` / `stage3_nodocs.json` | PoC verdicts, with and without docs |

## Open risks

- **No ground truth.** Verdicts look right on inspection; that is not measured accuracy.
  Hand-label ~20 tickets before quoting precision/recall anywhere.
- **`unverified` will be the majority verdict**, especially for the 28 tickets with no doc
  coverage and the Vietnamese ones. Honest, but thin to demo — lead with status conflicts.
- **Vietnamese tickets** should be translated at Stage 0; cross-lingual matching degrades
  at every stage.
