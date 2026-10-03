"""Import Smart-Truck's analysis tables and historic trip store into nx_analytics.

Every table was created from the legacy table's own CREATE TABLE
(migrations/analytics/0001_analysis_tables.sql), so each copy is a server-side
INSERT IGNORE ... SELECT *, ids included: a driver's or vehicle's page keeps
its URL, and trained models keep the ids they learned. smart_truck is only
read. Only the cost model is taken from app_settings; the lane settings
belong to ingestion.
"""

from __future__ import annotations

import logging
import time

from nexgen.core.config import get_config
from nexgen.core.db import connect

logger = logging.getLogger(__name__)

# Parents before children (the historic store keeps its foreign keys).
TABLES = ["drivers", "vehicles", "locations", "customers", "trips", "waypoints", "alerts", "file_uploads",
          "driver_summary", "vehicle_summary", "customer_summary", "route_summary", "route_time_patterns",
          "daily_fleet_stats", "gps_waypoint_agg", "gps_network_kpi", "gps_grid_agg", "gps_waypoint_hour_agg",
          "gps_state_agg", "tta_waypoints", "tta_waypoint_stats", "gps_speed_profile", "gps_speed_events",
          "gps_speed_build", "gps_stop_events", "gps_stop_extract_log", "gps_stop_clusters",
          "gps_stop_cluster_members", "gps_hotspot_labels", "tta_plant_delay_cache", "tta_weather_cache"]
SETTING_KEYS = ("cost_model",)


def import_legacy() -> dict:
    st = f"`{get_config().legacy_database('smart_truck')}`"
    out = {}
    with connect("analytics", read_timeout=7200, write_timeout=7200) as conn, conn.cursor() as cur:
        for t in TABLES:
            t0 = time.perf_counter()
            cur.execute(f"INSERT IGNORE INTO `{t}` SELECT * FROM {st}.`{t}`")
            out[t] = {"rows": cur.rowcount, "seconds": round(time.perf_counter() - t0, 1)}
            conn.commit()
        cur.execute(f"INSERT IGNORE INTO app_settings SELECT * FROM {st}.app_settings WHERE s_key IN %s",
                    (SETTING_KEYS,))
        out["app_settings"] = {"rows": cur.rowcount}
        conn.commit()
    return out
