"""
Scheduled TMS API Sync
----------------------
Pulls trip + GPS data from the upstream TMS HTTP API and hands it to the
existing idempotent loader (tta_ingestion.ingest_blocks), as an alternative
to CSV/Excel upload.

Duplicate safety (the core requirement) is guaranteed downstream by the DB:
  * tta_trips     — upsert on i_trip_no (PK)
  * trips (legacy)— upsert on dispatch_entry_no (UNIQUE)
  * tta_trip_gps  — INSERT IGNORE on uq_gps_ping (i_trip_no, s_device_id, dt_message)
so re-fetching an overlapping time window never creates duplicate rows.

This module only adds the *fetch*, *watermark* and *run-lock* layers:
  * fetch_blocks(since, until) — auth'd, paginated pull → block list
  * get/set_watermark          — last successful window-end in app_settings
  * run_sync(trigger)          — orchestrates one run (skip-if-running)

Auth (JWT access + refresh, ~10-min access token) lives in tms_auth.py.
"""

import json
import logging
import threading
import time
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

from nexgen.shared.legacy_settings import settings
from nexgen.shared.legacy_db import get_connection
from nexgen.services.ingestion.tms.tms_auth import get_auth_client
from nexgen.shared.feed.tta import clean, parse_dt
from nexgen.services.ingestion.landing import land_blocks
from nexgen.shared.feed.tta_lanes import LANES, LANE_KEYS, LOCAL, ZONAL, Lane, get_lane
from nexgen.services.ingestion.tms.etl_log import log_event
from nexgen.services.ingestion.tms.tta_data_gaps import (
    record_gap, mark_recovered, lane_summary as gap_lane_summary,
    actionable_gaps,
)

logger = logging.getLogger(__name__)


def _err_status(e) -> object:
    r = getattr(e, "response", None)
    return getattr(r, "status_code", None) if r is not None else None


def source_now() -> datetime:
    """"Now" as the UPSTREAM sees it — naive wall-clock in its timezone.

    Every timestamp the eTrans API returns is local time in
    settings.TMS_SOURCE_TIMEZONE (its login response reports
    serverTimezone: Asia/Kolkata), and every DATETIME column stores those naive.
    So the sync window has to be built in THAT zone.

    Using a bare datetime.now() instead ties the window to the container's
    timezone. A Docker image with no TZ set runs in UTC, which puts window_end
    5h30m BEHIND the newest data; the local lane — which filters on
    dt_trip_start at minute precision and clamps its watermark to window_end —
    would then never advance past that point and would re-read the same span
    forever. Falls back to plain local time only if the zone is unavailable.
    """
    try:
        return datetime.now(ZoneInfo(settings.TMS_SOURCE_TIMEZONE)).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — missing tzdata must not stop the sync
        logger.warning("Timezone %s unavailable — falling back to system local time. "
                       "If this process is not in the source timezone, sync windows "
                       "will be skewed.", settings.TMS_SOURCE_TIMEZONE)
        return datetime.now()


# ============================================================== #
# Concurrency model — two lanes, one database
# ============================================================== #
# The zonal (30 min) and local (20 min) schedules collide every hour, so the
# two lanes DO overlap by design. Overlap is safe because the work is split
# into two stages with different contention profiles:
#
#   FETCH  (network-bound, minutes) — runs CONCURRENTLY across lanes, bounded
#          globally by _gps_gate so two lanes can never put more than
#          TMS_GPS_MAX_CONCURRENCY requests on the upstream at once.
#
#   INGEST (database-bound, seconds) — SERIALIZED across lanes by _ingest_lock.
#          Both lanes write the same dimension tables (drivers / vehicles /
#          locations / customers) through SELECT-then-INSERT, which is not
#          atomic: two concurrent lanes inserting the same vehicle would race
#          into duplicate rows or deadlock on the unique index. Serializing the
#          short stage removes that class of bug entirely, and costs nothing —
#          ingest is seconds against a multi-minute fetch.
#
# Per-lane skip-if-running (_lane_locks) means a slow lane delays only itself.
# Trip-number collisions are impossible (the two upstream sequences are
# disjoint) and GPS is deduped by uq_gps_ping, so an overlap never double-counts.
_lane_locks: dict[str, threading.Lock] = {}
_lane_lock_guard = threading.Lock()

# Serializes the DB-write stage across ALL lanes and the backfill.
_ingest_lock = threading.Lock()

# Global ceiling on in-flight upstream GPS requests, shared by every lane.
_gps_gate = threading.BoundedSemaphore(max(1, settings.TMS_GPS_MAX_CONCURRENCY))

_state_lock = threading.Lock()
# Per-lane run state, keyed by lane.
_last_run: dict[str, dict] = {}
_running: dict[str, bool] = {}
_backfill_state: dict = {"status": "never"}
# Separate lock so a (slow) waypoint-registry rebuild never blocks a sync and
# never overlaps another rebuild. It writes tta_waypoints/_stats only, which
# syncs don't touch, so running alongside a sync is safe.
_wp_lock = threading.Lock()

# MySQL errors that mean "transient write contention, retry the transaction".
_RETRYABLE_DB_ERRNOS = {1213, 1205}   # deadlock found / lock wait timeout


def _lane_lock(lane_key: str) -> threading.Lock:
    """The skip-if-running lock for one lane (created on first use)."""
    with _lane_lock_guard:
        if lane_key not in _lane_locks:
            _lane_locks[lane_key] = threading.Lock()
        return _lane_locks[lane_key]


def _is_retryable_db_error(e: Exception) -> bool:
    return bool(getattr(e, "args", None)) and e.args[0] in _RETRYABLE_DB_ERRNOS


# --- Cross-process lane lock ------------------------------------------- #
# _lane_locks above only covers threads in ONE interpreter. A second process
# (uvicorn --workers 2, a rolling deploy overlapping two containers, or someone
# running a manual sync script) has its own locks and would happily sync the
# same lane concurrently. A MySQL advisory lock is visible to every process on
# the database, and MySQL drops it automatically when the holding connection
# goes away — so a hard kill leaves nothing to clean up.

def _db_lock_name(lane_key: str) -> str:
    return f"tta_sync_lane_{lane_key}"


