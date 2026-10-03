"""Filter and fit: turn a raw device trail into the best position estimate per fix.

This runs between the pre-filter (`engine/filters.py`, which decides which
fixes are admissible at all) and the crossing detector (`engine/state.py`,
which decides what a sequence of positions means). Its job is narrower than
either: for each admissible fix, what is the most accurate estimate of where
the truck actually was at that instant?

Raw fixes are never overwritten. The fitted position is stored beside the raw
one with the method that produced it and how far it moved, so every visit can
be traced back to the fixes it rests on and re-derived when settings change.
Timestamps are never touched: every fitted position is still stamped with an
instant a fix was really observed at.

What the corpus says needs fixing (measured, see docs/PIPELINE.md)
-----------------------------------------------------------------
* 75% of fixes are standstills, and 80% of consecutive standstill fixes carry
  identical coordinates -- the receiver holds its last position. The rest
  scatter: 10% sit more than 25 m from their standstill's median, 0.57% more
  than 250 m, which is past the detector's escape distance.
* ~5,500 out-and-back spikes survive the speed gate: a stationary truck
  "jumps" 100 m to 2 km for one or two fixes and lands back within 30 m. The
  jump is slow enough (median implied 48 km/h) that no speed filter can
  reject it, and next to a fence line it confirms a false exit and re-entry.

What is done about it, in order
-------------------------------
1. **Standstills get a rolling median.** Over a run of fixes reporting at most
   `still_kmph`, each fix is replaced by the coordinate-wise median of up to
   `window_fixes` neighbours either side (never across a gap longer than the
   detector's `max_gap_seconds`, never further than `window_seconds`). A median
   removes impulses and preserves steps: up to three consecutive bad fixes
   vanish, while a genuine relocation of four fixes or more -- weighbridge to
   parking bay -- keeps its position and its timing. That is why this is a
   median filter and not one centroid per stop, which would erase exactly the
   short in-plant moves that micro-scale fences exist to catch. A window whose
   own spread exceeds `still_spread_m` is not really standing still (a stuck
   speed sensor, a slow crawl) and is left raw.
2. **Out-and-back spikes are corrected** even when the device reports speed:
   both neighbours standing still within `spike_base_m` of each other, each at
   most `spike_max_dt_s` away, and the fix itself `spike_m` or more off. A
   loaded truck cannot drive 100 m+ out and park back within 30 m of where it
   started inside five minutes; a multipath fix does it routinely.
3. **Moving fixes are snapped to the road only if OSRM is configured,** and
   only when the snap is short (`OSRM_MAX_SNAP_M`). Standstills are never
   sent: yards and weighbridges are off the network, and matching would drag
   a parked truck onto the nearest road. A moving fix OSRM cannot place on
   any road, lying well off the line through neighbours it did place, is a
   spike.
4. **Holes are classified, and routed when possible.** A hole across which the
   truck did not move is a sleeping tracker at a standstill and costs little
   evidence; one across which it moved is where visits can hide. With OSRM a
   moving hole gets its most probable road path, which `engine/inferred.py`
   turns into inferred passages -- always kept apart from observed visits.

The detector downstream is unchanged. It is given better positions, not
different rules, so everything it guarantees still holds.

References
----------
* Tukey, J. W. *Exploratory Data Analysis*, 1977 -- running medians.
* Gallagher, N. C. & Wise, G. L. "A theoretical analysis of the properties of
  median filters", IEEE Trans. ASSP 29(6), 1981 -- edge preservation.
* Zheng, Y. "Trajectory Data Mining: An Overview", ACM TIST 6(3), 2015 --
  noise filtering and stay-point detection.
* Newson, P. & Krumm, J. "Hidden Markov Map Matching Through Noise and
  Sparseness", ACM SIGSPATIAL 2009 -- the matcher OSRM implements.
"""

from __future__ import annotations

import calendar
import logging
import warnings
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from nexgen.shared.geoengine.config import DetectorConfig, FitConfig
from nexgen.shared.geoengine.engine.filters import CleanTrail, clean
from nexgen.shared.geoengine.osrm.client import OsrmClient, OsrmUnavailable, Route

logger = logging.getLogger(__name__)

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_EQ = 111_320.0

