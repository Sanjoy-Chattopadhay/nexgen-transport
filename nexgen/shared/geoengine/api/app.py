"""HTTP API.

This is the attachment surface. Smart-Truck (or any other caller) talks to
this over HTTP and does not import the engine, share its process, or know its
schema -- which is what keeps the module detachable.

Endpoints split three ways:

  /master/*   what fences exist, and what the loader refused
  /locate     the live question: given coordinates, which fences am I in
  /runs/*     what a batch evaluation found

`/locate` is the one built for volume. It takes a batch of positions in one
request because the caller is a fleet: asking about 500 trucks individually is
500 round trips, and the index is the same for all of them.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nexgen.shared.geoengine import store
from nexgen.shared.geoengine.api import cache as response_cache
from nexgen.shared.geoengine.api.live_api import router as live_router
from nexgen.shared.geoengine.api.v1 import router as v1_router
from nexgen.shared.geoengine.config import ROOT, settings
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.reporting import reports

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Geofence Intelligence",
    version="3.0",
    description=(
        "Polygon geofence detection over vehicle GPS -- filtered and fitted before "
        "detection, with per-geofence, per-trip and per-day analysis. Standalone: "
        "owns its own database and its own copy of the feed."
    ),
)


@app.middleware("http")
async def version_cache(request: Request, call_next):
    """Serve an unchanged answer from memory, or as a 304 (api/cache.py).

    Registered before the compression below, so it sits inside it: bodies are
    kept uncompressed and compressed on the way out for whoever asks.
    """
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


# The base map is ~1.8 MB of JSON and compresses to about 30% of that. Without
# this the first page load ships the uncompressed file.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(live_router)
# The same live and map endpoints under the application prefix, so the new UI
# talks to one base path.
app.include_router(live_router, prefix="/api/v1")
app.include_router(v1_router)


# ---------------------------------------------------------------------------
# Health and master
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
def health(conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n FROM geo_fence WHERE b_active=1")
        fences = cur.fetchone()["n"]
        cur.execute("SELECT MAX(i_run_id) r FROM geo_run WHERE s_status='ok'")
        run = cur.fetchone()["r"]
    idx = store.get_index()
    return {"status": "ok", "active_fences": fences, "latest_run": run,
            "index": idx.stats, "geo_db": settings.geo_db_name,
            "source_db": settings.src_db["database"]}


@app.get("/master/fences", tags=["master"])
def list_fences(
    q: str | None = Query(None, description="substring of the site name"),
    category: str | None = Query(None, description="normal | restricted | high_risk"),
    scale: str | None = Query(None, description="micro | site | campus | regional"),
    include_inactive: bool = False,
    limit: int = Query(200, ge=1, le=2000),
    conn=Depends(get_geo_db),
):
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
        cur.execute(
            f"""SELECT i_fence_id, i_site_id, s_site_name, s_type, s_category,
                       i_vertices, i_max_speed, i_tolerance, b_active,
                       ROUND(d_area_sqm) d_area_sqm, d_centroid_lat, d_centroid_long,
                       b_self_intersecting, s_geom_notes
                  FROM geo_fence {clause}
                 ORDER BY s_site_name LIMIT %s""",
            (*params, limit),
        )
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


@app.get("/master/fences/{site_id}", tags=["master"])
def fence_detail(site_id: int, conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM geo_fence WHERE i_site_id=%s", (site_id,))
        fence = cur.fetchone()
        if not fence:
            raise HTTPException(404, f"no fence for site {site_id}")
        fence.pop("g_poly", None)
        cur.execute(
            "SELECT i_seq, d_lat, d_long FROM geo_site_vertex WHERE i_site_id=%s ORDER BY i_seq",
            (site_id,),
        )
        vertices = cur.fetchall()
        cur.execute(
            "SELECT s_reason, s_detail FROM geo_import_reject WHERE i_site_id=%s", (site_id,)
        )
        notes = cur.fetchall()
    return {"fence": fence, "vertices": vertices, "import_notes": notes}


@app.get("/master/rejects", tags=["master"])
def master_rejects(conn=Depends(get_geo_db)):
    """Everything the loader refused, and why. The answer to "why is site X
    not detected".

    Scoped to the import that produced the master currently loaded. The table
    keeps every import's findings deliberately -- it is an audit trail -- but
    a report that summed across them would double its counts on a re-import.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(i_import_id) i FROM geo_import_run WHERE s_status='ok'")
        import_id = cur.fetchone()["i"]
        cur.execute("""SELECT s_entity, s_reason, COUNT(*) n FROM geo_import_reject
                        WHERE i_import_id=%s GROUP BY 1,2 ORDER BY n DESC""", (import_id,))
        summary = cur.fetchall()
        cur.execute("""SELECT i_site_id, s_entity, s_reason, s_detail
                         FROM geo_import_reject WHERE i_import_id=%s
                        ORDER BY i_site_id LIMIT 500""", (import_id,))
        detail = cur.fetchall()
    return {"import_id": import_id, "summary": summary, "detail": detail}


