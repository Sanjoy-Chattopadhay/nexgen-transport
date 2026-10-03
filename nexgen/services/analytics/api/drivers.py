import logging
import math
from typing import Optional

from fastapi import APIRouter, Depends, Query
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import (ConsignorScope, consignor_scope, base_clause,
                                        summary_clause)
from nexgen.shared.common.sql import date_frags
from nexgen.services.analytics.schemas.driver import DriverSummaryOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drivers", tags=["Drivers"])


def _compute_scores(rows: list) -> list:
    """Compute composite driver score for every row using Bayesian-smoothed ETA
    + experience + speed safety.  Same formula as ml_service driver_scorer."""
    if not rows:
        return rows

    # Global priors. A NULL eta_success_rate means the driver has no trip with
    # a known outcome — unknown, not 0% — so it is left out of the fleet
    # average here and falls back to that average per driver below.
    known_eta = [float(r["eta_success_rate"]) for r in rows
                 if r.get("eta_success_rate") is not None]
    all_trips = [int(r.get("total_trips") or 0) for r in rows]

    global_eta_avg = sum(known_eta) / len(known_eta) if known_eta else 55.0
    max_trips = max(all_trips) if all_trips else 1
    confidence_trips = 15.0  # Bayesian smoothing constant

    for r in rows:
        raw = r.get("eta_success_rate")
        eta_raw = float(raw) if raw is not None else global_eta_avg
        n_trips = int(r.get("total_trips") or 0)
        speed = float(r.get("avg_speed_kmph") or 0)

        # 1. Bayesian-smoothed ETA (0-100)
        eta_score = (n_trips * eta_raw + confidence_trips * global_eta_avg) / (n_trips + confidence_trips)

        # 2. Experience (0-100) — log-scaled so diminishing returns
        import math as _m
        exp_score = min(100, (_m.log1p(n_trips) / _m.log1p(max_trips)) * 100) if max_trips > 0 else 0

        # 3. Speed safety (0-100) — ideal 20-40 km/h for trucks
        if 20 <= speed <= 40:
            speed_score = 100.0
        elif speed < 20:
            speed_score = max(0, speed / 20 * 100)
        else:
            speed_score = max(0, 100 - (speed - 40) * 2.5)

        # Composite: ETA 35%, Experience 25%, Speed 20%, base 20% (ETA raw scaled)
        composite = (
            eta_score * 0.35
            + exp_score * 0.25
            + speed_score * 0.20
            + min(eta_raw, 100) * 0.20
        )
        r["composite_score"] = round(composite, 1)

    return rows


@router.get("")
def list_drivers(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    sort_by: str = "total_trips",
    sort_order: str = "desc",
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    offset = (page - 1) * limit
    allowed_sorts = {
        "total_trips", "eta_success_rate", "avg_speed_kmph",
        "total_distance_km", "driver_name", "avg_duration_min",
    }
    if sort_by not in allowed_sorts:
        sort_by = "total_trips"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    cnr_clause, params = summary_clause(scope)
    conditions = [cnr_clause]
    if search:
        conditions.append("(driver_name LIKE %s OR driver_mobile LIKE %s)")
        params = params + [f"%{search}%", f"%{search}%"]
    where = "WHERE " + " AND ".join(conditions)

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM driver_summary {where}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT driver_id, driver_name, driver_mobile, total_trips,
                   eta_met_count, eta_known_count, eta_success_rate, avg_duration_min,
                   max_duration_min, min_duration_min, avg_speed_kmph,
                   vehicles_used, total_distance_km, avg_distance_km, avg_eta_delay_min
            FROM driver_summary
            {where}
            ORDER BY {sort_by} {sort_order}
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = cur.fetchall()

    # Compute composite score for every driver in the page
    rows = _compute_scores(rows)

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": math.ceil(total / limit) if limit else 0,
    }


