# tracelink — backlog ↔ code traceability

Given a backlog export and a code repository, recover which files implement which
tickets, then adjudicate **which side is wrong**: a ticket claiming work that isn't
there, or code no ticket tracks.

This is the generalised rebuild of the `stage1_match.py` / `stage3_adjudicate.py`
scripts. Those proved the idea on one Jira export and one Python repo; everything
here is structured so the *next* pair works too.

## Run

Free first, always. Nothing costs money until you ask it to, and every paid
call is cached by content, so re-running after a change pays only for what
actually changed.

### One command, for a project you have just been handed

```bash
python -m tracelink --run runs/acme pipeline \
    --export ../backlog.xlsx \
    --repo ../acme \
    --docs ../acme/docs \
    --project Acme \
    --project-id excel:Project:upload:acme \
    --done-status "Release it" \
    --into ../projectpulse/traceability_runs/acme
```

Every free stage, in dependency order, then the artifacts the product page
reads copied where it will find them. `--dry-run` prints the plan and runs
nothing.

It **never spends money**. The paid stages are named at the end with the
command for each, because a pipeline that bills you for asking it to read a
repository is a trap. A run with no paid stages still produces a readable
page: 43 findings on this project, with each absent artifact named on the
page together with the command that fills it.

A missing input is a gap, not a crash. No `--docs` and the documentation
half is skipped and said to be skipped; no `--done-status` and `reconcile`
is skipped, because which tracker status means *finished* is a decision and
this pipeline will not guess it.

### The stages on their own

```bash
R="--run runs/demo"

# --- free: read both sides, and look at what you have ------------------
python -m tracelink $R diagnose ../export.xlsx     # what structure does this export have?
python -m tracelink $R tickets  ../export.xlsx --project MyProject                                 --project-id excel:Project:upload:mine
python -m tracelink $R corpus   ../my-repo         # files, symbols, import edges
python -m tracelink $R features --docs ../my-repo/docs   # the team's own docs, claim by claim
python -m tracelink $R progress --docs ../my-repo/docs   # ...and what they say about the work
python -m tracelink $R retrieve                    # candidate files per ticket

# --- free: find out whether retrieval is any good ----------------------
python -m tracelink --run runs/synth synth --out synth/   # a backlog whose answers are known
python -m tracelink $R label -n 25 --labeller you         # hand labels on the real thing
python -m tracelink $R score --save-baseline runs/demo/baseline.json

# --- paid: estimate first, every time ----------------------------------
python -m tracelink $R translate  --dry-run        # only if the backlog is not in English
python -m tracelink $R adjudicate --dry-run --all  # what will it cost
python -m tracelink $R adjudicate --limit 12       # spend a little, look
python -m tracelink $R adjudicate --all

# --- free: ground it, then read it -------------------------------------
python -m tracelink $R verify                      # every citation, against the source
python -m tracelink $R couple                      # which tickets depend on which
python -m tracelink $R shadow                      # code no ticket accounts for
python -m tracelink $R drift                       # documented capabilities with no code
python -m tracelink $R gates                       # the gates the docs set, measured
python -m tracelink $R effort --docs ../my-repo/docs     # how long it took, what it produced
python -m tracelink $R map --file ui/dashboard_tab.py    # which doc section covers which lines
python -m tracelink $R reconcile --done-status "Release it"   # tracker vs docs vs code
python -m tracelink $R report                      # re-render, free
python -m tracelink $R cost --docs-cost 12.00      # what the whole thing cost
```

Stages are independent and write one versioned artifact each into the run
directory. Re-running `retrieve` after a config change touches nothing
upstream.

## Stages

| Stage | Module | Cost | Output |
|---|---|---|---|
| 0a tickets | `adapters/tickets_tabular.py` | free | `tickets.json`, `run.json` |
| 0b diagnose | `diagnose.py` | free | `diagnosis.json` |
| 0c corpus | `corpus.py` | free | `corpus.json` |
| 0c features | `adapters/featuremap_markdown.py`, `adapters/featuremap_json.py`, `features.py` | free | `features.json` |
| 0e progress | `adapters/progress_markdown.py` | free | `progress.json` |
| 0d translate | `translate.py` | **$** | `translations.json` |
| 1 retrieve | `index.py`, `retrieve.py` | free | `candidates.json` |
| 2 describe | `describe.py` | free | (in-memory) |
| 3 adjudicate | `adjudicate.py` | **$** | `verdicts.json` |
| 3b verify | `verify.py` | free | `grounding.json` |
| 4 shadow | `shadow.py` | free (`$` with `--describe`) | `shadow.json` |
| 4b drift | `features.py` | free | stdout |
| 4c map | `docmap.py` | free | stdout |
| 4d gates | `gates.py` | free | `gates.json` |
| 4e effort | `delivery.py` | free | `delivery.json` |
| 5b reconcile | `reconcile.py` | free | `reconciliation.json` |
| 5 explain | `explain.py` | **$** | `explain.json` |
| 6 couple | `couple.py` | free | `links.json` |
| — synth | `synth.py` | free | a repo + backlog with known answers |
| — label / score | `label.py`, `evaluate.py` | free | `labels.json` |
| — cost | `cost.py` | free | `cost_report.json` |
| — report | `report.py` | free | stdout |

