"""Contract tests for Stage 0e — progress documentation as delivery data.

Written before the adapter. The fixtures are the specification.

Three of these matter more than the rest:

* `test_a_template_is_not_a_backlog` — the trap the spike found. A naive
  checkbox count reported 14 open tasks on the real document when the truth
  was 7; the other seven were a blank Definition-of-Done pro-forma.
* `test_other_language_yields_identical_structure` — the whole of design
  rule 1 in one assertion. It fails the moment anybody hardcodes `Start:`.
* `test_malformed_input_is_recorded_not_dropped` — there is no ground truth
  for this extraction, so the only defence against silently losing half a
  document is that losing anything is itself reported.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.adapters.progress_markdown import read_progress
from tracelink.artifacts import (
    Corpus, SourceFile, Symbol, ROLE_CODE, rebuild_progress,
)


# --------------------------------------------------------------------------
# Fixture A — every shape the adapter must read, small enough to assert
# --------------------------------------------------------------------------

PLAIN = """\
# Widget Programme

## Phase One

- [x] **AA-01 (Alice)**: Build the card renderer ➔ `render_card()`
  *Start: `2026-03-02 09:00` | End: `2026-03-02 11:30`*
- [x] **AA-02 (Alice)**: Wire the totals ➔ `compute_total()`
  *Start: `2026-03-02 11:30` | End: `2026-03-03 09:15`*
- [ ] **AA-03 (Bob)**: Replace the legacy exporter ➔ `legacy_export()`
  *Start: `2026-03-04 09:00` | End: `2026-03-04 10:00`*
- [x] **AA-04 (Bob)**: Delete the shim ➔ `no_such_symbol()`

## Phase Two

- [x] **AA-05 (Cara)**: Cache the lookups ➔ `warm_cache()`
  *Start: `2026-03-05 09:00` | End: `2026-03-05 12:00`*
- [ ] **AA-06 (Cara)**: Ship the docs

## Daily plan

| Day | Task | Start | End | State |
|---|---|---|---|---|
| **02/03** | Card renderer and totals | `2026-03-02 09:00` | `2026-03-03 09:15` | [x] |
| **04/03** | Legacy exporter | `2026-03-06 14:00` | `2026-03-06 18:00` | [ ] |

## Gates

| When | Gate | Command | Criterion | Owner | Start | End | State |
|---|---|---|---|---|---|---|---|
| **05/03** | Size | `python check_loc.py --max 400` | no file over 400 lines | Bob | `2026-03-05 17:00` | `2026-03-05 17:05` | [x] |
| **06/03** | Secrets | `python audit_secrets.py` | no plaintext secret | Cara | `____-__-__ __:__` | `____-__-__ __:__` | [ ] |

## Defects

| ID | Found | Area | Kind | Status | Owner |
|---|---|---|---|---|---|
| `DD-001` | 2026-03-01 | `render_card()` | Rendering | open | Alice |
| `DD-002` | 2026-03-01 | `compute_total()` | Arithmetic | fixed | Alice |
| `DD-003` | 2026-03-02 | `legacy_export()` | Duplication | open | Bob |

### DD-001 in detail

* **Symptom**: the card renders blank.
* **Root cause**: the totals arrive after first paint.
* **Prevention**: never read a total during construction.

## Definition of Done

Copy this into every pull request:

- [ ] tests pass
- [ ] documentation updated
- [ ] reviewed by someone else
- [ ] merged to the default branch
"""


# --------------------------------------------------------------------------
# Fixture B — Fixture A with every label changed and nothing else
# --------------------------------------------------------------------------

OTHER_LANGUAGE = """\
# Programma Widget

## Fase Uno

- [x] **ZZ_101 (Alice)**: Costruire il renderer ➔ `render_card()`
  *Inizio: `2026-03-02 09:00` | Fine: `2026-03-02 11:30`*
- [x] **ZZ_102 (Alice)**: Collegare i totali ➔ `compute_total()`
  *Inizio: `2026-03-02 11:30` | Fine: `2026-03-03 09:15`*
- [ ] **ZZ_103 (Bob)**: Sostituire l'esportatore ➔ `legacy_export()`
  *Inizio: `2026-03-04 09:00` | Fine: `2026-03-04 10:00`*
- [x] **ZZ_104 (Bob)**: Eliminare lo shim ➔ `no_such_symbol()`

## Fase Due

