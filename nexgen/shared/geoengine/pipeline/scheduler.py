"""The background refresh: keep the published run current, every few minutes.

    python -m nexgen.shared.geoengine.cli scheduler                 # every 15 minutes
    python -m nexgen.shared.geoengine.cli scheduler --every 10 --sync-fleet
    python -m nexgen.shared.geoengine.cli scheduler --once          # one pass, for cron / Task Scheduler

Each pass does only what the new data requires:

1. **Copy** new fixes and changed trip records from the fleet system
   (`--sync-fleet`, read-only), by the source's own row id -- a range scan of
   its primary key, however large the table has grown.
2. **Find** the trips that changed: fixes above the ping watermark (the
   highest geo_gps_ping.id already evaluated) and trip records whose sync
   stamp moved. A late fix a device uploads hours afterwards still carries a
   new id, so it is found too. Nothing scans the whole feed.
3. **Re-evaluate** those trips whole, with the run's own settings
   (runner.reprocess): a trip is never patched, because a later fix can
   overturn an earlier unconfirmed crossing.
4. **Rebuild** the physical ledger, shares, phases and rollups only where
   those trips reach (pipeline/incremental.py) -- their vehicles' overlapping
   trips, the fences and the days they touch.
5. **Publish** the change: the data version moves, every API process drops
   its cached answers (api/cache.py), and open pages reload themselves.

A pass that finds nothing new costs two indexed queries. Only one pass runs at
a time across every process and machine, by a MySQL named lock; a second
scheduler simply waits its turn. Every pass is recorded in geo_job, which is
what the sidebar's "updated … ago" and the Data Quality page read.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
import traceback
from datetime import datetime, timedelta

from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import geo_session

logger = logging.getLogger(__name__)

LOCK = "nexgen.shared.geoengine.refresh"
BATCH = 1000


# ---------------------------------------------------------------------------
# shared state
# ---------------------------------------------------------------------------

def get_state(conn, key: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute("SELECT s_value FROM geo_state WHERE s_key=%s", (key,))
        row = cur.fetchone()
    return row["s_value"] if row else None


def set_state(conn, key: str, value) -> None:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO geo_state (s_key, s_value) VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE s_value = VALUES(s_value)""",
                    (key, None if value is None else str(value)))
    conn.commit()


def request_refresh() -> None:
    """Ask the scheduler to run now rather than at its next slot."""
    with geo_session() as conn:
        set_state(conn, "refresh_requested", datetime.now().isoformat(timespec="seconds"))


def status(every_minutes: float | None = None) -> dict:
    """What the sidebar shows: the last pass, whether one is running, and
    when the next is due."""
    with geo_session() as conn, conn.cursor() as cur:
        try:
            cur.execute("""SELECT i_job_id, s_trigger, s_status, dt_started, dt_finished, d_seconds,
                                  i_new_pings, i_dirty_trips, i_affected_trips, i_days, i_fences, s_error
                             FROM geo_job WHERE s_kind='refresh' ORDER BY i_job_id DESC LIMIT 1""")
            last = cur.fetchone()
            cur.execute("""SELECT dt_finished FROM geo_job WHERE s_kind='refresh' AND s_status IN ('ok','idle')
                            ORDER BY i_job_id DESC LIMIT 1""")
            ok = cur.fetchone()
            cur.execute("SELECT s_key, s_value, dt_updated FROM geo_state")
            state = {r["s_key"]: r for r in cur.fetchall()}
        except Exception:                                    # noqa: BLE001 -- before init-db
            return {"enabled": False}
    heartbeat = state.get("scheduler_heartbeat")
    every = every_minutes or (float(state["scheduler_every_min"]["s_value"])
                              if state.get("scheduler_every_min") else None)
    alive = bool(heartbeat and heartbeat["dt_updated"]
                 and datetime.now() - heartbeat["dt_updated"] < timedelta(minutes=(every or 15) + 5))
    next_due = None
    if alive and ok and ok["dt_finished"] and every:
        next_due = ok["dt_finished"] + timedelta(minutes=every)
    return {
        "enabled": alive, "every_min": every,
        "running": bool(last and last["s_status"] == "running"),
        "last": last, "last_ok": ok["dt_finished"] if ok else None, "next_due": next_due,
        "requested": bool(state.get("refresh_requested") and state["refresh_requested"]["s_value"]),
        "data_version": state["data_version"]["s_value"] if state.get("data_version") else None,
        "ping_watermark": state["ping_watermark"]["s_value"] if state.get("ping_watermark") else None,
    }


