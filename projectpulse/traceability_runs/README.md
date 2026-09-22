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
`links`, `explain`, `shadow`, `diagnosis`, `translations`, the delivery
half — `progress`, `reconciliation`, `gates` — and the two readings of the
backlog itself, `cohorts` and `governance`.

The list is exactly `tracelink_view.PRODUCED_BY`, and a stage added there
has to be copied here or the page reports it as a gap. Both of the last two
are free to produce — neither calls a model — so a refresh does not cost
what the first run did.

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

This is not a one-off. A backlog moves, a repository moves, and a page
showing last month's answer looks exactly like one showing this morning's.
The refresh is the same command as the first run, pointed at the same run
directory — stages are independent, so only what changed is recomputed:

```bash
python -m tracelink --run runs/demo pipeline \
    --export ../Jira\ Cowork\ Local_0913.xlsx \
    --repo https://github.com/example/coworklocal --ref main \
    --docs ../hackathon/docs \
    --project CoWorkLocal \
    --project-id excel:Project:upload:cowork-local \
    --done-status "Release it" \
    --into <this repo>/projectpulse/traceability_runs/demo
```

`--repo` takes a checkout **or** a git URL. A URL is cloned shallowly into
`~/.tracelink/sources` and fetched rather than re-downloaded next time; a
local checkout is read in place and never fetched, pulled or checked out,
because moving somebody's working tree while they are in it is the rudest
thing a tool can do.

**Every run records the revision it read.** `corpus.json`'s meta carries the
commit, the branch and whether the tree was dirty. That is what makes the
next question answerable:

```bash
python -m tracelink --run runs/demo stale
```

`stale` reports two different kinds of out-of-date. Artifacts that no longer
match the inputs they were built from — free stages name the command that
fixes them, paid ones are named and left to you. And, separately, whether
the **code itself** has moved since the run: every artifact can agree with
every other and all of them be about a commit from last week, which nothing
that compares artifacts to each other can see.

The paid stages (`translate`, `adjudicate`, `explain`) are never re-run
automatically. A refresh of the free half costs nothing and is worth doing
often; the rest is a decision with a number attached, and `cost` prints it.

Copy the artifacts above from the pipeline's run directory, or let `--into`
do it. The page reads whichever runs are present and asks for one by
project id.

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
