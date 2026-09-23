"""Copy the traceability pipeline into this project's build context.

`fly deploy` is run from `projectpulse/`, so Docker's build context is this
directory and nothing above it. The pipeline lives at `../../traceability/`,
which is outside that context and therefore uncopyable - and the repository it
would otherwise be installed from is private, so a build-time
`pip install git+https://...` would need a credential passed into every deploy.

So a copy lives here, committed, and `Dockerfile` copies it into the image
like any other source. The deploy procedure in `DEPLOY.md` is unchanged:
`cd projectpulse && fly deploy`, no new step and no build secret.

**A second copy of a package will drift, and that is what this script and its
test are for.** `tests/test_vendored_tracelink.py` compares the two trees
whenever the upstream checkout is present and fails on any difference, so the
copy cannot quietly fall behind the pipeline it is a copy of - the same
arrangement `test_every_page_carries_the_same_rail` uses for the four copies
of the navigation rail.

    python -m scripts.vendor_tracelink            # report what differs
    python -m scripts.vendor_tracelink --apply    # make the copy current
"""

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import filecmp  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent.parent

#: Where the pipeline is checked out, relative to this repository. A sibling
#: of `hackathon/`, which is where the traceability work already lives.
UPSTREAM = HERE.parent.parent / "traceability" / "tracelink"

#: Where the copy goes. Top level of the build context so the image can
#: `COPY tracelink ./tracelink` and `import tracelink` resolves with no path
#: manipulation - `pyproject.toml`'s `packages.find` includes only `app*` and
#: `scripts*`, so this directory is deliberately not part of the distribution.
VENDORED = HERE / "tracelink"

#: Never copied: build artefacts and caches, which differ between machines and
#: would make the drift check fail for no reason.
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")


def _files(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
        and p.suffix not in {".pyc", ".pyo"}
    }


def differences() -> tuple[list[str], list[str], list[str]]:
    """(missing here, extra here, different content). Empty means in step."""
    if not UPSTREAM.is_dir():
        raise FileNotFoundError(UPSTREAM)
    if not VENDORED.is_dir():
        return sorted(_files(UPSTREAM)), [], []

    theirs, ours = _files(UPSTREAM), _files(VENDORED)
    missing = sorted(theirs - ours)
    extra = sorted(ours - theirs)
    changed = sorted(
        name for name in theirs & ours
        # shallow=False: a same-size, same-mtime file after a checkout is
        # exactly the case a shallow compare gets wrong.
        if not filecmp.cmp(UPSTREAM / name, VENDORED / name, shallow=False)
    )
    return missing, extra, changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="replace the copy; without it, only report")
    args = parser.parse_args(argv)

    if not UPSTREAM.is_dir():
        print(f"no pipeline checkout at {UPSTREAM}")
        print("Nothing to copy from. Clone the traceability repository beside")
        print("this one, or leave the committed copy alone.")
        return 1

    missing, extra, changed = differences()
    if not (missing or extra or changed):
        print(f"{VENDORED} is current with {UPSTREAM}")
        return 0

    for name in missing:
        print(f"  missing   {name}")
    for name in extra:
        print(f"  extra     {name}")
    for name in changed:
        print(f"  differs   {name}")

    if not args.apply:
        print(f"\n{len(missing) + len(extra) + len(changed)} file(s) out of step.")
        print("Run with --apply to update the copy, then commit it.")
        return 1

    # Replaced wholesale rather than merged: a file deleted upstream has to
    # disappear here too, and a copy that only ever gains files is how a
    # deleted module keeps being importable in the image long after it is gone.
    if VENDORED.is_dir():
        shutil.rmtree(VENDORED)
    shutil.copytree(UPSTREAM, VENDORED, ignore=IGNORE)
    print(f"\ncopied {len(_files(VENDORED))} file(s) into {VENDORED}")
    print("Commit it - the image is built from this copy, not from upstream.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
