"""Download the advisory duration classifier from Hugging Face.

    python -m scripts.fetch_model

A separate command, run once, rather than anything automatic. Three reasons,
and the third is the one that matters:

* it is a multi-megabyte binary this repo has no business carrying;
* a judge running the demo needs neither it nor scikit-learn;
* an app that reaches out to a third-party host on first boot is a surprise,
  and surprises in a deployment are how a demo fails at the wrong moment.

Nothing else calls this. `app.ml.duration.load_classifier()` returns None while
the artefact is absent, and every caller treats that as "no duration advice",
which is a complete state rather than a degraded one.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

from app.config import settings  # noqa: E402
from app.ml.duration import ARTEFACT_NAME, HF_FILENAME, HF_REPO  # noqa: E402


def fetch(destination: Path, repo: str, filename: str) -> Path:
    """Copy one file out of a Hugging Face repo into `destination`."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print(
            "huggingface_hub is not installed.\n"
            '  pip install -e ".[ml-fetch]"',
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    print(f"downloading {filename} from {repo} ...")
    cached = hf_hub_download(repo_id=repo, filename=filename)

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Copied out of the hub cache rather than symlinked: the cache is not a
    # stable location, and a broken link would present as a corrupt model.
    shutil.copyfile(cached, destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=HF_REPO)
    parser.add_argument(
        "--filename",
        default=HF_FILENAME,
        help="path inside the repo, which is not the local filename",
    )
    parser.add_argument(
        "--out",
        default="",
        help="where to write it (default: PULSE_MODEL_ROOT/<filename>)",
    )
    args = parser.parse_args(argv)

    # `args.filename` is a repo path; only its basename becomes a local file, or
    # the download would land in `models/models/...`.
    destination = (
        Path(args.out)
        if args.out
        else Path(settings.model_root) / Path(args.filename).name
    )
    if destination.exists():
        print(f"already present: {destination}")
        return 0

    written = fetch(destination, args.repo, args.filename)
    size = written.stat().st_size
    print(f"wrote {written}  ({size} bytes)")
    print(
        "\nThe classifier is advisory only. It returns one of Short / Standard /\n"
        "Long-running and never a number of days, and nothing under\n"
        "app/intelligence/ is permitted to import it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
