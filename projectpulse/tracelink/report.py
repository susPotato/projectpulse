"""Turn verdicts into something a person acts on.

Leads with **status conflicts**, because `unverified` will be the majority
verdict on any real backlog and is honest but useless to read in bulk. A
status conflict — Jira says To Do, the code says shipped — is the one finding
that is both provable from a candidate subset and immediately actionable.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

from tracelink.artifacts import Ticket, Verdict


@dataclass
class Report:
    status_conflicts: list[tuple[Ticket, Verdict]]
    corroborated: list[tuple[Ticket, Verdict]]
    contradicted: list[tuple[Ticket, Verdict]]
    unverified: list[tuple[Ticket, Verdict]]
    counts: dict[str, int]
    confidence: dict[str, int]
    cost_usd: float

    def render(self) -> str:
        n = sum(self.counts.values()) or 1
        lines = ["=" * 72, "TRACEABILITY REPORT", "=" * 72, ""]

        lines.append(f"{len(self.status_conflicts)} STATUS CONFLICT(S) "
                     f"— tracker and code disagree about what is done")
        if not self.status_conflicts:
            lines.append("  none found")
        for t, v in self.status_conflicts:
            lines.append(f"\n  [{t.status}] {t.summary[:64]}")
            lines.append(f"    verdict={v.verdict} confidence={v.confidence}")
            lines.append(f"    {v.reasoning.strip()}")
            for e in v.evidence[:3]:
                anchor = f"{e.file}::{e.symbol}" if e.symbol else e.file
                lines.append(f"      - {anchor} — {e.why}")

        lines += ["", "-" * 72, "VERDICT MIX", ""]
        for k in ("corroborated", "contradicted", "unverified"):
            c = self.counts.get(k, 0)
            lines.append(f"  {k:<14} {c:>4}  ({100 * c / n:4.1f}%)")
        lines.append("")
        for k in ("high", "medium", "low"):
            lines.append(f"  confidence {k:<8} {self.confidence.get(k, 0):>4}")

        lines += ["", "-" * 72,
                  f"cost: ${self.cost_usd:.4f}", ""]
        lines.append("Reminder: `corroborated` means code exists that plausibly "
                     "implements the claim.")
        lines.append("It does not mean the feature works — this pipeline reads "
                     "code, it does not run it.")
        return "\n".join(lines)


def build(tickets: list[Ticket], verdicts: list[Verdict]) -> Report:
    by_uid = {t.uid: t for t in tickets}
    pairs = [(by_uid[v.uid], v) for v in verdicts if v.uid in by_uid]

    def bucket(name: str) -> list[tuple[Ticket, Verdict]]:
        return [(t, v) for t, v in pairs if v.verdict == name]

    conflicts = [(t, v) for t, v in pairs if v.status_conflict]
    # Most decisive first: a high-confidence conflict is the headline.
    order = {"high": 0, "medium": 1, "low": 2}
    conflicts.sort(key=lambda tv: order.get(tv[1].confidence, 3))

    return Report(
        status_conflicts=conflicts,
        corroborated=bucket("corroborated"),
        contradicted=bucket("contradicted"),
        unverified=bucket("unverified"),
        counts=collections.Counter(v.verdict for _, v in pairs),
        confidence=collections.Counter(v.confidence for _, v in pairs),
        cost_usd=round(sum(v.cost_usd for v in verdicts), 6),
    )
