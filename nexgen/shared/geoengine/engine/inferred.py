"""Fences a truck must have passed while its tracker was silent.

The detector only ever reports what fixes show. When a truck moves across a
hole in its trail, any fence on the road between the last fix before the hole
and the first fix after it is invisible to it -- not "never visited", just
unobserved. With an OSRM route across the hole (`prep/fit.py`), the fences on
that road can be named.

These are *inferences*, and are kept apart from observed visits in their own
table, never merged into visit counts. Each carries the window it must lie in
(the hole), an estimated entry and exit from the position along the route,
and a confidence that falls as the hole lengthens and as the route looks less
like what the truck could have done in the time.

Two kinds
---------
`through`    the most probable road path passes clearly inside the fence.
`near_stop`  the path passes within `near_m` of a facility fence and the hole
             holds far more time than the drive needs: the truck stopped
             somewhere, unobserved, and this fence is a candidate for where.
             Always low or medium confidence -- it is a lead, not a finding.

District-scale (`regional`) fences are skipped: passing through a catchment
is not an event anyone acts on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from nexgen.shared.geoengine.config import DetectorConfig
from nexgen.shared.geoengine.index import FenceIndex
from nexgen.shared.geoengine.model import Fence
from nexgen.shared.geoengine.store import scale_of

SAMPLE_M = 25.0
NEAR_M = 300.0
MIN_UNEXPLAINED_S = 900.0


@dataclass
class InferredPassage:
    gap_index: int
    fence: Fence
    kind: str                     # through | near_stop
    window_from: datetime
    window_to: datetime
    est_enter: datetime | None
    est_exit: datetime | None
    inside_m: float
    min_dist_m: float
    confidence: str               # high | medium | low


def densify(points: list[tuple[float, float]], step_m: float = SAMPLE_M):
    """Resample a route to roughly `step_m` spacing.

    OSRM geometries only carry vertices where the road bends, so a straight
    kilometre is two points -- which would step right over a 40 m fence.
    Returns (lats, lons, cumulative metres).
    """
    if not points:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    lats = [points[0][0]]
    lons = [points[0][1]]
    cum = [0.0]
    for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:]):
        mid = math.radians((a_lat + b_lat) / 2)
        dx = (b_lon - a_lon) * math.cos(mid) * 111_320.0
        dy = (b_lat - a_lat) * 110_574.0
        seg = math.hypot(dx, dy)
        if seg == 0.0:
            continue
        k = max(1, math.ceil(seg / step_m))
        for j in range(1, k + 1):
            f = j / k
            lats.append(a_lat + (b_lat - a_lat) * f)
            lons.append(a_lon + (b_lon - a_lon) * f)
            cum.append(cum[-1] + seg / k)
    return np.array(lats), np.array(lons), np.array(cum)


def _observed_inside(fence_id: int, when: datetime, visits: list[dict]) -> bool:
    for v in visits:
        if v["fence"].fence_id != fence_id:
            continue
        end = v["dt_exit"]
        if v["dt_enter"] <= when and (end is None or when <= end):
            return True
    return False


def _confidence(gap, kind: str) -> str:
    route = gap.route
    detour = route.distance_m / max(gap.straight_m, 1.0)
    # OSRM's car profile is faster than a laden truck, so a route that needs
    # more time than the hole had is not what happened.
    too_slow = route.duration_s > gap.gap_s * 1.25
    if too_slow or detour > 2.0:
        return "low"
    unexplained = gap.gap_s - route.duration_s
    if kind == "near_stop":
        return "medium" if unexplained >= 1800 else "low"
    if gap.gap_s <= 1800 and detour <= 1.5 and unexplained <= max(600.0, 0.5 * route.duration_s):
        return "high"
    if gap.gap_s <= 4 * 3600:
        return "medium"
    return "low"


def passages(gaps, index: FenceIndex, detector: DetectorConfig,
             observed_visits: list[dict], near_m: float = NEAR_M,
             min_unexplained_s: float = MIN_UNEXPLAINED_S) -> list[InferredPassage]:
    out: list[InferredPassage] = []
    for g in gaps:
        if g.kind != "moving" or g.route is None:
            continue
        pts = g.route.points()
        lats, lons, cum = densify(pts)
        if len(lats) < 2:
            continue
        total = float(cum[-1]) or 1.0
        pad_deg = (near_m + 50.0) / 110_574.0
        unexplained = g.gap_s - g.route.duration_s

        for fence in index.candidates_for_trail(lats, lons, chunk=256, pad_deg=pad_deg * 2):
            if scale_of(fence) == "regional":
                continue
            # A fence the truck was already seen inside at either end of the
            # hole is not something the route discovered.
            if (_observed_inside(fence.fence_id, g.dt_from, observed_visits)
                    or _observed_inside(fence.fence_id, g.dt_to, observed_visits)):
                continue
            dist = fence.signed_distance_windowed(lats, lons, near_m * 2)
            dmin = float(dist.min())
            inside = np.flatnonzero(dist <= -detector.band_for(fence))

            if len(inside):
                first, last = int(inside[0]), int(inside[-1])
                enter = g.dt_from + timedelta(seconds=g.route.duration_s * cum[first] / total)
                leave = g.dt_from + timedelta(seconds=g.route.duration_s * cum[last] / total)
                out.append(InferredPassage(
                    gap_index=g.index, fence=fence, kind="through",
                    window_from=g.dt_from, window_to=g.dt_to,
                    # With unexplained time in the hole the truck stopped
                    # somewhere, so position along the route no longer fixes
                    # the clock; only the window is honest then.
                    est_enter=min(enter, g.dt_to) if unexplained < min_unexplained_s else None,
                    est_exit=min(leave, g.dt_to) if unexplained < min_unexplained_s else None,
                    inside_m=float(cum[last] - cum[first]),
                    min_dist_m=dmin,
                    confidence=_confidence(g, "through"),
                ))
            elif dmin <= near_m and unexplained >= min_unexplained_s:
                out.append(InferredPassage(
                    gap_index=g.index, fence=fence, kind="near_stop",
                    window_from=g.dt_from, window_to=g.dt_to,
                    est_enter=None, est_exit=None, inside_m=0.0, min_dist_m=dmin,
                    confidence=_confidence(g, "near_stop"),
                ))
    return out
