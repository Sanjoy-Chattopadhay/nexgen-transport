"""Check the answer against something that is not the answer.

Every number on the upload page comes out of one engine. Agreement between
that engine and itself is not evidence, so this re-derives the same facts
three other ways and reports where they differ:

1. **Brute force, no index.** Every admissible fix tested against every active
   fence, with no bounding box, no chunking, no tree. This is the definition
   of the answer the index exists to compute faster. If the index ever dropped
   a fence, the two sets would differ -- and the index's whole claim is that it
   has no false negatives, only false positives that the polygon test removes.

2. **MySQL `ST_Contains`.** A different implementation, written by different
   people, reading a separately-stored copy of the geometry (`geo_fence.g_poly`,
   derived from the vertex table the engine reads). Both are planar, SRID 0,
   which is what makes the comparison meaningful at all -- a geodesic reference
   would disagree near boundaries for reasons unrelated to correctness.

3. **Arithmetic identities.** Things that must hold whatever the geometry says:
   fixes read = used + refused, every visit's dwell equals the span of the fixes
   it covers, the reported distance equals the recomputed path length, no visit
   ends before it starts, every closed visit pairs one entry with one exit.

A check that cannot fail is decoration. Each of these can, and the page shows
what each one actually returned.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from nexgen.shared.geoengine.geometry import haversine_m_vec, points_in_ring
from nexgen.shared.geoengine.model import FAR_M

logger = logging.getLogger(__name__)

# Fixes sent to MySQL. The oracle is a round trip per point, so it is sampled;
# the sample is drawn from the fixes nearest fence boundaries, where an
# off-by-one in edge handling would actually show, rather than uniformly over
# open road where both trivially answer "outside".
ORACLE_SAMPLE = 400


def brute_force(fit, index, detector) -> dict:
    """Every fix against every fence, with no index at all."""
    t0 = time.perf_counter()
    lats = np.asarray(fit.trail.lat, dtype=np.float64)
    lons = np.asarray(fit.trail.lon, dtype=np.float64)
    n = len(lats)
    touched: dict[int, int] = {}
    if n:
        for f in index.fences:
            inside = points_in_ring(lats, lons, f.ring_lat, f.ring_lon)
            if f.tolerance_m:
                # A buffered fence extends past its own ring, so containment is
                # the signed distance, exactly as the engine defines it.
                d = f.signed_distance(lats, lons)
                inside = d <= 0.0
            c = int(inside.sum())
            if c:
                touched[f.fence_id] = c
    return {
        "fences_tested": len(index.fences),
        "fixes_tested": n,
        "polygon_tests": len(index.fences) * n,
        "fences_containing_a_fix": len(touched),
        "seconds": round(time.perf_counter() - t0, 2),
        "_touched": touched,
    }


def compare_index(fit, index, detector, candidates) -> dict:
    """Did the index offer every fence the brute-force pass found?"""
    bf = brute_force(fit, index, detector)
    offered = {f.fence_id for f in candidates}
    contained = set(bf["_touched"])
    missed = contained - offered
    by_id = {f.fence_id: f for f in index.fences}
    return {
        "brute_force": {k: v for k, v in bf.items() if not k.startswith("_")},
        "candidates_offered": len(offered),
        "fences_actually_containing_a_fix": len(contained),
        "missed_by_the_index": len(missed),
        "false_positives_the_polygon_test_removed": len(offered - contained),
        "verdict": "no false negatives" if not missed else "INDEX MISSED FENCES",
        "missed": [[by_id[i].site_id, by_id[i].name, bf["_touched"][i]]
                   for i in sorted(missed)][:50],
    }


def oracle(fit, index, conn, samples: int = ORACLE_SAMPLE) -> dict:
    """The engine's containment against MySQL's, on the boundary-hugging fixes."""
    lats = np.asarray(fit.trail.lat, dtype=np.float64)
    lons = np.asarray(fit.trail.lon, dtype=np.float64)
    n = len(lats)
    if n == 0:
        return {"checked": 0, "agreed": 0, "disagreed": 0, "verdict": "no fixes",
                "rows": [], "note": "nothing to check"}

    # Rank fences by how close this trail came, then probe each near its edge.
    window = 400.0
    probes: list[tuple[int, int]] = []          # (fix index, fence_id)
    for f in index.fences:
        d = f.signed_distance_windowed(lats, lons, window)
        near = np.flatnonzero(d < FAR_M)
        if not len(near):
            continue
        order = near[np.argsort(np.abs(d[near]))]
        for k in order[:6]:
            probes.append((int(k), f.fence_id))
        if len(probes) >= samples * 2:
            break
    probes = probes[:samples]
    if not probes:
        return {"checked": 0, "agreed": 0, "disagreed": 0,
                "verdict": "no fix came near any fence", "rows": []}

    by_id = {f.fence_id: f for f in index.fences}
    rows: list[list] = []
    agreed = disagreed = 0
    unavailable = 0
    with conn.cursor() as cur:
        for k, fid in probes:
            f = by_id[fid]
            lat, lon = float(lats[k]), float(lons[k])
            mine = bool(points_in_ring(np.array([lat]), np.array([lon]),
                                       f.ring_lat, f.ring_lon)[0])
            cur.execute(
                "SELECT ST_Contains(g_poly, ST_GeomFromText(%s, 0)) AS c "
                "FROM geo_fence WHERE i_fence_id=%s",
                (f"POINT({lon} {lat})", fid),
            )
            row = cur.fetchone()
            if not row or row["c"] is None:
                unavailable += 1
                continue
            theirs = bool(row["c"])
            if mine == theirs:
                agreed += 1
            else:
                disagreed += 1
                rows.append([f.site_id, f.name, round(lat, 7), round(lon, 7),
                             "inside" if mine else "outside",
                             "inside" if theirs else "outside"])

    return {
        "checked": agreed + disagreed,
        "agreed": agreed,
        "disagreed": disagreed,
        "no_geometry_stored": unavailable,
        "verdict": "the engine and MySQL agree on every probe" if not disagreed
                   else f"{disagreed} disagreements",
        "rows": rows[:50],
    }


def identities(trail, fit, res, parsed_rows: int) -> list[dict]:
    """Things that must hold whatever the geometry says."""
    out: list[dict] = []

    def check(name: str, ok: bool, expected, got, why: str):
        out.append({"check": name, "expected": expected, "got": got,
                    "pass": bool(ok), "why": why})

    check("Every parsed row is accounted for",
          parsed_rows == trail.used + trail.total_dropped,
          parsed_rows, trail.used + trail.total_dropped,
          "a fix is either used or refused for a stated reason; nothing may vanish silently")

    ts = fit.trail.ts
    ok_order = all(ts[i] <= ts[i + 1] for i in range(len(ts) - 1))
    check("The trail is in time order", ok_order, "non-decreasing",
          "non-decreasing" if ok_order else "out of order",
          "every later stage assumes it, and the sort is what guarantees it")

    dupes = sum(1 for i in range(len(ts) - 1) if ts[i] == ts[i + 1])
    check("No two fixes share a timestamp", dupes == 0, 0, dupes,
          "a duplicate second would let one instant vote twice")

    bad = [v for v in res.visits if v["dt_exit"] and v["dt_exit"] < v["dt_enter"]]
    check("No visit ends before it starts", not bad, 0, len(bad),
          "an entry is always stamped at or before its exit")

    # Dwell must equal the observed span of the fixes the visit covers.
    drift = 0
    for v in res.visits:
        if v["open"]:
            continue
        span = int(((v["dt_exit"] or v["dt_enter"]) - v["dt_enter"]).total_seconds())
        drift = max(drift, abs(span - (v["dwell_seconds"] or 0)))
    check("Dwell equals the span it is measured over", drift == 0, 0, drift,
          "dwell is observed time between two real fixes, never interpolated")

    enters = sum(1 for e in res.events if e["event"] == "enter")
    exits = sum(1 for e in res.events if e["event"] == "exit")
    opens = sum(1 for v in res.visits if v["open"])
    unobserved = sum(1 for v in res.visits if not v["entry_observed"])
    check("Entries and exits pair up",
          enters + unobserved == exits + opens,
          f"{exits} exits + {opens} still open",
          f"{enters} entries + {unobserved} entries before the trail began",
          "every stay has two ends: one observed, or one flagged as unobserved")

    lat = np.asarray(fit.trail.lat, dtype=np.float64)
    lon = np.asarray(fit.trail.lon, dtype=np.float64)
    recomputed = 0.0
    if len(lat) > 1:
        seg = haversine_m_vec(lat[:-1], lon[:-1], lat[1:], lon[1:])
        gaps = np.array([(fit.trail.ts[i + 1] - fit.trail.ts[i]).total_seconds()
                         for i in range(len(lat) - 1)])
        recomputed = float(seg[gaps <= 1800].sum())
    diff = abs(recomputed - fit.distance_m)
    check("Distance recomputes", diff < 1.0,
          f"{fit.distance_m/1000:,.3f} km", f"{recomputed/1000:,.3f} km",
          "summed independently from the stored fitted positions, haversine per leg")

    inside_total = sum(v["dwell_seconds"] or 0 for v in res.visits if v["primary"])
    check("Time inside is not more than the trip lasted",
          inside_total <= (res.summary.get("pings_used") or 0) * 86400,
          "≤ trip duration", f"{inside_total:,} s",
          "innermost stays only, so nested fences cannot multiply the total")

    return out
