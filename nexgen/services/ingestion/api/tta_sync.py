"""
TMS API Sync endpoints — two lanes (zonal + local) running side by side.

Every lane-aware endpoint takes an optional `lane` query param
("zonal" | "local"), defaulting to zonal so existing clients keep working. The
legacy source ids ("tta_report" / "local_report") are accepted as aliases.

Workflow:
    GET  /api/v1/tta/sync/lanes            -> lane catalogue (what the UI renders)
    POST /api/v1/tta/sync/run?lane=local   -> trigger that lane now (background)
    GET  /api/v1/tta/sync/status           -> per-lane status + shared auth/backfill
    GET  /api/v1/tta/sync/scheduler        -> live APScheduler state (is it alive?)
    GET  /api/v1/tta/sync/settings?lane=   -> that lane's effective config
    PUT  /api/v1/tta/sync/settings?lane=   -> save + re-apply that lane's schedule
    POST /api/v1/tta/sync/preview/trips    -> read-only look at the upstream feed
"""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from nexgen.shared.legacy_settings import settings
from nexgen.shared.legacy_db import get_db
from nexgen.services.ingestion.tms.scheduler import (
    get_next_run_time, apply_lane_config, apply_waypoint_config, scheduler_snapshot,
)
from nexgen.services.ingestion.tms.tta_api_sync import (
    run_sync, run_backfill, get_sync_status, get_backfill_status, get_recent_runs, is_running,
    plan_boot_catchup,
)
from nexgen.services.ingestion.tms import tta_data_gaps as gaps_svc
from nexgen.shared.feed.tta_lanes import LANES, LANE_KEYS, ZONAL, get_lane, is_lane
from nexgen.services.ingestion.tms.tta_sync_config import (
    get_sync_config, save_sync_config, effective_entity_ids, all_sync_configs, TRIP_SOURCES,
)

router = APIRouter(prefix="/tta/sync", tags=["TMS API Sync"])


def lane_param(lane: str = Query(ZONAL, description="zonal | local")):
    """Validated lane, as a FastAPI dependency.

    An unknown value 422s rather than silently falling back — a typo must never
    quietly sync the wrong feed.
    """
    if lane and not is_lane(lane):
        raise HTTPException(422, f"Unknown lane {lane!r}. Valid: {', '.join(LANE_KEYS)}")
    return get_lane(lane)


def _parse_date(s: str) -> datetime:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise HTTPException(422, f"Unparseable date: {s!r} (use YYYY-MM-DD)")


def _parse_dt(s: str) -> datetime:
    """Like _parse_date but a time-of-day may be appended. The local report
    takes a date+time window, so 'to 2026-08-04 06:00' has to be expressible."""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return _parse_date(s)          # falls back to midnight


@router.get("/lanes")
def list_lanes(conn=Depends(get_db)):
    """The sync lanes and their live schedule — drives the ETL screen's per-lane
    cards without the frontend hardcoding lane names."""
    configs = all_sync_configs(conn)
    return {
        "lanes": [
            {
                "key": ln.key,
                "label": ln.label,
                "description": ln.description,
                "endpoint": ln.endpoint,
                "trip_source": ln.source_id,
                "needs_entities": ln.entity_scoped,
                "ready": ln.ready(
                    effective_entity_ids(configs.get(key, {})) if ln.entity_scoped else None),
                "enabled": bool(configs.get(key, {}).get("enabled")),
                "interval_minutes": configs.get(key, {}).get("interval_minutes"),
                "lookback_minutes": configs.get(key, {}).get("lookback_minutes"),
                "next_scheduled_run": get_next_run_time(ln),
            }
            for key, ln in LANES.items()
        ]
    }


@router.get("/scheduler")
def scheduler_state():
    """Live APScheduler state — whether the background worker is actually alive
    and what each lane's next fire time is. Observed from the running scheduler,
    not read back from config, so it is real proof the jobs exist."""
    snap = scheduler_snapshot()
    snap["server_time"] = datetime.now().isoformat()
    snap["lane_jobs"] = {ln.key: ln.job_id for ln in LANES.values()}
    return snap


