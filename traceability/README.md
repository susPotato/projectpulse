# Stage 1 — Jira ↔ code candidate retrieval

Deterministic retrieval that narrows a codebase to a handful of candidate files per
Jira ticket. **No LLM, no API key, no embeddings.** Runs in seconds.

This is the scaffolding the expensive stages stand on: it does not decide whether a
ticket's claim is true — it decides *where to look*.

## Run

```bash
python extract_tickets.py "../Jira Cowork Local_0913.xlsx" tickets.json
python evidence_index.py evidence_index.json ../pimsathon-main
python stage1_match.py .
```

Outputs `stage1_results.json` — per ticket: matched files, which matcher fired,
the matching token, and its document frequency.

## How it works

**Ticket side.** The 173 real feature tickets are unkeyed sub-rows of the 17 PM
tickets in the xlsx (`Key` column empty) — `extract_tickets.py` pulls those out.

**Code side.** Every file is classified by *role*, because they need different
treatment:

| Role | n | Treatment |
|---|---:|---|
| `logic` | 144 | AST → class/function names |
| `declarative` | 8 | YAML keys + prose content words |
| `test` | 10 | indexed as evidence |
| `data` | 5 | path tokens only (`i18n.py` is 9,649 strings / 4 functions) |
| `hubdoc` | 4 | **excluded** — describe everything, discriminate nothing |
| `vendored` | 1 | **excluded** — `assets/d3.min.js` is not their code |

**Three matchers**, all IDF-weighted (a token in ≤N files is informative; one in 30
is noise):

- `stem` — ticket title words ∩ filename words. Highest precision, does most work.
- `sym`  — ticket title words ∩ class/function names. Noisier, harder rarity bar (≤3).
- `prose`— ticket text ∩ declarative-file content words, rare-word overlap with
  length normalisation. Finds features that are *declared, not coded*.

## Results (pimsathon-main, 173 tickets)

| | |
|---|---:|
| strong candidate set | **143 (82.7%)** |
| weak only | 15 (8.7%) |
| nothing | 15 (8.7%) |
| median candidate-set size | **3 files** (of 148) |

## Known limitations

- **No ground truth**, so precision is spot-checked by hand, not measured.
- **Head-only matching.** Only the title before the `—` is used, so tickets sharing
  a head (`Dashboard — …` ×6, `Workspace — …` ×5) get near-identical candidates.
  The discriminating detail lives in the tail and needs Stage 2/3.
- **`sym` matcher fires on generic words** — `Notification Center` matched a canvas
  `center()` method. Sole evidence for 12 tickets; treat those as low confidence.
- **Absence proves nothing.** A ticket with no candidate is *not* evidence the
  feature is missing — `Console` is implemented in `ui/terminal_panel.py` and scores
  zero, because exact matching cannot bridge synonyms.

## Traps found the hard way

1. **Hub documents.** `assets/RULEBASE.md` (27 KB) matched 134/173 tickets before it
   was excluded — a big catch-all doc matches everything and means nothing.
2. **Mixing token sources.** Folding 1,697 function-name tokens into the filename
   index inflated document frequency and pushed good stem matches below the rarity
   bar. Index them separately with separate thresholds.
3. **Rarity is relative to corpus size.** `df ≤ 4` sounds strict; against 13 prose
   files it means "appears in 31% of the corpus".
