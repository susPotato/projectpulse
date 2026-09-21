# Traceability run snapshots

Read by `app/api/tracelink_view.py` and served at `/traceability`.

**These are snapshots, not generated here.** Everything else under `data/` is
rebuilt inside the container by `scripts.replay`, which is why `data/` is in
`.dockerignore`. These cannot be: producing one costs real money (the demo run
was ~$22 including the architecture docs it stands on) and needs an API key the
container does not have. So they are committed as artifacts and copied into the
image.

Written by the `tracelink` pipeline, which lives in its own repository. Nothing
here imports it — the page reads these files as data, so ProjectPulse keeps
working whether or not that pipeline is installed anywhere nearby.

## What is here, and what is not

Kept: `run`, `tickets`, `corpus`, `candidates`, `verdicts`, `grounding`,
`links`, `explain`, `shadow`, `diagnosis`, `translations`, and the delivery
half — `progress`, `reconciliation`, `gates`.

`features.json` is left out with the index: 3.8 MB that only the retrieval
and reconciliation stages read, and free to rebuild from `corpus.json` and
the docs tree.

Left out on purpose:

* `index.json` — the inverted index. 216 KB that only the retrieval stage
  reads, and it can be rebuilt from `corpus.json` for free.
* `config.json` — the thresholds a run used. Useful when reproducing a run,
  not when reading one.
* `cache/` — raw model responses. Large, and re-deriving a verdict from them
  is a pipeline job, not a page's.

## Recreating one from scratch

Given a backlog export, a repository and a documentation tree, one command
produces a run and stages it here:

```bash
python -m tracelink --run runs/acme pipeline \
    --export ../backlog.xlsx \
    --repo ../acme \
    --docs ../acme/docs \
    --project Acme \
    --project-id excel:Project:upload:acme \
    --done-status "Release it" \
    --into <this repo>/projectpulse/traceability_runs/acme
```

That runs only the free stages, so the result has no verdicts; the page
names each absence and the command that fills it, and is worth reading
without them. The paid stages are printed at the end for whoever decides to
spend.

`--project-id` is what binds the run to a delivery project here. Without it
the run exists but no project page will find it.

## Refreshing

Copy the artifacts above from the pipeline's run directory. The page reads
whichever runs are present and asks for one by project id.

**One run per project id.** Adding a second directory that declares the same
`project_id` does not give you a picker — `run_for_project` returns the first
match in sorted order, so the new directory silently shadows the old one and
whatever it is missing (most expensively, `verdicts.json`) disappears from the
page with no warning. Either refresh a run in place, or give the new one a
different project.

The delivery artifacts must come from the *same* run as `candidates.json`:
`reconcile` joins work items to tickets through the candidate sets, so
mixing a new reconciliation with an old retrieval reports agreement that was
never measured.
