"""
Waypoint Registry — persistent, pattern-storing waypoint intelligence.

Every waypoint that ever appears in the GPS feed is auto-discovered and
stored in `tta_waypoints`; its behaviour pattern (dwell, stop events,
hour-of-day / day-of-week profiles) accumulates in `tta_waypoint_stats`.

Because the pattern is STORED (not recomputed per page view), any other
analysis — route scoring, driver scoring, ETA models, detention league —
can simply JOIN these tables.

refresh_waypoints(conn) is idempotent (full rebuild from tta_trip_gps)
and is called automatically after every TTA ingest. At larger volumes it
becomes a nightly job / incremental update (see docs/SYSTEM_DESIGN.md).
"""

import json
import logging

logger = logging.getLogger(__name__)

GAP_CAP_MIN = 15
STOP_MIN_MINUTES = 5.0

_GAP_CTE = f"""
    WITH g AS (
        SELECT s_wpnt1, s_wpnt1_st_abbr, is_moving, i_trip_no, s_asset_id,
               dt_message, d_lat, d_long,
               LEAST(COALESCE(TIMESTAMPDIFF(SECOND,
                    LAG(dt_message) OVER (PARTITION BY i_trip_no ORDER BY dt_message),
                    dt_message), 0) / 60.0, {GAP_CAP_MIN}) AS gap_min
        FROM tta_trip_gps
    )
"""


# ============================================
# STOP-EVENT DETECTION (python, per trip)
# ============================================

