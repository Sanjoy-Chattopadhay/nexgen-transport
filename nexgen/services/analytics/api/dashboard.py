from fastapi import APIRouter, Depends
from typing import List
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import (ConsignorScope, consignor_scope, base_clause,
                                        summary_clause)
from nexgen.shared.common.sql import and_where, date_frags
from nexgen.shared.common.cache import cache_get_or_set, make_key
from nexgen.services.analytics.schemas.dashboard import FleetSummary, DailyTrend, AlertOut

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# Dashboard aggregates run over the whole (huge) trips table; the same 7-day
# default window is requested on every page load, so results are cached for a
# few minutes and refresh on their own as the TTL lapses. See core/cache.py.
DASH_TTL = 600  # seconds (10 min)


@router.get("/summary", response_model=FleetSummary)
def get_fleet_summary(date_from: str = "", date_to: str = "",
                      scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Fleet-wide totals (trips, drivers, vehicles, distance, avg speed, ETA rate).

    Optional `date_from`/`date_to` (YYYY-MM-DD) restrict to trips departing in that
    window; omitted → all-time. Cached per (range, consignor).
    """
    where, params = and_where(base_clause(scope, "cnr_id"),
                              *date_frags("trip_start", date_from, date_to))

    def produce():
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*) AS total_trips,
                    COUNT(DISTINCT driver_id) AS total_drivers,
                    COUNT(DISTINCT vehicle_id) AS total_vehicles,
                    ROUND(SUM(trip_km), 2) AS total_distance_km,
                    ROUND(AVG(avg_speed_kmph), 2) AS avg_speed_kmph,
                    ROUND(SUM(CASE WHEN eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(eta_met), 0) * 100, 2) AS eta_success_rate
                FROM trips
                {where}
            """, params)
            return cur.fetchone()

    row = cache_get_or_set(
        make_key("dashboard:summary", date_from, date_to, scope.summary_id), produce, DASH_TTL)
    return FleetSummary(**row)