# ---------------------------------------------------------------------------
# one pass
# ---------------------------------------------------------------------------

def _changed_trips(conn, run_id: int, ping_wm: int | None, meta_wm: str | None) -> tuple[set, set, int, str | None, int]:
    """Trips with new fixes, trips whose record changed, and the new marks."""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) m FROM geo_gps_ping")
        top = int(cur.fetchone()["m"] or 0)
        new_pings = 0
        fixes: set[int] = set()
        if ping_wm is not None and top > ping_wm:
            cur.execute("""SELECT i_trip_no, COUNT(*) n FROM geo_gps_ping WHERE id > %s AND id <= %s
                            GROUP BY i_trip_no""", (ping_wm, top))
            for r in cur.fetchall():
                fixes.add(int(r["i_trip_no"]))
                new_pings += int(r["n"])
        cur.execute("SELECT MAX(dt_synced) t FROM geo_trip_meta")
        meta_top = cur.fetchone()["t"]
        records: set[int] = set()
        if meta_wm and meta_top and str(meta_top) > meta_wm:
            cur.execute("""SELECT m.i_trip_no FROM geo_trip_meta m
                             JOIN geo_trip_summary s ON s.i_run_id=%s AND s.i_trip_no=m.i_trip_no
                            WHERE m.dt_synced > %s""", (run_id, meta_wm))
            records = {int(r["i_trip_no"]) for r in cur.fetchall()}
    return fixes, records - fixes, top, str(meta_top) if meta_top else meta_wm, new_pings


