"""Copy the fleet feed out of Smart-Truck and into this module's database.

Why copy rather than read through
---------------------------------
The brief was a standalone control centre. A module that queries Smart-Truck
on every map refresh is not standalone -- it inherits Smart-Truck's uptime,
its schema changes, and its load. After this copy runs, the application never
touches Smart-Truck again: it can be pointed at a different source, or at no
source at all, and the control centre keeps working on the data it owns.

The copy is one-way. Smart-Truck is read over a `READ ONLY` session and
nothing is ever written back.

How it is done
--------------
Both schemas live on the same MySQL server, so the bulk of the work is an
`INSERT ... SELECT` executed server-side -- 5.1M rows never enter Python.
That is roughly two orders of magnitude faster than fetching and re-inserting
them, and it is why this takes a couple of minutes rather than an hour.

A source on a different server is not supported: every copy refuses to
start (`_same_server()`) rather than silently falling back to fetching the
feed through Python. "Same" means `GEO_DB_HOST` / `GEO_DB_PORT` equal
`SRC_DB_HOST` / `SRC_DB_PORT` as written -- `localhost` and `127.0.0.1`
count as two.

Idempotent: the source `id` is preserved as the primary key and rows are
inserted with `INSERT IGNORE`, so a re-run tops up rather than duplicating,
and an interrupted copy can simply be run again.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import geo_session, src_session

logger = logging.getLogger(__name__)

BATCH = 50_000


def _same_server() -> bool:
    """Can MySQL do the copy itself?"""
    g, s = settings.geo_db, settings.src_db
    return (g["host"], g["port"]) == (s["host"], s["port"])


def _src_columns(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            # Aliased: information_schema returns COLUMN_NAME upper-cased.
            "SELECT column_name AS c FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            (settings.src_db["database"], settings.src_gps_table),
        )
        return {r["c"] for r in cur.fetchall()}


# The fleet system's trip columns -> ours. Missing source columns degrade to
# NULL rather than failing, so a different fleet system with a thinner trip
# table still attaches.
TRIP_COLUMNS = {
    "i_trip_no": "i_trip_no",
    "s_asset_id": "s_asset_id",
    "s_asset_type": "s_asset_type",
    "s_trip_class": "s_trip_class",
    "s_trip_type": "s_trip_type_desc",
    "s_driver_name": "s_driver_name",
    "s_driver_mobile": "s_driver_mobile_no",
    "s_trans_id": "i_trans_id",
    "s_trans_name": "s_trans_name",
    "s_cnr_name": "s_cnr_name",
    "s_cne_name": "s_cne_name",
    "s_origin": "s_org_node_name",
    "s_destination": "s_dest_node_name",
    "s_final_dest": "s_final_dest",
    "s_load_plant": "s_load_plant",
    "s_status": "c_trip_status",
    "s_close_reason": "s_close_reason",
    "s_invoice": "s_invoice",
    "dt_booking": "dt_booking",
    "dt_trip_start": "dt_trip_start",
    "dt_trip_eta": "dt_trip_eta",
    "dt_trip_ata": "dt_trip_ata",
    "dt_trip_end": "dt_trip_end",
}


def sync_trips(recent_days: int | None = None) -> dict:
    """Copy the trip master (vehicle, driver, transporter, lane) beside the feed.

    Upserts, so a re-run refreshes trips whose status or arrival changed
    upstream; a row whose values did not change keeps its dt_synced, which is
    how the scheduler finds the ones that did. Server-side like the ping
    copy; Smart-Truck is only read.

    `recent_days` limits the read to trips booked within that many days or
    not yet closed -- what can still change -- so the background refresh
    does not re-read years of closed trips every few minutes.
    """
    src_db = settings.src_db["database"]
    table = settings.src_trip_table
    if not table.replace("_", "").isalnum():
        raise ValueError(f"unsafe source table name: {table!r}")
    t0 = time.perf_counter()
    with src_session() as probe, probe.cursor() as cur:
        cur.execute(
            "SELECT column_name AS c FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s", (src_db, table))
        cols = {r["c"] for r in cur.fetchall()}
    if "i_trip_no" not in cols:
        raise RuntimeError(f"{src_db}.{table} has no i_trip_no column")
    if not _same_server():
        raise RuntimeError("source and target are on different servers; "
                           "cross-server copy is not implemented")

    ours = list(TRIP_COLUMNS)
    select = ", ".join(f"`{TRIP_COLUMNS[c]}`" if TRIP_COLUMNS[c] in cols else "NULL" for c in ours)
    updates = ", ".join(f"{c}=VALUES({c})" for c in ours if c != "i_trip_no")
    where, params = "", []
    if recent_days and "dt_booking" in cols:
        where = "WHERE dt_booking >= %s" + (" OR dt_trip_end IS NULL" if "dt_trip_end" in cols else "")
        params = [datetime.now() - timedelta(days=recent_days)]
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO geo_trip_meta ({', '.join(ours)})
                SELECT {select} FROM `{src_db}`.`{table}` {where}
                ON DUPLICATE KEY UPDATE {updates}""", params)
        conn.commit()
        cur.execute("SELECT COUNT(*) n FROM geo_trip_meta")
        n = cur.fetchone()["n"]
    return {"trips_meta": n, "source": f"{src_db}.{table}",
            "missing_columns": sorted(v for v in TRIP_COLUMNS.values() if v not in cols),
            "seconds": round(time.perf_counter() - t0, 2)}


