"""Stage 3 — decide whether the code supports what a ticket claims.

**Why three verdicts and not two.** Proving a claim true and proving it false
are not symmetric. Finding the code proves implementation; *not* finding it
proves nothing, because a candidate set is a guess and features hide under
other names. A binary forces the model to invent a decision, so `unverified`
is a first-class answer and the prompt says when to reach for it.

Every call is cached on disk by the exact content that produced it, so
re-running after a prompt change re-spends only on what actually changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from tracelink.artifacts import (
    Corpus, Description, Ticket, TicketCandidates, Verdict, VerdictEvidence,
)

logger = logging.getLogger(__name__)

# Bump when the prompt or schema changes so stale cache entries are not reused.
PROMPT_VERSION = 2

# $ per million tokens (input, output). Used for reporting, not billing.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5-1": (10.00, 50.00),
}

DEFAULT_MODEL = "claude-opus-5"


class EvidenceItem(BaseModel):
    file: str = Field(description="Path of a file shown to you, copied exactly.")
    symbol: str = Field(default="", description="Class/function name you can see.")
    why: str = Field(description="What this specific code shows, in one sentence.")


class VerdictOut(BaseModel):
    """The adjudication result. Field order is the order we want it reasoned in."""

    verdict: Literal["corroborated", "contradicted", "unverified"]
    confidence: Literal["high", "medium", "low"]
    reasoning: str = Field(description="2-3 sentences. No hedging, no restating the ticket.")
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description="Real files and symbols visible in the context. Never invent one.",
    )
    status_conflict: bool = Field(
        description="True when the code contradicts the ticket's STATUS rather than "
                    "its content — above all, status is 'To Do' but the feature is built.",
    )


SYSTEM = """You audit whether tickets in a tracker match a codebase. For each ticket you \
are given its text, optionally an excerpt of architecture documentation, and the source \
files a keyword retriever guessed are most relevant.

Rules:
- Judge ONLY from the evidence shown. You are seeing a candidate subset chosen by a \
keyword matcher that is often wrong, not the repository.
- If the shown code does not settle the question, answer "unverified". Absence of \
evidence in a candidate subset is NOT evidence the feature is missing. Do not treat a \
plausible-sounding ticket as corroborated without seeing the code.
- "contradicted" requires positive evidence the claim is wrong, not merely missing code.
- "corroborated" means code exists that plausibly implements this. It does NOT mean the \
feature works — you are reading code, not running it.
- Set status_conflict=true when the code contradicts the ticket's STATUS rather than its \
content. The case that matters most: status is "To Do" or "In Progress" but the feature \
is clearly built.
- Cite only files and symbols visible in the context. Never invent a name. If a candidate \
file is unrelated to the ticket, say so rather than rationalising a link.
- Some context is the team's own documentation, shown with each function name it cites \
already looked up in the repository for you: "-> found: path::Symbol" means that symbol \
really is there, "-> NOT in this repository" means it is not, and you cannot discover \
otherwise by reasoning. Trust those lookups over the document's prose.
- A documentation excerpt marked as NOT describing this repository is a design, a plan, \
or a different build. It tells you what the team intended. It is never evidence that \
anything is implemented here, however confidently it is written, and a ticket supported \
only by such an excerpt is "unverified" at best."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cached_calls: int = 0
    api_calls: int = 0
    #: Calls that raised. Counted because a run where everything failed
    #: otherwise reports `0 api calls, $0.0000` — indistinguishable from a
    #: run that was entirely cache hits, which is the good outcome.
    failures: int = 0
    first_error: str = ""
    #: Tokens a cached answer cost when it was first produced. Never billed
    #: again; carried so an artifact can record what making it took.
    prior_input_tokens: int = 0
    prior_output_tokens: int = 0

    def cost(self, model: str) -> float:
        """What this run spent. A cache hit spends nothing."""
        pin, pout = PRICING.get(model, (0.0, 0.0))
        return self.input_tokens / 1e6 * pin + self.output_tokens / 1e6 * pout

    def original_cost(self, model: str) -> float:
        """What producing these answers took, whenever that happened."""
        pin, pout = PRICING.get(model, (0.0, 0.0))
        return ((self.input_tokens + self.prior_input_tokens) / 1e6 * pin
                + (self.output_tokens + self.prior_output_tokens) / 1e6 * pout)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cached_calls += other.cached_calls
        self.api_calls += other.api_calls
        self.prior_input_tokens += other.prior_input_tokens
        self.prior_output_tokens += other.prior_output_tokens
        self.failures += other.failures
        self.first_error = self.first_error or other.first_error


def build_prompt(ticket: Ticket, descriptions: list[Description]) -> str:
    parts = [
        "## Ticket",
        f"Summary: {ticket.summary}",
        f"Description: {(ticket.description or '(none)')[:1500]}",
        f"Component: {ticket.component or '(none)'}",
        f"**Status: {ticket.status or '(none)'}**",
    ]
    # Show the translation alongside, never instead. The original is what the
    # tracker says; the English is there so the verdict is not also a
    # translation exercise performed silently inside the same call.
    if ticket.summary_en or ticket.description_en:
        parts += [
            "",
            f"_Translated from {ticket.lang} for matching:_",
            f"Summary: {ticket.summary_en}",
            f"Description: {(ticket.description_en or '(none)')[:1500]}",
        ]
    for d in descriptions:
        label = {
            "doc-feature": "## The team's own documentation for this capability "
                           "(every name it cites already checked against the repository)",
            "existing-docs": "## Architecture documentation excerpt",
            "raw-source": "## Candidate source files "
                          "(selected by keyword retrieval, may be wrong)",
        }.get(d.source, f"## Context from {d.source}")
        parts += ["", label, d.text]
    if not descriptions:
        parts += ["", "## Candidate code", "(the retriever proposed nothing for this ticket)"]
    parts += ["", "Adjudicate: does the code support this ticket's claim?"]
    return "\n".join(parts)


