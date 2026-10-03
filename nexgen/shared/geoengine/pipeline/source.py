"""Read GPS trails out of the source fleet database.

Everything here is a SELECT against a read-only session. The source table is
named in configuration (`SRC_GPS_TABLE`) and the column mapping is declared in
one place below, so attaching this module to a different fleet system means
editing `COLUMNS`, not the engine.

Trails are yielded one trip at a time even though the underlying cursor
streams row by row. A trip is the unit the detector works on -- it needs the
whole ordered trail to decide what held long enough to be believed -- but the
corpus is 5.1M fixes and materialising all of them at once is roughly 2 GB of
Python objects. Grouping a streaming cursor gives both: one trip resident at a
time, one query for the whole scan.
"""

from __future__ import annotations

import logging
from datetime import datetime

from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import src_conn

logger = logging.getLogger(__name__)

# The engine's field names -> the source table's. The only place the source
# schema is named.
COLUMNS = {
    "trip": "i_trip_no",
    "asset": "s_asset_id",
    "ts": "dt_message",
    "lat": "d_lat",
    "lon": "d_long",
    "speed": "i_speed",
}


def _table() -> str:
    t = settings.src_gps_table
    if not t.replace("_", "").isalnum():
        raise ValueError(f"unsafe source table name: {t!r}")
    return t


# ---------------------------------------------------------------------------
# The module's own copy of the feed
#
# Batch runs read `geo_gps_ping` by default: the application owns its data,
# and a run must not depend on Smart-Truck being up. `feed="src"` reads the
# source table instead, over the read-only session.
# ---------------------------------------------------------------------------

FEEDS = ("geo", "src")


def _feed_conn(feed: str):
    if feed == "src":
        return src_conn()
    from nexgen.shared.geoengine.db import geo_conn
    return geo_conn()


def list_trips_feed(feed: str = "geo", dt_from: datetime | None = None,
                    dt_to: datetime | None = None, limit: int | None = None,
                    trip_nos: list[int] | None = None) -> list[int]:
    """Trip numbers in scope, oldest activity first."""
    if feed == "src":
        return [t["trip_no"] for t in list_trips(dt_from=dt_from, dt_to=dt_to, limit=limit)]
    conn = _feed_conn(feed)
    try:
        where, params = [], []
        if trip_nos:
            where.append(f"i_trip_no IN ({','.join(['%s'] * len(trip_nos))})")
            params.extend(trip_nos)
        # A trip is in a window if any of its fixes are.
        if dt_from:
            where.append("dt_last_ping >= %s")
            params.append(dt_from)
        if dt_to:
            where.append("dt_first_ping < %s")
            params.append(dt_to)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT i_trip_no FROM geo_trip {clause} ORDER BY dt_first_ping, i_trip_no"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [r["i_trip_no"] for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_trip_feed(trip_no: int, feed: str = "geo", conn=None) -> list[dict]:
    """Every fix for one trip with its row id, in time order."""
    table = "geo_gps_ping" if feed == "geo" else _table()
    close = conn is None
    conn = conn or _feed_conn(feed)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, {COLUMNS['ts']} AS dt_message, {COLUMNS['lat']} AS d_lat,
                           {COLUMNS['lon']} AS d_long, {COLUMNS['speed']} AS i_speed,
                           {COLUMNS['asset']} AS s_asset_id
                      FROM {table}
                     WHERE {COLUMNS['trip']} = %s
                     ORDER BY {COLUMNS['ts']}, id""",
                (trip_no,),
            )
            return list(cur.fetchall())
    finally:
        if close:
            conn.close()


def list_trips(
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
    limit: int | None = None,
    min_pings: int = 1,
    conn=None,
) -> list[dict]:
    """Trips in scope, with their ping counts and time span."""
    close = conn is None
    conn = conn or src_conn()
    try:
        where, params = [], []
        if dt_from:
            where.append(f"{COLUMNS['ts']} >= %s")
            params.append(dt_from)
        if dt_to:
            where.append(f"{COLUMNS['ts']} < %s")
            params.append(dt_to)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT {COLUMNS['trip']} AS trip_no,
                   MIN({COLUMNS['asset']}) AS asset_id,
                   COUNT(*)   AS pings,
                   MIN({COLUMNS['ts']}) AS dt_first,
                   MAX({COLUMNS['ts']}) AS dt_last
              FROM {_table()}
              {clause}
             GROUP BY {COLUMNS['trip']}
            HAVING COUNT(*) >= %s
             ORDER BY {COLUMNS['trip']}
        """
        params.append(min_pings)
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        if close:
            conn.close()


def fetch_trip(trip_no: int, conn=None) -> list[dict]:
    """Every fix for one trip, in time order."""
    close = conn is None
    conn = conn or src_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {COLUMNS['ts']} AS dt_message, {COLUMNS['lat']} AS d_lat,
                           {COLUMNS['lon']} AS d_long, {COLUMNS['speed']} AS i_speed,
                           {COLUMNS['asset']} AS s_asset_id
                      FROM {_table()}
                     WHERE {COLUMNS['trip']} = %s
                     ORDER BY {COLUMNS['ts']}""",
                (trip_no,),
            )
            return list(cur.fetchall())
    finally:
        if close:
            conn.close()


def stream_trips(
    trip_nos: list[int] | None = None,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
    conn=None,
):
    """Yield `(trip_no, asset_id, rows)` per trip from one streaming scan.

    Relies on `ORDER BY trip, time` to group, so a trip's fixes are contiguous
    and only one trip is ever held in memory.
    """
    close = conn is None
    conn = conn or src_conn(streaming=True)
    try:
        where, params = [], []
        if trip_nos:
            where.append(f"{COLUMNS['trip']} IN ({','.join(['%s'] * len(trip_nos))})")
            params.extend(trip_nos)
        if dt_from:
            where.append(f"{COLUMNS['ts']} >= %s")
            params.append(dt_from)
        if dt_to:
            where.append(f"{COLUMNS['ts']} < %s")
            params.append(dt_to)
        clause = f"WHERE {' AND '.join(where)}" if where else ""

        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {COLUMNS['trip']} AS trip_no, {COLUMNS['asset']} AS s_asset_id,
                           {COLUMNS['ts']} AS dt_message, {COLUMNS['lat']} AS d_lat,
                           {COLUMNS['lon']} AS d_long, {COLUMNS['speed']} AS i_speed
                      FROM {_table()}
                      {clause}
                     ORDER BY {COLUMNS['trip']}, {COLUMNS['ts']}""",
                params,
            )
            current: int | None = None
            asset: str | None = None
            batch: list[dict] = []
            for row in cur:
                if row["trip_no"] != current:
                    if current is not None:
                        yield current, asset, batch
                    current = row["trip_no"]
                    asset = row["s_asset_id"]
                    batch = []
                batch.append(row)
            if current is not None:
                yield current, asset, batch
    finally:
        if close:
            conn.close()
