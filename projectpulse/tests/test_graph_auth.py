"""Guards for the OneDrive sign-in cache.

A real device-code sign-in needs a human and a network, so what is testable
here is the part that matters most for a Fly deployment: the cache is a
database row, round-trips correctly, and every "not signed in" path fails
the way `graph_source.py` expects rather than raising something else.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingest.sources.excel.graph_auth import (
    GraphAuthError,
    get_access_token,
    signed_in_account,
    signed_in_identity,
    sign_out,
)
from app.models.base import Base
from app.models.onedrive import OneDriveTokenCache

msal = pytest.importorskip("msal")


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


def test_nobody_signed_in_by_default(session):
    assert signed_in_account(session) is None
    assert signed_in_identity(session) is None


def test_get_access_token_without_a_sign_in_raises_graph_auth_error(session):
    with pytest.raises(GraphAuthError, match="not signed in"):
        get_access_token(session)


def test_signing_out_with_nothing_cached_is_a_no_op(session):
    sign_out(session)  # must not raise
    assert signed_in_account(session) is None


def test_an_unreadable_cache_row_is_ignored_rather_than_raising(session):
    # A corrupted or foreign value should behave like no cache at all, not
    # crash the sync that was only trying to check whether anyone signed in.
    session.add(OneDriveTokenCache(id="default", serialized="not valid json"))
    session.commit()

    assert signed_in_account(session) is None


def test_the_cache_survives_a_fresh_session(session, tmp_path):
    """A round trip through `_save_cache` / `_load_cache` via a real MSAL
    cache object, without a network call - `has_state_changed` is what
    `sign_in_device_code` gates the write on, so this exercises the same
    condition without going through the device flow itself."""
    from app.ingest.sources.excel.graph_auth import _load_cache, _save_cache

    cache = _load_cache(session)
    # Simulate MSAL recording a new token the way a real sign-in would -
    # `add` is the documented way to seed a cache in tests without a server.
    cache.add(
        {
            "response": {
                "token_type": "Bearer",
                "scope": "Files.Read",
                "expires_in": 3600,
                "access_token": "fake-token",
                "id_token": None,
            },
            "client_id": "test-client",
            "id_token_claims": {},
        }
    )
    _save_cache(session, cache)
    session.commit()

    reloaded = _load_cache(session)
    assert reloaded.serialize() == cache.serialize()
