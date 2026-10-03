"""Orchestrate a run: raw trail in -> filter and fit -> detect -> ledger out.

Per trip, in a worker process:

    geo_gps_ping --clean--> admissible fixes --fit--> fitted positions
                                                          |
                         FenceTracker (unchanged) <-------+
                                |
          events, visits, violations    stops, gaps    inferred passages

and the parent writes all of it, including the fitted position of every ping
(`geo_fit_trail`, one compressed row per trip -- see prep/codec.py), so any
visit can be traced back to the exact positions it was decided on.

Variants
--------
`fitted` (default) detects on fitted positions. `raw` detects on the cleaned
raw fixes exactly as the engine always has. Running both over the same scope
and diffing them (`pipeline/compare.py`) is how the fit stage earns its place
on this data rather than by assertion.

Concurrency
-----------
The work is embarrassingly parallel over trips -- no trip's verdict depends on
another's -- so trips are handed to a process pool. Processes, not threads:
the hot loop is numpy and pure-Python control flow, and the GIL makes threads
useless for the latter. Each worker builds its fence index and opens its feed
connection once and reuses them for every trip it is given.

Workers return database-ready tuples, not engine objects. A `Fence` owns
several numpy arrays and pickling one per visit would dominate the run.

Writing
-------
The parent does all the writing, in batches, one transaction per batch.
Workers never touch the ledger, so there is one writer and no lock contention.
A run is recorded even when it fails: `geo_run.s_status` moves to `failed`
with the error, so a half-finished run is visible as such.

Publishing
----------
The application pages read the one run with `b_published = 1`. A run is only
published when asked (`--publish`), after it has finished and its rollups are
built, so a new run can be checked before anyone is shown its numbers.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta

import pymysql

from nexgen.shared.geoengine.config import DetectorConfig, FitConfig, settings
from nexgen.shared.geoengine.db import geo_session
from nexgen.shared.geoengine.engine.evaluator import evaluate_trail
from nexgen.shared.geoengine.engine.inferred import passages
from nexgen.shared.geoengine.osrm.client import OsrmClient, OsrmConfig
from nexgen.shared.geoengine.pipeline import source
from nexgen.shared.geoengine.prep import codec
from nexgen.shared.geoengine.prep.fit import prepare
from nexgen.shared.geoengine.store import build_index, max_tolerance_m, scale_of
from nexgen.shared.geoengine.winperf import opt_out_of_power_throttling

logger = logging.getLogger(__name__)

BATCH_TRIPS = 100
FACILITY_SCALES = ("micro", "site", "campus")

# Per-worker globals, set once by the pool initialiser.
_IDX = None
_DET: DetectorConfig | None = None
_FIT: FitConfig | None = None
_OSRM: OsrmClient | None = None
_FEED = "geo"
_CONN = None


def _init_worker(det: dict, fit: dict, osrm: dict, feed: str) -> None:
    global _IDX, _DET, _FIT, _OSRM, _FEED, _CONN
    logging.basicConfig(level=logging.WARNING)
    opt_out_of_power_throttling()
    _DET = DetectorConfig(**det)
    _FIT = FitConfig(**fit)
    ocfg = OsrmConfig(**osrm)
    _OSRM = OsrmClient(ocfg) if ocfg.enabled else None
    _IDX = build_index(active_only=True, pad_m=max_tolerance_m())
    _FEED = feed
    _CONN = None


def _feed_rows(trip_no: int) -> list[dict]:
    global _CONN
    for attempt in (1, 2):
        try:
            if _CONN is None:
                _CONN = source._feed_conn(_FEED)
            return source.fetch_trip_feed(trip_no, feed=_FEED, conn=_CONN)
        except Exception:                          # noqa: BLE001
            if _CONN is not None:
                try:
                    _CONN.close()
                except Exception:                  # noqa: BLE001
                    pass
            _CONN = None
            if attempt == 2:
                raise
    return []


def _process_trip(trip_no: int) -> dict:
    """Fit and evaluate one trip. Runs inside a worker process."""
    rows = _feed_rows(trip_no)
    asset = next((r["s_asset_id"] for r in rows if r.get("s_asset_id")), None)
    fit = prepare(rows, _FIT, _DET, _OSRM)
    res = evaluate_trail(fit.trail, _IDX, _DET, trip_no=trip_no, asset_id=asset)
    inferred = []
    if any(g.route is not None for g in fit.gaps):
        inferred = passages(fit.gaps, _IDX, _DET, res.visits)
    return _to_rows(res, fit, inferred, trip_no, asset)


# ---------------------------------------------------------------------------
# Shaping results for the writer
# ---------------------------------------------------------------------------

def _facility_at(lat: float, lon: float):
    """Innermost facility-scale fence containing a point, or None."""
    best = None
    pad = 0.0012  # ~130 m: covers the largest declared tolerance
    for f in _IDX.query_box(lon - pad, lat - pad, lon + pad, lat + pad):
        if scale_of(f) not in FACILITY_SCALES:
            continue
        if f.contains(lat, lon) and (best is None or (f.area_m2, f.fence_id) < (best.area_m2, best.fence_id)):
            best = f
    return best


def places(visits: list[dict]) -> list[dict]:
    """Collapse nested facility visits into places.

    A truck at the hot strip mill is inside the mill, the works and the
    works' gate zone at once; for "where did the trip go" that is one place,
    named by its outermost facility fence. Visits overlapping in time are
    clustered and the largest fence names the cluster.
    """
    fac = sorted((v for v in visits if scale_of(v["fence"]) in FACILITY_SCALES),
                 key=lambda v: v["dt_enter"])
    clusters: list[dict] = []
    for v in fac:
        # An open visit (still inside when the trail ends) runs to its last
        # observed fix, which is what its dwell measures.
        end = v["dt_exit"] or v["dt_enter"] + timedelta(seconds=v["dwell_seconds"] or 0)
        if clusters and v["dt_enter"] <= clusters[-1]["end"]:
            c = clusters[-1]
            c["members"].append(v)
            c["end"] = max(c["end"], end)
            c["open"] = c["open"] or v["open"]
        else:
            clusters.append({"start": v["dt_enter"], "end": end, "open": v["open"], "members": [v]})
    return [{
        "fence": max(c["members"], key=lambda v: (v["fence"].area_m2, -v["fence"].fence_id))["fence"],
        "enter": c["start"],
        "exit": None if c["open"] else c["end"],
        "dwell_s": int((c["end"] - c["start"]).total_seconds()),
    } for c in clusters]


def _to_rows(res, fit, inferred, trip_no, asset) -> dict:
    trail = fit.trail
    events = [
        (trip_no, asset, e["fence"].fence_id, e["fence"].site_id, e["fence"].name,
         e["event"], e["ts"], e["gap_seconds"], e["lat"], e["lon"],
         e["speed"], e["confirmed_by"])
        for e in res.events
    ]
    visits = [
        (trip_no, asset, v["fence"].fence_id, v["fence"].site_id, v["fence"].name,
         v["fence"].site_type, v["fence"].category, v["dt_enter"], v["dt_exit"],
         1 if v["open"] else 0, v["dwell_seconds"], v["pings"], v["max_speed"],
         v["distance_m"], v["enter_gap"], v["exit_gap"],
         1 if v["primary"] else 0, 1 if v["entry_observed"] else 0,
         v["confirmed_by"], scale_of(v["fence"]))
        for v in res.visits
    ]
    violations = [
        (trip_no, asset, v["fence"].fence_id, v["fence"].site_id, v["fence"].name,
         v["kind"], v["ts"], v["lat"], v["lon"], v["observed"], v["limit"], v["detail"])
        for v in res.violations
    ]

    stops = []
    for s in fit.stops:
        f = _facility_at(s.lat, s.lon)
        stops.append((trip_no, asset, s.seq, s.dt_start, s.dt_end, s.duration_s, s.pings,
                      s.spikes, s.max_gap_s, round(s.lat, 8), round(s.lon, 8),
                      round(s.p90_spread_m, 1),
                      f.fence_id if f else None, f.site_id if f else None,
                      f.name if f else None, scale_of(f) if f else None))

    gaps = [
        (trip_no, asset, g.dt_from, g.dt_to, g.gap_s,
         round(g.from_lat, 8), round(g.from_lon, 8), round(g.to_lat, 8), round(g.to_lon, 8),
         round(g.straight_m, 1), g.kind, g.route_status,
         round(g.route.distance_m, 1) if g.route else None,
         round(g.route.duration_s, 1) if g.route else None,
         g.unexplained_s, g.route.polyline6 if g.route else None)
        for g in fit.gaps
    ]

    inferred_rows = [
        (trip_no, asset, p.window_from, p.window_to, p.fence.fence_id, p.fence.site_id,
         p.fence.name, p.fence.category, scale_of(p.fence), p.kind, p.est_enter, p.est_exit,
         round(p.inside_m, 1), round(p.min_dist_m, 1), p.confidence)
        for p in inferred
    ]

    pl = places(res.visits)
    first = pl[0] if pl else None
    last = pl[-1] if len(pl) > 1 else None
    transit = None
    if first and last and first["exit"] and last["enter"] > first["exit"]:
        transit = int((last["enter"] - first["exit"]).total_seconds())
    facility_primary = sum(1 for v in res.visits
                           if v["primary"] and scale_of(v["fence"]) in FACILITY_SCALES)
    moving_gaps = [g for g in fit.gaps if g.kind == "moving"]

    s = res.summary
    summary = (
        trip_no, asset, s["dt_first_ping"], s["dt_last_ping"], s["pings_read"],
        s["pings_used"], s["pings_dropped"], s["pings_inside"], s["visits"],
        s["distinct_sites"], s["violations"], s["inside_seconds"],
        s["max_gap_seconds"], s["coverage_pct"], fit.quality,
        len(fit.stops), fit.spikes, fit.medians, fit.snapped,
        len(moving_gaps), sum(g.gap_s for g in moving_gaps), len(inferred),
        (fit.quality_reason or None) and fit.quality_reason[:96], fit.osrm_status,
        round(fit.distance_m / 1000, 3), round(fit.gap_distance_m / 1000, 3),
        facility_primary, sum(p["dwell_s"] for p in pl), len(pl),
        first["fence"].site_id if first else None, first["fence"].name if first else None,
        first["enter"] if first else None, first["exit"] if first else None,
        last["fence"].site_id if last else None, last["fence"].name if last else None,
        last["enter"] if last else None, last["exit"] if last else None,
        transit,
    )
    rejects = [(trip_no, reason, n) for reason, n in trail.dropped.items() if n]
    ping_rows = fit.ping_rows(trip_no)
    per_day: dict = {}
    for r in ping_rows:
        d = per_day.setdefault(r[2].date(), [0, 0, 0, 0, 0])
        d[0] += 1
        if r[3] == "reject":
            d[1] += 1
        d[2] += r[8] == "spike"
        d[3] += r[8] == "median"
        d[4] += r[8] == "snapped"
    trip_days = [(trip_no, day, asset, *counts) for day, counts in per_day.items()]
    fit_trail = (trip_no, len(ping_rows), codec.CODEC, codec.encode(ping_rows)) if ping_rows else None
    return {"events": events, "visits": visits, "violations": violations,
            "summary": summary, "rejects": rejects,
            "fit_trail": fit_trail, "trip_days": trip_days, "stops": stops, "gaps": gaps,
            "inferred": inferred_rows,
            "read": trail.read, "used": trail.used, "dropped": trail.total_dropped,
            "spikes": fit.spikes, "medians": fit.medians, "snapped": fit.snapped,
            "n_gaps": len(fit.gaps), "n_moving_gaps": len(moving_gaps),
            "n_routed": sum(1 for g in fit.gaps if g.route is not None),
            "osrm": fit.osrm_status}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_EVENT_SQL = """INSERT INTO geo_event
    (i_run_id,i_trip_no,s_asset_id,i_fence_id,i_site_id,s_site_name,s_event,
     dt_event,i_gap_seconds,d_lat,d_long,i_speed,s_confirmed_by)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_VISIT_SQL = """INSERT INTO geo_visit
    (i_run_id,i_trip_no,s_asset_id,i_fence_id,i_site_id,s_site_name,s_type,
     s_category,dt_enter,dt_exit,b_open,i_dwell_seconds,i_pings,i_max_speed,
     d_distance_m,i_enter_gap_seconds,i_exit_gap_seconds,b_primary,
     b_entry_observed,s_confirmed_by,s_scale)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_VIOLATION_SQL = """INSERT INTO geo_violation
    (i_run_id,i_trip_no,s_asset_id,i_fence_id,i_site_id,s_site_name,s_kind,
     dt_event,d_lat,d_long,i_observed,i_limit,s_detail)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_SUMMARY_COLS = (
    "i_run_id,i_trip_no,s_asset_id,dt_first_ping,dt_last_ping,i_pings_read,"
    "i_pings_used,i_pings_dropped,i_pings_inside,i_visits,i_distinct_sites,"
    "i_violations,i_inside_seconds,i_max_gap_seconds,d_coverage_pct,s_quality,"
    "i_stops,i_spikes,i_medians,i_snapped,i_moving_gaps,i_moving_gap_s,i_inferred,"
    "s_quality_reason,s_osrm,d_distance_km,d_gap_distance_km,i_facility_visits,"
    "i_facility_dwell_s,i_places,i_first_site_id,s_first_site,dt_first_enter,"
    "dt_first_exit,i_last_site_id,s_last_site,dt_last_enter,dt_last_exit,i_transit_s"
).split(",")
_SUMMARY_SQL = (
    f"INSERT INTO geo_trip_summary ({','.join(_SUMMARY_COLS)}) "
    f"VALUES ({','.join(['%s'] * len(_SUMMARY_COLS))}) ON DUPLICATE KEY UPDATE "
    + ",".join(f"{c}=VALUES({c})" for c in _SUMMARY_COLS[2:])
)

