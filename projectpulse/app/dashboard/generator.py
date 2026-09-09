"""The AI Dashboard Generator: a prompt selects tiles, it never invents them.

`Layout_Program` images 17-19: "Create with AI" - "Describe the dashboard you
want and AI will create it for you." The PM's own mockup builds a whole
dashboard from a prompt, and that scope is what keeps this compatible with
this app's governing rule (`CLAUDE.md` invariant 1: the model never produces
a number or a date). There is nothing here for a model to invent - it picks
from `catalogue.CATALOGUE`, a closed set of tile keys the app already knows
how to render, in the same spirit as `narration/validator.py`'s
`known_entities` stage: an id the model returns that is not in the supplied
set is dropped, never rendered.

No JSON mode - no provider adapter in this codebase sets a structured-output
flag (`app/narration/providers.py` builds every `Drafter` as `(system, user)
-> str`), and asking for one tile key per line is both easy for a model to
produce and trivial to validate without adding that surface.
"""

from __future__ import annotations

from app.dashboard.catalogue import for_scope
from app.narration.client import Drafter

MAX_TILES = 10

SYSTEM_PROMPT = """You are choosing which dashboard tiles to show a project or \
program manager, from a fixed catalogue. You do not have access to any live \
data - you are only picking and ordering tile keys, nothing else.

Respond with ONLY a list of tile keys, one per line, most important first. \
Do not include any other text, explanation, numbering, or punctuation. Use \
only keys from the catalogue below - do not invent a key."""


def _build_user_prompt(tiles, request: str) -> str:
    lines = [f"{t.key}: {t.label} - {t.description}" for t in tiles]
    return (
        "Catalogue:\n" + "\n".join(lines) + f"\n\nWhat the person asked for:\n{request}"
    )


def _parse_tile_keys(raw: str, valid_keys: set[str]) -> list[str]:
    """Keep only lines that are exactly a known key, in the order given, no
    duplicates, capped at `MAX_TILES` - the allow-list gate."""
    picked: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        key = line.strip().strip("-*• ").strip()
        if key in valid_keys and key not in seen:
            picked.append(key)
            seen.add(key)
        if len(picked) >= MAX_TILES:
            break
    return picked


def generate_layout(
    prompt: str, scope_type: str, scope_id: str, *, drafter: Drafter | None
) -> tuple[list[str], str]:
    """Returns `(tile_keys, fallback_reason)`. `fallback_reason` is `""` when
    the model's own picks were used; otherwise it names why the deterministic
    default (every P0 tile for this scope, catalogue order) was served
    instead - the same shape as `narrate()` falling back to
    `render_narrative()`, so this feature also works with the model switched
    off."""
    del scope_id  # not used to pick tiles - every program/project sees the same catalogue
    tiles = for_scope(scope_type)  # type: ignore[arg-type]
    default_keys = [t.key for t in tiles]

    if drafter is None:
        return default_keys, "narration is switched off"

    try:
        raw = drafter(SYSTEM_PROMPT, _build_user_prompt(tiles, prompt))
    except Exception as exc:  # noqa: BLE001 - any provider failure degrades, never 500s
        return default_keys, f"the model call failed: {exc}"

    picked = _parse_tile_keys(raw, {t.key for t in tiles})
    if not picked:
        return default_keys, "the model returned no recognizable tile key"

    return picked, ""