Four stages can spend money: `translate`, `adjudicate`, `explain`, and
`shadow --describe`. Each estimates first with `--dry-run`.

## The three design rules

**1. Nothing may name a project, file or language.** The demo hardcoded
`"CoWorkLocal"`, `assets/d3`, `i18n.py`, and `.py`-means-code. Here the sheet shape is
*detected* (header row, column aliases, and whether real work lives in keyed or
unkeyed rows), file roles are *structural* (a file is `data` because it is large and
low-symbol, not because of its name), and symbols come from CodeWiki's analysers —
Python, Java, Kotlin, C#, C, C++, PHP, Ruby, JS, TS — with a Python-AST fallback that
says so when it is used.

**2. Rarity is relative to the corpus.** `df <= 4` is not a statement about rarity, it
is a statement about a 148-file repo. Bars are a fraction of the indexed corpus with a
floor and a ceiling (`config.corpus_bar`). This was already diagnosed in the demo's
own README and never made it into the demo's code.

**3. Coverage is not accuracy, and is never reported as if it were.** "82.7% of
tickets got a strong candidate set" counts a ticket as answered whether the files were
right or wrong. `evaluate.coverage()` and `evaluate.score()` are separate functions and
`score` refuses to run without hand labels.

## Two demo faults that are now structural fixes

**Title-head collision.** 63 of 143 demo tickets shared a candidate set with another
ticket, because only the text before the em-dash was tokenised — ten `Cowork Chat — …`
tickets got byte-identical answers, so 143 retrievals were really 96 decisions. When a
head is shared by `head_collision_threshold` tickets or more, the tail is tokenised too
and the ticket is flagged. On the demo this took distinct sets from 96 to 140.

**Hub documents.** `assets/RULEBASE.md` matched 134/173 tickets and was excluded by
name. A document matching most of the backlog describes everything and discriminates
nothing — so `retrieve.detect_hubdocs` *measures* it against
`hubdoc_ticket_fraction` and excludes it on evidence. Nothing is excluded by name.

## The fourth matcher, and what it was measured to be worth

`stem`, `sym` and `prose` all match a *name* that happens to be spelled the same in
two places. `doc` goes through a human's statement of what the product does: ticket →
documented feature row → the code that row names. It runs only when `features.json`
exists, and `retrieve --no-docs` is the ablation.

Over the same 173 tickets:

| | `--no-docs` | with docs |
|---|---:|---:|
| strong candidate set | 158 (91.3%) | **167 (96.5%)** |
| nothing at all | 7 | **3** |
| distinct candidate sets | 143 | **157** |
| median set size | 5 | 6 |

It fires for 132 tickets and is the sole strong witness for 9. The cost is one extra
file in the median set.

Almost all of that came from one rule. The default bar — two rare words shared
between ticket and feature row — made the matcher fire for 57 tickets and moved
coverage by 2, because a one-line table row rarely *has* two rare words. Letting a
hit on the row's own function name carry the bar alone is what made the stage worth
running. Strength is then decided **per claim, not per document**: if a row says the
budget card is `_apply_budget()` and that function is really there, the link is true
however wrong the rest of the document is. Modality governs framing, not retrieval.

It does **not** fix title-head collision. That was already repaired by tokenising the
tail; 1 of 36 head-sharing tickets still collides with a sibling either way.

## Ground truth

`score` refuses to run without labels, on purpose. Without them every threshold change
is unfalsifiable — which is how the demo reported 94.2% before three fixes brought it
down to an honest 82.7%, with no way to tell which fix helped.

`label` samples *stratified by retrieval outcome* (no candidates / shared head / clean
hit), not at random: a random sample of a skewed backlog mostly re-measures the majority
class, and the informative cases are the embarrassing ones — a shipped ticket with no
candidates. It is resumable and saves after every answer.

## Stage 0c — the team's own documentation, as a checked feature map

Most projects worth tracing already have documentation, and it is better than
anything a generator produces — organised by capability, written in the same words
as the tickets, correct about things no analyser can infer. It is also, in part,
about a system that does not exist.

### A docs folder is not one thing

`features` reads a markdown tree **recursively** into feature rows — a heading path,
a label, the sentence a person wrote, and the symbols and files that row names — then
looks up every one of those names in the corpus and writes down whether it is there.
That measurement classifies each document:

