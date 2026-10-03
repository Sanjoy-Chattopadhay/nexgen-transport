"""Geo-Fencing's database helpers, on NexGen's connections.

`geo_conn()` connects to the schema of the service hosting the engine
(nx_geo for geofence, nx_route for routing). `src_conn()` connects to the same
schema READ ONLY: the source tables there (tta_trip_gps, tta_trips,
geo_gps_ping, ...) are alias views over the fleet service's published views,
so the engine reads the fleet's GPS without touching fleet tables. Schema is
created by NexGen's migrations; ensure_database/apply_schema remain for the
engine's CLI.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from pymysql.cursors import DictCursor, SSDictCursor

from nexgen.core.db import connect
from nexgen.shared.legacy_db import current_schema_key

logger = logging.getLogger(__name__)


def _key() -> str:
    try:
        return current_schema_key()
    except RuntimeError:
        return "geofence"


def geo_conn(**overrides):
    return connect(_key(), **overrides)


@contextmanager
def geo_session(**overrides):
    conn = geo_conn(**overrides)
    try:
        yield conn
    finally:
        conn.close()


def get_geo_db():
    conn = geo_conn()
    try:
        yield conn
    finally:
        conn.close()


def src_conn(streaming: bool = False, **overrides):
    overrides.setdefault("cursorclass", SSDictCursor if streaming else DictCursor)
    return connect(_key(), read_only=True, **overrides)


@contextmanager
def src_session(streaming: bool = False, **overrides):
    conn = src_conn(streaming=streaming, **overrides)
    try:
        yield conn
    finally:
        conn.close()


def ensure_database() -> None:
    from nexgen.core.db import ensure_databases
    ensure_databases()


def apply_schema(sql_path) -> list[str]:
    """NexGen applies the schema through migrations (python -m nexgen migrate)."""
    from nexgen.core.migrate import migrate
    return [f"{r['schema']}/{r['file']}" for r in migrate()]