_REJECT_SQL = """INSERT INTO geo_ping_reject (i_run_id,i_trip_no,s_reason,i_count)
    VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE i_count=VALUES(i_count)"""

_FIT_SQL = """INSERT INTO geo_fit_trail (i_run_id,i_trip_no,i_pings,s_codec,m_data)
    VALUES (%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE i_pings=VALUES(i_pings), s_codec=VALUES(s_codec), m_data=VALUES(m_data)"""

_TRIP_DAY_SQL = """INSERT INTO geo_trip_day
    (i_run_id,i_trip_no,d_day,s_asset_id,i_pings,i_rejected,i_spikes,i_medians,i_snapped)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_STOP_SQL = """INSERT INTO geo_stop
    (i_run_id,i_trip_no,s_asset_id,i_seq,dt_start,dt_end,i_duration_s,i_pings,i_spikes,
     i_max_gap_s,d_lat,d_long,d_p90_spread_m,i_fence_id,i_site_id,s_site_name,s_scale)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_GAP_SQL = """INSERT INTO geo_gap
    (i_run_id,i_trip_no,s_asset_id,dt_from,dt_to,i_gap_s,d_from_lat,d_from_long,d_to_lat,
     d_to_long,d_straight_m,s_kind,s_route,d_route_m,d_route_s,i_unexplained_s,s_geometry)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_INFERRED_SQL = """INSERT INTO geo_inferred_visit
    (i_run_id,i_trip_no,s_asset_id,dt_gap_from,dt_gap_to,i_fence_id,i_site_id,s_site_name,
     s_category,s_scale,s_kind,dt_est_enter,dt_est_exit,d_inside_m,d_min_dist_m,s_confidence)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""