| modality | meaning | may be used as |
|---|---|---|
| `grounded` | its claims resolve in this corpus | evidence |
| `ungrounded` | it names code this corpus does not contain | context, never proof |
| `process` | it names almost no code | background |

Nothing is classified by its filename or its folder. A plan renamed
`current-implementation.md` stays ungrounded, and there is a test for that.

**Symbols and paths are counted apart**, because they fail for different reasons. A
refactor split map here resolves 127 of 233 *symbols* and 2 of 10 *paths*: it knows
today's code perfectly and files it under directories that do not exist. Averaged
into one rate that document reads as grounded, and a refactor plan then gets handed
to the adjudicator as a description of the shipped build. So naming absent files is
disqualifying on its own; otherwise the symbol rate decides.

### Ungrounded is not a synonym for wrong

Resolution alone cannot tell a design from an accurate description of a *different
build*, so the word does not pretend to. `features` reports a second measurement that
usually can: the fraction of misses that have a near-match in the code. A design
names files that do not exist; a later build names functions that were since renamed.

```
VERSION SKEW — documents that name functions this build *almost* has.
  function_list.md            12 of 127 misses are near-matches (9%)
      doc says _duplicate_task       code has duplicate_task (core/tasks.py)
      doc says _delete_task          code has delete_task (core/tasks.py)
```

The near-match cutoff is 0.92, set by looking at what fell either side of it. At 0.86
the pairs arriving were `nav_project` ~ `save_project` (0.870) and `run_node` ~
`run_code` (0.875) — different functions sharing a prefix. Real renames in the same
data score 0.97 and up. The gap is wide and empty, so the bar sits in it, and the
measure under-reports rather than over-reports: a near-match is used to argue a
document is merely out of date, so a false one is the expensive kind.

### `map` — which section covers which lines

A resolved claim already knows where the code is; `map` aggregates claims up to the
unit a person *wrote* (the section) and down to the unit a person *reads* (a line
range), in both directions. No model, no embedding, no keyword matching: a section
points at a region because the team wrote a function's name in it and that function
is at those lines. Open the two files and check.

```
$ tracelink map --section "Token Usage"

function_list.md:11   [ungrounded]
  … / 1. DASHBOARD (Bảng Điều Khiển) / Token Usage & Cost — Thống Kê Token & Chi Phí
  10 rows, 8/10 claims resolved
    ui/dashboard_tab.py:212-306   _apply_budget, _chart_next, _chart_prev, _on_gran_changed, +2
    ui/dashboard_tab.py:359-421   _ai_analyze, _apply_saving_strategy
    ui/monitoring_tab.py:826-850  _apply_budget, _refresh_budget
```

That third line is the kind of thing the map is for: the *Dashboard* section also
reaches into `monitoring_tab.py`, which carries its own `_apply_budget` and
`_refresh_budget`. Nobody wrote that down anywhere.

`--file PATH` runs it the other way — every section that reaches into a file, and
the lines each one reaches. `--undocumented` lists source files no section reaches
at all: 65 of 154 here, led by `ui/accounts_tab.py` at 659 symbol lines.

Three things make the difference between a map and a shape:

- **Line spans, not files.** `Symbol` carries `line`/`end_line` from CodeWiki's
  analyser or the AST fallback. Without them a trace bottoms out at
  "`ui/chat_panel.py`", and for 1,738 lines that is a direction, not a location.
  A corpus built before this degrades to file level rather than vanishing.
- **Adjacent symbols are one region.** Two methods six lines apart are one area of a
  file to a reader; printing them as two ranges is true and useless. Twelve lines is
  the stitch distance.
- **A name matching everything pins nothing.** `__init__` resolves in all 166
  classes. Left in, every section documented every file and coverage read as
  near-total. A claim resolving to more than `max_pin_targets` symbols still counts
  as resolved — the name *is* in the code — but is not allowed to point at a line,
  and is counted as `ambiguous` so the reason is visible.

**Coverage is reported twice, because the two numbers disagree.** A section writing
`ChatPanel` covers lines 69-1738 — by lines, the whole file — while locating exactly
one of its 71 methods. So `map` prints symbol precision beside line coverage: the
median documented file here has **12% of its symbols named individually and 14% of
its lines inside some section**. Reporting only the second would have called this
documentation set far more precise than it is.

## Stage 0e — the same documents, read as a record of *work*

`features` asks what these documents say about the code. `progress` asks what they
say about the **project**. On this dataset the two are disjoint, and that is the
finding: probing all 173 tickets for `R01`…`R10`, `EPIC`, `Team Duy/Nam/Hoa`, `ADR`,
`keyring`, `Clean Architecture` scores **zero**. The tracker holds features by
component and knows nothing about the ten-EPIC refactor the documents record.

