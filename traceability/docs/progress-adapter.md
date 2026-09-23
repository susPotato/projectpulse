# Reading a team's progress documentation as delivery data

**Design and test plan for `tracelink progress` + three-way reconciliation.**

Status: **built**, 2026-09-22 — `adapters/progress_markdown.py`, `reconcile.py`,
`tracelink progress`, `tracelink reconcile`. Three predictions in here turned out
wrong when measured; they are marked WRONG inline and the WORKLOG entry for that
date says what happened instead. Kept as written otherwise: a plan edited to match
its outcome stops being evidence of anything.

Written 2026-09-22, against `pimsathon-main` + `hackathon/docs` + `Jira Cowork Local_0913.xlsx`.

---

## 0. The finding this rests on

The 173-row Jira export and the 39-document `hackathon/docs/` tree describe
**two different projects on the same product**, and only one of them is in the
tracker. Probing the whole backlog text:

| probe | hits across 173 tickets |
|---|---:|
| `R01` … `R10`, `EPIC` | 0 |
| `Team Duy` / `Team Nam` / `Team Hoa` | 0 |
| `refactor`, `tái cấu trúc` | 1 |
| `ADR`, `circular`, `keyring`, `Clean Architecture` | 0 |

Jira tracks features by capability component. The docs track an 11-day,
three-team, ten-EPIC architectural refactor with 63 tasks and 112 real
timestamps. A program report built from Jira alone is missing half the
delivery, and no existing stage can see it.

`features`/`docmap` read these documents as *statements about code*. This
proposal reads the same documents as *statements about work*: who did what,
when, whether it passed, and what is still owed.

---

## 1. Go / no-go spike — do this before writing any code

Budget: about half an hour. Write it in the scratchpad, **not** in the repo;
it is a measurement, not an artifact.

The question is not "can markdown be parsed" but "how much of this document
is mechanically extractable, and is the remainder small enough to ignore?"

```
1. Count GFM task-list items      (`- [ ]` / `- [x]`)
2. Count those carrying an id     (a repeated PREFIX-NNN token)
3. Count those carrying two dates
4. Count table rows whose last cell is a checkbox
5. Print every line matched by (1) but not by (2) or (3)
```

**Go if** ≥80% of task-list items yield an id *and* a pair of dates, and the
unmatched remainder in step 5 is legible as a category rather than a mess.

Measured on `refactor/Refactoring_Checklist.md` on 2026-09-22:

```
70 task-list items
63 with an id (R##-T##)            -> 90%
63 with Start: + End: timestamps   -> 90%, and the same 63
36 table rows ending in a checkbox (30 daily plan + 6 gates)
 7 unmatched — all of them one category (see the trap below)
```

That is a clear go. **Re-run this spike against any new document set before
assuming the adapter transfers.** A document that yields 30% is not worth an
adapter; it is worth a person reading it.

### The trap the spike exists to find

A naive `- [ ]` count says **14 open tasks**. The truth is **7**.

The other seven are the *Definition of Done template* in Part 4 — a
pro-forma checklist that every pull request is supposed to copy, permanently
unchecked because it is a blank form, not work:

```markdown
## 📋 PHẦN 4: DEFINITION OF DONE (DOD) CHO MỖI PULL REQUEST
- [ ] **1. Kích thước file (LOC)**: ... không vượt quá **400 dòng code**.
```

Counting those as open work doubles the reported backlog. The discriminator
is structural and generic: **a real work item carries an identifier**. The
template items do not. This is why rule R2 below requires an id, and why the
first fixture in the test plan contains a template section.

The seven genuinely-open tasks are `R02-T01`…`R02-T06` and `R07-T06` — the
entire Configuration/Secrets/Keyring EPIC, unstarted. That is corroborated
independently: the checklist blames R02 for two failing tests, and
`config.py:108` still reads `"sandbox_pw": "quandh14"`. Keep this case; it is
the best end-to-end validation the dataset offers (§4.5).

---

## 2. What the data actually looks like

Four shapes, all in `refactor/Refactoring_Checklist.md` unless noted.

**2a — work item with execution timestamps** (63 of these):

