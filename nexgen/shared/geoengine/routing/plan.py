"""The route a trip should have taken.

Two sources, and every result says which it used:

**osrm** -- the fastest road route from where the truck left its loading
place to where it arrived at its unloading place, asked of an OSRM server:
what a navigation app would have told the driver. OSRM's times are for its
profile's speeds, so a truck's planned driving time is OSRM's times a factor
(`ROUTE_TRUCK_TIME_FACTOR`), plus the rest the driver is allowed.

**learned** -- without OSRM, the lane's own history is the plan. Of the
lane's trips with clean GPS, the one whose transit path lies closest to all
the others (the geometric medoid) is the route trucks on that lane actually
take; the lane's median transit distance and times are the planned figures.
It needs a few trips to learn from, and it answers a different question from
OSRM -- "did this truck go the way trucks on this lane go?" rather than "the
shortest way" -- which is often the more useful one for a fleet: it already
knows about the bypass that avoids a town, the weighbridge on the way, the
road closed to trucks by day.

Both are cached in geo_route_plan: an OSRM route per pair of points rounded
to ~100 m, a learned corridor per lane and data version.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import numpy as np

from nexgen.shared.geoengine.osrm import polyline as pl
from nexgen.shared.geoengine.routing.geometry import Polyline, mean_offset_m, simplify

logger = logging.getLogger(__name__)

MIN_LANE_TRIPS = 3
MEDOID_SAMPLE = 25
# Part of every learned plan's cache key: bump it when the way plans are
# learned changes, so no lane keeps a plan the current method would not make.
PLAN_VERSION = "p2"


@dataclass
class Plan:
    mode: str                     # osrm | learned
    line: Polyline
    distance_m: float
    drive_s: float | None         # planned driving time
    transit_s: float | None       # planned transit time, rests included
    plan_id: int | None = None
    ref_trip: int | None = None
    sample: int | None = None

    def as_dict(self) -> dict:
        return {"mode": self.mode, "plan_id": self.plan_id, "distance_km": round(self.distance_m / 1000, 2),
                "drive_s": None if self.drive_s is None else int(self.drive_s),
                "transit_s": None if self.transit_s is None else int(self.transit_s),
                "ref_trip": self.ref_trip, "sample": self.sample}


def _key(lat: float, lon: float) -> str:
    return f"{lat:.3f},{lon:.3f}"


def rest_allowance_s(drive_s: float, rest_min_per_4h: float) -> float:
    """Rest a driver is entitled to on a drive of this length."""
    return (drive_s / (4 * 3600)) * rest_min_per_4h * 60


# ---------------------------------------------------------------------------
# OSRM
# ---------------------------------------------------------------------------

def osrm_plan(conn, client, a: tuple[float, float], b: tuple[float, float], truck_factor: float,
              rest_min_per_4h: float, lane: tuple[int | None, int | None] = (None, None)) -> Plan | None:
    """OSRM's route between two points, cached by the points rounded to ~100 m."""
    ka, kb = _key(*a), _key(*b)
    with conn.cursor() as cur:
        cur.execute("""SELECT i_plan_id, d_distance_m, d_duration_s, s_polyline FROM geo_route_plan
                        WHERE s_mode='osrm' AND s_from_key=%s AND s_to_key=%s""", (ka, kb))
        row = cur.fetchone()
    if row:
        pts = pl.decode(row["s_polyline"], 6)
        drive = float(row["d_duration_s"]) * truck_factor
        return Plan("osrm", Polyline([p[0] for p in pts], [p[1] for p in pts]), float(row["d_distance_m"]),
                    drive, drive + rest_allowance_s(drive, rest_min_per_4h), row["i_plan_id"])
    route = client.route(a[0], a[1], b[0], b[1])
    if route is None:
        return None
    pts = route.points()
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO geo_route_plan (s_mode, s_from_key, s_to_key, i_from_site, i_to_site,
                              d_from_lat, d_from_long, d_to_lat, d_to_long, d_distance_m, d_duration_s,
                              s_polyline, i_points)
                       VALUES ('osrm',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE d_distance_m=VALUES(d_distance_m), d_duration_s=VALUES(d_duration_s),
                              s_polyline=VALUES(s_polyline), i_points=VALUES(i_points), dt_built=CURRENT_TIMESTAMP""",
                    (ka, kb, lane[0], lane[1], a[0], a[1], b[0], b[1], route.distance_m, route.duration_s,
                     route.polyline6, len(pts)))
        plan_id = cur.lastrowid
    conn.commit()
    drive = route.duration_s * truck_factor
    return Plan("osrm", Polyline([p[0] for p in pts], [p[1] for p in pts]), route.distance_m, drive,
                drive + rest_allowance_s(drive, rest_min_per_4h), plan_id)


def osrm_router(client, truck_factor: float):
    """A router for deviation.detect: a new route from the truck to the end."""
    def route(a_lat, a_lon, b_lat, b_lon):
        r = client.route(a_lat, a_lon, b_lat, b_lon)
        if r is None:
            return None
        pts = r.points()
        return [p[0] for p in pts], [p[1] for p in pts], r.distance_m, r.duration_s * truck_factor
    return route


# ---------------------------------------------------------------------------
# learned from the lane
# ---------------------------------------------------------------------------

def medoid(paths: dict[int, tuple[np.ndarray, np.ndarray]]) -> tuple[int, float]:
    """The path lying closest to all the others: for each candidate, the
    median over the others of the symmetric mean offset. A handful of
    detours cannot pull it, which a mean path would."""
    ids = list(paths)
    if len(ids) == 1:
        return ids[0], 0.0
    n = len(ids)
    m = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                m[i, j] = mean_offset_m(paths[ids[i]], paths[ids[j]])
    sym = (m + m.T) / 2
    score = [float(np.median(np.delete(sym[i], i))) for i in range(n)]
    k = int(np.argmin(score))
    return ids[k], score[k]


def learned_plan(conn, run_id: int, lane: tuple[int, int], transit_paths, version: str,
                 rest_min_per_4h: float) -> Plan | None:
    """The lane's typical route and figures, cached per lane and data version.

    `transit_paths(trip_nos)` returns {trip: (lat, lon)} of those trips'
    transit fixes.
    """
    key = f"{lane[0]}>{lane[1]}"
    with conn.cursor() as cur:
        cur.execute("""SELECT i_plan_id, d_distance_m, d_duration_s, d_transit_s, s_polyline, i_ref_trip,
                              i_sample, s_version
                         FROM geo_route_plan WHERE s_mode='learned' AND s_from_key=%s AND s_to_key=%s""",
                    (key, str(run_id)))
        row = cur.fetchone()
    if row and row["s_version"] == version:
        if not row["s_polyline"]:
            return None
        pts = pl.decode(row["s_polyline"], 6)
        return Plan("learned", Polyline([p[0] for p in pts], [p[1] for p in pts]), float(row["d_distance_m"]),
                    row["d_duration_s"], row["d_transit_s"], row["i_plan_id"], row["i_ref_trip"], row["i_sample"])

    with conn.cursor() as cur:
        cur.execute("""SELECT p.i_trip_no, p.d_transit_km, p.i_transit_moving_s, p.i_transit_silent_s, p.i_transit_s
                         FROM geo_trip_phase p
                         JOIN geo_trip_summary s USING (i_run_id, i_trip_no)
                        WHERE p.i_run_id=%s AND p.i_loading_site_id=%s AND p.i_unloading_site_id=%s
                          AND p.s_shape='loaded' AND p.d_transit_km > 0 AND s.s_quality IN ('good','sparse')""",
                    (run_id, lane[0], lane[1]))
        trips = list(cur.fetchall())
    plan: Plan | None = None
    ref, sample, poly = None, len(trips), ""
    if len(trips) >= MIN_LANE_TRIPS:
        km = np.array([float(t["d_transit_km"]) for t in trips])
        med_km = float(np.median(km))
        # Candidates: trips whose distance is the lane's usual one, so the
        # route's shape and the planned kilometres agree -- the tightest band
        # that still holds three trips, the most recent first, capped for the
        # medoid search.
        near: list[dict] = []
        for band in (0.03, 0.06, 0.12, 0.25):
            near = [t for t in trips if abs(float(t["d_transit_km"]) - med_km) <= band * med_km]
            if len(near) >= MIN_LANE_TRIPS:
                break
        near = sorted(near, key=lambda t: -t["i_trip_no"])[:MEDOID_SAMPLE]
        paths = {k: v for k, v in transit_paths([t["i_trip_no"] for t in near]).items() if len(v[0]) >= 2}
        if paths:
            ref, _ = medoid(paths)
            lat, lon = simplify(*paths[ref], tolerance_m=25.0)
            drive = float(np.median([(t["i_transit_moving_s"] or 0) + (t["i_transit_silent_s"] or 0)
                                     for t in trips]))
            transit = float(np.median([t["i_transit_s"] for t in trips if t["i_transit_s"]]))
            plan = Plan("learned", Polyline(lat, lon), med_km * 1000, drive, transit, None, ref, sample)
            poly = pl.encode(list(zip(lat.tolist(), lon.tolist())), 6)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO geo_route_plan (s_mode, s_from_key, s_to_key, i_from_site, i_to_site,
                              d_distance_m, d_duration_s, d_transit_s, s_polyline, i_points, i_ref_trip, i_sample,
                              s_version, j_meta)
                       VALUES ('learned',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE d_distance_m=VALUES(d_distance_m), d_duration_s=VALUES(d_duration_s),
                              d_transit_s=VALUES(d_transit_s), s_polyline=VALUES(s_polyline), i_points=VALUES(i_points),
                              i_ref_trip=VALUES(i_ref_trip), i_sample=VALUES(i_sample), s_version=VALUES(s_version),
                              j_meta=VALUES(j_meta), dt_built=CURRENT_TIMESTAMP""",
                    (key, str(run_id), lane[0], lane[1], plan.distance_m if plan else None,
                     plan.drive_s if plan else None, plan.transit_s if plan else None, poly,
                     len(plan.line) if plan else 0, ref, sample, version,
                     json.dumps({"run_id": run_id, "min_trips": MIN_LANE_TRIPS})))
        cur.execute("""SELECT i_plan_id FROM geo_route_plan WHERE s_mode='learned' AND s_from_key=%s AND s_to_key=%s""",
                    (key, str(run_id)))
        pid = cur.fetchone()["i_plan_id"]
    conn.commit()
    if plan:
        plan.plan_id = pid
    return plan