@router.post("/run")
def trigger_sync(background_tasks: BackgroundTasks, lane=Depends(lane_param),
                 conn=Depends(get_db)):
    """Trigger ONE lane's sync now, in the background (it opens its own DB
    connection). Returns 'already_running' if THAT lane is in flight — the other
    lane is never blocked."""
    cfg = get_sync_config(conn, lane)
    entity_ids = effective_entity_ids(cfg) if lane.entity_scoped else None
    if not settings.sync_ready_for(lane.source_id, entity_ids):
        return {
            "status": "not_configured",
            "lane": lane.key,
            "message": "Set TMS_AUTH_URL, TMS_API_BASE_URL, TMS_USERNAME, TMS_AUTH_KEY"
                       + (" and TMS_ENTITY_IDS" if lane.entity_scoped else "") + " in .env",
        }
    if is_running(lane.key):
        return {"status": "already_running", "lane": lane.key,
                "message": f"A {lane.label} sync is in progress. Poll GET /api/v1/tta/sync/status"}

    background_tasks.add_task(run_sync, None, "manual", lane.key)
    return {"status": "started", "lane": lane.key,
            "message": f"{lane.label} sync started in background."}


@router.get("/status")
def sync_status(lane=Depends(lane_param), conn=Depends(get_db)):
    """Per-lane running flag, watermark, last run and next scheduled run, plus
    the shared auth + backfill state. `lanes` holds every lane; the top-level
    fields mirror the requested one."""
    return get_sync_status(conn, lane.key)


@router.get("/health")
def sync_health(response: Response, conn=Depends(get_db)):
    """Machine-pollable ETL health. 200 = healthy, 503 = needs attention.

    Point an uptime monitor at this. It fails when a lane is falling behind
    (watermark older than TMS_LAG_ALERT_INTERVALS x its own interval), when a
    lane is enabled but has no scheduler job, when the scheduler itself is
    dead, or when the last run reported a data gap — i.e. every state where the
    ETL looks fine on the surface but is not actually keeping up.
    """
    status = get_sync_status(conn)
    snap = scheduler_snapshot()
    job_ids = {j["id"] for j in snap.get("jobs", [])}

    problems = []
    if not snap.get("alive"):
        problems.append("scheduler is not running")

    lanes_out = {}
    for key, ln in LANES.items():
        st = status["lanes"][key]
        if st["enabled"] and ln.job_id not in job_ids:
            problems.append(f"{key}: enabled but no scheduler job registered")
        if not st.get("healthy", True):
            problems.append(
                f"{key}: {st['lag_minutes']} min behind (limit {st['lag_limit_minutes']})")
        gap = (st.get("last_run") or {}).get("data_gap")
        if gap:
            problems.append(f"{key}: data gap {gap['from']} .. {gap['to']} — backfill required")
        if (st.get("last_run") or {}).get("status") == "error":
            problems.append(f"{key}: last run errored — {(st['last_run'].get('error') or '')[:120]}")
        lanes_out[key] = {
            "enabled": st["enabled"], "ready": st["ready"], "running": st["running"],
            "lag_minutes": st["lag_minutes"], "lag_limit_minutes": st["lag_limit_minutes"],
            "healthy": st.get("healthy"), "watermark": st["watermark"],
            "last_run_status": (st.get("last_run") or {}).get("status"),
            "scheduled": ln.job_id in job_ids,
        }

    ok = not problems
    if not ok:
        response.status_code = 503
    return {
        "status": "ok" if ok else "degraded",
        "problems": problems,
        "scheduler_alive": snap.get("alive"),
        "auth": status["auth"],
        "lanes": lanes_out,
        "checked_at": datetime.now().isoformat(),
    }


@router.get("/runs")
def sync_runs(limit: int = 10,
              lane: str = Query("", description="zonal | local | '' for all"),
              conn=Depends(get_db)):
    """Recent runs with records processed into tta_trips (trips_upserted) and
    tta_trip_gps (gps_inserted). Filtered to one lane when `lane` is given."""
    if lane and not is_lane(lane):
        raise HTTPException(422, f"Unknown lane {lane!r}. Valid: {', '.join(LANE_KEYS)}")
    lane_key = get_lane(lane).key if lane else None
    return {"runs": get_recent_runs(conn, min(max(limit, 1), 100), lane_key)}


@router.get("/logs")
def sync_logs(limit: int = 200, kind: str = "", errors_only: bool = False):
    """Today's ETL event log: each external call's payload (secrets masked),
    time, response status and exception detail. Only same-day logs are kept."""
    from nexgen.services.ingestion.tms.etl_log import read_events
    return read_events(
        limit=min(max(limit, 1), 2000),
        kind=kind or None,
        status_filter="error" if errors_only else None,
    )


