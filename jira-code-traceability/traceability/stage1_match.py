"""Stage 1 matcher over the full evidence index (all file roles).

Two matchers:
  A) identifier  - ticket title tokens vs file/class/function name tokens, IDF-weighted
  B) prose       - ticket text vs declarative-file content words, rare-word overlap
"""
import collections
import json
import re
import sys
from pathlib import Path

S = Path(sys.argv[1])
idx = json.loads((S / "evidence_index.json").read_text(encoding="utf-8"))
tickets = json.loads((S / "tickets.json").read_text(encoding="utf-8"))

FILES = idx["files"]
STEM = {k: set(v) for k, v in idx["stem2files"].items()}
SYM = {k: set(v) for k, v in idx["sym2files"].items()}
WRD = {k: set(v) for k, v in idx["word2files"].items()}

STOP = set("""
the and for with via per from new all use using based other auto full main user data
file files code list view page this that then than when where which what your you our
are was were has have had not but its into out over under more most some such can could
should would will shall may might must one two three also only just like about after
before between during each both few many much any own same so no nor too very system
dialog manager framework support integration integrations tool tools app service services
module feature features true false null none enabled disable disabled default name
description type value config configuration set get add remove update create delete
""".split())
WORD = re.compile(r"[a-z][a-z0-9]{3,}")
VIET = re.compile(r"[ăâđêôơưĂÂĐÊÔƠƯàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ]")

N_PROSE = sum(1 for f, m in FILES.items() if m["role"] == "declarative")

# Vocabulary size per file, for length normalisation of prose matches.
VOCAB = collections.Counter()
for w, fs in WRD.items():
    for f in fs:
        VOCAB[f] += 1


def ticket_words(t):
    return {w for w in WORD.findall((t["summary"] + " " + t["description"]).lower())
            if w not in STOP}


def head_tokens(t):
    head = re.split(r"[—:(]|-- ", t["summary"], maxsplit=1)[0]
    return [w for w in WORD.findall(head.lower()) if w not in STOP]


def evidence_for(t):
    ev = []
    # A) identifier match, IDF-weighted
    for tok in set(head_tokens(t)):
        hits = STEM.get(tok)
        if hits:
            ev.append({"m": "stem", "w": 3 if len(hits) <= 5 else 1, "anchor": tok,
                       "files": sorted(hits)[:4], "df": len(hits)})
        sym = SYM.get(tok)
        if sym:
            # symbol names are noisier than filenames: harder rarity bar
            ev.append({"m": "sym", "w": 3 if len(sym) <= 3 else 1, "anchor": tok,
                       "files": sorted(sym)[:4], "df": len(sym)})
    # B) prose match against declarative files (rare-word overlap)
    tw = ticket_words(t)
    per_file = collections.defaultdict(set)
    for w in tw:
        hits = WRD.get(w, set())
        if hits and len(hits) <= 2:          # genuinely rare, not 31%-of-corpus
            for f in hits:
                per_file[f].add(w)
    for f, shared in per_file.items():
        # length-normalised: a big document must share proportionally more
        need = 3 + int(VOCAB.get(f, 0) / 600)
        if len(shared) >= need:
            ev.append({"m": "prose", "w": 3, "anchor": ",".join(sorted(shared)[:4]),
                       "files": [f], "df": len(shared)})
    return ev


rows = []
for t in tickets:
    ev = evidence_for(t)
    rows.append({**t, "ev": ev,
                 "strong": sum(1 for e in ev if e["w"] >= 3),
                 "viet": bool(VIET.search(t["summary"] + t["description"]))})

(S / "stage1_results.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


def strong_files(r):
    return sorted({f for e in r["ev"] if e["w"] >= 3 for f in e["files"]})


print("=" * 70)
print("STAGE 1 (all file roles)   n=%d tickets" % len(rows))
print("=" * 70)
b = collections.Counter("strong" if r["strong"] else ("weak" if r["ev"] else "none")
                        for r in rows)
for k in ("strong", "weak", "none"):
    print("  %-10s %3d  (%4.1f%%)" % (k, b[k], b[k] / len(rows) * 100))

print()
print("  matcher contribution (tickets with >=1 strong hit of that kind):")
for m in ("stem", "sym", "prose"):
    n = sum(1 for r in rows if any(e["m"] == m and e["w"] >= 3 for e in r["ev"]))
    only = sum(1 for r in rows
               if r["strong"] and {e["m"] for e in r["ev"] if e["w"] >= 3} == {m})
    print("    %-6s  %3d   (sole evidence for %d)" % (m, n, only))

cand = [len(strong_files(r)) for r in rows if r["strong"]]
if cand:
    cand.sort()
    print("\n  candidate-set size: median=%d  max=%d  (<=3 files: %d/%d)"
          % (cand[len(cand) // 2], cand[-1],
             sum(1 for c in cand if c <= 3), len(cand)))

print()
print("=" * 70)
print("REGRESSION TEST: the 13 'Release it' tickets that had NO anchor in stage 1a")
print("=" * 70)
prev_missing = [
    "Welcome Dialog", "Network Guard", "Untrusted Content Fence",
    "Notification Center", "App Batch", "Toast Notifications", "QA Report",
    "Log Retention", "Run Timing", "Console", "Dependencies Check",
    "Perfomance update", "Thêm màn hình cấu hình report",
]
resolved = 0
for r in rows:
    if not any(r["summary"].startswith(p) for p in prev_missing):
        continue
    fs = strong_files(r)
    if fs:
        resolved += 1
        why = [e for e in r["ev"] if e["w"] >= 3][0]
        print("  RESOLVED  %-34s -> %s" % (r["summary"][:34], ", ".join(fs[:3])))
        print("            via %s match on '%s'" % (why["m"], why["anchor"][:40]))
    else:
        print("  still none %-33s" % r["summary"][:34])
print("\n  resolved %d of %d" % (resolved, len(prev_missing)))

print()
print("=" * 70)
print("EVIDENCE BY FILE ROLE (strong hits)")
print("=" * 70)
roles = collections.Counter()
for r in rows:
    for f in strong_files(r):
        roles[FILES[f]["role"]] += 1
for role, n in roles.most_common():
    print("  %-12s %4d" % (role, n))
