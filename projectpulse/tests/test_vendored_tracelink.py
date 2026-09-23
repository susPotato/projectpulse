"""The vendored pipeline must not drift from the one it is a copy of.

`scripts/vendor_tracelink.py` explains why a copy exists at all: the build
context is `projectpulse/` and the pipeline lives outside it, in a private
repository. What a copy costs is drift, and drift here is the worst kind -
the image would keep running a version of the pipeline nobody is editing,
while every local run used the real one, and the two would disagree about
findings rather than crashing.

Same arrangement as `test_every_page_carries_the_same_rail`, which guards the
four copies of the navigation rail for the same reason.

Skipped when the upstream checkout is absent, because a machine with only this
repository cloned has nothing to compare against and a red suite would say
nothing true about the code.
"""

from __future__ import annotations

import pytest

from scripts import vendor_tracelink


@pytest.fixture(autouse=True)
def _upstream():
    if not vendor_tracelink.UPSTREAM.is_dir():
        pytest.skip("the tracelink pipeline is not checked out beside this repo")


def test_the_vendored_copy_is_current():
    missing, extra, changed = vendor_tracelink.differences()
    assert not (missing or extra or changed), (
        "the vendored pipeline has drifted from "
        f"{vendor_tracelink.UPSTREAM}. Run "
        "`python -m scripts.vendor_tracelink --apply` and commit it.\n"
        f"  missing here: {missing}\n"
        f"  extra here:   {extra}\n"
        f"  differs:      {changed}"
    )


def test_the_copy_is_importable_as_the_image_imports_it():
    """The image runs `import tracelink` against this directory.

    A copy that is present and not importable is the failure this would
    otherwise only show on the deployed host - `source.available()` would
    report the pipeline missing on a server that is carrying it.
    """
    import sys

    sys.path.insert(0, str(vendor_tracelink.HERE))
    try:
        from tracelink import source
    finally:
        sys.path.remove(str(vendor_tracelink.HERE))

    assert hasattr(source, "resolve")
    assert source.looks_like_url("https://github.com/org/repo.git")


def test_build_artefacts_cannot_reach_git_or_the_image():
    """A committed `__pycache__` is bytecode for whichever interpreter wrote
    it, and the image's is a different one.

    The working tree is *expected* to hold one - importing the copy is what
    these very tests do. What must hold is that it cannot travel: excluded
    from the copy the script makes, from the commit, and from the build
    context. Three separate files, each easy to edit without thinking about
    this directory.
    """
    root = vendor_tracelink.HERE

    assert "__pycache__" in str(vendor_tracelink.IGNORE(".", ["__pycache__"])), \
        "scripts/vendor_tracelink.py would copy bytecode into the vendored tree"

    for name in (".gitignore", ".dockerignore"):
        body = (root / name).read_text(encoding="utf-8")
        assert "__pycache__" in body, f"{name} does not exclude bytecode"
