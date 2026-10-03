from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import (ConsignorScope, consignor_scope, base_clause,
                                        summary_clause)
from nexgen.shared.common.sql import date_frags
import math

router = APIRouter(prefix="/trips", tags=["Trips"])


@router.get("")
def list_trips(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    driver_id: Optional[int] = None,
    vehicle_id: Optional[int] = None,
    origin: Optional[str] = None,
    destination: Optional[str] = None,
    trip_status: Optional[str] = None,
    eta_met: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    offset = (page - 1) * limit
    conditions = []
    params = []

    cnr_clause, cnr_params = base_clause(scope, "cnr_id", alias="t")
    if cnr_clause:
        conditions.append(cnr_clause)
        params += cnr_params

    if driver_id:
        conditions.append("t.driver_id = %s")
        params.append(driver_id)
    if vehicle_id:
        conditions.append("t.vehicle_id = %s")
        params.append(vehicle_id)
    if origin:
        conditions.append("lo.name LIKE %s")
        params.append(f"%{origin}%")
    if destination:
        conditions.append("ld.name LIKE %s")
        params.append(f"%{destination}%")
    if trip_status:
        conditions.append("t.trip_status = %s")
        params.append(trip_status)
    if eta_met is not None:
        conditions.append("t.eta_met = %s")
        params.append(1 if eta_met else 0)
    # Same departure-window convention as every aggregate (date_to is a whole
    # day, not midnight) so this list always reconciles with the KPIs.
    for clause, p in date_frags("t.trip_start", date_from or "", date_to or ""):
        conditions.append(clause)
        params += p

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            {where_clause}
            """,
            params,
        )
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, d.name AS driver_name, v.asset_id,
                   lo.name AS origin_name, ld.name AS destination_name,
                   t.trip_start,
                   t.ata_in AS trip_end,
                   TIMESTAMPDIFF(MINUTE, t.trip_start, t.ata_in) AS trip_duration_minutes,
                   t.eta_met,
                   t.eta_delay_minutes, t.avg_speed_kmph, t.trip_km, t.trip_status,
                   t.is_active, t.material_desc,
                   -- Outcome context: eta_met is NULL whenever the trip has no
                   -- recorded arrival, so consumers need these two to tell
                   -- "delivered on time" from "closed without ever running".
                   m.s_delivery_status AS delivery_status,
                   t.trip_close_remark AS trip_closed_reason,
                   t.trip_closed_at
            FROM trips t
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN vehicles v ON t.vehicle_id = v.id
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            -- dispatch_entry_no holds the TTA i_trip_no; PK lookup per page row.
            -- The `> 0` guard is required, not cosmetic: MySQL casts a
            -- non-numeric dispatch number to 0 (warning, not error), which
            -- would otherwise attach a stray trip 0's delivery status to it.
            LEFT JOIN tta_trip_metrics m
                   ON m.i_trip_no > 0
                  AND m.i_trip_no = CAST(t.dispatch_entry_no AS UNSIGNED)
            {where_clause}
            ORDER BY t.trip_start DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()

    for r in rows:
        for k in ("trip_start", "trip_end", "trip_closed_at"):
            r[k] = str(r[k]) if r[k] else None

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": math.ceil(total / limit) if limit else 0,
    }


@router.get("/stats")
def get_trip_stats(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    cnr_clause, cnr_params = base_clause(scope, "cnr_id")
    cnr_filter = f" AND {cnr_clause}" if cnr_clause else ""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT
                COUNT(*) AS total_trips,
                SUM(CASE WHEN trip_status = 'C' THEN 1 ELSE 0 END) AS completed_trips,
                SUM(CASE WHEN trip_status != 'C' OR trip_status IS NULL THEN 1 ELSE 0 END) AS active_trips,
                ROUND(AVG(TIMESTAMPDIFF(MINUTE, trip_start, ata_in)), 2) AS avg_duration_minutes,
                ROUND(AVG(avg_speed_kmph), 2) AS avg_speed_kmph,
                ROUND(SUM(CASE WHEN eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(eta_met), 0) * 100, 2) AS eta_success_rate
            FROM trips
            WHERE trip_start IS NOT NULL AND ata_in IS NOT NULL{cnr_filter}
        """, cnr_params)
        row = cur.fetchone()
    return row


