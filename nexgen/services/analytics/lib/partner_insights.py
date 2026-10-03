"""
Consignor / consignee analytics.

Both dimensions share the same drill-down shape (KPIs, top routes, top vehicles,
top drivers, delivery-status split, monthly trend, recent trips) — only the base
filter on `trips` differs:

    consignor  -> trips.cnr_id
    consignee  -> trips.customer_id (customers.cne_name is the display name)

So a single `_detail()` builds the drill-down from any `WHERE` fragment, and the
list/detail helpers just supply that fragment. Everything honours the active
consignor scope (see backend/app/core/consignor.py).
"""
from fastapi import HTTPException

from nexgen.shared.common.consignor import base_clause


def _stringify_dates(rows, *keys):
    for r in rows:
        for k in keys:
            if r.get(k) is not None:
                r[k] = str(r[k])
    return rows


# ----------------------------------------------------------------------
# Shared drill-down for one partner (a fixed WHERE on trips t)
# ----------------------------------------------------------------------

def _detail(cur, where: str, params: list) -> dict:
    cur.execute(f"""
        SELECT COUNT(*) AS trips,
               ROUND(SUM(t.trip_km), 1) AS total_km,
               ROUND(AVG(t.avg_speed_kmph), 1) AS avg_speed,
               ROUND(AVG(t.trip_duration_minutes), 1) AS avg_duration_min,
               ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
               COUNT(t.eta_met) AS otd_known_trips,
               ROUND(AVG(t.eta_delay_minutes), 1) AS avg_delay_min,
               COUNT(DISTINCT t.destination_id) AS destinations,
               COUNT(DISTINCT t.vehicle_id) AS vehicles,
               COUNT(DISTINCT t.driver_id) AS drivers,
               COUNT(DISTINCT t.customer_id) AS consignees,
               MIN(t.trip_start) AS first_trip,
               MAX(t.trip_start) AS last_trip
        FROM trips t {where}
    """, params)
    kpis = cur.fetchone()

    cur.execute(f"""
        SELECT lo.name AS origin, ld.name AS destination, COUNT(*) AS trips,
               ROUND(AVG(t.trip_duration_minutes), 1) AS avg_duration_min,
               ROUND(AVG(t.trip_km), 1) AS avg_km,
               ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
               COUNT(t.eta_met) AS otd_known_trips
        FROM trips t
        JOIN locations lo ON t.origin_id = lo.id
        JOIN locations ld ON t.destination_id = ld.id
        {where}
        GROUP BY lo.name, ld.name ORDER BY trips DESC LIMIT 10
    """, params)
    top_routes = cur.fetchall()

    cur.execute(f"""
        SELECT v.id AS vehicle_id, v.asset_id, v.asset_type, COUNT(*) AS trips,
               ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
               COUNT(t.eta_met) AS otd_known_trips,
               ROUND(SUM(t.trip_km), 1) AS total_km
        FROM trips t JOIN vehicles v ON t.vehicle_id = v.id
        {where}
        GROUP BY v.id, v.asset_id, v.asset_type ORDER BY trips DESC LIMIT 10
    """, params)
    top_vehicles = cur.fetchall()

    cur.execute(f"""
        SELECT d.id AS driver_id, d.name AS driver_name, COUNT(*) AS trips,
               ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
               COUNT(t.eta_met) AS otd_known_trips,
               ROUND(AVG(t.avg_speed_kmph), 1) AS avg_speed
        FROM trips t JOIN drivers d ON t.driver_id = d.id
        {where}
        GROUP BY d.id, d.name ORDER BY trips DESC LIMIT 10
    """, params)
    top_drivers = cur.fetchall()

    cur.execute(f"""
        SELECT COALESCE(m.s_delivery_status, 'Unknown') AS status, COUNT(*) AS trips
        FROM trips t
        LEFT JOIN tta_trip_metrics m ON m.i_trip_no = CAST(t.dispatch_entry_no AS UNSIGNED)
        {where}
        GROUP BY status ORDER BY trips DESC
    """, params)
    delivery = cur.fetchall()

    cur.execute(f"""
        SELECT DATE_FORMAT(t.trip_start, '%%Y-%%m') AS month, COUNT(*) AS trips,
               ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
               COUNT(t.eta_met) AS otd_known_trips,
               ROUND(SUM(t.trip_km), 1) AS total_km,
               ROUND(AVG(t.avg_speed_kmph), 1) AS avg_speed
        FROM trips t {where} AND t.trip_start IS NOT NULL
        GROUP BY month ORDER BY month
    """, params)
    monthly = cur.fetchall()

    cur.execute(f"""
        SELECT t.id, t.dispatch_entry_no,
               lo.name AS origin, ld.name AS destination,
               d.id AS driver_id, d.name AS driver_name,
               v.id AS vehicle_id, v.asset_id,
               t.trip_start, t.trip_duration_minutes, t.eta_met,
               t.avg_speed_kmph, t.trip_km, t.trip_status
        FROM trips t
        LEFT JOIN locations lo ON t.origin_id = lo.id
        LEFT JOIN locations ld ON t.destination_id = ld.id
        LEFT JOIN drivers d ON t.driver_id = d.id
        LEFT JOIN vehicles v ON t.vehicle_id = v.id
        {where}
        ORDER BY t.trip_start DESC LIMIT 20
    """, params)
    recent = _stringify_dates(cur.fetchall(), "trip_start")

    return {
        "kpis": _stringify_dates([kpis], "first_trip", "last_trip")[0],
        "top_routes": top_routes,
        "top_vehicles": top_vehicles,
        "top_drivers": top_drivers,
        "delivery": delivery,
        "monthly": monthly,
        "recent_trips": recent,
    }


