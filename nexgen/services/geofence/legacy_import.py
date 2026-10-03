"""Import Geo-Fencing's masters, runs and ledgers, and Smart-Truck's circle
geofences, into nx_geo.

nx_geo's tables were created from the live legacy tables' own CREATE TABLE
(migrations/geofence/0001_geofencing.sql), so every copy is a server-side
INSERT IGNORE ... SELECT * with identical columns. The legacy databases are
only read. The feed copies (geo_gps_ping, geo_trip, geo_trip_meta,
geo_vehicle) are not imported: those names are views over the fleet service.

The published run comes across as published, so every geofencing page shows
the same figures it showed in Geo-Fencing. The detector then brings the run up
to date with trips Geo-Fencing never saw (its copy of the feed stopped at
4.91M of Smart-Truck's 6.64M rows): its first pass finds them by comparing
each trip's fix count with what the run read.
"""

from __future__ import annotations

import logging
import time

from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.tenancy import current_tenant_id

logger = logging.getLogger(__name__)

GEO_TABLES = ["geo_site", "geo_site_vertex", "geo_fence", "geo_import_run", "geo_import_reject", "geo_run",
              "geo_event", "geo_visit", "geo_violation", "geo_trip_summary", "geo_ping_reject", "geo_asset_state",
              "geo_fit_trail", "geo_trip_day", "geo_stop", "geo_gap", "geo_inferred_visit", "geo_fence_stats",
              "geo_fence_day", "geo_day_summary", "geo_pvisit", "geo_palert", "geo_pstop", "geo_trip_share",
              "geo_toll_plaza", "geo_trip_phase", "geo_job", "geo_upload", "geo_upload_ping",
              "geo_upload_artifact", "geo_live_position", "geo_live_event", "geo_live_cursor"]
# geo_state is imported selectively: its watermarks counted Geo-Fencing's
# copy of the feed (ping ids), which do not exist here.
GEO_STATE_KEYS = ("data_version", "scheduler_every_min")
ST_TABLES = ["geofences", "tta_trip_geofence_events", "gps_facility_anchors"]


def import_legacy() -> dict:
    cfg = get_config()
    geo = f"`{cfg.legacy_database('geofencing')}`"
    st = f"`{cfg.legacy_database('smart_truck')}`"
    report = {}
    with connect("geofence", read_timeout=7200, write_timeout=7200) as conn, conn.cursor() as cur:
        for t in GEO_TABLES:
            t0 = time.perf_counter()
            cur.execute(f"INSERT IGNORE INTO `{t}` SELECT * FROM {geo}.`{t}`")
            report[t] = {"rows": cur.rowcount, "seconds": round(time.perf_counter() - t0, 1)}
            conn.commit()
            logger.info("%-24s %8d rows", t, cur.rowcount)
        cur.execute(f"INSERT IGNORE INTO geo_state (s_key, s_value) SELECT s_key, s_value FROM {geo}.geo_state "
                    "WHERE s_key IN %s", (GEO_STATE_KEYS,))
        report["geo_state"] = {"rows": cur.rowcount}
        for t in ST_TABLES:
            cur.execute(f"INSERT IGNORE INTO `{t}` SELECT * FROM {st}.`{t}`")
            report[t] = {"rows": cur.rowcount}
        cur.execute(f"""INSERT IGNORE INTO trip_geofence_out (i_tenant_id, i_trip_no, dt_geofence_out,
                            i_geofence_out_gap_min, s_geofence_out_status)
                        SELECT %s, i_trip_no, dt_geofence_out, i_geofence_out_gap_min, s_geofence_out_status
                          FROM {st}.tta_trips
                         WHERE dt_geofence_out IS NOT NULL OR s_geofence_out_status IS NOT NULL""",
                    (current_tenant_id(),))
        report["trip_geofence_out"] = {"rows": cur.rowcount}
        conn.commit()
    return report
