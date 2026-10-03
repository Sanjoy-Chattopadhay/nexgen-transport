"""Smart-Truck's database helpers (get_connection, get_db, db_session),
connecting to the schema of the service the code runs in.

Ported Smart-Truck code calls `get_connection()` and then queries tables by
their legacy names (`tta_trips`, `tta_trip_gps`, `geofences`, ...). In
NexGen each of those names exists in the hosting service's schema -- as a
table the service owns, or as an alias view over another service's published
v1_* view (created by the service's R__*.sql migration). So the same SQL runs
unchanged, against normalised storage, without reaching into another
service's tables.

Which schema: the service this process serves (NEXGEN_SERVICE, set by the
supervisor), or one named explicitly with `use_schema()` for scripts.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar

from nexgen.core.db import connect

_override: ContextVar[str | None] = ContextVar("nexgen_legacy_schema", default=None)


def current_schema_key() -> str:
    key = _override.get() or os.environ.get("NEXGEN_SERVICE") or ""
    if not key:
        raise RuntimeError("no service schema: set NEXGEN_SERVICE or wrap the call in use_schema()")
    return key


@contextmanager
def use_schema(key: str):
    token = _override.set(key)
    try:
        yield
    finally:
        _override.reset(token)


def get_connection(**overrides):
    return connect(current_schema_key(), **overrides)


def get_conn(**overrides):
    return get_connection(**overrides)


def get_db():
    """FastAPI dependency: a connection for the request, closed afterwards."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def db_session(**overrides):
    conn = get_connection(**overrides)
    try:
        yield conn
    finally:
        conn.close()


def init_database():
    """Smart-Truck created its database here; NexGen's migrations do that."""
    from nexgen.core.db import ensure_databases
    ensure_databases()
