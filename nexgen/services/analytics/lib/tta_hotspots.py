"""
Pilferage Hotspot Discovery — Phase 1: coordinate-level stop extraction.

WHAT THIS IS
------------
Every confirmed standstill in the GPS trail, reduced to a *coordinate* and
persisted in `gps_stop_events`. Phase 2 clusters those coordinates (DBSCAN),
masks known places, and scores what remains.

WHY IT IS NOT tta_waypoints
---------------------------
`tta_waypoints.refresh_waypoints` already finds standstill blocks — and then
collapses each to the nearest *named node* from the feed. That is the right
call for detention analytics and the wrong one here: a pilferage point is by
definition a place the provider has never named. In this dataset 26% of all
stopped pings sit more than 5 km from any named node. This module keeps the
block's median lat/long so that mass stays addressable.

TWO INVARIANTS THAT MATTER
--------------------------
1. APPEND-ONLY. `refresh_waypoints` is a full idempotent rebuild from
   `tta_trip_gps`. Copying that here would be destructive: once
   `db_maintenance.run_gps_purge` archives old pings (and
   `tta_network.reconcile_ping_counts` zeroes the denormalized count), a full
   rebuild would find nothing for those trips and silently erase their history.
   These rows are meant to OUTLIVE the pings they came from, so a trip is
   re-extracted only when its ping count strictly GREW.

2. INDEXED READS ONLY. Extraction never scans `tta_trip_gps` whole. It walks
   pending trips one at a time on `idx_gps_trip_time`, so it can run alongside
   the TMS sync without competing with it for the big table. It also takes its
   own lock (mirroring `tta_api_sync._wp_lock`) so it never blocks a sync and
   never overlaps itself.

REJECTIONS ARE COUNTED, NOT SILENT
----------------------------------
Every guard below throws blocks away. `run_stop_extraction` returns a tally of
each rejection reason, so a run that quietly drops 90% of its input is visible
rather than mistaken for a clean one.
"""

import logging
import threading
from math import radians, sin, cos, asin, sqrt
from pathlib import Path

logger = logging.getLogger(__name__)

from nexgen.core.config import ROOT as PROJECT_ROOT

# --- Tunables -------------------------------------------------------------
# A stop shorter than this is traffic, not a facility visit. Deliberately well
# below the 30-60 min pilferage signature: Phase 2 scores the band, Phase 1
# must not pre-judge it away.
MIN_STOP_MIN = 10.0

# A "stop" built from one or two pings is not evidence of anything.
MIN_PINGS = 3

# Ping silence longer than this SPLITS a block rather than dropping it. The
# truck may or may not have stayed; two confirmed short stops is the honest
# reading, one long unconfirmed stop is not. Mirrors the 15-min gap cap that
# tta_waypoints/tta_network already use for dwell attribution.
MAX_GAP_MIN = 15.0

# If the block's pings sprawl further than this from their own centroid, the
# vehicle was crawling (or the fixes are bad) — that is not a standstill.
# Measured: real stops average 75 m of spread, so this is a loose outer bound.
MAX_SPREAD_M = 500

# Distance from the nearest named node beyond which a stop counts as
# "isolated". NOT `s_wpnt IS NULL` — the feed's s_wpnt1 is the nearest node
# *behind* the vehicle and is therefore never null, only far away. Measured on
# this corpus: 63% of stops are >2 km out, 44% >5 km, max 131 km.
ISOLATION_M = 2000

# Trips per run when the caller does not say. Keeps a scheduled run bounded.
DEFAULT_TRIP_LIMIT = 500

# Mirrors tta_api_sync._wp_lock: a slow extraction must never block a sync,
# and two extractions must never overlap.
_extract_lock = threading.Lock()


# ============================================
# GEO
# ============================================

def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371008.8


# ============================================
# SCHEMA
# ============================================