def acquire_db_lane_lock(conn, lane_key: str) -> bool:
    """Try to take the cluster-wide lock for this lane. Non-blocking.

    Returns False when another PROCESS holds it, in which case this run must
    skip — exactly like the in-process skip-if-running, but across the whole
    deployment.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (_db_lock_name(lane_key),))
            row = cur.fetchone()
        return bool(row and row.get("got") == 1)
    except Exception as e:  # noqa: BLE001
        # A database that cannot do advisory locks must not stop the sync;
        # the in-process lock still applies. Log loudly so it is visible.
        logger.warning("Could not take DB lane lock for %s (%s) — relying on the "
                       "in-process lock only", lane_key, e)
        return True


def release_db_lane_lock(conn, lane_key: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (_db_lock_name(lane_key),))
    except Exception:  # noqa: BLE001 — the lock dies with the connection anyway
        pass


def ingest_serialized(conn, blocks: list[dict], lane: Lane, trigger: str | None = None,
                      window: tuple | None = None) -> dict:
    """Land one lane's fetched blocks in the raw layer (NexGen).

    Smart-Truck wrote tta_trips / tta_trip_gps here, under a global write lock
    with deadlock retries, because both lanes upserted the same dimension
    tables. NexGen lands each run as one batch (landing.land_blocks): a few
    inserts into tables no other writer touches, so there is no contention to
    serialise. The fleet processor normalises the batch afterwards.
    """
    from nexgen.services.ingestion.tms.tta_sync_config import get_sync_config, effective_entity_ids

    default_cnr_id = None
    if lane.key == LOCAL:
        # The local feed carries no consignor id; borrow the zonal lane's
        # configured tenant so local trips land under the same consignor scope.
        ids = effective_entity_ids(get_sync_config(conn, LANES[ZONAL]))
        default_cnr_id = ids[0] if ids else None
    gps_kind = "F" if str(settings_gps_kind()).lower().startswith("f") else "R"
    return land_blocks(conn, blocks, source=f"tms.{lane.key}", trip_class=lane.key,
                       gps_kind=gps_kind, default_cnr_id=default_cnr_id, trigger=trigger,
                       window=window)


def settings_gps_kind() -> str:
    """'raw' or 'filtered': what the TMS GPS endpoint delivers (services.yaml)."""
    from nexgen.core.config import get_config
    return str(get_config().get("integrations.tms.gps_kind", "raw"))


# ============================================================== #
# Fetch — eTrans two-step flow:
#   1. trips, from ONE of two interchangeable sources (config `trip_source`):
#        tta_report   POST /vehicle/track/ttaReport/{entity_id}
#                     one call per entity, date-only window, tripStatus "Close"
#        local_report POST /vehicle/track/tripDetailsTtaLocalReport
#                     one call for all consignors, date+time window,
#                     tripStatus "C", and a differently-named response schema
#                     (dt_booking/eta_time/consigner_name/…) — the aliases in
#                     tta_ingestion.map_trip_row absorb the difference.
#   2. per trip: GET /vehicle/trip-analysis/waypoints?vehicle=&fromDt=&toDt=
# ============================================================== #

def _url(endpoint: str) -> str:
    base = settings.TMS_API_BASE_URL.rstrip("/")
    return base + (endpoint if endpoint.startswith("/") else "/" + endpoint)


def _request(method: str, url: str, kind: str = "api", **kw) -> object:
    """Auth'd request with a single 401 invalidate-and-retry. Thread-safe:
    passes the failed token to invalidate() so concurrent callers don't
    stampede the login endpoint. Every call is recorded to the ETL log."""
    auth = get_auth_client()
    t0 = time.perf_counter()
    payload, params = kw.get("json"), kw.get("params")
    try:
        token = auth.get_token()
        resp = requests.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                timeout=settings.TMS_API_TIMEOUT, **kw)
        if resp.status_code == 401:
            logger.info("TMS 401 — invalidating token and retrying once")
            auth.invalidate(token)
            token = auth.get_token()
            resp = requests.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                    timeout=settings.TMS_API_TIMEOUT, **kw)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log_event(kind, method=method, url=url, payload=payload, params=params,
                  status=_err_status(e), elapsed_ms=round((time.perf_counter() - t0) * 1000),
                  error=str(e))
        raise
    log_event(kind, method=method, url=url, payload=payload, params=params,
              status=resp.status_code, elapsed_ms=round((time.perf_counter() - t0) * 1000))
    return data


def fetch_trips(entity_id: int, from_date: datetime, to_date: datetime) -> list[dict]:
    """POST the TTA report for one entity over a date window → list of trips."""
    url = f"{_url(settings.TMS_TRIP_ENDPOINT)}/{entity_id}"
    payload = {
        "entity_id": entity_id,
        "entityId": entity_id,
        "closedReason": settings.TMS_CLOSED_REASON,
        "fromDate": from_date.strftime(settings.TMS_TRIP_DATE_FORMAT),
        "toDate": to_date.strftime(settings.TMS_TRIP_DATE_FORMAT),
        "tripStatus": settings.TMS_TRIP_STATUS,
        "tag": "0",
        "vehicle": "ALL",
        "specialcontract": "",
    }
    data = _request("POST", url, kind="trips", json=payload)
    return data if isinstance(data, list) else []


def _fmt_local_dt(dt: datetime) -> str:
    """Format a window bound for the local report.

    Defaults to the upstream's own unpadded shape ("2026-8-2 0:0"), which
    strftime can't produce portably. TMS_LOCAL_TRIP_DATETIME_FORMAT overrides
    it with a plain strftime string if the API prefers a padded form.
    """
    fmt = settings.TMS_LOCAL_TRIP_DATETIME_FORMAT.strip()
    if fmt:
        return dt.strftime(fmt)
    return f"{dt.year}-{dt.month}-{dt.day} {dt.hour}:{dt.minute}"


def fetch_trips_local(from_date: datetime, to_date: datetime) -> list[dict]:
    """POST the TTA *local* report over a date window → list of trips.

    Differs from fetch_trips in three ways that matter: no entity id in the
    path (a single call covers every consignor), a date+time window instead of
    a date-only one, and single-letter status codes ("C") instead of "Close".

    The response omits the trip-status field entirely — the filter lives only
    in the request — so the requested status is stamped onto each record,
    otherwise tta_trips.c_trip_status would land NULL for every local row.
    """
    url = _url(settings.TMS_LOCAL_TRIP_ENDPOINT)
    status = settings.TMS_LOCAL_TRIP_STATUS
    payload = {
        "fromDate": _fmt_local_dt(from_date),
        "toDate": _fmt_local_dt(to_date),
        "tripStatus": status,
    }
    data = _request("POST", url, kind="trips", json=payload)
    if not isinstance(data, list):
        return []
    for rec in data:
        if isinstance(rec, dict):
            rec.setdefault("trip_status", status)
    return data


def fetch_gps(vehicle: str, from_dt: datetime, to_dt: datetime) -> list[dict]:
    """GET the waypoint/ping trail for one vehicle over its trip window."""
    params = {
        "vehicle": vehicle,
        "fromDt": from_dt.strftime(settings.TMS_GPS_DATETIME_FORMAT),
        "toDt": to_dt.strftime(settings.TMS_GPS_DATETIME_FORMAT),
    }
    # _gps_gate caps in-flight GPS calls across ALL lanes, so two concurrent
    # lanes never double what we put on the upstream.
    with _gps_gate:
        data = _request("GET", _url(settings.TMS_GPS_ENDPOINT), kind="gps", params=params)
    return data if isinstance(data, list) else []


# The upstream rate-limits us: an 8-worker GPS pull produced a burst of 429s
# during a live soak (8 of 305 calls in ~2 seconds). 429 is the ONE 4xx that
# means "you were right, just slower" — everything else in the 4xx range is the
# server rejecting the request itself.
_RETRYABLE_STATUSES = {429}
# Never sleep longer than this on a Retry-After, however large the header says.
_RETRY_AFTER_CAP_SECONDS = 60


def _is_transient(e: Exception) -> bool:
    """True for failures that are worth another attempt: DNS, refused/reset
    connections, timeouts, upstream 5xx, and 429 (rate limited).

    Every other 4xx is the server rejecting the request itself — retrying that
    just repeats the same mistake.
    """
    if isinstance(e, (requests.exceptions.ConnectionError,
                      requests.exceptions.Timeout)):
        return True
    status = _err_status(e)
    if status is None:
        return False
    status = int(status)
    return status >= 500 or status in _RETRYABLE_STATUSES


def _retry_after_seconds(e: Exception) -> float | None:
    """The upstream's own Retry-After hint, in seconds, if it sent one.

    Honouring it beats guessing: backing off less than asked earns another 429,
    and backing off far more wastes the window. Capped so a hostile or
    mis-set header cannot stall a run.
    """
    resp = getattr(e, "response", None)
    if resp is None:
        return None
    raw = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
    if not raw:
        return None
    try:
        return min(float(raw), _RETRY_AFTER_CAP_SECONDS)
    except (TypeError, ValueError):
        # The header may be an HTTP-date instead of seconds; not worth parsing
        # — the caller's own backoff still applies.
        return None


def _retry_transient(fn, what: str, *args, **kwargs):
    """Run fn with bounded retries + linear backoff on transient failures.

    Trips and auth had no retry at all while GPS had three, so a single DNS
    blink on /auth/authenticate — which is the first call of every chunk —
    would abort a whole backfill chunk that was otherwise fine.
    """
    attempts = max(1, settings.TMS_FETCH_RETRIES)
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — classified by _is_transient
            last = e
            if attempt >= attempts or not _is_transient(e):
                raise
            wait = max(settings.TMS_FETCH_RETRY_BACKOFF * attempt,
                       _retry_after_seconds(e) or 0)
            logger.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs",
                           what, attempt, attempts, e, wait)
            time.sleep(wait)
    raise last if last else RuntimeError(f"{what} failed")


def _fetch_gps_resilient(vehicle: str, frm: datetime, to: datetime) -> list[dict]:
    """fetch_gps with bounded retries + linear backoff on transient errors.

    Retries ANY exception rather than only the transient ones: this is the
    bottom of the per-trip path, and giving up early here costs a trip its GPS
    and holds the lane's watermark. A rate-limit response (429) waits for the
    upstream's own Retry-After when it sends one — the GPS pull runs
    TMS_GPS_WORKERS-wide, so it is the call most likely to be throttled.

    Raises the last error if every attempt fails (the caller treats that as a
    failure, never as 'no pings').
    """
    last = None
    for attempt in range(1, max(1, settings.TMS_GPS_RETRIES) + 1):
        try:
            return fetch_gps(vehicle, frm, to)
        except Exception as e:  # noqa: BLE001 — network/HTTP errors are retriable
            last = e
            if attempt < settings.TMS_GPS_RETRIES:
                wait = max(settings.TMS_GPS_RETRY_BACKOFF * attempt,
                           _retry_after_seconds(e) or 0)
                time.sleep(wait)
    raise last if last else RuntimeError("GPS fetch failed")


def _build_block_safe(trip: dict) -> tuple[dict, object]:
    """Assemble one {vehicle, trip, gps} block, fetching the trip's GPS trail
    windowed by its own booking_date → ata_out.

    Returns (block, failed_trip_no_or_None). On a GPS fetch that fails after
    all retries, the trip is still returned (so the trip row is ingested) but
    with empty gps AND flagged as failed — the caller then holds the watermark
    so the window (and this trip's GPS) is retried next run. GPS is never
    silently dropped.
    """
    vehicle = clean(trip.get("vehicle_no"))
    trip_no = trip.get("trip_no") or trip.get("i_trip_no")

    def first_dt(*keys):
        for k in keys:
            dt = parse_dt(trip.get(k))
            if dt is not None:
                return dt
        return None

    # Both feeds are accepted: ttaReport names these booking_date/dept_date and
    # ata_out/ata/trip_closing_dt; the local report uses dt_booking/dt_trip_start
    # and ata_time. Missing the local names here would leave every local trip
    # without a resolvable window — i.e. silently no GPS at all.
    frm = first_dt("booking_date", "dt_booking", "dept_date", "dt_trip_start")
    to = first_dt("ata_out", "ata", "ata_time", "trip_closing_dt", "dt_trip_end")
    # The local feed reports ata_time "NA" for trips closed administratively
    # rather than by arrival. Fall back to the ETA so the trail is still
    # pulled; without this those trips would lose their GPS entirely.
    if to is None:
        to = first_dt("eta", "eta_time", "dt_trip_eta")
    if not (vehicle and frm and to):
        # No resolvable GPS window — nothing to fetch, not a failure.
        return {"vehicle": vehicle, "trip": trip, "gps": []}, None
    try:
        gps = _fetch_gps_resilient(vehicle, frm, to)
        return {"vehicle": vehicle, "trip": trip, "gps": gps, "gps_from": frm, "gps_to": to}, None
    except Exception as e:  # noqa: BLE001
        logger.error("GPS fetch permanently failed for trip %s (%s) after %d tries: %s",
                     trip_no, vehicle, settings.TMS_GPS_RETRIES, e)
        return {"vehicle": vehicle, "trip": trip, "gps": [], "gps_from": frm, "gps_to": to,
                "gps_failed": True}, trip_no


def fetch_blocks(since: datetime, until: datetime,
                 entity_ids: list[int],
                 trip_source: str | None = None) -> tuple[list[dict], list]:
    """Fetch trips (+ per-trip GPS) for the given entities over the window.

    Trips are fetched sequentially (one cheap POST per entity); the per-trip
    GPS pulls — the real bottleneck — run in a bounded thread pool. Returns
    (blocks, failed_trip_nos); a non-empty failure list means some GPS could
    not be fetched and the window should be retried (watermark held).
    """
    # `trip_source` now names a LANE. Legacy source ids ("tta_report" /
    # "local_report") still resolve, so an older caller keeps working.
    lane = get_lane(trip_source)
    source = lane.source_id

    def keep(records: list[dict]) -> None:
        for trip in records:
            if not isinstance(trip, dict):
                continue
            if trip.get("trip_no") is not None or trip.get("i_trip_no") is not None:
                trips.append(trip)

    trips: list[dict] = []
    if source == "local_report":
        # Not scoped by entity — one call returns the window across consignors.
        local_trips = _retry_transient(fetch_trips_local, "local trip report",
                                       since, until)
        logger.info("TMS local report: %d trip(s) [%s .. %s]",
                    len(local_trips), _fmt_local_dt(since), _fmt_local_dt(until))
        keep(local_trips)
    else:
        for entity_id in entity_ids:
            entity_trips = _retry_transient(fetch_trips, f"trip report (entity {entity_id})",
                                            entity_id, since, until)
            logger.info("TMS entity %s: %d trip(s) [%s .. %s]",
                        entity_id, len(entity_trips),
                        since.strftime(settings.TMS_TRIP_DATE_FORMAT),
                        until.strftime(settings.TMS_TRIP_DATE_FORMAT))
            keep(entity_trips)

    blocks: list[dict] = []
    failed: list = []
    workers = max(1, settings.TMS_GPS_WORKERS)
    if workers == 1 or len(trips) <= 1:
        results = (_build_block_safe(t) for t in trips)
    else:
        # get_token() is thread-safe and refreshes transparently, so a long
        # parallel pull spanning the ~10-min token life stays authenticated.
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_build_block_safe, trips))

    for block, fail in results:
        blocks.append(block)
        if fail is not None:
            failed.append(fail)

    total_gps = sum(len(b["gps"]) for b in blocks)
    logger.info("TMS fetch: %d trip block(s), %d GPS ping(s), %d GPS failure(s) [%d workers]",
                len(blocks), total_gps, len(failed), workers)
    return blocks, failed


# ============================================================== #
# Watermark (last successful window-end, persisted in app_settings)
# ============================================================== #

def next_watermark(lane: Lane, blocks: list[dict], window_end,
                   previous, window_start):
    """Where the lane's watermark should land after a successful run.

    Default (lane.watermark_field is None): wall-clock window_end — correct for
    a source whose window bounds are dates, because a re-query re-reads the
    whole day anyway.

    When the lane names a watermark_field, the upstream filters on that field
    AND publishes rows late. Advancing to wall-clock would step over rows that
    do not exist yet and never come back for them, so instead:
      * trips seen -> advance to the newest value actually observed;
      * none seen  -> hold the previous mark, so the next run re-scans the same
                      span rather than skipping it. The window is capped
                      elsewhere so a long quiet period cannot grow it forever.
    """
    if not lane.watermark_field:
        return window_end
    seen = [parse_dt(b["trip"].get(lane.watermark_field)) for b in blocks]
    seen = [d for d in seen if d]
    if seen:
        candidate = min(max(seen), window_end)      # never past wall-clock
        return max(candidate, previous) if previous else candidate
    return previous or window_start


def get_watermark(conn, lane: Lane) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT s_value FROM app_settings WHERE s_key=%s", (lane.watermark_key,))
        row = cur.fetchone()
    if not row or not row.get("s_value"):
        return None
    val = row["s_value"]
    data = json.loads(val) if isinstance(val, str) else val
    ts = data.get("last_synced")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def set_watermark(conn, lane: Lane, ts: datetime) -> None:
    payload = json.dumps({"last_synced": ts.isoformat()})
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO app_settings (s_key, s_value) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE s_value=VALUES(s_value)",
            (lane.watermark_key, payload),
        )
    conn.commit()


# ============================================================== #
# Boot catch-up — one extra run when a lane comes back behind
# ============================================================== #

def _boot_catchup_key(lane: Lane) -> str:
    return lane.watermark_key + "_boot_catchup"


def _last_boot_catchup(conn, lane: Lane) -> datetime | None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT s_value FROM app_settings WHERE s_key=%s",
                        (_boot_catchup_key(lane),))
            row = cur.fetchone()
        if not row or not row.get("s_value"):
            return None
        val = row["s_value"]
        data = json.loads(val) if isinstance(val, str) else val
        ts = data.get("last_catchup")
        return datetime.fromisoformat(ts) if ts else None
    except Exception:  # noqa: BLE001 — never block startup on bookkeeping
        return None


def _mark_boot_catchup(conn, lane: Lane, ts: datetime) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_settings (s_key, s_value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE s_value=VALUES(s_value)",
                (_boot_catchup_key(lane), json.dumps({"last_catchup": ts.isoformat()})),
            )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not record boot catch-up for %s: %s", lane.key, e)


def plan_boot_catchup(conn, lane: Lane, enabled: bool,
                      reserve: bool = True) -> dict:
    """Decide whether this lane should run one catch-up now, and say what it
    would fetch.

    The answer to "from when to when will it pull?" is the same on every path,
    catch-up or ordinary tick:

        window_end   = source_now()
        window_start = max(watermark - lookback, window_end - TMS_MAX_WINDOW_HOURS)

    Anything older than that floor is NOT fetched implicitly at any point. It
    is filed in the gap register and waits for a decision. Coming back after a
    fortnight therefore costs one capped run, not a fortnight of catching up.
    """
    # Lazy, like every other use of the config reader here — importing it at
    # module scope closes an import cycle through the lane config.
    from nexgen.services.ingestion.tms.tta_sync_config import get_sync_config
    now = source_now()
    lookback = timedelta(minutes=int(
        (get_sync_config(conn, lane) or {}).get("lookback_minutes") or lane.default_lookback))
    watermark = get_watermark(conn, lane)
    floor = now - timedelta(hours=settings.TMS_MAX_WINDOW_HOURS)
    desired_start = (watermark - lookback) if watermark else (now - lookback)
    window_start = max(desired_start, floor)
    plan = {
        "lane": lane.key,
        "should_run": False,
        "reason": "",
        "watermark": watermark.isoformat() if watermark else None,
        "lag_minutes": (round((now - watermark).total_seconds() / 60, 1)
                        if watermark else None),
        "window_start": window_start.isoformat(),
        "window_end": now.isoformat(),
        # What this run will NOT reach, and will therefore file as a gap.
        "unreachable_hours": (round((floor - desired_start).total_seconds() / 3600, 1)
                              if desired_start < floor else 0.0),
    }

    if not settings.TMS_BOOT_CATCHUP:
        plan["reason"] = "boot catch-up disabled (TMS_BOOT_CATCHUP=0)"
        return plan
    if not enabled:
        plan["reason"] = "lane disabled"
        return plan
    if watermark is None:
        # Never synced: the first ordinary tick already covers a full lookback,
        # so there is nothing to catch up TO.
        plan["reason"] = "no watermark yet — nothing to catch up"
        return plan
    if now - watermark <= lookback:
        plan["reason"] = "already current (within its own lookback)"
        return plan

    last = _last_boot_catchup(conn, lane)
    min_gap = timedelta(minutes=settings.TMS_BOOT_CATCHUP_MIN_GAP_MINUTES)
    if last and (datetime.now() - last) < min_gap:
        # The guard that makes this safe in a restart loop: one catch-up per
        # window, not one per boot.
        plan["reason"] = (f"rate-limited — last boot catch-up {last.isoformat()} "
                          f"(min {settings.TMS_BOOT_CATCHUP_MIN_GAP_MINUTES}m apart)")
        return plan

    plan["should_run"] = True
    plan["reason"] = f"{plan['lag_minutes']}m behind, lookback is {int(lookback.total_seconds() / 60)}m"
    # `reserve` claims the rate-limit slot. The startup path takes it; the
    # read-only preview endpoint must not, or looking at the screen would
    # silently consume the next restart's catch-up.
    if reserve:
        _mark_boot_catchup(conn, lane, datetime.now())
    return plan


# ============================================================== #
# Run history — one persisted row per scheduled/manual sync run
# ============================================================== #

_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS tta_sync_runs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trigger_type VARCHAR(20),
    lane VARCHAR(16) NOT NULL DEFAULT 'zonal',
    status VARCHAR(20),
    window_start DATETIME NULL,
    window_end DATETIME NULL,
    blocks_fetched INT DEFAULT 0,
    trips_upserted INT DEFAULT 0,          -- records into tta_trips
    gps_inserted INT DEFAULT 0,            -- records into tta_trip_gps
    gps_skipped INT DEFAULT 0,             -- duplicate pings ignored
    gps_failed INT DEFAULT 0,              -- trips whose GPS deferred for retry
    legacy_trips_synced INT DEFAULT 0,
    error TEXT NULL,
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    elapsed_seconds DECIMAL(10,1) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sync_runs_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _bootstrap_runs(conn):
    with conn.cursor() as cur:
        cur.execute(_RUNS_DDL)
        # Add columns to a table created before they existed.
        for ddl in ("ALTER TABLE tta_sync_runs ADD COLUMN gps_failed INT DEFAULT 0",
                    "ALTER TABLE tta_sync_runs ADD COLUMN lane VARCHAR(16) "
                    "NOT NULL DEFAULT 'zonal' AFTER trigger_type"):
            try:
                cur.execute(ddl)
            except Exception:
                pass  # column already present
    conn.commit()


def _record_run(conn, row: dict) -> None:
    """Persist one run to tta_sync_runs (best-effort; never breaks a sync)."""
    try:
        _bootstrap_runs(conn)
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tta_sync_runs
                   (trigger_type, lane, status, window_start, window_end, blocks_fetched,
                    trips_upserted, gps_inserted, gps_skipped, gps_failed, legacy_trips_synced,
                    error, started_at, finished_at, elapsed_seconds)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (row.get("trigger"), row.get("lane", ZONAL), row.get("status"),
                 row.get("window_start"), row.get("window_end"), row.get("blocks_fetched", 0),
                 row.get("trips_upserted", 0), row.get("gps_inserted", 0), row.get("gps_skipped", 0),
                 row.get("gps_failed", 0), row.get("legacy_trips_synced", 0), row.get("error"),
                 row.get("started_at"), row.get("finished_at"), row.get("elapsed_seconds")),
            )
        conn.commit()
    except Exception as e:
        logger.warning("Could not record sync run: %s", e)


def get_recent_runs(conn, limit: int = 10, lane_key: str | None = None) -> list[dict]:
    """Last N runs, newest first. Filtered to one lane when `lane_key` is given
    (the ETL screen renders one history table per lane)."""
    _bootstrap_runs(conn)
    where, params = "", []
    if lane_key:
        where = "WHERE lane = %s"
        params.append(lane_key)
    params.append(int(limit))
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, trigger_type, lane, status, window_start, window_end,
                      blocks_fetched, trips_upserted, gps_inserted, gps_skipped,
                      gps_failed, legacy_trips_synced, error, started_at, finished_at,
                      elapsed_seconds, created_at
               FROM tta_sync_runs {where} ORDER BY id DESC LIMIT %s""",
            params,
        )
        rows = cur.fetchall()
    for r in rows:
        for k in ("window_start", "window_end", "started_at", "finished_at", "created_at"):
            if r.get(k) is not None:
                r[k] = r[k].isoformat()
        if r.get("elapsed_seconds") is not None:
            r["elapsed_seconds"] = float(r["elapsed_seconds"])
    return rows


