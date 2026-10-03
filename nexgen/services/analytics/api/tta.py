"""
TTA Trip + GPS API endpoints.

Workflow:
    1. POST /api/v1/tta/schema            -> Create TTA tables (idempotent)
    2. POST /api/v1/tta/upload            -> Upload a TTA export file (json/txt) and ingest
    3. POST /api/v1/tta/ingest-path       -> Ingest a file already on the server
    4. GET  /api/v1/tta/progress          -> Poll ingestion progress
    5. GET  /api/v1/tta/status            -> Row counts for TTA tables
    6. GET  /api/v1/tta/trips             -> Paginated trips (with GPS ping counts)
    7. GET  /api/v1/tta/trips/{trip_no}   -> Trip detail + metrics + GPS summary
    8. GET  /api/v1/tta/trips/{trip_no}/gps -> GPS points (optionally decimated)
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from nexgen.shared.legacy_settings import settings
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.trip_class import TripClassScope, trip_class_scope, class_clause
from nexgen.shared.common.consignor import (
    ConsignorScope,
    consignor_scope,
    base_clause,
    enforce_tta_trip_scope,
)
from nexgen.shared.feed.tta import (
    get_tta_progress,
    ingest_tta_file,
    run_tta_schema,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tta", tags=["TTA Trips & GPS"])

TTA_TABLES = ["consignors", "tta_trips", "tta_trip_metrics", "tta_trip_gps"]


@router.post("/schema")
def create_tta_schema(conn=Depends(get_db)):
    """Create TTA tables (idempotent — safe to call multiple times)."""
    return run_tta_schema(conn)


@router.post("/upload")
async def upload_tta_file(file: UploadFile = File(...), conn=Depends(get_db)):
    """Upload a TTA export (sectioned text or JSON) and ingest it."""
    if not file.filename:
        raise HTTPException(400, "No file provided")

    upload_dir = Path(settings.UPLOAD_DIR) / "tta"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{datetime.now():%Y%m%d_%H%M%S}_{os.path.basename(file.filename)}"
    dest = upload_dir / stored_name

    content = await file.read()
    dest.write_bytes(content)

    # Track in the existing file_uploads table (existing upload procedure)
    ext = (os.path.splitext(file.filename)[1].lstrip(".") or "json")[:10]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO file_uploads
               (original_filename, stored_filename, file_type, file_size_bytes,
                upload_type, status, processing_started_at)
               VALUES (%s, %s, %s, %s, 'tta_json', 'processing', NOW())""",
            (file.filename, str(dest), ext, len(content)),
        )
        upload_id = cur.lastrowid
    conn.commit()

    try:
        result = ingest_tta_file(conn, str(dest))
        from nexgen.services.analytics.lib.tta_dashboard import invalidate_cache
        invalidate_cache()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE file_uploads
                   SET status='completed', total_records=%s, records_processed=%s,
                       records_failed=%s, processing_completed_at=NOW()
                   WHERE id=%s""",
                (
                    result.get("gps_inserted", 0) + result.get("gps_skipped", 0),
                    result.get("gps_inserted", 0),
                    len(result.get("errors", [])),
                    upload_id,
                ),
            )
        conn.commit()
        result["upload_id"] = upload_id
        return result
    except Exception as e:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE file_uploads SET status='failed', error_summary=JSON_OBJECT('error', %s) WHERE id=%s",
                (str(e)[:500], upload_id),
            )
        conn.commit()
        raise HTTPException(422, f"Ingestion failed: {e}")


class IngestPathBody(BaseModel):
    path: str


@router.post("/ingest-path")
def ingest_from_path(body: IngestPathBody, conn=Depends(get_db)):
    """Ingest a TTA export file already present on the server (dev helper)."""
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {p}")
    try:
        result = ingest_tta_file(conn, str(p))
        from nexgen.services.analytics.lib.tta_dashboard import invalidate_cache
        invalidate_cache()
        return result
    except Exception as e:
        raise HTTPException(422, f"Ingestion failed: {e}")


@router.get("/progress")
def tta_progress():
    """Poll ingestion progress."""
    return get_tta_progress()


@router.get("/status")
def tta_status(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Row counts for TTA tables (scoped to one consignor when a consignor id is given)."""
    counts = {}
    # When scoped, count only this consignor's rows. tta_trips carries i_cnr_id;
    # metrics/gps reach it by joining tta_trips on i_trip_no.
    scoped_sql = {
        "consignors": "SELECT COUNT(*) AS cnt FROM consignors WHERE i_cnr_id = %s",
        "tta_trips": "SELECT COUNT(*) AS cnt FROM tta_trips WHERE i_cnr_id = %s",
        "tta_trip_metrics": ("SELECT COUNT(*) AS cnt FROM tta_trip_metrics m "
                             "JOIN tta_trips t ON t.i_trip_no = m.i_trip_no WHERE t.i_cnr_id = %s"),
        "tta_trip_gps": ("SELECT COUNT(*) AS cnt FROM tta_trip_gps g "
                         "JOIN tta_trips t ON t.i_trip_no = g.i_trip_no WHERE t.i_cnr_id = %s"),
    }
    with conn.cursor() as cur:
        for table in TTA_TABLES:
            try:
                if scope.active:
                    cur.execute(scoped_sql[table], (scope.id,))
                else:
                    cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                counts[table] = cur.fetchone()["cnt"]
            except Exception:
                counts[table] = -1
    return counts