```markdown
- [x] **R01-T01 (Team Duy)**: Viết Architecture ADR định rõ ranh giới các tầng
  ➔ `docs/architecture/ADR-001-layered-architecture.md`
  *Start: `2026-08-21 18:23` | End: `2026-08-21 18:24`*
```

Identifier, owner, description, a deliverable path, real start and end.

**2b — the daily plan, one table per team** (3 tables, 30 rows):

```markdown
| Ngày | Task Cần Hoàn Thành | Start Time | End Time | Trạng Thái |
| **24/08 (T2)** | Xây dựng `RoutingApplicationService`… | `2026-08-22 18:53` | `2026-08-25 15:45` | [x] |
```

Planned day in column 1, *actual* dates in 3 and 4. The gap between them is
schedule slip, and it is only visible because both are recorded.

**2c — quality gates** (6 rows, all unsigned):

```markdown
| **30/08 (CN - 17:00)** | **CASAN Check 2: Modularity (LOC)** | `python scripts/check_loc.py --max-lines 400` | 0 file production nào > 400 dòng code | Team Hoa | `____-__-__ __:__` | `____-__-__ __:__` | [ ] |
```

A gate carries its own **verification command** and its own **pass criterion**.
All six have blank timestamps and unchecked boxes, while the daily tables
claim the same checks ran and passed on 28/08. The document contradicts
itself, and that contradiction is an output, not a parse failure.

**2d — defect register** (`refactor/bug.md`, 7 rows plus prose):

```markdown
| `BUG-001` | 2026-08-20 | `core/model_pricing.py` ↔ `core/usage_tracker.py` | Circular Dependency | 🟡 Đã có giải pháp (R09) | Team Duy & Team Nam |
```

Each row is followed by a prose block with symptom, root cause, resolution and
a prevention rule. The file/symbol names in the row are already claims the
existing `features.resolve()` can check against the corpus.

---

## 3. Design

### 3.1 Placement

A new **stage 0e**, beside `0c features`. Same relationship to the pipeline:
optional, free, and everything downstream must behave exactly as it does today
when `progress.json` is absent.

```
0a tickets    export        -> tickets.json
0b diagnose
0c corpus     repo          -> corpus.json
0c features   docs tree     -> features.json      (docs as statements about code)
0e progress   docs tree     -> progress.json      (docs as statements about work)   NEW
…
5b reconcile  all three     -> reconciliation.json                                  NEW
```

`progress` reads the same `--docs` directory as `features`. Two stages over one
input is correct here: they answer different questions, they fail
independently, and a project may have one kind of document and not the other.

### 3.2 Artifacts

Add to `artifacts.py`, following the existing dataclass + `rebuild_*` pattern.
Every record keeps `doc` and `line` so a person can reopen the source — the
same rule `Feature` and `Ticket.source_row` already follow.

```python
@dataclass
class WorkItem:
    wid: str              # "R01-T01" — the document's own id, never invented
    title: str
    group: str            # heading path: the EPIC, section, sprint
    owner: str = ""       # as written; no attempt to resolve to a person
    done: bool = False
    started: str = ""     # ISO 8601 or ""
    ended: str = ""
    planned: str = ""     # from a plan column, when there is one
    deliverables: list[Claim] = field(default_factory=list)   # reuse Claim
    doc: str = ""
    line: int = 0

@dataclass
class Gate:
    gid: str
    name: str
    command: str = ""     # the doc's own verification command
    criterion: str = ""
    owner: str = ""
    due: str = ""
    signed_off: bool = False
    doc: str = ""
    line: int = 0

@dataclass
class Defect:
    did: str              # "BUG-001"
    summary: str
    kind: str = ""        # "Circular Dependency"
    status: str = ""      # as written — never normalised to our vocabulary
    owner: str = ""
    found: str = ""
    anchors: list[Claim] = field(default_factory=list)
    root_cause: str = ""   # WRONG: became one `detail` dict keyed by the
    prevention: str = ""   # document's own `**Label**:`. Telling a root cause
                           # from a prevention rule means reading the language.
    doc: str = ""
    line: int = 0

@dataclass
class ProgressReport:
    root: str
    items: list[WorkItem]
    gates: list[Gate]
    defects: list[Defect]
    #: Every task-shaped line the adapter did NOT turn into a record, with
    #: the reason. See §4.3 — this field is the whole safety net.
    unparsed: list[dict] = field(default_factory=list)
```

