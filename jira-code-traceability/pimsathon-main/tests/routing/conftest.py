"""Pytest fixtures/shared helpers for the routing test suite.

Ensures the ``cowork_local`` package is importable when pytest is invoked from
the package directory itself (so ``import cowork_local.core.routing...`` works
regardless of the working directory the suite is launched from).
"""
from __future__ import annotations

import sys
from pathlib import Path

# .../cowork_local/tests/routing/conftest.py  →  parent of the package dir
_PKG_DIR = Path(__file__).resolve().parents[2]      # .../cowork_local
_REPO_ROOT = _PKG_DIR.parent                          # .../cowork_local_20260722
for p in (str(_REPO_ROOT), str(_PKG_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
