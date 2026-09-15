"""Dynamic benchmark probing + LLM-as-judge quality scoring.

For each ``(model, task_type)`` we send a fixed benchmark prompt, measure real
latency, and score the answer's quality with a single **fixed, cheap judge
model** (configurable) using a rubric that returns strict JSON. Using the same
judge for every candidate keeps the comparison fair, and never letting a model
judge its own answer avoids self-grading bias.

Concurrency is bounded **per provider** with a semaphore (the sync analogue of
``asyncio.Semaphore``, since the app's Provider layer is ``requests``-based) so
a reassess never trips a provider's rate limit. Every probe is timed and every
exception is captured as ``success=False`` — a dead model scores 0, it never
crashes the run.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

from .clients import CompletionResult, ProbeClient
from .models import ProbeResult, TaskType

# One fixed prompt per task type — shared by EVERY candidate so scores are
# comparable. Kept short to keep probing cheap (probes cost real tokens).
BENCHMARK_TASKS: Dict[TaskType, str] = {
    TaskType.QA: (
        "Answer concisely and correctly: What is the capital of Australia, and "
        "name one reason it — rather than Sydney — was chosen as the capital?"
    ),
    TaskType.CODING: (
        "Write a correct Python function `is_balanced(s: str) -> bool` that returns "
        "True iff the brackets (), [], {} in `s` are balanced and properly nested. "
        "Return only the function, no explanation."
    ),
    TaskType.REASONING: (
        "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the "
        "ball. How much does the ball cost? Show the reasoning in one or two lines "
        "and give the final numeric answer."
    ),
    TaskType.SUMMARIZATION: (
        "Summarize the following in exactly one sentence: 'Photosynthesis is the "
        "process by which green plants, algae and some bacteria convert light "
        "energy, usually from the sun, into chemical energy stored in glucose, "
        "releasing oxygen as a by-product and forming the base of most food chains.'"
    ),
    TaskType.CREATIVE: (
        "Write a vivid two-line poem about a lighthouse at dawn. Use one concrete "
        "sensory image per line."
    ),
}

# Rubric handed to the judge. It must return STRICT JSON: {"score": 0..1}.
_JUDGE_RUBRIC = (
    "You are grading an AI assistant's answer to a {task} task on a 0.0–1.0 scale.\n"
    "Judge correctness, relevance and quality only — ignore verbosity/style unless "
    "it harms the answer. 0.0 = wrong/empty/off-task, 0.5 = partially correct, "
    "1.0 = fully correct and high quality.\n\n"
    "TASK PROMPT:\n{prompt}\n\nANSWER TO GRADE:\n{answer}\n\n"
    'Respond with ONLY a JSON object, no prose: {{"score": <float 0..1>}}'
)

# JudgeFn: given (task_type, prompt, answer) → quality score in [0,1].
JudgeFn = Callable[[TaskType, str, str], float]

_SCORE_RE = re.compile(r'"score"\s*:\s*([0-9]*\.?[0-9]+)')


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, float(x)))


def parse_judge_score(text: str) -> float:
    """Extract the 0..1 score from a judge reply, tolerating minor noise.

    Tries strict JSON first, then a regex fallback for models that wrap the
    JSON in prose despite instructions. Returns 0.0 if nothing parseable.
    """
    if not text:
        return 0.0
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict) and "score" in obj:
            return _clamp01(obj["score"])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    m = _SCORE_RE.search(text)
    if m:
        try:
            return _clamp01(float(m.group(1)))
        except ValueError:
            return 0.0
    return 0.0


def make_judge(
    client: ProbeClient,
    judge_provider: str,
    judge_model: str,
) -> JudgeFn:
    """Build a :data:`JudgeFn` bound to one fixed judge model.

    The same judge grades every candidate (fair comparison). The orchestrator
    is responsible for not pointing the judge at the model being graded.
    """

    def judge(task_type: TaskType, prompt: str, answer: str) -> float:
        rubric = _JUDGE_RUBRIC.format(
            task=task_type.value, prompt=prompt, answer=(answer or "")[:4000]
        )
        messages = [{"role": "user", "content": rubric}]
        result = client.complete(judge_provider, judge_model, messages)
        if not result.ok:
            return 0.0
        return parse_judge_score(result.text)

    return judge


def probe_model(
    client: ProbeClient,
    provider: str,
    model_id: str,
    task_type: TaskType,
    judge: JudgeFn,
) -> ProbeResult:
    """Run one benchmark task against one model and score it.

    Measures wall-clock latency around the completion call. Any exception or a
    provider-level error becomes ``success=False`` with the error captured; the
    quality score then stays 0.
    """
    prompt = BENCHMARK_TASKS[task_type]
    messages = [{"role": "user", "content": prompt}]

    started = time.perf_counter()
    try:
        result: CompletionResult = client.complete(provider, model_id, messages)
    except Exception as exc:  # noqa: BLE001 — defensive; client should not raise
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ProbeResult(latency_ms=elapsed_ms, success=False, error=str(exc))
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if not result.ok:
        return ProbeResult(latency_ms=elapsed_ms, success=False, error=result.error)

    quality = judge(task_type, prompt, result.text)
    return ProbeResult(
        latency_ms=elapsed_ms,
        success=True,
        quality_score=quality,
        tokens_out=result.tokens_out,
    )


class _PerProviderSemaphores:
    """Lazily-created, per-provider bounded semaphores for rate-limit safety."""

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._sems: Dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()

    def get(self, provider: str) -> threading.Semaphore:
        with self._lock:
            sem = self._sems.get(provider)
            if sem is None:
                sem = threading.Semaphore(self._limit)
                self._sems[provider] = sem
            return sem


def probe_candidates(
    client: ProbeClient,
    candidates: List[Tuple[str, str]],
    task_types: List[TaskType],
    judge: JudgeFn,
    *,
    per_provider_concurrency: int = 2,
    max_workers: int = 8,
    call_counter: Optional[Callable[[], None]] = None,
) -> Dict[str, Dict[str, ProbeResult]]:
    """Probe every ``(provider, model_id)`` on every ``task_type`` concurrently.

    Concurrency is capped globally by ``max_workers`` and, more importantly,
    **per provider** by ``per_provider_concurrency`` — so many models on one
    provider never fire more than N calls at once at that provider.

    ``call_counter`` (if given) is invoked once per probe call, letting the
    orchestrator log "how many API calls this reassess made".

    Returns ``{candidate_key: {task_type_value: ProbeResult}}``.
    """
    from .models import candidate_key

    sems = _PerProviderSemaphores(per_provider_concurrency)
    results: Dict[str, Dict[str, ProbeResult]] = {}
    results_lock = threading.Lock()

    def _one(provider: str, model_id: str, task_type: TaskType) -> None:
        sem = sems.get(provider)
        with sem:
            if call_counter is not None:
                call_counter()
            probe = probe_model(client, provider, model_id, task_type, judge)
        key = candidate_key(provider, model_id)
        with results_lock:
            results.setdefault(key, {})[task_type.value] = probe

    jobs = [
        (provider, model_id, task_type)
        for (provider, model_id) in candidates
        for task_type in task_types
    ]
    if not jobs:
        return results

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = [pool.submit(_one, p, m, t) for (p, m, t) in jobs]
        for f in as_completed(futures):
            # _one swallows its own errors into a ProbeResult; this is just to
            # surface any truly-unexpected exception without killing the pool.
            f.result()

    return results


__all__ = [
    "BENCHMARK_TASKS",
    "JudgeFn",
    "make_judge",
    "probe_model",
    "probe_candidates",
    "parse_judge_score",
]
