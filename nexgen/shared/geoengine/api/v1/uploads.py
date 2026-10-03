"""Uploads: a spreadsheet of GPS in, every pipeline step out.

Read-only against everything else. An upload is analysed against the same
fence master, with the same engine and the same settings as a batch run, but
it writes only into its own three tables -- so somebody checking the engine,
or analysing a trip the fleet system never opened, cannot move a published
number.

Every step is served in the shape the page renders *and* the downloads emit,
because they are the same document: `/uploads/{id}` returns the steps with a
first page of rows, `/uploads/{id}/steps/{key}` pages through one of them, and
`/uploads/{id}/download/{key}.csv|json|xlsx` serves the whole thing. What a
user checks in Excel is therefore what they were shown, not a re-query that
might have drifted.
"""

from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from nexgen.shared.geoengine.config import DetectorConfig, FitConfig, settings
from nexgen.shared.geoengine.db import get_geo_db
from nexgen.shared.geoengine.upload import sample as sample_mod
from nexgen.shared.geoengine.upload import store
from nexgen.shared.geoengine.upload.parse import ParseError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["uploads"])

PAGE_ROWS = 200


# ---------------------------------------------------------------------------
# The sample trip
# ---------------------------------------------------------------------------

@router.get("/uploads/sample")
def sample_info(km: float = Query(500.0, ge=20, le=5000), conn=Depends(get_geo_db)):
    """Which trip the sample download would give, without building the file."""
    trip = sample_mod.pick(conn, km)
    if not trip:
        raise HTTPException(404, f"no finished trip near {km:,.0f} km to offer as a sample")
    return {"trip": trip}


@router.get("/uploads/sample.xlsx")
def sample_file(km: float = Query(500.0, ge=20, le=5000), trip: int | None = None,
                conn=Depends(get_geo_db)):
    """A real long trip's GPS as a spreadsheet, ready to upload."""
    try:
        data, name, meta = sample_mod.build(conn, km, trip)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"',
                 "X-Trip-No": str(meta.get("i_trip_no", "")),
                 "X-Fixes": str(meta.get("fixes", ""))},
    )


# ---------------------------------------------------------------------------
# Upload and analyse
# ---------------------------------------------------------------------------

