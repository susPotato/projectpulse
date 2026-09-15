"""Tests for the prompt → TaskType classifier."""
from __future__ import annotations

import pytest

from cowork_local.core.routing.classifier import classify
from cowork_local.core.routing.models import TaskType


@pytest.mark.parametrize("text,expected", [
    ("Write a Python function to reverse a linked list", TaskType.CODING),
    ("Debug this traceback, my import fails", TaskType.CODING),
    ("Summarize this article in one sentence", TaskType.SUMMARIZATION),
    ("Write a poem about the ocean", TaskType.CREATIVE),
    ("Why does the bat and ball puzzle trip people up? Prove it step by step", TaskType.REASONING),
    ("What is the capital of France?", TaskType.QA),
])
def test_classifies_common_prompts(text, expected):
    assert classify(text) == expected


def test_vietnamese_prompts():
    assert classify("Viết hàm Python tính giai thừa") == TaskType.CODING
    assert classify("Tóm tắt đoạn văn này giúp tôi") == TaskType.SUMMARIZATION
    assert classify("Viết một bài thơ về mùa thu") == TaskType.CREATIVE


def test_ambiguous_defaults_to_qa():
    assert classify("hello there") == TaskType.QA
    assert classify("") == TaskType.QA


def test_llm_fallback_used_when_heuristic_unsure():
    called = {"n": 0}

    def fake_llm(text):
        called["n"] += 1
        return "reasoning"

    # A prompt with no keywords → heuristic unsure → LLM fallback consulted.
    result = classify("xyzzy plugh", llm_classifier=fake_llm)
    assert called["n"] == 1
    assert result == TaskType.REASONING


def test_llm_fallback_not_used_when_heuristic_confident():
    called = {"n": 0}

    def fake_llm(text):
        called["n"] += 1
        return "qa"

    result = classify("Write a Python function", llm_classifier=fake_llm)
    assert called["n"] == 0  # heuristic was confident; no LLM call
    assert result == TaskType.CODING


def test_llm_fallback_bad_value_defaults_to_qa():
    result = classify("xyzzy plugh", llm_classifier=lambda t: "not-a-task-type")
    assert result == TaskType.QA
