"""
APScheduler wiring for the TMS API sync — one job PER LANE.
-----------------------------------------------------------
A single in-process BackgroundScheduler runs `tta_api_sync.run_sync` once per
lane on that lane's own interval (zonal 30 min, local 20 min by default). Each
lane's live schedule (enabled + interval) comes from its own DB-persisted,
screen-editable config row, so changing an interval from the ETL screen takes
effect without a restart.

This replaces the single job that pulled from whichever feed `trip_source`
named. Two jobs rather than one loop over both lanes, because:
  * a slow or failing lane must not delay the other's next tick;
  * each lane's interval is edited on its own from the screen;
  * APScheduler's max_instances=1 then applies PER LANE, which is the guarantee
    we actually want ("never two ticks of the SAME lane") instead of the
    stronger, throughput-destroying "never two ticks at all".

Overlap protection is defence-in-depth:
  * scheduler level  — max_instances=1, coalesce=True per job (a tick is dropped
    if that lane's previous one is still running);
  * run level        — run_sync's per-lane non-blocking lock (skip-if-running),
    which also covers manual triggers / backfill firing alongside a tick;
  * write level      — tta_api_sync._ingest_lock serializes the DB stage ACROSS
    lanes, and _gps_gate caps total upstream concurrency. See the "Concurrency
    model" comment in tta_api_sync.py.

The 20/30-minute cadences line up every 60 minutes, so the lanes get a
staggered start (a per-lane offset plus jitter) to make an exact collision
rare; when one does happen it is safe by the layers above, not by luck.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from nexgen.shared.legacy_settings import settings
from nexgen.shared.feed.tta_lanes import LANES, LANE_KEYS, ZONAL, Lane, get_lane

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
WP_JOB_ID = "tms_waypoint_refresh"

# Seconds of random spread applied to each lane's tick, so two lanes whose
# intervals share a factor don't fire on the same second every time.
_JITTER_SECONDS = 30

# Fixed per-lane head start, so the lanes' ticks are separated instead of
# landing on the same second whenever their intervals share a factor.
_LANE_OFFSET_SECONDS = {ZONAL: 0, "local": 90}


def _make_job(lane_key: str, trigger: str = "scheduled"):
    def _job() -> None:
        # Imported lazily so importing this module never drags in the sync stack.
        from nexgen.core.jobs import recorded
        from nexgen.services.ingestion.tms.tta_api_sync import run_sync
        # Recorded in job_run so every lane tick shows on the developer page.
        recorded("ingestion", f"tms-{lane_key}", trigger,
                 lambda: run_sync(trigger=trigger, lane_key=lane_key))
    _job.__name__ = f"tms_api_sync_{lane_key}"
    return _job


def schedule_boot_catchup(lane: Lane | str, delay_seconds: int) -> str | None:
    """Queue ONE extra run shortly after boot, for a lane that came back behind.

    The recurring job's first fire is deliberately a full interval away so a
    crash-looping container cannot hammer the upstream. After a long absence
    that delay is the difference between "the app is catching up" and "nothing
    is happening, I suppose I must backfill" — which is exactly the wrong
    conclusion. This closes that window WITHOUT weakening the guard: it is one
    shot, it runs the same capped window an ordinary tick runs, and the caller
    rate-limits it across restarts.

    Its own job id, so it never replaces or reschedules the recurring job.
    """
    ln = lane if isinstance(lane, Lane) else get_lane(lane)
    sched = _ensure_scheduler()
    when = datetime.now(timezone.utc) + timedelta(seconds=max(5, int(delay_seconds)))
    job_id = f"{ln.job_id}_catchup"
    sched.add_job(
        _make_job(ln.key, trigger="boot-catchup"),
        trigger=DateTrigger(run_date=when),
        id=job_id,
        name=f"TMS {ln.label} boot catch-up",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    logger.info("TMS[%s] boot catch-up queued for %s",
                ln.key, when.isoformat(timespec="seconds"))
    return when.isoformat()


def _ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.start()
        logger.info("BackgroundScheduler started")
    return _scheduler


def apply_lane_config(lane: Lane | str, enabled: bool, interval_minutes: int) -> None:
    """(Re)schedule ONE lane's sync job. Idempotent — call it on startup and
    whenever that lane's schedule is edited from the screen."""
    ln = lane if isinstance(lane, Lane) else get_lane(lane)
    sched = _ensure_scheduler()
    if enabled:
        offset = _LANE_OFFSET_SECONDS.get(ln.key, 0)
        every = max(1, int(interval_minutes))
        # First fire is one full interval away, NOT at boot: a restart must not
        # trigger an immediate sync (a crash-looping container would hammer the
        # upstream). Nothing is lost by waiting — the watermark means the next
        # run covers everything since the last successful one.
        first = datetime.now(timezone.utc) + timedelta(minutes=every, seconds=offset)
        sched.add_job(
            _make_job(ln.key),
            trigger=IntervalTrigger(
                minutes=every,
                jitter=_JITTER_SECONDS,
                start_date=first,
            ),
            id=ln.job_id,
            name=f"TMS {ln.label} API sync",
            max_instances=1,      # never run two ticks of THIS lane concurrently
            coalesce=True,        # collapse missed ticks into one
            replace_existing=True,
        )
        logger.info("TMS[%s] sync scheduled every %d min, first fire %s "
                    "(+%ds offset, %ds jitter)",
                    ln.key, every, first.isoformat(timespec="seconds"),
                    offset, _JITTER_SECONDS)
    elif sched.get_job(ln.job_id):
        sched.remove_job(ln.job_id)
        logger.info("TMS[%s] sync schedule removed (disabled)", ln.key)


def apply_waypoint_config(any_lane_enabled: bool) -> None:
    """In Smart-Truck the waypoint registry was rebuilt from this scheduler.
    In NexGen the registry belongs to analytics, which rebuilds it on its own
    schedule (services.yaml analytics.settings.schedules.waypoint_registry_hours),
    so there is nothing to schedule here. Kept so the ported API still calls it."""
    logger.debug("waypoint registry is scheduled by analytics (any lane enabled: %s)", any_lane_enabled)


def apply_all(configs: dict[str, dict]) -> None:
    """(Re)build the whole schedule from every lane's effective config."""
    any_enabled = False
    for key in LANE_KEYS:
        cfg = configs.get(key) or {}
        enabled = bool(cfg.get("enabled"))
        any_enabled = any_enabled or enabled
        apply_lane_config(LANES[key], enabled,
                          int(cfg.get("interval_minutes", LANES[key].default_interval)))
    apply_waypoint_config(any_enabled)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("BackgroundScheduler stopped")


def get_next_run_time(lane: Lane | str | None = ZONAL) -> str | None:
    """ISO timestamp of one lane's next scheduled tick, or None when not scheduled."""
    if _scheduler is None:
        return None
    ln = lane if isinstance(lane, Lane) else get_lane(lane)
    job = _scheduler.get_job(ln.job_id)
    return job.next_run_time.isoformat() if job and job.next_run_time else None


def scheduler_snapshot() -> dict:
    """What the scheduler is actually doing right now.

    Read from the LIVE job store rather than from the config rows, so a lane
    whose row says "enabled" but whose job never registered shows up here as
    missing instead of looking healthy.
    """
    if _scheduler is None:
        return {"alive": False, "jobs": []}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "max_instances": job.max_instances,
            "coalesce": job.coalesce,
        })
    return {"alive": bool(_scheduler.running), "jobs": jobs}
