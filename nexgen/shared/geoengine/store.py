"""Load compiled fences out of the database and into a queryable index.

The index is built from `geo_site_vertex`, not from the `g_poly` column. The
vertices are the auditable form and the WKT is derived from them; reading back
the derived form would mean a bug in the WKT writer could never be caught,
because the writer and the reader would agree with each other while both
disagreed with the client's data. `tests/test_oracle.py` exploits the
separation: the engine runs off the vertices, MySQL runs off `g_poly`, and the
two must agree on every verdict.

Building the index over ~4,900 polygons takes about a second, so it is cached
per process and invalidated by the master import's timestamp rather than
rebuilt per request.
"""

from __future__ import annotations

import logging
import threading
import time

from nexgen.shared.geoengine.db import geo_conn
from nexgen.shared.geoengine.index import FenceIndex
from nexgen.shared.geoengine.model import Fence

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict[tuple, tuple[FenceIndex, float]] = {}


def load_fences(active_only: bool = True, conn=None) -> list[Fence]:
    """Every compiled fence, with its ring, as engine objects."""
    close = conn is None
    conn = conn or geo_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT i_fence_id, i_site_id, s_site_name, s_type, s_category,
                           i_max_speed, i_tolerance, b_active, b_self_intersecting,
                           s_geom_notes, d_inradius_m
                      FROM geo_fence
                     {'WHERE b_active = 1' if active_only else ''}"""
            )
            meta = {r["i_site_id"]: r for r in cur.fetchall()}
            if not meta:
                return []

            # One pass over the vertex table rather than a query per fence:
            # 4,900 round trips is seconds of latency, one scan is milliseconds.
            cur.execute(
                "SELECT i_site_id, i_seq, d_lat, d_long FROM geo_site_vertex ORDER BY i_site_id, i_seq"
            )
            rings: dict[int, list[tuple[float, float]]] = {}
            for r in cur.fetchall():
                sid = r["i_site_id"]
                if sid in meta:
                    rings.setdefault(sid, []).append((float(r["d_lat"]), float(r["d_long"])))
    finally:
        if close:
            conn.close()

    fences: list[Fence] = []
    for sid, m in meta.items():
        ring = rings.get(sid)
        if not ring or len(ring) < 3:
            logger.warning("fence for site %s has no usable ring; skipped", sid)
            continue
        fences.append(Fence(
            fence_id=m["i_fence_id"],
            site_id=sid,
            name=m["s_site_name"],
            ring=ring,
            site_type=m["s_type"],
            category=m["s_category"],
            max_speed=m["i_max_speed"],
            tolerance_m=float(m["i_tolerance"] or 0),
            active=bool(m["b_active"]),
            self_intersecting=bool(m["b_self_intersecting"]),
            notes=m["s_geom_notes"],
            inradius_m=None if m.get("d_inradius_m") is None else float(m["d_inradius_m"]),
        ))
    return fences


def _inradius_job(args):
    fence_id, site_id, ring = args
    f = Fence(fence_id=fence_id, site_id=site_id, name="", ring=ring)
    return fence_id, f.compute_inradius()


def ensure_inradius(workers: int | None = None, recompute: bool = False) -> dict:
    """Measure the inscribed radius of every fence that lacks one, and store it.

    A one-off cost per master import (about 40 s for the whole master across
    a process pool), after which every detector reads it from the table.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor

    t0 = time.perf_counter()
    with geo_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT i_fence_id, i_site_id FROM geo_fence"
                    + ("" if recompute else " WHERE d_inradius_m IS NULL"))
        todo = {r["i_site_id"]: r["i_fence_id"] for r in cur.fetchall()}
        if not todo:
            return {"computed": 0, "seconds": 0.0}
        cur.execute("SELECT i_site_id, d_lat, d_long FROM geo_site_vertex ORDER BY i_site_id, i_seq")
        rings: dict[int, list] = {}
        for r in cur.fetchall():
            if r["i_site_id"] in todo:
                rings.setdefault(r["i_site_id"], []).append((float(r["d_lat"]), float(r["d_long"])))
    jobs = [(todo[sid], sid, ring) for sid, ring in rings.items() if len(ring) >= 3]
    n = workers or max(1, (os.cpu_count() or 2) - 1)
    if n > 1 and len(jobs) > 50:
        with ProcessPoolExecutor(n) as pool:
            results = list(pool.map(_inradius_job, jobs, chunksize=16))
    else:
        results = [_inradius_job(j) for j in jobs]
    with geo_conn() as conn, conn.cursor() as cur:
        cur.executemany("UPDATE geo_fence SET d_inradius_m=%s WHERE i_fence_id=%s",
                        [(round(r, 2), fid) for fid, r in results])
        conn.commit()
    invalidate()
    return {"computed": len(results), "seconds": round(time.perf_counter() - t0, 1)}


