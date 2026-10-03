"""Map and live endpoints for the control centre.

Split from `app.py` because these serve the UI and the ones there serve the
analysis; they have different consumers and different performance shapes.

The map is served in two resolutions, deliberately
------------------------------------------------
At national zoom, 4,505 fences are sub-pixel dots and shipping their polygons
would be megabytes to draw nothing. At works zoom, the polygon *is* the point.
So:

    /map/fences/summary   every fence as a centroid, area and category
    /map/fences           full rings, for one viewport, capped

The summary is ~4,500 short rows and is fetched once. The detailed call is
made on zoom-in and is bounded by the viewport, so its cost does not grow
with the size of the master.

Fence rings are served at full stored precision, never simplified. The base
map is simplified because a coastline off by 200 m is invisible; a fence
boundary off by 200 m is a different answer to "was the truck inside".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from nexgen.shared.geoengine import store
from nexgen.shared.geoengine.config import ROOT
from nexgen.shared.geoengine.db import get_geo_db

logger = logging.getLogger(__name__)
router = APIRouter()

DATA_DIR = ROOT / "data"

# Scales that represent a real place a truck can be "at". A `regional` fence is
# a district catchment, so being inside one is not an arrival.
FACILITY_SCALES = ("micro", "site", "campus")


# ---------------------------------------------------------------------------
# Base map
# ---------------------------------------------------------------------------

@router.get("/map/states", tags=["map"])
def map_states():
    """India's states. No basemap tiles, no neighbouring countries.

    Served as a file with its ETag and revalidated on every load: an unchanged
    map costs a 304, and a rebuilt one reaches every browser at once rather
    than after a day in a cache. GZip middleware takes 1.8 MB to about 550 KB
    on the wire.
    """
    path = DATA_DIR / "india_states.json"
    if not path.exists():
        raise HTTPException(503, "base map not built; run `python -m nexgen.shared.geoengine.cli build-map`")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.get("/map/districts", tags=["map"])
def map_districts():
    path = DATA_DIR / "india_districts.json"
    if not path.exists():
        raise HTTPException(503, "base map not built")
    return FileResponse(path, media_type="application/json", headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# Fences
# ---------------------------------------------------------------------------

@router.get("/map/fences/summary", tags=["map"])
def fence_summary(conn=Depends(get_geo_db)):
    """Every active fence as a point. What the national view draws."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT i_fence_id, i_site_id, s_site_name, s_type, s_category,
                   d_centroid_lat, d_centroid_long, d_area_sqm, i_vertices,
                   i_max_speed, d_min_lat, d_max_lat, d_min_long, d_max_long
              FROM geo_fence WHERE b_active = 1
        """)
        rows = cur.fetchall()

    out = []
    for r in rows:
        area = float(r["d_area_sqm"])
        out.append({
            "id": r["i_fence_id"], "site": r["i_site_id"],
            "name": r["s_site_name"], "type": r["s_type"],
            "cat": r["s_category"],
            "lat": float(r["d_centroid_lat"]), "lon": float(r["d_centroid_long"]),
            "area": round(area),
            "scale": ("micro" if area < 1e4 else "site" if area < 1e6
                      else "campus" if area < 1e8 else "regional"),
            "v": r["i_vertices"],
            "limit": r["i_max_speed"],
            "bbox": [round(float(r["d_min_long"]), 5), round(float(r["d_min_lat"]), 5),
                     round(float(r["d_max_long"]), 5), round(float(r["d_max_lat"]), 5)],
        })
    return {"count": len(out), "fences": out}


@router.get("/map/fences", tags=["map"])
def fence_rings(
    bbox: str = Query(..., description="min_lon,min_lat,max_lon,max_lat"),
    limit: int = Query(600, ge=1, le=3000),
    conn=Depends(get_geo_db),
):
    """Full rings for fences intersecting a viewport.

    Ordered largest-first so that if the cap bites, what is dropped is the
    detail a reader is least likely to be looking for at that zoom.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be 'min_lon,min_lat,max_lon,max_lat'") from None

    with conn.cursor() as cur:
        cur.execute("""
            SELECT i_fence_id, i_site_id, s_site_name, s_category, s_type,
                   d_area_sqm, i_max_speed
              FROM geo_fence
             WHERE b_active = 1
               AND d_min_long <= %s AND d_max_long >= %s
               AND d_min_lat  <= %s AND d_max_lat  >= %s
             ORDER BY d_area_sqm DESC
             LIMIT %s
        """, (max_lon, min_lon, max_lat, min_lat, limit))
        meta = {r["i_site_id"]: r for r in cur.fetchall()}
        if not meta:
            return {"count": 0, "truncated": False, "fences": []}

        ids = tuple(meta)
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"""SELECT i_site_id, d_lat, d_long FROM geo_site_vertex
                 WHERE i_site_id IN ({placeholders})
                 ORDER BY i_site_id, i_seq""", ids)
        rings: dict[int, list] = {}
        for r in cur.fetchall():
            rings.setdefault(r["i_site_id"], []).append(
                [round(float(r["d_long"]), 6), round(float(r["d_lat"]), 6)])

    out = []
    for sid, m in meta.items():
        ring = rings.get(sid)
        if not ring:
            continue
        area = float(m["d_area_sqm"])
        out.append({
            "id": m["i_fence_id"], "site": sid, "name": m["s_site_name"],
            "cat": m["s_category"], "type": m["s_type"],
            "limit": m["i_max_speed"],
            "scale": ("micro" if area < 1e4 else "site" if area < 1e6
                      else "campus" if area < 1e8 else "regional"),
            "ring": ring,
        })
    return {"count": len(out), "truncated": len(meta) >= limit, "fences": out}


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------