_LEDGER = (
    ("events", _EVENT_SQL), ("visits", _VISIT_SQL), ("violations", _VIOLATION_SQL),
    ("stops", _STOP_SQL), ("gaps", _GAP_SQL), ("inferred", _INFERRED_SQL),
)

# Every table a trip writes into, for per-trip replacement.
TRIP_TABLES = ("geo_event", "geo_visit", "geo_violation", "geo_trip_summary",
               "geo_ping_reject", "geo_fit_trail", "geo_trip_day", "geo_stop", "geo_gap",
               "geo_inferred_visit")


def _flush(run_id: int, buf: list[dict], persist_fit: bool, replace: bool = False) -> dict:
    counts = {"events": 0, "visits": 0, "violations": 0, "stops": 0, "gaps": 0, "inferred": 0}
    if not buf:
        return counts
    with geo_session() as conn:
        with conn.cursor() as cur:
            if replace:
                trips = [b["summary"][0] for b in buf]
                marks = ",".join(["%s"] * len(trips))
                for table in TRIP_TABLES:
                    cur.execute(f"DELETE FROM {table} WHERE i_run_id=%s AND i_trip_no IN ({marks})",
                                (run_id, *trips))
            for key, sql in _LEDGER:
                rows = [(run_id, *r) for b in buf for r in b[key]]
                if rows:
                    cur.executemany(sql, rows)
                    counts[key] += len(rows)
            summaries = [(run_id, *b["summary"]) for b in buf]
            if summaries:
                cur.executemany(_SUMMARY_SQL, summaries)
            rejects = [(run_id, *r) for b in buf for r in b["rejects"]]
            if rejects:
                cur.executemany(_REJECT_SQL, rejects)
            days = [(run_id, *r) for b in buf for r in b["trip_days"]]
            if days:
                cur.executemany(_TRIP_DAY_SQL, days)
            if persist_fit:
                for b in buf:
                    if b["fit_trail"]:
                        cur.execute(_FIT_SQL, (run_id, *b["fit_trail"]))
        conn.commit()
    return counts


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run(
    trip_nos: list[int] | None = None,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
    limit: int | None = None,
    workers: int | None = None,
    mode: str = "batch",
    scope: str | None = None,
    detector: DetectorConfig | None = None,
    fit: FitConfig | None = None,
    osrm: OsrmConfig | None = None,
    feed: str = "geo",
    persist_fit: bool = True,
    publish: bool = False,
    summarise: bool = True,
    progress=None,
) -> dict:
    """Evaluate a set of trips and persist the ledger. Returns the run row."""
    det = detector or settings.detector
    fcfg = fit or settings.fit
    ocfg = osrm or settings.osrm
    opt_out_of_power_throttling()
    if feed not in source.FEEDS:
        raise ValueError(f"feed must be one of {source.FEEDS}")
    started = datetime.now()
    # Only a run over every trip in the feed can later be kept current by refresh().
    whole_feed = trip_nos is None and not limit and dt_from is None and dt_to is None

    if trip_nos is None:
        trip_nos = source.list_trips_feed(feed, dt_from=dt_from, dt_to=dt_to, limit=limit)
    elif limit:
        trip_nos = trip_nos[:limit]

    scope = scope or (
        f"{len(trip_nos)} trips"
        + (f" from {dt_from}" if dt_from else "")
        + (f" to {dt_to}" if dt_to else "")
    )
    osrm_health = OsrmClient(ocfg).health() if ocfg.enabled else {"enabled": False, "status": "off"}
    params = {**det.as_dict(), **fcfg.as_dict(), **ocfg.as_dict(), "whole_feed": whole_feed}

    with geo_session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO geo_run
                   (dt_started,s_mode,s_scope,dt_from,dt_to,i_trips,j_params,s_status,
                    s_variant,s_feed,s_osrm,j_osrm)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'running',%s,%s,%s,%s)""",
                (started, mode, scope[:255], dt_from, dt_to, len(trip_nos),
                 json.dumps(params), fcfg.variant, feed, osrm_health["status"],
                 json.dumps(osrm_health)),
            )
            run_id = cur.lastrowid
        conn.commit()

    totals = {"events": 0, "visits": 0, "violations": 0, "stops": 0, "gaps": 0,
              "inferred": 0, "read": 0, "used": 0, "dropped": 0, "spikes": 0,
              "medians": 0, "snapped": 0, "n_gaps": 0, "n_moving_gaps": 0, "n_routed": 0}
    osrm_seen: dict[str, int] = {}
    t0 = time.perf_counter()

    try:
        if not trip_nos:
            raise RuntimeError("no trips in scope")

        init = (det.as_dict(), dataclasses.asdict(fcfg), dataclasses.asdict(ocfg), feed)
        n_workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
        # One trip is not worth a process pool, and a pool of 1 just adds
        # pickling to a single-threaded run.
        if n_workers <= 1 or len(trip_nos) <= 4:
            _init_worker(*init)
            results = (_process_trip(t) for t in trip_nos)
            _consume(run_id, results, trip_nos, totals, osrm_seen, persist_fit, progress)
        else:
            with ProcessPoolExecutor(max_workers=n_workers, initializer=_init_worker,
                                     initargs=init) as pool:
                results = pool.map(_process_trip, trip_nos, chunksize=2)
                _consume(run_id, results, trip_nos, totals, osrm_seen, persist_fit, progress)

        elapsed = time.perf_counter() - t0
        index_stats = build_index(active_only=True).stats
        # The run's OSRM verdict is the worst any trip saw, not the probe's.
        run_osrm = "off" if not ocfg.enabled else (
            "unreachable" if osrm_seen.get("unreachable") else
            "partial" if (osrm_seen.get("partial") or osrm_seen.get("error")) else "ok")

        with geo_session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE geo_run SET dt_finished=%s, i_pings_read=%s, i_pings_used=%s,
                              i_pings_dropped=%s, i_events=%s, i_visits=%s, i_violations=%s,
                              d_seconds=%s, d_pings_per_sec=%s, j_index_stats=%s, s_status='ok',
                              s_osrm=%s, i_stops=%s, i_spikes=%s, i_medians=%s, i_snapped=%s,
                              i_gaps=%s, i_moving_gaps=%s, i_gaps_routed=%s, i_inferred=%s
                        WHERE i_run_id=%s""",
                    (datetime.now(), totals["read"], totals["used"], totals["dropped"],
                     totals["events"], totals["visits"], totals["violations"],
                     round(elapsed, 3),
                     round(totals["read"] / elapsed, 1) if elapsed > 0 else None,
                     json.dumps(index_stats), run_osrm, totals["stops"], totals["spikes"],
                     totals["medians"], totals["snapped"], totals["n_gaps"],
                     totals["n_moving_gaps"], totals["n_routed"], totals["inferred"], run_id),
                )
            conn.commit()

        if summarise:
            from nexgen.shared.geoengine.pipeline import summaries
            summaries.build(run_id)
        if publish:
            publish_run(run_id)

        return {"run_id": run_id, "trips": len(trip_nos), "seconds": round(elapsed, 2),
                "pings_per_sec": round(totals["read"] / elapsed) if elapsed else None,
                "variant": fcfg.variant, "osrm": run_osrm, "published": publish, **totals}

    except BaseException as exc:
        logger.exception("run %s failed", run_id)
        with geo_session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE geo_run SET dt_finished=%s, s_status='failed', s_error=%s WHERE i_run_id=%s",
                    (datetime.now(), f"{type(exc).__name__}: {exc}"[:4000], run_id),
                )
            conn.commit()
        raise


