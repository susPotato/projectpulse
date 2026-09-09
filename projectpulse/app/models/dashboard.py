"""A saved canvas layout: which tiles, where, on a Program or Project dashboard.

Same shape as `app/risks/`'s `Risk` table, for the same reason: a dashboard
layout is not observed from an ingested source, it is a person's own choice of
what to look at, so it is a plain `Base`/`Timestamped` table with an
autoincrement int PK - not `DomainEntity`, no `RawDataOrigin`, no
`<source>:<Entity>:<connection>:<pk>` scheme. `scope_id` is deliberately not a
foreign key, again matching `Risk.project_id`: a Program's canonical id and a
Project's canonical id come from two different tables (`programs.id` /
`app.scope`'s canonical project ids), so one column cannot carry a single FK
to either.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, BigIntPK, Timestamped


class Dashboard(Base, Timestamped):
    __tablename__ = "dashboards"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    #: 'program' | 'project'.
    scope_type: Mapped[str] = mapped_column(String(20))
    #: A Program id or a canonical Project id, depending on `scope_type`.
    scope_id: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(Text, default="Dashboard")
    #: 'blank' | 'template' | 'ai' - how this dashboard was created, shown back
    #: on the canvas so a generated layout is never presented as hand-built.
    source: Mapped[str] = mapped_column(String(20), default="blank")
    #: The prompt that produced it, when `source == "ai"` - kept for
    #: transparency (the canvas can show "generated from: ...").
    ai_prompt: Mapped[str | None] = mapped_column(Text, default=None)


class DashboardTile(Base, Timestamped):
    __tablename__ = "dashboard_tiles"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    dashboard_id: Mapped[int] = mapped_column(
        ForeignKey("dashboards.id"), index=True
    )
    #: A key in `app.dashboard.catalogue.CATALOGUE` - never rendered directly,
    #: only ever looked up, so an unknown key (a stale save after a catalogue
    #: edit) fails soft instead of rendering garbage. See `service.py`.
    tile_key: Mapped[str] = mapped_column(String(64))
    x: Mapped[int] = mapped_column(Integer, default=0)
    y: Mapped[int] = mapped_column(Integer, default=0)
    w: Mapped[int] = mapped_column(Integer, default=4)
    h: Mapped[int] = mapped_column(Integer, default=3)
    #: Free-form per-tile display options (custom title, show legend, ...) -
    #: JSON text rather than a jsonb column so this works identically on the
    #: SQLite the tests run against and the Postgres deployment reads.
    settings_json: Mapped[str | None] = mapped_column(Text, default=None)


class CustomTile(Base, Timestamped):
    """A person's own chart, drafted from data they pasted in - never from
    anything the intelligence layer computed.

    This is the one tile type this app's governing rule does not apply to,
    and that has to be stated rather than papered over: `CLAUDE.md` invariant
    1 says the model never produces a number a PM will read as computed fact.
    A `CustomTile`'s numbers come from the person, typed or pasted by them -
    the model's job here is parsing their text into `{title, chart_type,
    labels, values}`, not authoring the values. `source_note` (what they said
    the data was) travels with the tile so the canvas can label it plainly as
    custom, the same honesty the Agent tab and the Risk register already
    practice for their own ungoverned surfaces.

    Saved once, addable to any dashboard many times - `DashboardTile.tile_key`
    for one of these is `f"custom:{id}"` rather than a `catalogue` key.
    """

    __tablename__ = "custom_tiles"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text)
    #: 'bar' | 'line' | 'pie'.
    chart_type: Mapped[str] = mapped_column(String(20))
    #: JSON arrays, same length - `["Jan", "Feb", ...]` / `[12, 18, ...]`.
    labels_json: Mapped[str] = mapped_column(Text)
    values_json: Mapped[str] = mapped_column(Text)
    #: What the person said this data was, kept verbatim - the honesty label
    #: rendered on the tile, and the only provenance a pasted number has.
    source_note: Mapped[str | None] = mapped_column(Text, default=None)