@router.get("/live/vehicles", tags=["live"])
def live_vehicles(
    since_minutes: int = Query(0, ge=0,
                               description="only vehicles seen this recently in feed time; 0 = all"),
    conn=Depends(get_geo_db),
):
    """Where every vehicle is now, as the live detector last saw it.

    Read from `geo_live_position` rather than aggregated off the ping feed:
    "the latest fix per vehicle" is a GROUP BY over 5.1M rows and takes
    seconds, and this endpoint is polled every few.
    """
    with conn.cursor() as cur:
        where = ""
        params: list = []
        if since_minutes:
            cur.execute("SELECT MAX(dt_message) t FROM geo_live_position")
            latest = cur.fetchone()["t"]
            if latest:
                where = "WHERE dt_message >= %s"
                params.append(latest - timedelta(minutes=since_minutes))
        cur.execute(f"""
            SELECT s_asset_id, dt_message, d_lat, d_long, i_speed, i_trip_no,
                   i_fence_id, i_site_id, s_site_name, s_category, i_inside_count,
                   s_scale
              FROM geo_live_position {where}
             ORDER BY dt_message DESC
        """, params)
        rows = cur.fetchall()

    vehicles = [{
        "asset": r["s_asset_id"],
        "ts": r["dt_message"],
        "lat": float(r["d_lat"]), "lon": float(r["d_long"]),
        "speed": r["i_speed"], "trip": r["i_trip_no"],
        "fence": r["i_fence_id"], "site": r["i_site_id"],
        "site_name": r["s_site_name"], "cat": r["s_category"],
        "inside": r["i_inside_count"], "scale": r["s_scale"],
        "at_site": r["s_scale"] in FACILITY_SCALES,
        "moving": bool(r["i_speed"] and r["i_speed"] > 0),
    } for r in rows]

    return {
        "count": len(vehicles),
        "feed_time": vehicles[0]["ts"] if vehicles else None,
        # "inside a fence" is nearly always true and therefore nearly useless:
        # 95 fences in the master are district-scale catchments and an ordinary
        # highway run sits inside one for hours. `at_site` is the number that
        # answers an operator's actual question.
        "inside": sum(1 for v in vehicles if v["inside"]),
        "at_site": sum(1 for v in vehicles if v["at_site"]),
        "moving": sum(1 for v in vehicles if v["moving"]),
        "vehicles": vehicles,
    }