def publish_run(run_id: int) -> None:
    """Make `run_id` the run the application reads. Exactly one is published."""
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("SELECT s_status FROM geo_run WHERE i_run_id=%s", (run_id,))
        row = cur.fetchone()
        if not row or row["s_status"] != "ok":
            raise ValueError(f"run {run_id} is not a finished run")
        cur.execute("UPDATE geo_run SET b_published = (i_run_id = %s)", (run_id,))
        conn.commit()


# geo_trip_route / geo_route_deviation belong to the routing service in
# NexGen; it drops a deleted run's rows when it hears of the deletion.
RUN_TABLES = TRIP_TABLES + ("geo_fence_stats", "geo_fence_day", "geo_day_summary",
                             "geo_pvisit", "geo_palert", "geo_pstop", "geo_trip_share",
                             "geo_trip_phase")


# ---------------------------------------------------------------------------
# Keeping a run current on a live feed
# ---------------------------------------------------------------------------

def stale_trips(run_id: int) -> list[int]:
    """Trips holding fixes that `run_id` has not evaluated.

    Counted straight from geo_gps_ping, so fixes are seen however they
    arrived -- `sync-fleet`, or a feed writing to the table directly. The
    table is append-only (a fix is keyed by its source id), so a trip whose
    count differs from what the run read has received fixes since, and a trip
    the run has no summary for is new. Last-fix times are not compared: a
    trip whose final fix the pre-filter refused would look stale for ever.
    """
    with geo_session() as conn, conn.cursor() as cur:
        # NexGen: the per-trip fix count is maintained by the fleet service
        # (geo_trip.i_pings = trip.i_gps_ping_count), so no scan of the feed.
        cur.execute("""SELECT g.i_trip_no
                         FROM geo_trip g
                         LEFT JOIN geo_trip_summary s ON s.i_run_id = %s AND s.i_trip_no = g.i_trip_no
                        WHERE s.i_trip_no IS NULL OR s.i_pings_read <> g.i_pings
                        ORDER BY g.i_trip_no""", (run_id,))
        return [r["i_trip_no"] for r in cur.fetchall()]