@router.get("/{trip_id}")
def get_trip_detail(trip_id: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    # Consignor-filter fragments reused across this endpoint's sub-queries.
    cnr_sum, cnr_sum_p = summary_clause(scope)               # summary tables (rollup row when unscoped)
    tclause, tparams = base_clause(scope, "cnr_id", alias="t")  # trips fact table
    tfilter = f" AND {tclause}" if tclause else ""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.*,
                   t.ata_in AS trip_end_actual,
                   TIMESTAMPDIFF(MINUTE, t.trip_start, t.ata_in) AS trip_duration_minutes_actual,
                   d.name AS driver_name, d.mobile1 AS driver_mobile,
                   v.asset_id, v.asset_type,
                   lo.name AS origin_name, ld.name AS destination_name,
                   c.cne_name AS customer_name
            FROM trips t
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN vehicles v ON t.vehicle_id = v.id
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN customers c ON t.customer_id = c.id
            WHERE t.id = %s
            """,
            (trip_id,),
        )
        trip = cur.fetchone()
        if not trip:
            return {"error": "Trip not found"}
        # Never let one consignor read another consignor's trip by id.
        if not scope.allows(trip.get("cnr_id")):
            raise HTTPException(404, "Trip not found")

        # Get waypoints if any
        cur.execute(
            """
            SELECT latitude, longitude, speed_kmph, status, location_text,
                   distance_from_prev, recorded_at
            FROM waypoints
            WHERE trip_id = %s
            ORDER BY recorded_at
            """,
            (trip_id,),
        )
        waypoints = cur.fetchall()

        # Fallback: TTA-ingested trips keep their GPS in tta_trip_gps
        # (dispatch_entry_no == TTA i_trip_no). Serve it in the same shape,
        # evenly decimated so the page stays fast.
        if not waypoints and str(trip.get("dispatch_entry_no", "")).isdigit():
            tta_no = int(trip["dispatch_entry_no"])
            cur.execute("SELECT COUNT(*) AS cnt FROM tta_trip_gps WHERE i_trip_no = %s", (tta_no,))
            total = cur.fetchone()["cnt"]
            if total:
                step = max(total // 500, 1)
                cur.execute(
                    """
                    SELECT latitude, longitude, speed_kmph, status, location_text,
                           distance_from_prev, recorded_at
                    FROM (
                        SELECT d_lat AS latitude, d_long AS longitude,
                               COALESCE(i_status_speed_kmph, i_speed) AS speed_kmph,
                               s_status AS status,
                               CONCAT(COALESCE(s_wpnt1, ''),
                                      IF(s_wpnt1_st_abbr IS NULL, '', CONCAT(' (', s_wpnt1_st_abbr, ')'))) AS location_text,
                               ROUND(i_dist / 1000, 2) AS distance_from_prev,
                               dt_message AS recorded_at,
                               ROW_NUMBER() OVER (ORDER BY dt_message) AS rn
                        FROM tta_trip_gps
                        WHERE i_trip_no = %s
                    ) x
                    WHERE (rn - 1) %% %s = 0 OR rn = %s
                    ORDER BY recorded_at
                    """,
                    (tta_no, step, total),
                )
                waypoints = cur.fetchall()

        # ── Driver performance summary ──
        driver_stats = None
        if trip.get("driver_id"):
            cur.execute(
                f"SELECT * FROM driver_summary WHERE driver_id = %s AND {cnr_sum}",
                [trip["driver_id"]] + cnr_sum_p,
            )
            driver_stats = cur.fetchone()

        # ── Route performance summary ──
        route_stats = None
        origin_name = trip.get("origin_name") or ""
        dest_name = trip.get("destination_name") or ""
        if origin_name and dest_name:
            cur.execute(
                f"SELECT * FROM route_summary WHERE origin = %s AND destination = %s AND {cnr_sum}",
                [origin_name, dest_name] + cnr_sum_p,
            )
            route_stats = cur.fetchone()

        # ── Vehicle performance summary ──
        vehicle_stats = None
        if trip.get("vehicle_id"):
            cur.execute(
                f"SELECT * FROM vehicle_summary WHERE vehicle_id = %s AND {cnr_sum}",
                [trip["vehicle_id"]] + cnr_sum_p,
            )
            vehicle_stats = cur.fetchone()

        # ── Driver's history on this exact route ──
        driver_route_stats = None
        if trip.get("driver_id") and origin_name and dest_name:
            cur.execute(
                f"""
                SELECT COUNT(*) AS route_trips,
                       ROUND(AVG(trip_duration_minutes), 2) AS avg_duration_min,
                       ROUND(AVG(avg_speed_kmph), 2) AS avg_speed_kmph,
                       ROUND(SUM(CASE WHEN eta_met = 1 THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(eta_met), 0), 2) AS eta_success_rate,
                       ROUND(AVG(trip_km), 2) AS avg_distance_km
                FROM trips t
                JOIN locations lo ON t.origin_id = lo.id
                JOIN locations ld ON t.destination_id = ld.id
                WHERE t.driver_id = %s AND lo.name = %s AND ld.name = %s
                  AND t.trip_duration_minutes > 0{tfilter}
                """,
                [trip["driver_id"], origin_name, dest_name] + tparams,
            )
            driver_route_stats = cur.fetchone()
            if driver_route_stats and driver_route_stats.get("route_trips", 0) == 0:
                driver_route_stats = None

        # ── Recent trips on same route (for comparison) ──
        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, d.name AS driver_name,
                   t.trip_start, t.trip_duration_minutes, t.avg_speed_kmph,
                   t.eta_met, t.trip_km
            FROM trips t
            LEFT JOIN drivers d ON t.driver_id = d.id
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE lo.name = %s AND ld.name = %s AND t.id != %s
              AND t.trip_duration_minutes > 0{tfilter}
            ORDER BY t.trip_start DESC
            LIMIT 10
            """,
            [origin_name, dest_name, trip_id] + tparams,
        )
        route_recent_trips = cur.fetchall()

    # Convert datetimes to strings
    for key in trip:
        if hasattr(trip[key], "isoformat"):
            trip[key] = str(trip[key])

    for w in waypoints:
        w["recorded_at"] = str(w["recorded_at"]) if w["recorded_at"] else None

    for t_row in route_recent_trips:
        t_row["trip_start"] = str(t_row["trip_start"]) if t_row["trip_start"] else None

    if driver_stats and driver_stats.get("last_refreshed"):
        driver_stats["last_refreshed"] = str(driver_stats["last_refreshed"])
    if route_stats and route_stats.get("last_refreshed"):
        route_stats["last_refreshed"] = str(route_stats["last_refreshed"])
    if vehicle_stats and vehicle_stats.get("last_refreshed"):
        vehicle_stats["last_refreshed"] = str(vehicle_stats["last_refreshed"])

    return {
        "trip": trip,
        "waypoints": waypoints,
        "driver_stats": driver_stats,
        "route_stats": route_stats,
        "vehicle_stats": vehicle_stats,
        "driver_route_stats": driver_route_stats,
        "route_recent_trips": route_recent_trips,
    }