"""Ground truth — the only thing a score may be measured against.

Without labels every threshold change is unfalsifiable, which is how the
demo reported 94.2% before three fixes brought it down to an honest 82.7%.

The sample is chosen to be *informative*, not representative: stratified
across retrieval outcome and ticket status, so it contains the cases that
can embarrass us (a shipped ticket with no candidates) rather than only the
easy ones. Labelling an unstratified random sample of a skewed backlog
mostly re-measures the majority class.

Resumable: an existing labels file is loaded and its tickets skipped.
"""

from __future__ import annotations

import collections
import random
from pathlib import Path

from tracelink.artifacts import Label, Ticket, TicketCandidates


def stratified_sample(
    tickets: list[Ticket],
    results: dict[str, TicketCandidates],
    n: int,
    seed: int = 0,
) -> list[Ticket]:
    """Spread the sample across the quadrants that behave differently."""
    buckets: dict[str, list[Ticket]] = collections.defaultdict(list)
    for t in tickets:
        r = results.get(t.uid)
        strong = bool(r and r.strong_paths())
        collided = bool(r and r.head_collision)
        if not strong:
            key = "no-candidates"
        elif collided:
            key = "shared-head"
        else:
            key = "clean-hit"
        buckets[key].append(t)

    rng = random.Random(seed)
    for v in buckets.values():
        rng.shuffle(v)

    # Round-robin so a small sample still touches every bucket.
    order = sorted(buckets)
    out: list[Ticket] = []
    i = 0
    while len(out) < n and any(buckets[k] for k in order):
        k = order[i % len(order)]
        if buckets[k]:
            out.append(buckets[k].pop())
        i += 1
    return out


def load_labels(path: str | Path) -> dict[str, Label]:
    from tracelink.artifacts import load_payload, rebuild_labels
    p = Path(path)
    if not p.exists():
        return {}
    return {l.uid: l for l in rebuild_labels(load_payload(p, "label"))}


def save_labels(path: str | Path, labels: dict[str, Label], **meta) -> Path:
    from tracelink.artifacts import save
    return save(path, "label", [labels[k] for k in sorted(labels)], **meta)


def prompt_session(
    tickets: list[Ticket],
    results: dict[str, TicketCandidates],
    labels: dict[str, Label],
    out_path: str | Path,
    repo_root: Path,
    labeller: str = "",
) -> dict[str, Label]:
    """Interactive labelling. Ctrl-C is safe: everything so far is saved."""
    todo = [t for t in tickets if t.uid not in labels]
    if not todo:
        print("Nothing left to label.")
        return labels

    print(f"\n{len(todo)} tickets to label. Ctrl-C saves and exits.\n")
    print("For each ticket, enter the NUMBERS of the files that genuinely relate")
    print("to it (comma-separated), or:")
    print("   <enter>  none of them are relevant")
    print("   a        all of them")
    print("   s        skip this ticket (do not record a label)")
    print("   ?N       print the first 40 lines of candidate N\n")

    try:
        for n, t in enumerate(todo, 1):
            r = results.get(t.uid)
            paths = r.strong_paths() if r else []
            print("=" * 72)
            print(f"[{n}/{len(todo)}]  {t.uid}  status={t.status!r}  component={t.component}")
            print(f"  {t.summary}")
            if t.description:
                body = " ".join(t.description.split())[:300]
                print(f"  {body}")
            if r and r.head_collision:
                print("  (title head is shared with other tickets)")
            if not paths:
                print("\n  no candidates retrieved")
            for i, p in enumerate(paths, 1):
                why = _why(r, p)
                print(f"   {i:>2}. {p}   [{why}]")

            while True:
                raw = input("\n  relevant> ").strip()
                if raw.startswith("?"):
                    _preview(repo_root, paths, raw[1:])
                    continue
                break

            if raw.lower() == "s":
                continue
            if raw.lower() == "a":
                chosen = list(paths)
            elif not raw:
                chosen = []
            else:
                chosen = []
                for part in raw.replace(" ", "").split(","):
                    if part.isdigit() and 1 <= int(part) <= len(paths):
                        chosen.append(paths[int(part) - 1])
            labels[t.uid] = Label(uid=t.uid, relevant_paths=chosen, labeller=labeller)
            save_labels(out_path, labels, partial=True)
    except (KeyboardInterrupt, EOFError):
        print("\n\ninterrupted — labels so far are saved")

    save_labels(out_path, labels, partial=False)
    print(f"\nwrote {len(labels)} labels to {out_path}")
    return labels


def _why(r: TicketCandidates | None, path: str) -> str:
    if not r:
        return ""
    for c in r.candidates:
        if c.path == path:
            bits = {f"{e.matcher}:{e.anchor}" for e in c.evidence if e.strong}
            return ", ".join(sorted(bits))[:54]
    return ""


def _preview(root: Path, paths: list[str], which: str) -> None:
    if not which.isdigit() or not (1 <= int(which) <= len(paths)):
        print("   no such candidate")
        return
    p = root / paths[int(which) - 1]
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[:40]
    except OSError as exc:
        print(f"   cannot read: {exc}")
        return
    print()
    for line in lines:
        print("   | " + line[:110])