def cache_key(model: str, system: str, prompt: str) -> str:
    h = hashlib.sha256()
    h.update(f"{PROMPT_VERSION}\x00{model}\x00{system}\x00{prompt}".encode())
    return h.hexdigest()[:32]


class StructuredCaller:
    """One model call returning a validated object, cached by content.

    Shared by Stage 3 and Stage 4 so there is exactly one place that knows
    how to call the API, what a cache key is, and how spend is counted.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        cache_dir: str | Path | None = None,
        effort: str = "medium",
        max_tokens: int = 4000,
    ):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def _cached(self, key: str) -> dict | None:
        if not self.cache_dir:
            return None
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("corrupt cache entry %s — ignoring", p.name)
        return None

    def _store(self, key: str, payload: dict) -> None:
        if self.cache_dir:
            (self.cache_dir / f"{key}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    def call(self, system: str, prompt: str, output_format, label: str = "") -> tuple[dict, Usage]:
        key = cache_key(self.model, system, prompt)
        hit = self._cached(key)
        if hit is not None:
            # Report the original spend, flagged as already paid, so a ledger
            # built from cached artifacts still knows what they cost.
            prior = hit.pop("_usage", None) or {}
            return hit, Usage(cached_calls=1,
                              prior_input_tokens=prior.get("input_tokens", 0),
                              prior_output_tokens=prior.get("output_tokens", 0))

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            system=[{
                "type": "text",
                "text": system,
                # The system prompt is identical across every call. Whether it
                # is long enough to reach the model's minimum cacheable prefix
                # is reported by usage.cache_read_input_tokens — check it rather
                # than assuming this helps.
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
            output_format=output_format,
        )

        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError(
                f"{label or 'call'}: model returned no parseable result "
                f"(stop_reason={response.stop_reason})"
            )
        payload = parsed.model_dump()

        u = response.usage
        # Store what the call cost alongside the answer. Without this a cache
        # hit can report the tokens it did not spend but not the tokens the
        # original spent, so the recorded cost of an artifact decays to zero
        # as it is re-run. `_usage` is namespaced so it can never collide with
        # a schema field.
        self._store(key, {**payload, "_usage": {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "model": self.model,
        }})

        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
            api_calls=1,
        )
        return payload, usage


class Adjudicator(StructuredCaller):
    """Stage 3: does the code support what this ticket claims?"""

    def adjudicate(
        self,
        ticket: Ticket,
        descriptions: list[Description],
    ) -> tuple[Verdict, Usage]:
        prompt = build_prompt(ticket, descriptions)
        sources = "+".join(d.source for d in descriptions) or "none"
        payload, usage = self.call(SYSTEM, prompt, VerdictOut, label=ticket.uid)
        return self._to_verdict(ticket.uid, payload, sources), usage

    def _to_verdict(self, uid: str, payload: dict, sources: str) -> Verdict:
        return Verdict(
            uid=uid,
            verdict=payload["verdict"],
            confidence=payload["confidence"],
            reasoning=payload["reasoning"],
            evidence=[VerdictEvidence(file=e["file"], symbol=e.get("symbol", ""),
                                      why=e["why"])
                      for e in payload.get("evidence", [])],
            status_conflict=bool(payload.get("status_conflict")),
            describer=sources,
        )


def run(
    tickets: list[Ticket],
    candidates: dict[str, TicketCandidates],
    corpus: Corpus,
    bundle,
    adjudicator: Adjudicator,
    on_result=None,
    workers: int = 6,
) -> tuple[list[Verdict], Usage]:
    """Adjudicate every ticket given. Caller decides which tickets those are.

    Tickets are independent, so they run concurrently. Results are returned
    in input order regardless of completion order — a verdicts file whose
    order depends on network timing would produce noisy diffs between runs.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    empty = TicketCandidates(uid="", candidates=[])
    total = Usage()
    lock = threading.Lock()

    def one(t: Ticket):
        tc = candidates.get(t.uid, empty)
        descriptions = bundle.describe_all(tc, corpus) if tc.candidates else []
        try:
            verdict, usage = adjudicator.adjudicate(t, descriptions)
        except Exception as exc:                          # noqa: BLE001
            logger.error("%s: %s", t.uid, exc)
            with lock:
                total.failures += 1
                total.first_error = total.first_error or f"{t.uid}: {exc}"
            return None
        verdict.cost_usd = round(usage.original_cost(adjudicator.model), 6)
        with lock:
            total.add(usage)
            if on_result:
                on_result(t, verdict, usage)
        return verdict

    if workers <= 1:
        results = [one(t) for t in tickets]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, tickets))

    return [v for v in results if v is not None], total


def api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
