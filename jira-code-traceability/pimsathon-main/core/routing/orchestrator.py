"""Orchestrate a full assessment run: enrich → probe → score → store.

``check_and_update`` is the single entry point the API/scheduler call. It:

1. Enriches each candidate's static metadata (price / context / capabilities).
2. Probes every candidate on every task type concurrently, bounded per provider
   (delegated to ``prober.probe_candidates``), grading each answer with one
   fixed judge.
3. Computes fit scores per task type under the active policy.
4. Persists the results atomically, backing up the previous version to history
   first (so a model that *degrades* between runs can be spotted).

Cost-awareness: probing spends real tokens, so this runs only on a schedule,
when a model is added, or on an explicit reassess — never per chat turn. The
number of API calls made is logged so the cost is visible.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Tuple

from .clients import ProbeClient
from .metadata import LLMDeclarer, enrich
from .models import ModelAssessment, Policy, TaskType, candidate_key
from .prober import JudgeFn, make_judge, probe_candidates
from .scorer import compute_fit_score
from .store import AssessmentStore, utc_now_iso

logger = logging.getLogger("cowork_local.routing")

# A candidate to assess: (provider, model_id, tier|None).
Candidate = Tuple[str, str, Optional[str]]


def build_assessment(
    provider: str,
    model_id: str,
    tier: Optional[str],
    probes: Dict[str, "object"],
    policy: Policy,
    *,
    config=None,
    task_types: Optional[List[TaskType]] = None,
    llm_declarer: Optional[LLMDeclarer] = None,
) -> ModelAssessment:
    """Assemble one :class:`ModelAssessment` from its probe results.

    Pure except for metadata enrichment (which may read the config price
    sheet). Kept separate from I/O so it's unit-testable without any network.
    """
    from .models import ProbeResult

    task_types = task_types or list(TaskType)
    meta = enrich(provider, model_id, config=config, tier=tier, llm_declarer=llm_declarer)

    # A model that failed EVERY probe is effectively unavailable this run.
    typed_probes: Dict[str, ProbeResult] = {}
    any_success = False
    for tt in task_types:
        probe = probes.get(tt.value)
        if isinstance(probe, ProbeResult):
            typed_probes[tt.value] = probe
            any_success = any_success or probe.success
    if typed_probes and not any_success:
        meta.available = False

    fit_scores: Dict[str, float] = {}
    for tt in task_types:
        probe = typed_probes.get(tt.value)
        if probe is not None:
            fit_scores[tt.value] = compute_fit_score(meta, probe, policy)

    return ModelAssessment(
        metadata=meta,
        probes=typed_probes,
        fit_scores=fit_scores,
        assessed_at=utc_now_iso(),
    )


def check_and_update(
    candidates: List[Candidate],
    client: ProbeClient,
    *,
    judge: Optional[JudgeFn] = None,
    judge_provider: str = "",
    judge_model: str = "",
    store: Optional[AssessmentStore] = None,
    config=None,
    policy: Policy = Policy.BALANCED,
    task_types: Optional[List[TaskType]] = None,
    per_provider_concurrency: int = 2,
    max_workers: int = 8,
    llm_declarer: Optional[LLMDeclarer] = None,
    persist: bool = True,
) -> Dict[str, ModelAssessment]:
    """Assess every candidate and (optionally) persist the results.

    Provide either a ready ``judge`` callable, or ``judge_provider`` +
    ``judge_model`` to build the standard rubric judge from ``client``.

    Returns ``{candidate_key: ModelAssessment}``. ``persist=False`` skips the
    store write (used by tests / dry runs).
    """
    task_types = task_types or list(TaskType)
    if not candidates:
        logger.info("routing.reassess: no candidates configured — nothing to do")
        return {}

    if judge is None:
        if not (judge_provider and judge_model):
            raise ValueError("check_and_update needs either `judge` or judge_provider+judge_model")
        judge = make_judge(client, judge_provider, judge_model)

    judge_key = candidate_key(judge_provider, judge_model) if judge_provider else None
    if judge_key and any(candidate_key(p, m) == judge_key for p, m, _ in candidates):
        # The judge is also a candidate — its own answers are self-graded. We
        # keep it routable (it may genuinely be a fine cheap model) but flag the
        # bias so it's not mistaken for an independent score.
        logger.warning(
            "routing.reassess: judge model %s is also a candidate — its quality "
            "scores are self-judged and may be optimistic", judge_key,
        )

    # --- count API calls so the cost of a reassess is visible ------------- #
    call_count = {"n": 0}

    def _tick() -> None:
        call_count["n"] += 1

    pairs = [(p, m) for (p, m, _tier) in candidates]
    probe_map = probe_candidates(
        client, pairs, task_types, judge,
        per_provider_concurrency=per_provider_concurrency,
        max_workers=max_workers,
        call_counter=_tick,
    )

    assessments: Dict[str, ModelAssessment] = {}
    for (provider, model_id, tier) in candidates:
        key = candidate_key(provider, model_id)
        assessments[key] = build_assessment(
            provider, model_id, tier,
            probe_map.get(key, {}), policy,
            config=config, task_types=task_types, llm_declarer=llm_declarer,
        )

    # ~1 probe call + 1 judge call per (candidate, task). The counter above only
    # counts probe calls (judge calls happen inside probe_model), so report both.
    probe_calls = call_count["n"]
    logger.info(
        "routing.reassess: %d candidate(s) × %d task(s) → ~%d probe calls "
        "(+~%d judge calls), policy=%s",
        len(candidates), len(task_types), probe_calls, probe_calls, policy.value,
    )

    if persist:
        store = store or AssessmentStore()
        store.save(assessments, policy)
        store.prune_history(keep=30)

    return assessments


__all__ = ["check_and_update", "build_assessment", "Candidate"]
