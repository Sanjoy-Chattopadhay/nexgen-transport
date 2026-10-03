"""
Fleet-wide GPS network analytics (across ALL trips, or one consignor's trips).

Powers the "Network Analytics" page:
  - Waypoint intelligence (dwell leaders, per-waypoint stopped hours)
  - India density heatmap (0.01-deg grid of ping / stop density)
  - Waypoint x hour heatmap (WHEN each hot waypoint jams up)
  - State-wise distribution

SCALE: tta_trip_gps grows to billions of pings, so these are NOT computed live
per request. `refresh_network_aggregates(conn)` scans the GPS table on a schedule
and writes small pre-aggregated rollups (gps_waypoint_agg / gps_network_kpi /
gps_grid_agg / gps_waypoint_hour_agg / gps_state_agg). The read functions serve
those rows in O(rows-returned); they fall back to a live scan only when the
aggregate is empty (e.g. before the first refresh on a fresh deploy).

Consignor scoping follows the *_summary convention: cnr_id = 0 is the
"all consignors" rollup row; cnr_id > 0 is that consignor's slice. So a scoped
read is WHERE cnr_id = <id> and an unscoped read is WHERE cnr_id = 0 — no
query-time re-aggregation.

All dwell math uses gap-attribution (LAG window, capped at 15 min) so
"stopped hours" mean real wall-clock standstill, not ping counts.
"""

import logging

logger = logging.getLogger(__name__)

GAP_CAP_MIN = 15


def _scope_id(cnr_id):
    """None (unscoped) -> the rollup row (0); else the consignor's own rows."""
    return 0 if cnr_id is None else cnr_id


# ---------------------------------------------------------------------------
# READ PATH — serve pre-aggregated rows; fall back to a live scan if empty.
# ---------------------------------------------------------------------------

def fleet_waypoints(conn, limit: int = 25, cnr_id: int | None = None) -> dict:
    """Dwell leaders + headline KPIs (from gps_waypoint_agg / gps_network_kpi)."""
    cid = _scope_id(cnr_id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT waypoint, state, trips, vehicles, pings, stopped_hours, moving_hours
               FROM gps_waypoint_agg WHERE cnr_id = %s
               ORDER BY stopped_hours DESC LIMIT %s""",
            (cid, limit),
        )
        leaders = cur.fetchall()
        if not leaders:
            return _live_fleet_waypoints(conn, limit, cnr_id)
        cur.execute(
            "SELECT waypoints, states, trips, vehicles, pings FROM gps_network_kpi WHERE cnr_id = %s",
            (cid,),
        )
        kpis = cur.fetchone() or {}
    return {"kpis": kpis, "leaders": leaders}


def density_heatmap(conn, mode: str = "all", max_cells: int = 20000, cnr_id: int | None = None) -> dict:
    """India density grid from gps_grid_agg. mode: 'all' | 'stops'."""
    cid = _scope_id(cnr_id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT lat, lng, weight FROM gps_grid_agg
               WHERE cnr_id = %s AND mode = %s
               ORDER BY weight DESC LIMIT %s""",
            (cid, mode, max_cells),
        )
        cells = cur.fetchall()
    if not cells:
        return _live_density_heatmap(conn, mode, max_cells, cnr_id)
    max_w = max((c["weight"] for c in cells), default=1)
    return {
        "mode": mode,
        "cells": [[float(c["lat"]), float(c["lng"]), round(c["weight"] / max_w, 4)] for c in cells],
        "max_weight": max_w,
        "cell_count": len(cells),
    }


