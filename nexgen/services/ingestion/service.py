"""Ingestion: the only part of NexGen that talks to the outside world.

It pulls trips and GPS from the eTrans TMS API on two lanes (zonal every 30
minutes, local every 20 by default), accepts uploaded TTA exports, and lands
everything it receives in the raw layer as batches (landing.py). It never
writes a clean table: the fleet processor does that from the batch event.

Stop this service, or just its `worker` role, and no data comes in from the
upstream -- the lane scheduler lives inside the worker role and dies with it.
Every page keeps working on what is stored.

The lane machinery (auth, windows, watermarks, the data-gap register, boot
catch-up, backfill) is Smart-Truck's, ported unchanged; see tms/.
"""

from __future__ import annotations

import logging

from nexgen.core.db import connect
from nexgen.core.service import Service

logger = logging.getLogger(__name__)


def _start_lanes() -> None:
    """Schedule each lane from its saved config, as Smart-Truck's start-up did:
    a lane that cannot authenticate is never scheduled, and a lane that comes
    back far behind gets one capped catch-up shortly after start."""
    from nexgen.services.ingestion.tms.scheduler import apply_all, schedule_boot_catchup
    from nexgen.services.ingestion.tms.tta_api_sync import plan_boot_catchup
    from nexgen.services.ingestion.tms.tta_sync_config import (
        all_sync_configs, effective_entity_ids, migrate_legacy_source,
    )
    from nexgen.shared.feed.tta_lanes import LANES
    from nexgen.shared.legacy_settings import settings

    with connect("ingestion") as conn:
        migrate_legacy_source(conn)
        configs = all_sync_configs(conn)
    for key, lane in LANES.items():
        ids = effective_entity_ids(configs.get(key, {})) if lane.entity_scoped else None
        if not lane.ready(ids):
            configs.setdefault(key, {})["enabled"] = False
            logger.warning("TMS[%s] not configured: lane idle (needs auth URL, base URL, username "
                           "and auth key%s)", key, "; and entity ids" if lane.entity_scoped else "")
    apply_all(configs)
    with connect("ingestion") as conn:
        for key, lane in LANES.items():
            plan = plan_boot_catchup(conn, lane, bool((configs.get(key) or {}).get("enabled")))
            logger.info("TMS[%s] start-up: data through %s (%s min behind); %s", key,
                        plan["watermark"] or "never", plan["lag_minutes"], plan["reason"] or "catch-up queued")
            if plan["should_run"]:
                schedule_boot_catchup(lane, settings.TMS_BOOT_CATCHUP_DELAY_SECONDS)


def _stop_lanes() -> None:
    from nexgen.services.ingestion.tms.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("lane scheduler stopped: no further calls to the TMS API")


def _raw_retention() -> dict:
    """Drop payloads older than the replay window (batch rows stay as history)."""
    from nexgen.core.config import get_config
    days = int(get_config().setting("ingestion", "raw_retention_days", 90))
    total = 0
    with connect("ingestion") as conn:
        while True:
            with conn.cursor() as cur:
                cur.execute("DELETE p FROM payload p JOIN batch b ON b.i_batch_id = p.i_batch_id "
                            "WHERE b.dt_landed < NOW() - INTERVAL %s DAY", (days,))
                n = cur.rowcount
            conn.commit()
            total += n
            if n == 0:
                break
    return {"payloads_deleted": total, "retention_days": days}


def build() -> Service:
    svc = Service("ingestion")
    from nexgen.services.ingestion.api.tta_files import router as files_router
    from nexgen.services.ingestion.api.tta_sync import router as sync_router
    from nexgen.services.ingestion.api.batches import router as batch_router
    svc.include(sync_router, prefix="/api/v1")
    svc.include(files_router, prefix="/api/v1")
    svc.include(batch_router)
    worker = svc.worker("worker")
    worker.on_start.append(_start_lanes)
    worker.on_stop.append(_stop_lanes)
    worker.jobs.add("raw-retention", _raw_retention, cron="40 1 * * *",
                    description="drop raw payloads older than the replay window")
    return svc
