"""Builder for the speed / safety rollups.

Turns ~3.5M rows of `tta_trip_gps` into two small tables that the speed and
safety screens read at request time (see migrations/schema_speed_safety.sql):

    gps_speed_profile   speed histogram per trip / zone / hour, at 1 km/h
    gps_speed_events    overspeed episodes, detected once per zone

Everything below runs inside MySQL. The obvious implementation -- stream the
pings into Python and test each one against the geofence index -- moves 3.5M
rows across the wire to answer a question that is five ellipse tests wide.

What the build costs, and why it is shaped this way
---------------------------------------------------
Both derived facts per ping (which zone it is in, how much wall time it
represents) are cheap to state and expensive to compute over the whole table:
the LAG that produces the wall time measured 127 s unchunked.

Three things follow, each of which was measured rather than assumed:

  * **Aggregate in place; never stage.** An earlier version wrote the zoned,
    gap-attributed pings to a staging table so the rollups could share the
    work. Reading a 150-trip chunk takes 1.8 s; *writing* those rows back ran
    at ~1.9k rows/s, which put a full rebuild hours away. INSERT..SELECT with
    the GROUP BY in place writes ~0.5M aggregated rows instead of 3.5M raw
    ones, so the write stops being the bottleneck.

  * **One rollup, two readings.** Hour-of-day sits in the histogram's key
    rather than in a second table, so the 24-hour running-pattern report is the
    same rollup summed over a different axis -- one scan, and no chance of two
    tables disagreeing.

  * **Detect episodes once per zone, not once per limit.** See EVENT_FLOORS.

Zoning
------
'plant' means the ping sits inside a **gazetteer** plant fence -- a published
Tata coordinate with the 10 km radius the business specified, not a fence
derived from the trip data (see geofence/plants.py for why that distinction
matters). 'road' is everywhere else. The split is positional: a truck that
returns to the works at the end of a trip is in-plant for those pings, which is
what an in-plant speed report should say.

Refresh
-------
`refresh_all()` is a full rebuild, not an incremental merge. A rebuild cannot
drift from the ping table the way an incremental merge can when the ETL
back-dates a trip -- which this ETL does.
"""
import json
import logging
import math
import time

from nexgen.shared.circlefence.store import load_fences

logger = logging.getLogger(__name__)

# A ping is credited with the wall time back to the previous ping, capped here.
# Uncapped, a 9-hour GPS outage would be attributed to whatever speed the truck
# happened to be doing when the tracker died. Same cap and same reasoning as
# tta_network.GAP_CAP_MIN, so the two modules' minute totals are comparable.
GAP_CAP_MIN = 15

# An episode ends if the trail goes quiet for longer than this, even when the
# next ping is still over the limit. Two bursts of speeding either side of a
# 40-minute silence are two events; calling them one would invent a duration
# nobody observed.
EVENT_BREAK_MIN = 20

# Trips per chunk for the histogram pass. Large enough that per-statement
# overhead is noise, small enough that each chunk's window-function sort stays
# in memory instead of spilling (one 3.5M-row sort measured 127 s; the chunked
# equivalent is roughly a third of that).
CHUNK_TRIPS = 150

# Limits the UI may select, per zone.
ROAD_LIMITS = (40, 50, 60, 70, 80)
PLANT_LIMITS = (10, 15, 20, 25, 30)

# Business defaults. 60 km/h on the road is the client's stated default; 20 km/h
# in-plant is the standard works limit and is chosen separately because judging
# yard movement against a highway limit would report zero violations forever.
DEFAULT_ROAD_LIMIT = 60
DEFAULT_PLANT_LIMIT = 20

# Episodes are detected once per zone, at the lowest limit that zone offers, so
# that a register built here can serve every higher limit by filtering on peak
# speed. Detecting per limit would mean nine windowed scans of the ping table
# per rebuild. The consequence -- episode counts at a higher limit are a lower
# bound, and duration/distance must come from the histogram -- is spelled out in
# migrations/schema_speed_safety.sql and enforced by speed_safety.py, which
# reads those two figures from the histogram and never from an episode row.
EVENT_FLOORS = {"road": min(ROAD_LIMITS), "plant": min(PLANT_LIMITS)}

ZONES = ("plant", "road")


def limits_for(zone: str) -> tuple[int, ...]:
    return PLANT_LIMITS if zone == "plant" else ROAD_LIMITS


def default_limit(zone: str) -> int:
    return DEFAULT_PLANT_LIMIT if zone == "plant" else DEFAULT_ROAD_LIMIT


