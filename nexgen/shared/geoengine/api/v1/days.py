"""The day: the operation summarised one calendar day at a time.

This is the landing page's data. It answers "what happened today" in the
order an operations owner asks it: how much moved, where it went, what went
wrong, and how far the numbers can be trusted.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException

from nexgen.shared.geoengine.api.v1.common import clean, parse_day, resolve_run, rows
from nexgen.shared.geoengine.db import get_geo_db

router = APIRouter(tags=["day summary"])

DAY_COLS = ("i_vehicles", "i_trips", "i_pings", "i_pings_rejected", "i_spikes", "i_entries",
            "i_exits", "i_facility_visits", "i_sites_visited", "i_facility_dwell_s",
            "i_restricted", "i_overspeed", "i_stops", "i_stops_outside", "i_stop_outside_s",
            "i_moving_gaps", "i_inferred")


@router.get("/days")
def list_days(run: int | None = None, conn=Depends(get_geo_db)):
    """Every day in the published run, oldest first -- the trend strip and
    the day picker."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute(f"SELECT d_day, {', '.join(DAY_COLS)} FROM geo_day_summary "
                    "WHERE i_run_id=%s ORDER BY d_day", (rid,))
        days = rows(cur)
    return {"run_id": rid, "days": days,
            "latest": days[-1]["d_day"] if days else None}


@router.get("/days/hourly")
def days_hourly(run: int | None = None, conn=Depends(get_geo_db)):
    """Arrivals, departures and alerts by hour, for every day of the run:
    the day summary's 3D skyline of the whole feed."""
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute("""SELECT d_day, i_facility_visits, i_vehicles, j_hourly FROM geo_day_summary
                        WHERE i_run_id=%s ORDER BY d_day""", (rid,))
        out = []
        for r in cur.fetchall():
            h = r["j_hourly"]
            h = json.loads(h) if isinstance(h, str) else (h or {})
            out.append({"d_day": r["d_day"], "arrivals": int(r["i_facility_visits"] or 0),
                        "vehicles": int(r["i_vehicles"] or 0), "entries": h.get("entries") or [0] * 24,
                        "exits": h.get("exits") or [0] * 24, "alerts": h.get("alerts") or [0] * 24})
    return {"run_id": rid, "days": out}


