"""Guards for the narration layer: the validator gate and the fallback prose.

The validator tests are adversarial on purpose. They are written as the failures
a careless model actually produces - a number rewritten as a word, a task id that
looks plausible, a "because" attached to two facts nobody linked - rather than as
coverage of the code that catches them.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.api.schemas.insight import (
    CausalLink,
    ChainStep,
    DataQuality,
    Finding,
    InsightBundle,
    TimeInterval,
)
from app.narration.client import NarrationUnavailable, build_brief, narrate
from app.narration.fallback import QUESTION_HEADINGS, render_narrative
from app.narration.providers import (
    DEFAULT_MODELS,
    PROVIDERS,
    ModelConfig,
    _import,
    _nonempty,
    drafter_for,
)
from app.narration.validator import contains_quantity, validate_draft

FACTS = {"days": "12", "count": "6", "task": "WBS-108"}
ENTITIES = {"WBS-108", "WBS-114", "QA-001"}

GOOD = (
    "Environment Setup slipped by {{days}} days. {{count}} downstream items "
    "moved in step. The team should re-baseline before the next review."
)


def validate(draft, **kw):
    kw.setdefault("facts", FACTS)
    kw.setdefault("allowed_entities", ENTITIES)
    return validate_draft(draft, **kw)


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_a_clean_draft_passes_all_eight_stages():
    result = validate(GOOD)

    assert result.ok, result.summary
    assert len(result.stages_run) == 8


def test_a_literal_number_is_rejected():
    """The load-bearing rule: a model may never write a digit."""
    result = validate("Environment Setup slipped by 12 days and nothing else moved.")

    assert not result.ok
    assert any(i.stage == "no_literal_digits" for i in result.issues)


def test_a_number_hidden_in_prose_is_still_rejected():
    result = validate(
        "Environment Setup slipped and 6 downstream items moved with it, roughly."
    )

    assert any(i.stage == "no_literal_digits" for i in result.issues)


def test_an_invented_token_is_rejected():
    """A model inventing a figure has to invent a token name to carry it."""
    result = validate(
        "Environment Setup slipped by {{made_up_number}} days, which is a problem."
    )

    assert any(i.stage == "known_tokens" for i in result.issues)


def test_dropping_a_required_number_is_rejected():
    """Quietly omitting a figure is a silent downgrade of the finding."""
    result = validate(
        "Environment Setup slipped and several downstream items moved with it.",
        required_tokens={"days"},
    )

    assert any(i.stage == "required_tokens" for i in result.issues)


def test_an_invented_task_id_is_rejected():
    result = validate(
        "Environment Setup slipped, and WBS-999 moved with it as a consequence here."
    )

    issue = next(i for i in result.issues if i.stage == "known_entities")
    assert "WBS-999" in issue.detail


def test_a_real_task_id_is_accepted():
    assert validate(
        "Environment Setup slipped by {{days}} days, and WBS-114 moved with it."
    ).ok


def test_asserting_a_cause_without_a_chain_is_rejected():
    """The one claim the whole system exists to make carefully."""
    result = validate(
        "The QA queue has stalled because Environment Setup slipped by {{days}} days."
    )

    assert any(i.stage == "no_unsupported_causation" for i in result.issues)


def test_the_same_sentence_passes_when_a_chain_exists():
    assert validate(
        "The QA queue has stalled because Environment Setup slipped by {{days}} days.",
        has_causal_link=True,
    ).ok


def test_stating_a_projection_as_a_certainty_is_rejected():
    result = validate(
        "The UAT milestone will definitely be missed given the {{days}} day slip.",
        has_causal_link=True,
    )

    assert any(i.stage == "no_overclaimed_certainty" for i in result.issues)


def test_model_meta_commentary_is_rejected():
    result = validate(
        "As an AI, I cannot be certain, but the schedule slipped by {{days}} days."
    )

    assert any(i.stage == "no_model_artifacts" for i in result.issues)


def test_a_code_fence_is_rejected():
    result = validate(GOOD + "\n```json\n{}\n```")

    assert any(i.stage == "no_model_artifacts" for i in result.issues)


def test_an_empty_draft_is_rejected():
    assert any(i.stage == "shape" for i in validate("   ").issues)


def test_every_stage_runs_even_after_one_fails():
    """A prompt is easier to fix when every objection is visible at once."""
    result = validate("As an AI I must say 12 things happened because of WBS-999.")

    assert len(result.stages_run) == 8
    assert len({i.stage for i in result.issues}) >= 3


def test_the_summary_is_one_line_suitable_for_a_fallback_reason():
    summary = validate("only 3 words").summary

    assert "\n" not in summary
    assert summary != "passed"


# --------------------------------------------------------------------------
# The fallback
# --------------------------------------------------------------------------


def _bundle(**kw) -> InsightBundle:
    return InsightBundle(
        project_id="p",
        generated_at=datetime(2026, 3, 22, tzinfo=timezone.utc),
        as_of=datetime(2026, 3, 22, tzinfo=timezone.utc),
        **kw,
    )


def _finding(**kw) -> Finding:
    base = dict(
        id="f",
        category="schedule_risk",
        severity="high",
        headline="The plan cannot hold.",
        recommendation="Re-baseline.",
    )
    base.update(kw)
    return Finding(**base)


def _link() -> CausalLink:
    step = ChainStep(
        entity_id="excel:Task:1:WBS-108",
        entity_label="WBS-108",
        field="planned_end",
        occurred=TimeInterval(
            lower=datetime(2026, 3, 2, tzinfo=timezone.utc),
            upper=datetime(2026, 3, 6, tzinfo=timezone.utc),
            precision="bounded",
        ),
    )
    return CausalLink(
        template_id="dependency_slip_hits_successor",
        template_name="Dependency slip hit its successor",
        question="Which of my tasks slipped because something upstream slipped?",
        cause=step,
        effect=step,
        evidence_basis="dependency_edge",
        lag_days_min=4.0,
        lag_days_max=12.0,
        ordering_basis="bounded_disjoint",
    )


def test_an_empty_bundle_still_produces_a_complete_narrative():
    """The offline safety net must never yield a blank page."""
    text = render_narrative(_bundle())

    for heading in ("What is at risk", "Why it is happening", "What to do next"):
        assert heading in text


def test_with_no_chain_the_narrative_refuses_to_explain():
    text = render_narrative(_bundle(findings=[_finding()]))

    assert "No cause can be established" in text


def test_a_chain_produces_an_explanation_naming_its_basis():
    text = render_narrative(
        _bundle(findings=[_finding(category="root_cause", causal_link=_link())])
    )

    assert "dependency stated in the schedule sheet" in text


def test_a_bounded_ordering_is_explained_rather_than_hidden():
    text = render_narrative(
        _bundle(findings=[_finding(category="root_cause", causal_link=_link())])
    )

    assert "range rather than a point" in text


def test_an_inferred_edge_caveat_reaches_the_narrative():
    text = render_narrative(
        _bundle(
            findings=[_finding()],
            data_quality=DataQuality(depends_on_inferred_edges=True),
        )
    )

    assert "inferred from" in text


def test_the_narrative_never_invents_a_number():
    """It selects sentences; it does not compute. Every digit comes from a finding."""
    text = render_narrative(_bundle(findings=[_finding(headline="Slipped by 12 days.")]))

    assert "12" in text
    assert text.count("12") == 1


def test_the_explanation_leads_with_the_chain_not_the_summary_about_chains():
    """An informational finding can carry a chain to illustrate itself.

    Leading with "2 of 50 findings trace to a dependency" tells a PM how well
    evidenced the answer is without ever giving them the answer.
    """
    summary = _finding(
        id="root_cause_available",
        category="root_cause",
        severity="info",
        headline="2 of 50 findings trace to a stated dependency.",
        causal_link=_link(),
        evidence_basis="dependency_edge",
    )
    real = _finding(
        id="chain:dependency_slip_hits_successor:0",
        category="root_cause",
        severity="high",
        headline="WBS-108 moved later; WBS-114 moved with it.",
        causal_link=_link(),
        evidence_basis="dependency_edge",
    )

    text = render_narrative(_bundle(findings=[summary, real]))
    why = text.split("Why it is happening\n")[1].split("\n\n")[0]

    assert why.startswith("WBS-108 moved later")


def test_a_better_evidenced_chain_outranks_a_weaker_one_in_the_explanation():
    weak = _finding(
        id="chain:blocked_work_stalls_qa:0",
        category="root_cause",
        severity="medium",
        headline="Something and something else moved together.",
        causal_link=_link(),
        evidence_basis="same_project",
    )
    strong = _finding(
        id="chain:dependency_slip_hits_successor:1",
        category="root_cause",
        severity="medium",
        headline="WBS-108 moved later; WBS-114 moved with it.",
        causal_link=_link(),
        evidence_basis="dependency_edge",
    )

    text = render_narrative(_bundle(findings=[weak, strong]))
    why = text.split("Why it is happening\n")[1].split("\n\n")[0]

    assert why.startswith("WBS-108 moved later")


# --------------------------------------------------------------------------
# The model client
#
# No network anywhere. `narrate` takes its drafter as an argument precisely so
# the interesting cases - a model that invents a number, drops a heading, or
# times out - are three lines of stub each rather than a recorded cassette.
# --------------------------------------------------------------------------


def _model_bundle() -> InsightBundle:
    """A bundle shaped like the real one: tokenised prose beside its facts."""
    return _bundle(
        findings=[
            _finding(
                headline="The plan cannot hold: 4 task(s) are late by 34 days.",
                headline_template=(
                    "The plan cannot hold: {{tasks_inconsistent}} task(s) are "
                    "late by {{max_propagated_days}} days."
                ),
                recommendation="Re-baseline WBS-108.",
                recommendation_template="Re-baseline WBS-108.",
                facts={"tasks_inconsistent": "4", "max_propagated_days": "34"},
                causal_link=_link(),
            )
        ],
        data_quality=DataQuality(rows_rejected=2, changes_total=27),
    )


def _draft_of(bundle: InsightBundle) -> str:
    """A well-behaved draft: every heading present, every figure a token."""
    brief = build_brief(bundle)
    figures = " ".join("{{" + name + "}}" for name in sorted(brief.required_tokens))
    return "\n\n".join(
        f"{heading}\nWBS-108 is the problem here, involving {figures} in total."
        for heading in QUESTION_HEADINGS[:4]
    )


def test_the_brief_never_contains_a_figure():
    """The rule the module imposes on the model, imposed on the module.

    This is the load-bearing test. A brief that leaks a value lets a model copy
    it instead of emitting the token for it, and a copied number is
    indistinguishable on the page from a computed one - so the digit rule would
    go on passing while having stopped meaning anything.
    """
    brief = build_brief(_model_bundle())

    assert not contains_quantity(brief.user)
    # And the value really is available for substitution, so withholding it
    # from the prompt costs the narrative nothing.
    assert brief.facts["fa_max_propagated_days"] == "34"


def test_the_brief_shows_the_tokenised_headline_not_the_substituted_one():
    brief = build_brief(_model_bundle())

    assert "{{fa_max_propagated_days}}" in brief.user
    assert "34 days" not in brief.user


def test_a_finding_with_no_template_is_described_rather_than_quoted():
    """A headline holding numbers and no template is not shown at all.

    The safe direction: describing the finding by category and severity loses
    detail, where quoting the substituted prose would hand over the figures.
    """
    bundle = _bundle(
        findings=[_finding(headline="Slipped by 34 days.", headline_template="")]
    )
    brief = build_brief(bundle)

    assert not contains_quantity(brief.user)
    assert "Slipped by" not in brief.user
    assert "schedule_risk, severity high" in brief.user


def test_two_findings_sharing_a_field_do_not_share_a_value():
    """Token names are namespaced per finding, so a flat merge cannot happen."""
    bundle = _bundle(
        findings=[
            _finding(
                severity="high",
                headline_template="{{days}} days.",
                facts={"days": "34"},
            ),
            _finding(
                severity="low",
                headline_template="{{days}} days.",
                facts={"days": "2"},
            ),
        ]
    )
    brief = build_brief(bundle)

    assert brief.facts["fa_days"] == "34"
    assert brief.facts["fb_days"] == "2"


def test_a_good_draft_is_served_as_the_model_narrative():
    bundle = _model_bundle()
    draft = _draft_of(bundle)

    outcome = narrate(bundle, drafter=lambda system, user: draft)

    assert outcome.source == "model"
    assert outcome.fallback_reason is None
    # Substitution happens after validation, so the figure reaches the page.
    assert "34" in outcome.narrative
    assert "{{" not in outcome.narrative


def test_a_draft_that_invents_a_number_falls_back_to_the_template():
    bundle = _model_bundle()
    invented = _draft_of(bundle).replace(
        "WBS-108 is the problem", "It slipped 99 days"
    )

    outcome = narrate(bundle, drafter=lambda system, user: invented)

    assert outcome.source == "template"
    assert "no_literal_digits" in (outcome.fallback_reason or "")
    assert "99" not in outcome.narrative


def test_a_draft_missing_a_heading_falls_back():
    """A missing heading leaves that panel of the insight screen blank."""
    bundle = _model_bundle()
    truncated = _draft_of(bundle).split("What to do next")[0]

    outcome = narrate(bundle, drafter=lambda system, user: truncated)

    assert outcome.source == "template"
    assert "What to do next" in (outcome.fallback_reason or "")


def test_a_rejected_draft_is_retried_with_the_objections_attached():
    """Why the validator reports every objection at once instead of the first."""
    bundle = _model_bundle()
    seen: list[str] = []

    def drafter(system: str, user: str) -> str:
        seen.append(user)
        # Bad first, good second - the shape of the ordinary failure.
        return _draft_of(bundle) if len(seen) > 1 else "It slipped 99 days entirely."

    outcome = narrate(bundle, drafter=drafter)

    assert outcome.source == "model"
    assert outcome.attempts == 2
    assert "no_literal_digits" in seen[1]
    assert "no_literal_digits" not in seen[0]


def test_a_drafter_that_raises_still_produces_a_narrative():
    """A dead socket is a downgrade, never a blank page."""

    def explode(system: str, user: str) -> str:
        raise NarrationUnavailable("connection reset")

    outcome = narrate(_model_bundle(), drafter=explode)

    assert outcome.source == "template"
    assert "connection reset" in (outcome.fallback_reason or "")
    for heading in QUESTION_HEADINGS[:4]:
        assert heading in outcome.narrative


def test_with_no_drafter_the_template_is_served_without_a_fallback_reason():
    """The default is not a downgrade, and must not read as one.

    `fallback_reason` means a model was asked and its answer was not used. Set
    on every bundle, it would make the ordinary configuration look broken.
    """
    outcome = narrate(_model_bundle())

    assert outcome.source == "template"
    assert outcome.fallback_reason is None
    assert outcome.narrative == render_narrative(_model_bundle())


def test_a_leaking_brief_is_refused_rather_than_sent():
    """`BriefLeak` is a bug, and it must surface as a fallback, not a crash."""
    bundle = _model_bundle()
    bundle.findings[0].headline_template = "The plan slipped 34 days."

    outcome = narrate(bundle, drafter=lambda system, user: "unreachable")

    assert outcome.source == "template"
    assert "brief rejected" in (outcome.fallback_reason or "")


def test_an_empty_bundle_still_briefs_and_still_narrates():
    outcome = narrate(_bundle(), drafter=lambda system, user: "unusable")

    assert outcome.source == "template"
    assert "What is at risk" in outcome.narrative


def test_every_task_the_model_may_name_is_named_in_the_brief():
    """`allowed_entities` and the brief's task list must be the same set.

    If they diverge, the prompt invites a mention the validator then rejects -
    which reads as a bad model rather than a bad brief.
    """
    brief = build_brief(_model_bundle())

    assert brief.allowed_entities
    for entity in brief.allowed_entities:
        assert entity in brief.user


# --------------------------------------------------------------------------
# The vendor adapters
#
# The point of these is not that three SDKs work - two of them are not
# installed. It is that the fence does not care which one answers, and that a
# vendor which is absent, blocked, truncated or silent produces a template with
# a reason rather than anything worse.
# --------------------------------------------------------------------------


def test_every_provider_resolves_to_a_drafter():
    for provider in PROVIDERS:
        assert callable(drafter_for(provider))


def test_an_unknown_provider_fails_at_configuration_not_mid_request():
    """A typo in PULSE_NARRATION_PROVIDER must not read as a dead model."""
    with pytest.raises(ValueError) as caught:
        drafter_for("claude-3")

    assert "anthropic" in str(caught.value)


def test_each_provider_has_a_default_model():
    assert set(DEFAULT_MODELS) == set(PROVIDERS)
    for provider in PROVIDERS:
        assert DEFAULT_MODELS[provider]


def test_an_explicit_model_overrides_the_default():
    """The escape hatch when a vendor renames a model - no code change."""
    cfg = ModelConfig(model="some-future-model")

    assert callable(drafter_for("openai", cfg))


def test_a_missing_sdk_becomes_a_fallback_with_an_install_hint():
    """An absent vendor package is a downgrade with instructions, not a crash.

    Driven through `_import` with a module that cannot exist, rather than by
    checking which SDKs happen to be installed here - a test that passes or
    fails on the contents of a venv tells you about the venv.
    """
    from app.narration.providers import EXTRAS

    for provider in EXTRAS:
        with pytest.raises(NarrationUnavailable) as caught:
            _import("a_vendor_sdk_that_does_not_exist", provider)

        assert "not installed" in str(caught.value)
        assert "pip install" in str(caught.value)

    # A provider with no entry needs no package, so there is nothing to be
    # missing. `fpt` speaks HTTP from the standard library - which makes it the
    # only one that cannot fail this way, not one that was forgotten.
    assert set(PROVIDERS) - set(EXTRAS) == {"fpt"}


def test_a_vendor_that_cannot_be_reached_still_leaves_a_narrative():
    """The whole point of the fence, with a real adapter failure message."""

    def unreachable(system: str, user: str) -> str:
        raise NarrationUnavailable(
            'the openai SDK is not installed; pip install -e ".[llm-openai]"'
        )

    outcome = narrate(_model_bundle(), drafter=unreachable)

    assert outcome.source == "template"
    assert "pip install" in (outcome.fallback_reason or "")
    for heading in QUESTION_HEADINGS[:4]:
        assert heading in outcome.narrative


def test_an_empty_completion_is_refused_rather_than_narrated():
    """A successful call returning nothing is the quiet failure every vendor has.

    Left alone it fails the validator as "draft is empty", which sends a reader
    to the prompt when the problem was the vendor.
    """
    with pytest.raises(NarrationUnavailable) as caught:
        _nonempty("   ", "openai")

    assert "empty" in str(caught.value)


def test_the_fence_treats_every_vendor_identically():
    """No vendor is trusted more than another, and none is trusted at all.

    Not a tautology about the current code so much as a guard against the
    tempting future edit - "Claude is reliable, skip a stage for it". Three
    distinguishable drafters, one bad draft, and the outcome must be identical
    down to the reason string.
    """
    bundle = _model_bundle()
    invented = _draft_of(bundle).replace("WBS-108 is the problem", "It slipped 99 days")

    def drafter_named(name):
        def draft(system: str, user: str) -> str:
            return invented

        draft.__name__ = f"{name}_drafter"
        return draft

    outcomes = [narrate(bundle, drafter=drafter_named(p)) for p in PROVIDERS]

    assert len({o.fallback_reason for o in outcomes}) == 1
    for outcome in outcomes:
        assert outcome.source == "template"
        assert "no_literal_digits" in (outcome.fallback_reason or "")
        assert "99" not in outcome.narrative


# --------------------------------------------------------------------------
# End to end, over HTTP, with no vendor
#
# The only test that exercises the whole model path as it actually runs: a real
# HTTP round trip, the real OpenAI adapter, the real eight-stage gate, and the
# real substitution. The server is local and answers in the
# `/v1/chat/completions` shape that vLLM, Ollama, LM Studio and an internal
# gateway all serve - which is also the evidence that a self-hosted open model
# needs no code change, only `OPENAI_BASE_URL`.
# --------------------------------------------------------------------------

_REQUIRED_LINE = re.compile(r"Figures the summary must state: (.+)")
_ANY_TOKEN = re.compile(r"\{\{\w+\}\}")


def _well_behaved_draft(user_prompt: str) -> str:
    """What a model that followed the brief would return.

    It reads the tokens the brief demands and places them in its own sentences.
    It writes no digit, because the brief it was given contains none.
    """
    match = _REQUIRED_LINE.search(user_prompt)
    figures = " and ".join(_ANY_TOKEN.findall(match.group(1))) if match else ""
    bodies = [
        f"The plan for this project cannot hold as written, involving {figures} "
        "in total, and WBS-108 is at the centre of it.",
        "WBS-108 moved and WBS-108 was the cause of what followed, connected by "
        "a dependency stated in the schedule sheet.",
        "Work downstream of WBS-108 inherits that movement.",
        "Re-baseline the affected tasks with their owners before the next "
        "steering review.",
    ]
    return "\n\n".join(
        f"{heading}\n{body}" for heading, body in zip(QUESTION_HEADINGS, bodies)
    )


class _ChatCompletions(BaseHTTPRequestHandler):
    """Answers `/v1/chat/completions` using whatever composer the server holds.

    The composer is an attribute of the server rather than a module global. The
    first version swapped the global to install a badly-behaved model, and the
    replacement then called the name it had just replaced - infinite recursion,
    surfacing as a connection error because the handler died mid-response.
    """

    def do_POST(self):  # noqa: N802 - the base class names it
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        user = next(
            (m["content"] for m in payload["messages"] if m["role"] == "user"), ""
        )
        body = json.dumps(
            {
                "id": "chatcmpl-local",
                "object": "chat.completion",
                "model": payload.get("model", "local"),
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": self.server.compose(user),
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        pass


@contextlib.contextmanager
def _local_openai_server(compose=_well_behaved_draft):
    server = HTTPServer(("127.0.0.1", 0), _ChatCompletions)
    server.compose = compose
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _openai_drafter():
    return drafter_for("openai", ModelConfig(model="local-open-model"))


requires_openai = pytest.mark.skipif(
    importlib.util.find_spec("openai") is None, reason="openai is an optional extra"
)


@requires_openai
def test_a_self_hosted_model_completes_the_whole_path(monkeypatch):
    """The end-to-end proof, and the on-premise story in one test.

    Everything here is real except the weights: the adapter, the HTTP call, the
    eight validation stages and the substitution. What it demonstrates is the
    property the whole design rests on - **the figures in the finished prose
    were put there by the server, after the draft was checked.** The model was
    never shown them and could not have changed them.
    """
    with _local_openai_server() as base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)
        monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
        outcome = narrate(_model_bundle(), drafter=_openai_drafter())

    assert outcome.source == "model", outcome.fallback_reason
    assert outcome.fallback_reason is None
    assert outcome.attempts == 1

    # Substituted after validation - the value came from `facts`, not the model.
    assert "34" in outcome.narrative
    assert "{{" not in outcome.narrative
    for heading in QUESTION_HEADINGS[:4]:
        assert heading in outcome.narrative


@requires_openai
def test_a_self_hosted_model_that_writes_a_digit_is_still_refused(monkeypatch):
    """The gate does not soften for a model you host yourself.

    Worth pinning separately: running the weights on your own hardware is
    exactly the situation where someone would be tempted to trust them more.
    """

    def sloppy(user: str) -> str:
        return _well_behaved_draft(user).replace(
            "WBS-108 is at the centre of it", "the slip is 41 days"
        )

    with _local_openai_server(sloppy) as base_url:
        monkeypatch.setenv("OPENAI_BASE_URL", base_url)
        monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
        outcome = narrate(_model_bundle(), drafter=_openai_drafter())

    assert outcome.source == "template"
    assert "no_literal_digits" in (outcome.fallback_reason or "")
    assert "41" not in outcome.narrative


# --------------------------------------------------------------------------
# Gemini, end to end
#
# The vendor's own wire format, observed rather than assumed: the SDK posts to
# `/v1beta/models/<model>:generateContent` with `contents`, `systemInstruction`
# and `generationConfig`, and the reply carries `candidates[].content.parts[]`.
# This exercises the real adapter through `base_url`, so what is proven is the
# shipped request and the shipped response parsing.
# --------------------------------------------------------------------------


class _GenerateContent(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - the base class names it
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.seen = {
            "path": self.path,
            "keys": sorted(payload.keys()),
            "system": bool(
                payload.get("systemInstruction") or payload.get("system_instruction")
            ),
        }
        user = payload["contents"][0]["parts"][0]["text"]
        body = json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": self.server.compose(user)}],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def _local_gemini_server(compose=_well_behaved_draft):
    server = HTTPServer(("127.0.0.1", 0), _GenerateContent)
    server.compose = compose
    server.seen = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


requires_gemini = pytest.mark.skipif(
    importlib.util.find_spec("google.genai") is None,
    reason="google-genai is an optional extra",
)


@requires_gemini
def test_gemini_completes_the_whole_path():
    """The same proof the OpenAI adapter has, through the real Gemini client."""
    with _local_gemini_server() as (server, base_url):
        outcome = narrate(
            _model_bundle(),
            drafter=drafter_for(
                "gemini",
                ModelConfig(
                    model="gemini-3.8-flash", api_key="not-a-real-key", base_url=base_url
                ),
            ),
        )

    assert outcome.source == "model", outcome.fallback_reason
    assert outcome.attempts == 1
    assert "34" in outcome.narrative
    assert "{{" not in outcome.narrative

    # The request the SDK actually built, not the one we assumed it would.
    assert server.seen["path"].endswith(":generateContent")
    assert "contents" in server.seen["keys"]
    assert server.seen["system"], "the system prompt must not arrive as a user turn"


@requires_gemini
def test_gemini_writing_a_digit_is_refused_like_any_other_vendor():
    def sloppy(user: str) -> str:
        return _well_behaved_draft(user).replace(
            "WBS-108 is at the centre of it", "the slip is 41 days"
        )

    with _local_gemini_server(sloppy) as (_server, base_url):
        outcome = narrate(
            _model_bundle(),
            drafter=drafter_for(
                "gemini",
                ModelConfig(
                    model="gemini-3.8-flash", api_key="not-a-real-key", base_url=base_url
                ),
            ),
        )

    assert outcome.source == "template"
    assert "no_literal_digits" in (outcome.fallback_reason or "")
    assert "41" not in outcome.narrative


# --------------------------------------------------------------------------
# The FPT gateway, against a local server speaking its wire format.
#
# The shape being pinned is the one that made this a separate adapter rather
# than `_openai_drafter` with a `base_url`: the gateway answers with the
# completion wrapped in `{"code", "message", "data"}`, so `choices` is one
# level down and the OpenAI SDK cannot read it.
# --------------------------------------------------------------------------


class _FptChat(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - the base class names it
        length = int(self.headers.get("content-length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.seen = {
            "path": self.path,
            "auth": self.headers.get("authorization"),
            "agent": self.headers.get("user-agent"),
            "stream": payload.get("stream"),
            "roles": [m["role"] for m in payload.get("messages", [])],
        }
        user = next(
            m["content"] for m in payload["messages"] if m["role"] == "user"
        )
        body = json.dumps(self.server.compose_envelope(user)).encode()
        self.send_response(self.server.status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _fpt_envelope(text: str, *, finish: str = "stop", code: int = 200) -> dict:
    """The gateway's documented response shape - see `FPT_AI/output.txt`."""
    return {
        "code": code,
        "message": "Chat completion successful",
        "data": {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "DeepSeek-V4-Flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish,
                }
            ],
            "usage": {"prompt_tokens": 13, "completion_tokens": 10, "total_tokens": 23},
            "provider": "openai",
        },
    }


