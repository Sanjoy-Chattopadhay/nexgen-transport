"""Database access for every service.

`connect('fleet')` opens a connection to the schema database.yaml names for
the key 'fleet', with that schema's credentials. A service connects to its own
schema; what it needs from other services it reads through the published
`v1_*` views (or the local alias views its migrations create over them), never
through another service's tables.

`legacy('smart_truck')` opens one of the two legacy databases READ ONLY: the
session runs `SET SESSION TRANSACTION READ ONLY`, so an accidental write raises
MySQL error 1792 rather than changing them. Geo-Fencing introduced this guard
for Smart-Truck; it now protects both legacy databases, which stay untouched
as the import source and the comparison baseline.

Connections are opened per use, as both legacy applications did. PyMySQL has
no pool, MySQL connects in about a millisecond on localhost, and a connection
held across requests is how one slow query ends up blocking unrelated pages.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import pymysql
from pymysql.cursors import DictCursor, SSDictCursor

from nexgen.core.config import get_config

logger = logging.getLogger(__name__)


def _params(schema_key: str | None, database: str | None = None, server: str = "primary") -> dict:
    cfg = get_config()
    srv = cfg.server(server)
    pool = cfg.database_raw.get("pool") or {}
    creds = cfg.credentials(schema_key or "default")
    params = {
        "host": srv.get("host", "localhost"),
        "port": int(srv.get("port", 3306)),
        "user": creds.get("user", "root"),
        "password": creds.get("password", ""),
        "charset": pool.get("charset", "utf8mb4"),
        "connect_timeout": int(pool.get("connect_timeout_s", 30)),
        "read_timeout": int(pool.get("read_timeout_s", 600)),
        "write_timeout": int(pool.get("write_timeout_s", 600)),
    }
    if database:
        params["database"] = database
    elif schema_key:
        params["database"] = cfg.schema(schema_key)
    return params


def connect(schema_key: str, *, streaming: bool = False, read_only: bool = False,
            autocommit: bool = False, **overrides) -> pymysql.connections.Connection:
    """A connection to one service schema. DictCursor rows; caller commits."""
    kw = {**_params(schema_key), "cursorclass": SSDictCursor if streaming else DictCursor,
          "autocommit": autocommit}
    kw.update(overrides)
    conn = pymysql.connect(**kw)
    if read_only:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
    return conn


@contextmanager
def session(schema_key: str, **kw) -> Iterator[pymysql.connections.Connection]:
    conn = connect(schema_key, **kw)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - closing a dead socket
            pass


def server_connection(server: str = "primary", **overrides) -> pymysql.connections.Connection:
    """A connection with no database selected, for CREATE DATABASE and
    information_schema queries."""
    kw = {**_params(None, server=server), "cursorclass": DictCursor, "autocommit": True}
    kw.update(overrides)
    return pymysql.connect(**kw)


def legacy(name: str, *, streaming: bool = False, **overrides) -> pymysql.connections.Connection:
    """A READ ONLY connection to a legacy database ('smart_truck' | 'geofencing')."""
    cfg = get_config()
    kw = {**_params(None, database=cfg.legacy_database(name)),
          "cursorclass": SSDictCursor if streaming else DictCursor, "autocommit": False}
    kw.update(overrides)
    conn = pymysql.connect(**kw)
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
    return conn


@contextmanager
def legacy_session(name: str, **kw) -> Iterator[pymysql.connections.Connection]:
    conn = legacy(name, **kw)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover
            pass


def ensure_databases() -> list[str]:
    """Create every schema database.yaml names that does not exist yet."""
    cfg = get_config()
    created = []
    conn = server_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT schema_name AS s FROM information_schema.schemata")
            have = {r["s"] for r in cur.fetchall()}
            for key in cfg.schema_keys:
                name = cfg.schema(key)
                if name in have:
                    continue
                cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                created.append(name)
                logger.info("created database %s", name)
    finally:
        conn.close()
    return created


@contextmanager
def named_lock(conn, name: str, timeout_s: int = 0) -> Iterator[bool]:
    """MySQL GET_LOCK held for the block. Yields whether it was acquired.

    Named locks are server-wide, so this keeps one runner per job across every
    process and machine using the server -- the guard Geo-Fencing's scheduler
    used, generalised to every job and consumer.
    """
    lock = f"nx:{name}"[:64]
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, %s) AS got", (lock, timeout_s))
        got = bool((cur.fetchone() or {}).get("got"))
    try:
        yield got
    finally:
        if got:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT RELEASE_LOCK(%s)", (lock,))
            except Exception:  # pragma: no cover - connection already gone
                pass


def fetch_all(schema_key: str, sql: str, params=None) -> list[dict]:
    with session(schema_key) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(schema_key: str, sql: str, params=None) -> dict | None:
    with session(schema_key) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()