@router.get("/days/{day}")
def day_detail(day: str, run: int | None = None, conn=Depends(get_geo_db)):
    d = parse_day(day)
    start = datetime.combine(d, time.min)
    end = start + timedelta(days=1)
    with conn.cursor() as cur:
        rid = resolve_run(cur, run)
        cur.execute("SELECT * FROM geo_day_summary WHERE i_run_id=%s AND d_day=%s", (rid, d))
        summary = cur.fetchone()
        if not summary:
            cur.execute("SELECT MIN(d_day) a, MAX(d_day) b FROM geo_day_summary WHERE i_run_id=%s", (rid,))
            span = cur.fetchone()
            raise HTTPException(404, f"no data for {d}; the run covers {span['a']} to {span['b']}")
        hourly = summary.pop("j_hourly")
        hourly = json.loads(hourly) if isinstance(hourly, str) else (hourly or {})
        summary = clean(summary)

        cur.execute(f"SELECT d_day, {', '.join(DAY_COLS)} FROM geo_day_summary "
                    "WHERE i_run_id=%s AND d_day < %s ORDER BY d_day DESC LIMIT 7", (rid, d))
        prior = rows(cur)
        avg7 = ({c: round(sum(p[c] for p in prior) / len(prior), 1) for c in DAY_COLS}
                if prior else None)

        # Sites by how many vehicles they held that day.
        cur.execute("""
            SELECT fd.i_site_id, fs.s_site_name, fs.s_type, fs.s_category, fs.s_scale,
                   fd.i_entries, fd.i_exits, fd.i_vehicles, fd.i_dwell_s, fd.i_dwell_p50_s
              FROM geo_fence_day fd
              JOIN geo_fence_stats fs ON fs.i_run_id = fd.i_run_id AND fs.i_fence_id = fd.i_fence_id
             WHERE fd.i_run_id=%s AND fd.d_day=%s AND fs.s_scale IN ('micro','site','campus')
             ORDER BY fd.i_vehicles DESC, fd.i_dwell_s DESC
             LIMIT 15""", (rid, d))
        top_sites = rows(cur)

        # Everything below that counts events reads the physical ledger, so a
        # stay shared by several consignment trips appears once.

        # The longest stays in progress that day, innermost fence only.
        cur.execute("""
            SELECT v.i_trip_no, v.i_trips, v.s_trips, v.s_asset_id, v.i_site_id, v.s_site_name,
                   v.s_scale, v.dt_enter, v.dt_exit, v.b_open, v.b_entry_observed, v.i_dwell_seconds,
                   v.s_trans_name, v.s_driver_name
              FROM geo_pvisit v
             WHERE v.i_run_id=%s AND v.b_primary=1 AND v.s_scale IN ('micro','site','campus')
               AND v.dt_enter < %s
               AND COALESCE(v.dt_exit, v.dt_enter + INTERVAL v.i_dwell_seconds SECOND) >= %s
             ORDER BY v.i_dwell_seconds DESC
             LIMIT 12""", (rid, end, start))
        longest = rows(cur)

        cur.execute("""
            SELECT x.dt_event, x.s_kind, x.i_site_id, x.s_site_name, x.i_trip_no, x.i_trips,
                   x.s_asset_id, x.i_observed, x.i_limit, x.s_detail, x.s_trans_name, x.s_driver_name
              FROM geo_palert x
             WHERE x.i_run_id=%s AND x.dt_event >= %s AND x.dt_event < %s
             ORDER BY x.dt_event DESC
             LIMIT 100""", (rid, start, end))
        alerts = rows(cur)

        cur.execute("""
            SELECT s.i_trip_no, s.i_trips, s.s_asset_id, s.dt_start, s.dt_end, s.i_duration_s,
                   s.d_lat, s.d_long, s.s_trans_name, s.s_origin, s.s_destination
              FROM geo_pstop s
             WHERE s.i_run_id=%s AND s.i_fence_id IS NULL AND s.dt_start >= %s AND s.dt_start < %s
             ORDER BY s.i_duration_s DESC
             LIMIT 12""", (rid, start, end))
        stops_outside = rows(cur)

        cur.execute("""
            SELECT COALESCE(v.s_trans_name, '(no trip record)') AS transporter,
                   COUNT(*) visits, COUNT(DISTINCT v.s_asset_id) vehicles,
                   SUM(v.i_dwell_seconds) dwell_s
              FROM geo_pvisit v
             WHERE v.i_run_id=%s AND v.b_primary=1 AND v.s_scale IN ('micro','site','campus')
               AND v.b_entry_observed=1 AND v.dt_enter >= %s AND v.dt_enter < %s
             GROUP BY transporter
             ORDER BY visits DESC
             LIMIT 10""", (rid, start, end))
        transporters = rows(cur)

        cur.execute("""
            SELECT s.i_trip_no, s.s_asset_id, m.s_trans_name, m.s_driver_name, m.s_origin,
                   m.s_destination, m.s_status, s.s_quality, s.s_quality_reason,
                   s.i_facility_visits, s.i_places, s.i_violations, s.s_first_site, s.s_last_site,
                   s.dt_first_ping, s.dt_last_ping
              FROM geo_trip_summary s
              LEFT JOIN geo_trip_meta m ON m.i_trip_no = s.i_trip_no
             WHERE s.i_run_id=%s AND s.dt_first_ping < %s AND s.dt_last_ping >= %s
             ORDER BY s.i_violations DESC, s.i_facility_visits DESC
             LIMIT 25""", (rid, end, start))
        trips = rows(cur)

        cur.execute("""
            SELECT s.s_quality, COUNT(*) n
              FROM geo_trip_summary s
             WHERE s.i_run_id=%s AND s.dt_first_ping < %s AND s.dt_last_ping >= %s
             GROUP BY s.s_quality""", (rid, end, start))
        quality_mix = {r["s_quality"]: int(r["n"]) for r in cur.fetchall()}

    return {
        "run_id": rid, "day": d, "summary": summary,
        "previous": prior[0] if prior else None, "avg7": avg7,
        "hourly": hourly, "top_sites": top_sites, "longest_stays": longest,
        "alerts": alerts, "stops_outside": stops_outside, "transporters": transporters,
        "trips": trips, "trip_quality": quality_mix,
    }