# ---------------------------------------------------------------------------
# Live containment
# ---------------------------------------------------------------------------

class Position(BaseModel):
    id: str | None = Field(None, description="caller's own key, echoed back")
    lat: float
    lon: float
    ts: datetime | None = None
    speed: int | None = None


class LocateRequest(BaseModel):
    positions: list[Position] = Field(..., max_length=5000)
    include_all: bool = Field(
        True,
        description="return every containing fence; false returns only the innermost",
    )


@app.post("/locate", tags=["live"])
def locate(req: LocateRequest):
    """Which fences contain each position.

    Stateless -- it answers containment, not crossings. Crossings need history
    and are produced by a run, because a single position cannot tell you
    whether a state change held long enough to be believed. Anything claiming
    to detect an entry from one fix is reporting jitter as an event.
    """
    # Padded by the largest declared tolerance: a buffered fence extends past
    # its own bounding box, so an unpadded index would not even offer it as a
    # candidate for a point in its buffer.
    idx = store.get_index(pad_m=store.max_tolerance_m())
    out = []
    for p in req.positions:
        hits = []
        for f in idx.query_point(p.lat, p.lon):
            if f.contains(p.lat, p.lon):
                hits.append(f)
        hits.sort(key=lambda f: f.area_m2)
        rows = [{
            "site_id": f.site_id, "fence_id": f.fence_id, "name": f.name,
            "type": f.site_type, "category": f.category,
            "scale": store.scale_of(f),
            "max_speed": f.max_speed,
            "primary": i == 0,
            "over_speed": bool(f.max_speed and p.speed and p.speed > f.max_speed),
        } for i, f in enumerate(hits)]
        out.append({
            "id": p.id, "lat": p.lat, "lon": p.lon,
            "inside_count": len(rows),
            "primary": rows[0] if rows else None,
            "fences": rows if req.include_all else rows[:1],
        })
    return {"count": len(out), "results": out}


# ---------------------------------------------------------------------------
# Runs and reports
# ---------------------------------------------------------------------------

@app.get("/runs", tags=["runs"])
def list_runs(limit: int = Query(20, ge=1, le=200), conn=Depends(get_geo_db)):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT i_run_id, dt_started, dt_finished, s_mode, s_scope, s_status,
                      i_trips, i_pings_read, i_visits, i_violations, d_seconds,
                      d_pings_per_sec
                 FROM geo_run ORDER BY i_run_id DESC LIMIT %s""", (limit,)
        )
        return {"runs": cur.fetchall()}


@app.get("/runs/{run_id}/report", tags=["runs"])
def run_report(run_id: int, conn=Depends(get_geo_db)):
    try:
        return reports.build(run_id, conn=conn)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/runs/{run_id}/report.html", response_class=HTMLResponse, tags=["runs"])
def run_report_html(run_id: int, conn=Depends(get_geo_db)):
    try:
        return reports.render_html(reports.build(run_id, conn=conn))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/runs/{run_id}/report.txt", response_class=PlainTextResponse, tags=["runs"])
def run_report_text(run_id: int, conn=Depends(get_geo_db)):
    try:
        return reports.render_text(reports.build(run_id, conn=conn))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/runs/{run_id}/trips/{trip_no}", tags=["runs"])
def trip_detail(run_id: int, trip_no: int, conn=Depends(get_geo_db)):
    data = reports.trip_report(run_id, trip_no, conn=conn)
    if data is None:
        raise HTTPException(404, f"trip {trip_no} not in run {run_id}")
    return data


@app.get("/runs/{run_id}/visits", tags=["runs"])
def run_visits(
    run_id: int,
    site_id: int | None = None,
    asset_id: str | None = None,
    primary_only: bool = True,
    limit: int = Query(200, ge=1, le=5000),
    conn=Depends(get_geo_db),
):
    where = ["i_run_id = %s"]
    params: list = [run_id]
    if primary_only:
        where.append("b_primary = 1")
    if site_id:
        where.append("i_site_id = %s")
        params.append(site_id)
    if asset_id:
        where.append("s_asset_id = %s")
        params.append(asset_id)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT i_trip_no, s_asset_id, i_site_id, s_site_name, s_category, s_scale,
                       dt_enter, dt_exit, b_open, i_dwell_seconds, i_pings, i_max_speed,
                       i_enter_gap_seconds, i_exit_gap_seconds, s_confirmed_by
                  FROM geo_visit WHERE {' AND '.join(where)}
                 ORDER BY dt_enter DESC LIMIT %s""",
            (*params, limit),
        )
        return {"visits": cur.fetchall()}