```
63 work items (56 done, 7 open), 56 with real start and end times
30 plan rows, 6 quality gates (0 signed off), 50 register entries
64 lines declined for carrying no identifier (templates, checklists);
 4 could not be read (4 ragged-row)
```

Six structural rules replace the six things it is forbidden to name:

- **a work item is a checkbox** — a GFM task item or a table row whose state cell is
  one;
- **it must carry an identifier**, and an identifier is a token whose *prefix
  recurs*. This is the one that earns its keep: a naive checkbox count reports **14
  open tasks** here when the answer is **7**, because a Definition-of-Done template
  contributes seven permanently-unchecked boxes. Templates do not carry ids;
- **dates are found by pattern, their labels learned** — cluster `label: <date>`
  pairs, keep the two commonest, earlier median is the start. `Start:`/`End:` and
  `Inizio:`/`Fine:` both work with neither appearing in the code;
- **a table is a work table when half its rows carry a checkbox**, and columns are
  typed by content, never by position;
- **a work row carrying a runnable command is a gate**, not a task;
- **grouping is the heading stack.**

Everything a rule does not classify is kept verbatim **under the author's own column
name**. Deciding which column is "the criterion" needs the document's language;
recording `fields["Criterion"]` does not.

There is no ground truth for this extraction, so it cannot be scored. The defence is
`--audit`: every task-shaped line the adapter declined, with a reason. Note the two
reasons are not the same thing — `no-id` is rule 2 working, `ragged-row` is the
parser failing — and collapsing them into one "unread" number hides the second
behind the first.

## Stage 5b — `reconcile`, three witnesses to one delivery

Tracker, documentation, code. Two disagreeing says something is wrong; three say
which. The join is on resolved code anchors, never on text — matching Vietnamese
task descriptions to English ticket summaries is a translation problem wearing a
traceability hat.

| tracker | docs | code | label |
|---|---|---|---|
| done | done | present | `agreed` |
| done | done | absent | `both-sides-wrong` |
| done | open | present | `undertracked-doc` |
| done | open | absent | `tracker-optimistic` |
| open | done | present | `untracked-delivery` |
| open | done | absent | `doc-optimistic` |
| open | open | present | `unclaimed-code` |
| open | open | absent | `agreed-outstanding` |

Three refusals, each of which cost a bug to learn:

1. **Absence is never inferred from a failed lookup.** An item naming no deliverable
   is `unknown`, not `absent` — the same reason `adjudicate` has three verdicts.
2. **An *unverifiable* claim from an ungrounded document may not assert done.** The
   discount is per item, not per document: a task whose deliverable we looked up
   ourselves does not need the document to vouch for it. Only where there was
   nothing to check does modality decide, and there the item reports
   `claimed-done-elsewhere`, which is deliberately absent from the label table so no
   triple containing it can come out `agreed`.
3. **Which tracker status means finished is a decision.** `--done-status` is
   explicit and recorded in the artifact; without it every row is honestly `unknown`.

Rule 2 was first written per *document*, and it silently swallowed the entire
report: every document in this set is ungrounded, so all 56 completed tasks were
discounted and **57 of 63 rows came out `unknown` — 54 of them with a code state
already established**. The report knew the answer and threw it away at the last
step. Retrieval had had the right rule since the previous session: *strength is
decided per claim, not per document.*

**The join has two strengths and the weaker one is labelled.** An item whose own code
is missing resolves nothing, joins to no ticket, and would be permanently `unknown` —
which would make the four labels worth reading unreachable. So an item can also join
through the *section* it sits in, via that section's other rows. `joined_via` records
which, and a section join is area-level evidence: the tracker claims this part of the
system is done, not this task.

### What it found

```
$ tracelink reconcile --done-status "Release it"
63 work items reconciled against 173 tickets and 29 files

  DIRECT — the item's own code anchor found these tickets
      14  agreed

  AREA-LEVEL — joined through the item's doc section, so the tracker
              column is about this part of the system, not this task
      36  both-sides-wrong
       6  tracker-optimistic
       7  unknown

  [both-sides-wrong]  R01-T03  Viết script quét tĩnh chặn code mới trong domain/…
    tracker=done  docs=done  code=absent   (joined via section)
    code missing: scripts/check_imports.py
```

**The split is structural, not a quality score.** An item joins directly only when
one of its deliverables resolved, and `absent` means none did — so every
`both-sides-wrong`, `tracker-optimistic`, `doc-optimistic` and `agreed-outstanding`
row rests on area-level tracker evidence, always, by construction. There is no
stronger join available for them: a task claiming to have written a file that does
not exist leaves nothing for a ticket to touch. Reporting the weakness is the only
honest option, so the summary is printed in two blocks rather than one.