# ---------------------------------------------------------------------------
# Zone predicate
# ---------------------------------------------------------------------------

def plant_fences(conn) -> list:
    """The gazetteer plant fences, in whatever role they were seeded under.

    A plant appears as an origin fence when trips start there and as a
    destination fence when trips end there; both are the same physical works,
    so both count as 'plant' here.
    """
    return [f for f in load_fences(conn) if f.source == "plant"]


def zone_expr(fences: list, col_lat: str = "gp.d_lat", col_lon: str = "gp.d_long") -> str:
    """SQL CASE returning 'plant' / 'road' for a ping.

    Each fence is an ellipse in degree space: the radius converted to degrees of
    latitude, and to degrees of longitude by dividing by cos(lat) because a
    degree of longitude is shorter than a degree of latitude away from the
    equator. A point is inside when the normalised squared offsets sum to <= 1.

    Why not ST_Distance_Sphere, which is exact? It constructs two geometry
    objects per row, and 46% of this corpus's pings sit inside the Jamshedpur
    fence, so it is *not* the rare path -- measured 60 s against 21 s for the
    arithmetic below. The two disagree on 0.13% of in-plant pings, all of them
    within metres of a 10 km boundary the business chose as a round number.
    Paying 3x for that is not a trade worth making; treating the fence edge as
    precise would be the actual error.
    """
    if not fences:
        # No plant fences seeded: every ping is 'road'. Reporting an empty
        # in-plant section is honest; silently treating the origin as a plant
        # would invent a boundary that has not been established.
        return "'road'"

    terms = []
    for f in fences:
        dlat = f.radius_m / 110_574.0
        dlon = dlat / max(abs(math.cos(math.radians(f.lat))), 0.01)
        terms.append(
            f"(POW(({col_lat} - {f.lat:.8f}) / {dlat:.8f}, 2)"
            f" + POW(({col_lon} - {f.lon:.8f}) / {dlon:.8f}, 2) <= 1)"
        )
    return "CASE WHEN " + " OR ".join(terms) + " THEN 'plant' ELSE 'road' END"


def _gap_expr() -> str:
    return (f"LEAST(COALESCE(TIMESTAMPDIFF(SECOND,"
            f" LAG(gp.dt_message) OVER (PARTITION BY gp.i_trip_no ORDER BY gp.dt_message),"
            f" gp.dt_message), 0) / 60.0, {GAP_CAP_MIN})")


def _trip_chunks(conn, size: int = CHUNK_TRIPS) -> list[tuple[int, int]]:
    """Trip-id ranges of `size` trips each.

    Chunked by trip COUNT, not by id range: ids come from the source system and
    are wildly uneven, so a fixed id stride puts 13% of the pings in one chunk
    and none in the next.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT i_trip_no FROM tta_trip_gps ORDER BY i_trip_no")
        ids = [int(r["i_trip_no"]) for r in cur.fetchall()]
    return [(ids[i], ids[min(i + size, len(ids)) - 1]) for i in range(0, len(ids), size)]


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------

def _log_build(conn, key: str, rows: int, trips: int, pings: int,
               seconds: float, detail: dict | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gps_speed_build (s_key, i_rows, i_trips, i_pings, d_seconds, s_detail)
                    VALUES (%s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE i_rows = VALUES(i_rows), i_trips = VALUES(i_trips),
                    i_pings = VALUES(i_pings), d_seconds = VALUES(d_seconds),
                    s_detail = VALUES(s_detail), dt_built = CURRENT_TIMESTAMP""",
            (key, rows, trips, pings, round(seconds, 2),
             json.dumps(detail) if detail else None),
        )
    conn.commit()


