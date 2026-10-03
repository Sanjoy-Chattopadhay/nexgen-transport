"""Routes: every loaded trip against its planned route, and what it cost.

Reads geo_trip_route and geo_route_deviation (geofencing/routing). The money
is at the configured rates; the totals come with the kilometre and hour sums
behind them, so the page can re-price them at any other rates exactly -- the
variance is linear in the rates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from nexgen.shared.geoengine.api.v1.common import clean, day_bounds, order_clause, paged, resolve_run, rows
from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.osrm import polyline as pl

router = APIRouter(tags=["routes and cost"])

UNKNOWN = "(no trip record)"

SORT = {"variance": "r.d_variance", "extra": "r.d_extra_km", "extra_pct": "r.d_extra_pct",
        "deviations": "r.i_deviations", "offroute": "r.d_offroute_km", "adherence": "r.d_adherence_pct",
        "planned": "r.d_planned_km", "actual": "r.d_actual_km", "start": "r.dt_transit_from",
        "trip": "r.i_trip_no", "detention": "r.d_detention_h", "margin": "r.d_margin"}


def _where(rid: int, q, transporter, vehicle, verdict, from_site, to_site, date_from, date_to, status="ok"):
    where, params = ["r.i_run_id=%s"], [rid]
    if status:
        where.append("r.s_status=%s")
        params.append(status)
    if q:
        if q.isdigit():
            where.append("r.i_trip_no=%s")
            params.append(int(q))
        else:
            where.append("(r.s_asset_id LIKE %s OR r.s_trans_name LIKE %s OR r.s_from_site LIKE %s OR r.s_to_site LIKE %s)")
            params += [f"%{q}%"] * 4
    if transporter:
        if transporter == UNKNOWN:
            where.append("r.s_trans_name IS NULL")
        else:
            where.append("r.s_trans_name=%s")
            params.append(transporter)
    for col, val in (("r.s_asset_id", vehicle), ("r.s_verdict", verdict), ("r.i_from_site", from_site),
                     ("r.i_to_site", to_site)):
        if val not in (None, ""):
            where.append(f"{col}=%s")
            params.append(val)
    a, b = day_bounds(date_from, date_to)
    if a:
        where.append("r.dt_transit_from >= %s")
        params.append(a)
    if b:
        where.append("r.dt_transit_from < %s")
        params.append(b)
    return where, params


def _settings() -> dict:
    r = settings.routing
    return {"mode": "osrm" if settings.osrm.enabled else "learned", "osrm_url_set": settings.osrm.enabled,
            "rates": {"per_km": r.cost_per_km, "per_hour": r.cost_per_hour,
                      "detention_per_hour": r.detention_per_hour, "free_hours": r.free_hours,
                      "revenue_per_km": r.revenue_per_km, "tolerance_pct": r.tolerance_pct},
            "planning": {"truck_time_factor": r.truck_time_factor, "rest_min_per_4h": r.rest_min_per_4h,
                         "min_plan_km": r.min_plan_km},
            "thresholds": {"osrm": {"off_m": r.off_m, "on_m": r.on_m, "confirm_s": r.confirm_s,
                                    "escape_m": r.escape_m, "terminal_m": r.terminal_m},
                           "learned": {"off_m": r.learned_off_m, "on_m": r.learned_on_m,
                                       "terminal_m": r.learned_terminal_m},
                           "long_stop_s": r.long_stop_s}}


@router.get("/routes/settings")
def route_settings():
    return _settings()


@router.get("/routes")
def list_routes(q: str | None = None, transporter: str | None = None, vehicle: str | None = None,
                verdict: str | None = None, from_site: int | None = None, to_site: int | None = None,
                date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                sort: str = "variance", order: str = "asc",
                page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
                run: int | None = None, conn=Depends(get_geo_db)):
    """Trips against plan, worst first, with fleet totals."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        where, params = _where(rid, q, transporter, vehicle, verdict, from_site, to_site, date_from, date_to)
        base = f"FROM geo_trip_route r WHERE {' AND '.join(where)}"
        cur.execute(f"""SELECT COUNT(*) trips, ROUND(SUM(r.d_planned_km), 1) planned_km,
                               ROUND(SUM(r.d_actual_km), 1) actual_km, ROUND(SUM(r.d_extra_km), 1) extra_km,
                               ROUND(SUM(GREATEST(r.d_extra_km, 0)), 1) extra_km_over,
                               SUM(r.i_planned_transit_s) planned_s, SUM(r.i_actual_transit_s) actual_s,
                               ROUND(SUM(r.d_offroute_km), 1) offroute_km, SUM(r.i_deviations) deviations,
                               SUM(r.i_detours) detours, SUM(r.i_offroute_stops) offroute_stops,
                               SUM(r.i_reroutes) reroutes, ROUND(AVG(r.d_adherence_pct), 1) adherence_pct,
                               SUM(r.d_cost_plan) cost_plan, SUM(r.d_cost_actual) cost_actual,
                               SUM(r.d_variance) variance, SUM(r.d_variance_km) variance_km,
                               SUM(r.d_variance_time) variance_time,
                               SUM(CASE WHEN r.s_verdict='loss' THEN r.d_variance END) loss,
                               SUM(CASE WHEN r.s_verdict='saving' THEN r.d_variance END) saving,
                               SUM(r.s_verdict='loss') loss_trips, SUM(r.s_verdict='saving') saving_trips,
                               SUM(r.s_verdict='on_plan') on_plan_trips,
                               ROUND(SUM(r.d_detention_h), 1) detention_h, SUM(r.d_detention_cost) detention_cost,
                               SUM(r.d_revenue) revenue, SUM(r.d_margin) margin,
                               COUNT(DISTINCT r.s_asset_id) vehicles
                          {base}""", params)
        totals = clean(cur.fetchone())
        cur.execute(f"""SELECT r.*, m.s_driver_name, m.s_origin, m.s_destination
                          FROM geo_trip_route r LEFT JOIN geo_trip_meta m ON m.i_trip_no = r.i_trip_no
                         WHERE {' AND '.join(where)}
                         {order_clause(sort, order, SORT, 'variance')}, r.i_trip_no
                         LIMIT %s OFFSET %s""", (*params, page_size, (page - 1) * page_size))
        items = rows(cur)
        cur.execute("""SELECT s_status, COUNT(*) n FROM geo_trip_route WHERE i_run_id=%s GROUP BY s_status""", (rid,))
        status = {r["s_status"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute(f"""SELECT d.s_kind, COUNT(*) n, ROUND(SUM(d.d_extra_km), 1) extra_km,
                               SUM(d.i_duration_s) duration_s
                          FROM geo_route_deviation d JOIN geo_trip_route r
                            ON r.i_run_id = d.i_run_id AND r.i_trip_no = d.i_trip_no
                         WHERE {' AND '.join(where)} GROUP BY d.s_kind ORDER BY n DESC""", params)
        kinds = rows(cur)
    return {"run_id": rid, "totals": totals, "status": status, "deviation_kinds": kinds,
            "settings": _settings(), **paged(totals["trips"], page, page_size, items)}


@router.get("/routes/lanes")
def route_lanes(q: str | None = None, transporter: str | None = None,
                date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                sort: str = "loss", order: str = "asc",
                page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
                run: int | None = None, conn=Depends(get_geo_db)):
    """Lanes by their loading and unloading places: how far trucks keep to
    the lane's route, and what the deviations cost."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        where, params = _where(rid, q, transporter, None, None, None, None, date_from, date_to)
        lane_sort = {"trips": "trips", "loss": "variance", "extra": "extra_km", "deviations": "deviations",
                     "adherence": "adherence_pct", "planned": "planned_km"}
        base = f"""FROM geo_trip_route r LEFT JOIN geo_route_plan p ON p.i_plan_id = r.i_plan_id
                   WHERE {' AND '.join(where)}"""
        cur.execute(f"SELECT COUNT(*) n FROM (SELECT 1 {base} GROUP BY r.i_from_site, r.i_to_site) x", params)
        total = int(cur.fetchone()["n"] or 0)
        cur.execute(f"""SELECT r.i_from_site, ANY_VALUE(r.s_from_site) s_from_site, r.i_to_site,
                               ANY_VALUE(r.s_to_site) s_to_site, COUNT(*) trips, ANY_VALUE(r.s_mode) s_mode,
                               ROUND(AVG(r.d_planned_km), 1) planned_km, ROUND(AVG(r.d_actual_km), 1) actual_km,
                               ROUND(AVG(r.d_extra_km), 1) extra_km, ROUND(SUM(r.i_deviations) / COUNT(*), 2) deviations,
                               ROUND(AVG(r.d_adherence_pct), 1) adherence_pct, SUM(r.d_variance) variance,
                               SUM(r.s_verdict='loss') loss_trips, ANY_VALUE(r.i_ref_trip) ref_trip,
                               ANY_VALUE(p.i_sample) sample
                          {base} GROUP BY r.i_from_site, r.i_to_site
                         {order_clause(sort, order, lane_sort, 'loss')}
                         LIMIT %s OFFSET %s""", (*params, page_size, (page - 1) * page_size))
        items = rows(cur)
    return {"run_id": rid, **paged(total, page, page_size, items)}


@router.get("/routes/trip/{trip_no}")
def route_trip(trip_no: int, run: int | None = None, conn=Depends(get_geo_db)):
    """One trip against its plan: both paths, every deviation, the money."""
    from nexgen.shared.geoengine.pipeline import phases
    from nexgen.shared.geoengine.prep import codec

    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute("SELECT * FROM geo_trip_route WHERE i_run_id=%s AND i_trip_no=%s", (rid, trip_no))
        route = cur.fetchone()
        if not route:
            cur.execute("SELECT s_shape FROM geo_trip_phase WHERE i_run_id=%s AND i_trip_no=%s", (rid, trip_no))
            ph = cur.fetchone()
            raise HTTPException(404, "this trip has no loading and unloading place to plan between"
                                if ph and ph["s_shape"] != "loaded" else f"trip {trip_no} has not been analysed")
        route = clean(route)
        cur.execute("SELECT * FROM geo_route_deviation WHERE i_run_id=%s AND i_trip_no=%s ORDER BY i_seq",
                    (rid, trip_no))
        deviations = rows(cur)
        plan_path = []
        if route.get("i_plan_id"):
            cur.execute("SELECT s_mode, s_polyline, i_ref_trip, i_sample FROM geo_route_plan WHERE i_plan_id=%s",
                        (route["i_plan_id"],))
            p = cur.fetchone()
            if p and p["s_polyline"]:
                plan_path = [[round(a, 6), round(b, 6)] for a, b in pl.decode(p["s_polyline"], 6)]
        cur.execute("SELECT m_data FROM geo_fit_trail WHERE i_run_id=%s AND i_trip_no=%s", (rid, trip_no))
        blob = cur.fetchone()
    actual = []
    if blob and route.get("dt_transit_from") and route.get("dt_transit_to"):
        arr = codec.decode(blob["m_data"])
        a, b = phases._epoch(route["dt_transit_from"]), phases._epoch(route["dt_transit_to"])
        sel = arr[(arr["t"] >= a) & (arr["t"] <= b) & (arr["role"] != codec.ROLES.index("reject"))]
        # Each point carries the deviation it belongs to (its i_seq), 0 when on the plan.
        spans = [(phases._epoch(d["dt_leave"]), phases._epoch(d["dt_back"] or route["dt_transit_to"]), d["i_seq"])
                 for d in deviations]
        step = max(1, len(sel) // 3000)
        keep = set(range(0, len(sel), step)) | {len(sel) - 1}
        for a_, b_, _ in spans:        # never drop the points where a deviation starts or ends
            keep |= {int(i) for i in ((sel["t"] >= a_) & (sel["t"] <= b_)).nonzero()[0][[0, -1]]}                 if ((sel["t"] >= a_) & (sel["t"] <= b_)).any() else set()
        for i in sorted(keep):
            r = sel[i]
            t = int(r["t"])
            dev = next((q for a_, b_, q in spans if a_ <= t <= b_), 0)
            actual.append([round(float(r["lat"]), 6), round(float(r["lon"]), 6), dev])
    return {"run_id": rid, "trip_no": trip_no, "route": route, "deviations": deviations,
            "plan_path": plan_path, "actual_path": actual, "settings": _settings()}