def run_hotspot_schema(conn) -> dict:
    """Apply migrations/schema_hotspots.sql (idempotent).

    Strips comments to the end of line rather than only dropping comment-only
    lines (which is what run_tta_schema does). The difference matters: this DDL
    splits on ';', so a semicolon inside a trailing `-- comment` would cut a
    CREATE TABLE in half. Safe here because the file contains no string
    literals — it is pure DDL.
    """
    # NexGen: every table is created by the migrations (python -m nexgen migrate);
    # tta_trips and the other feed tables are read-only views in this schema.
    return {"status": "ok", "note": "tables are created by NexGen migrations"}
    sql_path = PROJECT_ROOT / "migrations" / "schema_hotspots.sql"
    sql = sql_path.read_text(encoding="utf-8")
    sql = "\n".join(ln.split("--", 1)[0] for ln in sql.splitlines())
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()
    return {"status": "ok", "statements_executed": len(statements)}


# ============================================
# BLOCK DETECTION
# ============================================

def _blocks(pings):
    """Yield runs of consecutive stopped pings.

    A run ends when the vehicle moves, OR when the ping trail goes silent for
    longer than MAX_GAP_MIN — the silence is split out rather than swallowed,
    so an outage can never be read as a long dwell.
    """
    block, prev_ts = [], None
    for p in pings:
        if p["is_moving"]:
            if block:
                yield block
            block, prev_ts = [], None
            continue
        if prev_ts is not None:
            gap = (p["dt_message"] - prev_ts).total_seconds() / 60.0
            if gap > MAX_GAP_MIN:
                if block:
                    yield block
                block = []
        block.append(p)
        prev_ts = p["dt_message"]
    if block:
        yield block