def refresh_speed_profile(conn, fences: list, progress=None) -> dict:
    """Rebuild gps_speed_profile: one row per (trip, zone, hour, exact km/h)."""
    t0 = time.time()
    zone = zone_expr(fences)
    gap = _gap_expr()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gps_speed_profile")
    conn.commit()

    chunks = _trip_chunks(conn)
    rows = 0
    for n, (lo, hi) in enumerate(chunks, 1):
        with conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO gps_speed_profile
                       (i_trip_no, s_zone, i_hour, i_kmph, i_pings, d_minutes, i_dist_m)
                SELECT p.i_trip_no, p.zone, p.hour, p.i_speed,
                       COUNT(*), ROUND(SUM(p.gap_min), 2), SUM(p.dist)
                  FROM (
                    SELECT gp.i_trip_no, gp.i_speed, {zone} AS zone,
                           HOUR(gp.dt_message) AS hour,
                           COALESCE(gp.i_dist, 0) AS dist,
                           {gap} AS gap_min
                      FROM tta_trip_gps gp
                     WHERE gp.i_trip_no BETWEEN %s AND %s
                       AND gp.d_lat IS NOT NULL AND gp.d_long IS NOT NULL
                  ) p
                 GROUP BY p.i_trip_no, p.zone, p.hour, p.i_speed
            """, (lo, hi))
            rows += cur.rowcount
        conn.commit()
        if progress and (n % 5 == 0 or n == len(chunks)):
            progress(n, len(chunks), rows)
        if n % 5 == 0 or n == len(chunks):
            logger.info("speed profile: chunk %d/%d, %d rows, %.0fs",
                        n, len(chunks), rows, time.time() - t0)

    with conn.cursor() as cur:
        cur.execute("""SELECT COUNT(DISTINCT i_trip_no) t, COALESCE(SUM(i_pings), 0) p
                         FROM gps_speed_profile""")
        agg = cur.fetchone()
    took = time.time() - t0
    _log_build(conn, "speed_profile", rows, int(agg["t"]), int(agg["p"]), took)
    logger.info("gps_speed_profile: %d rows over %d trips in %.1fs", rows, agg["t"], took)
    return {"rows": rows, "trips": int(agg["t"]), "pings": int(agg["p"]),
            "chunks": len(chunks), "seconds": round(took, 1)}


# Group one zone's over-floor pings into episodes.
#
# The scan is filtered to `i_speed > floor` first, so the surviving pings are
# already the speeding ones and the only thing that can end an episode is
# SILENCE: `brk` opens a new group whenever the previous over-floor ping is more
# than EVENT_BREAK_MIN old. A running SUM over `brk` then numbers the episodes.
#
# Note what this deliberately does NOT split on: a single compliant ping in the
# middle of a sustained run. Dipping to 38 for one ping inside a 12-minute run
# at 55 is one violation to act on, not two -- the same debounce reasoning the
# geofence module applies to fence crossings.
#
# `gap_min` here is the gap to the previous OVER-FLOOR ping rather than to the
# previous ping of any speed. Inside a run those are the same ping; they differ
# only for the first ping of an episode, which is credited 0 rather than the
# time it spent below the floor getting there. That under-counts by at most one
# ping interval per episode, which is the right direction for a number someone
# will be held to.
#
# The peak coordinate is picked with the MAX(CONCAT(...)) idiom -- lexical max
# over a zero-padded speed prefix -- because MySQL has no argmax aggregate.
# d_lat/d_long are fixed-width DECIMALs and India is entirely north-east of
# (0, 0), so no sign or width case can reorder the concatenation.
_EVENTS_SQL = """
    INSERT INTO gps_speed_events (i_trip_no, i_floor_kmph, s_zone, dt_start, dt_end,
                                  i_peak_kmph, d_avg_kmph, d_minutes, i_dist_m, i_pings,
                                  d_lat, d_long)
    SELECT i_trip_no, %(floor)s, %(zone)s, MIN(dt_message), MAX(dt_message),
           MAX(i_speed), ROUND(AVG(i_speed), 1), ROUND(SUM(gap_min), 2),
           SUM(dist), COUNT(*),
           SUBSTRING_INDEX(SUBSTRING_INDEX(
               MAX(CONCAT(LPAD(i_speed, 3, '0'), '|', d_lat, '|', d_long)), '|', -2), '|', 1),
           SUBSTRING_INDEX(
               MAX(CONCAT(LPAD(i_speed, 3, '0'), '|', d_lat, '|', d_long)), '|', -1)
    FROM (
        SELECT o.*, SUM(o.brk) OVER (PARTITION BY o.i_trip_no ORDER BY o.dt_message) AS grp
          FROM (
            SELECT f.*,
                   CASE WHEN f.prev_dt IS NULL
                          OR TIMESTAMPDIFF(SECOND, f.prev_dt, f.dt_message) / 60.0
                             > %(break_min)s
                        THEN 1 ELSE 0 END AS brk,
                   LEAST(COALESCE(
                       TIMESTAMPDIFF(SECOND, f.prev_dt, f.dt_message), 0) / 60.0,
                       %(gap_cap)s) AS gap_min
              FROM (
                SELECT z.i_trip_no, z.dt_message, z.i_speed, z.dist, z.d_lat, z.d_long,
                       LAG(z.dt_message) OVER (PARTITION BY z.i_trip_no
                                               ORDER BY z.dt_message) AS prev_dt
                  FROM (
                    SELECT gp.i_trip_no, gp.dt_message, gp.i_speed, gp.d_lat, gp.d_long,
                           COALESCE(gp.i_dist, 0) AS dist, {zone} AS zone
                      FROM tta_trip_gps gp
                     WHERE gp.i_speed > %(floor)s
                       AND gp.d_lat IS NOT NULL AND gp.d_long IS NOT NULL
                  ) z
                 WHERE z.zone = %(zone)s
              ) f
          ) o
    ) e
    GROUP BY i_trip_no, grp
