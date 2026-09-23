"""Scoring — what makes a change to the matcher falsifiable.

Reports retrieval quality against hand labels, and reports it **only over
labelled tickets**. The demo's headline "82.7% of tickets got a strong
candidate set" is a coverage figure, not an accuracy one: it counts a
ticket as answered whether the files proposed were right or wrong. Both
numbers are printed here, side by side, because the gap between them is
the thing worth watching.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracelink.artifacts import Label, Ticket, TicketCandidates


@dataclass
class Scores:
    n_labelled: int
    precision: float
    recall: float
    f1: float
    hit_rate: float          # labelled tickets with >=1 correct file proposed
    exact: int               # proposed set == labelled set
    empty_correct: int       # correctly proposed nothing
    false_positive_only: int  # proposed only wrong files
    missed: int              # labelled relevant, proposed nothing

    def render(self) -> str:
        return (
            f"  labelled tickets     {self.n_labelled}\n"
            f"  precision            {self.precision:.3f}\n"
            f"  recall               {self.recall:.3f}\n"
            f"  F1                   {self.f1:.3f}\n"
            f"  hit rate             {self.hit_rate:.3f}  "
            f"(>=1 correct file proposed)\n"
            f"  exact set matches    {self.exact}\n"
            f"  correct 'nothing'    {self.empty_correct}\n"
            f"  all-wrong sets       {self.false_positive_only}\n"
            f"  missed entirely      {self.missed}"
        )


def score(
    labels: dict[str, Label],
    results: dict[str, TicketCandidates],
) -> Scores:
    tp = fp = fn = 0
    hits = exact = empty_correct = fp_only = missed = 0
    n = 0

    for uid, label in labels.items():
        r = results.get(uid)
        proposed = set(r.strong_paths()) if r else set()
        truth = set(label.relevant_paths)
        n += 1

        tp += len(proposed & truth)
        fp += len(proposed - truth)
        fn += len(truth - proposed)

        if proposed & truth:
            hits += 1
        if proposed == truth:
            exact += 1
            if not truth:
                empty_correct += 1
        if proposed and not (proposed & truth):
            fp_only += 1
        if truth and not proposed:
            missed += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return Scores(n_labelled=n, precision=precision, recall=recall, f1=f1,
                  hit_rate=hits / n if n else 0.0, exact=exact,
                  empty_correct=empty_correct, false_positive_only=fp_only,
                  missed=missed)


def coverage(results: list[TicketCandidates]) -> dict[str, float | int]:
    """Coverage — how often we answered at all. Not accuracy. Never quote
    this as if it were."""
    n = len(results) or 1
    strong = [r for r in results if r.strong_paths()]
    any_ev = [r for r in results if r.candidates]
    sets = [tuple(r.strong_paths()) for r in strong]
    distinct = len(set(sets))
    sizes = sorted(len(s) for s in sets)
    return {
        "tickets": len(results),
        "with_strong": len(strong),
        "strong_pct": round(100 * len(strong) / n, 1),
        "weak_only": len(any_ev) - len(strong),
        "none": len(results) - len(any_ev),
        "distinct_sets": distinct,
        "collapsed_onto_shared_set": len(strong) - distinct,
        "median_set_size": sizes[len(sizes) // 2] if sizes else 0,
        "head_collisions": sum(1 for r in results if r.head_collision),
    }


def per_matcher(results: list[TicketCandidates]) -> dict[str, dict[str, int]]:
    """Which matcher is doing the work, and where it is the only witness.

    The matcher list is read off the evidence rather than hardcoded. It was
    a fixed triple, so when the `doc` matcher was added it became the
    highest-contributing matcher in the run and the table showing matcher
    contribution silently did not mention it.
    """
    present = {e.matcher for r in results for c in r.candidates for e in c.evidence}
    out: dict[str, dict[str, int]] = {}
    for m in [k for k in ("stem", "sym", "prose", "doc") if k in present] + \
             sorted(present - {"stem", "sym", "prose", "doc"}):
        fires = sole = 0
        for r in results:
            kinds = {e.matcher for c in r.candidates for e in c.evidence if e.strong}
            if m in kinds:
                fires += 1
                if kinds == {m}:
                    sole += 1
        out[m] = {"tickets": fires, "sole_evidence": sole}
    return out


def compare(before: Scores, after: Scores) -> str:
    """Did a change help? The question the demo could not answer."""
    def arrow(a: float, b: float) -> str:
        d = b - a
        return f"{b:.3f} ({'+' if d >= 0 else ''}{d:.3f})"
    return (
        f"  precision  {before.precision:.3f} -> {arrow(before.precision, after.precision)}\n"
        f"  recall     {before.recall:.3f} -> {arrow(before.recall, after.recall)}\n"
        f"  F1         {before.f1:.3f} -> {arrow(before.f1, after.f1)}"
    )
