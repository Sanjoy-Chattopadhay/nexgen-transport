"""Data quality and the pipeline: how far every other page can be trusted.

Status, runs, the fit stage's work, GPS gaps and refused fixes, and the
raw-versus-fitted comparison. This is the page to open before arguing with a
number anywhere else.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from nexgen.shared.geoengine.api import cache as response_cache
from nexgen.shared.geoengine.api.v1.common import clean, resolve_run, rows
from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.osrm.client import OsrmClient
from nexgen.shared.geoengine.pipeline import scheduler

router = APIRouter(tags=["data quality"])

_compare_cache: dict[tuple[int, int], dict] = {}
_compare_lock = threading.Lock()


def _params(run_row: dict) -> dict:
    p = run_row.get("j_params")
    return json.loads(p) if isinstance(p, str) else (p or {})


@router.get("/status")
def status(conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("""SELECT i_run_id, dt_started, dt_finished, s_scope, s_variant, s_feed, s_osrm,
                              i_trips, i_pings_read, i_visits, b_published, dt_summarised
                         FROM geo_run WHERE s_status='ok' ORDER BY b_published DESC, i_run_id DESC LIMIT 1""")
        published = cur.fetchone()
        # MIN/MAX come off the dt_message index in O(1); a COUNT(*) over the
        # feed is a full scan of millions of rows and this is called on every
        # page load, so the count comes from the per-trip rollup instead.
        cur.execute("SELECT MIN(dt_message) a, MAX(dt_message) b FROM geo_gps_ping")
        feed = cur.fetchone()
        cur.execute("SELECT COALESCE(SUM(i_pings), 0) n FROM geo_trip")
        feed["n"] = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n, SUM(b_active) active FROM geo_fence")
        fences = cur.fetchone()
        cur.execute("SELECT COUNT(*) n FROM geo_trip_meta")
        meta = cur.fetchone()["n"]
        cur.execute("SELECT MAX(dt_updated) u, MAX(dt_message) t, COUNT(*) n FROM geo_live_position")
        live = cur.fetchone()
    live_running = bool(live["u"] and (datetime.now() - live["u"]).total_seconds() < 90)
    return {
        "app": "Geofence Intelligence", "client": "Tata Steel",
        "published_run": clean(published) if published else None,
        "feed": {"pings": int(feed["n"]), "from": feed["a"], "to": feed["b"]},
        "fences": {"total": int(fences["n"]), "active": int(fences["active"] or 0)},
        "trip_meta": int(meta),
        "live": {"running": live_running, "feed_time": live["t"], "vehicles": int(live["n"])},
        "osrm": {"configured": settings.osrm.enabled, "url": settings.osrm.url},
        "refresh": scheduler.status(),
        "cache": response_cache.info(),
    }


@router.post("/refresh")
def request_refresh():
    """Ask the background scheduler for a pass now. It picks the request up
    within its poll interval; without a scheduler running nothing happens,
    and the answer says so."""
    scheduler.request_refresh()
    st = scheduler.status()
    return {"requested": True, "scheduler_running": st.get("enabled", False), "status": st}


@router.get("/jobs")
def list_jobs(limit: int = 30, conn=Depends(get_geo_db)):
    """The scheduler's recent passes: what each found and how long it took."""
    with conn.cursor() as cur:
        cur.execute("""SELECT i_job_id, s_kind, s_trigger, i_run_id, dt_started, dt_finished, s_status,
                              i_new_pings, i_dirty_trips, i_affected_trips, i_days, i_fences, d_seconds,
                              LEFT(s_error, 400) s_error
                         FROM geo_job ORDER BY i_job_id DESC LIMIT %s""", (min(max(limit, 1), 200),))
        jobs = rows(cur)
    return {"jobs": jobs, "status": scheduler.status()}


@router.get("/osrm/health")
def osrm_health():
    """A fresh client per call, so one failed probe cannot trip a breaker the
    pipeline is using."""
    return OsrmClient(settings.osrm).health()