- [x] **ZZ_105 (Cara)**: Memorizzare le ricerche ➔ `warm_cache()`
  *Inizio: `2026-03-05 09:00` | Fine: `2026-03-05 12:00`*
- [ ] **ZZ_106 (Cara)**: Pubblicare la documentazione

## Piano giornaliero

| Giorno | Attività | Inizio | Fine | Stato |
|---|---|---|---|---|
| **02/03** | Renderer e totali | `2026-03-02 09:00` | `2026-03-03 09:15` | [x] |
| **04/03** | Esportatore | `2026-03-06 14:00` | `2026-03-06 18:00` | [ ] |

## Controlli

| Quando | Controllo | Comando | Criterio | Responsabile | Inizio | Fine | Stato |
|---|---|---|---|---|---|---|---|
| **05/03** | Dimensione | `python check_loc.py --max 400` | nessun file oltre 400 righe | Bob | `2026-03-05 17:00` | `2026-03-05 17:05` | [x] |
| **06/03** | Segreti | `python audit_secrets.py` | nessun segreto in chiaro | Cara | `____-__-__ __:__` | `____-__-__ __:__` | [ ] |

## Difetti

| ID | Trovato | Area | Tipo | Stato | Responsabile |
|---|---|---|---|---|---|
| `EE_001` | 2026-03-01 | `render_card()` | Rendering | aperto | Alice |
| `EE_002` | 2026-03-01 | `compute_total()` | Aritmetica | risolto | Alice |
| `EE_003` | 2026-03-02 | `legacy_export()` | Duplicazione | aperto | Bob |

### EE_001 nel dettaglio

* **Sintomo**: la scheda è vuota.
* **Causa**: i totali arrivano dopo il primo disegno.
* **Prevenzione**: mai leggere un totale durante la costruzione.

## Definizione di Fatto

Copiare in ogni richiesta di modifica:

- [ ] i test passano
- [ ] documentazione aggiornata
- [ ] revisionato da un altro
- [ ] unito al ramo predefinito
"""


# --------------------------------------------------------------------------
# Fixture C — the shapes that break parsers
# --------------------------------------------------------------------------

HOSTILE = """\
# Hostile

## Fenced

```markdown
| Day | Task | State |
|---|---|---|
| **01/01** | not a real row | [x] |
- [x] **CC-01**: not a real task
```

## Real

- [x] **CC-02**: a real task
  *Start: `2026-04-01 09:00` | End: `2026-04-01 10:00`*
- [x] **CC-02**: a duplicate id
- [ ] no identifier here at all
- [x] **CC-03**: dates that are not dates
  *Start: `TBD` | End: `____-__-__ __:__`*

See CC-02 for context; it is mentioned in prose and must not become a task.

## Ragged

| A | B | State |
|---|---|---|
| **CC-04** | fine | [x] |
| **CC-05** | missing a cell | [ ]
| **CC-06** | extra | cell | here | [ ] |
"""


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    return Corpus(
        root=str(tmp_path),
        files=[SourceFile(path="cards.py", role=ROLE_CODE, size=900,
                          language="python")],
        symbols=[
            Symbol(sid="cards.py::render_card", path="cards.py",
                   name="render_card", kind="function", line=10, end_line=40),
            Symbol(sid="cards.py::compute_total", path="cards.py",
                   name="compute_total", kind="function", line=44, end_line=70),
            Symbol(sid="cards.py::legacy_export", path="cards.py",
                   name="legacy_export", kind="function", line=80, end_line=95),
            Symbol(sid="cards.py::warm_cache", path="cards.py",
                   name="warm_cache", kind="function", line=100, end_line=120),
        ],
    )


def _docs(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "docs"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(body, encoding="utf-8")
    return d


@pytest.fixture
def plain(tmp_path: Path):
    return read_progress(_docs(tmp_path, "plan.md", PLAIN))


# --------------------------------------------------------------------------
# Work items
# --------------------------------------------------------------------------

def test_work_items_are_read_with_state_and_owner(plain):
    assert len(plain.items) == 6
    assert sum(1 for i in plain.items if i.done) == 4
    assert sum(1 for i in plain.items if not i.done) == 2
    first = next(i for i in plain.items if i.wid == "AA-01")
    assert first.owner == "Alice"
    assert "card renderer" in first.title
    assert first.group.endswith("Phase One")
    assert first.line > 0


def test_a_template_is_not_a_backlog(plain):
    """Four unchecked boxes in the Definition of Done are a blank form.

    Counted as work, they double the reported backlog. The generic
    discriminator is that real work items carry an identifier.
    """
    assert len(plain.items) == 6, "the four template boxes must not be tasks"
    assert not any("tests pass" in i.title for i in plain.items)
    reasons = {u["reason"] for u in plain.unparsed}
    assert "no-id" in reasons, "and their exclusion must be visible, not silent"


def test_timestamps_are_extracted_without_being_told_the_label(plain):
    item = next(i for i in plain.items if i.wid == "AA-02")
    assert item.started == "2026-03-02 11:30"
    assert item.ended == "2026-03-03 09:15"


def test_an_item_with_no_dates_is_empty_not_guessed(plain):
    item = next(i for i in plain.items if i.wid == "AA-06")
    assert item.started == "" and item.ended == ""


def test_deliverables_become_claims(plain, corpus):
    from tracelink.features import resolve_claims

    resolve_claims([c for i in plain.items for c in i.deliverables], corpus)
    got = {c.name: c.resolved for i in plain.items for c in i.deliverables}
    assert got.get("render_card") is True
    assert got.get("no_such_symbol") is False


# --------------------------------------------------------------------------
# What a task produced, versus what it merely mentioned
# --------------------------------------------------------------------------

ARROWED = """\
# Build

