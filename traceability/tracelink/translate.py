"""Stage 0d — put every ticket into English before anything tries to match it.

Retrieval works by finding ticket words inside identifiers, and identifiers
are written in English in almost every codebase, including ones written by
teams who do not work in English. A ticket in another language therefore
cannot match its own code — not because the matcher is weak but because
the two sides are in different languages.

Measured on the demo backlog before this stage existed:

    149 English tickets   92% retrieved   72 corroborated
     24 Vietnamese        75% retrieved    0 corroborated

Zero. Not "fewer" — none of the 24 ever reached a verdict backed by code.

**Two rules make this safe.**

*The original is never replaced.* Translations are written to new fields.
Every screen still shows what the tracker actually says, because that is
the text a person will search for and the text an auditor must see. Only
the matcher reads the translation.

*Technical terms are not translated.* `RAG`, `MCP`, `Agent`, `RULEBASE`,
`Co4E` are the tokens that match code. A fluent translation that localised
them would read better and retrieve worse, which is the exact failure this
stage exists to fix — so the prompt forbids it and the model returns the
terms it preserved.

Free to skip: language detection is deterministic, so a fully English
backlog costs nothing and makes no API call.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from tracelink.adjudicate import StructuredCaller, Usage
from tracelink.artifacts import Ticket

logger = logging.getLogger(__name__)

# Script ranges, not word lists: a word list is a guess about vocabulary,
# a script range is a fact about the characters on the page.
SCRIPTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("vi", re.compile(
        "[ĂăÂâĐđÊêÔô"
        "ƠơƯưẠ-ỹ]")),
    ("ja", re.compile("[぀-ゟ゠-ヿ]")),
    ("zh", re.compile("[一-鿿]")),
    ("ko", re.compile("[가-힯]")),
    ("th", re.compile("[฀-๿]")),
    ("ru", re.compile("[Ѐ-ӿ]")),
    ("ar", re.compile("[؀-ۿ]")),
)


def detect(text: str) -> str:
    """Best-effort script detection. `en` means "no other script present"."""
    for code, pattern in SCRIPTS:
        if pattern.search(text):
            return code
    return "en"


def needs_translation(t: Ticket) -> bool:
    return detect(t.text) != "en"


class Translated(BaseModel):
    summary_en: str = Field(
        description="The summary in English. Keep every product name, "
                    "acronym and technical term exactly as written.")
    description_en: str = Field(
        default="",
        description="The description in English, same rules. Empty if there "
                    "was no description.")
    detected_language: str = Field(
        description="The language you translated from, as a short name.")
    preserved_terms: list[str] = Field(
        default_factory=list,
        description="Terms you deliberately left untranslated because they "
                    "name a product, module, acronym or technology.")


SYSTEM = """You translate software backlog tickets into English so they can be \
matched against source code.

This is a translation for machine matching, not for reading. The rules follow from \
that and they matter more than fluency:

- **Never translate a technical term.** Product names, module names, acronyms, \
library names, protocol names and anything that looks like an identifier stay exactly \
as written — RAG, MCP, Agent, API, RULEBASE, Co4E, UI/UX, BE, FE, CI/CD. These are the \
words that match code. Translating them produces a better sentence and a useless one.
- If a term might be either a common word or a product name, keep it as written and \
list it in preserved_terms.
- Translate the surrounding prose plainly and literally. Do not summarise, do not \
improve, do not add detail that is not there.
- Keep the original's structure: if the summary is a fragment, the translation is a \
fragment.
- If the text is already English, return it unchanged.
- Report every term you preserved, so a reader can check you did not translate \
something that should have matched code."""


def build_prompt(t: Ticket) -> str:
    return (
        f"## Ticket\n"
        f"Summary: {t.summary}\n"
        f"Description: {t.description or '(none)'}\n"
        f"Component: {t.component or '(none)'}\n\n"
        f"Translate into English for code matching."
    )


def translate(
    tickets: list[Ticket],
    caller: StructuredCaller,
    on_result=None,
) -> tuple[dict[str, dict], Usage]:
    """Translate only the tickets that need it. Returns uid -> translation."""
    total = Usage()
    out: dict[str, dict] = {}
    for t in tickets:
        lang = detect(t.text)
        if lang == "en":
            continue
        try:
            payload, usage = caller.call(SYSTEM, build_prompt(t), Translated,
                                         label=t.uid)
        except Exception as exc:                      # noqa: BLE001
            logger.error("%s: %s", t.uid, exc)
            continue
        total.add(usage)
        payload["script"] = lang
        payload["cost_usd"] = round(usage.cost(caller.model), 6)
        out[t.uid] = payload
        if on_result:
            on_result(t, payload, usage)
    return out, total


def apply(tickets: list[Ticket], translations: dict[str, dict]) -> int:
    """Attach translations to tickets in place. Originals are untouched."""
    n = 0
    for t in tickets:
        tr = translations.get(t.uid)
        if not tr:
            t.lang = detect(t.text)
            continue
        t.lang = tr.get("script") or detect(t.text)
        t.summary_en = tr.get("summary_en", "")
        t.description_en = tr.get("description_en", "")
        n += 1
    return n


def census(tickets: list[Ticket]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tickets:
        code = detect(t.text)
        out[code] = out.get(code, 0) + 1
    return out
