"""The deployed image must build corpora with CodeWiki, not the fallback.

`tracelink/corpus.py` prefers CodeWiki's dependency analyser and drops to a
Python-only AST walk when it cannot be imported. That fallback is deliberate
and correct for a laptop without CodeWiki installed - but it fails *silently*:
`codewiki_symbols` catches everything, logs at INFO and returns None. A server
missing the package serves every page normally and quietly produces a corpus
with no edges and no non-Python symbols, and the only evidence is
`analyzer: "python-ast"` inside corpus.json.

That is the same class of defect as the `/models/` gitignore in `.gitignore`:
something that only ever *subtracts* from a result, with nothing red to show
for it. So it gets a test.

Two of them, with different reach:

* `test_corpus_uses_codewiki_when_it_is_installed` runs the real analyser and
  is skipped where CodeWiki is absent. It catches the case a static check
  cannot - the package installed but broken, which is what a missing
  transitive dependency looks like after `pip install --no-deps`.
* `test_the_image_installs_the_analyser` reads the Dockerfile and runs
  everywhere, including on a machine that has never seen CodeWiki. It is the
  one that would fail if somebody trimmed the install to save image size.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"


def test_corpus_uses_codewiki_when_it_is_installed(tmp_path):
    """An importable CodeWiki must actually be the analyser that runs.

    Asserting on `analyzer` rather than on symbol counts, because the counts
    are a property of the tree and this is a question about which code path
    executed. The edge assertion is the second half of the same question: the
    AST fallback returns no edges at all by design, so an edge is proof the
    real analyser ran rather than a well-populated fallback.
    """
    pytest.importorskip(
        "codewiki.src.be.dependency_analyzer",
        reason="CodeWiki is not installed; the fallback is the correct path here",
    )
    from tracelink import corpus

    # Two files with a real import between them: the fallback can find both
    # symbols, but only a dependency analyser can find the edge.
    (tmp_path / "store.py").write_text(
        "class Ledger:\n"
        "    def balance(self):\n"
        "        return 0\n",
        encoding="utf-8",
    )
    (tmp_path / "report.py").write_text(
        "from store import Ledger\n"
        "\n"
        "def summarise():\n"
        "    return Ledger().balance()\n",
        encoding="utf-8",
    )

    built = corpus.build(tmp_path)

    assert built.analyzer == "codewiki", (
        "CodeWiki imports but tracelink still fell back to the Python-only "
        "walk. That is a broken install rather than an absent one - most "
        "likely a dependency missing after `pip install --no-deps`. Run the "
        "import by hand to see the real error:\n"
        "  from codewiki.src.be.dependency_analyzer import DependencyGraphBuilder"
    )
    assert built.edges, (
        "CodeWiki ran but produced no edges on a tree that has one "
        "(report.py imports store.py). The corpus would be indistinguishable "
        "from the fallback's output downstream."
    )


def test_the_image_installs_the_analyser():
    """The Dockerfile must carry CodeWiki, pinned, with its hidden dependency.

    Runs without CodeWiki present, which is the point: this is the check that
    still works on a machine where the test above is skipped.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "FSoft-AI4Code/CodeWiki" in text, (
        "the image no longer installs CodeWiki, so every corpus it builds "
        "will use the Python-only fallback - silently. See tracelink/corpus.py."
    )

    # A branch or tag would make the analyser's version a function of the build
    # date, and its output is an input to every verdict.
    assert re.search(r"FSoft-AI4Code/CodeWiki@[0-9a-f]{40}", text), (
        "CodeWiki must be pinned to a full commit sha. A moving ref makes the "
        "corpus - and therefore every verdict built on it - depend on what "
        "upstream's default branch said on the day the image was built."
    )

    # Not in CodeWiki's own pyproject.toml: `codewiki/src/be/utils.py` imports
    # it, and upstream only gets away with that because litellm pulls it in.
    # `--no-deps` removes that accident, so the name has to be here.
    assert "tiktoken" in text, (
        "tiktoken is missing. It is an undeclared dependency of CodeWiki - "
        "`codewiki/src/be/utils.py` imports it, but it is absent from "
        "CodeWiki's pyproject.toml - so installing with --no-deps and without "
        "naming it here makes `import codewiki...` raise ModuleNotFoundError, "
        "which corpus.py catches and turns into a silent fallback."
    )

    assert "--no-deps" in text, (
        "CodeWiki must be installed with --no-deps. Its declared dependencies "
        "are written for the whole product (litellm, openai, pydantic-ai, "
        "fastapi, uvicorn, mermaid, networkx) and pull ~414 MB onto a 512 MB "
        "machine for an analyser that needs ~33 MB of it."
    )