# ---- Screen-editable schedule config ----------------------------------- #

class SyncSettingsBody(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = None
    lookback_minutes: int | None = None
    entity_ids: list[int] | None = None


@router.get("/settings")
def get_settings(lane=Depends(lane_param), conn=Depends(get_db)):
    """One lane's effective ETL config (DB-persisted, seeded from .env)."""
    cfg = get_sync_config(conn, lane)
    entity_ids = effective_entity_ids(cfg) if lane.entity_scoped else []
    cfg["lane"] = lane.key
    cfg["label"] = lane.label
    cfg["trip_source"] = lane.source_id
    cfg["needs_entities"] = lane.entity_scoped
    cfg["next_scheduled_run"] = get_next_run_time(lane)
    # Readiness depends on THIS lane's source and its EFFECTIVE entity ids, both
    # edited here rather than in .env — judging by the .env seed reports "not
    # configured" for a perfectly valid screen-set config.
    cfg["ready"] = settings.sync_ready_for(lane.source_id, entity_ids or None)
    cfg["trip_sources"] = list(TRIP_SOURCES)
    return cfg


@router.put("/settings")
def update_settings(body: SyncSettingsBody, lane=Depends(lane_param), conn=Depends(get_db)):
    """Persist ONE lane's schedule config and re-apply it to the live scheduler.
    Only that lane's job is rebuilt — the other keeps its current cadence."""
    try:
        cfg = save_sync_config(conn, body.model_dump(exclude_none=True), lane)
    except ValueError as e:
        raise HTTPException(422, str(e))
    ids = effective_entity_ids(cfg) if lane.entity_scoped else None
    enabled = bool(cfg.get("enabled")) and lane.ready(ids)
    apply_lane_config(lane, enabled, int(cfg.get("interval_minutes", lane.default_interval)))
    # The shared waypoint job follows "any lane enabled".
    configs = all_sync_configs(conn)
    apply_waypoint_config(any(c.get("enabled") for c in configs.values()))
    cfg["lane"] = lane.key
    cfg["next_scheduled_run"] = get_next_run_time(lane)
    return {"status": "ok", **cfg}


# ---- Bounded backfill -------------------------------------------------- #

class BackfillBody(BaseModel):
    from_date: str
    to_date: str
    chunk_days: int = 1


@router.post("/backfill")
def trigger_backfill(body: BackfillBody, background_tasks: BackgroundTasks,
                     lane=Depends(lane_param), conn=Depends(get_db)):
    """Backfill a historical date range for ONE lane, in day-sized chunks."""
    cfg = get_sync_config(conn, lane)
    entity_ids = effective_entity_ids(cfg) if lane.entity_scoped else None
    if not settings.sync_ready_for(lane.source_id, entity_ids):
        return {"status": "not_configured", "lane": lane.key}
    if is_running(lane.key):
        return {"status": "already_running", "lane": lane.key,
                "message": f"A {lane.label} sync/backfill is in progress."}
    frm = _parse_date(body.from_date)
    to = _parse_date(body.to_date)
    if frm > to:
        raise HTTPException(422, "from_date must be on or before to_date")

    background_tasks.add_task(run_backfill, frm, to, body.chunk_days, None, lane.key)
    return {"status": "started", "lane": lane.key,
            "message": f"{lane.label} backfill {frm.date()} .. {to.date()} started."}


@router.get("/backfill/status")
def backfill_status():
    """Live backfill progress."""
    return get_backfill_status()


# ---- Data-gap register ------------------------------------------------- #
#
# A gap is a span this ETL KNOWS it never fetched, because the lane was down
# longer than one window may reach back. These endpoints exist so declining to
# backfill is a recorded decision instead of the span quietly disappearing from
# the screen 30 minutes later. See backend/app/services/tta_data_gaps.py.


@router.get("/gaps")
def list_data_gaps(lane: str = Query("", description="zonal | local; empty = both"),
                   state: str = Query("", description="comma-separated: open,snoozed,dismissed,recovered"),
                   limit: int = Query(100, ge=1, le=500),
                   conn=Depends(get_db)):
    """Every recorded gap, newest missing span first."""
    if lane and not is_lane(lane):
        raise HTTPException(422, f"Unknown lane {lane!r}")
    states = tuple(s.strip() for s in state.split(",") if s.strip()) or None
    lane_key = get_lane(lane).key if lane else None
    rows = gaps_svc.list_gaps(conn, lane_key=lane_key, states=states, limit=limit)
    return {
        "status": "ok",
        "gaps": rows,
        "summary": {k: gaps_svc.lane_summary(conn, k) for k in LANE_KEYS},
    }


class GapDecision(BaseModel):
    """`snooze_hours` applies to /snooze only; `note` records why."""
    snooze_hours: int = 24
    note: str | None = None


def _decide(conn, gap_id: int, state: str, body: GapDecision | None):
    body = body or GapDecision()
    row = gaps_svc.set_state(conn, gap_id, state,
                             snooze_hours=body.snooze_hours, note=body.note)
    if row is None:
        raise HTTPException(404, f"No data gap with id {gap_id}")
    return {"status": "ok", "gap": row}


@router.post("/gaps/{gap_id}/dismiss")
def dismiss_gap(gap_id: int, body: GapDecision | None = None, conn=Depends(get_db)):
    """Decline to recover this span. It leaves the banner but keeps counting on
    the lane card — declining does not put the data back."""
    return _decide(conn, gap_id, gaps_svc.DISMISSED, body)


@router.post("/gaps/{gap_id}/snooze")
def snooze_gap(gap_id: int, body: GapDecision | None = None, conn=Depends(get_db)):
    """Hide it for `snooze_hours`, then show it again."""
    return _decide(conn, gap_id, gaps_svc.SNOOZED, body)


@router.post("/gaps/{gap_id}/reopen")
def reopen_gap(gap_id: int, body: GapDecision | None = None, conn=Depends(get_db)):
    """Undo a dismiss or snooze."""
    return _decide(conn, gap_id, gaps_svc.OPEN, body)


@router.get("/window-preview")
def window_preview(conn=Depends(get_db)):
    """What the NEXT run of each lane will actually fetch — from when to when.

    Read-only, and the same arithmetic the scheduler uses:
        window_end   = source_now()
        window_start = max(watermark - lookback, window_end - TMS_MAX_WINDOW_HOURS)
    `unreachable_hours` is what falls before that floor: it is not fetched by
    any automatic path, and becomes a gap row awaiting a decision.
    """
    cfgs = all_sync_configs(conn)
    return {
        "status": "ok",
        "max_window_hours": settings.TMS_MAX_WINDOW_HOURS,
        "lanes": {
            key: plan_boot_catchup(conn, LANES[key],
                                   bool((cfgs.get(key) or {}).get("enabled")),
                                   reserve=False)      # preview must not consume the slot
            for key in LANE_KEYS
        },
    }


# ---- Read-only preview: see the upstream trip feed, write nothing ------ #

class TripPreviewBody(BaseModel):
    """One window of trips, straight from the upstream API."""
    from_date: str = "2026-08-02"
    to_date: str = "2026-08-04"
    trip_source: str | None = None   # None => whatever the ETL screen has set
    entity_id: int | None = None     # tta_report only; None => first configured
    limit: int = 5                   # how many records to include in the response


@router.post("/preview/trips")
def preview_trips(body: TripPreviewBody, conn=Depends(get_db)):
    """Fetch trips from the upstream API and return them as-is.

    Logging in happens automatically — the cached JWT is reused, or a fresh one
    is fetched, exactly as a real sync would. Nothing is written to the database
    and the watermark is untouched, so this is safe to call repeatedly while
    exploring.

    The response echoes the exact URL and payload that were sent, so what the
    upstream actually received is never a guess.
    """
    from nexgen.services.ingestion.tms.tta_api_sync import (
        fetch_trips, fetch_trips_local, _url, _fmt_local_dt,
    )
    from nexgen.services.ingestion.tms.tms_auth import get_auth_client

    cfg = get_sync_config(conn)
    source = (body.trip_source or cfg.get("trip_source")
              or settings.TMS_TRIP_SOURCE or "tta_report").strip().lower()
    if source not in TRIP_SOURCES:
        raise HTTPException(422, f"trip_source must be one of {', '.join(TRIP_SOURCES)}")

    if not settings.TMS_API_BASE_URL:
        raise HTTPException(
            422,
            "TMS_API_BASE_URL is not set in .env — the app knows how to log in "
            "but not which host to fetch data from. Set it to the API host "
            "(no trailing slash) and restart.",
        )

    frm, to = _parse_dt(body.from_date), _parse_dt(body.to_date)
    if frm > to:
        raise HTTPException(422, "from_date must be on or before to_date")

    started = datetime.now()
    try:
        if source == "local_report":
            sent = {
                "method": "POST",
                "url": _url(settings.TMS_LOCAL_TRIP_ENDPOINT),
                "payload": {
                    "fromDate": _fmt_local_dt(frm),
                    "toDate": _fmt_local_dt(to),
                    "tripStatus": settings.TMS_LOCAL_TRIP_STATUS,
                },
            }
            trips = fetch_trips_local(frm, to)
        else:
            ids = effective_entity_ids(cfg)   # DB config first, .env fallback
            entity_id = body.entity_id if body.entity_id is not None else (ids[0] if ids else None)
            if entity_id is None:
                raise HTTPException(
                    422,
                    "tta_report is scoped per consignor: pass entity_id, or set "
                    "TMS_ENTITY_IDS in .env. (The local_report source needs neither.)",
                )
            sent = {
                "method": "POST",
                "url": f"{_url(settings.TMS_TRIP_ENDPOINT)}/{entity_id}",
                "payload": {
                    "fromDate": frm.strftime(settings.TMS_TRIP_DATE_FORMAT),
                    "toDate": to.strftime(settings.TMS_TRIP_DATE_FORMAT),
                    "tripStatus": settings.TMS_TRIP_STATUS,
                    "closedReason": settings.TMS_CLOSED_REASON,
                },
            }
            trips = fetch_trips(entity_id, frm, to)
    except HTTPException:
        raise
    except Exception as e:
        # Surface the upstream failure as data rather than a 500 — the point of
        # this endpoint is to SEE what happened, including when it goes wrong.
        return {
            "status": "error",
            "trip_source": source,
            "error": str(e),
            "auth": get_auth_client().status(),
            "elapsed_seconds": round((datetime.now() - started).total_seconds(), 2),
        }

    limit = max(0, min(int(body.limit), 200))
    return {
        "status": "ok",
        "trip_source": source,
        "request_sent": sent,
        "auth": get_auth_client().status(),   # booleans + expiry only, never the token
        "elapsed_seconds": round((datetime.now() - started).total_seconds(), 2),
        "trips_returned": len(trips),
        "showing": min(limit, len(trips)),
        "trip_numbers": [t.get("trip_no") or t.get("i_trip_no") for t in trips[:limit]],
        "trips": trips[:limit],
        "wrote_to_database": False,
    }


@router.get("/config")
def sync_config():
    """Effective TMS config. TMS_AUTH_KEY is masked."""
    return {
        "enabled": settings.TMS_SYNC_ENABLED,
        "ready": settings.TMS_SYNC_READY,
        "auth_url": settings.TMS_AUTH_URL,
        "refresh_url": settings.TMS_REFRESH_URL,
        "username": settings.TMS_USERNAME,
        "access_mode": settings.TMS_ACCESS_MODE,
        "auth_key_set": bool(settings.TMS_AUTH_KEY),
        "api_base_url": settings.TMS_API_BASE_URL,
        "trip_endpoint": settings.TMS_TRIP_ENDPOINT,
        "local_trip_endpoint": settings.TMS_LOCAL_TRIP_ENDPOINT,
        "local_trip_status": settings.TMS_LOCAL_TRIP_STATUS,
        "trip_source": settings.TMS_TRIP_SOURCE,
        "trip_sources": list(TRIP_SOURCES),
        "gps_endpoint": settings.TMS_GPS_ENDPOINT,
        "entity_ids": settings.TMS_ENTITY_ID_LIST,
        "trip_status": settings.TMS_TRIP_STATUS,
        "closed_reason": settings.TMS_CLOSED_REASON,
        "interval_minutes": settings.TMS_SYNC_INTERVAL_MINUTES,
        "lookback_minutes": settings.TMS_SYNC_LOOKBACK_MINUTES,
        "token_skew_seconds": settings.TMS_TOKEN_SKEW_SECONDS,
        "next_scheduled_run": get_next_run_time(),
    }