def _stop_events_by_waypoint(conn) -> dict[str, list[float]]:
    """Detect standstill events (>= 5 min) across all trips and attribute
    each to its dominant waypoint. Returns {waypoint: [minutes, ...]}."""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT i_trip_no FROM tta_trip_gps")
        trip_nos = [r["i_trip_no"] for r in cur.fetchall()]

    out: dict[str, list[float]] = {}
    for trip_no in trip_nos:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT dt_message, is_moving, s_wpnt1
                   FROM tta_trip_gps WHERE i_trip_no = %s ORDER BY dt_message""",
                (trip_no,),
            )
            rows = cur.fetchall()
        block: list = []
        for r in rows + [None]:
            if r is not None and not r["is_moving"]:
                block.append(r)
                continue
            if block:
                mins = (block[-1]["dt_message"] - block[0]["dt_message"]).total_seconds() / 60
                if mins >= STOP_MIN_MINUTES:
                    wpnts = [b["s_wpnt1"] for b in block if b["s_wpnt1"]]
                    near = max(set(wpnts), key=wpnts.count) if wpnts else None
                    if near:
                        out.setdefault(near, []).append(round(mins, 1))
                block = []
    return out


# ============================================
# REFRESH (idempotent full rebuild)
# ============================================

def refresh_waypoints(conn) -> dict:
    """Discover new waypoints + rebuild their accumulated pattern stats."""
    with conn.cursor() as cur:
        # 1. discover / update the dimension (new waypoints auto-register)
        cur.execute("""
            INSERT INTO tta_waypoints (s_wpnt, s_state, d_lat, d_long, first_seen, last_seen)
            SELECT s_wpnt1, MAX(s_wpnt1_st_abbr), AVG(d_lat), AVG(d_long),
                   MIN(dt_message), MAX(dt_message)
            FROM tta_trip_gps
            WHERE s_wpnt1 IS NOT NULL
            GROUP BY s_wpnt1
            ON DUPLICATE KEY UPDATE
                s_state = VALUES(s_state),
                d_lat = VALUES(d_lat), d_long = VALUES(d_long),
                first_seen = LEAST(first_seen, VALUES(first_seen)),
                last_seen = GREATEST(last_seen, VALUES(last_seen))
        """)
        discovered = cur.rowcount

        cur.execute("SELECT id, s_wpnt FROM tta_waypoints")
        wp_ids = {r["s_wpnt"]: r["id"] for r in cur.fetchall()}

        # 2. time / ping aggregates
        cur.execute(_GAP_CTE + """
            SELECT s_wpnt1 AS wpnt,
                   COUNT(DISTINCT i_trip_no) AS trips,
                   COUNT(DISTINCT s_asset_id) AS vehicles,
                   COUNT(*) AS pings,
                   SUM(is_moving = 0) AS stopped_pings,
                   ROUND(SUM(CASE WHEN is_moving = 1 THEN gap_min ELSE 0 END), 1) AS moving_min,
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END), 1) AS stopped_min
            FROM g WHERE s_wpnt1 IS NOT NULL GROUP BY s_wpnt1
        """)
        aggregates = {r["wpnt"]: r for r in cur.fetchall()}

        # 3. hour-of-day profile (stopped minutes)
        cur.execute(_GAP_CTE + """
            SELECT s_wpnt1 AS wpnt, HOUR(dt_message) AS h,
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END), 0) AS m
            FROM g WHERE s_wpnt1 IS NOT NULL GROUP BY s_wpnt1, HOUR(dt_message)
        """)
        hour_prof: dict[str, list] = {}
        for r in cur.fetchall():
            hour_prof.setdefault(r["wpnt"], [0] * 24)[int(r["h"])] = float(r["m"])

        # 4. day-of-week profile (0 = Monday)
        cur.execute(_GAP_CTE + """
            SELECT s_wpnt1 AS wpnt, WEEKDAY(dt_message) AS d,
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END), 0) AS m
            FROM g WHERE s_wpnt1 IS NOT NULL GROUP BY s_wpnt1, WEEKDAY(dt_message)
        """)
        dow_prof: dict[str, list] = {}
        for r in cur.fetchall():
            dow_prof.setdefault(r["wpnt"], [0] * 7)[int(r["d"])] = float(r["m"])

    # 5. stop events (python pass)
    stop_events = _stop_events_by_waypoint(conn)

    # 6. write stats
    written = 0
    with conn.cursor() as cur:
        for wpnt, agg in aggregates.items():
            wid = wp_ids.get(wpnt)
            if wid is None:
                continue
            events = stop_events.get(wpnt, [])
            cur.execute(
                """INSERT INTO tta_waypoint_stats
                   (waypoint_id, total_trips, total_vehicles, total_pings, stopped_pings,
                    moving_min, stopped_min, stop_events, avg_stop_min, longest_stop_min,
                    hour_profile, dow_profile)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE
                    total_trips=VALUES(total_trips), total_vehicles=VALUES(total_vehicles),
                    total_pings=VALUES(total_pings), stopped_pings=VALUES(stopped_pings),
                    moving_min=VALUES(moving_min), stopped_min=VALUES(stopped_min),
                    stop_events=VALUES(stop_events), avg_stop_min=VALUES(avg_stop_min),
                    longest_stop_min=VALUES(longest_stop_min),
                    hour_profile=VALUES(hour_profile), dow_profile=VALUES(dow_profile)""",
                (wid, agg["trips"], agg["vehicles"], agg["pings"], agg["stopped_pings"],
                 agg["moving_min"], agg["stopped_min"],
                 len(events),
                 round(sum(events) / len(events), 1) if events else None,
                 max(events) if events else None,
                 json.dumps(hour_prof.get(wpnt, [0] * 24)),
                 json.dumps(dow_prof.get(wpnt, [0] * 7))),
            )
            written += 1
    conn.commit()
    logger.info("Waypoint registry refreshed: %d waypoints", written)
    return {"status": "ok", "waypoints": written, "rows_touched_in_dim": discovered}


# ============================================
# READS
# ============================================

def list_waypoints(conn, search: str = "", sort: str = "stopped_min",
                   page: int = 1, page_size: int = 25) -> dict:
    sort_cols = {"stopped_min": "s.stopped_min", "stop_events": "s.stop_events",
                 "longest_stop_min": "s.longest_stop_min", "total_trips": "s.total_trips",
                 "total_pings": "s.total_pings", "last_seen": "w.last_seen",
                 "s_wpnt": "w.s_wpnt"}
    order = sort_cols.get(sort, "s.stopped_min")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)

    where, params = "", []
    if search:
        where = "WHERE (w.s_wpnt LIKE %s OR w.s_state LIKE %s)"
        params = [f"%{search}%"] * 2

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM tta_waypoints w {where}", params)
        total = cur.fetchone()["total"]
        cur.execute(f"""
            SELECT w.id, w.s_wpnt, w.s_state, w.d_lat, w.d_long,
                   w.first_seen, w.last_seen,
                   s.total_trips, s.total_vehicles, s.total_pings, s.stopped_pings,
                   s.moving_min, s.stopped_min, s.stop_events,
                   s.avg_stop_min, s.longest_stop_min
            FROM tta_waypoints w
            LEFT JOIN tta_waypoint_stats s ON s.waypoint_id = w.id
            {where}
            ORDER BY {order} DESC
            LIMIT %s OFFSET %s
        """, params + [page_size, (page - 1) * page_size])
        items = cur.fetchall()

        cur.execute("""
            SELECT COUNT(*) AS waypoints,
                   SUM(s.stopped_min) AS total_stopped_min,
                   SUM(s.stop_events) AS total_stop_events,
                   MAX(s.longest_stop_min) AS longest_stop_min
            FROM tta_waypoints w LEFT JOIN tta_waypoint_stats s ON s.waypoint_id = w.id
        """)
        kpis = cur.fetchone()
        cur.execute("""
            SELECT w.s_wpnt FROM tta_waypoints w
            JOIN tta_waypoint_stats s ON s.waypoint_id = w.id
            ORDER BY s.stopped_min DESC LIMIT 1
        """)
        top = cur.fetchone()
        kpis["worst_waypoint"] = top["s_wpnt"] if top else None

    return {"items": items, "total": total, "page": page, "page_size": page_size, "kpis": kpis}


def waypoint_detail(conn, waypoint_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT w.*, s.total_trips, s.total_vehicles, s.total_pings, s.stopped_pings,
                   s.moving_min, s.stopped_min, s.stop_events, s.avg_stop_min,
                   s.longest_stop_min, s.hour_profile, s.dow_profile, s.last_refreshed
            FROM tta_waypoints w
            LEFT JOIN tta_waypoint_stats s ON s.waypoint_id = w.id
            WHERE w.id = %s
        """, (waypoint_id,))
        wp = cur.fetchone()
        if not wp:
            return None
        for k in ("hour_profile", "dow_profile"):
            if wp.get(k):
                try:
                    wp[k] = json.loads(wp[k])
                except Exception:
                    wp[k] = None

        # Per-trip visits at this waypoint.
        #
        # The window function is scoped to the trips that actually visit this
        # waypoint, NOT run over the whole ping table. `_GAP_CTE` has no WHERE of
        # its own, so the previous version made MySQL compute a LAG across all
        # ~3.6M pings and only then filtered to one waypoint -- every click on a
        # registry row queued a full-table window scan, which never returned
        # inside the request timeout. That is why the detail report appeared to
        # do nothing.
        #
        # The trip filter has to sit INSIDE the CTE rather than replacing the
        # `s_wpnt1` filter after it: a ping's gap is the time since the previous
        # ping OF THAT TRIP, wherever it was. Filtering the CTE down to
        # `s_wpnt1 = X` would measure the gap since the trip was last AT this
        # waypoint, silently attributing time spent elsewhere to it.
        #
        # `picked` narrows it further to the 50 trips this report actually shows.
        # The busiest waypoint is visited by 400+ trips and windowing all of them
        # to display 50 cost 66 s; picking first costs 4 s.
        cur.execute("""
            WITH picked AS (
                SELECT i_trip_no, MIN(dt_message) AS t0
                  FROM tta_trip_gps
                 WHERE s_wpnt1 = %s
                 GROUP BY i_trip_no
                 ORDER BY t0 DESC
                 LIMIT 50
            ),
            g AS (
                SELECT s_wpnt1, is_moving, i_trip_no, s_asset_id, dt_message,
                       LEAST(COALESCE(TIMESTAMPDIFF(SECOND,
                            LAG(dt_message) OVER (PARTITION BY i_trip_no ORDER BY dt_message),
                            dt_message), 0) / 60.0, %s) AS gap_min
                FROM tta_trip_gps
                WHERE i_trip_no IN (SELECT i_trip_no FROM picked)
            )
            SELECT g.i_trip_no, MAX(g.s_asset_id) AS vehicle,
                   MIN(g.dt_message) AS first_ping, MAX(g.dt_message) AS last_ping,
                   COUNT(*) AS pings,
                   ROUND(SUM(CASE WHEN g.is_moving = 0 THEN g.gap_min ELSE 0 END), 0) AS stopped_min,
                   ROUND(SUM(CASE WHEN g.is_moving = 1 THEN g.gap_min ELSE 0 END), 0) AS moving_min
            FROM g
            WHERE g.s_wpnt1 = %s
            GROUP BY g.i_trip_no
            ORDER BY MIN(g.dt_message) DESC
            LIMIT 50
        """, (wp["s_wpnt"], GAP_CAP_MIN, wp["s_wpnt"]))
        visits = cur.fetchall()

        # join trip context (route, driver, consignor)
        trip_ctx = {}
        if visits:
            ph = ",".join(["%s"] * len(visits))
            cur.execute(f"""
                SELECT i_trip_no, s_org_node_name, s_dest_node_name, s_driver_name, s_cnr_name
                FROM tta_trips WHERE i_trip_no IN ({ph})
            """, [v["i_trip_no"] for v in visits])
            trip_ctx = {r["i_trip_no"]: r for r in cur.fetchall()}
        for v in visits:
            ctx = trip_ctx.get(v["i_trip_no"], {})
            v["route"] = f"{ctx.get('s_org_node_name', '?')} → {ctx.get('s_dest_node_name', '?')}"
            v["driver"] = ctx.get("s_driver_name")
            v["consignor"] = ctx.get("s_cnr_name")

    return {"waypoint": wp, "visits": visits}