def sync_after(after_id: int, limit: int | None = None) -> dict:
    """Copy the fixes the fleet system has written since `after_id`.

    The background refresh's copy. The source's own row id is the cursor --
    a range scan of its primary key, however large the table -- and a fix a
    device uploads late still gets a new id, so it is never skipped. Vehicles
    and trips are updated only for what arrived, never by re-aggregating the
    whole feed.
    """
    if not _same_server():
        raise RuntimeError("source and target are on different servers; cross-server copy is not implemented")
    src_db = settings.src_db["database"]
    table = settings.src_gps_table
    t0 = time.perf_counter()
    with src_session() as probe:
        cols = _src_columns(probe)
    moving = "is_moving" if "is_moving" in cols else "0"
    status = "s_status" if "s_status" in cols else "NULL"
    cap = f"LIMIT {int(limit)}" if limit else ""
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT IGNORE INTO geo_gps_ping
                (id, i_trip_no, s_asset_id, s_device_id, dt_message, d_lat, d_long, i_speed, s_status, b_moving)
                SELECT id, i_trip_no, s_asset_id, s_device_id, dt_message, d_lat, d_long,
                       COALESCE(i_speed,0), {status}, {moving}
                  FROM `{src_db}`.`{table}` WHERE id > %s ORDER BY id {cap}""", (after_id,))
        pings = cur.rowcount
        conn.commit()
        if pings:
            cur.execute("""
                INSERT INTO geo_vehicle (s_asset_id, s_device_id, i_pings, dt_first_seen, dt_last_seen)
                SELECT s_asset_id, MAX(s_device_id), COUNT(*), MIN(dt_message), MAX(dt_message)
                  FROM geo_gps_ping WHERE id > %s AND s_asset_id IS NOT NULL GROUP BY s_asset_id
                ON DUPLICATE KEY UPDATE
                    i_pings = geo_vehicle.i_pings + VALUES(i_pings),
                    dt_first_seen = LEAST(geo_vehicle.dt_first_seen, VALUES(dt_first_seen)),
                    dt_last_seen = GREATEST(geo_vehicle.dt_last_seen, VALUES(dt_last_seen))""", (after_id,))
            conn.commit()
    return {"after_id": after_id, "pings_inserted": pings, "seconds": round(time.perf_counter() - t0, 2)}


def sync(truncate: bool = False, limit: int | None = None,
         since: datetime | None = None) -> dict:
    """Copy pings, then derive vehicles and trips from what landed."""
    started = datetime.now()
    src_db = settings.src_db["database"]
    table = settings.src_gps_table
    source = f"{settings.src_db['host']}:{settings.src_db['port']}/{src_db}.{table}"

    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO geo_fleet_sync (dt_started, s_source, s_status) VALUES (%s,%s,'running')",
                (started, source),
            )
            sync_id = cur.lastrowid
        conn.commit()

    t0 = time.perf_counter()
    try:
        with src_session() as probe:
            cols = _src_columns(probe)
        # `is_moving` and `s_status` are Smart-Truck specific and may not exist
        # in another fleet system; degrade rather than fail.
        moving = "is_moving" if "is_moving" in cols else "0"
        status = "s_status" if "s_status" in cols else "NULL"

        where, params = [], []
        if since:
            where.append("dt_message >= %s")
            params.append(since)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        cap = f"LIMIT {int(limit)}" if limit else ""

        with geo_session() as conn:
            with conn.cursor() as cur:
                if truncate:
                    cur.execute("TRUNCATE TABLE geo_gps_ping")
                    cur.execute("TRUNCATE TABLE geo_vehicle")
                    cur.execute("TRUNCATE TABLE geo_trip")
                    conn.commit()

                if not _same_server():
                    raise RuntimeError(
                        "source and target are on different servers; "
                        "cross-server copy is not implemented"
                    )

                logger.info("copying pings from %s ...", source)
                cur.execute(
                    f"""INSERT IGNORE INTO geo_gps_ping
                        (id, i_trip_no, s_asset_id, s_device_id, dt_message,
                         d_lat, d_long, i_speed, s_status, b_moving)
                        SELECT id, i_trip_no, s_asset_id, s_device_id, dt_message,
                               d_lat, d_long, COALESCE(i_speed,0), {status}, {moving}
                          FROM `{src_db}`.`{table}` {clause} {cap}""",
                    params,
                )
                pings = cur.rowcount
                conn.commit()

                logger.info("deriving vehicles ...")
                cur.execute("""
                    INSERT INTO geo_vehicle
                        (s_asset_id, s_device_id, i_pings, dt_first_seen, dt_last_seen)
                    SELECT s_asset_id, MAX(s_device_id), COUNT(*),
                           MIN(dt_message), MAX(dt_message)
                      FROM geo_gps_ping
                     WHERE s_asset_id IS NOT NULL
                     GROUP BY s_asset_id
                    ON DUPLICATE KEY UPDATE
                        i_pings = VALUES(i_pings),
                        dt_first_seen = LEAST(geo_vehicle.dt_first_seen, VALUES(dt_first_seen)),
                        dt_last_seen  = GREATEST(geo_vehicle.dt_last_seen, VALUES(dt_last_seen))
                """)
                conn.commit()
                cur.execute("SELECT COUNT(*) n FROM geo_vehicle")
                vehicles = cur.fetchone()["n"]

                logger.info("deriving trips ...")
                cur.execute("""
                    INSERT INTO geo_trip
                        (i_trip_no, s_asset_id, dt_first_ping, dt_last_ping, i_pings)
                    SELECT i_trip_no, MAX(s_asset_id), MIN(dt_message), MAX(dt_message), COUNT(*)
                      FROM geo_gps_ping GROUP BY i_trip_no
                    ON DUPLICATE KEY UPDATE
                        i_pings = VALUES(i_pings),
                        dt_last_ping = GREATEST(geo_trip.dt_last_ping, VALUES(dt_last_ping))
                """)
                conn.commit()
                cur.execute("SELECT COUNT(*) n FROM geo_trip")
                trips = cur.fetchone()["n"]

                cur.execute("SELECT COUNT(*) n, MIN(dt_message) a, MAX(dt_message) b FROM geo_gps_ping")
                totals = cur.fetchone()

        elapsed = time.perf_counter() - t0
        with geo_session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE geo_fleet_sync SET dt_finished=%s, i_pings=%s, i_vehicles=%s,
                              i_trips=%s, d_seconds=%s, s_status='ok' WHERE i_sync_id=%s""",
                    (datetime.now(), totals["n"], vehicles, trips, round(elapsed, 2), sync_id),
                )
            conn.commit()

        try:
            trip_meta = sync_trips()
        except Exception as exc:                    # noqa: BLE001
            # The feed is usable without trip metadata; pages just cannot cut
            # by driver or transporter until it arrives.
            logger.warning("trip master not copied: %s", exc)
            trip_meta = {"error": str(exc)}

        return {
            "sync_id": sync_id, "source": source,
            "pings_inserted": pings, "pings_total": totals["n"],
            "vehicles": vehicles, "trips": trips,
            "trip_meta": trip_meta,
            "span": [totals["a"], totals["b"]],
            "seconds": round(elapsed, 2),
        }

    except Exception as exc:
        with geo_session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE geo_fleet_sync SET dt_finished=%s, s_status='failed', s_error=%s WHERE i_sync_id=%s",
                    (datetime.now(), f"{type(exc).__name__}: {exc}"[:4000], sync_id),
                )
            conn.commit()
        raise