The 36 `both-sides-wrong` rows are coherent rather than noisy, and say the same thing
the modality measurement did — now at task granularity with the tracker's view
attached. They name missing files under `domain/`, `application/`, `presentation/`,
`infrastructure/` and `scripts/`. **None of those directories exists in this build.**
These are tasks ticked complete whose deliverables belong to the 4-tier layout the
repository never received.

The 14 `agreed` are the mirror image: tasks whose named deliverable really is in the
corpus, verified end to end. Discounting these wholesale was the expensive half of
the rule-2 mistake above.

The design document predicted the R02 case would come out `agreed-outstanding`. It
comes out `tracker-optimistic` — the tracker has no concept of R02 at all, so the
tickets reaching those files are feature tickets marked done.

### The structured half of a docs tree

Teams generate as much documentation as they write. `adapters/featuremap_json.py`
reads the generated kind with the same claim test the markdown adapter uses: find
the records (a top-level list of objects, or the one list inside an object), offer
every string value to `normalise_claim`, and let a nested list inherit the file its
parent names — without that last rule a control at line 108 is an orphan.

Line numbers *in* the record are deliberately not read. The corpus already knows
where a resolved symbol is, and trusting the document's own number would pin a
section to a line that moved three commits ago. A document is believed about what
exists, never about where.

Ablated with `features --no-json`, over the same tree:

| | markdown only | + json |
|---|---:|---:|
| feature rows | 2,728 | **4,065** |
| distinct claims | 1,193 | **1,648** |
| claims resolved | 439 | **537** |
| files reached | 109 | **111** |
| median symbol precision | 11% | 10% |

**Precision did not improve — it fell a point**, which is the opposite of what was
predicted when this was proposed. The structured sources add breadth, not depth:
378 control rows each naming one or two things spread coverage over more files
without locating more of any one of them. Worth keeping for the 98 extra resolved
claims and the screenshot anchors, but not for the reason it was picked.

Two fixes fell out of reading them. `ui/dashboard_tab.py:35` is a path with a
locator, and leaving the `:35` on meant the manifest's 54 anchored rows matched
nothing at all; and a `screens/*.png` reference is a sibling artifact, so the
docref rule now knows every file in the docs tree rather than only the parsed ones.

### `gates` — run the criteria the documents set

A quality gate carries a criterion and usually the command that proves it. Those
criteria are arithmetic over a corpus already built, so `gates.py` evaluates them:
`file_size`, `layer_purity`, `secrets`. A criterion matching none of the three
comes back `unchecked`, which is honest and common.

The document's command is quoted, never executed. `python scripts/audit_security.py`
names a script this repository does not contain, and running a command out of a
document is executing a file nobody in the process wrote.

```
6 gates the documentation defines: 0 pass, 2 fail, 4 not checkable here

  [FAIL] CASAN Check 1: Security Audit        python scripts/audit_security.py
         3 literal credential(s) in tracked files
           config.py:108  sandbox_pw = 'quandh14'
  [FAIL] CASAN Check 2: Modularity (LOC)      python scripts/check_loc.py --max-lines 400
         27 of 154 files exceed 400 lines
  [  ? ] CASAN Check 3: Clean Architecture    python scripts/check_imports.py
         names PySide6 but no directory in the criterion exists here
```

`sandbox_pw` is the exact credential the team's own checklist blames for its two
failing tests. Check 3 is the one to read twice: **a rule about a directory that
does not exist is not a pass.** "0 PySide6 imports under `domain/`" holds
trivially when there is no `domain/`, and reporting that as green tells a reader
the architecture holds when what happened is that it was never built.

Three things the first version got wrong, all of the same kind — reading operands
out of text that was not the criterion:

- the gate's *title* supplied module names, so "Checkpoint 1: Contracts & Fakes"
  was answered "0 imports of `Checkpoint` under `tests/`" — a confident pass for a
  rule nobody wrote. A candidate module must now be something the repository
  actually imports.
- the *command* supplied directories, so `pytest tests/` made three unrelated
  gates into rules about `tests/`.
- `pass` matched as a credential word, so "five scenarios pass" ran a secrets
  audit; and scanning whole lines rather than key-value pairs reported **73**
  credentials, most of them words inside comments. Tied to an assignment and with
  comments stripped, it is 3.

### `effort` — how long the recorded work took, and what it produced

56 of the 63 tasks carry both a start and an end. That is the only source of real
durations anywhere in this dataset — the tracker has none.

```
56 of 63 tasks record both a start and an end.
  median 10 min   quartiles 5-150   total 125.2 h
  11 tasks record under five minutes.
         1 min  R01-T01  Team Duy      <- "write the Architecture ADR"
```

**A recorded interval is not effort**, and the module says so before it says
anything else. These are gaps between two timestamps somebody typed, and no
arithmetic separates a fast team from a backfilled sheet. The distribution is the
output; the reader judges.