def waypoint_hour_heatmap(conn, top_n: int = 12, cnr_id: int | None = None) -> dict:
    """Top-N dwell waypoints x hour-of-day stopped minutes (gps_waypoint_hour_agg)."""
    cid = _scope_id(cnr_id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT waypoint FROM gps_waypoint_hour_agg WHERE cnr_id = %s
               GROUP BY waypoint ORDER BY SUM(stopped_min) DESC LIMIT %s""",
            (cid, top_n),
        )
        top = [r["waypoint"] for r in cur.fetchall()]
        if not top:
            return _live_waypoint_hour_heatmap(conn, top_n, cnr_id)
        placeholders = ",".join(["%s"] * len(top))
        cur.execute(
            f"""SELECT waypoint, hour, stopped_min FROM gps_waypoint_hour_agg
                WHERE cnr_id = %s AND waypoint IN ({placeholders})""",
            [cid] + top,
        )
        rows = cur.fetchall()
    by_wp = {w: [0] * 24 for w in top}
    for r in rows:
        by_wp[r["waypoint"]][int(r["hour"])] = int(r["stopped_min"])
    return {
        "waypoints": top,
        "matrix": [{"waypoint": w, "hours": by_wp[w], "total_min": sum(by_wp[w])} for w in top],
    }


def state_distribution(conn, cnr_id: int | None = None) -> list[dict]:
    """State-wise distribution (gps_state_agg)."""
    cid = _scope_id(cnr_id)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT state, trips, vehicles, pings, moving_hours, stopped_hours
               FROM gps_state_agg WHERE cnr_id = %s ORDER BY pings DESC""",
            (cid,),
        )
        rows = cur.fetchall()
    if not rows:
        return _live_state_distribution(conn, cnr_id)
    return rows


# ---------------------------------------------------------------------------
# REFRESH PATH — scan tta_trip_gps ONCE (scheduled) into the aggregate tables.
# ---------------------------------------------------------------------------

# Gap-attribution as a derived table (window fn) joined to tta_trips for cnr_id.
# A derived table (not a CTE) so it embeds cleanly inside INSERT ... SELECT.
_GAP_SUBQ = f"""(
    SELECT tt.i_cnr_id AS cnr_id, gp.s_wpnt1, gp.s_wpnt1_st_abbr, gp.is_moving,
           gp.i_trip_no, gp.s_asset_id, gp.dt_message,
           LEAST(COALESCE(TIMESTAMPDIFF(SECOND,
                LAG(gp.dt_message) OVER (PARTITION BY gp.i_trip_no ORDER BY gp.dt_message),
                gp.dt_message), 0) / 60.0, {GAP_CAP_MIN}) AS gap_min
    FROM tta_trip_gps gp JOIN tta_trips tt ON tt.i_trip_no = gp.i_trip_no
) g"""


def refresh_gps_ping_counts(conn) -> int:
    """NexGen: the fleet service keeps tta_trips.i_gps_ping_count exact as it
    stores fixes (trip.i_gps_ping_count), so there is nothing to reconcile here
    -- and tta_trips is a read-only view in this schema."""
    return {"status": "skipped", "reason": "kept by the fleet service"}
    # Smart-Truck's original body follows, unreachable:
    """Reconcile tta_trips.i_gps_ping_count from tta_trip_gps in one set-based
    pass. Ingest maintains it incrementally; this is a safety-net rebuild."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE tta_trips t
               LEFT JOIN (SELECT i_trip_no, COUNT(*) AS c
                          FROM tta_trip_gps GROUP BY i_trip_no) g
                 ON g.i_trip_no = t.i_trip_no
               SET t.i_gps_ping_count = COALESCE(g.c, 0)"""
        )
        n = cur.rowcount
    conn.commit()
    logger.info("Reconciled gps_ping_count on %d trips", n)
    return n


