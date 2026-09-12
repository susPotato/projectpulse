"""CRUD for dashboard layouts and tiles, and the catalogue they draw from.

One dashboard per `(scope_type, scope_id)` - "New Dashboard" (Blank / Browse
Templates / Create with AI, Phase D) replaces the current one rather than
adding to a list. That is a deliberate round-1 simplification: no dashboard
history, no picker between several saved layouts per scope - see the plan's
cut line. `get_dashboard` auto-creates an empty one on first look, so the
canvas always has something to render.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from app.api.schemas.dashboard import (
    CatalogueBundle,
    DashboardOut,
    TileIn,
    TileOut,
    TileSpecOut,
)
from app.dashboard.catalogue import (
    BY_KEY,
    CATALOGUE,
    DEFAULT_TEMPLATE,
    TEMPLATES,
    for_scope,
)
from app.models.dashboard import Dashboard, DashboardTile

#: react-grid-layout's column count on every canvas - fixed rather than
#: responsive, matching the mockup's fixed desktop canvas (design §15: the
#: mockups are desktop-only).
GRID_COLS = 12


def auto_layout(tile_keys: list[str]) -> list[tuple[str, int, int, int, int]]:
    """Pack tiles left-to-right, wrapping at `GRID_COLS` - used by Blank/
    Template/the AI generator (Phase D), none of which ask a person or a
    model to place x/y by hand. An unknown key is skipped rather than
    raising, so a stale template or a generator response with one bad key
    still produces a dashboard instead of a 500."""
    x = y = row_h = 0
    placed: list[tuple[str, int, int, int, int]] = []
    for key in tile_keys:
        spec = BY_KEY.get(key)
        if spec is None:
            continue
        w, h = spec.default_w, spec.default_h
        if x + w > GRID_COLS:
            x, y = 0, y + row_h
            row_h = 0
        placed.append((key, x, y, w, h))
        x += w
        row_h = max(row_h, h)
    return placed


def get_catalogue() -> CatalogueBundle:
    return CatalogueBundle(
        tiles=[
            TileSpecOut(
                key=t.key, label=t.label, category=t.category,
                description=t.description, scope=t.scope,
                default_w=t.default_w, default_h=t.default_h,
                preview=t.preview,
            )
            for t in CATALOGUE
        ],
        templates={name: list(keys) for name, keys in TEMPLATES.items()},
    )


def _to_tile_out(tile: DashboardTile) -> TileOut:
    settings = None
    if tile.settings_json:
        try:
            settings = json.loads(tile.settings_json)
        except (TypeError, ValueError):
            settings = None
    return TileOut(
        id=tile.id, tile_key=tile.tile_key, x=tile.x, y=tile.y, w=tile.w, h=tile.h,
        settings=settings,
    )


def _to_dashboard_out(session, dashboard: Dashboard) -> DashboardOut:
    tiles = session.scalars(
        select(DashboardTile)
        .where(DashboardTile.dashboard_id == dashboard.id)
        .order_by(DashboardTile.id)
    ).all()
    return DashboardOut(
        id=dashboard.id,
        scope_type=dashboard.scope_type,  # type: ignore[arg-type]
        scope_id=dashboard.scope_id,
        name=dashboard.name,
        source=dashboard.source,
        ai_prompt=dashboard.ai_prompt,
        tiles=[_to_tile_out(t) for t in tiles],
    )


def get_dashboard(session, scope_type: str, scope_id: str) -> DashboardOut:
    dashboard = session.scalars(
        select(Dashboard).where(
            Dashboard.scope_type == scope_type, Dashboard.scope_id == scope_id
        )
    ).first()
    if dashboard is None:
        dashboard = Dashboard(scope_type=scope_type, scope_id=scope_id, name="Dashboard")
        session.add(dashboard)
        session.flush()
    return _to_dashboard_out(session, dashboard)


def _dashboard_row(session, scope_type: str, scope_id: str) -> Dashboard:
    dashboard = session.scalars(
        select(Dashboard).where(
            Dashboard.scope_type == scope_type, Dashboard.scope_id == scope_id
        )
    ).first()
    if dashboard is None:
        dashboard = Dashboard(scope_type=scope_type, scope_id=scope_id)
        session.add(dashboard)
        session.flush()
    return dashboard


def replace_dashboard(
    session,
    scope_type: str,
    scope_id: str,
    *,
    name: str | None = None,
    source: str = "blank",
    ai_prompt: str | None = None,
    tiles: list[tuple[str, int, int, int, int]] = (),
) -> DashboardOut:
    """Reset this scope's dashboard: new name/source, and a fresh tile set.

    `tiles` is `(tile_key, x, y, w, h)` tuples rather than `TileIn` so callers
    that already computed a layout (Blank/Template/the generator) don't have
    to round-trip through the API schema to call this.
    """
    dashboard = _dashboard_row(session, scope_type, scope_id)
    dashboard.name = name or dashboard.name or "Dashboard"
    dashboard.source = source
    dashboard.ai_prompt = ai_prompt

    for existing in session.scalars(
        select(DashboardTile).where(DashboardTile.dashboard_id == dashboard.id)
    ).all():
        session.delete(existing)
    session.flush()

    for tile_key, x, y, w, h in tiles:
        session.add(
            DashboardTile(
                dashboard_id=dashboard.id, tile_key=tile_key, x=x, y=y, w=w, h=h
            )
        )
    session.flush()
    return _to_dashboard_out(session, dashboard)


def reset_blank(session, scope_type: str, scope_id: str) -> DashboardOut:
    """Blank Canvas: an empty dashboard, ready for "+ Add Tiles"."""
    return replace_dashboard(session, scope_type, scope_id, source="blank", tiles=[])


def apply_template(
    session, scope_type: str, scope_id: str, template: str
) -> DashboardOut | None:
    """Browse Templates: one of the canned starter sets in `catalogue.TEMPLATES`."""
    keys = TEMPLATES.get(template)
    if keys is None:
        return None
    scoped_keys = [k for k in keys if BY_KEY[k].scope == scope_type]
    return replace_dashboard(
        session, scope_type, scope_id,
        name=template.replace("_", " ").title(),
        source="template",
        tiles=auto_layout(scoped_keys),
    )


def apply_default(session, scope_type: str, scope_id: str) -> DashboardOut | None:
    """Default setup: the starting board for this scope, in one click.

    Deliberately thin - it resolves `catalogue.DEFAULT_TEMPLATE` and defers to
    `apply_template`, so the button and "Browse Templates" place the identical
    layout and cannot drift apart. `None` for a scope_type that names no
    default, which the route turns into a 400 rather than an empty canvas.

    Like every other "New Dashboard" path this **replaces** the scope's current
    board (one dashboard per scope - see the module docstring), which is why the
    button that calls it asks first. `seed_default_dashboard` below is the
    non-destructive variant, and the two are not interchangeable: that one is
    for a database being seeded, this one is for a person who pressed a button.
    """
    template = DEFAULT_TEMPLATE.get(scope_type)  # type: ignore[arg-type]
    if template is None:
        return None
    return apply_template(session, scope_type, scope_id, template)


def apply_fitted(session, scope_type: str, scope_id: str) -> DashboardOut:
    """Fit a board to what this scope's data actually carries.

    The third way to populate one, beside blank and a fixed template - and the
    honest one for a data-poor project, where a template lays out tiles whose
    inputs do not exist. See `app/dashboard/fit.py` for why that matters more
    than it sounds.

    Falls back to placing the always-available tiles rather than an empty board
    if a project has nothing ingested at all: a canvas with "nothing has been
    ingested for this project yet" on it is more use than a blank one, and it is
    the same reasoning `get_dashboard` uses when it auto-creates.
    """
    from app.dashboard.fit import (
        fitted_tiles,
        missing_for,
        program_signals,
        project_signals,
    )
    from app.scope import source_ids_for

    if scope_type == "program":
        signals = program_signals(session, scope_id)
    else:
        signals = project_signals(session, source_ids_for(scope_id))

    tiles = fitted_tiles(scope_type, signals)
    out = replace_dashboard(
        session, scope_type, scope_id,
        name="Fitted To This Data",
        source="fitted",
        tiles=auto_layout([t.key for t in tiles]),
    )
    return out.model_copy(
        update={
            "fit": {
                "signals": sorted(signals),
                "placed": len(tiles),
                "of": len(for_scope(scope_type)),  # type: ignore[arg-type]
                "missing": missing_for(scope_type, signals),
            }
        }
    )


def seed_default_dashboard(
    session, scope_type: str, scope_id: str, template: str
) -> bool:
    """Give a scope a starting layout, but only if it has none.

    `get_dashboard` auto-creates an *empty* canvas on first look, which is
    right for a new scope in a real deployment - the PM builds it - and wrong
    for a seeded demo database, where it means the biggest feature in the app
    opens blank for anyone who just ran `scripts.replay`. This is the demo's
    answer, kept out of `get_dashboard` so production behaviour stays honest.

    Returns whether it placed anything. Skipping a dashboard that already has
    tiles is what makes this safe to re-run and non-destructive: seeding must
    never overwrite a layout somebody arranged by hand.
    """
    if get_dashboard(session, scope_type, scope_id).tiles:
        return False
    return apply_template(session, scope_type, scope_id, template) is not None


def generate(
    session, scope_type: str, scope_id: str, prompt: str, drafter
) -> DashboardOut:
    """Create with AI: the generator picks tiles, this places and saves them."""
    from app.dashboard.generator import generate_layout

    keys, fallback_reason = generate_layout(
        prompt, scope_type, scope_id, drafter=drafter
    )
    out = replace_dashboard(
        session, scope_type, scope_id,
        name="Dashboard",
        source="ai",
        ai_prompt=prompt,
        tiles=auto_layout(keys),
    )
    return out.model_copy(update={"fallback_reason": fallback_reason or None})


def add_tile(session, scope_type: str, scope_id: str, data: TileIn) -> TileOut | None:
    if not data.tile_key:
        raise ValueError("tile_key is required")
    dashboard = _dashboard_row(session, scope_type, scope_id)
    tile = DashboardTile(
        dashboard_id=dashboard.id,
        tile_key=data.tile_key,
        x=data.x or 0,
        y=data.y or 0,
        w=data.w or 4,
        h=data.h or 3,
        settings_json=json.dumps(data.settings) if data.settings is not None else None,
    )
    session.add(tile)
    session.flush()
    return _to_tile_out(tile)


def update_tile(session, tile_id: int, data: TileIn) -> TileOut | None:
    """Merge only the fields the request actually set - move, resize, or
    change settings independently, same partial-update convention as
    `app/risks/service.py:update_risk`."""
    tile = session.get(DashboardTile, tile_id)
    if tile is None:
        return None

    changes = data.model_dump(exclude_unset=True)
    if "settings" in changes:
        settings = changes.pop("settings")
        tile.settings_json = json.dumps(settings) if settings is not None else None
    for field, value in changes.items():
        if value is not None:
            setattr(tile, field, value)

    session.flush()
    return _to_tile_out(tile)


def duplicate_tile(session, tile_id: int) -> TileOut | None:
    tile = session.get(DashboardTile, tile_id)
    if tile is None:
        return None
    copy = DashboardTile(
        dashboard_id=tile.dashboard_id,
        tile_key=tile.tile_key,
        x=tile.x,
        y=tile.y + tile.h,
        w=tile.w,
        h=tile.h,
        settings_json=tile.settings_json,
    )
    session.add(copy)
    session.flush()
    return _to_tile_out(copy)


def delete_tile(session, tile_id: int) -> bool:
    tile = session.get(DashboardTile, tile_id)
    if tile is None:
        return False
    session.delete(tile)
    return True
