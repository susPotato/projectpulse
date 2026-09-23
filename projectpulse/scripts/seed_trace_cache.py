"""Give a project's traceability run the verdict cache an earlier run produced.

    python -m scripts.seed_trace_cache --from demo --project excel:Project:upload:cowork-local
    python -m scripts.seed_trace_cache --from demo --project ... --apply

Reports without copying until `--apply`, like every other script here.

**Why this exists.** `tracelink adjudicate` calls a model once per ticket and
caches every response on disk under `<run>/cache/<key>.json`, keyed by a hash
of the prompt version, the model and the exact prompt
(`tracelink/adjudicate.py#cache_key`). `app/traceability_run.py` names the run
directory after the project, so a *second* run of the same project already
reads the first one's cache and bills nothing. The gap is the *first* run for
a newly registered project: its directory is empty, so every ticket is a miss
and a demo of the Run button either costs real money or, with no credentials
set, produces no verdicts at all.

Copying the cache across closes that gap. **It is not a way to fake a run.**
A cache hit requires the prompt to match byte for byte, which means the same
model, the same ticket text and the same candidate files - so what comes back
is the answer that was genuinely computed for that exact input, and anything
that has actually changed still misses and is still reported as such. That is
the difference between reusing a computation and pretending to do one.

**The hit rate is the thing to check, and this script cannot promise it.**
Candidates come from `retrieve` over the corpus, so a different commit of the
repository or a different export produces different prompts and therefore
different keys. Run it, then read `verdicts.json` - the number of tickets with
a verdict against the number of tickets is the real answer.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from app.traceability_run import runs_root  # noqa: E402


def _resolve(root: Path, spec: str) -> Path:
    """A run named by directory name, or by path. Directory name wins.

    Both spellings are useful - `--from demo` while looking at the page's own
    list, `--from ../traceability/runs/demo` while looking at a shell - and
    guessing wrong is cheap to rule out here rather than in a copy that lands
    somewhere surprising.
    """
    candidate = root / spec
    if candidate.is_dir():
        return candidate
    direct = Path(spec).expanduser()
    if direct.is_dir():
        return direct
    raise SystemExit(
        f"no run directory called {spec!r}. Looked in {root} and at the path "
        f"as given. Available: "
        f"{', '.join(sorted(p.name for p in root.iterdir() if p.is_dir())) or 'none'}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", required=True,
                        help="the run to copy the cache from, e.g. `demo`")
    parser.add_argument("--project", required=True,
                        help="the delivery project id the run is for, e.g. "
                             "excel:Project:upload:cowork-local")
    parser.add_argument("--apply", action="store_true",
                        help="actually copy; without it this only reports")
    args = parser.parse_args(argv)

    # Imported here rather than at module scope: `_slug` is private to that
    # module and this is the one caller that has any business knowing it.
    from app.traceability_run import _slug

    root = runs_root()
    source = _resolve(root, args.source)
    target = root / _slug(args.project)

    src_cache = source / "cache"
    if not src_cache.is_dir():
        raise SystemExit(
            f"{source} has no cache/ directory, so there is nothing to copy. "
            f"Only a run whose `adjudicate` stage has been run has one."
        )

    entries = sorted(src_cache.glob("*.json"))
    if not entries:
        raise SystemExit(f"{src_cache} is empty, so there is nothing to copy.")

    dst_cache = target / "cache"
    existing = {p.name for p in dst_cache.glob("*.json")} if dst_cache.is_dir() else set()
    new = [p for p in entries if p.name not in existing]

    print(f"source  {source}")
    print(f"target  {target}")
    print(f"        {len(entries)} cached verdicts, {len(existing)} already there, "
          f"{len(new)} to copy")

    if source.resolve() == target.resolve():
        raise SystemExit(
            "source and target are the same directory - this project's run "
            "already owns that cache and copying it onto itself would do "
            "nothing."
        )

    if not args.apply:
        print("\ndry run. Add --apply to copy.")
        return 0

    dst_cache.mkdir(parents=True, exist_ok=True)
    for entry in new:
        # `copy2` rather than `copy`: the modification time is the only clue
        # anybody has about when a cached answer was produced, and a fresh
        # timestamp on a month-old verdict is a small lie told to the next
        # person who looks.
        shutil.copy2(entry, dst_cache / entry.name)

    print(f"\ncopied {len(new)} entries into {dst_cache}")
    print("A run for this project will now serve those verdicts from cache "
          "instead of calling the model. Any ticket whose prompt has changed "
          "still misses, and still says so.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