@app.get("/runs/{run_id}/violations", tags=["runs"])
def run_violations(
    run_id: int,
    kind: str | None = None,
    limit: int = Query(200, ge=1, le=5000),
    conn=Depends(get_geo_db),
):
    where = ["i_run_id = %s"]
    params: list = [run_id]
    if kind:
        where.append("s_kind = %s")
        params.append(kind)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT i_trip_no, s_asset_id, i_site_id, s_site_name, s_kind, dt_event,
                       i_observed, i_limit, s_detail
                  FROM geo_violation WHERE {' AND '.join(where)}
                 ORDER BY dt_event DESC LIMIT %s""",
            (*params, limit),
        )
        return {"violations": cur.fetchall()}


# ---------------------------------------------------------------------------
# The application UI
# ---------------------------------------------------------------------------

# A React build in frontend/dist, served from the same process as the API so
# the whole thing is one port and one command to start. There is no other UI:
# when the build is missing the server says so rather than showing anything
# older.
DIST_DIR = ROOT / "frontend" / "dist"
if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.middleware("http")
async def cache_policy(request: Request, call_next):
    """Never let a browser show a stale copy of the application.

    Sent without a caching instruction, an HTML page may be reused for hours
    on heuristics alone -- a tenth of its age since it last changed -- so a
    browser opening the address kept showing a UI the server had already
    replaced. The page is therefore revalidated on every load (a 304 when
    unchanged). The bundles it names carry a content hash in their file names
    and never change, so those are cached for good.
    """
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


def _no_build() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Geofence Intelligence</title>"
        "<h1>The application has not been built</h1>"
        "<p>Run <code>start.bat</code>, which builds it when it is missing, or "
        "<code>npm --prefix frontend install</code> and then "
        "<code>npm --prefix frontend run build</code>, and reload this page. "
        "The API itself is running: <a href='/docs'>/docs</a>.</p>",
        status_code=503,
    )


@app.get("/", include_in_schema=False)
def application():
    index = DIST_DIR / "index.html"
    return FileResponse(index, media_type="text/html") if index.exists() else _no_build()


@app.get("/open", include_in_schema=False)
def open_application():
    """The address start.bat opens: empty this browser's cache for the
    application, then go to it.

    A browser that cached an earlier version's page can keep showing it
    without asking the server. `Clear-Site-Data` makes it discard that copy
    before this page sends it on to `/`, so the current build is what loads.
    """
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Geofence Intelligence</title>"
        "<meta http-equiv='refresh' content='2;url=/'>"
        "<script>location.replace('/')</script>"
        "<p>Opening <a href='/'>Geofence Intelligence</a>...</p>",
        headers={"Clear-Site-Data": '"cache"', "Cache-Control": "no-store"},
    )


@app.get("/validate", tags=["ops"])
def validate(samples: int = Query(2000, ge=100, le=50000)):
    """Cross-check the engine against MySQL ST_Contains on live geometry."""
    from nexgen.shared.geoengine.validate import cross_check

    return cross_check(samples=samples)


# ---------------------------------------------------------------------------
# SPA fallback -- registered last so every API route above wins.
# ---------------------------------------------------------------------------

_API_PREFIXES = ("api/", "map/", "live/", "runs", "master/", "docs", "redoc", "openapi",
                 "static/", "assets/", "health", "locate", "validate")


@app.get("/{path:path}", include_in_schema=False)
def spa_fallback(path: str):
    """Deep links such as /trips/28844883 are client-side routes: return the
    app shell and let the router resolve them. Unknown API paths stay 404."""
    if path.startswith(_API_PREFIXES):
        raise HTTPException(404, f"not found: /{path}")
    candidate = (DIST_DIR / path).resolve()
    if DIST_DIR.is_dir() and candidate.is_file() and DIST_DIR.resolve() in candidate.parents:
        return FileResponse(candidate)
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index, media_type="text/html")
    return _no_build()