def refresh_once(trigger: str = "schedule", sync_fleet: bool = False, run_id: int | None = None,
                 batch: int = BATCH) -> dict:
    """One pass. Returns what it did; records it in geo_job."""
    from nexgen.shared.geoengine.pipeline import incremental, runner, summaries

    run_id = run_id or runner.published_run_id()
    if run_id is None:
        return {"status": "idle", "reason": "no finished run to refresh"}
    t0 = time.perf_counter()
    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) got", (LOCK,))
            if not cur.fetchone()["got"]:
                return {"status": "busy", "reason": "another refresh is running"}
        try:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO geo_job (s_kind, s_trigger, i_run_id, dt_started, s_status)
                               VALUES ('refresh', %s, %s, %s, 'running')""", (trigger, run_id, datetime.now()))
                job = cur.lastrowid
            conn.commit()
            set_state(conn, "refresh_requested", None)
            stats: dict = {"run_id": run_id}
            try:
                if sync_fleet:
                    stats["sync"] = _sync(conn)
                ping_wm = get_state(conn, "ping_watermark")
                meta_wm = get_state(conn, "meta_watermark")
                if ping_wm is None:
                    # First pass on this database: compare the feed with the run
                    # once, the slow way, then keep a watermark from here on.
                    stale = set(runner.stale_trips(run_id))
                    with conn.cursor() as cur:
                        cur.execute("SELECT COALESCE(MAX(id), 0) m FROM geo_gps_ping")
                        top = int(cur.fetchone()["m"] or 0)
                        cur.execute("SELECT MAX(dt_synced) t FROM geo_trip_meta")
                        mt = cur.fetchone()["t"]
                    fixes, records, meta_top, new_pings = stale, set(), str(mt) if mt else None, None
                    stats["initialised_watermark"] = True
                else:
                    fixes, records, top, meta_top, new_pings = _changed_trips(conn, run_id, int(ping_wm), meta_wm)
                stats.update({"new_pings": new_pings, "trips_with_new_fixes": len(fixes),
                              "trips_with_new_records": len(records)})
                if fixes or records:
                    before = incremental.snapshot(conn, run_id, fixes) if fixes else None
                    if fixes:
                        runner._upsert_trips(sorted(fixes))
                        trips = sorted(fixes)
                        for i in range(0, len(trips), batch):
                            runner.reprocess(run_id, trips[i:i + batch], workers=settings.scheduler.workers)
                        runner._recount(run_id)
                    stats["rebuild"] = incremental.rebuild(conn, run_id, fixes | records, before,
                                                           reevaluated=fixes)
                    try:
                        from nexgen.shared.geoengine.routing import analysis as routing
                        stats["routes"] = routing.analyse_trips(conn, run_id, sorted(fixes))
                    except Exception as exc:                 # noqa: BLE001 -- routing is additive
                        logger.warning("route analysis skipped: %s", exc)
                        stats["routes"] = {"error": str(exc)}
                    summaries.bump_version()
                    state = "ok"
                else:
                    state = "idle"
                set_state(conn, "ping_watermark", top)
                if meta_top:
                    set_state(conn, "meta_watermark", meta_top)
                rb = stats.get("rebuild") or {}
                secs = round(time.perf_counter() - t0, 2)
                with conn.cursor() as cur:
                    cur.execute("""UPDATE geo_job SET dt_finished=%s, s_status=%s, i_new_pings=%s, i_dirty_trips=%s,
                                          i_affected_trips=%s, i_days=%s, i_fences=%s, d_seconds=%s, j_stats=%s
                                    WHERE i_job_id=%s""",
                                (datetime.now(), state, new_pings, len(fixes) + len(records),
                                 rb.get("affected_trips"), rb.get("days"), rb.get("fences"), secs,
                                 json.dumps(stats, default=str), job))
                conn.commit()
                return {"status": state, "job": job, "seconds": secs, **stats}
            except Exception as exc:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("""UPDATE geo_job SET dt_finished=%s, s_status='failed', s_error=%s,
                                          d_seconds=%s WHERE i_job_id=%s""",
                                (datetime.now(), f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"[:8000],
                                 round(time.perf_counter() - t0, 2), job))
                conn.commit()
                raise
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK,))


def _sync(conn) -> dict:
    """New fixes and changed trip records from the fleet system, read-only."""
    from nexgen.shared.geoengine.ingest import fleet

    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) m FROM geo_gps_ping")
        after = int(cur.fetchone()["m"] or 0)
    out = {"pings": fleet.sync_after(after)}
    try:
        out["trips"] = fleet.sync_trips(recent_days=settings.scheduler.trip_sync_days)
    except Exception as exc:                                 # noqa: BLE001
        out["trips"] = {"error": str(exc)}
    return out


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

def loop(every_minutes: float = 15.0, sync_fleet: bool = False, poll_seconds: float = 20.0,
         stop: threading.Event | None = None) -> None:
    """Run a pass every `every_minutes`, or sooner when one is requested."""
    stop = stop or threading.Event()
    with geo_session() as conn:
        set_state(conn, "scheduler_every_min", every_minutes)
    logger.info("scheduler: a refresh every %s minutes%s", every_minutes,
                " with a copy from the fleet system" if sync_fleet else "")
    next_at = time.monotonic()                     # the first pass straight away
    trigger = "start"
    while not stop.is_set():
        try:
            with geo_session() as conn:
                set_state(conn, "scheduler_heartbeat", datetime.now().isoformat(timespec="seconds"))
                if get_state(conn, "refresh_requested"):
                    next_at, trigger = time.monotonic(), "manual"
        except Exception as exc:                             # noqa: BLE001
            logger.warning("scheduler: database not reachable: %s", exc)
        if time.monotonic() >= next_at:
            try:
                out = refresh_once(trigger=trigger, sync_fleet=sync_fleet)
                logger.info("scheduler: %s", {k: out.get(k) for k in
                                              ("status", "seconds", "new_pings", "trips_with_new_fixes")})
            except Exception:                                # noqa: BLE001 -- recorded in geo_job
                logger.exception("scheduler: refresh failed")
            next_at = time.monotonic() + every_minutes * 60
            trigger = "schedule"
        stop.wait(poll_seconds)


def main(every_minutes: float = 15.0, sync_fleet: bool = False, once: bool = False) -> int:
    if once:
        out = refresh_once(trigger="manual", sync_fleet=sync_fleet)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("status") in ("ok", "idle") else 1
    stop = threading.Event()

    def _sig(_signum, _frame):
        stop.set()
    signal.signal(signal.SIGINT, _sig)
    try:
        signal.signal(signal.SIGTERM, _sig)
    except (AttributeError, ValueError):
        pass
    loop(every_minutes, sync_fleet, stop=stop)
    return 0