RAW = "raw"
MEDIAN = "median"
SPIKE = "spike"
SNAPPED = "snapped"


@dataclass
class Stop:
    seq: int
    start: int                 # index into the fitted trail
    end: int
    dt_start: datetime
    dt_end: datetime
    duration_s: int
    pings: int
    lat: float
    lon: float
    p90_spread_m: float
    max_gap_s: int
    spikes: int


@dataclass
class Gap:
    index: int                 # the fix that ended the hole
    dt_from: datetime
    dt_to: datetime
    gap_s: int
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    straight_m: float
    kind: str                  # stationary | moving
    route_status: str = "not_needed"   # ok | no_route | osrm_off | osrm_error | not_needed
    route: Route | None = None

    @property
    def unexplained_s(self) -> int | None:
        """Hole duration the road path does not account for -- time the truck
        spent somewhere, unobserved. None without a route."""
        if self.route is None:
            return None
        return int(round(self.gap_s - self.route.duration_s))


@dataclass
class FitResult:
    trail: CleanTrail              # fitted positions; same fixes and timestamps as `clean`
    raw_lat: np.ndarray
    raw_lon: np.ndarray
    still: np.ndarray              # bool per fix
    method: list[str]
    shift_m: np.ndarray
    snap_m: list[float | None]
    confidence: list[float | None]
    stop_of: np.ndarray            # stop seq per fix, -1 outside a stop
    stops: list[Stop] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    osrm_status: str = "off"
    quality: str = "good"
    quality_reason: str = ""
    distance_m: float = 0.0        # observed path length over fitted positions
    gap_distance_m: float = 0.0    # road (or straight-line) length across moving holes

    @property
    def spikes(self) -> int:
        return sum(1 for m in self.method if m == SPIKE)

    @property
    def snapped(self) -> int:
        return sum(1 for m in self.method if m == SNAPPED)

    @property
    def medians(self) -> int:
        return sum(1 for m in self.method if m == MEDIAN)

    def ping_rows(self, trip_no: int) -> list[tuple]:
        """Per-ping rows for `geo_fit_ping`, kept and refused alike."""
        t = self.trail
        rows: list[tuple] = []
        for i in range(t.used):
            pid = t.ids[i] if i < len(t.ids) else None
            if pid is None:
                continue
            rows.append((
                pid, trip_no, t.ts[i],
                "still" if self.still[i] else "move", None,
                int(self.stop_of[i]) if self.stop_of[i] >= 0 else None,
                round(float(t.lat[i]), 8), round(float(t.lon[i]), 8),
                self.method[i], round(float(self.shift_m[i]), 1),
                None if self.snap_m[i] is None else round(self.snap_m[i], 1),
                None if self.confidence[i] is None else round(self.confidence[i], 4),
            ))
        for pid, ts, lat, lon, reason in t.rejected:
            if ts is None:
                continue
            rows.append((pid, trip_no, ts, "reject", reason, None,
                         None if lat is None else round(float(lat), 8),
                         None if lon is None else round(float(lon), 8),
                         None, None, None, None))
        return rows


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dist_m(lat1, lon1, lat2, lon2):
    """Equirectangular distance; exact enough below a few km, vectorised."""
    lat1 = np.asarray(lat1, dtype=np.float64)
    lat2 = np.asarray(lat2, dtype=np.float64)
    mid = np.radians((lat1 + lat2) / 2.0)
    dx = (np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64)) \
        * np.cos(mid) * M_PER_DEG_LON_EQ
    dy = (lat2 - lat1) * M_PER_DEG_LAT
    return np.hypot(dx, dy)


def _haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2)
    return 2 * 6_371_008.8 * np.arcsin(np.sqrt(np.minimum(1.0, a)))


def _epochs(ts: list[datetime]) -> np.ndarray:
    """Seconds since the first fix. The feed's datetimes are naive local
    time; only differences are used, so no zone is assumed."""
    base = ts[0]
    return np.array([(t - base).total_seconds() for t in ts], dtype=np.float64)


def _unix(ts: datetime) -> float:
    """A monotonic epoch for OSRM, which wants absolute timestamps. The naive
    local time is read as if it were UTC; OSRM only differences them."""
    return float(calendar.timegm(ts.timetuple()))


# ---------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------