@contextlib.contextmanager
def _local_fpt_server(compose_envelope=None, status=200):
    server = HTTPServer(("127.0.0.1", 0), _FptChat)
    server.compose_envelope = compose_envelope or (
        lambda user: _fpt_envelope(_well_behaved_draft(user))
    )
    server.status = status
    server.seen = {}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _fpt(base_url: str) -> object:
    return drafter_for(
        "fpt", ModelConfig(api_key="not-a-real-key", base_url=base_url)
    )


def test_fpt_completes_the_whole_path():
    """Request, envelope parsing, the eight-stage gate, substitution."""
    with _local_fpt_server() as (server, base_url):
        outcome = narrate(_model_bundle(), drafter=_fpt(base_url))

    assert outcome.source == "model", outcome.fallback_reason
    assert outcome.attempts == 1
    assert "34" in outcome.narrative
    assert "{{" not in outcome.narrative

    # The request as it actually went out, not as we assumed it would.
    assert server.seen["path"].endswith("/v1/chat/completions")
    assert server.seen["auth"] == "Bearer not-a-real-key"
    assert server.seen["roles"] == ["system", "user"]


def test_fpt_never_asks_for_a_stream():
    """The gateway's own example sets `stream: true`.

    Copying that example would answer with server-sent events, and this parser
    would read the first chunk as a whole response - narrating a fragment that
    looks complete.
    """
    with _local_fpt_server() as (server, base_url):
        narrate(_model_bundle(), drafter=_fpt(base_url))

    assert server.seen["stream"] is False


