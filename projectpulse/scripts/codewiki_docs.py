"""Generate a documentation tree for a repository that has none, with CodeWiki.

Run by `app/codewiki_docs.py` when somebody registering a repository on
Settings > Sources is told it has no `docs/` and chooses to generate them. It
writes into the clone's own docs directory. The pipeline's documentation
stages read markdown, and CodeWiki writes markdown from the code - so a
project with no documents can still be traced, against documents derived
from its source.

**Standalone on purpose.** It imports nothing from `app`: the interpreter that
runs it is whichever one has CodeWiki's generator installed
(`PULSE_CODEWIKI_PYTHON`), which on a host is usually a separate venv without
this app's dependencies. In the image both are the same interpreter.

**Configured from the environment, never from `~/.codewiki`.** CodeWiki's own
CLI reads a per-user config file and the OS keyring; a server running several
projects cannot share one of those. The adapter underneath the CLI takes a
plain dict, and that is what this builds:

    CODEWIKI_BASE_URL        OpenAI-compatible endpoint (FPT's gateway, say)
    CODEWIKI_API_KEY         its credential
    CODEWIKI_MODEL           main model; also used for clustering
    CODEWIKI_FALLBACK_MODEL  tried when the main model fails a call

**Resumable.** CodeWiki skips every module whose page already exists, so a run
that stopped half way - a rate limit, a closed laptop - continues where it was
rather than paying for the finished pages again. When the commit moved since
the last generation, only the modules holding changed files are regenerated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

#: The same steering the CoWorkLocal run used (traceability/codewiki-run.log):
#: the traceability stages match ticket text against documented capabilities,
#: so many small capability statements with file anchors beat broad summaries.
INSTRUCTIONS = (
    "For each module, enumerate the concrete capabilities it implements as "
    "separate bullets. State each capability as one sentence of plain behaviour "
    "language describing what a user or caller can do, then name the specific "
    "files and class/function symbols that implement it. Prefer many small "
    "capability statements over broad module summaries. Explicitly note "
    "capabilities that are declared in configuration or data files rather than "
    "implemented in code."
)

EXCLUDE = ["tests", "test", "__pycache__", "node_modules", ".git", "dist", "build"]


def _previous_commit(out: Path) -> str:
    try:
        meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        return str(meta.get("generation_info", {}).get("commit_id") or "")
    except (OSError, ValueError):
        return ""


def _changed_files(repo: Path, before: str, after: str) -> list[str] | None:
    """`git diff --name-only`, or None when git cannot say.

    Plain git rather than CodeWiki's `_detect_changed_files`, which needs
    GitPython - a dependency the image deliberately does not carry.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", before, after],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line.strip() for line in done.stdout.splitlines() if line.strip()]


def _invalidate(repo: Path, out: Path, commit: str) -> None:
    """Drop the pages a commit move made stale, so the resume rewrites them."""
    before = _previous_commit(out)
    if not before or not commit or before == commit:
        return
    changed = _changed_files(repo, before, commit)
    if changed is None:
        print(f"codewiki: cannot diff {before[:12]}..{commit[:12]}; "
              f"keeping the existing pages", flush=True)
        return
    print(f"codewiki: {len(changed)} files changed since {before[:12]}", flush=True)
    if not changed:
        return
    # CodeWiki's own `_invalidate_affected_modules`, restated: importing it
    # drags in the CLI's config manager and `keyring`, which the image lacks.
    try:
        tree = json.loads((out / "module_tree.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    stale: set[str] = set()

    def walk(nodes: dict, parents: list[str]) -> None:
        for name, info in nodes.items():
            if any(f in c or c in f for c in info.get("components", []) for f in changed):
                stale.update([name, *parents])
            walk(info.get("children") or {}, [*parents, name])

    walk(tree, [])
    if stale:
        stale.add("overview")
    for name in stale:
        (out / f"{name}.md").unlink(missing_ok=True)
    print(f"codewiki: {len(stale)} pages to rewrite", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--commit", default="")
    args = parser.parse_args(argv)

    base_url = os.environ.get("CODEWIKI_BASE_URL", "").strip()
    api_key = os.environ.get("CODEWIKI_API_KEY", "").strip()
    model = os.environ.get("CODEWIKI_MODEL", "").strip()
    fallback = os.environ.get("CODEWIKI_FALLBACK_MODEL", "").strip() or model
    if not (base_url and api_key and model):
        print("codewiki: CODEWIKI_BASE_URL, CODEWIKI_API_KEY and CODEWIKI_MODEL "
              "must all be set. Choose a model and save its key on /llm.",
              file=sys.stderr, flush=True)
        return 2

    print(f"=== codewiki ({model})", flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    _invalidate(args.repo, args.out, args.commit)

    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator

    config = {
        "base_url": base_url,
        "api_key": api_key,
        "main_model": model,
        "cluster_model": model,
        "fallback_model": fallback,
        "provider": "openai-compatible",
        "max_tokens": 32768,
        "max_token_per_leaf_module": 16000,
        # Anthropic-style cache markers. An OpenAI-shaped gateway in front of
        # open models rejects them, and CodeWiki's fallback is a failed call
        # retried - so do not send them in the first place.
        "prompt_caching": False,
        "agent_instructions": {
            "doc_type": "architecture",
            "custom_instructions": INSTRUCTIONS,
            "exclude_patterns": EXCLUDE,
        },
    }
    generator = CLIDocumentationGenerator(
        repo_path=args.repo.resolve(), output_dir=args.out.resolve(),
        config=config, verbose=True, commit_id=args.commit or None,
    )
    try:
        generator.generate()
    except Exception as exc:  # noqa: BLE001 - reported, and the pages stay
        pages = len(list(args.out.glob("*.md")))
        print(f"codewiki: stopped with {pages} pages written: {exc}. Generating "
              f"again resumes from here.", file=sys.stderr, flush=True)
        return 1

    pages = len(list(args.out.glob("*.md")))
    if not (args.out / "overview.md").is_file():
        print(f"codewiki: finished without an overview ({pages} pages). Generating "
              f"again resumes from here.", file=sys.stderr, flush=True)
        return 1
    print(f"codewiki: {pages} pages in {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