## Phase

- [x] **BB-01**: stop `LegacyLib` leaking into the core ➔ `guard.py`
- [x] **BB-02**: wrap `OldClient` and `OtherLib` ➔ `wrapper.py`
- [x] **BB-03**: delete the shim ➔ `cleanup.py`
- [x] **BB-04**: tidy up ➔ `tidy.py`
"""

UNARROWED = """\
# Build

## Phase

- [x] **CC-01**: stop `LegacyLib` leaking, see `guard.py`
- [x] **CC-02**: wrap `OldClient`, see `wrapper.py`
- [x] **CC-03**: delete the shim in `cleanup.py`
- [x] **CC-04**: tidy `tidy.py`
"""


def test_a_deliverable_is_what_comes_after_the_arrow(tmp_path):
    """Otherwise every dependency named in passing is code we are missing.

    `PySide6` was reported as a missing deliverable of a task whose actual
    output was `scripts/check_imports.py`: the task's job was to *ban* that
    import. The arrow is how the author already distinguished the two.
    """
    r = read_progress(_docs(tmp_path, "plan.md", ARROWED))
    first = next(i for i in r.items if i.wid == "BB-01")
    assert [c.name for c in first.deliverables] == ["guard.py"]
    assert [c.name for c in first.mentions] == ["LegacyLib"]


def test_nothing_is_discarded_by_the_split(tmp_path):
    r = read_progress(_docs(tmp_path, "plan.md", ARROWED))
    second = next(i for i in r.items if i.wid == "BB-02")
    assert {c.name for c in second.mentions} == {"OldClient", "OtherLib"}


def test_the_convention_is_learned_and_not_assumed(tmp_path):
    """A document that does not use arrows keeps every span as a deliverable."""
    r = read_progress(_docs(tmp_path, "plan.md", UNARROWED))
    first = next(i for i in r.items if i.wid == "CC-01")
    assert {c.name for c in first.deliverables} == {"LegacyLib", "guard.py"}
    assert first.mentions == []


# --------------------------------------------------------------------------
# Schedule: planned against actual
# --------------------------------------------------------------------------

def test_dateless_rows_become_schedule_entries_not_tasks(plain):
    """The daily plan is a second view of the same work, not more of it.

    Emitting those rows as work items would double-count every task; and
    dropping them would lose the only record of planned-versus-actual,
    which is where schedule slip lives.
    """
    assert len(plain.schedule) == 2
    assert len(plain.items) == 6
    slipped = next(s for s in plain.schedule if not s.done)
    assert slipped.planned == "04/03"
    assert slipped.started == "2026-03-06 14:00"


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def test_a_row_carrying_a_command_is_a_gate(plain):
    assert len(plain.gates) == 2
    assert all(g.command for g in plain.gates)


def test_an_unsigned_gate_is_reported_as_unsigned(plain):
    unsigned = [g for g in plain.gates if not g.signed_off]
    assert len(unsigned) == 1
    assert unsigned[0].started == "", "a blank placeholder is not a date"


def test_unclassified_columns_survive_under_their_own_headers(plain):
    """We keep the author's vocabulary for anything we cannot classify.

    Guessing which column is "the criterion" needs the document's language.
    Recording it under the name the author gave it does not.
    """
    gate = next(g for g in plain.gates if g.signed_off)
    assert gate.fields.get("Criterion") == "no file over 400 lines"
    assert gate.fields.get("Owner") == "Bob"


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------

def test_an_id_table_without_checkboxes_is_a_register(plain):
    assert len(plain.register) == 3
    d = next(d for d in plain.register if d.rid == "DD-001")
    assert d.found == "2026-03-01"
    assert d.fields.get("Status") == "open"
    assert "render_card" in {c.name for c in d.anchors}


def test_labelled_prose_under_a_defect_is_kept_verbatim(plain):
    d = next(d for d in plain.register if d.rid == "DD-001")
    assert d.detail.get("Root cause", "").startswith("the totals arrive")
    assert "Prevention" in d.detail


# --------------------------------------------------------------------------
# Design rule 1, in one test
# --------------------------------------------------------------------------

def test_other_language_yields_identical_structure(tmp_path):
    a = read_progress(_docs(tmp_path / "a", "plan.md", PLAIN))
    b = read_progress(_docs(tmp_path / "b", "piano.md", OTHER_LANGUAGE))

    assert len(a.items) == len(b.items)
    assert ([i.done for i in a.items]) == ([i.done for i in b.items])
    assert ([i.started for i in a.items]) == ([i.started for i in b.items])
    assert ([i.owner for i in a.items]) == ([i.owner for i in b.items])
    assert len(a.schedule) == len(b.schedule)
    assert ([s.planned for s in a.schedule]) == ([s.planned for s in b.schedule])
    assert len(a.gates) == len(b.gates)
    assert ([g.signed_off for g in a.gates]) == ([g.signed_off for g in b.gates])
    assert ([g.command for g in a.gates]) == ([g.command for g in b.gates])
    assert len(a.register) == len(b.register)
    assert ([d.found for d in a.register]) == ([d.found for d in b.register])


# --------------------------------------------------------------------------
# Hostile input
# --------------------------------------------------------------------------

@pytest.fixture
def hostile(tmp_path: Path):
    return read_progress(_docs(tmp_path, "hostile.md", HOSTILE))


def test_nothing_inside_a_fence_is_extracted(hostile):
    assert "CC-01" not in {i.wid for i in hostile.items}
    assert not any(s.title == "not a real row" for s in hostile.schedule)


def test_an_id_mentioned_in_prose_is_not_a_task(hostile):
    assert sum(1 for i in hostile.items if i.wid == "CC-02") <= 1


def test_a_duplicate_id_is_reported_not_overwritten(hostile):
    reasons = {u["reason"] for u in hostile.unparsed}
    assert "duplicate-id" in reasons


def test_placeholder_dates_never_become_dates(hostile):
    item = next(i for i in hostile.items if i.wid == "CC-03")
    assert item.started == "" and item.ended == ""


def test_malformed_input_is_recorded_not_dropped(hostile):
    """There is no ground truth here, so a silent loss is undetectable.

    The defence is that anything the adapter declines to read is listed
    with a reason, the way `ExistingDocs` returns empty rather than
    disguising a gap.
    """
    assert hostile.unparsed, "hostile input must produce a report"
    for u in hostile.unparsed:
        assert u["reason"] and u["doc"] and u["line"] > 0


def test_a_ragged_table_does_not_raise(hostile):
    assert isinstance(hostile.items, list)


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------

def test_progress_survives_save_and_reload(plain, tmp_path):
    from tracelink.artifacts import load_payload, save

    p = save(tmp_path / "progress.json", "progress", {
        "root": plain.root, "items": plain.items, "schedule": plain.schedule,
        "gates": plain.gates, "register": plain.register,
        "unparsed": plain.unparsed,
    })
    back = rebuild_progress(load_payload(p, "progress"))
    assert len(back.items) == len(plain.items)
    assert len(back.gates) == len(plain.gates)
    assert back.register[0].detail == plain.register[0].detail
    assert [c.name for c in back.items[0].deliverables] == \
           [c.name for c in plain.items[0].deliverables]


def test_an_empty_directory_is_empty_not_an_error(tmp_path):
    d = tmp_path / "nothing"
    d.mkdir()
    r = read_progress(d)
    assert r.items == [] and r.gates == [] and r.register == []