@router.get("/daily-trend", response_model=List[DailyTrend])
def get_daily_trend(days: int = 30, date_from: str = "", date_to: str = "",
                    scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Daily fleet stats. With `date_from`/`date_to` → that span in ascending order;
    otherwise the most recent `days` days."""
    clause, cparams = summary_clause(scope)
    conds = [clause]
    params = list(cparams)
    if date_from:
        conds.append("stat_date >= %s")
        params.append(date_from)
    if date_to:
        conds.append("stat_date <= %s")
        params.append(date_to)
    where_sql = " AND ".join(conds)

    def produce():
        with conn.cursor() as cur:
            if date_from or date_to:
                cur.execute(f"""
                    SELECT stat_date, total_trips, total_distance_km, avg_speed,
                           eta_success_rate, active_drivers, active_vehicles
                    FROM daily_fleet_stats
                    WHERE {where_sql}
                    ORDER BY stat_date
                """, params)
                return cur.fetchall()
            cur.execute(f"""
                SELECT stat_date, total_trips, total_distance_km, avg_speed,
                       eta_success_rate, active_drivers, active_vehicles
                FROM daily_fleet_stats
                WHERE {where_sql}
                ORDER BY stat_date DESC
                LIMIT %s
            """, params + [days])
            return list(reversed(cur.fetchall()))

    rows = cache_get_or_set(
        make_key("dashboard:daily-trend", days, date_from, date_to, scope.summary_id), produce, DASH_TTL)
    return [DailyTrend(**r) for r in rows]


@router.get("/top-drivers")
def get_top_drivers(limit: int = 10, date_from: str = "", date_to: str = "",
                    scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Top drivers by trip count. With a date range → recomputed live from `trips`
    in that window; otherwise the all-time `driver_summary` fast path."""
    def produce():
        with conn.cursor() as cur:
            if date_from or date_to:
                where, params = and_where(
                    base_clause(scope, "cnr_id", "t"),
                    ("t.driver_id IS NOT NULL", []),
                    *date_frags("t.trip_start", date_from, date_to),
                )
                cur.execute(f"""
                    SELECT t.driver_id, d.name AS driver_name, d.mobile1 AS driver_mobile,
                           COUNT(*) AS total_trips,
                           ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2) AS eta_success_rate,
                           ROUND(AVG(t.avg_speed_kmph), 2) AS avg_speed_kmph,
                           ROUND(SUM(t.trip_km), 2) AS total_distance_km
                    FROM trips t
                    JOIN drivers d ON d.id = t.driver_id
                    {where}
                    GROUP BY t.driver_id, d.name, d.mobile1
                    ORDER BY total_trips DESC
                    LIMIT %s
                """, params + [limit])
                return cur.fetchall()
            clause, cparams = summary_clause(scope)
            cur.execute(f"""
                SELECT driver_id, driver_name, driver_mobile, total_trips,
                       eta_success_rate, avg_speed_kmph, total_distance_km
                FROM driver_summary
                WHERE {clause} AND total_trips >= 5
                ORDER BY total_trips DESC
                LIMIT %s
            """, cparams + [limit])
            return cur.fetchall()

    return cache_get_or_set(
        make_key("dashboard:top-drivers", limit, date_from, date_to, scope.summary_id), produce, DASH_TTL)


@router.get("/route-heatmap")
def get_route_heatmap(limit: int = 20, date_from: str = "", date_to: str = "",
                      scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Busiest origin→destination pairs. With a date range → recomputed live from
    `trips` joined to locations; otherwise the all-time `route_summary` fast path."""
    def produce():
        with conn.cursor() as cur:
            if date_from or date_to:
                where, params = and_where(
                    base_clause(scope, "cnr_id", "t"),
                    ("t.origin_id IS NOT NULL", []),
                    ("t.destination_id IS NOT NULL", []),
                    *date_frags("t.trip_start", date_from, date_to),
                )
                cur.execute(f"""
                    SELECT o.name AS origin, dst.name AS destination,
                           CONCAT(o.name, ' → ', dst.name) AS route_name,
                           COUNT(*) AS trip_count,
                           ROUND(AVG(t.trip_duration_minutes), 2) AS avg_duration_min,
                           ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2) AS eta_success_rate,
                           ROUND(AVG(t.trip_km), 2) AS avg_distance_km
                    FROM trips t
                    JOIN locations o ON o.id = t.origin_id
                    JOIN locations dst ON dst.id = t.destination_id
                    {where}
                    GROUP BY o.name, dst.name
                    ORDER BY trip_count DESC
                    LIMIT %s
                """, params + [limit])
                return cur.fetchall()
            clause, cparams = summary_clause(scope)
            cur.execute(f"""
                SELECT origin, destination, route_name, trip_count,
                       avg_duration_min, eta_success_rate, avg_distance_km
                FROM route_summary
                WHERE {clause}
                ORDER BY trip_count DESC
                LIMIT %s
            """, cparams + [limit])
            return cur.fetchall()

    return cache_get_or_set(
        make_key("dashboard:route-heatmap", limit, date_from, date_to, scope.summary_id), produce, DASH_TTL)


# ── Drill-down lists ──────────────────────────────────────────────────────────
# Back the clickable KPI counts on the Dashboard. Each returns the actual entities
# behind a count, scoped to the same date window + consignor, so "N active drivers"
# opens the exact N drivers. Rows link to their detail pages on the frontend.

@router.get("/active-drivers")
def get_active_drivers(date_from: str = "", date_to: str = "", limit: int = 200,
                       scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Drivers with ≥1 trip in the window — backs the 'Active Drivers' KPI
    drill-down. Each row → /drivers/:driver_id."""
    where, params = and_where(
        base_clause(scope, "cnr_id", "t"),
        ("t.driver_id IS NOT NULL", []),
        *date_frags("t.trip_start", date_from, date_to),
    )

    def produce():
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT t.driver_id, d.name AS driver_name, d.mobile1 AS driver_mobile,
                       COUNT(*) AS total_trips,
                       ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2) AS eta_success_rate,
                       ROUND(SUM(t.trip_km), 2) AS total_distance_km
                FROM trips t JOIN drivers d ON d.id = t.driver_id
                {where}
                GROUP BY t.driver_id, d.name, d.mobile1
                ORDER BY total_trips DESC
                LIMIT %s
            """, params + [limit])
            return cur.fetchall()

    return cache_get_or_set(
        make_key("dashboard:active-drivers", date_from, date_to, limit, scope.summary_id), produce, DASH_TTL)


@router.get("/active-vehicles")
def get_active_vehicles(date_from: str = "", date_to: str = "", limit: int = 200,
                        scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Vehicles with ≥1 trip in the window — backs the 'Vehicles' KPI drill-down.
    Each row → /vehicles/:vehicle_id."""
    where, params = and_where(
        base_clause(scope, "cnr_id", "t"),
        ("t.vehicle_id IS NOT NULL", []),
        *date_frags("t.trip_start", date_from, date_to),
    )

    def produce():
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT t.vehicle_id, v.asset_id, v.asset_type,
                       COUNT(*) AS total_trips,
                       ROUND(SUM(t.trip_km), 2) AS total_distance_km,
                       ROUND(AVG(t.avg_speed_kmph), 2) AS avg_speed_kmph
                FROM trips t JOIN vehicles v ON v.id = t.vehicle_id
                {where}
                GROUP BY t.vehicle_id, v.asset_id, v.asset_type
                ORDER BY total_trips DESC
                LIMIT %s
            """, params + [limit])
            return cur.fetchall()

    return cache_get_or_set(
        make_key("dashboard:active-vehicles", date_from, date_to, limit, scope.summary_id), produce, DASH_TTL)


@router.get("/recent-trips")
def get_recent_trips(date_from: str = "", date_to: str = "", limit: int = 200,
                     scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Trips in the window — backs the 'Total Trips' KPI drill-down. Each row →
    /trips/:trip_no (dispatch entry no.)."""
    where, params = and_where(
        base_clause(scope, "cnr_id", "t"),
        *date_frags("t.trip_start", date_from, date_to),
    )

    def produce():
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT t.id, t.dispatch_entry_no, t.trip_start, t.trip_status,
                       t.eta_met, t.trip_km, t.avg_speed_kmph,
                       d.name AS driver_name, v.asset_id AS vehicle,
                       o.name AS origin, dst.name AS destination
                FROM trips t
                LEFT JOIN drivers d ON d.id = t.driver_id
                LEFT JOIN vehicles v ON v.id = t.vehicle_id
                LEFT JOIN locations o ON o.id = t.origin_id
                LEFT JOIN locations dst ON dst.id = t.destination_id
                {where}
                ORDER BY t.trip_start DESC
                LIMIT %s
            """, params + [limit])
            rows = cur.fetchall()
        for r in rows:
            r["trip_start"] = str(r["trip_start"]) if r["trip_start"] else None
            r["eta_met"] = bool(r["eta_met"]) if r["eta_met"] is not None else None
        return rows

    return cache_get_or_set(
        make_key("dashboard:recent-trips", date_from, date_to, limit, scope.summary_id), produce, DASH_TTL)


@router.get("/alerts/recent", response_model=List[AlertOut])
def get_recent_alerts(limit: int = 20, scope: ConsignorScope = Depends(consignor_scope),
                      conn=Depends(get_db)):
    # The alerts table has no consignor column of its own; an alert's owner is
    # the consignor of its trip. When scoped we INNER JOIN trips and filter that
    # consignor, so a scoped caller only ever sees its own trip-linked alerts.
    cnr_clause, cnr_params = base_clause(scope, "cnr_id", alias="t")
    if cnr_clause:
        sql = f"""
            SELECT a.id, a.alert_type, a.severity, a.title, a.message, a.trip_id,
                   a.driver_id, a.vehicle_id, a.is_acknowledged, a.created_at
            FROM alerts a
            JOIN trips t ON t.id = a.trip_id
            WHERE {cnr_clause}
            ORDER BY a.created_at DESC
            LIMIT %s
        """
        params = cnr_params + [limit]
    else:
        sql = """
            SELECT id, alert_type, severity, title, message, trip_id,
                   driver_id, vehicle_id, is_acknowledged, created_at
            FROM alerts
            ORDER BY created_at DESC
            LIMIT %s
        """
        params = [limit]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    results = []
    for r in rows:
        r["created_at"] = str(r["created_at"]) if r["created_at"] else None
        r["is_acknowledged"] = bool(r["is_acknowledged"])
        results.append(AlertOut(**r))
    return results
