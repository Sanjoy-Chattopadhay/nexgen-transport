"""Import Smart-Truck's lane settings, watermarks, run history and gap register.

The lane configuration comes across as it was (entities, intervals,
lookbacks, watermarks) but with every lane **switched off**: Smart-Truck may
still be pulling the same feed, and two pullers would double the load on the
upstream. Switch a lane on from the Ingestion page when NexGen takes over;
it then resumes from Smart-Truck's watermark instead of re-reading history.
"""

from __future__ import annotations

import json
import logging

from nexgen.core.config import get_config
from nexgen.core.db import connect

logger = logging.getLogger(__name__)

_LANE_KEYS = ("tta_api_sync", "tta_api_sync_local", "tta_api_sync_boot_catchup",
              "tta_api_sync_local_boot_catchup", "tta_sync_config", "tta_sync_config_local")


def import_smart_truck() -> dict:
    st = f"`{get_config().legacy_database('smart_truck')}`"
    out = {}
    with connect("ingestion") as conn, conn.cursor() as cur:
        cur.execute(f"SELECT s_key, s_value FROM {st}.app_settings WHERE s_key IN %s", (_LANE_KEYS,))
        rows = cur.fetchall()
        for r in rows:
            value = r["s_value"]
            data = json.loads(value) if isinstance(value, (str, bytes)) else value
            if r["s_key"].startswith("tta_sync_config") and isinstance(data, dict):
                data["enabled_in_smart_truck"] = bool(data.get("enabled"))
                data["enabled"] = False
            cur.execute("INSERT INTO app_settings (s_key, s_value) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE s_value=VALUES(s_value)", (r["s_key"], json.dumps(data)))
        out["settings"] = [r["s_key"] for r in rows]
        cur.execute(f"""INSERT IGNORE INTO tta_sync_runs (id, trigger_type, lane, status, window_start, window_end,
                        blocks_fetched, trips_upserted, gps_inserted, gps_skipped, gps_failed, legacy_trips_synced,
                        error, started_at, finished_at, elapsed_seconds, created_at)
                        SELECT id, trigger_type, lane, status, window_start, window_end, blocks_fetched,
                        trips_upserted, gps_inserted, gps_skipped, gps_failed, legacy_trips_synced, error,
                        started_at, finished_at, elapsed_seconds, created_at FROM {st}.tta_sync_runs""")
        out["sync_runs"] = cur.rowcount
        cur.execute(f"""INSERT IGNORE INTO tta_data_gaps (id, lane, gap_start, gap_end, hours, state, detected_at,
                        detected_by, decided_at, snooze_until, note, recovered_at, created_at)
                        SELECT id, lane, gap_start, gap_end, hours, state, detected_at, detected_by, decided_at,
                        snooze_until, note, recovered_at, created_at FROM {st}.tta_data_gaps""")
        out["data_gaps"] = cur.rowcount
        conn.commit()
    return out