One thing it *does* assert, because it is a statement about the record rather than
about anybody's day: **two intervals cannot overlap for one owner.** Four tasks do
here, so at least one of each pair is an estimate or a backfill. Unowned tasks are
never compared — two tasks with no owner are not evidence about one person.

The calendar comes free with the timestamps: work ran 21–28 August against a plan
through the 31st, **nothing at all on the 24th**, and 18 plan rows started on a
different day than planned — six days early in one case, three days late in
another.

**What finished tasks say they produced.** A completed task names its output; some
of those names are files, and the same resolution the rest of the pipeline does
will say whether they are there. 91 are not, split by kind because a missing test
and a missing source file are different findings with different owners:

```
  5 tests:
    R01-T02    tests/fakes/fake_provider.py
    R01-T04    tests/characterization/test_run_cowork.py
    R10-T05    tests/e2e/test_smoke.py
  79 source files, 6 symbols, 1 document
```

Those five are the ones to read twice. The Definition of Done requires passing
tests and the checklist quotes "377 pass / 4 fail" — against a suite whose named
files are not in the repository. Only *completed* tasks are counted: an open task
has not claimed anything yet, so its missing output is the plan, not a finding.

Wiring this up found a bug in `corpus.looks_like_test` that predates it:
`TEST_HINTS` held `test` but not **`tests`**, the commonest convention there is.
`tests/fakes/fake_provider.py` was classified as production source, and a helper
under `tests/` counted against a rule written for production code. Fixed, and the
size gate now reports production and test files separately rather than choosing
which reading of "production" the criterion meant.

### `drift` — the mirror of `shadow`

`shadow` finds code no ticket accounts for. `drift` finds the opposite: a capability
the documentation describes with nothing behind it. Restricted to grounded documents
by default, because an unresolved claim in a design is the design, not a defect.

## Stage 2 — describers

`describe.py` is an interface, not a CodeWiki wrapper. Three free implementations:

- **`RawSource`** quotes the candidate files, windowed **around the matched anchors**.
  The demo's adjudicator showed `lines[:220]` — the top of the file — so a symbol at
  line 400 was never shown and the model answered `unverified` about code that was
  right there. Elisions are printed, never silent.
- **`DocFeature`** shows the team's own row for this ticket with **every name in it
  already looked up**: `` `_apply_budget` -> found: `ui/dashboard_tab.py::DashboardTab._apply_budget` ``
  or `-> NOT in this repository`. The model is not asked to search and cannot
  hallucinate a hit. Rows from ungrounded documents arrive carrying their own warning
  and the measurement behind it.
- **`ExistingDocs`** excerpts any markdown that names the candidate files, and returns
  *empty* when nothing covers them rather than disguising the gap. It reads the tree
  recursively — globbing one level is how a first pass saw 3 files out of 39 and
  reported "no documentation" about a project with a hundred pages of it. When a
  feature map exists, the documents it measured as ungrounded are withheld from this
  describer, because here they would arrive unlabelled.

