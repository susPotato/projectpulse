"""Optional Java runtime detection.

``opendataloader-pdf`` (structured PDF→JSON extraction, see chat_agent.py) is a
Python wrapper around a JAVA CLI tool — the first Java dependency anywhere in
this app. Same optional-native-binary shape as doc_extract.py::find_soffice:
env override → PATH → common install directories → ``None`` if not found.
Callers must degrade gracefully — a missing JVM just means that one capability
isn't available on this machine, not an error.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_EXE = "java.exe" if os.name == "nt" else "java"

# Common JDK/JRE install roots, newest-version subfolder picked first.
_COMMON_ROOTS = (
    r"C:\Program Files\Java",
    r"C:\Program Files\Eclipse Adoptium",
    r"C:\Program Files (x86)\Java",
    "/Library/Java/JavaVirtualMachines",
    "/usr/lib/jvm",
)


def _version_key(path: Path) -> tuple:
    """Natural-sort key so "jdk-17..." ranks above "jdk-8..." — a plain string
    sort ranks '8' > '1' and would pick the OLDER JDK when major-version digit
    counts differ (e.g. Eclipse Adoptium installs several majors side by side
    under one root)."""
    return tuple(int(n) for n in re.findall(r"\d+", path.name))


def find_java() -> str | None:
    """Locate a Java launcher (``JAVA_HOME`` → PATH → common install dirs)."""
    env = os.environ.get("JAVA_HOME")
    if env:
        candidate = Path(env) / "bin" / _EXE
        if candidate.exists():
            return str(candidate)
    found = shutil.which("java")
    if found:
        return found
    for base in _COMMON_ROOTS:
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        try:
            subdirs = sorted(base_path.iterdir(), key=_version_key, reverse=True)
        except OSError:
            continue  # unreadable dir (permissions/AV) — not fatal, just try the next root
        for sub in subdirs:
            candidate = sub / "bin" / _EXE
            if candidate.exists():
                return str(candidate)
            # macOS JDK bundles nest an extra Contents/Home.
            mac_candidate = sub / "Contents" / "Home" / "bin" / _EXE
            if mac_candidate.exists():
                return str(mac_candidate)
    return None
