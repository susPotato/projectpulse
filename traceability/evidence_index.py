"""Stage 1b: evidence index over ALL file roles, not just Python logic.

Classifies every file by role, extracts searchable tokens per role, and builds a
rarity-weighted inverted index. Declarative/prose files additionally get a
content-word index so ticket text can be matched prose-to-prose.
"""
import ast
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[2]).resolve()   # repo to index

IMAGE_EXT = {".png", ".ico", ".icns", ".jpg", ".jpeg", ".gif", ".svg", ".woff", ".ttf"}
PROSE_EXT = {".yaml", ".yml", ".skill", ".md", ".html", ".txt", ".json", ".cfg", ".ini"}

STOP = set("""
the and for with via per from new all use using based other auto full main user data
file files code list view page this that then than when where which what your you our
are was were has have had not but its it's into out over under more most some such can
could should would will shall may might must one two three also only just like about
after before between during each both few many much any own same so no nor too very
system dialog manager framework support integration integrations tool tools app service
services module feature features true false null none enabled disable disabled default
name description type value config configuration set get add remove update create delete
""".split())

WORD = re.compile(r"[a-z][a-z0-9]{3,}")


def classify(rel: str, suffix: str) -> str:
    low = rel.lower()
    if "min.js" in low or low.startswith("assets/d3"):
        return "vendored"
    # Project-wide docs describe everything, so they discriminate nothing.
    # Same treatment as vendored: never a candidate target.
    if suffix in (".md", ".txt") and ("/" not in rel or low.startswith("assets/")):
        return "hubdoc"
    if suffix == ".html":  # templates: markup + embedded JS, not prose
        return "data"
    if low.startswith("tests/") or "/test_" in low or low.startswith("test_"):
        return "test"
    if suffix in IMAGE_EXT:
        return "data"
    if rel == "i18n.py":
        return "data"
    if suffix == ".py":
        return "logic"
    if suffix in PROSE_EXT:
        return "declarative"
    return "other"


def py_identifiers(text):
    toks = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return toks
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            toks.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            toks.add(node.name)
    return toks


def split_ident(name):
    """MainWindow / model_pricing / block-network -> component words."""
    parts = re.split(r"[_\-.]", name)
    out = []
    for p in parts:
        out.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", p))
    return [w.lower() for w in out if len(w) >= 4 and w.lower() not in STOP]


files = {}
for p in sorted(ROOT.rglob("*")):
    if not p.is_file() or "__pycache__" in p.parts:
        continue
    rel = p.relative_to(ROOT).as_posix()
    role = classify(rel, p.suffix.lower())
    entry = {"role": role, "size": p.stat().st_size,
             "stems": set(), "syms": set(), "words": set()}

    # Stem tokens (filename/dir) are high-precision; symbol tokens (class/function)
    # are far noisier, so they are indexed separately and thresholded harder.
    entry["stems"].update(split_ident(p.stem))
    entry["stems"].update(split_ident(p.parent.name) if p.parent != ROOT else [])

    if role in ("logic", "test", "data") and p.suffix.lower() == ".py":
        text = p.read_text(encoding="utf-8", errors="ignore")
        if role != "data":  # skip AST on the 242KB translation blob
            for ident in py_identifiers(text):
                entry["syms"].update(split_ident(ident))
    elif role == "declarative":
        text = p.read_text(encoding="utf-8", errors="ignore")
        # YAML/JSON keys and prose words alike become content words
        entry["words"].update(w for w in WORD.findall(text.lower()) if w not in STOP)
        for key in re.findall(r"^\s*([A-Za-z_][\w\-]*)\s*:", text, re.M):
            entry["stems"].update(split_ident(key))

    files[rel] = entry

# Inverted indexes with document frequency for rarity weighting.
stem2files = collections.defaultdict(set)
sym2files = collections.defaultdict(set)
word2files = collections.defaultdict(set)
for rel, e in files.items():
    if e["role"] in ("vendored", "hubdoc"):
        continue
    for t in e["stems"]:
        stem2files[t].add(rel)
    for t in e["syms"]:
        sym2files[t].add(rel)
    for w in e["words"]:
        word2files[w].add(rel)

out = {
    "files": {k: {"role": v["role"], "size": v["size"]} for k, v in files.items()},
    "stem2files": {k: sorted(v) for k, v in stem2files.items()},
    "sym2files": {k: sorted(v) for k, v in sym2files.items()},
    "word2files": {k: sorted(v) for k, v in word2files.items()},
}
Path(sys.argv[1]).write_text(json.dumps(out), encoding="utf-8")

roles = collections.Counter(e["role"] for e in files.values())
print("ROLE CENSUS")
for r, n in roles.most_common():
    kb = sum(e["size"] for e in files.values() if e["role"] == r) / 1024
    print("  %-12s %4d files  %8.0f KB" % (r, n, kb))
print()
print("stem tokens: %d  symbol tokens: %d  prose words: %d"
      % (len(stem2files), len(sym2files), len(word2files)))