# ============================================================== #
# Orchestrator
# ============================================================== #

def _set_running(lane_key: str, flag: bool, **extra) -> None:
    with _state_lock:
        _running[lane_key] = flag
        _last_run.setdefault(lane_key, {"status": "never"}).update(extra)


def is_running(lane_key: str | None = None) -> bool:
    """True when the given lane has a run in flight (any lane when omitted)."""
    with _state_lock:
        if lane_key is None:
            return any(_running.values())
        return bool(_running.get(lane_key))


def run_sync(conn=None, trigger: str = "scheduled", lane_key: str = ZONAL) -> dict:
    """Run one sync cycle FOR ONE LANE. Skips (returns 'already_running') if that
    lane already has a run in flight — the other lane is unaffected.

    Opens its own DB connection when `conn` is None (scheduler / background
    task); pass a connection to reuse an existing one.
    """
    lane = get_lane(lane_key)
    lock = _lane_lock(lane.key)
    if not lock.acquire(blocking=False):
        logger.info("TMS[%s] sync already running — skipping %s trigger", lane.key, trigger)
        return {"status": "already_running", "lane": lane.key}

    own_conn = conn is None
    started = source_now()
    _set_running(lane.key, True, status="running", trigger=trigger, lane=lane.key,
                 started_at=started.isoformat(), error=None)
    db_locked = False
    try:
        if own_conn:
            conn = get_connection()

        # Cluster-wide guard: the in-process lock above only covers THIS
        # interpreter. If another process (a second uvicorn worker, an
        # overlapping deploy, a manual script) already holds this lane, stand
        # down rather than double-syncing it.
        db_locked = acquire_db_lane_lock(conn, lane.key)
        if not db_locked:
            logger.warning("TMS[%s] lane is locked by another process — skipping %s trigger",
                           lane.key, trigger)
            return {"status": "already_running", "lane": lane.key,
                    "detail": "held by another process"}

        # Effective config (DB-persisted, screen-editable) drives lookback +
        # which entities to pull.
        from nexgen.services.ingestion.tms.tta_sync_config import get_sync_config, effective_entity_ids
        cfg = get_sync_config(conn, lane)
        entity_ids = effective_entity_ids(cfg)

        # Upstream wall-clock, not container-local — see source_now().
        window_end = source_now()
        lookback = timedelta(minutes=cfg.get("lookback_minutes", lane.default_lookback))
        watermark = get_watermark(conn, lane)
        # Always re-fetch a lookback overlap so nothing slips between runs; the
        # DB dedup absorbs the re-fetched rows.
        window_start = (watermark or window_end) - lookback
        # A lane that holds its watermark (nothing published yet) must not let
        # the window grow without bound over a long quiet period.
        floor = window_end - timedelta(hours=settings.TMS_MAX_WINDOW_HOURS)
        gap = None
        if window_start < floor:
            # The lane has been down (or holding its watermark) longer than one
            # window may span. Clamping is necessary — an unbounded window would
            # try to re-pull months in one run — but the skipped span is REAL
            # MISSING DATA, so it is recorded on the run and surfaced on the ETL
            # screen instead of being buried in a log line. Recover it with a
            # bounded backfill over exactly this range.
            gap = {"from": window_start.isoformat(), "to": floor.isoformat(),
                   "hours": round((floor - window_start).total_seconds() / 3600, 1)}
            logger.error(
                "TMS[%s] DATA GAP: watermark is %s, older than the %sh window cap. "
                "Skipping %s .. %s — run a backfill over that range to recover it.",
                lane.key, window_start.isoformat(), settings.TMS_MAX_WINDOW_HOURS,
                gap["from"], gap["to"])
            log_event("run", status="gap", detail=f"{lane.key}:{trigger}", lane=lane.key,
                      error=f"data gap {gap['from']} .. {gap['to']} ({gap['hours']}h) "
                            f"— backfill required")
            # Durable record. Everything above forgets this within a day: the
            # run row below stores the CLAMPED window, the event log keeps only
            # today, and _last_run is in-process. The register is what survives
            # a restart, what the banner reads, and what holds the operator's
            # decision when they choose NOT to backfill.
            recorded = record_gap(conn, lane.key, window_start, floor, detected_by=trigger)
            if recorded:
                # Report the merged span, not this run's slice — two detections
                # of one outage are one gap.
                gap = {"id": recorded["id"], "from": recorded["gap_start"],
                       "to": recorded["gap_end"], "hours": recorded["hours"],
                       "state": recorded["state"]}
            window_start = floor

        log_event("run", status="started", detail=f"{lane.key}:{trigger}",
                  window_start=window_start.isoformat(), window_end=window_end.isoformat(),
                  lane=lane.key, entities=entity_ids, trip_source=lane.source_id)

        blocks, gps_failures = fetch_blocks(window_start, window_end, entity_ids,
                                            trip_source=lane.key)

        if blocks:
            # Skip the inline waypoint-registry rebuild — it runs on its own
            # cadence (run_waypoint_refresh) so the per-run path stays fast.
            # Serialized across lanes (see the concurrency model at the top).
            summary = ingest_serialized(conn, blocks, lane, trigger=trigger,
                                        window=(window_start, window_end))
        else:
            summary = {"status": "ok", "trips_upserted": 0,
                       "gps_inserted": 0, "gps_skipped": 0, "errors": []}

        # Advance the watermark ONLY when the window is fully captured. If any
        # trip's GPS could not be fetched, HOLD the watermark so the whole
        # window (and that trip's GPS) is retried next run — no GPS is lost.
        # The trip rows themselves are already ingested (idempotent), so this
        # only re-pulls; dedup absorbs the overlap.
        if gps_failures:
            logger.warning("TMS[%s] holding watermark: %d trip(s) had GPS fetch failures "
                           "(will retry next run): %s",
                           lane.key, len(gps_failures), gps_failures[:20])
        else:
            advanced = next_watermark(lane, blocks, window_end, watermark, window_start)
            set_watermark(conn, lane, advanced)
            if advanced < window_end:
                logger.info("TMS[%s] watermark held at %s (source lag on %s), not %s",
                            lane.key, advanced.isoformat(), lane.watermark_field,
                            window_end.isoformat())

        # Per-run breakdown for the ETL log.
        trips_fetched = len(blocks)                          # returned by trip API
        trips_passed = summary.get("trips_upserted", 0)      # stored into tta_trips
        trips_failed = max(0, trips_fetched - trips_passed)  # couldn't be ingested
        trips_with_gps = sum(1 for b in blocks if b.get("gps"))  # got >=1 ping
        gps_deferred = len(gps_failures)                     # GPS fetch failed → retry

        # A clamped window means data was skipped, so the run is NOT clean even
        # when every trip it did fetch succeeded.
        status = "partial" if (gps_failures or gap) else "ok"
        result = {
            "status": status,
            "trigger": trigger,
            "lane": lane.key,
            "trip_source": lane.source_id,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "blocks_fetched": trips_fetched,
            "trips_fetched": trips_fetched,
            "trips_passed": trips_passed,
            "trips_failed": trips_failed,
            "trips_with_gps": trips_with_gps,
            "trips_upserted": trips_passed,
            "gps_inserted": summary.get("gps_inserted", 0),
            "gps_skipped": summary.get("gps_skipped", 0),
            "gps_failed": gps_deferred,
            "watermark_advanced": not gps_failures,
            # Non-null => this run could not reach back far enough; that span is
            # missing until a backfill covers it.
            "data_gap": gap,
            "legacy_trips_synced": summary.get("legacy_trips_synced", 0),
            "errors": summary.get("errors", []),
            "finished_at": source_now().isoformat(),
            "elapsed_seconds": round((source_now() - started).total_seconds(), 1),
        }
        logger.info("TMS[%s] sync %s (%s): fetched=%d passed=%d failed=%d gps_for=%d/%d trips "
                    "(%d pings, %d dup, %d GPS deferred)",
                    lane.key, status, trigger, trips_fetched, trips_passed, trips_failed,
                    trips_with_gps, trips_fetched, result["gps_inserted"],
                    result["gps_skipped"], gps_deferred)
        _record_run(conn, {
            "trigger": trigger, "lane": lane.key, "status": status,
            "window_start": window_start, "window_end": window_end,
            "blocks_fetched": len(blocks),
            "trips_upserted": summary.get("trips_upserted", 0),
            "gps_inserted": summary.get("gps_inserted", 0),
            "gps_skipped": summary.get("gps_skipped", 0),
            "gps_failed": len(gps_failures),
            "legacy_trips_synced": summary.get("legacy_trips_synced", 0),
            "error": (
                f"DATA GAP {gap['from']} .. {gap['to']} ({gap['hours']}h) — backfill required"
                if gap else
                (f"{len(gps_failures)} trip(s) GPS deferred for retry" if gps_failures else None)
            ),
            "started_at": started, "finished_at": source_now(),
            "elapsed_seconds": result["elapsed_seconds"],
        })
        log_event("run", status=status, detail=f"{lane.key}:{trigger}", lane=lane.key,
                  elapsed_ms=round(result["elapsed_seconds"] * 1000),
                  trips_fetched=trips_fetched, trips_passed=trips_passed,
                  trips_failed=trips_failed, trips_with_gps=trips_with_gps,
                  gps_records=result["gps_inserted"], gps_skipped=result["gps_skipped"],
                  gps_deferred=gps_deferred,
                  error=(result["errors"][0] if result["errors"] else None))
        with _state_lock:
            _last_run[lane.key] = result
        return result

    except Exception as e:
        logger.exception("TMS[%s] sync failed (%s)", lane.key, trigger)
        log_event("run", status="error", detail=f"{lane.key}:{trigger}", lane=lane.key,
                  error=str(e),
                  elapsed_ms=round((datetime.now() - started).total_seconds() * 1000))
        err = {
            "status": "error",
            "trigger": trigger,
            "lane": lane.key,
            "error": str(e),
            "started_at": started.isoformat(),
            "finished_at": source_now().isoformat(),
        }
        with _state_lock:
            _last_run[lane.key] = err
        if conn is not None:
            _record_run(conn, {"trigger": trigger, "lane": lane.key, "status": "error",
                               "error": str(e),
                               "started_at": started, "finished_at": source_now()})
        # Watermark intentionally NOT advanced — next run retries this window.
        return err
    finally:
        _set_running(lane.key, False)
        if conn is not None and db_locked:
            release_db_lane_lock(conn, lane.key)
        if own_conn and conn is not None:
            conn.close()
        lock.release()