def _upsert_trips(trip_nos: list[int]) -> None:
    """geo_trip rows for trips the feed extended or introduced, so every
    later join -- a trail's vehicle, a trip's span -- sees the new fixes.

    NexGen: geo_trip is a view over the fleet service, which keeps every
    trip's span and fix count as it stores the fixes. Nothing to write."""
    return
    for i in range(0, len(trip_nos), 1000):
        chunk = trip_nos[i:i + 1000]
        marks = ",".join(["%s"] * len(chunk))
        with geo_session() as conn, conn.cursor() as cur:
            cur.execute(f"""
                INSERT INTO geo_trip (i_trip_no, s_asset_id, dt_first_ping, dt_last_ping, i_pings)
                SELECT i_trip_no, MAX(s_asset_id), MIN(dt_message), MAX(dt_message), COUNT(*)
                  FROM geo_gps_ping WHERE i_trip_no IN ({marks}) GROUP BY i_trip_no
                ON DUPLICATE KEY UPDATE
                    s_asset_id = COALESCE(VALUES(s_asset_id), geo_trip.s_asset_id),
                    dt_first_ping = LEAST(geo_trip.dt_first_ping, VALUES(dt_first_ping)),
                    dt_last_ping = GREATEST(geo_trip.dt_last_ping, VALUES(dt_last_ping)),
                    i_pings = VALUES(i_pings)""", chunk)
            conn.commit()


