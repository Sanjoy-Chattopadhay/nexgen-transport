"""Run one trail against the fence master.

The shape of the hot loop, and why
----------------------------------
The naive form of this problem is "for every fix, for every fence, is the fix
inside?" -- 5.1M x 4,937, about 25 billion polygon tests. Even at a
microsecond each that is eight hours.

Three observations collapse it:

1. **A trajectory is not a set of independent points.** Consecutive fixes are
   metres apart. The set of fences that could possibly matter to a 500-fix
   stretch of trail is the set whose bounding box touches that stretch's
   bounding box -- one index query per 500 fixes instead of one per fix. Not
   per *trip*, though: a Jamshedpur-Delhi trip's bounding box covers half of
   northern India and selects most of the master. Per chunk, it tracks the
   road.

2. **Almost every candidate is eliminated by four float comparisons.** Once a
   fence is a candidate, testing whether *any* fix falls in its bounding box
   is a vectorised numpy mask over the whole trail. Fences that survive the
   chunk query but were merely near the road fail this and cost nothing more.

3. **Only the surviving few need real geometry**, and then it is computed for
   the whole trail in one vectorised call rather than per fix -- the
   point-in-polygon loop runs over the ring's *edges* (a median of 13) with
   numpy handling every fix at once.

The result is that the per-fix cost on open road is a handful of comparisons,
and the expensive geometry runs only where a truck was actually near a fence.

What comes out
--------------
Per fence the truck genuinely interacted with: the confirmed crossings, the
visits those pair into, and any rule breaches. Plus a per-trip summary
carrying the data-quality verdict, because a clean answer computed from a
broken trail is the most dangerous output this system can produce.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from nexgen.shared.geoengine.engine import state as st
from nexgen.shared.geoengine.engine.filters import CleanTrail
from nexgen.shared.geoengine.geometry import haversine_m_vec
from nexgen.shared.geoengine.index import FenceIndex
from nexgen.shared.geoengine.model import Fence

logger = logging.getLogger(__name__)

# Fixes per index query. Small enough that a chunk's bounding box hugs the
# road; large enough that the tree descent is amortised over many fixes.
DEFAULT_CHUNK = 512


@dataclass
class TrailResult:
    trip_no: int | None = None
    asset_id: str | None = None
    events: list[dict] = field(default_factory=list)
    visits: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    candidates_tested: int = 0
    fences_hit: int = 0


def evaluate_trail(
    trail: CleanTrail,
    index: FenceIndex,
    detector,
    trip_no: int | None = None,
    asset_id: str | None = None,
    chunk: int = DEFAULT_CHUNK,
) -> TrailResult:
    """Detect every fence interaction along one cleaned trail."""
    res = TrailResult(trip_no=trip_no, asset_id=asset_id)
    n = trail.used
    if n == 0:
        res.summary = _summary(trail, [], [], None, None, 0)
        return res

    lats = np.asarray(trail.lat, dtype=np.float64)
    lons = np.asarray(trail.lon, dtype=np.float64)
    ts = trail.ts
    speeds = trail.speed

    # The window inside which exact geometry is required. Beyond it the
    # detector's thresholds cannot be affected, so distance is not computed.
    window_m = max(detector.escape_m, detector.hysteresis_m) * 4.0 + 1000.0

    # Pad the candidate search by the same window in degrees, so a fence the
    # truck skirted without entering is still available to the escape rule.
    pad_deg = window_m / 110_574.0

    candidates: list[Fence] = index.candidates_for_trail(
        lats, lons, chunk=chunk, pad_deg=pad_deg
    )
    res.candidates_tested = len(candidates)

    # Inter-fix distances, computed once and shared by every fence that needs
    # a path length.
    seg_m = np.zeros(n, dtype=np.float64)
    if n > 1:
        seg_m[1:] = haversine_m_vec(lats[:-1], lons[:-1], lats[1:], lons[1:])

    inside_any = np.zeros(n, dtype=bool)
    all_visits: list[dict] = []
    all_events: list[dict] = []
    all_violations: list[dict] = []
    hit = 0

    for fence in candidates:
        # Cheap rejection: did the trail ever come near this fence's box?
        min_lon, min_lat, max_lon, max_lat = fence.bbox(window_m)
        near = ((lats >= min_lat) & (lats <= max_lat)
                & (lons >= min_lon) & (lons <= max_lon))
        if not near.any():
            continue

        dist = fence.signed_distance_windowed(lats, lons, window_m)
        band = detector.band_for(fence)
        # Nothing even within the band: no crossing is possible.
        if not (dist <= band).any():
            continue

        det = st.detect(
            ts, dist,
            hysteresis_m=band,
            confirm_seconds=detector.confirm_seconds,
            escape_m=detector.escape_m,
            max_gap_seconds=detector.max_gap_seconds,
        )
        if not det.crossings and det.initial_state != st.INSIDE:
            continue

        hit += 1
        inside_any |= det.inside_mask

        for c in det.crossings:
            all_events.append({
                "fence": fence, "event": c.event, "ts": c.ts,
                "gap_seconds": c.gap_seconds,
                "lat": float(lats[c.index]), "lon": float(lons[c.index]),
                "speed": speeds[c.index], "confirmed_by": c.confirmed_by,
            })

        for v in st.pair_visits(det, ts):
            a, b = v["start"], v["end"]
            span = speeds[a:b + 1]
            spans = [s for s in span if s is not None]
            dwell = int((ts[b] - ts[a]).total_seconds())
            all_visits.append({
                "fence": fence,
                "dt_enter": ts[a], "dt_exit": None if v["open"] else ts[b],
                "open": v["open"],
                # Always the *observed* dwell. When `open` is set the truck was
                # still inside at the last fix, so this is a lower bound, not a
                # measurement -- but a lower bound is far more useful than the
                # NULL that a stricter reading would produce, and `open` says
                # which of the two it is.
                "dwell_seconds": dwell,
                "pings": b - a + 1,
                "max_speed": max(spans) if spans else None,
                "distance_m": float(seg_m[a + 1:b + 1].sum()) if b > a else 0.0,
                "enter_gap": v["enter_gap"], "exit_gap": v["exit_gap"],
                "entry_observed": v["entry_observed"],
                "confirmed_by": v["confirmed_by"],
            })

            all_violations.extend(_violations(fence, ts, lats, lons, speeds, a, b))

    _mark_primary(all_visits)

    res.events = sorted(all_events, key=lambda e: e["ts"])
    res.visits = sorted(all_visits, key=lambda v: v["dt_enter"])
    res.violations = sorted(all_violations, key=lambda v: v["ts"])
    res.fences_hit = hit

    inside_seconds = 0
    if n > 1:
        gaps = np.array([(ts[i] - ts[i - 1]).total_seconds() for i in range(1, n)])
        gaps = np.minimum(gaps, detector.max_gap_seconds)
        inside_seconds = int(gaps[inside_any[1:]].sum())

    res.summary = _summary(trail, res.visits, res.violations,
                           ts[0], ts[-1], inside_seconds,
                           pings_inside=int(inside_any.sum()))
    return res


def _mark_primary(visits: list[dict]) -> None:
    """Flag the innermost fence for each overlapping group of visits.

    The client's master nests heavily. A truck at the Jamshedpur hot strip
    mill is genuinely inside "HSM MILL", "INSIDE TSL", "INSIDE TSL_CSD",
    "TATA STEEL, JAMSHEDPUR", "TWS" and a city-scale polygon simultaneously --
    six true containments for one physical location. Reporting that as six
    visits is correct and useless; suppressing five of them is useful and
    wrong.

    So all six are kept, and the smallest-area fence overlapping in time is
    marked `primary`. "Where is the truck" reads the primary; "which zones
    does this touch" reads them all. Smallest-area is the right tie-break
    because a containing fence always has the larger area, so the innermost
    zone wins without needing an explicit containment hierarchy the master
    does not provide.
    """
    for v in visits:
        v["primary"] = True
    for i, a in enumerate(visits):
        a_end = a["dt_exit"] or a["dt_enter"]
        for j, b in enumerate(visits):
            if i == j:
                continue
            b_end = b["dt_exit"] or b["dt_enter"]
            overlaps = a["dt_enter"] <= b_end and b["dt_enter"] <= a_end
            if not overlaps:
                continue
            # Strictly smaller area wins; ties broken on fence_id so the
            # choice is stable across runs rather than dict-order dependent.
            if (b["fence"].area_m2, b["fence"].fence_id) < (a["fence"].area_m2, a["fence"].fence_id):
                a["primary"] = False
                break


def _violations(fence: Fence, ts, lats, lons, speeds, a: int, b: int) -> list[dict]:
    """Rule breaches inside one visit.

    Overspeed is reported once per visit, at its worst fix, not once per fix.
    A truck 10 km/h over for twenty minutes inside a works is one event an
    operator can act on; 1,200 of them is a wall of noise that guarantees the
    alert stream gets muted.
    """
    out: list[dict] = []

    if fence.category in ("restricted", "high_risk"):
        kind = "restricted_entry" if fence.category == "restricted" else "high_risk_entry"
        out.append({
            "fence": fence, "kind": kind, "ts": ts[a],
            "lat": float(lats[a]), "lon": float(lons[a]),
            "observed": None, "limit": None,
            "detail": f"entered {fence.category} site {fence.name}",
        })

    limit = fence.max_speed
    if limit and limit > 0:
        worst_i, worst_v = -1, -1
        for i in range(a, b + 1):
            s = speeds[i]
            if s is not None and s > limit and s > worst_v:
                worst_i, worst_v = i, s
        if worst_i >= 0:
            over = sum(1 for i in range(a, b + 1)
                       if speeds[i] is not None and speeds[i] > limit)
            out.append({
                "fence": fence, "kind": "overspeed", "ts": ts[worst_i],
                "lat": float(lats[worst_i]), "lon": float(lons[worst_i]),
                "observed": worst_v, "limit": limit,
                "detail": f"{over} fixes over the {limit} km/h site limit; peak {worst_v}",
            })
    return out


def _summary(trail: CleanTrail, visits, violations,
             first_ts, last_ts, inside_seconds: int,
             pings_inside: int = 0) -> dict:
    span = 0
    if first_ts and last_ts:
        span = int((last_ts - first_ts).total_seconds())
    return {
        "dt_first_ping": first_ts,
        "dt_last_ping": last_ts,
        "pings_read": trail.read,
        "pings_used": trail.used,
        "pings_dropped": trail.total_dropped,
        "pings_inside": pings_inside,
        "visits": len(visits),
        "distinct_sites": len({v["fence"].site_id for v in visits}),
        "violations": len(violations),
        "inside_seconds": inside_seconds,
        "max_gap_seconds": trail.max_gap_seconds,
        "coverage_pct": round(100.0 * inside_seconds / span, 2) if span else None,
        "quality": trail.quality,
    }