def test_fpt_sends_a_user_agent_cloudflare_will_accept():
    """Cloudflare fronts the gateway and 403s `Python-urllib/3.x` outright.

    Observed as HTTP 403 "error code: 1010" - a banned browser signature, which
    reads exactly like an auth failure and is not one.
    """
    with _local_fpt_server() as (server, base_url):
        narrate(_model_bundle(), drafter=_fpt(base_url))

    agent = server.seen["agent"] or ""
    assert agent and "urllib" not in agent.lower()


def test_fpt_writing_a_digit_is_refused_like_any_other_vendor():
    def sloppy(user: str) -> dict:
        return _fpt_envelope(
            _well_behaved_draft(user).replace(
                "WBS-108 is at the centre of it", "the slip is 41 days"
            )
        )

    with _local_fpt_server(sloppy) as (_server, base_url):
        outcome = narrate(_model_bundle(), drafter=_fpt(base_url))

    assert outcome.source == "template"
    assert "no_literal_digits" in (outcome.fallback_reason or "")
    assert "41" not in outcome.narrative


def test_a_non_200_code_inside_a_200_response_is_still_a_failure():
    """The one shape that sails past every HTTP-level check.

    The gateway can answer 200 OK with `{"code": 500}` in the body, and a
    parser that trusts the status would read `data` as a successful completion.
    """
    with _local_fpt_server(
        lambda user: {"code": 500, "message": "upstream exploded", "data": {}}
    ) as (_server, base_url):
        outcome = narrate(_model_bundle(), drafter=_fpt(base_url))

    assert outcome.source == "template"
    assert "upstream exploded" in (outcome.fallback_reason or "")


