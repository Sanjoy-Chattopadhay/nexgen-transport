"""TTA Master Reporting dashboard API.

Every figure of the TTA analytics dashboard (KPIs, trends, transporter
scorecard, lanes, geo map, heatmaps, correlation, fleet, distributions,
outliers, funnel, raw records) is served from here.

Shared filters (all endpoints):
    date_from / date_to          YYYY-MM-DD (dept date, inclusive)
    transporters / destinations / vehicle_categories / own_market /
    consignors                   ||-separated multi-values
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.trip_class import TripClassScope, trip_class_scope
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope
from nexgen.services.analytics.lib import tta_dashboard as svc

router = APIRouter(prefix="/tta/dashboard", tags=["TTA Dashboard"])


def dashboard_filters(
    date_from: str = "",
    date_to: str = "",
    transporters: str = "",
    destinations: str = "",
    vehicle_categories: str = "",
    own_market: str = "",
    consignors: str = "",
    # Additional exact-match list filters (all ||-joined, e.g. drivers=A||B).
    consignees: str = "",       # customer receiving the load (s_cne_name; ≠ destination city)
    vehicles: str = "",         # specific vehicle numbers (s_asset_id)
    drivers: str = "",          # specific driver names (s_driver_name)
    device_types: str = "",     # GPS/device type (m.s_tag)
    asset_makes: str = "",      # vehicle make (m.s_asset_make)
    scope: ConsignorScope = Depends(consignor_scope),
    tclass: TripClassScope = Depends(trip_class_scope),
) -> dict:
    def multi(v: str):
        return [x for x in v.split("||") if x.strip()] if v else None

    # A route-based consignor scope is authoritative and enforced by id
    # (cnr_id) — a scoped caller can never widen to other consignors, so any
    # consignor picked in the dashboard filter bar is ignored while scoped.
    return {
        "cnr_id": scope.id if scope.active else None,
        # Upstream classification (zonal / local); None = both.
        "trip_class": tclass.value,
        "date_from": date_from or None,
        "date_to": date_to or None,
        "transporters": multi(transporters),
        "destinations": multi(destinations),
        "vehicle_categories": multi(vehicle_categories),
        "own_market": multi(own_market),
        "consignors": None if scope.active else multi(consignors),
        "consignees": multi(consignees),
        "vehicles": multi(vehicles),
        "drivers": multi(drivers),
        "device_types": multi(device_types),
        "asset_makes": multi(asset_makes),
    }


@router.get("/meta")
def dashboard_meta(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Filter option lists + dataset bounds (scoped to the consignor when set).

    Only the consignor scope is applied here — never the transient date/
    transporter picks — so the dropdowns list every option available to this
    consignor, not just those matching the current filter selection.
    """
    return svc.meta(conn, {"cnr_id": scope.id} if scope.active else None)


