from fastapi import APIRouter, Depends, Query
from typing import Optional
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope, base_clause, summary_clause
import math

router = APIRouter(prefix="/routes", tags=["Routes"])


@router.get("")
def list_routes(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = "trip_count",
    sort_order: str = "desc",
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    offset = (page - 1) * limit
    allowed_sorts = {"trip_count", "avg_duration_min", "eta_success_rate", "avg_distance_km"}
    if sort_by not in allowed_sorts:
        sort_by = "trip_count"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    cnr_clause, params = summary_clause(scope)
    conditions = [cnr_clause]
    if search:
        conditions.append("route_name LIKE %s")
        params = params + [f"%{search}%"]
    where = "WHERE " + " AND ".join(conditions)

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM route_summary {where}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT id, origin, destination, route_name, trip_count,
                   avg_duration_min, eta_success_rate, avg_distance_km
            FROM route_summary
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


@router.get("/detail")
def get_route_detail(origin: str, destination: str, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    cnr_sum, cnr_sum_p = summary_clause(scope)
    tclause, tparams = base_clause(scope, "cnr_id", alias="t")
    tfilter = f" AND {tclause}" if tclause else ""
    with conn.cursor() as cur:
        # Route summary (rollup or per-consignor slice)
        cur.execute(
            f"SELECT * FROM route_summary WHERE origin = %s AND destination = %s AND {cnr_sum}",
            [origin, destination] + cnr_sum_p,
        )
        summary = cur.fetchone()

        # Time patterns.
        #   Unscoped -> read the pre-aggregated route_time_patterns table (a
        #     fleet-wide rollup; it has no cnr_id dimension).
        #   Scoped   -> that table would leak every consignor's timing, so
        #     recompute this one route's pattern live from the consignor's own
        #     trips (mirrors refresh_all_summaries' route_time_patterns query).
        if scope.active:
            cur.execute(
                f"""
                SELECT HOUR(t.trip_start) AS hour_of_day, DAYOFWEEK(t.trip_start) AS day_of_week,
                       ROUND(AVG(t.trip_duration_minutes), 2) AS avg_duration, COUNT(*) AS trip_count,
                       ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2) AS eta_success_rate
                FROM trips t
                JOIN locations lo ON t.origin_id = lo.id
                JOIN locations ld ON t.destination_id = ld.id
                WHERE lo.name = %s AND ld.name = %s
                  AND t.trip_start IS NOT NULL AND t.trip_duration_minutes IS NOT NULL
                  AND t.eta_data_status = 'available'{tfilter}
                GROUP BY HOUR(t.trip_start), DAYOFWEEK(t.trip_start) HAVING COUNT(*) >= 2
                ORDER BY day_of_week, hour_of_day
                """,
                [origin, destination] + tparams,
            )
        else:
            cur.execute(
                """
                SELECT hour_of_day, day_of_week, avg_duration, trip_count, eta_success_rate
                FROM route_time_patterns
                WHERE origin = %s AND destination = %s
                ORDER BY day_of_week, hour_of_day
                """,
                (origin, destination),
            )
        time_patterns = cur.fetchall()

        # Top drivers on this route
        cur.execute(
            f"""
            SELECT d.id AS driver_id, d.name AS driver_name, COUNT(*) AS trip_count,
                   ROUND(AVG(t.trip_duration_minutes), 2) AS avg_duration,
                   ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2) AS eta_rate
            FROM trips t
            JOIN drivers d ON t.driver_id = d.id
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE lo.name = %s AND ld.name = %s{tfilter}
            GROUP BY d.id, d.name
            ORDER BY trip_count DESC
            LIMIT 10
            """,
            [origin, destination] + tparams,
        )
        top_drivers = cur.fetchall()

        # Recent trips on this route
        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, d.name AS driver_name, v.asset_id,
                   t.trip_start,
                   t.ata_in AS trip_end,
                   TIMESTAMPDIFF(MINUTE, t.trip_start, t.ata_in) AS trip_duration_minutes,
                   t.eta_met, t.avg_speed_kmph
            FROM trips t
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN vehicles v ON t.vehicle_id = v.id
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE lo.name = %s AND ld.name = %s{tfilter}
            ORDER BY t.trip_start DESC
            LIMIT 20
            """,
            [origin, destination] + tparams,
        )
        recent_trips = cur.fetchall()

    for t in recent_trips:
        t["trip_start"] = str(t["trip_start"]) if t["trip_start"] else None

    return {
        "summary": summary,
        "time_patterns": time_patterns,
        "top_drivers": top_drivers,
        "recent_trips": recent_trips,
    }