@router.get("/trip-classes")
def trip_class_counts(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Trip counts per upstream classification, for the global class filter.

    Every known lane is returned even when it has no rows yet, so the filter
    renders a stable set of options instead of appearing and disappearing with
    the data.
    """
    from nexgen.shared.feed.tta_lanes import LANES

    clause, params = base_clause(scope, "i_cnr_id")
    where = f"WHERE {clause}" if clause else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT s_trip_class AS trip_class, COUNT(*) AS trips FROM tta_trips {where} "
            "GROUP BY s_trip_class", params)
        found = {r["trip_class"]: r["trips"] for r in cur.fetchall()}
    classes = [
        {"key": key, "label": lane.label, "description": lane.description,
         "trips": int(found.get(key, 0))}
        for key, lane in LANES.items()
    ]
    return {"classes": classes, "total": sum(c["trips"] for c in classes)}


@router.get("/trips")
def list_tta_trips(
    page: int = 1,
    page_size: int = 25,
    search: str = "",
    status: str = "",
    scope: ConsignorScope = Depends(consignor_scope),
    tclass: TripClassScope = Depends(trip_class_scope),
    conn=Depends(get_db),
):
    """Paginated trips with GPS ping counts, filters and headline KPIs.

    Narrowed by the active trip class (zonal / local) when one is
    selected, so the KPI strip and the table always describe the same set.
    """
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    offset = (page - 1) * page_size

    clauses, params = [], []
    cnr_clause, cnr_params = base_clause(scope, "i_cnr_id", alias="t")
    if cnr_clause:
        clauses.append(cnr_clause)
        params += cnr_params
    cls_clause, cls_params = class_clause(tclass, alias="t")
    if cls_clause:
        clauses.append(cls_clause)
        params += cls_params
    if search:
        clauses.append("""(CAST(t.i_trip_no AS CHAR) LIKE %s OR t.s_asset_id LIKE %s
                   OR t.s_cnr_name LIKE %s OR t.s_org_node_name LIKE %s
                   OR t.s_dest_node_name LIKE %s OR t.s_driver_name LIKE %s)""")
        like = f"%{search}%"
        params += [like] * 6
    if status:
        clauses.append("t.c_trip_status = %s")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS total FROM tta_trips t {where}", params)
        total = cur.fetchone()["total"]

        # headline KPIs over the filtered set
        cur.execute(
            f"""SELECT COUNT(*) AS total_trips,
                       SUM(LOWER(t.c_trip_status) LIKE '%%close%%') AS closed_trips,
                       SUM(LOWER(COALESCE(t.c_trip_status, '')) NOT LIKE '%%close%%') AS active_trips,
                       ROUND(100 * SUM(m.i_delivery_delta_min <= 0) / NULLIF(SUM(m.i_delivery_delta_min IS NOT NULL), 0), 1) AS ontime_pct,
                       ROUND(AVG(m.i_transit_time_min), 0) AS avg_transit_min,
                       ROUND(SUM(m.d_distance_travelled_km), 1) AS total_distance_km,
                       ROUND(AVG(m.i_detention_min), 0) AS avg_detention_min,
                       ROUND(100 * SUM(t.i_gps_ping_count > 0) / NULLIF(COUNT(*), 0), 0) AS gps_coverage_pct
                FROM tta_trips t
                LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                {where}""",
            params,
        )
        kpis = cur.fetchone()

        # Status dropdown: restrict to the scoped consignor's own statuses so it
        # never reveals labels that only exist in another consignor's data.
        status_where = "WHERE t.c_trip_status IS NOT NULL"
        status_params = []
        if cnr_clause:
            status_where += f" AND {cnr_clause}"
            status_params += cnr_params
        if cls_clause:
            status_where += f" AND {cls_clause}"
            status_params += cls_params
        cur.execute(f"SELECT DISTINCT t.c_trip_status FROM tta_trips t {status_where}", status_params)
        statuses = [r["c_trip_status"] for r in cur.fetchall()]

        cur.execute(
            f"""SELECT t.i_trip_no, t.i_cnr_id, t.s_cnr_name, t.s_trip_class, t.s_asset_id, t.s_device_id,
                       t.s_asset_type, t.s_org_node_name, t.s_dest_node_name,
                       t.dt_trip_start, t.dt_trip_eta, t.dt_trip_ata, t.dt_trip_end,
                       t.c_trip_status, t.s_close_reason, t.s_driver_name, t.s_driver_mobile_no,
                       t.s_trans_name,
                       m.d_distance_travelled_km, m.s_delivery_status, m.i_speed_violation,
                       m.i_transit_time_min, m.i_moving_time_min, m.i_stoppage_time_min,
                       t.i_gps_ping_count AS gps_points
                FROM tta_trips t
                LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                {where}
                ORDER BY t.dt_trip_start DESC
                LIMIT %s OFFSET %s""",
            params + [page_size, offset],
        )
        rows = cur.fetchall()

    return {"items": rows, "total": total, "page": page, "page_size": page_size,
            "kpis": kpis, "statuses": statuses, "trip_class": tclass.label}


@router.get("/trips/{trip_no}")
def get_tta_trip(trip_no: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Full trip record + metrics + GPS summary.
    Also resolves LEGACY trip ids (old trips.id) so pre-merge links keep working."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM tta_trips WHERE i_trip_no = %s", (trip_no,))
        trip = cur.fetchone()
        if not trip:
            # legacy id? trips.id -> dispatch_entry_no == tta i_trip_no
            cur.execute("SELECT dispatch_entry_no FROM trips WHERE id = %s", (trip_no,))
            legacy = cur.fetchone()
            if legacy and str(legacy["dispatch_entry_no"]).isdigit():
                trip_no = int(legacy["dispatch_entry_no"])
                cur.execute("SELECT * FROM tta_trips WHERE i_trip_no = %s", (trip_no,))
                trip = cur.fetchone()
        if not trip:
            raise HTTPException(404, f"Trip {trip_no} not found")
        # Out-of-scope trips are indistinguishable from missing ones (no leak).
        if not scope.allows(trip["i_cnr_id"]):
            raise HTTPException(404, f"Trip {trip_no} not found")

        cur.execute("SELECT * FROM tta_trip_metrics WHERE i_trip_no = %s", (trip_no,))
        metrics = cur.fetchone()
        if metrics:
            metrics.pop("raw_json", None)

        cur.execute(
            """SELECT COUNT(*) AS total_pings,
                      SUM(is_moving) AS moving_pings,
                      MIN(dt_message) AS first_ping,
                      MAX(dt_message) AS last_ping,
                      MAX(i_cdist) AS max_cdist_m,
                      MAX(COALESCE(i_status_speed_kmph, i_speed)) AS max_speed_kmph,
                      AVG(CASE WHEN is_moving = 1
                          THEN COALESCE(i_status_speed_kmph, i_speed) END) AS avg_moving_speed_kmph
               FROM tta_trip_gps_cdist WHERE i_trip_no = %s""",
            (trip_no,),
        )
        gps_summary = cur.fetchone()

    return {"trip": trip, "metrics": metrics, "gps_summary": gps_summary}


@router.get("/trips/{trip_no}/analysis")
def get_tta_trip_analysis(trip_no: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Full GPS analysis bundle: lifecycle phases, stops, speed,
    driving pattern, waypoints, progress, cost."""
    enforce_tta_trip_scope(conn, scope, trip_no)
    from nexgen.shared.analysis.tta_analysis import build_trip_analysis
    result = build_trip_analysis(conn, trip_no)
    if result is None:
        raise HTTPException(404, f"Trip {trip_no} not found")
    return result


@router.get("/trips/{trip_no}/plant-delay")
def get_tta_plant_delay(
    trip_no: int,
    insights: bool = True,
    radius_km: float | None = None,
    benchmark_min: float = 240.0,
    refresh: bool = False,
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """In-plant / pre-dispatch delay for a trip, measured against the plant fence.

    Returns two things that answer different questions:

    * ``phases`` -- the origin journey split into the windows the business
      defined: plant entry to gate-out (what the TMS declares), then gate-out to
      clearing the plant geofence (the stretch nobody was billing for). These
      come from the trip stamps, so they survive a dead tracker.
    * the ping-trail reconstruction -- station dwell, the bottleneck, the delay
      classification, and with ``insights=true`` an LLM narrative. This explains
      *where* the time in those windows went, and needs GPS.

    ``radius_km`` defaults to the plant geofence (10 km on the published plant
    coordinate) rather than a fixed ring around wherever the truck started,
    because a trip closed at a plant it was not booked against used to drag that
    ring with it. Pass a value only to override.

    Results are cached in tta_plant_delay_cache: once a real LLM insight is
    stored it is served back (``cached: true``) without re-calling the LLM.
    Pass ``refresh=true`` to force a fresh analysis + LLM call and overwrite.
    """
    enforce_tta_trip_scope(conn, scope, trip_no)
    # The cache is keyed on what was ASKED for, not on what the fence resolved
    # to, so "use the fence default" needs a stable key of its own. -1 is that
    # key; it can never collide with a real radius.
    cache_radius = -1.0 if radius_km is None else radius_km
    from nexgen.services.analytics.lib.tta_plant_delay import build_plant_delay
    from nexgen.services.analytics.lib import tta_plant_delay_cache as cache

    # 1. Serve from cache when the stored row is complete for what was asked.
    if not refresh:
        hit = cache.get_cached(conn, trip_no, cache_radius, benchmark_min)
        if hit and (not insights or hit["insight"]):
            result = dict(hit["analysis"])
            result["cached"] = True
            if insights:
                ins = dict(hit["insight"])
                ins["cached"] = True
                result["insights"] = ins
            return result

    # 2. Compute fresh (LLM call only happens here).
    analysis = build_plant_delay(conn, trip_no, radius_km=radius_km,
                                 benchmark_min=benchmark_min)
    if analysis is None:
        raise HTTPException(404, f"Trip {trip_no} not found")

    insight = None
    if insights:
        from nexgen.services.analytics.lib.llm_insights import generate_insight
        insight = generate_insight(analysis)

    # 3. Persist (analysis always; insight only when it's a real LLM answer).
    cache.store(conn, trip_no, cache_radius, benchmark_min,
                {k: v for k, v in analysis.items() if k != "insights"}, insight)

    if insights:
        analysis["insights"] = insight
    analysis["cached"] = False
    return analysis


@router.get("/trips/{trip_no}/breaks")
def get_tta_trip_breaks(
    trip_no: int,
    weather: bool = True,
    scope: ConsignorScope = Depends(consignor_scope),
    conn=Depends(get_db),
):
    """Every break the truck took on this trip, named and totalled.

    Replaces the raw ping dump, which restated "the truck was parked" fifty
    times. Halts are runs of non-moving pings; each is named by trip phase
    (loading / detention at origin / unloading), then by duration and time of
    day (night rest, lunch, dinner, tea), then by weather.

    `unexplained_hours` in the KPIs is the number worth looking at: halt time
    with no account at all.

    Weather comes from the Open-Meteo archive and is cached per day per ~25 km
    cell, so a trip is a handful of calls on first view and free afterwards --
    `weather.api_calls` reports how many were made. Pass `weather=false` to skip
    it entirely; the halt taxonomy does not depend on it.
    """
    enforce_tta_trip_scope(conn, scope, trip_no)
    from nexgen.services.analytics.lib.tta_trip_breaks import build_trip_breaks

    result = build_trip_breaks(conn, trip_no, with_weather=weather)
    if result is None:
        raise HTTPException(404, f"Trip {trip_no} not found")
    return result


@router.get("/trips/{trip_no}/weather")
def get_tta_trip_weather(trip_no: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """On-demand weather impact: hourly historical weather along the trip's
    own GPS path + timestamps, correlated against slow windows."""
    enforce_tta_trip_scope(conn, scope, trip_no)
    from nexgen.shared.analysis.tta_weather import weather_impact_for_trip
    try:
        return weather_impact_for_trip(conn, trip_no)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/compare")
def compare_trips(trips: str, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Side-by-side comparison bundle for 2-4 trips (?trips=1,2,3)."""
    from nexgen.shared.analysis.tta_analysis import build_trip_analysis
    try:
        trip_nos = [int(t) for t in trips.split(",") if t.strip()][:4]
    except ValueError:
        raise HTTPException(400, "trips must be comma-separated trip numbers")
    if len(trip_nos) < 2:
        raise HTTPException(400, "need at least 2 trip numbers to compare")

    out = []
    for no in trip_nos:
        # Every compared trip must belong to the scoped consignor.
        enforce_tta_trip_scope(conn, scope, no)
        a = build_trip_analysis(conn, no)
        if a is None:
            raise HTTPException(404, f"Trip {no} not found")
        out.append({
            "overview": a["overview"],
            "phases": [{k: p[k] for k in ("key", "label", "duration_min", "share_pct")}
                       | {"gps_distance_km": p["gps"]["distance_km"],
                          "gps_utilization_pct": p["gps"]["utilization_pct"]}
                       for p in a["phases"]],
            "speed": {"kpis": a["speed"]["kpis"], "zones": a["speed"]["zones"],
                      "histogram": a["speed"]["histogram"]},
            "stops_kpis": a["stops"]["kpis"],
            "stop_categories": a["stops"].get("categories", []),
            "driving": {"score": a["driving"].get("score"), "kpis": a["driving"].get("kpis", {}),
                        "daily": a["driving"].get("daily", [])},
            "cost": {k: v for k, v in a["cost"].items() if k != "params"},
            # Downsampled cumulative-distance / speed track for time-aligned
            # overlays (elapsed pacing on the same lane).
            "progress": a["progress"].get("series", []),
        })
    return {"trips": out}


# ----------------------------------------------------------------------
# Configuration (UI-editable cost model)
# ----------------------------------------------------------------------

class CostConfigBody(BaseModel):
    fuel_price_per_liter: float
    fuel_efficiency_kmpl: float
    driver_wage_per_hour: float
    idle_fuel_consumption_lph: float


@router.get("/config/cost")
def get_cost_config_ep(conn=Depends(get_db)):
    """Current journey cost model parameters."""
    from nexgen.shared.analysis.tta_config import get_cost_config
    return get_cost_config(conn)


@router.post("/config/cost")
def save_cost_config_ep(body: CostConfigBody, conn=Depends(get_db)):
    """Save cost model parameters (used by trip analysis + comparison)."""
    from nexgen.shared.analysis.tta_config import save_cost_config
    try:
        return save_cost_config(conn, body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e))


# ----------------------------------------------------------------------
# Waypoint registry (persistent pattern store)
# ----------------------------------------------------------------------

# The waypoint registry (tta_waypoints / tta_waypoint_stats) is a FLEET-WIDE
# pattern store built by aggregating every consignor's GPS; it has no consignor
# dimension. There is no way to return a single consignor's slice of it without a
# schema change, so when a consignor is scoped we return an explicit empty result
# rather than leak cross-consignor patterns. (Follow-up: add a cnr_id dimension to
# the registry and rebuild per-consignor to support scoped access here.)
_WAYPOINT_REGISTRY_SCOPED_NOTE = (
    "The waypoint registry is a fleet-wide aggregate and is not partitioned by "
    "consignor yet, so it is unavailable when scoped to a single consignor. "
    "Use /tta/analytics/* for consignor-scoped waypoint analytics."
)


@router.get("/waypoints")
def waypoints_list(search: str = "", sort: str = "stopped_min",
                   page: int = 1, page_size: int = 25,
                   scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Registry of every waypoint ever seen in GPS data, with its stored
    behaviour pattern (dwell, stop events, profiles)."""
    if scope.active:
        return {"items": [], "total": 0, "page": page, "page_size": page_size,
                "scoped_unavailable": True, "note": _WAYPOINT_REGISTRY_SCOPED_NOTE}
    from nexgen.services.analytics.lib.tta_waypoints import list_waypoints
    return list_waypoints(conn, search, sort, page, page_size)


@router.get("/waypoints/{waypoint_id}")
def waypoints_detail(waypoint_id: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Every stored detail for one waypoint: pattern profiles + per-trip visits."""
    if scope.active:
        raise HTTPException(404, _WAYPOINT_REGISTRY_SCOPED_NOTE)
    from nexgen.services.analytics.lib.tta_waypoints import waypoint_detail
    d = waypoint_detail(conn, waypoint_id)
    if d is None:
        raise HTTPException(404, f"Waypoint {waypoint_id} not found")
    return d


@router.post("/waypoints/refresh")
def waypoints_refresh(conn=Depends(get_db)):
    """Rebuild the waypoint registry from GPS data. Runs under the shared
    waypoint lock so it never overlaps the scheduled rebuild."""
    from nexgen.services.ingestion.tms.tta_api_sync import run_waypoint_refresh
    return run_waypoint_refresh(conn)


# ----------------------------------------------------------------------
# Fleet-wide network analytics
# ----------------------------------------------------------------------

@router.get("/analytics/waypoints")
def analytics_waypoints(limit: int = 25, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    from nexgen.services.analytics.lib.tta_network import fleet_waypoints
    return fleet_waypoints(conn, limit, cnr_id=scope.id)


@router.get("/analytics/heatmap")
def analytics_heatmap(mode: str = "all", scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    from nexgen.services.analytics.lib.tta_network import density_heatmap
    if mode not in ("all", "stops"):
        raise HTTPException(400, "mode must be 'all' or 'stops'")
    return density_heatmap(conn, mode, cnr_id=scope.id)


@router.get("/analytics/waypoint-hours")
def analytics_waypoint_hours(top_n: int = 12, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    from nexgen.services.analytics.lib.tta_network import waypoint_hour_heatmap
    return waypoint_hour_heatmap(conn, min(max(top_n, 3), 30), cnr_id=scope.id)


@router.get("/analytics/states")
def analytics_states(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    from nexgen.services.analytics.lib.tta_network import state_distribution
    return {"states": state_distribution(conn, cnr_id=scope.id)}


@router.post("/analytics/refresh")
def analytics_refresh():
    """Rebuild the Network-Analytics aggregate tables (and reconcile per-trip GPS
    ping counts) from tta_trip_gps. Scans the raw pings ONCE; after this the
    /tta/analytics/* reads serve pre-aggregated rows. Also runs on the scheduled
    waypoint-refresh cadence — call this to force an immediate rebuild.

    Heavy batch op (can take minutes on a large GPS table): runs on a dedicated
    long-timeout connection, not the request connection."""
    from nexgen.shared.legacy_db import get_connection
    from nexgen.services.analytics.lib.tta_network import (
        refresh_gps_ping_counts, refresh_network_aggregates,
    )
    conn = get_connection(read_timeout=3600, write_timeout=3600)
    try:
        ping_counts = refresh_gps_ping_counts(conn)
        net = refresh_network_aggregates(conn)
        return {"ping_counts_reconciled": ping_counts, "network_aggregates": net}
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Data lifecycle (backup / retention purge)
# ----------------------------------------------------------------------

@router.get("/maintenance/status")
def maintenance_status_ep(conn=Depends(get_db)):
    from nexgen.services.analytics.lib.db_maintenance import maintenance_status
    return maintenance_status(conn)


@router.post("/maintenance/backup")
def maintenance_backup():
    from nexgen.services.analytics.lib.db_maintenance import run_backup
    try:
        return run_backup()
    except Exception as e:
        raise HTTPException(500, f"Backup failed: {e}")


@router.post("/maintenance/purge")
def maintenance_purge(dry_run: bool = True, retention_days: int | None = None,
                      conn=Depends(get_db)):
    """Archive + delete GPS pings of trips older than the retention window.
    Defaults to dry_run=true — call with dry_run=false to actually purge."""
    from nexgen.services.analytics.lib.db_maintenance import run_gps_purge
    return run_gps_purge(conn, retention_days, dry_run)


@router.get("/trips/{trip_no}/gps")
def get_tta_trip_gps(trip_no: int, max_points: int = 2000,
                     scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """GPS points for a trip, evenly decimated to max_points if needed."""
    enforce_tta_trip_scope(conn, scope, trip_no)
    max_points = min(max(max_points, 10), 20000)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM tta_trip_gps WHERE i_trip_no = %s", (trip_no,)
        )
        total = cur.fetchone()["cnt"]
        if total == 0:
            return {"trip_no": trip_no, "total": 0, "returned": 0, "points": []}

        step = max(total // max_points, 1)
        cur.execute(
            """SELECT * FROM (
                   SELECT g.*, ROW_NUMBER() OVER (ORDER BY dt_message) AS rn
                   FROM tta_trip_gps g WHERE i_trip_no = %s
               ) x
               WHERE (rn - 1) %% %s = 0 OR rn = %s
               ORDER BY dt_message""",
            (trip_no, step, total),
        )
        points = cur.fetchall()
        for p in points:
            p.pop("rn", None)
            p["d_lat"] = float(p["d_lat"])
            p["d_long"] = float(p["d_long"])

    return {"trip_no": trip_no, "total": total, "returned": len(points), "points": points}
