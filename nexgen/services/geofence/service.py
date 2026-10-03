"""Geofencing: fences, crossings, visits, the physical ledger, live detection.

Owns nx_geo. The engine is Geo-Fencing's, unchanged in what it computes
(nexgen/shared/geoengine); it reads the fleet service's GPS through alias views
(migrations/geofence/R__aliases.sql) instead of keeping its own copy.

Roles:
  * detector -- Geo-Fencing's background refresh loop, kept current by events:
    when the fleet processor stores fixes or a trip record changes, the next
    pass re-evaluates exactly those trips and rebuilds what they touch, then
    publishes geo.visits.changed for routing and analytics. A safety-net pass
    runs every refresh.every_minutes regardless.
  * live -- the streaming detector (replay or tail of newly stored fixes).
  * api -- Geo-Fencing's pages' API under /api/v1/geo, and Smart-Truck's
    circle-geofence API under /api/v1/geofence.
"""

from __future__ import annotations

import logging
import threading

from nexgen.core.config import get_config
from nexgen.core.service import Service

logger = logging.getLogger(__name__)


def _bootstrap_run() -> None:
    """A database with no published run gets one: the whole feed, evaluated and
    published, so the refresh loop has a run to keep current.

    Geo-Fencing's start-up script did this by hand (`cli run --publish`); a
    fresh NexGen database would otherwise sit idle forever ("no finished run
    to refresh"). On a fresh database the feed is empty or small, so this is
    quick; the run's j_params record the settings it used, as every run's do.
    """
    from nexgen.shared.geoengine.pipeline import runner
    if runner.published_run_id() is not None:
        return
    logger.info("no published geofence run: evaluating the whole feed as the first run")
    out = runner.run(publish=True)
    logger.info("first geofence run published: %s", {k: out.get(k) for k in ("i_run_id", "i_trips", "s_status")})


def _detector_loop(stop: threading.Event) -> None:
    from nexgen.shared.geoengine.pipeline import scheduler
    try:
        _bootstrap_run()
    except Exception:               # the loop still runs; the next start retries
        logger.exception("could not create the first geofence run")
    every = float(get_config().setting("geofence", "refresh.every_minutes", 15.0) or 15.0)
    scheduler.loop(every_minutes=every, sync_fleet=False, stop=stop)


def _on_fleet_change(events) -> None:
    """New fixes or changed trip records: ask the refresh loop to run now.
    The loop picks it up within its poll (20 s), so a burst of events is one
    pass; the pass itself finds what changed from the batch watermark."""
    from nexgen.shared.geoengine.pipeline import scheduler
    scheduler.request_refresh()


def _live_loop(stop: threading.Event) -> None:
    from nexgen.shared.geoengine.pipeline.live_runner import LiveService
    s = get_config().setting("geofence", "live", {}) or {}
    svc = LiveService(mode=str(s.get("mode", "replay")), speed=float(s.get("speed", 300)),
                      window_hours=float(s.get("window_hours", 72)) or None)
    threading.Thread(target=lambda: (stop.wait(), svc.stop()), name="live-stop", daemon=True).start()
    svc.run()


def _ledger_partitions() -> dict:
    """Live events churn (written and trimmed constantly): trim beyond retention."""
    from nexgen.core.db import connect
    days = int(get_config().setting("geofence", "live_event_retention_days", 30))
    with connect("geofence") as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM geo_live_event WHERE dt_event < NOW() - INTERVAL %s DAY LIMIT 100000", (days,))
        n = cur.rowcount
        conn.commit()
    return {"live_events_deleted": n}


def build() -> Service:
    from nexgen.services.geofence.api.circle import router as circle_router
    from nexgen.services.geofence.api.engine_routes import router as engine_router, version_cache
    from nexgen.shared.geoengine.api.live_api import router as live_router
    from nexgen.shared.geoengine.api.v1 import router as v1_router

    svc = Service("geofence")
    svc.middleware(version_cache)
    svc.include(engine_router)
    svc.include(live_router, prefix="/api/v1/geo")
    svc.include(v1_router)
    svc.include(circle_router, prefix="/api/v1")

    detector = svc.worker("detector")
    detector.loop("refresh", _detector_loop)
    detector.consumer("detector", ["fleet.fixes.stored", "fleet.trips.changed"], _on_fleet_change)
    detector.jobs.add("live-event-trim", _ledger_partitions, cron="50 1 * * *",
                      description="drop live events past their retention")

    live = svc.worker("live")
    live.loop("live", _live_loop)
    return svc