Reuse `Claim` for deliverables and defect anchors so `features.resolve()`
resolves them against the corpus with no new code. That is what makes
"BUG-002 says `agent_security` ↔ `agent_security_alert`" checkable.

### 3.3 The generic-parsing problem

The project's first design rule is **nothing may name a project, file or
language** (`tracelink/README.md`). `R\d\d-T\d\d`, `Start:`, `Team`, and
`BUG-` are all specific to this document. The adapter must not contain any of
them.

Six rules, all structural:

- **R1 — a work item is a GFM task-list item or a table row whose row state is
  a checkbox.** Both are markdown, neither is language-specific.
- **R2 — an item must carry an identifier**, defined as a token matching
  `[A-Za-z]+[-_]?\d+([-_][A-Za-z]*\d+)*` that *recurs with the same prefix* at
  least `min_id_family` times (default 3) in the document. Learned, not
  declared. This is what separates real tasks from a Definition-of-Done
  template, and the rule generalises: templates do not carry ids.
- **R3 — dates are found by pattern; their labels are learned.** Collect every
  `<label>: <date>` pair on or under item lines, cluster by label, and take the
  two most frequent label families. The one whose median value is earlier is
  the start. `Start:`/`End:`, `Bắt đầu:`/`Kết thúc:` and `開始`/`終了` all work
  without being listed anywhere. **Precedent: `diagnose.py` already detects
  inline `Key: value` fields this way** — follow it, do not reinvent it.
- **R4 — a table is a work table when ≥50% of its body rows carry a checkbox
  or a recurring status token.** Column roles are then assigned by content:
  the id column is the one whose cells match the id family; date columns are
  the ones whose cells parse as dates; the state column holds the checkbox.
  Assign by content, never by index — a table that puts description first must
  still work.
- **R5 — a gate is a work row that also contains a fenced or backticked
  command.** That single extra signal is what distinguishes "run this to
  prove it" from "do this".
- **R6 — grouping comes from the heading stack**, exactly as
  `featuremap_markdown` already computes it.

**Reuse, do not fork.** `featuremap_markdown.parse_document` already walks
headings, tables, bullets and fenced blocks correctly, including the
fence-skipping that stops `|` inside a code sample being read as a table.
Extract that walker into `adapters/_markdown.py` and have both adapters call
it. Copying it will produce two parsers that drift, and the fence bug will be
fixed in only one of them.

### 3.4 Stage 5b — reconciliation

Three witnesses to the same delivery: the **tracker** (`tickets.json`), the
**team's documentation** (`progress.json`), and the **code**
(`corpus.json`, via resolved claims). Two-way disagreement says something is
wrong; three-way says *which one*.

Join on resolved code anchors, not on text. A ticket and a work item belong
together when the files they reach overlap — `docmap` already computes exactly
that mapping, so reuse it rather than matching titles across two languages.

Emit one row per joined group with the three states and a label:

| tracker | docs | code | label |
|---|---|---|---|
| done | done | present | `agreed` |
| done | done | absent | `both-sides-wrong` — the strongest finding available |
| done | open | present | `undertracked-doc` |
| done | open | absent | `tracker-optimistic` |
| open | done | present | `untracked-delivery` |
| open | done | absent | `doc-optimistic` |
| open | open | present | `unclaimed-code` (cf. `shadow`) |
| open | open | absent | `agreed-outstanding` |

Two hard rules, both inherited from mistakes this pipeline already made:

1. **"absent" is never inferred from a failed lookup alone.** Retrieval
   missing a file is not evidence the code is missing — that lesson is
   already written into the three-valued verdict in `adjudicate.py`. Any cell
   the evidence does not settle is `unknown`, and `unknown` is printed.
2. **Document modality gates what the docs column may assert.** A work item
   from an *ungrounded* document (`features.classify`) describes a build that
   is not this one. 33 of 39 documents here are ungrounded, so this is the
   common case, not an edge case. Such an item may report `open` but must
   never report `done` as fact — it reports `claimed-done-elsewhere`.

