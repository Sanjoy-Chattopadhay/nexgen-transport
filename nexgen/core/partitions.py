"""Monthly partitions: keep future months ready, retire old months whole.

High-volume time-series tables (gps_fix, the geofence ledgers, live events)
are RANGE-partitioned by month. Two jobs keep them healthy:

  * upkeep: split the catch-all `p_future` partition so the next few months
    each have their own partition before data arrives (a row landing in
    p_future would make that partition impossible to drop by month later);
  * retention: archive a month that has aged out (compressed JSON lines) and
    DROP its partition. Dropping is instant and returns the space, where
    Smart-Truck's DELETE in 20,000-row batches took hours at scale and left
    the space allocated -- the reason geo_live_event held 500 MB with no rows.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime
from pathlib import Path

from nexgen.core.config import get_config

logger = logging.getLogger(__name__)


def _month_add(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def partitions(conn, schema: str, table: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT partition_name AS name, partition_description AS bound, table_rows AS rows_ "
                    "FROM information_schema.partitions WHERE table_schema=%s AND table_name=%s "
                    "AND partition_name IS NOT NULL ORDER BY partition_ordinal_position", (schema, table))
        return list(cur.fetchall())


def _bound(p: dict) -> date | None:
    b = str(p["bound"] or "").strip("'")
    if b.upper() == "MAXVALUE" or not b:
        return None
    return datetime.strptime(b[:10], "%Y-%m-%d").date()


def ensure_future(conn, table: str, months_ahead: int = 3, schema_key: str | None = None) -> list[str]:
    """Make sure every month up to `months_ahead` from now has its partition."""
    schema = get_config().schema(schema_key) if schema_key else conn.db.decode()
    parts = partitions(conn, schema, table)
    bounded = [p for p in parts if _bound(p) is not None]
    if not bounded or not any(p["name"] == "p_future" for p in parts):
        return []
    last = max(_bound(p) for p in bounded)
    target = _month_add(date.today().replace(day=1), months_ahead + 1)
    added = []
    while last < target:
        nxt = _month_add(last, 1)
        name = f"p{last:%Y_%m}"
        with conn.cursor() as cur:
            cur.execute(f"ALTER TABLE `{table}` REORGANIZE PARTITION p_future INTO ("
                        f"PARTITION {name} VALUES LESS THAN ('{nxt:%Y-%m-%d}'), "
                        f"PARTITION p_future VALUES LESS THAN (MAXVALUE))")
        added.append(name)
        last = nxt
    if added:
        logger.info("%s: added partitions %s", table, ", ".join(added))
    return added


def retire(conn, table: str, keep_days: int, time_column: str, archive_dir: Path | None,
           schema_key: str, dry_run: bool = False) -> dict:
    """Archive and drop every monthly partition entirely older than keep_days."""
    schema = get_config().schema(schema_key)
    cutoff = date.today().toordinal() - int(keep_days)
    out = {"table": table, "keep_days": keep_days, "retired": [], "rows": 0, "dry_run": dry_run}
    for p in partitions(conn, schema, table):
        b = _bound(p)
        if b is None or b.toordinal() > cutoff:
            continue
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) n FROM `{table}` PARTITION ({p['name']})")
            n = int(cur.fetchone()["n"])
        entry = {"partition": p["name"], "rows": n, "before": b.isoformat()}
        if not dry_run:
            if n and archive_dir is not None:
                from nexgen.core.db import connect
                archive_dir.mkdir(parents=True, exist_ok=True)
                path = archive_dir / f"{table}_{p['name']}_{datetime.now():%Y%m%d%H%M%S}.jsonl.gz"
                # A month can be tens of millions of rows: stream it, never
                # hold it in memory.
                with gzip.open(path, "wt", encoding="utf-8") as gz, \
                        connect(schema_key, streaming=True) as sconn, sconn.cursor() as scur:
                    scur.execute(f"SELECT * FROM `{table}` PARTITION ({p['name']}) ORDER BY {time_column}")
                    for row in scur:
                        gz.write(json.dumps(row, default=str) + "\n")
                entry["archive"] = path.name
            with conn.cursor() as cur:
                if p["name"] == "p_old":
                    cur.execute(f"ALTER TABLE `{table}` TRUNCATE PARTITION p_old")
                else:
                    cur.execute(f"ALTER TABLE `{table}` DROP PARTITION {p['name']}")
        out["retired"].append(entry)
        out["rows"] += n
    return out
