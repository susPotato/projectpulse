"""Stage 3 PoC — adjudicate a sample of tickets against the code.

For each ticket: assemble (ticket text + CodeWiki doc excerpt + candidate source
files from Stage 1) and ask the model for a three-valued verdict with evidence.

The point of the three-valued verdict: proving a claim TRUE is easy (find the code),
proving it FALSE is not (absence of evidence isn't evidence of absence). So the model
must be able to answer "unverified" rather than being forced into a binary.

Usage:
  ANTHROPIC_API_KEY=... python stage3_adjudicate.py <dir> [--limit N] [--model M]
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

API = "https://api.anthropic.com/v1/messages"
MAX_SRC_LINES = 220
MAX_DOC_LINES = 70

VERDICTS = {
    "corroborated": "code clearly implements what the ticket claims",
    "contradicted": "code clearly shows the claim is wrong",
    "unverified": "candidate code does not settle it either way",
}

TOOL = {
    "name": "report_verdict",
    "description": "Report the adjudication verdict for one Jira ticket.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "evidence": {
                "type": "array",
                "description": "Specific file/symbol references supporting the verdict.",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "symbol": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["file", "why"],
                },
            },
            "reasoning": {"type": "string", "description": "2-3 sentences, no hedging."},
            "status_conflict": {
                "type": "boolean",
                "description": "True if the ticket's Jira status disagrees with what the code shows "
                               "(e.g. status 'To Do' but the feature is fully implemented).",
            },
        },
        "required": ["verdict", "confidence", "evidence", "reasoning", "status_conflict"],
    },
}

SYSTEM = """You audit whether Jira tickets match a codebase. You are given a ticket, an
excerpt of generated architecture documentation, and the source files most likely to
contain the feature.

Rules:
- Judge ONLY from the evidence shown. You are seeing a candidate subset, not the repo.
- If the shown code does not settle the question, answer "unverified". Absence of
  evidence in a candidate subset is NOT evidence the feature is missing. Do not guess,
  and do not treat a plausible-sounding ticket as corroborated without seeing code.
- "contradicted" requires positive evidence the claim is wrong, not merely missing code.
- Set status_conflict=true when the code contradicts the ticket's STATUS rather than its
  content - most importantly when status is "To Do" but the feature is clearly built.
- Cite real symbols you can see. Never invent a file or function name."""


def load(scratch):
    rows = json.loads((scratch / "stage1_results.json").read_text(encoding="utf-8"))
    docs = {}
    for p in (scratch / "codewiki-docs").glob("*.md"):
        docs[p.name] = p.read_text(encoding="utf-8", errors="ignore")
    return rows, docs


def doc_excerpt(docs, filenames):
    """Pull the part of any doc that talks about these files."""
    out = []
    for name, text in docs.items():
        lines = text.splitlines()
        hits = [i for i, l in enumerate(lines)
                if any(re.search(r"\b" + re.escape(f) + r"\b", l) for f in filenames)]
        if not hits:
            continue
        lo, hi = max(0, hits[0] - 8), min(len(lines), hits[-1] + 12)
        chunk = lines[lo:hi][:MAX_DOC_LINES]
        out.append("--- from %s ---\n%s" % (name, "\n".join(chunk)))
        if len(out) >= 2:
            break
    return "\n\n".join(out) or "(no documentation covers these files)"


def build_prompt(t, repo, docs, use_docs=True):
    files = sorted({f for e in t["ev"] if e["w"] >= 3 for f in e["files"]})[:3]
    bases = {f.split("/")[-1] for f in files}
    src = []
    for rel in files:
        p = repo / rel
        if not p.exists():
            continue
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        body = "\n".join(lines[:MAX_SRC_LINES])
        trunc = "" if len(lines) <= MAX_SRC_LINES else (
            "\n... [%d more lines truncated]" % (len(lines) - MAX_SRC_LINES))
        src.append("### %s (%d lines)\n```python\n%s%s\n```" % (rel, len(lines), body, trunc))

    return (
        "## Jira ticket\n"
        "Summary: %s\nDescription: %s\nComponent: %s\n**Status: %s**\n\n"
        "## Architecture documentation excerpt\n%s\n\n"
        "## Candidate source files (selected by keyword retrieval, may be wrong)\n%s\n\n"
        "Adjudicate: does the code support this ticket's claim?"
        % (t["summary"], (t["description"] or "(none)")[:1200], t["component"],
           t["status"],
           doc_excerpt(docs, bases) if use_docs else "(documentation withheld - ablation run)",
           "\n\n".join(src) or "(no candidate files)")
    )


def call(prompt, model, key):
    body = json.dumps({
        "model": model,
        "max_tokens": 1500,
        "system": SYSTEM,
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": "report_verdict"},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(API, data=body, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    usage = d.get("usage", {})
    for blk in d.get("content", []):
        if blk.get("type") == "tool_use":
            return blk["input"], usage
    return None, usage


def pick_sample(rows, limit):
    """Deliberately span the interesting quadrants, including cases that SHOULD
    come back 'unverified' - that is the test of whether the model confabulates."""
    strong = [r for r in rows if r["strong"]]
    buckets = [
        ("shipped+evidence", [r for r in strong if r["status"] == "Release it"]),
        ("todo+evidence", [r for r in strong if r["status"] == "To Do"]),
        ("inprogress+evidence", [r for r in strong if r["status"] == "In Progress"]),
        ("shipped+NO evidence", [r for r in rows if not r["strong"]
                                 and r["status"] == "Release it"]),
    ]
    per = max(2, limit // len(buckets))
    out = []
    for label, rs in buckets:
        for r in rs[:per]:
            out.append((label, r))
    return out[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--repo", default="../pimsathon-main")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--no-docs", action="store_true",
                    help="ablation: withhold CodeWiki doc excerpts")
    ap.add_argument("--out", default="stage3_results.json")
    a = ap.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("set ANTHROPIC_API_KEY")

    scratch = Path(a.dir)
    repo = Path(a.repo).resolve()
    rows, docs = load(scratch)
    sample = pick_sample(rows, a.limit)

    results, tin, tout = [], 0, 0
    for i, (label, t) in enumerate(sample, 1):
        try:
            v, usage = call(build_prompt(t, repo, docs, not a.no_docs), a.model, key)
        except Exception as e:                      # noqa: BLE001
            print("  [%d] API error: %s" % (i, e))
            continue
        tin += usage.get("input_tokens", 0)
        tout += usage.get("output_tokens", 0)
        results.append({"bucket": label, "ticket": t["summary"], "status": t["status"],
                        "component": t["component"], **(v or {})})
        mark = "!" if v and v.get("status_conflict") else " "
        print("%s[%2d/%d] %-19s %-11s %-6s %s"
              % (mark, i, len(sample), label, (v or {}).get("verdict", "ERROR"),
                 (v or {}).get("confidence", "-"), t["summary"][:44]))

    (scratch / a.out).write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    # Sonnet pricing: $3 / $15 per Mtok
    cost = tin / 1e6 * 3 + tout / 1e6 * 15
    print("\ntokens: in=%d out=%d   approx cost: $%.3f" % (tin, tout, cost))
    print("wrote %s" % (scratch / a.out))


if __name__ == "__main__":
    main()