`Bundle` runs them and drops whichever produced nothing. The ablation found doc value
is concentrated, not uniform, so this is a per-ticket choice — which is why a
generating describer (calling CodeWiki for a doc that doesn't exist yet) is
deliberately not written until the free ones are measured.

**The failure this exists to prevent.** A refactor plan describes the feature in
confident present tense, written by the people who own the code. Shown to an
adjudicator under the heading "Architecture documentation excerpt", it is an argument
that the feature is already built. The verdict comes back corroborated, the citation
is real, and nothing downstream can catch it — `verify` checks that cited symbols
exist, and they do; they are just in a document rather than in the repository. The
only place to stop it is before the prompt is built.

## Stage 3 — adjudication

Three verdicts, not two, because proving a claim true and proving it false are not
symmetric: finding the code proves implementation, not finding it proves nothing.
`unverified` is a first-class answer.

Uses the Anthropic SDK with structured outputs (`messages.parse`), so the verdict is a
validated object rather than parsed prose. Defaults to `claude-opus-5`; `--model
claude-sonnet-5` costs roughly a third. Every call is cached by a hash of
(prompt version, model, system prompt, prompt), so editing the prompt re-spends and
re-running does not.

The report leads with **status conflicts** — tracker says To Do, code says shipped —
because `unverified` will be the majority verdict on any real backlog and is honest
but useless to read in bulk.

## Stage 4 — shadow scope

Stages 1-3 walk from a ticket to the code. This walks the other way, and it is the
half that finds work that exists and appears nowhere in the plan. A backlog audit
that only checks tickets can only ever find over-claiming.

**Coverage is graded in three tiers, not two.** A file being *proposed* by the keyword
matcher means very little — the matcher is noisy by construction. A file being *cited
as evidence* in a settled verdict means a model looked at it and said it implements
something:

| tier | meaning |
|---|---|
| `cited` | a corroborated or contradicted verdict pointed at it — genuinely tracked |
| `retrieved` | proposed for some ticket, never cited — ambiguous |
| `unclaimed` | never proposed for any ticket — shadow scope |

Only `unclaimed` is reported as shadow scope, and `retrieved` is kept as its own tier
rather than folded into either side: collapsing it would silently decide a question the
evidence does not answer. An `unverified` verdict does **not** promote a file to
`cited`, because a verdict that settled nothing is not evidence of tracking.

Tests and generated data are excluded (untracked test code is normal); config is not,
because an untracked policy file can absolutely be scope. Files with fewer than
`--min-symbols` symbols are dropped — `__init__.py` is untracked in every repository
ever written and reporting it is noise. Results group by directory, a weak proxy for a
module that needs no LLM and cannot hallucinate.

`--describe` adds a model pass that names each untracked capability and judges whether
it is user-facing scope. The prompt tells it that **most untracked code is not scope**
and to return an empty suggested ticket rather than inventing one.

## Stage 0b — diagnose, before trusting anything

An export can have 400 columns and carry almost nothing, and it can hide its
real structure in a free-text field or in row order rather than in the fields
built for it. Both are common and both change what the rest of the pipeline
can honestly say, so this runs first and reports:

* which columns carry anything (the demo export: **8 of 421**);
* whether any **relationship** field is populated — if not, no dependency can
  be read from the tracker and any shown downstream must be labelled inferred;
* planning fields that are really timestamps (`Planned Start` equal to
  `Created` on every row is a creation date, not a plan);
* structure buried in free text — `PO:`/`BA:`/`Developer:` pairs and raw
  spreadsheet date serials, both of which the adapter then recovers as real
  fields;
* whether row *order* groups by a column, which is structure somebody created
  by hand and no field records.

Jira's `Rank` is deliberately **not** counted as a relationship. It is a board
ordering token, always populated, and counting it would turn "no dependencies
recorded" into "dependencies recorded" on every Jira export ever produced.

## Stage 0d — translate, because matching is cross-lingual or it fails

Retrieval finds ticket words inside identifiers, and identifiers are English
in almost every codebase — including ones written by teams who do not work in
English. Measured on the demo backlog before this existed: 149 English tickets
retrieved at 92% with 72 corroborated; 24 Vietnamese at 75% with **zero**.

Two rules keep it safe. **The original is never replaced** — translations go
to `summary_en`/`description_en`, every screen still leads with what the
tracker says, and only the matcher reads the English. **Technical terms are
not translated**: `RAG`, `MCP`, `Co4E`, `RULEBASE` are the tokens that match
code, so the prompt forbids localising them and the model reports what it
preserved.

Detection is by script range, not word list, so an all-English backlog costs
nothing and makes no call. On the demo: 24 tickets, $0.11, retrieval 75% → 88%,
and it surfaced a status conflict that had been unreachable.

## Stage 3b — verify, the only checked claim on the page

A verdict is an opinion; the citations under it are facts. Every
`file::symbol` a verdict names is looked up **in the source** — not in the
symbol index, which was the first version and reported 45 real module
constants as hallucinations.

Three outcomes: `defined` (found as a definition), `present` (in the file but
not as a definition — probably a call site, real but weaker than claimed) and
`absent`, which invalidates the verdict. A verdict citing nothing is
`uncited`, not a fault: `unverified` is supposed to cite nothing.

On the demo: **492 citations, 0 absent.** `verify` exits non-zero on any
ungrounded verdict, so CI can gate on it. Half its tests plant a fabrication
and require it to be caught; the other half plant a real citation in an
awkward shape — qualified method, module constant, artifact-node id, prose
heading — and require it to be left alone. Each of those was a false positive
this checker actually produced.

## Stage 6 — couple, relationships nobody recorded

The tracker records no links, so any relationship here is inferred from the
**code**, not the text: two tickets are related when the code implementing
them is related, and the file or import that connects them rides on the edge
so a reader can reject it.

`depends-on` is directional and comes from a real import edge; `shares-code`
is symmetric and means the same file implements both. Hub files are excluded
on measurement, not by name — a module most of the code imports connects
everything to everything, which is a fact about the module. Only **cited**
evidence counts, `unverified` verdicts contribute nothing, and a diffuse edge
(many tickets on each side) is reported at low confidence rather than dropped.

## Ground truth without waiting: `synth`

`score` needs labels and labelling needs an afternoon. `synth` builds a repo
and a backlog **from one plan**, so the mapping is known by construction: 46
tickets over 13 files across six difficulty tiers — `named`, `symbol`,
`prose`, `shared`, `foreign`, and `decoy`, whose right answer is *nothing*.

It immediately found a real bug: the index stores `InvoiceEngine` as
`{invoice, engine}` while ticket text was lowercased to `invoiceengine`, so a
ticket naming a class by its exact name could not find that class. The
`symbol` tier scored 0/12; after the fix, 12/12, and F1 went .545 → .769.
That score is now a test with a floor.

It measures the matcher against difficulties **we thought to build in**. Treat
a regression here as real and an improvement as a hypothesis. Hand labels on
real data remain the only evidence about reality.

## What it costs

`cost` totals every stage, including spend that happened elsewhere. The demo
run: $0.11 translate + $10.44 adjudicate + $0.20 explain = **$10.75 of
pipeline**, standing on **$12 of CodeWiki architecture docs** — so $22.75
all in, and the docs were the largest single line item while changing
roughly one verdict in twelve.

That $12 line is now avoidable on any project that documents itself. Running
`features` over the team's existing `docs/` costs nothing, produces 2,716
feature rows against CodeWiki's 20 generated pages, and — unlike a generated
page — each row carries the name of the function the team says implements it,
which is what makes the claim checkable at all. Declare it as
`--docs-cost 0 --docs-note "team's own docs, already written"` and the
pipeline total stands on its own.

The catch is in the next section: the docs being free does not make them
*true*, and the stage measures which ones are.

Two things this exists to prevent. A cached re-run reports `$0.00`, which is
true and does not mean the artifact was free — so the cache stores token
counts and the recorded cost survives re-runs. And external inputs are
declared once (`--docs-cost`) and remembered, because a number nobody can
re-derive has to be written down or it is lost.

## What the docs turned out to say about CoWorkLocal

The finding, from `features --docs ../hackathon/docs` over `pimsathon-main`:

```
39 documents -> 2716 feature rows, 1193 distinct code claims,
434 of them present in the corpus (36%); 108 of 172 files reached
```

**1 grounded document, 33 ungrounded, 5 process.** That is not a parser
failure — it is the answer. The documentation set is *ahead of the code it
ships with*:

- `architecture/` specifies a 4-tier layout (`presentation/`, `application/`,
  `infrastructure/`, `domain/`). `pimsathon-main`'s top level is `ui/`,
  `core/`, `providers/`, `security/`. ADR-001 is marked
  "Status: ACCEPTED / ENFORCED"; 363 planned symbol placements target files
  that do not exist.
- `function_list.md`, the nav-bar feature inventory whose structure lines up
  exactly with the ticket heads, resolves 96 of 223 symbols. The misses are
  systematic, not random: it says `_submit_message()` where the code has
  `submit()`, `_build_job()` where the code has `build_job()`, and names whole
  subsystems (`_build_kanban`, `_build_calendar`) the repository has no trace
  of, not even a call site.

So the docs and the code are two different snapshots. **This matters before
anything is concluded from either**: treating `function_list.md` as ground
truth would report a backlog far more complete than it is, and treating an
unresolved claim as a missing feature would report drift that is really skew.
Both readings are now measured and separated rather than assumed.

## Known gaps

- **The doc side is not translated.** `translate` puts *tickets* into English;
  feature rows stay as written. Against a Vietnamese feature table and a
  mostly-English backlog, the `doc` matcher is carried by headings (which are
  bilingual here) and by symbol names (which are English everywhere). Rows
  whose only discriminating words are Vietnamese prose are unreachable from an
  English ticket. Extending `translate` to the feature map is the obvious next
  measurement, and it is a paid stage, so it should be justified by an ablation
  and not by taste.
- **Modality is per document, not per section.** A file that is half status
  report and half design gets one label. The split maps are the honest case for
  finer granularity: their rows are individually classifiable and are not
  classified individually.
- **No hand labels on real data.** Zero. `synth` scores the matcher against
  difficulties we invented; it cannot tell you real tickets resemble them.
  This is the single biggest hole and it costs an afternoon, not money.
- **The `prose` tier scores 0/12.** "Improve reversal" → `billing/refund.py`
  needs a synonym, which keyword matching cannot do. This is the concrete
  case for embeddings, now with a number on it.
- **57% of real verdicts are `unverified`**, and 84 of those 99 *had*
  candidates — the model said the files shown were unrelated or that the code
  was elsewhere. That is an evidence problem (`--max-files`, `--window`), not
  a ceiling, and the experiment to measure it is written but unrun.
- **English-only vocabulary.** Column aliases, stopwords and the relationship
  field list are English. A non-English *header* now fails loudly rather than
  silently; non-English *tickets* are translated. A tracker whose link field
  is named in another language would read as "no dependencies recorded",
  which is a wrong finding rather than a missing one — the worst kind.
- **`explain.py` has no tests** and `label.py` little. Overall coverage 76%.
- **Inferred links are unvalidated.** The confidence labels on `couple` are
  structural heuristics, not measured accuracy.
