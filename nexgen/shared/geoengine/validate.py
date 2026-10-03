"""Independent verification of the containment engine.

The engine decides containment with its own ray-casting implementation over
the vertex table. MySQL decides it with `ST_Contains` over the `g_poly`
column, which is a different implementation, written by different people,
reading a separately-stored copy of the geometry. Agreement between them is
therefore evidence; agreement between the engine and itself would not be.

Both are planar (SRID 0), which is what makes the comparison meaningful --
that was the reason for choosing SRID 0 in the schema. A geodesic reference
would disagree near boundaries for reasons that have nothing to do with
whether either is correct.

Sampling is deliberately adversarial. Uniform points over India would land
almost entirely on empty ground where both trivially answer "outside"; the
interesting points are the ones near boundaries, where an off-by-one in edge
handling shows up. So most samples are drawn just inside and just outside real
fence edges, and a minority uniformly, to catch anything the targeted sampling
would miss.
"""

from __future__ import annotations

import logging
import random

import numpy as np

from nexgen.shared.geoengine.db import geo_conn
from nexgen.shared.geoengine.geometry import points_in_ring
from nexgen.shared.geoengine.store import load_fences

logger = logging.getLogger(__name__)


def _sample_points(fences, samples: int, seed: int) -> list[tuple[float, float, int]]:
    """(lat, lon, fence_id) probes concentrated around fence boundaries."""
    rng = random.Random(seed)
    pts: list[tuple[float, float, int]] = []
    n_edge = int(samples * 0.7)
    n_box = samples - n_edge

    for _ in range(n_edge):
        f = rng.choice(fences)
        i = rng.randrange(f.n_vertices)
        j = (i + 1) % f.n_vertices
        # A point on the edge, jittered off it by up to ~30 m in each axis --
        # the scale of real GPS scatter, and the scale at which an incorrect
        # boundary convention becomes visible.
        t = rng.random()
        lat = f.ring_lat[i] + t * (f.ring_lat[j] - f.ring_lat[i])
        lon = f.ring_lon[i] + t * (f.ring_lon[j] - f.ring_lon[i])
        lat += rng.uniform(-3e-4, 3e-4)
        lon += rng.uniform(-3e-4, 3e-4)
        pts.append((float(lat), float(lon), f.fence_id))

    for _ in range(n_box):
        f = rng.choice(fences)
        pts.append((rng.uniform(f.min_lat, f.max_lat),
                    rng.uniform(f.min_lon, f.max_lon), f.fence_id))
    return pts


def cross_check(samples: int = 20000, seed: int = 20260913,
                active_only: bool = True) -> dict:
    """Compare engine containment against MySQL for `samples` probes.

    Tolerance is intentionally exact: no epsilon is applied. The two
    implementations read the same coordinates and use the same planar
    semantics, so any disagreement is a real difference in edge handling and
    should be surfaced, not smoothed over.
    """
    fences = load_fences(active_only=active_only)
    if not fences:
        return {"error": "no fences loaded", "mismatches": 0}

    by_id = {f.fence_id: f for f in fences}
    probes = _sample_points(fences, samples, seed)

    mismatches: list[dict] = []
    agree = 0
    engine_in = mysql_in = 0

    conn = geo_conn()
    try:
        with conn.cursor() as cur:
            # Batched so the comparison is not dominated by round trips.
            for start in range(0, len(probes), 500):
                chunk = probes[start:start + 500]
                cases = []
                params: list = []
                for k, (lat, lon, fid) in enumerate(chunk):
                    cases.append(
                        "SELECT %s AS k, ST_Contains(g_poly, ST_GeomFromText("
                        "CONCAT('POINT(', %s, ' ', %s, ')'), 0)) AS inside "
                        "FROM geo_fence WHERE i_fence_id = %s"
                    )
                    params.extend([k, lon, lat, fid])
                cur.execute(" UNION ALL ".join(cases), params)
                sql_result = {r["k"]: bool(r["inside"]) for r in cur.fetchall()}

                for k, (lat, lon, fid) in enumerate(chunk):
                    f = by_id[fid]
                    # Compare the raw polygon test, with tolerance excluded --
                    # tolerance is our rule, not part of the geometry, and
                    # MySQL knows nothing about it.
                    ours = bool(points_in_ring(
                        np.array([lat]), np.array([lon]), f.ring_lat, f.ring_lon)[0])
                    theirs = sql_result.get(k, False)
                    engine_in += ours
                    mysql_in += theirs
                    if ours == theirs:
                        agree += 1
                    elif len(mismatches) < 25:
                        mismatches.append({
                            "fence_id": fid, "site_id": f.site_id, "name": f.name,
                            "lat": lat, "lon": lon,
                            "engine": ours, "mysql": theirs,
                            "vertices": f.n_vertices,
                            "self_intersecting": f.self_intersecting,
                        })
    finally:
        conn.close()

    total = len(probes)
    return {
        "samples": total,
        "agree": agree,
        "mismatches": total - agree,
        "agreement_pct": round(100.0 * agree / total, 6) if total else None,
        "engine_inside": engine_in,
        "mysql_inside": mysql_in,
        "examples": mismatches,
        "note": ("Compared against MySQL ST_Contains on the independently "
                 "stored g_poly column, planar SRID 0, no epsilon."),
    }