@router.get("/runs")
def list_runs(conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("""SELECT i_run_id, dt_started, dt_finished, s_status, s_scope, s_variant, s_feed,
                              s_osrm, i_trips, i_pings_read, i_pings_used, i_pings_dropped, i_events,
                              i_visits, i_violations, i_stops, i_spikes, i_medians, i_snapped, i_gaps,
                              i_moving_gaps, i_gaps_routed, i_inferred, d_seconds, d_pings_per_sec,
                              b_published, dt_summarised, s_error
                         FROM geo_run ORDER BY i_run_id DESC""")
        return {"runs": rows(cur)}


@router.get("/quality")
def quality(run: int | None = None, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute("SELECT * FROM geo_run WHERE i_run_id=%s", (rid,))
        run_row = cur.fetchone()
        params = _params(run_row)
        run_row.pop("j_params", None)
        osrm = run_row.pop("j_osrm", None)
        run_row.pop("j_index_stats", None)

        # Visits by the quality of the trip that owns them (geo_trip_share), so
        # a stay shared by consignment trips is counted once.
        cur.execute("""SELECT s.s_quality, COUNT(*) trips, SUM(s.i_pings_read) pings,
                              SUM(COALESCE(sh.i_facility_visits, s.i_facility_visits)) facility_visits
                         FROM geo_trip_summary s
                         LEFT JOIN geo_trip_share sh ON sh.i_run_id = s.i_run_id AND sh.i_trip_no = s.i_trip_no
                        WHERE s.i_run_id=%s GROUP BY 1 ORDER BY trips DESC""", (rid,))
        quality_mix = rows(cur)
        cur.execute("""SELECT s_reason, SUM(i_count) fixes, COUNT(DISTINCT i_trip_no) trips
                         FROM geo_ping_reject WHERE i_run_id=%s GROUP BY 1 ORDER BY fixes DESC""", (rid,))
        rejects = rows(cur)
        cur.execute("""
            SELECT s_kind,
                   CASE WHEN i_gap_s < 900 THEN '5-15 min' WHEN i_gap_s < 1800 THEN '15-30 min'
                        WHEN i_gap_s < 3600 THEN '30-60 min' WHEN i_gap_s < 14400 THEN '1-4 h'
                        ELSE '4 h+' END bucket,
                   COUNT(*) gaps, SUM(i_gap_s) seconds, SUM(s_route = 'ok') routed
              FROM (SELECT DISTINCT COALESCE(s_asset_id, CONCAT('trip:', i_trip_no)) vehicle,
                                    dt_from, dt_to, s_kind, i_gap_s, s_route
                      FROM geo_gap WHERE i_run_id=%s) g
             -- DISTINCT: consignment trips on one truck carry the same hole.
             GROUP BY 1, 2""", (rid,))
        gaps = rows(cur)
        cur.execute("""SELECT d_day, i_pings, i_pings_rejected, i_spikes, i_moving_gaps, i_trips
                         FROM geo_day_summary WHERE i_run_id=%s ORDER BY d_day""", (rid,))
        daily = rows(cur)
        cur.execute("""SELECT s.i_trip_no, COALESCE(m.s_asset_id, s.s_asset_id) s_asset_id, m.s_trans_name,
                              s.s_quality, s.s_quality_reason, s.i_pings_read, s.i_spikes,
                              s.i_moving_gaps, s.i_moving_gap_s, s.i_pings_dropped
                         FROM geo_trip_summary s LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
                        WHERE s.i_run_id=%s AND s.s_quality IN ('broken', 'noisy')
                        ORDER BY s.i_moving_gap_s DESC LIMIT 30""", (rid,))
        worst = rows(cur)
        cur.execute("""SELECT m.s_trans_name transporter, COUNT(*) trips,
                              SUM(s.s_quality='good') good, SUM(s.s_quality='broken') broken,
                              SUM(s.i_spikes) spikes, SUM(s.i_pings_read) pings
                         FROM geo_trip_summary s JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
                        WHERE s.i_run_id=%s GROUP BY 1 HAVING trips >= 10
                        ORDER BY broken / trips DESC LIMIT 20""", (rid,))
        by_transporter = rows(cur)
        cur.execute("""SELECT COUNT(*) n, SUM(d_inradius_m < 25) small, SUM(b_self_intersecting) crossing
                         FROM geo_fence WHERE b_active=1""")
        master = clean(cur.fetchone())
        cur.execute("""SELECT i_run_id, s_variant, s_scope FROM geo_run
                        WHERE s_status='ok' AND i_run_id<>%s AND i_trips=%s
                        ORDER BY i_run_id DESC""", (rid, run_row["i_trips"]))
        comparable = rows(cur)

    return {"run_id": rid, "run": clean(run_row), "params": params,
            "osrm": json.loads(osrm) if isinstance(osrm, str) else osrm,
            "quality_mix": quality_mix, "rejects": rejects, "gaps": gaps, "daily": daily,
            "worst_trips": worst, "by_transporter": by_transporter, "master": master,
            "comparable_runs": comparable}


@router.get("/runs/{run_a}/compare/{run_b}")
def compare_runs(run_a: int, run_b: int, refresh: bool = False):
    """Raw-versus-fitted (or any two runs) over their common trips. Cached per
    pair: it reads every visit of both runs."""
    from nexgen.shared.geoengine.pipeline.compare import compare

    key = (run_a, run_b)
    with _compare_lock:
        if not refresh and key in _compare_cache:
            return _compare_cache[key]
    try:
        out = compare(run_a, run_b, sample=40)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    with _compare_lock:
        _compare_cache[key] = out
    return out