def _recount(run_id: int) -> None:
    """Re-derive the run row's totals from its ledger after trips were swapped in."""
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("""SELECT COUNT(*) trips, COALESCE(SUM(i_pings_read),0) rd, COALESCE(SUM(i_pings_used),0) us,
                              COALESCE(SUM(i_pings_dropped),0) dr, COALESCE(SUM(i_stops),0) st,
                              COALESCE(SUM(i_spikes),0) sp, COALESCE(SUM(i_medians),0) md,
                              COALESCE(SUM(i_snapped),0) sn, COALESCE(SUM(i_moving_gaps),0) mg,
                              COALESCE(SUM(i_inferred),0) inf
                         FROM geo_trip_summary WHERE i_run_id=%s""", (run_id,))
        s = cur.fetchone()
        n = {}
        for table in ("geo_event", "geo_visit", "geo_violation"):
            cur.execute(f"SELECT COUNT(*) n FROM {table} WHERE i_run_id=%s", (run_id,))
            n[table] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n, COALESCE(SUM(s_route='ok'),0) routed FROM geo_gap WHERE i_run_id=%s",
                    (run_id,))
        g = cur.fetchone()
        cur.execute("""UPDATE geo_run SET i_trips=%s, i_pings_read=%s, i_pings_used=%s, i_pings_dropped=%s,
                              i_events=%s, i_visits=%s, i_violations=%s, i_stops=%s, i_spikes=%s,
                              i_medians=%s, i_snapped=%s, i_gaps=%s, i_moving_gaps=%s, i_gaps_routed=%s,
                              i_inferred=%s
                        WHERE i_run_id=%s""",
                    (s["trips"], s["rd"], s["us"], s["dr"], n["geo_event"], n["geo_visit"],
                     n["geo_violation"], s["st"], s["sp"], s["md"], s["sn"], g["n"], s["mg"],
                     g["routed"], s["inf"], run_id))
        conn.commit()