def run_waypoint_refresh(conn=None) -> dict:
    """Ask analytics to rebuild the waypoint registry, network aggregates and
    speed rollups now. In Smart-Truck this ran here, inside the sync process;
    in NexGen those tables belong to analytics, which also rebuilds them on its
    own schedule (developer page -> analytics -> jobs)."""
    import httpx
    from nexgen.core.config import get_config
    cfg = get_config()
    url = f"http://{cfg.host}:{cfg.service('analytics').port}/internal/v1/jobs/waypoint-registry/run"
    try:
        r = httpx.post(url, timeout=10.0)
        r.raise_for_status()
        return {"status": "started", "where": "analytics", **r.json()}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": f"analytics did not accept the job: {e}"}


def get_lane_status(conn, lane: Lane) -> dict:
    """Live snapshot for ONE lane — what its card on the ETL screen renders."""
    from nexgen.services.ingestion.tms.tta_sync_config import get_sync_config, effective_entity_ids
    from nexgen.services.ingestion.tms.scheduler import get_next_run_time
    watermark = None
    try:
        wm = get_watermark(conn, lane)
        watermark = wm.isoformat() if wm else None
    except Exception as e:
        logger.warning("Could not read %s watermark: %s", lane.key, e)
    cfg = get_sync_config(conn, lane)
    with _state_lock:
        last_run = dict(_last_run.get(lane.key) or {"status": "never"})
        running = bool(_running.get(lane.key))
    entity_ids = effective_entity_ids(cfg) if lane.entity_scoped else []

    # Lag is THE health metric for an ETL: how far behind the source clock the
    # last fully-captured window ends. A lane can look "enabled, no errors" and
    # still be quietly falling behind — a run that overruns its interval gets
    # its next tick dropped by coalesce, and only lag reveals it.
    interval = int(cfg.get("interval_minutes") or lane.default_interval)
    lag_minutes = None
    if watermark:
        lag_minutes = round((source_now() - datetime.fromisoformat(watermark)).total_seconds() / 60, 1)
    # Budget = a few of this lane's own intervals, PLUS the delay the source
    # itself introduces. Without the second term a lane that tracks the newest
    # published value (local) alerts forever, because it can never reach "now".
    limit = interval * settings.TMS_LAG_ALERT_INTERVALS + lane.expected_source_lag_minutes
    # A lane whose source publishes late can never have zero lag, so its own
    # publication delay is part of the expected baseline, not an alert.
    healthy = (not cfg.get("enabled")) or lag_minutes is None or lag_minutes <= limit

    # Gap counts come from the durable register, so a lane carrying a known
    # hole can never render as plain healthy just because its watermark is
    # fresh. `healthy` stays a LAG verdict; the gap count is reported beside it
    # rather than folded in, because they are different failures needing
    # different fixes (catch up vs. backfill).
    gaps = gap_lane_summary(conn, lane.key)

    return {
        "lane": lane.key,
        "gaps": gaps,
        # What the screen answers "until when do I have data?" with. The
        # watermark is the sync frontier; `earliest_missing` is the caveat.
        "data_through": watermark,
        "lag_minutes": lag_minutes,
        "lag_limit_minutes": round(limit, 1),
        "expected_source_lag_minutes": lane.expected_source_lag_minutes,
        "healthy": bool(healthy),
        "label": lane.label,
        "description": lane.description,
        "endpoint": lane.endpoint,
        "trip_source": lane.source_id,
        "running": running,
        "enabled": bool(cfg.get("enabled")),
        # Judged against THIS lane's source and its effective entity ids — both
        # live in the saved config, so checking the .env seed instead would
        # misreport a screen-configured lane as not configured.
        "ready": settings.sync_ready_for(lane.source_id, entity_ids or None),
        "interval_minutes": cfg.get("interval_minutes"),
        "lookback_minutes": cfg.get("lookback_minutes"),
        "entity_ids": entity_ids,
        "needs_entities": lane.entity_scoped,
        "watermark": watermark,
        "next_scheduled_run": get_next_run_time(lane),
        "last_run": last_run,
    }


