"""Ask the FPT gateway which of its models this product can actually use.

    python -m scripts.probe_fpt              # can each model hold a chat?
    python -m scripts.probe_fpt --full       # ...and does it pass the gate?
    python -m scripts.probe_fpt --model GLM-5.2

Not a pytest test, deliberately. It makes real network calls to a real gateway
with a real credential, and `pytest` has to run offline on a judge's machine -
so this is a script you run when the model list changes, and the answer gets
written into CLAUDE.md rather than re-derived.

Two questions, and only the second one matters:

**Does the model answer at all?** Half the entries on the permission screen are
not chat models - text-to-speech, Whisper, rerankers, embeddings - and asking
one for a sentence fails in whatever way that vendor chose. Knowing which is
which stops someone setting PULSE_NARRATION_MODEL to an embedding model and
reading the fallback as a broken feature.

**Does its prose survive the fence?** This is the real question. `narrate()`
runs all eight validation stages, and a model that writes a digit is refused no
matter how good it sounds - so "can chat" and "can narrate for us" are
different properties, and the second is the one that decides what we ship. A
model that fails here is not a bad model; it is a model that would not follow
the one instruction this job has.

Credentials come from FPT_API_KEY. Nothing is written anywhere.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

#: Every model on the gateway's permission screen, with what it is for. The
#: `chat` flag is our expectation, not the gateway's - the probe reports what
#: actually happened, so a wrong guess here shows up as a mismatch rather than
#: as a model nobody tried.
MODELS: tuple[tuple[str, bool, str], ...] = (
    ("DeepSeek-V4-Flash", True, "chat - our default"),
    ("GLM-5.2", True, "chat"),
    ("Qwen3.6-27B", True, "chat"),
    ("gpt-oss-120b", True, "chat"),
    ("Llama-3.3-70B-Instruct", True, "chat"),
    ("gemma-3-27b-it", True, "chat"),
    ("gemma-4-31B-it", True, "chat"),
    ("gemma-4-26B-A4B-it", True, "chat"),
    ("Qwen2.5-VL-7B-Instruct", True, "chat - vision-language"),
    ("FPT.TTS-pro", False, "text to speech"),
    ("FPT.AI-VITs", False, "text to speech"),
    ("whisper-large-v3-turbo", False, "speech to text"),
    ("FPT.AI-whisper-large-v3-turbo", False, "speech to text"),
    ("FPT.AI-whisper-medium", False, "speech to text"),
    ("bge-reranker-v2-m3", False, "reranker"),
    ("Vietnamese_Embedding", False, "embeddings"),
    ("multilingual-e5-large", False, "embeddings"),
)

#: Short enough that a slow model is not the reason a probe fails, long enough
#: that the answer proves the model composed something.
_PROMPT = "Reply with exactly one short sentence about project delivery."

#: Generous on purpose, even for a one-sentence answer.
#:
#: WARNING: several of these are reasoning models - DeepSeek-V4-Flash (our own
#: default), GLM-5.2 and Qwen3.6-27B all spend the budget on thinking tokens
#: before emitting a word of content. At 120 tokens all three came back
#: "truncated at max_tokens" in under two seconds, which reads exactly like a
#: model that cannot chat and is nothing of the kind. A tight ceiling here
#: measures the probe, not the gateway.
_PROBE_TOKENS = 3000


def _short(text: str, width: int = 58) -> str:
    """One line, ASCII only.

    The console here is cp932 and a judge's may be cp1252 - and this function
    prints **model output**, which is exactly the data no rule upstream can
    keep ASCII. `_bootstrap.printable_console` would already replace the
    character; collapsing it here keeps the table's columns aligned too.
    """
    flat = " ".join(str(text).split())
    flat = flat.encode("ascii", "replace").decode("ascii")
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def probe_chat(model: str, timeout: float) -> tuple[bool, float, str]:
    """One tiny call. Returns (answered, seconds, what came back or why not)."""
    from app.narration.providers import ModelConfig, drafter_for

    draft = drafter_for(
        "fpt", ModelConfig(model=model, max_tokens=_PROBE_TOKENS, timeout_seconds=timeout)
    )
    started = time.time()
    try:
        answer = draft("You are terse.", _PROMPT)
        return True, time.time() - started, _short(answer)
    except Exception as exc:  # noqa: BLE001 - every failure is a result here
        return False, time.time() - started, _short(f"{type(exc).__name__}: {exc}")


def probe_narration(bundle, model: str, timeout: float) -> tuple[str, float, str]:
    """The real path. Returns (source, seconds, fallback reason or '')."""
    from app.narration.client import narrate
    from app.narration.providers import ModelConfig, drafter_for

    started = time.time()
    outcome = narrate(
        bundle,
        drafter=drafter_for("fpt", ModelConfig(model=model, timeout_seconds=timeout)),
    )
    return outcome.source, time.time() - started, _short(outcome.fallback_reason or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="also run the eight-stage gate on each chat model (slow: ~60s each)",
    )
    parser.add_argument(
        "--model", action="append", help="just this model, repeatable"
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--project", default="excel:Project:1:HRMS")
    args = parser.parse_args()

    if not os.environ.get("FPT_API_KEY"):
        print("FPT_API_KEY is not set. Nothing to probe.")
        return 2

    wanted = (
        [m for m in MODELS if m[0] in set(args.model)] if args.model else list(MODELS)
    )
    if not wanted:
        print(f"no such model. Known: {', '.join(m[0] for m in MODELS)}")
        return 2

    print(f"probing {len(wanted)} model(s) on the FPT gateway\n")
    print(f"{'model':<32} {'kind':<22} {'ok':<4} {'secs':>6}  answer / error")
    print("-" * 118)

    chatted: list[str] = []
    for model, expect_chat, kind in wanted:
        answered, seconds, detail = probe_chat(model, args.timeout)
        if answered:
            chatted.append(model)
        # A mismatch against our own expectation is worth seeing: it means this
        # table is out of date, not that the gateway is wrong.
        flag = "" if answered == expect_chat else "  <- unexpected"
        print(
            f"{model:<32} {kind:<22} {'yes' if answered else 'no':<4} "
            f"{seconds:>6.1f}  {detail}{flag}"
        )

    if not args.full:
        print(
            f"\n{len(chatted)} model(s) answered. Re-run with --full to check which "
            "of them survive the narration gate."
        )
        return 0

    from app.db import SessionLocal
    from app.intelligence.pipeline import analyze_project
    from app.scope import also_for

    with SessionLocal() as session:
        bundle = analyze_project(
            session, project_id=args.project, also=also_for(args.project)
        )

    print(f"\n\nthe eight-stage gate, on the real brief ({len(chatted)} model(s))\n")
    print(f"{'model':<32} {'narration':<10} {'secs':>6}  why it fell back")
    print("-" * 118)

    usable = 0
    for model in chatted:
        source, seconds, reason = probe_narration(bundle, model, args.timeout)
        if source == "model":
            usable += 1
        print(f"{model:<32} {source:<10} {seconds:>6.1f}  {reason}")

    print(
        f"\n{usable} of {len(chatted)} model(s) produced a narrative the validator "
        "accepted."
    )
    print("A refusal here is the fence working, not a broken model.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
