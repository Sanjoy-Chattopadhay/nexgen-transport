"""Stops: where trucks stood still, and which of those places no fence covers.

A stop inside a facility fence is a visit seen from the other side. A stop
outside every facility fence is either an unscheduled halt or a place the
master is missing -- and when many trucks stop at the same spot, it is almost
certainly the second. The hotspot list is the evidence for proposing a new
fence: it clusters outside-stops on a ~200 m grid and ranks by how many
distinct vehicles stood there, which a single truck's long halt cannot fake.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from nexgen.shared.geoengine.api.v1.common import day_bounds, order_clause, paged, resolve_run, rows
from nexgen.shared.geoengine.db import get_geo_db

router = APIRouter(tags=["stops"])

SORT = {"start": "s.dt_start", "duration": "s.i_duration_s", "vehicle": "s.s_asset_id"}
GRID_DEG = 0.002   # ~220 m of latitude


@router.get("/stops")
def list_stops(outside_only: bool = True, min_minutes: int = Query(15, ge=0),
               q: str | None = None, site_id: int | None = None,
               date_from: str | None = Query(None, alias="from"),
               date_to: str | None = Query(None, alias="to"),
               sort: str = "duration", order: str = "desc",
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
               run: int | None = None, conn=Depends(get_geo_db)):
    a, b = day_bounds(date_from, date_to)
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        where, params = ["s.i_run_id=%s", "s.i_duration_s >= %s"], [rid, min_minutes * 60]
        if outside_only:
            where.append("s.i_fence_id IS NULL")
        if site_id:
            where.append("s.i_site_id = %s")
            params.append(site_id)
        if q:
            where.append("(s.s_asset_id LIKE %s OR s.s_trans_name LIKE %s)")
            params += [f"%{q}%", f"%{q}%"]
        if a:
            where.append("s.dt_start >= %s")
            params.append(a)
        if b:
            where.append("s.dt_start < %s")
            params.append(b)
        # The physical ledger: a standstill shared by consignment trips once.
        base = f"""FROM geo_pstop s LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
                   WHERE {' AND '.join(where)}"""
        cur.execute(f"SELECT COUNT(*) n, SUM(s.i_duration_s) dur, COUNT(DISTINCT s.s_asset_id) veh {base}",
                    params)
        tot = cur.fetchone()
        cur.execute(f"""SELECT s.i_trip_no, s.i_trips, s.s_trips, s.s_asset_id, s.dt_start, s.dt_end, s.i_duration_s,
                               s.i_pings, s.d_lat, s.d_long, s.d_p90_spread_m, s.i_site_id, s.s_site_name,
                               s.s_scale, s.s_trans_name, m.s_driver_name, s.s_origin, s.s_destination
                          {base} {order_clause(sort, order, SORT, 'duration')}
                         LIMIT %s OFFSET %s""", (*params, page_size, (page - 1) * page_size))
        items = rows(cur)
        cur.execute(f"""SELECT ROUND(s.d_lat / {GRID_DEG}) gy, ROUND(s.d_long / {GRID_DEG}) gx,
                               AVG(s.d_lat) lat, AVG(s.d_long) lon, COUNT(*) stops,
                               COUNT(DISTINCT s.s_asset_id) vehicles, SUM(s.i_duration_s) duration_s,
                               COUNT(DISTINCT s.s_trans_name) transporters
                          {base}
                         GROUP BY gy, gx
                        HAVING vehicles >= 2
                         ORDER BY vehicles DESC, duration_s DESC
                         LIMIT 60""", params)
        hotspots = rows(cur)
        # The list above is the busiest 60; the headline counts them all.
        cur.execute(f"""SELECT COUNT(*) n FROM (
                           SELECT 1 {base} GROUP BY ROUND(s.d_lat / {GRID_DEG}), ROUND(s.d_long / {GRID_DEG})
                           HAVING COUNT(DISTINCT s.s_asset_id) >= 2) h""", params)
        hotspots_total = int(cur.fetchone()["n"] or 0)
    return {"run_id": rid, "stops_total": int(tot["n"] or 0),
            "duration_total_s": int(tot["dur"] or 0), "vehicles": int(tot["veh"] or 0),
            "hotspots_total": hotspots_total, "hotspots": hotspots,
            **paged(tot["n"], page, page_size, items)}
