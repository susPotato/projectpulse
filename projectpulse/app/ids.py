"""Deterministic domain identifiers.

Port of DevLake's `didgen` (backend/core/models/domainlayer/didgen/domain_id_generator.go).

Every domain row's primary key is a pure function of where it came from:

    <source>:<Entity>:<connection_id>:<pk0>[:<pk1>...]

e.g.  jira:Issue:1:10023        excel:Task:2:WBS-114

This buys three things that are otherwise expensive:

* **Idempotent re-runs** - the same source record always regenerates the same id,
  so every write is an upsert and re-running a sync is a no-op.
* **Collision-free multi-source** - a Jira issue and an Excel task cannot fight
  over a key, so both can live in `tasks` without a discriminator column.
* **Self-describing provenance** - the id names its own source system, so the
  evidence panel can route a lookup without a join table.
"""

from __future__ import annotations

SEP = ":"
_ESCAPE = "%3A"


def _encode(part: object) -> str:
    """Escape the separator so ids round-trip.

    A Jira issue key never contains a colon, but an Excel `Task ID` typed by a
    human very well might, and a key that silently splits into two would map two
    different tasks onto one row.
    """
    text = str(part)
    if not text:
        raise ValueError("domain id components must not be empty")
    return text.replace(SEP, _ESCAPE)


def _decode(part: str) -> str:
    return part.replace(_ESCAPE, SEP)


def domain_id(source: str, entity: str, connection_id: int, *pks: object) -> str:
    """Build a domain id. At least one primary-key component is required."""
    if not pks:
        raise ValueError(
            f"{source}:{entity} needs at least one primary key component; "
            "an entity with no natural key cannot have a deterministic id"
        )
    parts = [_encode(source), _encode(entity), _encode(connection_id)]
    parts.extend(_encode(p) for p in pks)
    return SEP.join(parts)


def parse_domain_id(value: str) -> tuple[str, str, int, list[str]]:
    """Inverse of :func:`domain_id`. Returns (source, entity, connection_id, pks)."""
    parts = value.split(SEP)
    if len(parts) < 4:
        raise ValueError(f"not a domain id: {value!r}")
    source, entity, connection_id, *pks = parts
    return (
        _decode(source),
        _decode(entity),
        int(connection_id),
        [_decode(p) for p in pks],
    )


def source_of(value: str) -> str:
    """Which system a row came from, without parsing the whole id."""
    return _decode(value.split(SEP, 1)[0])