def prepare(rows, fit: FitConfig, detector: DetectorConfig,
            osrm: OsrmClient | None = None) -> FitResult:
    """Clean, fit, and classify one trip's fixes."""
    trail = clean(rows, detector.max_plausible_kmph, detector.max_gap_seconds)
    return fit_trail(trail, fit, detector, osrm)


def fit_trail(trail: CleanTrail, fit: FitConfig, detector: DetectorConfig,
              osrm: OsrmClient | None = None) -> FitResult:
    n = trail.used
    raw_lat = np.asarray(trail.lat, dtype=np.float64)
    raw_lon = np.asarray(trail.lon, dtype=np.float64)
    speed = np.array([np.nan if s is None else float(s) for s in trail.speed], dtype=np.float64)
    still = np.zeros(n, dtype=bool) if n == 0 else (~np.isnan(speed) & (speed <= fit.still_kmph))

    if n == 0:
        return FitResult(trail=trail, raw_lat=raw_lat, raw_lon=raw_lon, still=still,
                         method=[], shift_m=np.zeros(0), snap_m=[], confidence=[],
                         stop_of=np.zeros(0, dtype=np.int64), quality="no_gps",
                         quality_reason="no admissible fixes")

    t = _epochs(trail.ts)
    dt = np.diff(t)
    run_id = _standstill_runs(still, dt, detector.max_gap_seconds)

    fit_lat = raw_lat.copy()
    fit_lon = raw_lon.copy()
    method = np.array([RAW] * n, dtype=object)
    snap_m: list[float | None] = [None] * n
    conf: list[float | None] = [None] * n
    use_osrm = fit.variant != "raw" and osrm is not None and osrm.cfg.enabled
    statuses: list[str] = []

    if fit.variant != "raw":
        # 1. standstills
        mlat, mlon, spread, count = _rolling_median(raw_lat, raw_lon, t, run_id,
                                                    fit.window_fixes, fit.window_seconds)
        use = still & (count >= 3) & (spread <= fit.still_spread_m)
        fit_lat = np.where(use, mlat, raw_lat)
        fit_lon = np.where(use, mlon, raw_lon)
        method[use] = MEDIAN

        # 2. out-and-back spikes the median did not own
        _out_and_back(fit_lat, fit_lon, raw_lat, raw_lon, t, still, method, fit)

        # 3. road snapping for moving fixes
        if use_osrm and osrm.cfg.snap_moves:
            statuses.append(_snap_moves(fit_lat, fit_lon, raw_lat, raw_lon,
                                        t + _unix(trail.ts[0]), still, method,
                                        snap_m, conf, fit, detector, osrm))

        shift = _dist_m(raw_lat, raw_lon, fit_lat, fit_lon)
        # A median that moved a fix a long way was correcting a spike.
        method[(method == MEDIAN) & (shift >= fit.spike_m)] = SPIKE
    else:
        shift = np.zeros(n, dtype=np.float64)

    fitted = CleanTrail(
        ts=list(trail.ts), lat=fit_lat.tolist(), lon=fit_lon.tolist(),
        speed=list(trail.speed), ids=list(trail.ids), rejected=list(trail.rejected),
        read=trail.read, dropped=dict(trail.dropped),
        max_gap_seconds=trail.max_gap_seconds, breaks=trail.breaks,
        resequenced=trail.resequenced,
    )

    res = FitResult(trail=fitted, raw_lat=raw_lat, raw_lon=raw_lon, still=still,
                    method=method.tolist(), shift_m=shift, snap_m=snap_m, confidence=conf,
                    stop_of=np.full(n, -1, dtype=np.int64))

    res.stops = _stops(res, t, run_id, fit)
    res.gaps = _gaps(res, t, fit)
    if use_osrm and osrm.cfg.route_gaps:
        statuses.append(_route_gaps(res.gaps, osrm))
    res.osrm_status = _combine(statuses) if use_osrm else "off"

    obs = 0.0
    if n > 1:
        seg = _haversine_m(fit_lat[:-1], fit_lon[:-1], fit_lat[1:], fit_lon[1:])
        obs = float(seg[dt <= detector.max_gap_seconds].sum())
    res.distance_m = obs
    res.gap_distance_m = float(sum(
        (g.route.distance_m if g.route is not None else g.straight_m)
        for g in res.gaps if g.kind == "moving" and g.gap_s > detector.max_gap_seconds))

    res.quality, res.quality_reason = _quality(res)
    return res