@router.post("/uploads")
async def create_upload(
    file: UploadFile = File(..., description=".xlsx, .csv or .json of one trip's GPS"),
    label: str | None = Form(None),
    analyse: bool = Form(True),
):
    """Parse a file, store its fixes, and run the whole pipeline over them."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "the file is empty")
    try:
        upload_id = store.create(data, file.filename or "upload.xlsx", label)
    except ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:                                  # noqa: BLE001
        logger.exception("upload failed")
        raise HTTPException(500, f"could not read the file: {exc}") from exc

    if not analyse:
        return {"upload_id": upload_id, "status": "parsed"}
    try:
        result = store.analyse(upload_id)
    except Exception as exc:                                  # noqa: BLE001
        logger.exception("analysis of upload %s failed", upload_id)
        store.mark_failed(upload_id, str(exc))
        raise HTTPException(500, f"the file was read, but the analysis failed: {exc}") from exc
    return {"upload_id": upload_id, "status": "ready", **result}


@router.post("/uploads/{upload_id}/reanalyse")
def reanalyse(
    upload_id: int,
    hysteresis_m: float | None = None,
    confirm_seconds: float | None = None,
    escape_m: float | None = None,
    max_gap_seconds: float | None = None,
    adaptive_band: bool | None = None,
    variant: str | None = Query(None, description="fitted | raw"),
    still_kmph: float | None = None,
    stop_min_seconds: float | None = None,
    use_osrm: bool | None = None,
):
    """Run the same fixes again under different settings.

    The whole point of keeping the parsed fixes is that a threshold can be
    moved and the *same* input re-judged, which is the only honest way to see
    what a threshold is doing.
    """
    d = settings.detector.as_dict()
    for k, v in (("hysteresis_m", hysteresis_m), ("confirm_seconds", confirm_seconds),
                 ("escape_m", escape_m), ("max_gap_seconds", max_gap_seconds),
                 ("adaptive_band", adaptive_band)):
        if v is not None:
            d[k] = v
    f = {k[4:] if k.startswith("fit_") else k: v
         for k, v in settings.fit.as_dict().items()}
    f["variant"] = variant or f.get("variant", "fitted")
    if still_kmph is not None:
        f["still_kmph"] = still_kmph
    if stop_min_seconds is not None:
        f["stop_min_seconds"] = stop_min_seconds
    try:
        return store.analyse(upload_id, DetectorConfig(**d), FitConfig(**f), use_osrm)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:                                  # noqa: BLE001
        logger.exception("re-analysis of upload %s failed", upload_id)
        store.mark_failed(upload_id, str(exc))
        raise HTTPException(500, str(exc)) from exc


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@router.get("/uploads")
def list_uploads(limit: int = Query(50, ge=1, le=200), conn=Depends(get_geo_db)):
    return {"items": store.listing(conn, limit)}


@router.get("/uploads/{upload_id}")
def get_upload(upload_id: int, rows: int = Query(PAGE_ROWS, ge=0, le=5000),
               conn=Depends(get_geo_db)):
    """The upload header and every step, each with its first page of rows."""
    h = store.head(conn, upload_id)
    if not h:
        raise HTTPException(404, f"no upload {upload_id}")
    return {"upload": h, "steps": store.artifacts(conn, upload_id, max_rows=rows),
            "page_rows": rows}


@router.get("/uploads/{upload_id}/steps/{key}")
def get_step(upload_id: int, key: str, page: int = Query(1, ge=1),
             page_size: int = Query(PAGE_ROWS, ge=1, le=5000),
             conn=Depends(get_geo_db)):
    """One step, paged. The narrative comes back with every page so a deep
    link to page 12 still explains what it is showing."""
    doc = store.artifact(conn, upload_id, key)
    if doc is None:
        raise HTTPException(404, f"upload {upload_id} has no step {key!r}")
    all_rows = doc.get("rows") or []
    start = (page - 1) * page_size
    doc["rows"] = all_rows[start:start + page_size]
    doc["total_rows"] = len(all_rows)
    doc["page"] = page
    doc["page_size"] = page_size
    doc["pages"] = max(1, -(-len(all_rows) // page_size))
    return doc


@router.get("/uploads/{upload_id}/track")
def upload_track(upload_id: int, max_points: int = Query(8000, ge=200, le=60000),
                 conn=Depends(get_geo_db)):
    """Raw and fitted positions, the fences touched, stops and holes — for the map.

    Downsampled for drawing, but never by dropping the fixes that explain the
    result: spikes, refused fixes and stop boundaries are always kept.
    """
    h = store.head(conn, upload_id)
    if not h:
        raise HTTPException(404, f"no upload {upload_id}")
    trail = store.artifact(conn, upload_id, "fitted")
    if trail is None:
        raise HTTPException(409, "this upload has not been analysed yet")

    rows = trail["rows"]
    n = len(rows)
    keep_every = max(1, -(-n // max_points))
    points = []
    prev_stop = object()
    for i, r in enumerate(rows):
        # fix, ts, raw lat, raw lon, speed, role, fit lat, fit lon, method, moved, stop
        important = r[8] == "spike" or r[10] != prev_stop or i == 0 or i == n - 1
        prev_stop = r[10]
        if not important and i % keep_every:
            continue
        points.append([r[1], r[2], r[3], r[4], r[6], r[7], r[8], r[9], r[10]])

    kpis = store.artifact(conn, upload_id, "kpis") or {}
    visits = store.artifact(conn, upload_id, "visits") or {}
    stops = store.artifact(conn, upload_id, "stops") or {}
    gaps = store.artifact(conn, upload_id, "gaps") or {}

    site_ids = sorted({v[0] for v in (visits.get("rows") or [])})
    fences = _rings(conn, site_ids)

    lats = [r[2] for r in rows if r[2] is not None]
    lons = [r[3] for r in rows if r[3] is not None]
    return {
        "upload_id": upload_id,
        "columns": ["ts", "lat", "lon", "speed", "fit_lat", "fit_lon", "method",
                    "moved_m", "stop"],
        "total_points": n, "returned": len(points), "points": points,
        "fences": fences,
        "stops": [{"seq": s[0], "from": s[1], "to": s[2], "seconds": s[3],
                   "lat": s[5], "lon": s[6], "spread_m": s[7]}
                  for s in (stops.get("rows") or [])],
        "gaps": [{"from": g[0], "to": g[1], "seconds": g[2], "kind": g[3],
                  "straight_m": g[4]} for g in (gaps.get("rows") or [])],
        "timeline": (kpis.get("extra") or {}).get("timeline", []),
        "bbox": [min(lons), min(lats), max(lons), max(lats)] if lats else None,
    }


@router.get("/uploads/{upload_id}/index-map")
def index_map(upload_id: int, level: int = Query(-1, description="-1 = a sensible middle level"),
              conn=Depends(get_geo_db)):
    """The R-tree over India: node boxes at one level, the Hilbert curve
    through the leaves, the trip's chunk boxes, and which fences were chosen.

    This is the index made visible. The node boxes show what the Hilbert
    packing bought — tight, compact groups rather than country-spanning
    rectangles — and the chunk boxes show the query tracking the road.
    """
    from nexgen.shared.geoengine.store import build_index, max_tolerance_m

    h = store.head(conn, upload_id)
    if not h:
        raise HTTPException(404, f"no upload {upload_id}")

    idx = build_index(active_only=True, pad_m=max_tolerance_m())
    if level < 0:
        # High enough that the boxes are readable on a country-scale map, and
        # low enough that they still show clustering rather than one blob.
        level = max(1, min(idx.levels - 2, 2))
    level = max(0, min(level, idx.levels - 1))
    boxes = idx.node_boxes(level)

    cand = store.artifact(conn, upload_id, "candidates") or {}
    prune = store.artifact(conn, upload_id, "prune") or {}
    visits = store.artifact(conn, upload_id, "visits") or {}
    hilbert = store.artifact(conn, upload_id, "hilbert") or {}

    hit_sites = {v[0] for v in (visits.get("rows") or [])}
    cand_sites = {r[0] for r in (prune.get("rows") or [])}

    fences = idx.fences
    pts = [[round(float(f.centroid_lon), 5), round(float(f.centroid_lat), 5),
            f.site_id,
            2 if f.site_id in hit_sites else 1 if f.site_id in cand_sites else 0]
           for f in fences]

    return {
        "level": level,
        "levels": idx.levels,
        "level_label": ("leaves — one box per fence" if level == 0 else
                        "root — one box over the whole master" if level == idx.levels - 1
                        else f"internal level {level}"),
        "node_boxes": [[round(float(b[0]), 5), round(float(b[1]), 5),
                        round(float(b[2]), 5), round(float(b[3]), 5)] for b in boxes],
        "fences": pts,
        "curve": (hilbert.get("extra") or {}).get("curve", []),
        "chunk_boxes": (cand.get("extra") or {}).get("chunk_boxes", []),
        "counts": {
            "master": len(fences),
            "nodes_at_level": len(boxes),
            "candidates": len(cand_sites),
            "hit": len(hit_sites),
        },
        "stats": idx.stats,
    }


@router.get("/uploads/{upload_id}/fences")
def upload_fence_rings(upload_id: int, conn=Depends(get_geo_db)):
    """The rings of every fence this upload touched, for the map."""
    visits = store.artifact(conn, upload_id, "visits")
    if visits is None:
        raise HTTPException(404, f"upload {upload_id} has no analysis yet")
    return {"fences": _rings(conn, sorted({v[0] for v in (visits.get("rows") or [])}))}


def _rings(conn, site_ids: list[int], limit: int = 300) -> list[dict]:
    if not site_ids:
        return []
    marks = ",".join(["%s"] * len(site_ids))
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT i_site_id, s_site_name, s_category, d_area_sqm,
                       CASE WHEN d_area_sqm < 10000 THEN 'micro'
                            WHEN d_area_sqm < 1000000 THEN 'site'
                            WHEN d_area_sqm < 100000000 THEN 'campus'
                            ELSE 'regional' END AS s_scale
                  FROM geo_fence WHERE i_site_id IN ({marks})
                 ORDER BY d_area_sqm DESC LIMIT %s""", (*site_ids, limit))
        meta = {r["i_site_id"]: r for r in cur.fetchall()}
        if not meta:
            return []
        marks = ",".join(["%s"] * len(meta))
        cur.execute(
            f"""SELECT i_site_id, d_lat, d_long FROM geo_site_vertex
                 WHERE i_site_id IN ({marks}) ORDER BY i_site_id, i_seq""", list(meta))
        rings: dict[int, list] = {}
        for r in cur.fetchall():
            rings.setdefault(r["i_site_id"], []).append(
                [round(float(r["d_lat"]), 6), round(float(r["d_long"]), 6)])
    return [{"site_id": sid, "name": m["s_site_name"], "category": m["s_category"],
             "scale": m["s_scale"], "ring": rings.get(sid, [])} for sid, m in meta.items()]