def refresh(run_id: int | None = None, batch: int = 200, dry_run: bool = False) -> dict:
    """Bring a run up to date with the feed.

    Every stale trip is re-evaluated whole into the run with the run's own
    recorded settings (see `reprocess`), in batches, and the run row is
    recounted. The caller rebuilds the summaries afterwards -- the physical
    ledger is cross-trip, so it is rebuilt as a whole, never patched.

    Only a run over the whole feed can be refreshed: a run limited to a date
    range or a list of trips would silently grow past its selection.
    """
    run_id = run_id or published_run_id()
    if run_id is None:
        raise LookupError("no finished run to refresh")
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("SELECT s_status, dt_from, dt_to, j_params FROM geo_run WHERE i_run_id=%s", (run_id,))
        row = cur.fetchone()
    if not row or row["s_status"] != "ok":
        raise ValueError(f"run {run_id} is not a finished run")
    params = json.loads(row["j_params"]) if isinstance(row["j_params"], str) else (row["j_params"] or {})
    # Runs from before the flag existed were whole-feed unless date-limited.
    if not params.get("whole_feed", not (row["dt_from"] or row["dt_to"])):
        raise ValueError(f"run {run_id} covers a selection of trips; refresh only extends whole-feed runs")

    t0 = time.perf_counter()
    trips = stale_trips(run_id)
    out = {"run_id": run_id, "stale_trips": len(trips), "sample": trips[:20]}
    if dry_run or not trips:
        return out
    _upsert_trips(trips)
    counts: dict = {}
    for i in range(0, len(trips), batch):
        done = reprocess(run_id, trips[i:i + batch])
        for k, v in done.items():
            if isinstance(v, int) and k not in ("run_id", "trips"):
                counts[k] = counts.get(k, 0) + v
        logger.info("refresh run %s: %s/%s trips", run_id, min(i + batch, len(trips)), len(trips))
    _recount(run_id)
    return {**out, **counts, "seconds": round(time.perf_counter() - t0, 1)}