"""


def refresh_speed_events(conn, fences: list) -> dict:
    """Rebuild gps_speed_events — one detection pass per zone, at its floor."""
    t0 = time.time()
    zone_sql = zone_expr(fences)
    per_zone: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gps_speed_events")
        conn.commit()
        for zone in ZONES:
            floor = EVENT_FLOORS[zone]
            cur.execute(_EVENTS_SQL.format(zone=zone_sql),
                        {"floor": floor, "zone": zone, "break_min": EVENT_BREAK_MIN,
                         "gap_cap": GAP_CAP_MIN})
            per_zone[f"{zone}>{floor}"] = cur.rowcount
            conn.commit()
            logger.info("speed events: %s -> %d episodes (%.0fs)",
                        zone, per_zone[f"{zone}>{floor}"], time.time() - t0)
    total = sum(per_zone.values())
    took = time.time() - t0
    _log_build(conn, "speed_events", total, 0, 0, took,
               {"floors": EVENT_FLOORS, "episodes": per_zone})
    logger.info("gps_speed_events: %d episodes in %.1fs", total, took)
    return {"rows": total, "floors": EVENT_FLOORS, "per_zone": per_zone,
            "seconds": round(took, 1)}


def refresh_all(conn, progress=None) -> dict:
    """Rebuild every speed/safety rollup. Safe to re-run; full replace."""
    fences = plant_fences(conn)
    if not fences:
        logger.warning(
            "No gazetteer plant fences found — every ping will be zoned 'road'. "
            "Seed them first (POST /geofence/seed)."
        )
    t0 = time.time()
    out = {
        "plant_fences": [f.key for f in fences],
        "speed_profile": refresh_speed_profile(conn, fences, progress),
        "speed_events": refresh_speed_events(conn, fences),
    }
    out["seconds"] = round(time.time() - t0, 1)
    out["status"] = "ok"
    logger.info("speed/safety rollups rebuilt in %.0fs", out["seconds"])
    return out


def build_status(conn) -> dict:
    """What is in the rollups and when it was built — so a screen reading these
    tables can state its own as-of time instead of implying it is live."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM gps_speed_build")
        rows = {r["s_key"]: r for r in cur.fetchall()}
        # The ping total comes from tta_trips.i_gps_ping_count, not from
        # COUNT(*) on tta_trip_gps: the count is a 3.5M-row scan costing ~3 s
        # on every call, and this figure sits on the Speed page's first paint.
        # The two agree exactly -- ingest maintains the column per trip and
        # refresh_gps_ping_counts reconciles it in the SAME job that rebuilds
        # these rollups, so the freshness figure below cannot drift from the
        # rollup it describes.
        cur.execute("SELECT COALESCE(SUM(i_gps_ping_count), 0) n FROM tta_trips")
        pings = int(cur.fetchone()["n"])
    built = [r["dt_built"] for r in rows.values() if r.get("dt_built")]
    covered = int(rows.get("speed_profile", {}).get("i_pings") or 0)
    return {
        "built_at": max(built).strftime("%Y-%m-%d %H:%M") if built else None,
        "built": bool(built),
        "rollups": {
            k: {
                "rows": int(v["i_rows"]), "trips": int(v["i_trips"]),
                "seconds": float(v["d_seconds"]),
                "built_at": v["dt_built"].strftime("%Y-%m-%d %H:%M") if v["dt_built"] else None,
                "detail": json.loads(v["s_detail"]) if v.get("s_detail") else None,
            }
            for k, v in rows.items()
        },
        "gps_pings": pings,
        "pings_covered": covered,
        # A rollup built before the last ETL run is stale, and the gap says by
        # how much rather than just "stale".
        "pings_behind": max(pings - covered, 0),
        "stale": pings > covered,
        "event_floors": EVENT_FLOORS,
    }
