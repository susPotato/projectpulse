"""Classify a user prompt into a :class:`TaskType`.

Routing runs on every turn, so classification must be cheap — a keyword
heuristic first, with an optional one-shot LLM fallback only when the heuristic
is unsure. The heuristic is intentionally conservative: it defaults to ``QA``
(the safest general bucket) rather than mis-routing an ambiguous prompt.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

from .models import TaskType

# Signal words per task type. Matched case-insensitively on word boundaries.
# Ordered by specificity when scoring ties (CODING/REASONING beat QA).
_KEYWORDS: dict[TaskType, List[str]] = {
    TaskType.CODING: [
        "code", "function", "class", "bug", "debug", "refactor", "compile",
        "stack trace", "traceback", "python", "javascript", "typescript",
        "java", "c++", "golang", "rust", "sql", "regex", "api", "endpoint",
        "unit test", "pytest", "npm", "docker", "git", "implement", "algorithm",
        "syntax", "exception", "import", "def ", "async", "lập trình", "hàm",
        "sửa lỗi", "biên dịch",
    ],
    TaskType.REASONING: [
        "why", "prove", "explain why", "reason", "logic", "deduce", "infer",
        "step by step", "step-by-step", "solve", "calculate", "how many",
        "puzzle", "riddle", "strategy", "trade-off", "tradeoff", "analyze",
        "compare and", "chứng minh", "suy luận", "tính toán", "phân tích",
    ],
    TaskType.SUMMARIZATION: [
        "summarize", "summary", "tl;dr", "tldr", "condense", "shorten",
        "key points", "in short", "brief", "recap", "abstract of", "gist",
        "tóm tắt", "rút gọn", "tóm lược",
    ],
    TaskType.CREATIVE: [
        "poem", "story", "write a", "creative", "imagine", "fiction", "lyrics",
        "song", "haiku", "screenplay", "dialogue", "brainstorm", "slogan",
        "tagline", "marketing copy", "viết truyện", "bài thơ", "sáng tạo",
        "kịch bản",
    ],
    TaskType.QA: [
        "what is", "who is", "when did", "where is", "define", "meaning of",
        "how do i", "how to", "is it", "does", "can you tell", "fact",
        "là gì", "ai là", "khi nào", "ở đâu", "định nghĩa",
    ],
}

# Precompiled boundary regexes; ASCII \b doesn't hug Vietnamese diacritics well,
# so multi-word/diacritic phrases fall back to plain substring matching.
_COMPILED: dict[TaskType, List[Tuple[str, Optional[re.Pattern]]]] = {}
for _tt, _words in _KEYWORDS.items():
    entries: List[Tuple[str, Optional[re.Pattern]]] = []
    for w in _words:
        if w.isascii() and " " not in w and w.strip().isalpha():
            entries.append((w, re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE)))
        else:
            entries.append((w, None))  # substring match
    _COMPILED[_tt] = entries

# Tie-break priority when multiple task types score equally.
_PRIORITY = [
    TaskType.CODING,
    TaskType.REASONING,
    TaskType.SUMMARIZATION,
    TaskType.CREATIVE,
    TaskType.QA,
]

# LLM fallback: given the prompt, return a TaskType value string.
LLMClassifier = Callable[[str], str]


def _heuristic_scores(text: str) -> dict[TaskType, int]:
    low = (text or "").lower()
    scores: dict[TaskType, int] = {tt: 0 for tt in TaskType}
    for tt, entries in _COMPILED.items():
        for raw, pat in entries:
            if pat is not None:
                if pat.search(low):
                    scores[tt] += 1
            elif raw in low:
                scores[tt] += 1
    return scores


def classify(
    text: str,
    *,
    llm_classifier: Optional[LLMClassifier] = None,
    min_confidence: int = 1,
) -> TaskType:
    """Return the most likely :class:`TaskType` for ``text``.

    Uses the keyword heuristic first. If nothing scores at least
    ``min_confidence`` and an ``llm_classifier`` is provided, defers to it once;
    otherwise defaults to :attr:`TaskType.QA`.
    """
    scores = _heuristic_scores(text)
    best_score = max(scores.values()) if scores else 0

    if best_score >= min_confidence:
        # Highest score, ties broken by _PRIORITY order.
        for tt in _PRIORITY:
            if scores[tt] == best_score:
                return tt

    if llm_classifier is not None:
        try:
            raw = (llm_classifier(text) or "").strip().lower()
            return TaskType(raw)
        except Exception:  # noqa: BLE001 — bad/failed classification → default
            pass

    return TaskType.QA


__all__ = ["classify", "LLMClassifier", "BENCHMARK_HINT"]

# Small doc alias so callers can show which task types exist.
BENCHMARK_HINT = [tt.value for tt in TaskType]