def delete_run(run_id: int) -> dict:
    """Remove a run and everything it wrote. Refuses the published run."""
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("SELECT b_published FROM geo_run WHERE i_run_id=%s", (run_id,))
        row = cur.fetchone()
        if not row:
            raise LookupError(f"no run {run_id}")
        if row["b_published"]:
            raise ValueError(f"run {run_id} is published; publish another run first")
        removed = {}
        for table in RUN_TABLES:
            try:
                cur.execute(f"DELETE FROM {table} WHERE i_run_id=%s", (run_id,))
            except pymysql.err.ProgrammingError as exc:
                if exc.args[0] == 1146:          # a table from a later schema, not created here yet
                    continue
                raise
            removed[table] = cur.rowcount
        cur.execute("DELETE FROM geo_run WHERE i_run_id=%s", (run_id,))
        conn.commit()
    return removed


def published_run_id(conn=None) -> int | None:
    """The published run, or the newest finished one if none is published."""
    close = conn is None
    from nexgen.shared.geoengine.db import geo_conn
    conn = conn or geo_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT i_run_id FROM geo_run WHERE s_status='ok'
                            ORDER BY b_published DESC, i_run_id DESC LIMIT 1""")
            row = cur.fetchone()
            return row["i_run_id"] if row else None
    finally:
        if close:
            conn.close()


def _consume(run_id, results, trip_nos, totals, osrm_seen, persist_fit, progress) -> None:
    buf: list[dict] = []
    done = 0
    for r in results:
        buf.append(r)
        for k in ("read", "used", "dropped", "spikes", "medians", "snapped",
                  "n_gaps", "n_moving_gaps", "n_routed"):
            totals[k] += r[k]
        osrm_seen[r["osrm"]] = osrm_seen.get(r["osrm"], 0) + 1
        done += 1
        if len(buf) >= BATCH_TRIPS:
            for k, v in _flush(run_id, buf, persist_fit).items():
                totals[k] += v
            buf = []
            if progress:
                progress(done, len(trip_nos), totals)
    for k, v in _flush(run_id, buf, persist_fit).items():
        totals[k] += v
    if progress:
        progress(done, len(trip_nos), totals)


def reprocess(run_id: int, trip_nos: list[int], feed: str = "geo",
              persist_fit: bool = True, workers: int | None = None) -> dict:
    """Re-evaluate some trips *into an existing run*, replacing their rows.

    This is how a published run stays current on a live feed: trips that
    received new fixes are re-fitted and re-detected whole, with the run's own
    recorded settings, and their ledger rows swapped in one transaction per
    batch. A trip is always recomputed from its full trail, never patched,
    because a later fix can overturn an earlier unconfirmed state.
    """
    with geo_session() as conn, conn.cursor() as cur:
        cur.execute("SELECT j_params FROM geo_run WHERE i_run_id=%s", (run_id,))
        row = cur.fetchone()
    if not row:
        raise LookupError(f"no run {run_id}")
    params = json.loads(row["j_params"]) if isinstance(row["j_params"], str) else row["j_params"]
    det = DetectorConfig(**{k: params[k] for k in DetectorConfig().as_dict() if k in params})
    fit = FitConfig(**{f.name: params[f"fit_{f.name}"] for f in dataclasses.fields(FitConfig)
                       if f"fit_{f.name}" in params})
    ocfg = settings.osrm
    init = (det.as_dict(), dataclasses.asdict(fit), dataclasses.asdict(ocfg), feed)
    # With `workers`, trips are evaluated across processes as a run does --
    # what keeps a refresh inside its interval when hundreds of trucks moved
    # since the last one. A pool is not worth starting for a handful.
    if workers and workers > 1 and len(trip_nos) >= 20:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=init) as pool:
            results = list(pool.map(_process_trip, trip_nos, chunksize=2))
    else:
        _init_worker(*init)
        results = [_process_trip(t) for t in trip_nos]
    counts = {"events": 0, "visits": 0, "violations": 0, "stops": 0, "gaps": 0, "inferred": 0}
    for i in range(0, len(results), BATCH_TRIPS):
        for k, v in _flush(run_id, results[i:i + BATCH_TRIPS], persist_fit, replace=True).items():
            counts[k] += v
    return {"run_id": run_id, "trips": len(trip_nos), **counts}