def refresh_network_aggregates(conn) -> dict:
    """Rebuild all Network-Analytics aggregate tables from tta_trip_gps.

    Per-consignor rows are computed directly from GPS; the cnr_id=0 rollup row is
    then folded from those per-consignor rows (trips/pings/hours are additive and
    exact; distinct 'vehicles' at the rollup is a slight over-count when a vehicle
    serves multiple consignors — acceptable for these leaderboards). The KPI
    rollup is computed directly for correct distinct counts.
    """
    out = {}
    with conn.cursor() as cur:
        # ---- 1. Dwell leaders (gps_waypoint_agg) ----
        cur.execute("DELETE FROM gps_waypoint_agg")
        cur.execute(f"""
            INSERT INTO gps_waypoint_agg (cnr_id, waypoint, state, trips, vehicles, pings, stopped_hours, moving_hours)
            SELECT cnr_id, s_wpnt1, MAX(s_wpnt1_st_abbr),
                   COUNT(DISTINCT i_trip_no), COUNT(DISTINCT s_asset_id), COUNT(*),
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END) / 60, 1),
                   ROUND(SUM(CASE WHEN is_moving = 1 THEN gap_min ELSE 0 END) / 60, 1)
            FROM {_GAP_SUBQ}
            WHERE s_wpnt1 IS NOT NULL
            GROUP BY cnr_id, s_wpnt1
        """)
        cur.execute("""
            INSERT INTO gps_waypoint_agg (cnr_id, waypoint, state, trips, vehicles, pings, stopped_hours, moving_hours)
            SELECT 0, waypoint, MAX(state), SUM(trips), SUM(vehicles), SUM(pings),
                   SUM(stopped_hours), SUM(moving_hours)
            FROM gps_waypoint_agg WHERE cnr_id <> 0 GROUP BY waypoint
        """)
        out["waypoint_agg"] = cur.rowcount

        # ---- 2. Headline KPIs (gps_network_kpi) ----
        cur.execute("DELETE FROM gps_network_kpi")
        cur.execute("""
            INSERT INTO gps_network_kpi (cnr_id, waypoints, states, trips, vehicles, pings)
            SELECT tt.i_cnr_id, COUNT(DISTINCT gp.s_wpnt1), COUNT(DISTINCT gp.s_wpnt1_st_abbr),
                   COUNT(DISTINCT gp.i_trip_no), COUNT(DISTINCT gp.s_asset_id), COUNT(*)
            FROM tta_trip_gps gp JOIN tta_trips tt ON tt.i_trip_no = gp.i_trip_no
            GROUP BY tt.i_cnr_id
        """)
        cur.execute("""
            INSERT INTO gps_network_kpi (cnr_id, waypoints, states, trips, vehicles, pings)
            SELECT 0, COUNT(DISTINCT s_wpnt1), COUNT(DISTINCT s_wpnt1_st_abbr),
                   COUNT(DISTINCT i_trip_no), COUNT(DISTINCT s_asset_id), COUNT(*)
            FROM tta_trip_gps
        """)

        # ---- 3. Density grid (gps_grid_agg) — mode 'all' + 'stops' ----
        cur.execute("DELETE FROM gps_grid_agg")
        for mode, where in (("all", ""), ("stops", "AND gp.is_moving = 0")):
            cur.execute(f"""
                INSERT INTO gps_grid_agg (cnr_id, mode, lat, lng, weight)
                SELECT tt.i_cnr_id, '{mode}', ROUND(gp.d_lat, 2), ROUND(gp.d_long, 2), COUNT(*)
                FROM tta_trip_gps gp JOIN tta_trips tt ON tt.i_trip_no = gp.i_trip_no
                WHERE gp.d_lat IS NOT NULL {where}
                GROUP BY tt.i_cnr_id, ROUND(gp.d_lat, 2), ROUND(gp.d_long, 2)
            """)
        cur.execute("""
            INSERT INTO gps_grid_agg (cnr_id, mode, lat, lng, weight)
            SELECT 0, mode, lat, lng, SUM(weight)
            FROM gps_grid_agg WHERE cnr_id <> 0 GROUP BY mode, lat, lng
        """)
        out["grid_agg"] = cur.rowcount

        # ---- 4. Waypoint x hour (gps_waypoint_hour_agg) ----
        cur.execute("DELETE FROM gps_waypoint_hour_agg")
        cur.execute(f"""
            INSERT INTO gps_waypoint_hour_agg (cnr_id, waypoint, hour, stopped_min)
            SELECT cnr_id, s_wpnt1, HOUR(dt_message),
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END), 0)
            FROM {_GAP_SUBQ}
            WHERE s_wpnt1 IS NOT NULL
            GROUP BY cnr_id, s_wpnt1, HOUR(dt_message)
        """)
        cur.execute("""
            INSERT INTO gps_waypoint_hour_agg (cnr_id, waypoint, hour, stopped_min)
            SELECT 0, waypoint, hour, SUM(stopped_min)
            FROM gps_waypoint_hour_agg WHERE cnr_id <> 0 GROUP BY waypoint, hour
        """)

        # ---- 5. State distribution (gps_state_agg) ----
        cur.execute("DELETE FROM gps_state_agg")
        cur.execute(f"""
            INSERT INTO gps_state_agg (cnr_id, state, trips, vehicles, pings, moving_hours, stopped_hours)
            SELECT cnr_id, s_wpnt1_st_abbr,
                   COUNT(DISTINCT i_trip_no), COUNT(DISTINCT s_asset_id), COUNT(*),
                   ROUND(SUM(CASE WHEN is_moving = 1 THEN gap_min ELSE 0 END) / 60, 1),
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END) / 60, 1)
            FROM {_GAP_SUBQ}
            WHERE s_wpnt1_st_abbr IS NOT NULL
            GROUP BY cnr_id, s_wpnt1_st_abbr
        """)
        cur.execute("""
            INSERT INTO gps_state_agg (cnr_id, state, trips, vehicles, pings, moving_hours, stopped_hours)
            SELECT 0, state, SUM(trips), SUM(vehicles), SUM(pings), SUM(moving_hours), SUM(stopped_hours)
            FROM gps_state_agg WHERE cnr_id <> 0 GROUP BY state
        """)
    conn.commit()
    logger.info("Refreshed network aggregates: %s", out)
    return {"status": "ok", **out}


