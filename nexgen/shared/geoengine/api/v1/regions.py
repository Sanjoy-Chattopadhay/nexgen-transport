"""States: the geofence master and the fleet's activity, cut by Indian state,
beside the National Highway toll plazas in each.

A fence's state is decided once, geometrically, from the application's own
official boundaries (geo/regions.py). Visits and alerts come from the physical
ledger, so a stay shared by consignment trips counts once here as everywhere.
Toll plazas come from IHMCL's published NH fee plaza list (ingest/tolls.py):
National Highway plazas only, and without coordinates.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from nexgen.shared.geoengine.api.v1.common import clean, paged, resolve_run, rows, scale_sql
from nexgen.shared.geoengine.db import get_geo_db

router = APIRouter(tags=["states and toll plazas"])

SCALE = scale_sql("f.d_area_sqm")


@router.get("/states")
def list_states(run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute(f"""
            SELECT f.s_state,
                   COUNT(*) fences, SUM(f.b_active) active,
                   SUM(f.b_active = 1 AND {SCALE} IN ('micro','site','campus')) facility,
                   SUM(f.b_active = 1 AND {SCALE} = 'regional') regional,
                   SUM(f.b_active = 1 AND f.s_category IN ('restricted','high_risk')) restricted,
                   SUM(f.b_active = 1 AND COALESCE(s.i_visits, 0) > 0) visited,
                   SUM(f.s_states IS NOT NULL) cross_border,
                   SUM(f.d_state_offset_m IS NOT NULL) offshore
              FROM geo_fence f
              LEFT JOIN geo_fence_stats s ON s.i_run_id = %s AND s.i_fence_id = f.i_fence_id
             GROUP BY f.s_state""", (rid,))
        by_state = {r["s_state"]: clean(r) for r in cur.fetchall()}

        # Innermost facility visits and alerts, each real event once.
        cur.execute("""
            SELECT f.s_state, COUNT(*) visits, COUNT(DISTINCT v.s_asset_id) vehicles,
                   SUM(v.i_dwell_seconds) dwell_s
              FROM geo_pvisit v JOIN geo_fence f ON f.i_fence_id = v.i_fence_id
             WHERE v.i_run_id = %s AND v.b_primary = 1 AND v.s_scale IN ('micro','site','campus')
             GROUP BY f.s_state""", (rid,))
        activity = {r["s_state"]: clean(r) for r in cur.fetchall()}
        cur.execute("""
            SELECT f.s_state, COUNT(*) alerts FROM geo_palert x JOIN geo_fence f ON f.i_fence_id = x.i_fence_id
             WHERE x.i_run_id = %s GROUP BY f.s_state""", (rid,))
        alerts = {r["s_state"]: int(r["alerts"]) for r in cur.fetchall()}
        cur.execute("""SELECT s_state, COUNT(*) n, MAX(d_as_of) as_of, MAX(s_source) source
                         FROM geo_toll_plaza GROUP BY s_state""")
        tolls = {r["s_state"]: r for r in cur.fetchall()}
        cur.execute("SELECT MAX(d_as_of) as_of, COUNT(*) n FROM geo_toll_plaza")
        toll_total = clean(cur.fetchone())

    items = []
    for state in sorted(set(by_state) | set(tolls), key=lambda s: (s is None, s or "")):
        f = by_state.get(state, {})
        a = activity.get(state, {})
        items.append({
            "state": state,
            "fences": int(f.get("fences") or 0), "active": int(f.get("active") or 0),
            "facility": int(f.get("facility") or 0), "regional": int(f.get("regional") or 0),
            "restricted": int(f.get("restricted") or 0), "visited": int(f.get("visited") or 0),
            "cross_border": int(f.get("cross_border") or 0), "offshore": int(f.get("offshore") or 0),
            "facility_visits": int(a.get("visits") or 0), "vehicles": int(a.get("vehicles") or 0),
            "facility_dwell_s": int(a.get("dwell_s") or 0),
            "alerts": alerts.get(state, 0),
            "toll_plazas": int(tolls[state]["n"]) if state in tolls else 0,
        })
    return {
        "run_id": rid, "states": items,
        "totals": {
            "states_with_fences": sum(1 for i in items if i["fences"] and i["state"]),
            "fences": sum(i["fences"] for i in items),
            "cross_border": sum(i["cross_border"] for i in items),
            "offshore": sum(i["offshore"] for i in items),
            "unplaced": next((i["fences"] for i in items if i["state"] is None), 0),
            "toll_plazas": int(toll_total["n"] or 0),
            "toll_states": sum(1 for i in items if i["toll_plazas"]),
            "toll_list_as_of": toll_total["as_of"],
        },
    }


@router.get("/tolls")
def list_tolls(state: str | None = None, q: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
               conn=Depends(get_geo_db)):
    """National Highway toll plazas, as IHMCL lists them."""
    where, params = [], []
    if state:
        where.append("s_state = %s")
        params.append(state)
    if q:
        where.append("(s_name LIKE %s OR s_nh LIKE %s OR s_district LIKE %s OR s_section LIKE %s)")
        params += [f"%{q}%"] * 4
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) n FROM geo_toll_plaza {clause}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"""SELECT i_plaza_id, s_netc_code, s_name, s_state, s_state_listed, s_district, s_section,
                               s_nh, s_piu, s_regional_office, d_lat, d_long, s_source, d_as_of
                          FROM geo_toll_plaza {clause}
                         ORDER BY s_state, s_name LIMIT %s OFFSET %s""",
                    (*params, page_size, (page - 1) * page_size))
        items = rows(cur)
    return paged(total, page, page_size, items)