@router.get("/kpis")
def dashboard_kpis(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """KPI block + previous-equal-window deltas."""
    return svc.kpis(conn, f)


@router.get("/timeseries")
def dashboard_timeseries(granularity: str = Query("D", pattern="^[DWM]$"),
                         f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Daily/weekly/monthly trend aggregates (volume, OTD, transit, detention,
    km, violations, dispatch lead)."""
    return {"granularity": granularity, "series": svc.timeseries(conn, f, granularity)}


@router.get("/group")
def dashboard_group(by: str = "transporter", min_trips: int = 1,
                    f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Full scorecard per transporter / destination / vehicle_category /
    own_market / consignor / consignee (customer) / ship_to_site / device_type.

    `by` accepts UI aliases: customer -> consignee, ship-to-site -> ship_to_site.
    Each row carries volume (trips), otd_pct, transit, detention, distance, speed,
    GPS uptime and violations — enough for the "worst OTD" and scorecard panels.
    """
    by = svc.resolve_group_field(by)
    if by not in svc.GROUP_FIELDS:
        raise HTTPException(400, f"by must be one of {svc.GROUP_FIELDS}")
    return {"by": by, "rows": svc.group_summary(conn, f, by, min_trips)}


@router.get("/funnel")
def dashboard_funnel(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Trip lifecycle milestone funnel."""
    return {"stages": svc.funnel(conn, f)}


@router.get("/geo")
def dashboard_geo(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Destination bubbles (offline-geocoded) + unmapped list + origin."""
    return svc.geo_points(conn, f)


@router.get("/geo/states")
def dashboard_geo_states(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Volume and on-time rolled up to destination STATE, with centroids.

    The state grain is what makes the bubble maps readable: 126 destinations
    drawn on a country map overlap into noise, and most carry too few trips for
    an on-time rate to mean anything. Each row carries a Wilson interval on OTD
    so the map can decline to colour a state it has only four trips of evidence
    about.
    """
    return svc.geo_states(conn, f)


@router.get("/heatmap/dow-hour")
def dashboard_heatmap_dow_hour(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Departure rhythm: weekday x hour trip counts."""
    return svc.heatmap_dow_hour(conn, f)


@router.get("/heatmap/pivot")
def dashboard_heatmap_pivot(rows: str = "transporter", cols: str = "dept_month",
                            metric: str = "otd_pct", top: int = 12,
                            f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Generic top-N rows x period heatmap for a chosen metric."""
    if rows not in svc.GROUP_FIELDS:
        raise HTTPException(400, f"rows must be one of {svc.GROUP_FIELDS}")
    if cols not in ("dept_month", "dept_week", "dept_date", "dept_dow", "dept_hour"):
        raise HTTPException(400, "cols must be a calendar key")
    return svc.heatmap_pivot(conn, f, rows, cols, metric, min(max(top, 3), 30))


@router.get("/correlation")
def dashboard_correlation(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Pearson correlation matrix over the numeric trip metrics."""
    return svc.correlation(conn, f)


@router.get("/distribution")
def dashboard_distribution(metric: str = "transit_hours",
                           f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Summary stats + histogram bins + ECDF for one metric."""
    if metric not in svc.NUMERIC_METRICS:
        raise HTTPException(400, f"metric must be one of {svc.NUMERIC_METRICS}")
    return {"metric": metric, **svc.distribution(conn, f, metric)}


@router.get("/boxplot")
def dashboard_boxplot(group_by: str = "transporter", metric: str = "transit_hours",
                      top: int = 10, f: dict = Depends(dashboard_filters),
                      conn=Depends(get_db)):
    """Per-group box-plot statistics (quartiles, whiskers, outlier sample)."""
    if group_by not in svc.GROUP_FIELDS:
        raise HTTPException(400, f"group_by must be one of {svc.GROUP_FIELDS}")
    if metric not in svc.NUMERIC_METRICS:
        raise HTTPException(400, f"metric must be one of {svc.NUMERIC_METRICS}")
    return {"group_by": group_by, "metric": metric,
            "groups": svc.boxplot(conn, f, group_by, metric, min(max(top, 2), 20))}


@router.get("/fleet")
def dashboard_fleet(f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Own vs market, vehicle categories, device types, asset makes,
    top violating + low-GPS vehicles."""
    return svc.fleet(conn, f)


@router.get("/outliers")
def dashboard_outliers(z: float = 3.0, min_lane_trips: int = 8,
                       f: dict = Depends(dashboard_filters), conn=Depends(get_db)):
    """Per-lane z-score transit-time anomalies."""
    return {"z_threshold": z,
            "rows": svc.outliers(conn, f, max(min(z, 6.0), 1.0), max(min_lane_trips, 2))}


@router.get("/records")
def dashboard_records(limit: int = 500, f: dict = Depends(dashboard_filters),
                      conn=Depends(get_db)):
    """Raw filtered trip records (newest first, capped at 5000)."""
    return svc.records(conn, f, limit)