---

## 4. Test plan

Write §4.1 and §4.2 **before** the adapter. The fixtures are the
specification; the real document is the integration test, and it is the one
that will lie to you, because there is no ground truth for it.

### 4.1 Three fixtures, in this order

**Fixture A — `progress_plain.md`.** Everything the adapter must get right,
small enough to assert exactly:

- 6 work items with ids `AA-01`…`AA-06`, 4 checked and 2 not
- start/end on 5 of them; one deliberately has neither
- one item naming a deliverable that exists in the fixture corpus, one naming
  a file that does not
- a daily table with a *planned* column and actual dates, one row where actual
  is later than planned
- a gate table: 2 rows with a command, 1 signed off and 1 with blank
  timestamps
- a defect table with 3 ids and prose blocks beneath
- **a Definition-of-Done template section: 4 unchecked boxes, no ids**

Assertions: exactly 6 work items (not 10); 4 done, 2 open; the template
contributes nothing; the unsigned gate is `signed_off=False`; the slipped row
reports both planned and actual.

**Fixture B — `progress_other_language.md`.** Fixture A with every label
changed and nothing else: ids `ZZ_101`…, dates labelled `Inizio:`/`Fine:`,
headings and prose in another language, a different checkbox-column position.

> Assertion: **structurally identical output to Fixture A.** Same counts, same
> done/open split, same gate states. Only the strings differ.

This single test is what enforces design rule 1. It is cheap and it will fail
the first time someone hardcodes `Start:`. Write it second, not last.

**Fixture C — `progress_hostile.md`.** The shapes that break parsers:

- a fenced code block containing a markdown table and `- [ ]` lines
- a table with a missing trailing pipe, and one with a ragged row
- an id-like token in prose that is not a task (`see R01-T01 for context`)
- a nested list under a task item
- an item whose date cell is `TBD`, and one that is `____-__-__ __:__`
- two items sharing an id

Assertions: nothing inside a fence is extracted; malformed rows land in
`unparsed` with a reason rather than raising; `TBD` and the blank placeholder
both become `""` and never a fabricated date; a duplicate id is reported, not
silently overwritten.

### 4.2 What each layer is for

| layer | proves | fails when |
|---|---|---|
| A | the shapes are read correctly | a rule is wrong |
| B | no language or project is named | someone hardcodes a label |
| C | malformed input degrades visibly | a parser guesses |
| §4.3 audit | nothing was silently dropped | coverage is overstated |
| §4.4 truth table | reconciliation logic | a case is mislabelled |
| §4.5 real data | it survives contact | the document is unusual |

### 4.3 The no-ground-truth problem

Nobody has labelled this document, so "did we extract the right 63 tasks?"
cannot be scored. The mitigation is the one this codebase already uses
elsewhere: **make the gap visible instead of measuring it.**

- `ProgressReport.unparsed` records every task-shaped line that did not become
  a record, with the reason (`no-id`, `in-fence`, `ragged-row`, `duplicate-id`).
- `tracelink progress --audit` prints it.
- A test asserts `unparsed` is populated on Fixture C. A silent drop is a bug
  even when the extracted records are all correct.

This is the same principle as `ExistingDocs` returning empty text rather than
disguising a gap, and `unverified` being a first-class verdict.

### 4.4 Reconciliation truth table

Eight synthetic triples, one per row of §3.4, built from a fixture corpus, a
fixture backlog and Fixture A. Assert the label for each. Then two more:

- an `unknown` case where retrieval found nothing — asserts rule 1 (no
  inference from absence)
- a `done` work item from a document classified `ungrounded` — asserts rule 2
  (it must come out `claimed-done-elsewhere`, never `agreed`)

### 4.5 Real-data regression, kept honest

`tests/test_structure.py` opens with: *anything asserting "173 tickets" would
fail on the next document and would be testing the fixture rather than the
system.* Respect that. Real-data checks go in a clearly separated
characterization test, and they assert **invariants**, not counts:

