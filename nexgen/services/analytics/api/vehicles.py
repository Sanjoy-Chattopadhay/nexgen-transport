from fastapi import APIRouter, Depends, Query
from typing import Optional
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope, base_clause, summary_clause
import math

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


@router.get("")
def list_vehicles(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = "total_trips",
    sort_order: str = "desc",
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    offset = (page - 1) * limit
    allowed_sorts = {"total_trips", "avg_speed_kmph", "total_distance_km", "eta_success_rate", "asset_id"}
    if sort_by not in allowed_sorts:
        sort_by = "total_trips"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    cnr_clause, params = summary_clause(scope)
    conditions = [cnr_clause]
    if search:
        conditions.append("(asset_id LIKE %s OR asset_type LIKE %s)")
        params = params + [f"%{search}%", f"%{search}%"]
    where = "WHERE " + " AND ".join(conditions)

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM vehicle_summary {where}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT vehicle_id, asset_id, asset_type, total_trips, drivers_used,
                   avg_speed_kmph, total_distance_km, avg_distance_km, eta_success_rate
            FROM vehicle_summary
            {where}
            ORDER BY {sort_by} {sort_order}
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": math.ceil(total / limit) if limit else 0,
    }


@router.get("/{vehicle_id}")
def get_vehicle_detail(vehicle_id: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    cnr_sum, cnr_sum_p = summary_clause(scope)
    tclause, tparams = base_clause(scope, "cnr_id", alias="t")
    tfilter = f" AND {tclause}" if tclause else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT * FROM vehicle_summary WHERE vehicle_id = %s AND {cnr_sum}",
            [vehicle_id] + cnr_sum_p,
        )
        summary = cur.fetchone()
        if not summary:
            return {"error": "Vehicle not found"}

        # Drivers who drove this vehicle
        cur.execute(
            f"""
            SELECT d.id AS driver_id, d.name AS driver_name, COUNT(*) AS trip_count
            FROM trips t
            JOIN drivers d ON t.driver_id = d.id
            WHERE t.vehicle_id = %s{tfilter}
            GROUP BY d.id, d.name
            ORDER BY trip_count DESC
            LIMIT 10
            """,
            [vehicle_id] + tparams,
        )
        drivers_used = cur.fetchall()

        # Recent trips
        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, d.name AS driver_name,
                   lo.name AS origin_name, ld.name AS destination_name,
                   t.trip_start, t.trip_duration_minutes, t.eta_met, t.avg_speed_kmph
            FROM trips t
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            WHERE t.vehicle_id = %s{tfilter}
            ORDER BY t.trip_start DESC
            LIMIT 20
            """,
            [vehicle_id] + tparams,
        )
        recent_trips = cur.fetchall()

        # ── Top routes for this vehicle with fleet comparison ──
        cur.execute(
            f"""
            SELECT
                lo.name AS origin, ld.name AS destination,
                COUNT(*) AS trip_count,
                ROUND(AVG(t.trip_duration_minutes), 2) AS avg_duration_min,
                ROUND(AVG(t.avg_speed_kmph), 2) AS avg_speed_kmph,
                ROUND(AVG(t.trip_km), 2) AS avg_distance_km,
                ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(t.eta_met), 0), 2) AS eta_success_rate
            FROM trips t
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE t.vehicle_id = %s AND t.trip_duration_minutes > 0{tfilter}
            GROUP BY lo.name, ld.name
            ORDER BY trip_count DESC
            LIMIT 10
            """,
            [vehicle_id] + tparams,
        )
        vehicle_routes = cur.fetchall()

        # Get fleet averages for those same routes (consignor-scoped benchmark row)
        route_keys = [(r["origin"], r["destination"]) for r in vehicle_routes]
        route_benchmarks = {}
        for origin, destination in route_keys:
            cur.execute(
                f"""
                SELECT avg_duration_min, avg_speed_kmph, avg_distance_km, eta_success_rate, trip_count
                FROM route_summary
                WHERE origin = %s AND destination = %s AND {cnr_sum}
                """,
                [origin, destination] + cnr_sum_p,
            )
            bench = cur.fetchone()
            if bench:
                route_benchmarks[f"{origin} -> {destination}"] = bench

        # ── Monthly performance trend ──
        cur.execute(
            f"""
            SELECT
                DATE_FORMAT(t.trip_start, '%%Y-%%m') AS month,
                COUNT(*) AS trips,
                ROUND(AVG(t.avg_speed_kmph), 2) AS avg_speed,
                ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(t.eta_met), 0), 2) AS eta_rate,
                ROUND(SUM(t.trip_km), 2) AS total_km
            FROM trips t
            WHERE t.vehicle_id = %s AND t.trip_start IS NOT NULL AND t.trip_duration_minutes > 0{tfilter}
            GROUP BY month
            ORDER BY month DESC
            LIMIT 12
            """,
            [vehicle_id] + tparams,
        )
        monthly_trend = cur.fetchall()

    for t in recent_trips:
        t["trip_start"] = str(t["trip_start"]) if t["trip_start"] else None

    return {
        "summary": summary,
        "drivers_used": drivers_used,
        "recent_trips": recent_trips,
        "vehicle_routes": vehicle_routes,
        "route_benchmarks": route_benchmarks,
        "monthly_trend": list(reversed(monthly_trend)),
    }


@router.get("/{vehicle_id}/trips")
def get_vehicle_trips(
    vehicle_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    offset = (page - 1) * limit
    tclause, tparams = base_clause(scope, "cnr_id", alias="t")
    tfilter = f" AND {tclause}" if tclause else ""

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM trips t WHERE t.vehicle_id = %s{tfilter}",
            [vehicle_id] + tparams,
        )
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, d.name AS driver_name,
                   lo.name AS origin_name, ld.name AS destination_name,
                   t.trip_start, t.trip_duration_minutes, t.eta_met,
                   t.avg_speed_kmph, t.trip_km, t.trip_status
            FROM trips t
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            WHERE t.vehicle_id = %s{tfilter}
            ORDER BY t.trip_start DESC
            LIMIT %s OFFSET %s
            """,
            [vehicle_id] + tparams + [limit, offset],
        )
        rows = cur.fetchall()

    for r in rows:
        r["trip_start"] = str(r["trip_start"]) if r["trip_start"] else None

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": math.ceil(total / limit) if limit else 0,
    }