@router.get("/{driver_id}")
def get_driver_detail(driver_id: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    cnr_sum, cnr_sum_p = summary_clause(scope)
    tclause, tparams = base_clause(scope, "cnr_id", alias="t")
    tfilter = f" AND {tclause}" if tclause else ""
    with conn.cursor() as cur:
        # Driver summary (one row: the rollup, or the per-consignor slice)
        cur.execute(
            f"SELECT * FROM driver_summary WHERE driver_id = %s AND {cnr_sum}",
            [driver_id] + cnr_sum_p,
        )
        summary = cur.fetchone()
        if not summary:
            return {"error": "Driver not found"}

        # Compute score for this driver
        if summary:
            _compute_scores([summary])

        # Recent trips
        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, lo.name AS origin_name, ld.name AS destination_name,
                   t.trip_start,
                   t.ata_in AS trip_end,
                   TIMESTAMPDIFF(MINUTE, t.trip_start, t.ata_in) AS trip_duration_minutes,
                   t.eta_met,
                   t.avg_speed_kmph, t.trip_km, t.trip_status,
                   v.asset_id
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN vehicles v ON t.vehicle_id = v.id
            WHERE t.driver_id = %s{tfilter}
            ORDER BY t.trip_start DESC
            LIMIT 20
            """,
            [driver_id] + tparams,
        )
        recent_trips = cur.fetchall()

        # Vehicles used
        cur.execute(
            f"""
            SELECT DISTINCT v.id, v.asset_id, v.asset_type, COUNT(*) AS trip_count
            FROM trips t
            JOIN vehicles v ON t.vehicle_id = v.id
            WHERE t.driver_id = %s{tfilter}
            GROUP BY v.id, v.asset_id, v.asset_type
            ORDER BY trip_count DESC
            """,
            [driver_id] + tparams,
        )
        vehicles_used = cur.fetchall()

        # Frequent routes
        cur.execute(
            f"""
            SELECT lo.name AS origin, ld.name AS destination, COUNT(*) AS trip_count
            FROM trips t
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE t.driver_id = %s{tfilter}
            GROUP BY lo.name, ld.name
            ORDER BY trip_count DESC
            LIMIT 10
            """,
            [driver_id] + tparams,
        )
        frequent_routes = cur.fetchall()

    for t in recent_trips:
        t["trip_start"] = str(t["trip_start"]) if t["trip_start"] else None
        t["trip_end"] = str(t["trip_end"]) if t["trip_end"] else None

    return {
        "summary": summary,
        "recent_trips": recent_trips,
        "vehicles_used": vehicles_used,
        "frequent_routes": frequent_routes,
    }


@router.get("/{driver_id}/trips")
def get_driver_trips(
    driver_id: int,
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
            f"SELECT COUNT(*) AS cnt FROM trips t WHERE t.driver_id = %s{tfilter}",
            [driver_id] + tparams,
        )
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT t.id, t.dispatch_entry_no, lo.name AS origin_name, ld.name AS destination_name,
                   t.trip_start,
                   t.ata_in AS trip_end,
                   TIMESTAMPDIFF(MINUTE, t.trip_start, t.ata_in) AS trip_duration_minutes,
                   t.eta_met,
                   t.avg_speed_kmph, t.trip_km, t.trip_status, v.asset_id
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN vehicles v ON t.vehicle_id = v.id
            WHERE t.driver_id = %s{tfilter}
            ORDER BY t.trip_start DESC
            LIMIT %s OFFSET %s
            """,
            [driver_id] + tparams + [limit, offset],
        )
        rows = cur.fetchall()

    for r in rows:
        r["trip_start"] = str(r["trip_start"]) if r["trip_start"] else None
        r["trip_end"] = str(r["trip_end"]) if r["trip_end"] else None

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": math.ceil(total / limit) if limit else 0,
    }


@router.get("/{driver_id}/trend")
def get_driver_trend(
    driver_id: int,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    group_by: str = Query("month", regex="^(month|week|day)$"),
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """Get driver performance trend. Supports date range filtering and grouping by month/week/day."""
    where_parts = ["driver_id = %s", "trip_start IS NOT NULL"]
    params: list = [driver_id]

    cnr_clause, cnr_params = base_clause(scope, "cnr_id")
    if cnr_clause:
        where_parts.append(cnr_clause)
        params.extend(cnr_params)

    for clause, p in date_frags("trip_start", date_from or "", date_to or ""):
        where_parts.append(clause)
        params.extend(p)

    where_clause = " AND ".join(where_parts)

    if group_by == "day":
        date_expr = "DATE(trip_start)"
        date_format = "DATE_FORMAT(trip_start, '%%Y-%%m-%%d')"
    elif group_by == "week":
        date_expr = "YEARWEEK(trip_start, 1)"
        date_format = "DATE_FORMAT(MIN(trip_start), '%%Y-W%%v')"
    else:
        date_expr = "DATE_FORMAT(trip_start, '%%Y-%%m')"
        date_format = "DATE_FORMAT(trip_start, '%%Y-%%m')"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                {date_format} AS period,
                COUNT(*) AS trip_count,
                ROUND(AVG(trip_duration_minutes), 2) AS avg_duration,
                ROUND(SUM(CASE WHEN eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(eta_met), 0) * 100, 2) AS eta_success_rate,
                ROUND(AVG(avg_speed_kmph), 2) AS avg_speed,
                ROUND(AVG(trip_km), 2) AS avg_distance,
                ROUND(AVG(eta_delay_minutes), 2) AS avg_delay
            FROM trips
            WHERE {where_clause}
            GROUP BY {date_expr}
            ORDER BY {date_expr}
            """,
            params,
        )
        rows = cur.fetchall()
    return rows


@router.get("/{driver_id}/driving-pattern")
def get_driver_driving_pattern(
    driver_id: int,
    window: str = Query("all", regex="^(all|7d|15d|30d|90d|last3|last5|last10)$"),
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """Driving pattern (hour-of-day travel share, speed & day-of-week profile)
    computed from the trip-wise GPS of the trips this driver drove."""
    from nexgen.services.analytics.lib.driver_insights import build_driving_pattern
    return build_driving_pattern(conn, driver_id, window, cnr_id=scope.id)


@router.get("/{driver_id}/alerts")
def get_driver_alerts(
    driver_id: int,
    window: str = Query("all", regex="^(all|7d|15d|30d|90d|last3|last5|last10)$"),
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """Safety alerts (overspeed, harsh driving, long stops, night driving)
    derived from the driver's GPS pings and merged onto this driver."""
    from nexgen.services.analytics.lib.driver_insights import build_driver_alerts
    return build_driver_alerts(conn, driver_id, window, cnr_id=scope.id)
