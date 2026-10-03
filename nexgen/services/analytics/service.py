"""Analytics: every dashboard, report, KPI and drill-down.

Owns nx_analytics: Smart-Truck's analysis tables (network, waypoint, speed and
hotspot aggregates, plant-delay and weather caches, settings) and its historic
trip store with the summaries the Dashboard, Drivers, Vehicles and Routes pages
read. Serves every Smart-Truck read API at its original path, so those pages
(and the chatbot) work unchanged. Reads the fleet's and geofence's data through
their published views (migrations/analytics/R__aliases.sql).

The `kpi` worker keeps the precomputed figures current:
  * fleet.trips.changed -> the historic store and the summaries for exactly the
    drivers, vehicles, routes, customers and days those trips touch
    (Smart-Truck's incremental refresh), and drop the dashboard caches;
  * geo.visits.changed / route.analysed -> drop the caches those feed;
  * every 6 h -> the waypoint registry, network aggregates and speed rollups;
  * nightly -> stop extraction and hotspot clusters.
"""

from __future__ import annotations

import logging

from nexgen.core.config import get_config
from nexgen.core.service import Service
from nexgen.shared.legacy_db import get_connection

logger = logging.getLogger(__name__)

ST_ROUTERS = ("dashboard", "drivers", "trips", "routes_analysis", "vehicles", "migrate", "tta",
              "tta_dashboard", "consignors", "consignees", "transporters", "hotspots", "speed", "manual")


def _drop_caches() -> None:
    from nexgen.services.analytics.lib.tta_dashboard import invalidate_cache
    from nexgen.shared.common.cache import invalidate
    invalidate_cache()
    invalidate()


def _on_trips_changed(events) -> None:
    trips = sorted({int(t) for ev in events for t in (ev.payload or {}).get("trip_nos") or []})
    if not trips:
        return
    from nexgen.services.analytics.lib.family_a import sync_trips
    conn = get_connection(read_timeout=3600, write_timeout=3600)
    try:
        out = sync_trips(conn, trips)
    finally:
        conn.close()
    _drop_caches()
    logger.info("historic store: %s trip(s) synced, summaries %s", out["legacy_trips_synced"],
                (out.get("summaries") or {}).get("status", out.get("summaries")))


def _on_results_changed(events) -> None:
    _drop_caches()


def _waypoint_registry() -> dict:
    from nexgen.services.analytics.lib.registry import run_waypoint_refresh
    out = run_waypoint_refresh()
    _drop_caches()
    return out


def _hotspots() -> dict:
    from nexgen.services.analytics.lib.tta_hotspot_clusters import refresh_clusters, refresh_facility_anchors
    from nexgen.services.analytics.lib.tta_hotspots import run_stop_extraction
    conn = get_connection(read_timeout=3600, write_timeout=3600)
    try:
        out = {"stops": run_stop_extraction(conn), "anchors": refresh_facility_anchors(conn),
               "clusters": refresh_clusters(conn)}
    finally:
        conn.close()
    _drop_caches()
    return out


def build() -> Service:
    import importlib

    svc = Service("analytics")
    for name in ST_ROUTERS:
        module = importlib.import_module(f"nexgen.services.analytics.api.{name}")
        svc.include(module.router, prefix="/api/v1")
    from nexgen.services.analytics.api import locations
    svc.include(locations.router)            # carries its own /api/v1/locations prefix

    kpi = svc.worker("kpi")
    kpi.consumer("kpi", ["fleet.trips.changed"], _on_trips_changed, batch=50)
    kpi.consumer("caches", ["geo.visits.changed", "route.analysed", "ml.predictions.ready"],
                 _on_results_changed, batch=200)
    hours = float(get_config().setting("analytics", "schedules.waypoint_registry_hours", 6) or 6)
    kpi.jobs.add("waypoint-registry", _waypoint_registry, every_minutes=hours * 60,
                 description="waypoint registry, network aggregates and speed rollups from all GPS")
    kpi.jobs.add("hotspots", _hotspots,
                 cron=str(get_config().setting("analytics", "schedules.hotspots", "30 3 * * *")),
                 description="stop extraction, facility anchors and hotspot clusters")
    return svc