def _summarize(block, tally: dict):
    """Reduce one standstill block to a stop-event row, or None if it fails a
    guard. Every rejection is counted in `tally`."""
    n = len(block)
    if n < MIN_PINGS:
        tally["rejected_too_few_pings"] += 1
        return None

    dt_start, dt_end = block[0]["dt_message"], block[-1]["dt_message"]
    duration = (dt_end - dt_start).total_seconds() / 60.0
    if duration < MIN_STOP_MIN:
        tally["rejected_too_short"] += 1
        return None

    # Median, not mean — a single stray fix must not drag the centroid.
    lats = sorted(float(p["d_lat"]) for p in block)
    lngs = sorted(float(p["d_long"]) for p in block)
    lat, lng = lats[n // 2], lngs[n // 2]

    spread = max(
        _haversine_m(lat, lng, float(p["d_lat"]), float(p["d_long"])) for p in block
    )
    if spread > MAX_SPREAD_M:
        tally["rejected_drifting"] += 1
        return None

    max_gap = 0.0
    for a, b in zip(block, block[1:]):
        max_gap = max(max_gap, (b["dt_message"] - a["dt_message"]).total_seconds() / 60.0)

    # Dominant named node during the stop (often None — that is the signal).
    names = [p["s_wpnt1"] for p in block if p["s_wpnt1"]]
    wpnt = max(set(names), key=names.count) if names else None
    wpnt_mt = None
    if wpnt:
        dists = sorted(
            p["i_wpnt1_mt"] for p in block
            if p["s_wpnt1"] == wpnt and p["i_wpnt1_mt"] is not None
        )
        if dists:
            wpnt_mt = int(dists[len(dists) // 2])

    return {
        "dt_start": dt_start,
        "dt_end": dt_end,
        "d_duration_min": round(duration, 1),
        "i_hour": dt_start.hour,
        "d_lat": round(lat, 8),
        "d_long": round(lng, 8),
        "i_spread_m": int(spread),
        "i_ping_count": n,
        "d_max_gap_min": round(max_gap, 1),
        "s_wpnt": wpnt,
        "i_wpnt_mt": wpnt_mt,
    }


# ============================================
# EXTRACTION
# ============================================

def _pending_trips(conn, limit: int) -> list[dict]:
    """Trips needing extraction: never processed, or grown since last time.

    The `>` is load-bearing. A trip whose ping count SHRANK has been purged and
    reconciled — re-extracting it would find an empty trail and delete evidence
    that can no longer be rebuilt. Only growth (a still-syncing trip gaining
    pings) justifies a re-run.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.i_trip_no, t.i_cnr_id, t.s_asset_id, t.i_trans_id,
                      t.s_trans_name, t.i_gps_ping_count
               FROM tta_trips t
               LEFT JOIN gps_stop_extract_log l ON l.i_trip_no = t.i_trip_no
               WHERE t.i_gps_ping_count > 0
                 AND (l.i_trip_no IS NULL OR t.i_gps_ping_count > l.i_ping_count)
               ORDER BY t.i_trip_no
               LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def _extract_trip(conn, trip: dict, tally: dict) -> int:
    """Extract and persist one trip's stop events. Returns rows written.

    Reads only via idx_gps_trip_time — never a full scan of tta_trip_gps.
    """
    trip_no = trip["i_trip_no"]
    with conn.cursor() as cur:
        cur.execute(
            """SELECT dt_message, d_lat, d_long, is_moving, s_wpnt1, i_wpnt1_mt
               FROM tta_trip_gps WHERE i_trip_no = %s ORDER BY dt_message""",
            (trip_no,),
        )
        pings = cur.fetchall()

    # Guard: the denormalized count promised pings, the table has none — they
    # were purged. Do NOT touch whatever evidence already exists for this trip.
    #
    # The log is stamped with the trip's CLAIMED count, not 0. Writing 0 here
    # would leave `t.i_gps_ping_count > l.i_ping_count` permanently true and the
    # trip permanently pending — an infinite re-scan. Recording the claim marks
    # it settled until the claim itself grows again.
    if not pings:
        tally["skipped_no_pings"] += 1
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO gps_stop_extract_log (i_trip_no, i_ping_count, i_stops_found)
                   VALUES (%s, %s, 0)
                   ON DUPLICATE KEY UPDATE
                       i_ping_count = VALUES(i_ping_count),
                       dt_extracted = CURRENT_TIMESTAMP""",
                (trip_no, trip["i_gps_ping_count"]),
            )
        conn.commit()
        return 0

    stops = [s for s in (_summarize(b, tally) for b in _blocks(pings)) if s]

    with conn.cursor() as cur:
        # Re-extraction of a grown trip: clear this trip's rows only, then
        # rewrite. Scoped to one trip — the corpus is never truncated.
        cur.execute("DELETE FROM gps_stop_events WHERE i_trip_no = %s", (trip_no,))
        for s in stops:
            cur.execute(
                """INSERT INTO gps_stop_events
                   (i_trip_no, cnr_id, s_asset_id, i_trans_id, s_trans_name,
                    dt_start, dt_end, d_duration_min, i_hour,
                    d_lat, d_long, i_spread_m, i_ping_count, d_max_gap_min,
                    s_wpnt, i_wpnt_mt)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                       dt_end = VALUES(dt_end),
                       d_duration_min = VALUES(d_duration_min),
                       d_lat = VALUES(d_lat), d_long = VALUES(d_long),
                       i_spread_m = VALUES(i_spread_m),
                       i_ping_count = VALUES(i_ping_count),
                       d_max_gap_min = VALUES(d_max_gap_min),
                       s_wpnt = VALUES(s_wpnt), i_wpnt_mt = VALUES(i_wpnt_mt)""",
                (trip_no, trip["i_cnr_id"], trip["s_asset_id"], trip["i_trans_id"],
                 trip["s_trans_name"], s["dt_start"], s["dt_end"], s["d_duration_min"],
                 s["i_hour"], s["d_lat"], s["d_long"], s["i_spread_m"],
                 s["i_ping_count"], s["d_max_gap_min"], s["s_wpnt"], s["i_wpnt_mt"]),
            )

        cur.execute(
            """INSERT INTO gps_stop_extract_log (i_trip_no, i_ping_count, i_stops_found)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   i_ping_count = VALUES(i_ping_count),
                   i_stops_found = VALUES(i_stops_found),
                   dt_extracted = CURRENT_TIMESTAMP""",
            (trip_no, trip["i_gps_ping_count"], len(stops)),
        )
    conn.commit()
    return len(stops)


def run_stop_extraction(conn, limit: int | None = None) -> dict:
    """Extract stop events for every pending trip. Skip-if-running.

    Idempotent: running it twice in a row does nothing the second time, because
    every trip it touched is recorded in gps_stop_extract_log at its current
    ping count.
    """
    if not _extract_lock.acquire(blocking=False):
        logger.info("Stop extraction already running — skipped")
        return {"status": "skipped", "reason": "already_running"}

    tally = {
        "rejected_too_few_pings": 0,
        "rejected_too_short": 0,
        "rejected_drifting": 0,
        "skipped_no_pings": 0,
    }
    try:
        trips = _pending_trips(conn, limit or DEFAULT_TRIP_LIMIT)
        stops_written = 0
        failed = 0
        for trip in trips:
            try:
                stops_written += _extract_trip(conn, trip, tally)
            except Exception:
                # One malformed trip must not abort the run.
                failed += 1
                conn.rollback()
                logger.exception("Stop extraction failed for trip %s", trip["i_trip_no"])

        logger.info(
            "Stop extraction: %d trips -> %d stops (%d failed, rejected: %s)",
            len(trips), stops_written, failed, tally,
        )
        return {
            "status": "ok",
            "trips_processed": len(trips),
            "trips_failed": failed,
            "stops_written": stops_written,
            "rejected": tally,
            "more_pending": len(trips) == (limit or DEFAULT_TRIP_LIMIT),
        }
    finally:
        _extract_lock.release()


# ============================================
# STATUS
# ============================================

def stop_extraction_status(conn, cnr_id: int | None = None) -> dict:
    """Coverage + shape of the extracted corpus. Consignor-scoped when asked."""
    where, params = "", []
    if cnr_id:
        where, params = "WHERE cnr_id = %s", [cnr_id]

    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT COUNT(*) AS stops,
                       COUNT(DISTINCT i_trip_no) AS trips,
                       COUNT(DISTINCT s_asset_id) AS vehicles,
                       COUNT(DISTINCT s_trans_name) AS carriers,
                       ROUND(AVG(d_duration_min), 1) AS avg_duration_min,
                       SUM(i_wpnt_mt > {ISOLATION_M}) AS isolated_stops,
                       SUM(i_hour < 5 OR i_hour >= 22) AS night_stops,
                       SUM(d_duration_min BETWEEN 20 AND 90
                           AND i_wpnt_mt > {ISOLATION_M}) AS candidate_stops,
                       MIN(dt_start) AS first_stop,
                       MAX(dt_start) AS last_stop
                FROM gps_stop_events {where}""",
            params,
        )
        corpus = cur.fetchone()

        cur.execute(
            """SELECT COUNT(*) AS extracted,
                      SUM(i_stops_found = 0) AS trips_without_stops
               FROM gps_stop_extract_log"""
        )
        log = cur.fetchone()

        cur.execute(
            """SELECT COUNT(*) AS pending
               FROM tta_trips t
               LEFT JOIN gps_stop_extract_log l ON l.i_trip_no = t.i_trip_no
               WHERE t.i_gps_ping_count > 0
                 AND (l.i_trip_no IS NULL OR t.i_gps_ping_count > l.i_ping_count)"""
        )
        pending = cur.fetchone()["pending"]

    return {"corpus": corpus, "trips_extracted": log["extracted"],
            "trips_without_stops": log["trips_without_stops"], "trips_pending": pending}