# ---------------------------------------------------------------------------
# LIVE FALLBACK — the original per-request scans, used only when an aggregate
# is empty (pre-first-refresh). Consignor-scoped via a JOIN to tta_trips.
# ---------------------------------------------------------------------------

def _scoped_source(cnr_id: int | None) -> tuple[str, list]:
    if cnr_id is None:
        return "tta_trip_gps gp", []
    return ("tta_trip_gps gp JOIN tta_trips tt ON tt.i_trip_no = gp.i_trip_no "
            "AND tt.i_cnr_id = %s"), [cnr_id]


def _gap_cte(cnr_id: int | None) -> tuple[str, list]:
    source, params = _scoped_source(cnr_id)
    cte = f"""
    WITH g AS (
        SELECT gp.s_wpnt1, gp.s_wpnt1_st_abbr, gp.is_moving, gp.i_trip_no, gp.s_asset_id,
               gp.dt_message, gp.d_lat, gp.d_long,
               LEAST(COALESCE(TIMESTAMPDIFF(SECOND,
                    LAG(gp.dt_message) OVER (PARTITION BY gp.i_trip_no ORDER BY gp.dt_message),
                    gp.dt_message), 0) / 60.0, {GAP_CAP_MIN}) AS gap_min
        FROM {source}
    )
    """
    return cte, params


def _live_fleet_waypoints(conn, limit: int = 25, cnr_id: int | None = None) -> dict:
    cte, cte_p = _gap_cte(cnr_id)
    source, src_p = _scoped_source(cnr_id)
    with conn.cursor() as cur:
        cur.execute(cte + """
            SELECT s_wpnt1 AS waypoint, MAX(s_wpnt1_st_abbr) AS state,
                   COUNT(DISTINCT i_trip_no) AS trips, COUNT(DISTINCT s_asset_id) AS vehicles,
                   COUNT(*) AS pings,
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END) / 60, 1) AS stopped_hours,
                   ROUND(SUM(CASE WHEN is_moving = 1 THEN gap_min ELSE 0 END) / 60, 1) AS moving_hours
            FROM g WHERE s_wpnt1 IS NOT NULL
            GROUP BY s_wpnt1 ORDER BY stopped_hours DESC LIMIT %s
        """, cte_p + [limit])
        leaders = cur.fetchall()
        cur.execute(f"""
            SELECT COUNT(DISTINCT gp.s_wpnt1) AS waypoints, COUNT(DISTINCT gp.s_wpnt1_st_abbr) AS states,
                   COUNT(DISTINCT gp.i_trip_no) AS trips, COUNT(DISTINCT gp.s_asset_id) AS vehicles,
                   COUNT(*) AS pings
            FROM {source}
        """, src_p)
        kpis = cur.fetchone()
    return {"kpis": kpis, "leaders": leaders}


