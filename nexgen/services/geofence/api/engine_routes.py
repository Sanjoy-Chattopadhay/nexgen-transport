"""Geo-Fencing's own endpoints, under /api/v1/geo.

The attachment surface of the old module (api/app.py): what fences exist and
what the loader refused (/master), containment for a batch of positions
(/locate), what a run found (/runs), the engine-vs-MySQL cross-check
(/validate) and the engine's health. Same handlers, same answers; only the
prefix moved, and the UI is now served by the gateway.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from nexgen.shared.geoengine import store
from nexgen.shared.geoengine.api import cache as response_cache
from nexgen.shared.geoengine.config import settings
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.reporting import reports

router = APIRouter(prefix="/api/v1/geo", tags=["geofence engine"])


async def version_cache(request: Request, call_next):
    """Serve an unchanged answer from memory, or as a 304 (geoengine/api/cache.py)."""
    path = request.url.path
    if request.method != "GET" or not response_cache.cacheable(path):
        return await call_next(request)
    version = await run_in_threadpool(response_cache.current_version)
    key = response_cache.key_of(path, request.url.query, False)
    tag = response_cache.etag(version, key)
    if request.headers.get("if-none-match") == tag:
        response_cache.stats["not_modified"] += 1
        return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-cache"})
    hit = response_cache.get(key, version)
    if hit is not None:
        body, headers = hit
        return Response(body, status_code=200, headers={**headers, "ETag": tag, "X-Cache": "hit"})
    response = await call_next(request)
    if response.status_code != 200 or not response.headers.get("content-type", "").startswith("application/json"):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    headers = {"Content-Type": response.headers["content-type"], "Cache-Control": "no-cache"}
    response_cache.put(key, version, body, headers)
    return Response(body, status_code=200, headers={**headers, "ETag": tag, "X-Cache": "miss"})


@router.get("/engine/health")
def engine_health(conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM geo_fence WHERE b_active=1")
        fences = cur.fetchone()["n"]
        cur.execute("SELECT MAX(i_run_id) r FROM geo_run WHERE s_status='ok'")
        run = cur.fetchone()["r"]
    idx = store.get_index()
    return {"status": "ok", "active_fences": fences, "latest_run": run, "index": idx.stats,
            "geo_db": settings.geo_db_name, "source": "fleet service (published views)",
            "cache": response_cache.info()}


@router.get("/master/fences")
def list_fences(q: str | None = Query(None), category: str | None = Query(None),
                scale: str | None = Query(None), include_inactive: bool = False,
                limit: int = Query(200, ge=1, le=2000), conn=Depends(get_geo_db)):
    where, params = [], []
    if not include_inactive:
        where.append("b_active = 1")
    if q:
        where.append("s_site_name LIKE %s")
        params.append(f"%{q}%")
    if category:
        where.append("s_category = %s")
        params.append(category)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with conn.cursor() as cur:
        cur.execute(f"""SELECT i_fence_id, i_site_id, s_site_name, s_type, s_category, i_vertices, i_max_speed,
                               i_tolerance, b_active, ROUND(d_area_sqm) d_area_sqm, d_centroid_lat,
                               d_centroid_long, b_self_intersecting, s_geom_notes
                          FROM geo_fence {clause} ORDER BY s_site_name LIMIT %s""", (*params, limit))
        rows = cur.fetchall()
    out = []
    for r in rows:
        a = float(r["d_area_sqm"])
        r["scale"] = ("micro" if a < 10_000 else "site" if a < 1_000_000
                      else "campus" if a < 100_000_000 else "regional")
        if scale and r["scale"] != scale:
            continue
        out.append(r)
    return {"count": len(out), "fences": out}


@router.get("/master/fences/{site_id}")
def fence_detail(site_id: int, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM geo_fence WHERE i_site_id=%s", (site_id,))
        fence = cur.fetchone()
        if not fence:
            raise HTTPException(404, f"no fence for site {site_id}")
        fence.pop("g_poly", None)
        cur.execute("SELECT i_seq, d_lat, d_long FROM geo_site_vertex WHERE i_site_id=%s ORDER BY i_seq", (site_id,))
        vertices = cur.fetchall()
        cur.execute("SELECT s_reason, s_detail FROM geo_import_reject WHERE i_site_id=%s", (site_id,))
        notes = cur.fetchall()
    return {"fence": fence, "vertices": vertices, "import_notes": notes}


@router.get("/master/rejects")
def master_rejects(conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(i_import_id) i FROM geo_import_run WHERE s_status='ok'")
        import_id = cur.fetchone()["i"]
        cur.execute("""SELECT s_entity, s_reason, COUNT(*) n FROM geo_import_reject
                        WHERE i_import_id=%s GROUP BY 1,2 ORDER BY n DESC""", (import_id,))
        summary = cur.fetchall()
        cur.execute("""SELECT i_site_id, s_entity, s_reason, s_detail FROM geo_import_reject
                        WHERE i_import_id=%s ORDER BY i_site_id LIMIT 500""", (import_id,))
        detail = cur.fetchall()
    return {"import_id": import_id, "summary": summary, "detail": detail}


class Position(BaseModel):
    id: str | None = Field(None, description="caller's own key, echoed back")
    lat: float
    lon: float
    ts: datetime | None = None
    speed: int | None = None


class LocateRequest(BaseModel):
    positions: list[Position] = Field(..., max_length=5000)
    include_all: bool = True


@router.post("/locate")
def locate(req: LocateRequest):
    """Which fences contain each position (containment, never crossings)."""
    idx = store.get_index(pad_m=store.max_tolerance_m())
    out = []
    for p in req.positions:
        hits = [f for f in idx.query_point(p.lat, p.lon) if f.contains(p.lat, p.lon)]
        hits.sort(key=lambda f: f.area_m2)
        rows = [{"site_id": f.site_id, "fence_id": f.fence_id, "name": f.name, "type": f.site_type,
                 "category": f.category, "scale": store.scale_of(f), "max_speed": f.max_speed,
                 "primary": i == 0,
                 "over_speed": bool(f.max_speed and p.speed and p.speed > f.max_speed)}
                for i, f in enumerate(hits)]
        out.append({"id": p.id, "lat": p.lat, "lon": p.lon, "inside_count": len(rows),
                    "primary": rows[0] if rows else None, "fences": rows if req.include_all else rows[:1]})
    return {"count": len(out), "results": out}


@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200), conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("""SELECT i_run_id, dt_started, dt_finished, s_mode, s_scope, s_status, i_trips, i_pings_read,
                              i_visits, i_violations, d_seconds, d_pings_per_sec, b_published
                         FROM geo_run ORDER BY i_run_id DESC LIMIT %s""", (limit,))
        return {"runs": cur.fetchall()}


@router.get("/runs/{run_id}/report")
def run_report(run_id: int, conn=Depends(get_geo_db)):
    try:
        return reports.build(run_id, conn=conn)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
def run_report_html(run_id: int, conn=Depends(get_geo_db)):
    try:
        return reports.render_html(reports.build(run_id, conn=conn))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/report.txt", response_class=PlainTextResponse)
def run_report_text(run_id: int, conn=Depends(get_geo_db)):
    try:
        return reports.render_text(reports.build(run_id, conn=conn))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/trips/{trip_no}")
def trip_detail(run_id: int, trip_no: int, conn=Depends(get_geo_db)):
    data = reports.trip_report(run_id, trip_no, conn=conn)
    if data is None:
        raise HTTPException(404, f"trip {trip_no} not in run {run_id}")
    return data


@router.get("/runs/{run_id}/visits")
def run_visits(run_id: int, site_id: int | None = None, asset_id: str | None = None, primary_only: bool = True,
               limit: int = Query(200, ge=1, le=5000), conn=Depends(get_geo_db)):
    where, params = ["i_run_id = %s"], [run_id]
    if primary_only:
        where.append("b_primary = 1")
    if site_id:
        where.append("i_site_id = %s")
        params.append(site_id)
    if asset_id:
        where.append("s_asset_id = %s")
        params.append(asset_id)
    with conn.cursor() as cur:
        cur.execute(f"""SELECT i_trip_no, s_asset_id, i_site_id, s_site_name, s_category, s_scale, dt_enter, dt_exit,
                               b_open, i_dwell_seconds, i_pings, i_max_speed, i_enter_gap_seconds,
                               i_exit_gap_seconds, s_confirmed_by
                          FROM geo_visit WHERE {' AND '.join(where)} ORDER BY dt_enter DESC LIMIT %s""",
                    (*params, limit))
        return {"visits": cur.fetchall()}


@router.get("/runs/{run_id}/violations")
def run_violations(run_id: int, kind: str | None = None, limit: int = Query(200, ge=1, le=5000),
                   conn=Depends(get_geo_db)):
    where, params = ["i_run_id = %s"], [run_id]
    if kind:
        where.append("s_kind = %s")
        params.append(kind)
    with conn.cursor() as cur:
        cur.execute(f"""SELECT i_trip_no, s_asset_id, i_site_id, s_site_name, s_kind, dt_event, i_observed, i_limit,
                               s_detail FROM geo_violation WHERE {' AND '.join(where)}
                         ORDER BY dt_event DESC LIMIT %s""", (*params, limit))
        return {"violations": cur.fetchall()}


@router.get("/validate")
def validate(samples: int = Query(2000, ge=100, le=50000)):
    """Cross-check the engine against MySQL ST_Contains on live geometry."""
    from nexgen.shared.geoengine.validate import cross_check
    return cross_check(samples=samples)