def test_a_bare_openai_response_is_also_accepted():
    """The gateway proxies several upstreams, so the envelope may not be there.

    Unwrapping tolerantly costs one `.get` and removes a whole class of
    "worked in staging" failure.
    """
    with _local_fpt_server(
        lambda user: {
            "choices": [
                {
                    "message": {"content": _well_behaved_draft(user)},
                    "finish_reason": "stop",
                }
            ]
        }
    ) as (_server, base_url):
        outcome = narrate(_model_bundle(), drafter=_fpt(base_url))

    assert outcome.source == "model", outcome.fallback_reason


def test_a_truncated_fpt_draft_is_discarded_rather_than_narrated():
    with _local_fpt_server(
        lambda user: _fpt_envelope(_well_behaved_draft(user), finish="length")
    ) as (_server, base_url):
        outcome = narrate(_model_bundle(), drafter=_fpt(base_url))

    assert outcome.source == "template"
    assert "truncated" in (outcome.fallback_reason or "")


def test_fpt_needs_no_sdk_at_all():
    """The only provider that cannot fail with "package not installed".

    It speaks HTTP from the standard library, which is why it is absent from
    `EXTRAS` - and why it is the one that always works on a judge's machine.
    """
    from app.narration.providers import EXTRAS

    assert "fpt" not in EXTRAS
    assert importlib.util.find_spec("urllib.request") is not None


def test_a_missing_fpt_key_is_named_rather_than_becoming_a_401():
    """Nothing resolves credentials for us here, so an absent key is ours to say."""
    import os

    saved = os.environ.pop("FPT_API_KEY", None)
    try:
        outcome = narrate(_model_bundle(), drafter=drafter_for("fpt"))
    finally:
        if saved is not None:
            os.environ["FPT_API_KEY"] = saved

    assert outcome.source == "template"
    assert "FPT_API_KEY" in (outcome.fallback_reason or "")