def _live_density_heatmap(conn, mode: str = "all", max_cells: int = 20000, cnr_id: int | None = None) -> dict:
    source, src_p = _scoped_source(cnr_id)
    where = "WHERE gp.is_moving = 0" if mode == "stops" else ""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT ROUND(gp.d_lat, 2) AS lat, ROUND(gp.d_long, 2) AS lng, COUNT(*) AS weight
            FROM {source} {where}
            GROUP BY ROUND(gp.d_lat, 2), ROUND(gp.d_long, 2)
            ORDER BY weight DESC LIMIT %s
        """, src_p + [max_cells])
        cells = cur.fetchall()
    max_w = max((c["weight"] for c in cells), default=1)
    return {
        "mode": mode,
        "cells": [[float(c["lat"]), float(c["lng"]), round(c["weight"] / max_w, 4)] for c in cells],
        "max_weight": max_w,
        "cell_count": len(cells),
    }


def _live_waypoint_hour_heatmap(conn, top_n: int = 12, cnr_id: int | None = None) -> dict:
    cte, cte_p = _gap_cte(cnr_id)
    with conn.cursor() as cur:
        cur.execute(cte + """
            SELECT s_wpnt1 AS waypoint FROM g
            WHERE s_wpnt1 IS NOT NULL AND is_moving = 0
            GROUP BY s_wpnt1 ORDER BY SUM(gap_min) DESC LIMIT %s
        """, cte_p + [top_n])
        top = [r["waypoint"] for r in cur.fetchall()]
        if not top:
            return {"waypoints": [], "matrix": []}
        placeholders = ",".join(["%s"] * len(top))
        cur.execute(cte + f"""
            SELECT s_wpnt1 AS waypoint, HOUR(dt_message) AS hour,
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END), 0) AS stopped_min
            FROM g WHERE s_wpnt1 IN ({placeholders})
            GROUP BY s_wpnt1, HOUR(dt_message)
        """, cte_p + top)
        rows = cur.fetchall()
    by_wp = {w: [0] * 24 for w in top}
    for r in rows:
        by_wp[r["waypoint"]][int(r["hour"])] = int(r["stopped_min"])
    return {
        "waypoints": top,
        "matrix": [{"waypoint": w, "hours": by_wp[w], "total_min": sum(by_wp[w])} for w in top],
    }


def _live_state_distribution(conn, cnr_id: int | None = None) -> list[dict]:
    cte, cte_p = _gap_cte(cnr_id)
    with conn.cursor() as cur:
        cur.execute(cte + """
            SELECT s_wpnt1_st_abbr AS state, COUNT(DISTINCT i_trip_no) AS trips,
                   COUNT(DISTINCT s_asset_id) AS vehicles, COUNT(*) AS pings,
                   ROUND(SUM(CASE WHEN is_moving = 1 THEN gap_min ELSE 0 END) / 60, 1) AS moving_hours,
                   ROUND(SUM(CASE WHEN is_moving = 0 THEN gap_min ELSE 0 END) / 60, 1) AS stopped_hours
            FROM g WHERE s_wpnt1_st_abbr IS NOT NULL
            GROUP BY s_wpnt1_st_abbr ORDER BY pings DESC
        """, cte_p)
        return cur.fetchall()