def ensure_regions(recompute: bool = False) -> dict:
    """Record the state and district of every fence that lacks them
    (geo/regions.py). Seconds for the whole master; a no-op once done."""
    from nexgen.shared.geoengine.geo import regions

    t0 = time.perf_counter()
    with geo_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT i_fence_id FROM geo_fence" + ("" if recompute else " WHERE s_state IS NULL"))
        todo = {r["i_fence_id"] for r in cur.fetchall()}
    if not todo:
        return {"classified": 0, "seconds": 0.0}
    fences = [f for f in load_fences(active_only=False) if f.fence_id in todo]
    states, districts = regions.load_layers()
    rows = regions.classify(fences, states, districts)
    with geo_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE geo_fence SET s_state=%s, s_district=%s, s_states=%s, d_state_offset_m=%s WHERE i_fence_id=%s",
            [(r["state"], r["district"], r["states"], r["offshore_m"], r["fence_id"]) for r in rows])
        conn.commit()
    return {"classified": len(rows),
            "cross_border": sum(1 for r in rows if r["states"]),
            "offshore": sum(1 for r in rows if r["offshore_m"] is not None),
            "seconds": round(time.perf_counter() - t0, 1)}


def build_index(active_only: bool = True, node_size: int = 16,
                pad_m: float = 0.0, conn=None) -> FenceIndex:
    """Fresh index, no cache."""
    t0 = time.perf_counter()
    fences = load_fences(active_only=active_only, conn=conn)
    idx = FenceIndex(fences, node_size=node_size, pad_m=pad_m)
    idx.stats["build_seconds"] = round(time.perf_counter() - t0, 3)
    logger.info("built fence index: %s", idx.stats)
    return idx


def get_index(active_only: bool = True, pad_m: float = 0.0,
              max_age_s: float = 300.0) -> FenceIndex:
    """Process-cached index.

    `pad_m` is part of the cache key because it changes the bounding boxes the
    tree is built over -- an index padded for one detector configuration is
    not reusable by another.
    """
    key = (active_only, round(pad_m, 3))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[1]) < max_age_s:
            return hit[0]
    idx = build_index(active_only=active_only, pad_m=pad_m)
    with _lock:
        _cache[key] = (idx, now)
    return idx


def invalidate() -> None:
    """Drop the cache. Called after a master import."""
    with _lock:
        _cache.clear()


def max_tolerance_m(conn=None) -> float:
    """The largest outward buffer any fence declares.

    An index must be padded by at least this much before it can be used to
    find candidates, because a fence with a tolerance is *effectively* larger
    than its own bounding box: a point can be inside the buffered fence while
    lying outside the box the index filed it under. Without the padding those
    fences would go quietly undetected near their edges.
    """
    close = conn is None
    conn = conn or geo_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(i_tolerance), 0) AS m FROM geo_fence WHERE b_active=1")
            return float(cur.fetchone()["m"] or 0.0)
    finally:
        if close:
            conn.close()


def scale_of(fence: Fence) -> str:
    """Coarse size class for a fence.

    A "visit" means something different at each end of this range and reports
    must not aggregate across it: the corpus holds 1,419 fences under a hectare
    (a weighbridge, a gate) and 93 over 100 km2 (GANESH SURAT is 133,000 km2 --
    a regional catchment, not a facility). Averaging dwell time across both
    produces a number with no meaning.
    """
    a = fence.area_m2
    if a < 10_000:
        return "micro"          # < 1 ha -- gate, weighbridge, dock
    if a < 1_000_000:
        return "site"           # < 1 km2 -- a facility
    if a < 100_000_000:
        return "campus"         # < 100 km2 -- works complex, industrial belt
    return "regional"           # a city or district catchment
