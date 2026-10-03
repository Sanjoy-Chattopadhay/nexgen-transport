"""Versioned, forward-only migrations, one sequence per schema.

    migrations/<schema key>/0001_create_core.sql     versioned: applied once, in order
    migrations/<schema key>/0002_backfill.py         versioned Python: def up(conn, cfg)
    migrations/<schema key>/R__views.sql             repeatable: re-applied on every run

Each schema records what it has applied in its own `schema_migrations` table
(version, name, checksum). A versioned file that changes after it was applied
is refused: an applied migration is history, and editing history is how two
databases end up with the same version and different tables. Fix forward with
a new file instead.

Why not the legacy approach: both Smart-Truck and Geo-Fencing re-ran idempotent
SQL files on every start with no version table. That works for adding tables
and columns, but it cannot express a data move (backfill this column, then
drop that one), and a merged product needs those. The expand -> backfill ->
contract sequence is written as consecutive versioned files.

Repeatable files hold views. MySQL expands `SELECT *` when a view is created,
so a view over another service's view must be re-created whenever that view
gains a column; re-applying every repeatable file on each run (they are all
CREATE OR REPLACE) keeps them in step at the cost of a few milliseconds.

Placeholders let one file name another schema without hard-coding it:
`{{schema:fleet}}` becomes the database database.yaml names for 'fleet', and
`{{legacy:smart_truck}}` the legacy database.

Statements re-run after a failed partial application tolerate "already
exists" errors (MySQL commits each DDL statement on its own, so a file that
failed half way has left its first half behind).
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pymysql

from nexgen.core.config import ROOT, get_config
from nexgen.core.db import connect, ensure_databases

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = ROOT / "migrations"
_VERSIONED = re.compile(r"^(\d{4})_([A-Za-z0-9_\-]+)\.(sql|py)$")
_REPEATABLE = re.compile(r"^R__([A-Za-z0-9_\-]+)\.sql$")
# Errors that mean "this statement already ran": table exists, duplicate
# column, duplicate key name, can't drop missing key, duplicate FK, partition
# already exists, duplicate entry for an INSERT IGNORE-less seed.
_ALREADY = {1050, 1060, 1061, 1091, 1826, 1517, 1068}

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    s_version   VARCHAR(16)  NOT NULL,
    s_name      VARCHAR(200) NOT NULL,
    s_checksum  CHAR(64)     NOT NULL,
    dt_applied  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    i_ms        INT          NOT NULL DEFAULT 0,
    PRIMARY KEY (s_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


@dataclass
class Migration:
    schema_key: str
    version: str | None          # None for repeatable
    name: str
    path: Path
    kind: str                    # sql | py

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def split_sql(text: str) -> list[str]:
    """Split a SQL script into statements.

    Handles `--` and `#` line comments, `/* */` block comments, and quoted
    strings ('', "", ``), so a semicolon inside any of them does not end a
    statement. The legacy loaders split on every ';', which is why their
    schema files could not contain one in a comment.
    """
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote != "`" and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                if i + 1 < n and text[i + 1] == quote:   # doubled quote escape
                    buf.append(text[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if c in ("'", '"', "`"):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == "-" and text.startswith("--", i) and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                out.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def render(text: str) -> str:
    cfg = get_config()

    def schema(m):
        return cfg.schema(m.group(1))

    def legacy(m):
        return cfg.legacy_database(m.group(1))

    text = re.sub(r"\{\{schema:([a-z_]+)\}\}", schema, text)
    return re.sub(r"\{\{legacy:([a-z_]+)\}\}", legacy, text)


def discover(schema_key: str) -> tuple[list[Migration], list[Migration]]:
    folder = MIGRATIONS_DIR / schema_key
    versioned, repeatable = [], []
    if not folder.exists():
        return versioned, repeatable
    for path in sorted(folder.iterdir()):
        m = _VERSIONED.match(path.name)
        if m:
            versioned.append(Migration(schema_key, m.group(1), m.group(2), path, m.group(3)))
            continue
        r = _REPEATABLE.match(path.name)
        if r:
            repeatable.append(Migration(schema_key, None, r.group(1), path, "sql"))
    seen = {}
    for mig in versioned:
        if mig.version in seen:
            raise ValueError(f"{schema_key}: version {mig.version} used by both "
                             f"{seen[mig.version]} and {mig.path.name}")
        seen[mig.version] = mig.path.name
    return versioned, repeatable


def _run_sql(conn, text: str, tolerate: bool) -> int:
    count = 0
    for stmt in split_sql(render(text)):
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
            conn.commit()
            count += 1
        except pymysql.err.MySQLError as exc:
            code = exc.args[0] if exc.args else None
            if tolerate and code in _ALREADY:
                conn.rollback()
                continue
            raise RuntimeError(f"{exc}\n--- statement ---\n{stmt[:2000]}") from exc
    return count


def _run_py(conn, path: Path) -> None:
    spec = importlib.util.spec_from_file_location(f"nexgen_migration_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.up(conn, get_config())
    conn.commit()


def applied(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(_TABLE_SQL)
        cur.execute("SELECT s_version, s_name, s_checksum, dt_applied FROM schema_migrations")
        return {r["s_version"]: r for r in cur.fetchall()}


def status() -> list[dict]:
    """What is applied and what is pending, per schema (developer page)."""
    cfg = get_config()
    rows = []
    for key in cfg.schema_keys:
        versioned, repeatable = discover(key)
        try:
            with connect(key) as conn:
                done = applied(conn)
                conn.commit()
        except pymysql.err.OperationalError as exc:
            rows.append({"schema": key, "database": cfg.schema(key), "error": str(exc),
                         "applied": 0, "pending": len(versioned)})
            continue
        pending = [m.path.name for m in versioned if m.version not in done]
        changed = [m.path.name for m in versioned
                   if m.version in done and done[m.version]["s_checksum"] != m.checksum]
        rows.append({
            "schema": key, "database": cfg.schema(key),
            "applied": len([m for m in versioned if m.version in done]),
            "pending": len(pending), "pending_files": pending,
            "changed_after_apply": changed,
            "repeatable": [m.path.name for m in repeatable],
            "latest": max(done) if done else None,
        })
    return rows


def migrate(schemas: list[str] | None = None, *, dry_run: bool = False) -> list[dict]:
    """Apply every pending migration, then every repeatable file, per schema."""
    cfg = get_config()
    if not dry_run:
        ensure_databases()
    keys = schemas or cfg.schema_keys
    report: list[dict] = []
    # Versioned first, across all schemas in database.yaml order, then the
    # repeatable views: a consumer's alias view needs the owner's table.
    for key in keys:
        versioned, _ = discover(key)
        if not versioned:
            continue
        with connect(key) as conn:
            done = applied(conn)
            conn.commit()
            for mig in versioned:
                if mig.version in done:
                    if done[mig.version]["s_checksum"] != mig.checksum:
                        raise RuntimeError(
                            f"{key}/{mig.path.name} changed after it was applied. Applied "
                            "migrations are history: add a new numbered file instead.")
                    continue
                if dry_run:
                    report.append({"schema": key, "file": mig.path.name, "action": "would apply"})
                    continue
                t0 = time.perf_counter()
                logger.info("applying %s/%s", key, mig.path.name)
                if mig.kind == "sql":
                    statements = _run_sql(conn, mig.path.read_text(encoding="utf-8"), tolerate=True)
                else:
                    _run_py(conn, mig.path)
                    statements = None
                ms = int((time.perf_counter() - t0) * 1000)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO schema_migrations (s_version, s_name, s_checksum, i_ms) "
                        "VALUES (%s, %s, %s, %s)", (mig.version, mig.name, mig.checksum, ms))
                conn.commit()
                report.append({"schema": key, "file": mig.path.name, "action": "applied",
                               "statements": statements, "ms": ms})
    for key in keys:
        _, repeatable = discover(key)
        if not repeatable or dry_run:
            continue
        with connect(key) as conn:
            for mig in repeatable:
                t0 = time.perf_counter()
                n = _run_sql(conn, mig.path.read_text(encoding="utf-8"), tolerate=False)
                report.append({"schema": key, "file": mig.path.name, "action": "refreshed",
                               "statements": n, "ms": int((time.perf_counter() - t0) * 1000)})
    return report