# ---------------------------------------------------------------------------
# 1. standstills
# ---------------------------------------------------------------------------

def _standstill_runs(still: np.ndarray, dt: np.ndarray, max_gap_s: float) -> np.ndarray:
    """Run number per standstill fix; a unique negative per moving fix, so a
    window can never reach across a moving fix or a long hole."""
    n = len(still)
    starts = still.copy()
    if n > 1:
        continues = still[1:] & still[:-1] & (dt <= max_gap_s)
        starts[1:] = still[1:] & ~continues
    run = np.cumsum(starts) - 1
    return np.where(still, run, -1 - np.arange(n))


def _rolling_median(lat, lon, t, run_id, half: int, window_s: float):
    n = len(lat)
    offsets = np.arange(-half, half + 1)
    idx = np.arange(n)[:, None] + offsets[None, :]
    valid = (idx >= 0) & (idx < n)
    idc = np.clip(idx, 0, n - 1)
    valid &= run_id[idc] == run_id[:, None]
    valid &= np.abs(t[idc] - t[:, None]) <= window_s
    wl = np.where(valid, lat[idc], np.nan)
    wo = np.where(valid, lon[idc], np.nan)
    count = valid.sum(axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mlat = np.nanmedian(wl, axis=1)
        mlon = np.nanmedian(wo, axis=1)
        cos = np.cos(np.radians(np.where(np.isnan(mlat), lat, mlat)))
        dx = (wo - mlon[:, None]) * cos[:, None] * M_PER_DEG_LON_EQ
        dy = (wl - mlat[:, None]) * M_PER_DEG_LAT
        spread = np.nanmedian(np.hypot(dx, dy), axis=1)
    spread = np.where(np.isnan(spread), np.inf, spread)
    return mlat, mlon, spread, count


# ---------------------------------------------------------------------------
# 2. out-and-back spikes
# ---------------------------------------------------------------------------

def _out_and_back(fit_lat, fit_lon, raw_lat, raw_lon, t, still, method, fit: FitConfig) -> None:
    n = len(t)
    if n < 3:
        return
    # one-fix excursions
    k = np.arange(1, n - 1)
    d_prev = t[k] - t[k - 1]
    d_next = t[k + 1] - t[k]
    cand = ((method[k] == RAW) & still[k - 1] & still[k + 1]
            & (d_prev > 0) & (d_next > 0)
            & (d_prev <= fit.spike_max_dt_s) & (d_next <= fit.spike_max_dt_s))
    k = k[cand]
    if len(k):
        base = _dist_m(fit_lat[k - 1], fit_lon[k - 1], fit_lat[k + 1], fit_lon[k + 1])
        mid_lat = (fit_lat[k - 1] + fit_lat[k + 1]) / 2.0
        mid_lon = (fit_lon[k - 1] + fit_lon[k + 1]) / 2.0
        off = _dist_m(raw_lat[k], raw_lon[k], mid_lat, mid_lon)
        hit = (base <= fit.spike_base_m) & (off >= fit.spike_m)
        fit_lat[k[hit]] = mid_lat[hit]
        fit_lon[k[hit]] = mid_lon[hit]
        method[k[hit]] = SPIKE

    # two-fix excursions
    if n < 4:
        return
    k = np.arange(1, n - 2)
    cand = ((method[k] == RAW) & (method[k + 1] == RAW) & still[k - 1] & still[k + 2]
            & (np.diff(t)[k - 1] > 0) & (np.diff(t)[k] > 0) & (np.diff(t)[k + 1] > 0)
            & (t[k] - t[k - 1] <= fit.spike_max_dt_s)
            & (t[k + 2] - t[k + 1] <= fit.spike_max_dt_s)
            & (t[k + 1] - t[k] <= fit.spike_max_dt_s))
    k = k[cand]
    if len(k):
        base = _dist_m(fit_lat[k - 1], fit_lon[k - 1], fit_lat[k + 2], fit_lon[k + 2])
        mid_lat = (fit_lat[k - 1] + fit_lat[k + 2]) / 2.0
        mid_lon = (fit_lon[k - 1] + fit_lon[k + 2]) / 2.0
        off1 = _dist_m(raw_lat[k], raw_lon[k], mid_lat, mid_lon)
        off2 = _dist_m(raw_lat[k + 1], raw_lon[k + 1], mid_lat, mid_lon)
        hit = (base <= fit.spike_base_m) & (off1 >= fit.spike_m) & (off2 >= fit.spike_m)
        for j in (0, 1):
            kk = k[hit] + j
            fit_lat[kk] = mid_lat[hit]
            fit_lon[kk] = mid_lon[hit]
            method[kk] = SPIKE


# ---------------------------------------------------------------------------
# 3. road snapping
# ---------------------------------------------------------------------------

def _snap_moves(fit_lat, fit_lon, raw_lat, raw_lon, t, still, method,
                snap_m, conf, fit: FitConfig, detector: DetectorConfig,
                osrm: OsrmClient) -> str:
    """`t` is absolute epoch seconds per fix."""
    n = len(t)
    movable = (~still) & (method == RAW)
    if not movable.any():
        return "not_needed"
    if not osrm.enabled:
        return "unreachable"

    cfg = osrm.cfg
    segments: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not movable[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and movable[j + 1] and (t[j + 1] - t[j]) <= detector.max_gap_seconds:
            j += 1
        if j > i:
            segments.append((i, j))
        i = j + 1

    status = "ok"
    for a, b in segments:
        idx = np.arange(a, b + 1)
        try:
            tps = osrm.match(raw_lat[idx], raw_lon[idx], t[idx])
        except OsrmUnavailable as exc:
            logger.debug("OSRM match failed: %s", exc)
            return "error"
        for off, tp in enumerate(tps):
            k = a + off
            if tp is None:
                continue
            snap_m[k] = tp.snap_m
            conf[k] = tp.confidence
            if tp.snap_m <= cfg.max_snap_m and tp.confidence >= cfg.min_confidence:
                fit_lat[k] = tp.lat
                fit_lon[k] = tp.lon
                method[k] = SNAPPED
        # A fix the matcher could not place on any road, well off the line
        # through neighbours it did place, is a spike.
        for off in range(1, len(tps) - 1):
            k = a + off
            if tps[off] is None and method[k - 1] == SNAPPED and method[k + 1] == SNAPPED:
                mid_lat = (fit_lat[k - 1] + fit_lat[k + 1]) / 2.0
                mid_lon = (fit_lon[k - 1] + fit_lon[k + 1]) / 2.0
                if float(_dist_m(raw_lat[k], raw_lon[k], mid_lat, mid_lon)) >= fit.spike_m:
                    fit_lat[k] = mid_lat
                    fit_lon[k] = mid_lon
                    method[k] = SPIKE
    return status


# ---------------------------------------------------------------------------
# stops and gaps
# ---------------------------------------------------------------------------

def _stops(res: FitResult, t: np.ndarray, run_id: np.ndarray, fit: FitConfig) -> list[Stop]:
    """Standstill runs long enough to be called a stop.

    Consecutive runs split only by a hole (no fix between them) at the same
    place are one stop: a tracker that sleeps for an hour while the truck is
    parked has not ended the parking.
    """
    trail = res.trail
    n = trail.used
    lat = np.asarray(trail.lat)
    lon = np.asarray(trail.lon)
    runs: list[list[int]] = []
    i = 0
    while i < n:
        if run_id[i] < 0:
            i += 1
            continue
        j = i
        while j + 1 < n and run_id[j + 1] == run_id[i]:
            j += 1
        runs.append([i, j])
        i = j + 1

    merged: list[list[int]] = []
    for r in runs:
        if merged and r[0] == merged[-1][1] + 1:
            p = merged[-1]
            a_lat, a_lon = np.median(lat[p[0]:p[1] + 1]), np.median(lon[p[0]:p[1] + 1])
            b_lat, b_lon = np.median(lat[r[0]:r[1] + 1]), np.median(lon[r[0]:r[1] + 1])
            if float(_dist_m(a_lat, a_lon, b_lat, b_lon)) <= fit.gap_moved_m / 2:
                p[1] = r[1]
                continue
        merged.append(list(r))

    stops: list[Stop] = []
    for a, b in merged:
        dur = t[b] - t[a]
        if dur < fit.stop_min_seconds or b == a:
            continue
        seq = len(stops) + 1
        clat = float(np.median(lat[a:b + 1]))
        clon = float(np.median(lon[a:b + 1]))
        spread = _dist_m(res.raw_lat[a:b + 1], res.raw_lon[a:b + 1], clat, clon)
        stops.append(Stop(
            seq=seq, start=a, end=b,
            dt_start=trail.ts[a], dt_end=trail.ts[b], duration_s=int(dur),
            pings=b - a + 1, lat=clat, lon=clon,
            p90_spread_m=float(np.percentile(spread, 90)),
            max_gap_s=int(np.max(np.diff(t[a:b + 1]))) if b > a else 0,
            spikes=sum(1 for m in res.method[a:b + 1] if m == SPIKE),
        ))
        res.stop_of[a:b + 1] = seq
    return stops


def _gaps(res: FitResult, t: np.ndarray, fit: FitConfig) -> list[Gap]:
    trail = res.trail
    n = trail.used
    if n < 2:
        return []
    dt = np.diff(t)
    idx = np.flatnonzero(dt >= fit.gap_min_seconds) + 1
    gaps: list[Gap] = []
    for k in idx:
        a_lat, a_lon = trail.lat[k - 1], trail.lon[k - 1]
        b_lat, b_lon = trail.lat[k], trail.lon[k]
        straight = float(_haversine_m(a_lat, a_lon, b_lat, b_lon))
        gaps.append(Gap(
            index=int(k), dt_from=trail.ts[k - 1], dt_to=trail.ts[k],
            gap_s=int(dt[k - 1]), from_lat=a_lat, from_lon=a_lon, to_lat=b_lat, to_lon=b_lon,
            straight_m=straight,
            kind="moving" if straight >= fit.gap_moved_m else "stationary",
        ))
    return gaps


def _route_gaps(gaps: list[Gap], osrm: OsrmClient) -> str:
    seen: list[str] = []
    for g in gaps:
        if g.kind != "moving":
            continue
        if not osrm.enabled:
            g.route_status = "osrm_off"
            seen.append("unreachable")
            continue
        try:
            g.route = osrm.route(g.from_lat, g.from_lon, g.to_lat, g.to_lon)
            g.route_status = "ok" if g.route is not None else "no_route"
            seen.append("ok")
        except OsrmUnavailable:
            g.route_status = "osrm_error"
            seen.append("error")
    return _combine(seen)


def _combine(statuses: list[str]) -> str:
    """Roll per-stage OSRM outcomes into one word for the trip.

    `partial` is its own verdict: some fixes were matched and some were not,
    which a reader must not confuse with either extreme.
    """
    s = set(statuses)
    failed = s & {"error", "unreachable"}
    if failed and "ok" in s:
        return "partial"
    if "unreachable" in s:
        return "unreachable"
    if "error" in s:
        return "error"
    if "ok" in s:
        return "ok"
    return "not_needed"


def _quality(res: FitResult) -> tuple[str, str]:
    """One-word trustworthiness verdict, with the reason.

    Holes count against a trail only when the truck moved across them. The
    corpus's long holes are overwhelmingly a tracker asleep at a standstill --
    99.5% of 1-4 hour holes begin and end within 500 m of each other -- and
    calling those trails "broken" overstated how much evidence was missing.
    """
    t = res.trail
    if t.used == 0:
        return "no_gps", "no admissible fixes"
    if t.used < 5:
        return "sparse", f"only {t.used} admissible fixes"
    moving = [g for g in res.gaps if g.kind == "moving"]
    worst = max((g.gap_s for g in moving), default=0)
    if worst >= 3600:
        return "broken", f"moved {max(g.straight_m for g in moving if g.gap_s == worst)/1000:.1f} km during a {worst/3600:.1f} h hole"
    bad = t.total_dropped + res.spikes
    if t.read and bad / t.read > 0.10:
        return "noisy", f"{100 * bad / t.read:.0f}% of fixes refused or spikes"
    if worst >= 900:
        return "sparse", f"moved during a {worst/60:.0f} min hole"
    return "good", ""
