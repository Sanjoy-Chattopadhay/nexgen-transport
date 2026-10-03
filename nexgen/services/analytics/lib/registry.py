"""The waypoint registry, network aggregates and speed rollups, rebuilt together.

In Smart-Truck this ran inside the sync process (tta_api_sync.
run_waypoint_refresh) on its own 6-hour cadence. In NexGen those tables
belong to analytics, so the rebuild is an analytics job
(`waypoint-registry`), run on the same cadence, on demand from the Network
page, or when ingestion asks for it. The per-trip ping counts it also
reconciled are kept by the fleet service as it stores fixes.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from nexgen.shared.legacy_db import get_connection

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def run_waypoint_refresh(conn=None) -> dict:
    if not _lock.acquire(blocking=False):
        return {"status": "already_running"}
    own = conn is None
    started = datetime.now()
    try:
        if own:
            conn = get_connection(read_timeout=3600, write_timeout=3600)
        from nexgen.services.analytics.lib.tta_network import refresh_network_aggregates
        from nexgen.services.analytics.lib.tta_waypoints import refresh_waypoints
        res = refresh_waypoints(conn)
        net = refresh_network_aggregates(conn)
        speed = None
        try:
            from nexgen.services.analytics.lib.speed_profile import refresh_all as refresh_speed
            speed = refresh_speed(conn)
        except Exception as exc:  # noqa: BLE001 -- the registry and network are already rebuilt
            logger.exception("speed/safety rollup refresh failed")
            speed = {"status": "error", "error": str(exc)}
        elapsed = round((datetime.now() - started).total_seconds(), 1)
        return {"status": "ok", "elapsed_seconds": elapsed, "result": res,
                "ping_counts_reconciled": "kept by the fleet service",
                "network_aggregates": net, "speed_rollups": speed}
    except Exception as exc:
        logger.exception("waypoint refresh failed")
        return {"status": "error", "error": str(exc)}
    finally:
        if own and conn is not None:
            conn.close()
        _lock.release()