# ----------------------------------------------------------------------
# Consignees (receivers) — trips.customer_id -> customers.cne_name
# ----------------------------------------------------------------------

def list_consignees(conn, scope, search: str = "") -> dict:
    cnr_clause, cnr_params = base_clause(scope, "cnr_id", alias="t")
    conds = ["c.cne_name IS NOT NULL AND c.cne_name <> ''"]
    params: list = []
    if cnr_clause:
        conds.append(cnr_clause)
        params += cnr_params
    if search:
        conds.append("c.cne_name LIKE %s")
        params.append(f"%{search}%")
    where = "WHERE " + " AND ".join(conds)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT c.cne_name AS consignee,
                   COUNT(*) AS trips,
                   ROUND(SUM(t.trip_km), 1) AS total_km,
                   ROUND(AVG(t.avg_speed_kmph), 1) AS avg_speed,
                   ROUND(AVG(t.trip_duration_minutes), 1) AS avg_duration_min,
                   ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
                   COUNT(t.eta_met) AS otd_known_trips,
                   ROUND(AVG(t.eta_delay_minutes), 1) AS avg_delay_min,
                   COUNT(DISTINCT t.destination_id) AS destinations,
                   COUNT(DISTINCT t.vehicle_id) AS vehicles,
                   COUNT(DISTINCT t.driver_id) AS drivers,
                   MAX(t.trip_start) AS last_trip
            FROM trips t JOIN customers c ON t.customer_id = c.id
            {where}
            GROUP BY c.cne_name ORDER BY trips DESC
        """, params)
        rows = _stringify_dates(cur.fetchall(), "last_trip")
    return {"data": rows, "total": len(rows)}


def consignee_detail(conn, name: str, scope) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM customers WHERE cne_name = %s", (name,))
        ids = [r["id"] for r in cur.fetchall()]
        if not ids:
            raise HTTPException(404, f"Consignee '{name}' not found")
        ph = ",".join(["%s"] * len(ids))
        where = f"WHERE t.customer_id IN ({ph})"
        params = list(ids)
        cnr_clause, cnr_params = base_clause(scope, "cnr_id", alias="t")
        if cnr_clause:
            where += f" AND {cnr_clause}"
            params += cnr_params
        detail = _detail(cur, where, params)
    return {"consignee": name, **detail}


# ----------------------------------------------------------------------
# Consignors (shippers) — trips.cnr_id -> consignors.s_cnr_name
# ----------------------------------------------------------------------

def list_consignors(conn, scope) -> dict:
    cnr_clause, cnr_params = base_clause(scope, "cnr_id", alias="t")
    where = f"WHERE {cnr_clause}" if cnr_clause else ""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT t.cnr_id AS id,
                   COALESCE(co.s_cnr_name, CONCAT('CNR-', t.cnr_id)) AS name,
                   COUNT(*) AS trips,
                   ROUND(SUM(t.trip_km), 1) AS total_km,
                   ROUND(AVG(t.avg_speed_kmph), 1) AS avg_speed,
                   ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
                   COUNT(t.eta_met) AS otd_known_trips,
                   COUNT(DISTINCT t.customer_id) AS consignees,
                   COUNT(DISTINCT t.destination_id) AS destinations,
                   COUNT(DISTINCT t.vehicle_id) AS vehicles,
                   COUNT(DISTINCT t.driver_id) AS drivers,
                   MAX(t.trip_start) AS last_trip
            FROM trips t
            LEFT JOIN consignors co ON co.i_cnr_id = t.cnr_id
            {where}
            GROUP BY t.cnr_id, co.s_cnr_name ORDER BY trips DESC
        """, cnr_params)
        rows = _stringify_dates(cur.fetchall(), "last_trip")
    return {"data": rows, "total": len(rows)}


def consignor_detail(conn, cnr_id: int, scope) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT s_cnr_name FROM consignors WHERE i_cnr_id = %s", (cnr_id,))
        row = cur.fetchone()
        name = row["s_cnr_name"] if row else None
        where = "WHERE t.cnr_id = %s"
        params: list = [cnr_id]
        # a route-based scope may not contradict the requested consignor
        if scope.active and scope.id != cnr_id:
            raise HTTPException(404, "Consignor not in the active scope")
        cur.execute("SELECT COUNT(*) AS c FROM trips WHERE cnr_id = %s", (cnr_id,))
        if not cur.fetchone()["c"] and name is None:
            raise HTTPException(404, f"Consignor {cnr_id} not found")
        # top consignees for this consignor (the extra dimension)
        cur.execute(f"""
            SELECT c.cne_name AS consignee, COUNT(*) AS trips,
                   ROUND(SUM(t.eta_met) / NULLIF(COUNT(t.eta_met), 0) * 100, 1) AS otd_pct,
                   COUNT(t.eta_met) AS otd_known_trips,
                   ROUND(SUM(t.trip_km), 1) AS total_km
            FROM trips t JOIN customers c ON t.customer_id = c.id
            {where} AND c.cne_name IS NOT NULL AND c.cne_name <> ''
            GROUP BY c.cne_name ORDER BY trips DESC LIMIT 10
        """, params)
        top_consignees = cur.fetchall()
        detail = _detail(cur, where, params)
    return {"id": cnr_id, "name": name or f"CNR-{cnr_id}",
            "top_consignees": top_consignees, **detail}
