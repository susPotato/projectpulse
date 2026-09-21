"""What each model call cost, and where the credentials to make it live.

Two tables with very different lifetimes:

`llm_usage` is an append-only log - one row per call to a vendor, written
whether the call succeeded or not. It is the only record of spend this app
has, because none of the four adapters in `narration/providers.py` used to
look at `response.usage` at all.

`llm_credentials` holds API keys **encrypted**, unlike `app_settings.narration`
which held one in plaintext. See `app/llm/keys.py` for exactly what that buys
and what it does not.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import BigIntPK, Base, Timestamped


class LlmUsage(Timestamped, Base):
    """One call to a language model, priced at the moment it was made.

    **`cost_usd` is stamped here rather than computed when the board is read.**
    A rate table edited in March must not silently rewrite what February cost -
    the number in this column is what the call was worth under the rates in
    force when it ran, which is the only figure that reconciles against a
    vendor invoice.

    Failed calls are rows too, with `ok = False`. A refusal or a timeout still
    burns input tokens on most vendors, and a board that hid them would
    under-report spend exactly when something is looping.
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)

    #: Which feature spent this - a key from `app.llm.features.FEATURES`.
    #: Indexed because every panel on the board groups by it.
    feature: Mapped[str] = mapped_column(String(64), index=True)

    provider: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(128))

    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    #: Billed at a fraction of the input rate where a vendor reports it, and
    #: folded into `input_tokens` where it does not. Kept separate so the
    #: saving from prompt caching is visible rather than averaged away.
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    #: Billed inside the output budget on every vendor that has them. Counted
    #: separately because a reasoning model's bill is mostly this column, and
    #: "why did that cost so much" has no answer without it.
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)

    #: US dollars. A float, not Decimal: these are fractions of a cent summed
    #: for display, never used to bill anyone.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    #: False when the rate table had no entry for this model, so the board can
    #: say "3 calls unpriced" instead of quietly showing $0.00 and reading as
    #: free.
    priced: Mapped[bool] = mapped_column(Boolean, default=False)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: The exception's message when `ok` is false. Never the prompt, never the
    #: response - this column is safe to show on a page.
    error: Mapped[str | None] = mapped_column(Text, default=None)


class LlmCredential(Timestamped, Base):
    """One vendor's API key, encrypted at rest.

    Keyed by provider, so there is exactly one key per vendor and saving a new
    one replaces it rather than accumulating.
    """

    __tablename__ = "llm_credentials"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)

    #: Fernet token. Opaque without `PULSE_SECRET_KEY`; see `app/llm/keys.py`.
    ciphertext: Mapped[str] = mapped_column(Text)

    #: A masked hint like `sk-ant-...bQ4A`, stored in the clear on purpose so
    #: the page can tell two keys apart without the server decrypting anything
    #: to render a list.
    hint: Mapped[str] = mapped_column(String(64), default="")

    #: Which `PULSE_SECRET_KEY` sealed this row. A rotation writes new rows at
    #: version + 1, so a row sealed by a retired key reports itself unreadable
    #: rather than raising somewhere unrelated.
    key_version: Mapped[int] = mapped_column(Integer, default=1)

    #: Who last wrote it, as far as the server can tell - "loopback" or
    #: "admin-token". Not identity, just provenance for an audit read.
    updated_by: Mapped[str] = mapped_column(String(32), default="")