def get_sync_status(conn, lane_key: str | None = None) -> dict:
    """Snapshot for /tta/sync/status.

    Returns every lane under "lanes", plus the shared auth + backfill state.
    The top-level keys mirror the requested lane (zonal by default) so older
    clients of this endpoint keep working unchanged.
    """
    lanes = {k: get_lane_status(conn, LANES[k]) for k in LANE_KEYS}
    # Open gaps plus snoozed ones whose time is up — the banner's whole input.
    # Served here rather than from its own endpoint so the screen keeps one poll.
    gaps = actionable_gaps(conn)
    with _state_lock:
        backfill = dict(_backfill_state)
    primary = lanes[get_lane(lane_key).key]
    return {
        **primary,
        "lanes": lanes,
        "any_running": any(v["running"] for v in lanes.values()),
        "auth": get_auth_client().status(),
        "backfill": backfill,
        "data_gaps": gaps,
    }


# ============================================================== #
# Bounded backfill — historical range in day-sized chunks
# ============================================================== #

def get_backfill_status() -> dict:
    with _state_lock:
        return dict(_backfill_state)


def _set_backfill(**extra) -> None:
    with _state_lock:
        _backfill_state.update(extra)


def run_backfill(from_date: datetime, to_date: datetime,
                 chunk_days: int = 1, conn=None, lane_key: str = ZONAL) -> dict:
    """Backfill a historical [from_date, to_date] range in day-sized chunks.

    Shares the sync run-lock (won't overlap a scheduled run). Does NOT move the
    live watermark — backfill fills history; the scheduled sync owns the tail.
    Each chunk is fetched + ingested independently, so a mid-run failure keeps
    the chunks already committed. Dedup makes re-running a range safe.
    """
    lane = get_lane(lane_key)
    if from_date > to_date:
        return {"status": "error", "error": "from_date must be <= to_date"}
    # Shares the LANE's run-lock, so it never overlaps that lane's scheduled
    # tick. The other lane keeps running on its own cadence.
    lock = _lane_lock(lane.key)
    if not lock.acquire(blocking=False):
        logger.info("TMS[%s] sync/backfill already running — backfill skipped", lane.key)
        return {"status": "already_running", "lane": lane.key}

    own_conn = conn is None
    started = datetime.now()
    chunk_days = max(1, int(chunk_days))
    step = timedelta(days=chunk_days)
    total_chunks = 0
    cursor = from_date
    while cursor <= to_date:
        total_chunks += 1
        cursor += step

    _set_backfill(status="running", lane=lane.key, started_at=started.isoformat(),
                  from_date=from_date.isoformat(), to_date=to_date.isoformat(),
                  chunk_days=chunk_days, total_chunks=total_chunks,
                  chunks_done=0, trips_upserted=0, gps_inserted=0,
                  gps_skipped=0, errors=[], error=None)
    try:
        if own_conn:
            conn = get_connection()

        from nexgen.services.ingestion.tms.tta_sync_config import get_sync_config, effective_entity_ids
        bf_cfg = get_sync_config(conn, lane)
        entity_ids = effective_entity_ids(bf_cfg)
        trip_source = lane.source_id

        totals = {"trips": 0, "gps_ins": 0, "gps_skip": 0, "gps_fail": 0}
        chunks_done = 0
        cursor = from_date
        while cursor <= to_date:
            if trip_source == "local_report":
                # The local report takes a date+TIME window whose upper bound is
                # exclusive, so a chunk must run to the start of the next one.
                # Borrowing the date-only convention below would collapse every
                # chunk to 00:00..00:00 — a zero-width window returning nothing.
                chunk_end = min(cursor + step, to_date + timedelta(days=1))
            else:
                chunk_end = min(cursor + step - timedelta(days=1), to_date)
            logger.info("Backfill chunk %d/%d: %s .. %s",
                        chunks_done + 1, total_chunks,
                        cursor.date(), chunk_end.date())
            try:
                blocks, gps_failures = fetch_blocks(cursor, chunk_end, entity_ids,
                                                    trip_source=lane.key)
                if blocks:
                    summary = ingest_serialized(conn, blocks, lane, trigger="backfill",
                                                window=(cursor, chunk_end))
                    totals["trips"] += summary.get("trips_upserted", 0)
                    totals["gps_ins"] += summary.get("gps_inserted", 0)
                    totals["gps_skip"] += summary.get("gps_skipped", 0)
                totals["gps_fail"] += len(gps_failures)
                if gps_failures:
                    # No data lost: re-run this range (idempotent) to fill the
                    # deferred GPS; flag it so the operator knows.
                    _set_backfill(errors=_backfill_state["errors"] + [
                        f"{cursor.date()}..{chunk_end.date()}: {len(gps_failures)} "
                        f"trip(s) GPS deferred — re-run this range to fill"])
            except Exception as e:
                logger.exception("Backfill chunk failed (%s..%s)", cursor.date(), chunk_end.date())
                _set_backfill(errors=_backfill_state["errors"] + [
                    f"{cursor.date()}..{chunk_end.date()}: {e}"])
            chunks_done += 1
            _set_backfill(chunks_done=chunks_done, trips_upserted=totals["trips"],
                          gps_inserted=totals["gps_ins"], gps_skipped=totals["gps_skip"],
                          gps_failed=totals["gps_fail"],
                          percent=round(chunks_done / total_chunks * 100, 1))
            cursor += step

        result = {
            "status": "ok" if totals["gps_fail"] == 0 else "partial",
            "lane": lane.key,
            "from_date": from_date.isoformat(), "to_date": to_date.isoformat(),
            "chunk_days": chunk_days, "chunks": total_chunks,
            "trips_upserted": totals["trips"], "gps_inserted": totals["gps_ins"],
            "gps_skipped": totals["gps_skip"], "gps_failed": totals["gps_fail"],
            "finished_at": datetime.now().isoformat(),
            "elapsed_seconds": round((datetime.now() - started).total_seconds(), 1),
        }
        # A backfill that finished CLEANLY over a recorded gap closes it. Only
        # on "ok": a partial run left GPS deferred, so the span is not whole yet
        # and closing the row would hide what is still missing.
        if result["status"] == "ok":
            # A date-picked range means whole days — "to 2026-09-14" covers that
            # day's 23:59, not its 00:00, or a gap ending mid-afternoon would
            # never match the backfill that just filled it.
            midnight = (to_date.hour == 0 and to_date.minute == 0
                        and to_date.second == 0)
            covered_end = (to_date + timedelta(days=1) - timedelta(seconds=1)
                           if midnight else to_date)
            closed = mark_recovered(conn, lane.key, from_date, covered_end)
            if closed:
                result["gaps_recovered"] = closed
                _set_backfill(gaps_recovered=closed)
        _set_backfill(**result, percent=100.0)
        logger.info("Backfill done: %d chunks, %d trips, %d gps (+%d dup skipped) in %.0fs",
                    total_chunks, totals["trips"], totals["gps_ins"], totals["gps_skip"],
                    result["elapsed_seconds"])
        return result
    except Exception as e:
        logger.exception("Backfill failed")
        _set_backfill(status="error", error=str(e), finished_at=datetime.now().isoformat())
        return {"status": "error", "error": str(e)}
    finally:
        if own_conn and conn is not None:
            conn.close()
        lock.release()