@router.get("/live/events", tags=["live"])
def live_events(
    after_id: int = Query(0, ge=0, description="only events with a higher id"),
    severity: str | None = Query(None, description="info | warn | alert"),
    limit: int = Query(150, ge=1, le=1000),
    conn=Depends(get_geo_db),
):
    """The event tail. `after_id` makes polling incremental."""
    where = ["id > %s"]
    params: list = [after_id]
    if severity:
        where.append("s_severity = %s")
        params.append(severity)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, dt_event, s_asset_id, i_trip_no, i_fence_id, i_site_id,
                   s_site_name, s_category, s_scale, s_event, s_severity,
                   i_gap_seconds, d_lat, d_long, i_speed, i_limit, s_detail,
                   s_confirmed_by
              FROM geo_live_event
             WHERE {' AND '.join(where)}
             ORDER BY id DESC LIMIT %s
        """, (*params, limit))
        rows = cur.fetchall()
    for r in rows:
        r["d_lat"] = float(r["d_lat"])
        r["d_long"] = float(r["d_long"])
    return {"count": len(rows), "max_id": max((r["id"] for r in rows), default=after_id),
            "events": rows}


@router.get("/live/stats", tags=["live"])
def live_stats(conn=Depends(get_geo_db)):
    """Headline numbers for the control-centre header."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) vehicles,
                   SUM(i_inside_count > 0) inside,
                   SUM(s_scale IN ('micro','site','campus')) at_site,
                   SUM(i_speed > 0) moving,
                   MAX(dt_message) feed_time,
                   MAX(dt_updated) updated
              FROM geo_live_position
        """)
        pos = cur.fetchone()
        cur.execute("""
            SELECT s_severity, COUNT(*) n FROM geo_live_event
             GROUP BY 1
        """)
        sev = {r["s_severity"]: r["n"] for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) n FROM geo_live_event")
        total = cur.fetchone()["n"]
        cur.execute("""
            SELECT i_last_ping_id, dt_last_message, i_processed
              FROM geo_live_cursor WHERE i_id = 1
        """)
        cursor = cur.fetchone()
        # Polled every few seconds: MIN/MAX use the dt_message index, and the
        # row count is InnoDB's estimate rather than a full scan of the feed.
        cur.execute("SELECT MIN(dt_message) a, MAX(dt_message) b FROM geo_gps_ping")
        feed = cur.fetchone()
        cur.execute("""SELECT table_rows n FROM information_schema.tables
                        WHERE table_schema = DATABASE() AND table_name = 'geo_gps_ping'""")
        pings = (cur.fetchone() or {}).get("n") or 0
        cur.execute("SELECT COUNT(*) n FROM geo_fence WHERE b_active=1")
        fences = cur.fetchone()["n"]
        cur.execute("""
            SELECT s_site_name, COUNT(*) n FROM geo_live_position
             WHERE i_site_id IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 8
        """)
        busiest = cur.fetchall()

    # Progress is measured in FEED TIME, not in row ids. Replay advances on
    # (dt_message, id) because the feed's insert order is not chronological,
    # so the id cursor is not a position in the replay and dividing by the row
    # count produced figures over 100%.
    progress = None
    if cursor and cursor["dt_last_message"] and feed["a"] and feed["b"]:
        span = (feed["b"] - feed["a"]).total_seconds()
        if span > 0:
            done = (cursor["dt_last_message"] - feed["a"]).total_seconds()
            progress = round(max(0.0, min(100.0, 100.0 * done / span)), 1)

    return {
        "vehicles": pos["vehicles"] or 0,
        "inside": int(pos["inside"] or 0),
        "at_site": int(pos["at_site"] or 0),
        "moving": int(pos["moving"] or 0),
        "feed_time": pos["feed_time"],
        "updated": pos["updated"],
        "events_total": total,
        "events_by_severity": sev,
        "active_fences": fences,
        "feed_pings": pings,
        "cursor_ping_id": (cursor or {}).get("i_last_ping_id", 0),
        "feed_span": [feed["a"], feed["b"]],
        "replay_progress_pct": progress,
        "busiest_sites": busiest,
        # Generous: a batch can take tens of seconds on a fast replay, and a
        # header that flickers "idle" mid-batch trains people to ignore it.
        "detector_running": bool(
            pos["updated"] and
            (datetime.now() - pos["updated"]).total_seconds() < 90
        ),
    }


@router.get("/live/vehicle/{asset_id}/trail", tags=["live"])
def vehicle_trail(
    asset_id: str,
    minutes: int = Query(120, ge=1, le=10080),
    conn=Depends(get_geo_db),
):
    """Recent trail for one vehicle, for the detail panel."""
    with conn.cursor() as cur:
        cur.execute("SELECT dt_message FROM geo_live_position WHERE s_asset_id=%s",
                    (asset_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"no live position for {asset_id}")
        end = row["dt_message"]
        cur.execute("""
            SELECT dt_message, d_lat, d_long, i_speed
              FROM geo_gps_ping
             WHERE s_asset_id = %s AND dt_message BETWEEN %s AND %s
             ORDER BY dt_message
        """, (asset_id, end - timedelta(minutes=minutes), end))
        pts = cur.fetchall()
        cur.execute("""
            SELECT dt_event, s_event, s_site_name, s_severity, s_detail
              FROM geo_live_event WHERE s_asset_id=%s
             ORDER BY id DESC LIMIT 30
        """, (asset_id,))
        events = cur.fetchall()

    return {
        "asset": asset_id,
        "points": [[round(float(p["d_long"]), 6), round(float(p["d_lat"]), 6),
                    p["i_speed"] or 0] for p in pts],
        "from": pts[0]["dt_message"] if pts else None,
        "to": end,
        "events": events,
    }


@router.get("/live/sites", tags=["live"])
def live_sites(conn=Depends(get_geo_db)):
    """Occupancy per site: which fences currently hold vehicles."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.i_site_id, p.s_site_name, p.s_category, COUNT(*) n,
                   f.d_centroid_lat, f.d_centroid_long, f.d_area_sqm
              FROM geo_live_position p
              JOIN geo_fence f ON f.i_fence_id = p.i_fence_id
             WHERE p.i_site_id IS NOT NULL
             GROUP BY 1,2,3,5,6,7
             ORDER BY n DESC LIMIT 100
        """)
        rows = cur.fetchall()
    return {"count": len(rows), "sites": [{
        "site": r["i_site_id"], "name": r["s_site_name"], "cat": r["s_category"],
        "vehicles": r["n"], "lat": float(r["d_centroid_lat"]),
        "lon": float(r["d_centroid_long"]), "area": round(float(r["d_area_sqm"])),
    } for r in rows]}
