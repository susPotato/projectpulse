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
`links`, `explain`, `shadow`, `diagnosis`, `translations`.

Left out on purpose:

* `index.json` — the inverted index. 216 KB that only the retrieval stage
  reads, and it can be rebuilt from `corpus.json` for free.
* `config.json` — the thresholds a run used. Useful when reproducing a run,
  not when reading one.
* `cache/` — raw model responses. Large, and re-deriving a verdict from them
  is a pipeline job, not a page's.

## Refreshing

Copy the artifacts above from the pipeline's run directory. The page reads
whichever runs are present and asks for one by project id, so adding a second
run directory is enough to make it appear in the picker.
