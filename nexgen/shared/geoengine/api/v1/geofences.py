"""Geofences: the master, and what the fleet did at each fence.

A fence's page answers four questions in order: what shape is it (and is the
shape trustworthy), how busy is it, how long do trucks stay, and who comes.
Counts here include every visit to the fence, nested or not -- on a fence's
own page the question is who was inside *this* fence.
"""

from __future__ import annotations

import math
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query

from nexgen.shared.geoengine import store
from nexgen.shared.geoengine.api.v1.common import (clean, day_bounds, order_clause, paged, percentile,
                                      resolve_run, rows, scale_sql)
from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import get_geo_db

router = APIRouter(tags=["geofences"])

LIST_SORT = {
    "name": "f.s_site_name", "visits": "COALESCE(s.i_visits,0)",
    "vehicles": "COALESCE(s.i_vehicles,0)", "trips": "COALESCE(s.i_trips,0)",
    "dwell_p50": "s.i_dwell_p50_s", "dwell_total": "COALESCE(s.i_dwell_total_s,0)",
    "last": "s.dt_last", "area": "f.d_area_sqm", "violations": "COALESCE(s.i_violations,0)",
    "site": "f.i_site_id", "state": "f.s_state",
}

SCALE = scale_sql("f.d_area_sqm")


@router.get("/geofences")
def list_geofences(
    q: str | None = None,
    type: str | None = Query(None, description="site type as in the master"),
    category: str | None = Query(None, description="normal | restricted | high_risk"),
    scale: str | None = Query(None, description="micro | site | campus | regional"),
    status: str = Query("active", description="active | inactive | all"),
    visited: str = Query("all", description="yes | no | all"),
    state: str | None = Query(None, description="Indian state, as the map names it"),
    sort: str = "visits", order: str = "desc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
    run: int | None = None, conn=Depends(get_geo_db),
):
    where, params = [], []
    if status == "active":
        where.append("f.b_active = 1")
    elif status == "inactive":
        where.append("f.b_active = 0")
    if q:
        if q.isdigit():
            where.append("(f.i_site_id = %s OR f.s_site_name LIKE %s)")
            params += [int(q), f"%{q}%"]
        else:
            where.append("f.s_site_name LIKE %s")
            params.append(f"%{q}%")
    if type:
        where.append("f.s_type = %s")
        params.append(type)
    if category:
        where.append("f.s_category = %s")
        params.append(category)
    if scale:
        where.append(f"{SCALE} = %s")
        params.append(scale)
    if state:
        where.append("f.s_state = %s")
        params.append(state)
    if visited == "yes":
        where.append("COALESCE(s.i_visits, 0) > 0")
    elif visited == "no":
        where.append("COALESCE(s.i_visits, 0) = 0")
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        base = f"""FROM geo_fence f
                   LEFT JOIN geo_fence_stats s ON s.i_run_id = {int(rid)} AND s.i_fence_id = f.i_fence_id
                   {clause}"""
        cur.execute(f"SELECT COUNT(*) n {base}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"""
            SELECT f.i_fence_id, f.i_site_id, f.s_site_name, f.s_type, f.s_category, f.b_active,
                   {SCALE} AS s_scale, ROUND(f.d_area_sqm) d_area_sqm, f.i_vertices,
                   f.d_inradius_m, f.i_tolerance, f.i_max_speed, f.b_self_intersecting,
                   f.d_centroid_lat, f.d_centroid_long, f.s_state, f.s_district, f.s_states,
                   COALESCE(s.i_visits,0) i_visits, COALESCE(s.i_vehicles,0) i_vehicles,
                   COALESCE(s.i_trips,0) i_trips, COALESCE(s.i_transporters,0) i_transporters,
                   s.i_dwell_p50_s, s.i_dwell_p90_s, COALESCE(s.i_dwell_total_s,0) i_dwell_total_s,
                   COALESCE(s.i_violations,0) i_violations, COALESCE(s.i_inferred,0) i_inferred,
                   COALESCE(s.i_open_visits,0) i_open_visits, s.dt_first, s.dt_last
              {base}
              {order_clause(sort, order, LIST_SORT, 'visits')}, f.s_site_name
             LIMIT %s OFFSET %s""", (*params, page_size, (page - 1) * page_size))
        items = rows(cur)
        for it in items:
            it["band_m"] = round(settings.detector.band_for(_band_probe(it)), 1)
    return {"run_id": rid, **paged(total, page, page_size, items)}


class _band_probe:
    """Just enough of a Fence for DetectorConfig.band_for."""
    __slots__ = ("inradius_m", "tolerance_m")

    def __init__(self, row: dict):
        self.inradius_m = row.get("d_inradius_m")
        self.tolerance_m = float(row.get("i_tolerance") or 0)


@router.get("/geofences/facets")
def geofence_facets(run: int | None = None, conn=Depends(get_geo_db)):
    """Filter options with counts, and the headline numbers for the list page."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute("SELECT s_type, COUNT(*) n FROM geo_fence WHERE b_active=1 GROUP BY 1 ORDER BY n DESC")
        types = rows(cur)
        cur.execute("SELECT s_category, COUNT(*) n FROM geo_fence WHERE b_active=1 GROUP BY 1 ORDER BY n DESC")
        categories = rows(cur)
        cur.execute(f"SELECT {SCALE} s_scale, COUNT(*) n FROM geo_fence f WHERE b_active=1 GROUP BY 1")
        scales = rows(cur)
        cur.execute("SELECT s_state, COUNT(*) n FROM geo_fence WHERE b_active=1 GROUP BY 1 ORDER BY n DESC")
        states = rows(cur)
        cur.execute(f"""
            SELECT COUNT(*) fences, SUM(f.b_active) active,
                   SUM(f.b_active=1 AND COALESCE(s.i_visits,0) > 0) visited,
                   SUM(f.b_active=1 AND f.d_inradius_m < 25) small,
                   SUM(f.b_self_intersecting) self_intersecting
              FROM geo_fence f
              LEFT JOIN geo_fence_stats s ON s.i_run_id=%s AND s.i_fence_id=f.i_fence_id""", (rid,))
        totals = clean(cur.fetchone())
        cur.execute("""SELECT SUM(i_visits) visits, SUM(i_violations) violations, SUM(i_inferred) inferred
                         FROM geo_fence_stats WHERE i_run_id=%s AND s_scale IN ('micro','site','campus')""",
                    (rid,))
        totals.update(clean(cur.fetchone()))
        # Hours at facilities as the day summary measures them: each vehicle's
        # facility time once, however many nested fences it was inside. Summing
        # fence dwell instead would count a truck in a mill inside a works twice.
        cur.execute("SELECT SUM(i_facility_dwell_s) dwell_s FROM geo_day_summary WHERE i_run_id=%s", (rid,))
        totals.update(clean(cur.fetchone()))
    return {"run_id": rid, "types": types, "categories": categories, "scales": scales,
            "states": states, "totals": totals}


def _fence_row(cur, site_id: int) -> dict:
    cur.execute(f"""SELECT i_fence_id, i_site_id, s_site_name, s_type, s_category, i_max_speed,
                           i_tolerance, b_active, i_vertices, d_min_lat, d_max_lat, d_min_long,
                           d_max_long, d_centroid_lat, d_centroid_long, d_area_sqm, d_perimeter_m,
                           b_self_intersecting, s_geom_notes, d_inradius_m, dt_compiled,
                           s_state, s_district, s_states, d_state_offset_m,
                           {scale_sql('d_area_sqm')} s_scale
                      FROM geo_fence WHERE i_site_id=%s""", (site_id,))
    fence = cur.fetchone()
    if not fence:
        raise HTTPException(404, f"no geofence for site {site_id}")
    return clean(fence)


@router.get("/geofences/{site_id}")
def geofence_detail(site_id: int, run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        fence = _fence_row(cur, site_id)
        fence["band_m"] = round(settings.detector.band_for(_band_probe(fence)), 1)
        cur.execute("SELECT i_seq, d_lat, d_long FROM geo_site_vertex WHERE i_site_id=%s ORDER BY i_seq",
                    (site_id,))
        ring = [[float(r["d_lat"]), float(r["d_long"])] for r in cur.fetchall()]
        cur.execute("SELECT s_reason, s_detail FROM geo_import_reject WHERE i_site_id=%s", (site_id,))
        notes = rows(cur)
        cur.execute("SELECT * FROM geo_fence_stats WHERE i_run_id=%s AND i_fence_id=%s",
                    (rid, fence["i_fence_id"]))
        stats = cur.fetchone()
        cur.execute("""SELECT COUNT(*) n FROM geo_site WHERE s_site_name=%s""", (fence["s_site_name"],))
        same_name = cur.fetchone()["n"]
        cur.execute("""SELECT dt_created, s_created_by, dt_modified, s_modified_by, i_entity_id
                         FROM geo_site WHERE i_site_id=%s""", (site_id,))
        site = cur.fetchone()

    return {"run_id": rid, "fence": fence, "ring": ring, "import_notes": notes,
            "stats": clean(stats) if stats else None, "site": clean(site) if site else None,
            "sites_sharing_name": int(same_name),
            "nesting": _nesting(fence)}


def _nesting(fence: dict) -> dict:
    """Fences this one sits inside, and fences inside it.

    Answered with the live index rather than SQL: containment is decided by
    the same ray cast the detector uses, so the page shows exactly the
    nesting the visit counts were produced under.
    """
    idx = store.get_index()
    me = idx.by_id(fence["i_fence_id"])
    if me is None:
        return {"parents": [], "children": []}
    parents, children = [], []
    for f in idx.query_point(me.centroid_lat, me.centroid_lon):
        if f.fence_id != me.fence_id and f.area_m2 > me.area_m2 and f.contains(me.centroid_lat, me.centroid_lon):
            parents.append(f)
    for f in idx.query_box(me.min_lon, me.min_lat, me.max_lon, me.max_lat):
        if f.fence_id != me.fence_id and f.area_m2 < me.area_m2 and me.contains(f.centroid_lat, f.centroid_lon):
            children.append(f)

    def brief(f):
        return {"site_id": f.site_id, "name": f.name, "category": f.category,
                "scale": store.scale_of(f), "area_m2": round(f.area_m2)}
    parents.sort(key=lambda f: f.area_m2)
    children.sort(key=lambda f: -f.area_m2)
    return {"parents": [brief(f) for f in parents[:20]],
            "children": [brief(f) for f in children[:200]],
            "children_total": len(children)}


@router.get("/geofences/{site_id}/activity")
def geofence_activity(site_id: int, date_from: str | None = Query(None, alias="from"),
                      date_to: str | None = Query(None, alias="to"),
                      run: int | None = None, conn=Depends(get_geo_db)):
    """Daily series, arrival pattern, dwell distribution, who comes, and where
    trucks actually cross the boundary."""
    a, b = day_bounds(date_from, date_to)
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        fence = _fence_row(cur, site_id)
        fid = fence["i_fence_id"]

        where, params = ["i_run_id=%s", "i_fence_id=%s"], [rid, fid]
        if a:
            where.append("d_day >= %s")
            params.append(a.date())
        if b:
            where.append("d_day < %s")
            params.append(b.date())
        cur.execute(f"""SELECT d_day, i_entries, i_exits, i_vehicles, i_dwell_s, i_dwell_p50_s
                          FROM geo_fence_day WHERE {' AND '.join(where)} ORDER BY d_day""", params)
        daily = rows(cur)

        vwhere, vparams = ["v.i_run_id=%s", "v.i_fence_id=%s"], [rid, fid]
        if a:
            vwhere.append("v.dt_enter >= %s")
            vparams.append(a)
        if b:
            vwhere.append("v.dt_enter < %s")
            vparams.append(b)
        # Physical visits: a stay shared by several consignment trips counts once.
        cur.execute(f"""
            SELECT v.s_asset_id, v.i_trip_no, v.dt_enter, v.dt_exit, v.b_open, v.b_entry_observed,
                   v.i_dwell_seconds, v.s_trans_name
              FROM geo_pvisit v
             WHERE {' AND '.join(vwhere)}""", vparams)
        visits = cur.fetchall()

        ewhere = ["i_run_id=%s", "i_fence_id=%s"]
        eparams: list = [rid, fid]
        if a:
            ewhere.append("dt_event >= %s")
            eparams.append(a)
        if b:
            ewhere.append("dt_event < %s")
            eparams.append(b)
        # DISTINCT: consignment trips on one truck record the same crossing.
        cur.execute(f"""SELECT DISTINCT s_asset_id, dt_event, s_event, d_lat, d_long, s_confirmed_by,
                                 i_gap_seconds
                          FROM geo_event WHERE {' AND '.join(ewhere)}
                         ORDER BY dt_event DESC LIMIT 3000""", eparams)
        crossings = [{"event": r["s_event"], "lat": float(r["d_lat"]), "lon": float(r["d_long"]),
                      "by": r["s_confirmed_by"], "gap": r["i_gap_seconds"]} for r in cur.fetchall()]

        cur.execute("""SELECT s_kind, COUNT(*) n FROM geo_palert
                        WHERE i_run_id=%s AND i_fence_id=%s GROUP BY 1""", (rid, fid))
        violations = rows(cur)
        cur.execute("""SELECT s_confidence, s_kind, COUNT(*) n FROM geo_inferred_visit
                        WHERE i_run_id=%s AND i_fence_id=%s GROUP BY 1, 2""", (rid, fid))
        inferred = rows(cur)

    # Arrivals by weekday x hour. Only observed entries: a visit that began
    # before its trail did has no known arrival time.
    heat = [[0] * 24 for _ in range(7)]
    for v in visits:
        if v["b_entry_observed"]:
            heat[v["dt_enter"].weekday()][v["dt_enter"].hour] += 1

    # Dwell distribution over fully observed visits only.
    edges = [0, 5, 15, 30, 60, 120, 240, 480, 720, 1440, 2880, None]
    labels = ["<5m", "5-15m", "15-30m", "30-60m", "1-2h", "2-4h", "4-8h", "8-12h", "12-24h", "1-2d", ">2d"]
    hist = [0] * len(labels)
    measured = []
    for v in visits:
        if v["b_open"] or not v["b_entry_observed"] or v["dt_exit"] is None or v["i_dwell_seconds"] is None:
            continue
        m = v["i_dwell_seconds"] / 60
        measured.append(v["i_dwell_seconds"])
        for i in range(len(labels)):
            hi = edges[i + 1]
            if hi is None or m < hi:
                hist[i] += 1
                break

    vehicles = Counter(v["s_asset_id"] for v in visits if v["s_asset_id"])
    trans = Counter(v["s_trans_name"] or "(no trip record)" for v in visits)
    dwell_by_trans: dict[str, list] = {}
    for v in visits:
        if v["dt_exit"] is not None and v["b_entry_observed"] and not v["b_open"]:
            dwell_by_trans.setdefault(v["s_trans_name"] or "(no trip record)", []).append(v["i_dwell_seconds"] or 0)

    return {
        "run_id": rid, "site_id": site_id,
        "daily": daily,
        "weekday_hour": heat,
        "dwell_histogram": [{"bucket": l, "visits": n} for l, n in zip(labels, hist)],
        "dwell": {"measured_visits": len(measured), "p25_s": percentile(measured, 25),
                  "p50_s": percentile(measured, 50), "p75_s": percentile(measured, 75),
                  "p90_s": percentile(measured, 90)},
        "visits_total": len(visits),
        "open_visits": sum(1 for v in visits if v["b_open"]),
        "unobserved_entries": sum(1 for v in visits if not v["b_entry_observed"]),
        "top_vehicles": [{"s_asset_id": k, "visits": n} for k, n in vehicles.most_common(15)],
        "top_transporters": [{"transporter": k, "visits": n,
                              "dwell_p50_s": percentile(dwell_by_trans.get(k, []), 50)}
                             for k, n in trans.most_common(12)],
        "crossings": crossings,
        "violations": violations,
        "inferred": inferred,
    }


VISIT_SORT = {"enter": "v.dt_enter", "exit": "v.dt_exit", "dwell": "v.i_dwell_seconds",
              "vehicle": "v.s_asset_id", "trip": "v.i_trip_no"}


@router.get("/geofences/{site_id}/visits")
def geofence_visits(site_id: int, q: str | None = None,
                    date_from: str | None = Query(None, alias="from"),
                    date_to: str | None = Query(None, alias="to"),
                    sort: str = "enter", order: str = "desc",
                    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=500),
                    run: int | None = None, conn=Depends(get_geo_db)):
    a, b = day_bounds(date_from, date_to)
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        fence = _fence_row(cur, site_id)
        where, params = ["v.i_run_id=%s", "v.i_fence_id=%s"], [rid, fence["i_fence_id"]]
        if a:
            where.append("COALESCE(v.dt_exit, v.dt_enter + INTERVAL v.i_dwell_seconds SECOND) >= %s")
            params.append(a)
        if b:
            where.append("v.dt_enter < %s")
            params.append(b)
        if q:
            where.append("(v.s_asset_id LIKE %s OR v.s_trans_name LIKE %s OR v.s_driver_name LIKE %s "
                         "OR FIND_IN_SET(%s, v.s_trips) > 0)")
            params += [f"%{q}%", f"%{q}%", f"%{q}%", q]
        base = f"""FROM geo_pvisit v LEFT JOIN geo_trip_meta m ON m.i_trip_no = v.i_trip_no
                   WHERE {' AND '.join(where)}"""
        cur.execute(f"SELECT COUNT(*) n {base}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"""
            SELECT v.id, v.i_trip_no, v.i_trips, v.s_trips, v.s_asset_id, v.dt_enter, v.dt_exit,
                   v.b_open, v.b_primary, v.b_entry_observed, v.i_dwell_seconds, v.i_max_speed,
                   v.i_enter_gap_seconds, v.i_exit_gap_seconds, v.s_confirmed_by,
                   v.s_trans_name, v.s_driver_name, m.s_origin, m.s_destination
              {base}
              {order_clause(sort, order, VISIT_SORT, 'enter')}
             LIMIT %s OFFSET %s""", (*params, page_size, (page - 1) * page_size))
        items = rows(cur)
    return {"run_id": rid, **paged(total, page, page_size, items)}


@router.get("/geofences-rings")
def geofence_rings(ids: str = Query(..., description="comma-separated site ids"),
                   conn=Depends(get_geo_db)):
    """Full rings for named sites -- what a trip or vehicle map draws."""
    try:
        site_ids = sorted({int(x) for x in ids.split(",") if x.strip()})[:500]
    except ValueError:
        raise HTTPException(400, "ids must be integers") from None
    if not site_ids:
        return {"fences": []}
    marks = ",".join(["%s"] * len(site_ids))
    with conn.cursor() as cur:
        cur.execute(f"""SELECT i_site_id, s_site_name, s_category, {scale_sql('d_area_sqm')} s_scale
                          FROM geo_fence WHERE i_site_id IN ({marks})""", site_ids)
        meta = {r["i_site_id"]: r for r in cur.fetchall()}
        cur.execute(f"""SELECT i_site_id, d_lat, d_long FROM geo_site_vertex
                         WHERE i_site_id IN ({marks}) ORDER BY i_site_id, i_seq""", site_ids)
        rings: dict[int, list] = {}
        for r in cur.fetchall():
            rings.setdefault(r["i_site_id"], []).append([float(r["d_lat"]), float(r["d_long"])])
    return {"fences": [{"site_id": sid, "name": m["s_site_name"], "category": m["s_category"],
                        "scale": m["s_scale"], "ring": rings.get(sid, [])}
                       for sid, m in meta.items()]}


@router.get("/geofences-near")
def geofences_near(lat: float, lon: float, radius_m: float = Query(1500, le=20000),
                   limit: int = Query(200, le=1000), conn=Depends(get_geo_db)):
    """Fence rings around a point, for map context on detail pages."""
    dlat = radius_m / 110_574.0
    dlon = dlat / max(0.2, abs(math.cos(math.radians(lat))))
    with conn.cursor() as cur:
        cur.execute(f"""SELECT i_site_id FROM geo_fence
                         WHERE b_active=1 AND d_min_long <= %s AND d_max_long >= %s
                           AND d_min_lat <= %s AND d_max_lat >= %s
                           AND d_area_sqm < 100000000
                         ORDER BY d_area_sqm ASC LIMIT %s""",
                    (lon + dlon, lon - dlon, lat + dlat, lat - dlat, limit))
        ids = [r["i_site_id"] for r in cur.fetchall()]
    if not ids:
        return {"fences": []}
    return geofence_rings(",".join(map(str, ids)), conn)
