"""Alerts: restricted-zone entries and in-site overspeed.

One row per breach per physical visit, never per fix and never once per
consignment trip: read from the physical ledger (pipeline/physical.py). The
master's duplicate restricted zones (docs/DATA_QUALITY.md, item 7) still mean
some breaches appear under two site ids; they are shown as the master defines
them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from nexgen.shared.geoengine.api.v1.common import clean, day_bounds, order_clause, paged, resolve_run, rows
from nexgen.shared.geoengine.db import get_geo_db

router = APIRouter(tags=["alerts"])

SORT = {"time": "x.dt_event", "kind": "x.s_kind", "site": "x.s_site_name",
        "vehicle": "x.s_asset_id", "excess": "(x.i_observed - x.i_limit)"}


@router.get("/alerts")
def list_alerts(kind: str | None = Query(None, description="overspeed | restricted_entry | high_risk_entry"),
                site_id: int | None = None, q: str | None = None,
                date_from: str | None = Query(None, alias="from"),
                date_to: str | None = Query(None, alias="to"),
                sort: str = "time", order: str = "desc",
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                run: int | None = None, conn=Depends(get_geo_db)):
    a, b = day_bounds(date_from, date_to)
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        where, params = ["x.i_run_id=%s"], [rid]
        if kind:
            where.append("x.s_kind=%s")
            params.append(kind)
        if site_id:
            where.append("x.i_site_id=%s")
            params.append(site_id)
        if q:
            where.append("(x.s_asset_id LIKE %s OR x.s_site_name LIKE %s OR x.s_trans_name LIKE %s "
                         "OR x.s_driver_name LIKE %s)")
            params += [f"%{q}%"] * 4
        if a:
            where.append("x.dt_event >= %s")
            params.append(a)
        if b:
            where.append("x.dt_event < %s")
            params.append(b)
        base = f"""FROM geo_palert x LEFT JOIN geo_trip_meta m ON m.i_trip_no = x.i_trip_no
                   WHERE {' AND '.join(where)}"""
        cur.execute(f"SELECT COUNT(*) n {base}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"""SELECT x.id, x.dt_event, x.s_kind, x.i_site_id, x.s_site_name, x.i_trip_no,
                               x.i_trips, x.s_trips, x.s_asset_id, x.i_observed, x.i_limit, x.s_detail,
                               x.d_lat, x.d_long, x.s_trans_name, x.s_driver_name, m.s_origin,
                               m.s_destination
                          {base} {order_clause(sort, order, SORT, 'time')}
                         LIMIT %s OFFSET %s""", (*params, page_size, (page - 1) * page_size))
        items = rows(cur)

        cur.execute(f"SELECT x.s_kind, COUNT(*) n {base} GROUP BY 1 ORDER BY n DESC", params)
        by_kind = rows(cur)
        cur.execute(f"SELECT DATE(x.dt_event) d_day, x.s_kind, COUNT(*) n {base} GROUP BY 1, 2 ORDER BY 1",
                    params)
        by_day = rows(cur)
        cur.execute(f"""SELECT x.i_site_id, x.s_site_name, x.s_kind, COUNT(*) n,
                               COUNT(DISTINCT x.s_asset_id) vehicles
                          {base} GROUP BY 1, 2, 3 ORDER BY n DESC LIMIT 15""", params)
        by_site = rows(cur)
        cur.execute(f"""SELECT COALESCE(x.s_trans_name, '(no trip record)') transporter, COUNT(*) n,
                               COUNT(DISTINCT x.s_asset_id) vehicles
                          {base} GROUP BY 1 ORDER BY n DESC LIMIT 15""", params)
        by_transporter = rows(cur)
        cur.execute(f"""SELECT x.s_asset_id, COUNT(*) n {base}
                         GROUP BY 1 ORDER BY n DESC LIMIT 15""", params)
        by_vehicle = rows(cur)
    return {"run_id": rid, "by_kind": by_kind, "by_day": by_day, "by_site": by_site,
            "by_transporter": by_transporter, "by_vehicle": by_vehicle,
            **paged(total, page, page_size, items)}