# ---------------------------------------------------------------------------
# Downloads -- the same document the page rendered
# ---------------------------------------------------------------------------

def _csv(columns: list, rows: list) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    # utf-8 with a BOM, because Excel on Windows reads a BOM-less utf-8 CSV as
    # the system codepage and mangles every non-ASCII site name.
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def _xlsx_sheets(sheets: list[tuple[str, list, list]], notes: list[tuple] | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    fill = PatternFill("solid", fgColor="1F3A5F")
    used: set[str] = set()
    for title, columns, rows in sheets:
        # Excel sheet names: 31 chars, no []:*?/\, and unique.
        name = "".join(c for c in str(title) if c not in "[]:*?/\\")[:31] or "Sheet"
        base, i = name, 2
        while name.lower() in used:
            name = f"{base[:28]}_{i}"
            i += 1
        used.add(name.lower())
        ws = wb.create_sheet(name)
        ws.append([str(c) for c in columns])
        for c in range(1, len(columns) + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", wrap_text=True)
        for r in rows:
            ws.append([v if isinstance(v, (int, float, type(None))) else str(v) for v in r])
        for c, col in enumerate(columns, start=1):
            ws.column_dimensions[get_column_letter(c)].width = min(
                44, max(11, len(str(col)) + 3))
        ws.freeze_panes = "A2"
    if notes:
        ws = wb.create_sheet("About", 0)
        ws.append(["Geofence Intelligence · uploaded trip"])
        ws.cell(row=1, column=1).font = Font(bold=True, size=13)
        ws.append([])
        for row in notes:
            ws.append(list(row))
        ws.column_dimensions["A"].width = 34
        ws.column_dimensions["B"].width = 90
        for row in ws.iter_rows(min_col=2, max_col=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _disposition(name: str) -> dict:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.get("/uploads/{upload_id}/download/pings.csv")
def download_pings(upload_id: int, conn=Depends(get_geo_db)):
    """The fixes exactly as parsed out of the file — the input, so the whole
    analysis can be reproduced from the same starting point."""
    with conn.cursor() as cur:
        cur.execute("""SELECT i_seq, i_row, dt_message, d_lat, d_long, i_speed
                         FROM geo_upload_ping WHERE i_upload_id=%s ORDER BY i_seq""",
                    (upload_id,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"no upload {upload_id}")
    return Response(
        _csv(["seq", "file row", "timestamp", "lat", "lon", "speed"],
             [[r["i_seq"], r["i_row"], r["dt_message"], float(r["d_lat"]),
               float(r["d_long"]), r["i_speed"]] for r in rows]),
        media_type="text/csv; charset=utf-8",
        headers=_disposition(f"upload-{upload_id}-pings.csv"))


@router.get("/uploads/{upload_id}/download/{key}.{fmt}")
def download_step(upload_id: int, key: str, fmt: str, conn=Depends(get_geo_db)):
    """One step, whole, as csv / json / xlsx."""
    doc = store.artifact(conn, upload_id, key)
    if doc is None:
        raise HTTPException(404, f"upload {upload_id} has no step {key!r}")
    stem = f"upload-{upload_id}-{key}"
    if fmt == "json":
        return JSONResponse(doc, headers=_disposition(f"{stem}.json"))
    if fmt == "csv":
        return Response(_csv(doc["columns"], doc["rows"]), media_type="text/csv; charset=utf-8",
                        headers=_disposition(f"{stem}.csv"))
    if fmt == "xlsx":
        notes = [("Step", doc["title"]), ("What it does", doc["what"]),
                 ("Why", doc["why"]), ("How", doc["how"])]
        notes += [(str(k), "" if v is None else str(v)) for k, v in doc["stats"].items()]
        return Response(
            _xlsx_sheets([(key, doc["columns"], doc["rows"])], notes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=_disposition(f"{stem}.xlsx"))
    raise HTTPException(400, f"unknown format {fmt!r}; use csv, json or xlsx")


@router.get("/uploads/{upload_id}/report.xlsx")
def download_all(upload_id: int, conn=Depends(get_geo_db)):
    """Every step of the analysis in one workbook: a sheet per step, an About
    sheet carrying each step's explanation and numbers.

    This is the artifact somebody takes away to check the result independently
    — which is why it carries the reasoning and the settings, not just the
    rows. A table of dwell times with no statement of the thresholds that
    produced them cannot be verified by anyone.
    """
    h = store.head(conn, upload_id)
    if not h:
        raise HTTPException(404, f"no upload {upload_id}")
    docs = store.artifacts(conn, upload_id)
    if not docs:
        raise HTTPException(409, "this upload has not been analysed yet")

    notes: list[tuple] = [
        ("File", h["s_name"]),
        ("Uploaded", str(h["dt_uploaded"])),
        ("Vehicle", h["s_asset_id"] or "—"),
        ("GPS from", str(h["dt_first_ping"])),
        ("GPS to", str(h["dt_last_ping"])),
        ("Fixes parsed", h["i_rows_parsed"]),
        ("Fixes used", h["i_pings_used"]),
        ("Fence visits", h["i_visits"]),
        ("Places", h["i_places"]),
        ("Distance (km)", h["d_distance_km"]),
        ("GPS quality", h["s_quality"]),
        ("", ""),
        ("Settings the analysis ran under", json.dumps(h.get("j_params") or {})),
        ("", ""),
    ]
    sheets = []
    for d in docs:
        notes += [("— " + d["title"], ""), ("   what", d["what"]), ("   why", d["why"]),
                  ("   how", d["how"])]
        notes += [(f"   {k}", "" if v is None else str(v)) for k, v in d["stats"].items()]
        notes.append(("", ""))
        if d["columns"]:
            sheets.append((d["key"], d["columns"], d["rows"]))

    data = _xlsx_sheets(sheets, notes)
    return Response(
        data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_disposition(f"upload-{upload_id}-full-analysis.xlsx"))


@router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: int):
    if not store.delete(upload_id):
        raise HTTPException(404, f"no upload {upload_id}")
    return {"deleted": upload_id}