- every extracted timestamp parses, and `ended >= started` where both exist
- `done + open == len(items)`
- every `wid` is unique
- every item's `doc`/`line` points at a line that still contains its id
- ~~`len(unparsed) < 0.2 * len(items)`~~ **WRONG.** Measured 47%, because 64 of 68
  are `no-id` — rule R2 correctly refusing templates. Cap only the reasons that
  mean the parser failed, and assert separately that no `no-id` line carries a
  real identifier.

Counts go in one snapshot assertion, labelled as a snapshot and expected to
change: `63 items, 56 done, 7 open, 6 gates, 7 defects`.

**One end-to-end case is worth naming explicitly**, because all three
witnesses can be checked by hand:

> R02 (six tasks) is unchecked in the docs. The checklist blames R02 for two
> failing tests. `config.py:108` still reads `"sandbox_pw": "quandh14"`.
> Reconciliation must label this ~~`agreed-outstanding`~~ with the code anchor
> attached — not `agreed`, and not silence.

**WRONG about the label.** It comes out `tracker-optimistic`: tracker=done,
docs=open, code=absent. The tracker has no concept of R02, so the tickets reaching
those files are feature tickets marked done. The case holds and is sharper than
predicted.

If that one case comes out right, the pipeline is doing the thing it was
built for.

---

## 5. Order of work

1. Spike (§1). Stop if it fails.
2. Extract the shared markdown walker out of `featuremap_markdown` into
   `adapters/_markdown.py`; both adapters import it. Existing tests must stay
   green — this step changes no behaviour.
3. Fixtures A, B, C and their assertions. All red.
4. `adapters/progress_markdown.py` until green.
5. Artifacts + `rebuild_progress` + round-trip test.
6. `tracelink progress --docs DIR` with `--audit`; run it on the real tree and
   read the `unparsed` list end to end. Fix what it shows.
7. `reconcile.py` + the §4.4 truth table.
8. `tracelink reconcile`, then the §4.5 characterization test.
9. README section and a WORKLOG entry recording what the numbers came out as
   and what was wrong on the way.

Steps 1–6 stand alone and are worth shipping without 7–8.

---

## 6. Traps, from this pipeline's own history

Each of these already happened here, in the last two sessions:

- **A hardcoded list silently excluded the new thing.** `per_matcher` iterated
  a fixed `("stem","sym","prose")`, so the new `doc` matcher became the
  largest contributor in the run and never appeared in its own report. Any
  place that enumerates kinds of thing must read them off the data.
- **`glob` instead of `rglob`** made a describer see 3 files out of 39 and
  report "no documentation" about a project with a hundred pages of it.
- **A name that matches everything looks like total coverage.** `__init__`
  resolved to 166 constructors and made every section document every file.
  If a work item's deliverable is a bare common name, it must not pin.
- **Two plausible metrics that disagree must both be printed.** Line coverage
  said a file was 100% documented; symbol precision said 12%. Pick one and
  you have picked a story. For this stage the pair is *tasks closed* versus
  *tasks closed with evidence that resolves in the code*.
- **The console is not UTF-8.** A Vietnamese heading killed `drift` after its
  work was done. `cli._force_utf8_stdout()` exists; new commands get it free
  via `main()`, but anything run outside the CLI needs it.
- **Ungrounded documents are the majority here, not the exception.** 33 of 39.
  Any default that assumes documentation describes the current build will be
  wrong most of the time on this dataset.

---

## 7. Definition of done

- [ ] Spike run and recorded, on this document set and any other targeted
- [ ] Shared markdown walker extracted; pre-existing tests unchanged and green
- [ ] Fixtures A, B, C pass; B asserts structural identity with A
- [ ] `unparsed` populated and printed; a silent drop fails a test
- [ ] Round-trip through `progress.json` preserves every field
- [ ] Reconciliation truth table covers all 8 labels plus `unknown` and
      `claimed-done-elsewhere`
- [ ] The R02 case (§4.5) produces the right label with a code anchor
- [ ] Real-data invariants hold; counts recorded as a labelled snapshot
- [ ] Pipeline with no `progress.json` behaves exactly as before
- [ ] README stage table and run block updated; WORKLOG entry written
      including what came out wrong on the way
