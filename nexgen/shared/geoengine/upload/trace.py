"""The pipeline, instrumented: every decision recorded with the numbers behind it.

This runs the *same* code a batch run runs -- `engine.filters.clean`,
`prep.fit.fit_trail`, `index.FenceIndex`, `engine.state.detect`,
`engine.evaluator.evaluate_trail` -- and then re-derives, stage by stage, the
intermediate quantities each of them decided on. Nothing here re-implements a
rule. If it did, the page would be explaining a second engine that happens to
resemble the first, which is worse than no explanation at all: the numbers
would look verified and would not be.

Instrumentation that re-runs, rather than instrumentation that hooks in, is
deliberate. Threading a recorder through the hot loop would slow every batch
run for the benefit of the handful of trails anyone ever inspects, and would
put branches in the code whose correctness the whole system rests on. A
re-derivation costs one extra pass over one trail and cannot affect the
engine's answer -- which also means it can be checked *against* that answer,
and `verify.py` does exactly that.

The output is a list of artifacts. Each is one step of the pipeline as a
self-contained document -- what it does, why, the formula, the numbers it
produced on this trail, a worked example taken from this trail's own data, and
the rows -- which is what the page renders and what the download endpoints
serve. Show and download are therefore the same bytes.
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime

import numpy as np

from nexgen.shared.geoengine.config import DetectorConfig, FitConfig
from nexgen.shared.geoengine.engine import state as st
from nexgen.shared.geoengine.engine.evaluator import DEFAULT_CHUNK, evaluate_trail
from nexgen.shared.geoengine.engine.filters import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN, clean
from nexgen.shared.geoengine.geometry import haversine_m, haversine_m_vec
from nexgen.shared.geoengine.index import FenceIndex
from nexgen.shared.geoengine.model import FAR_M
from nexgen.shared.geoengine.prep.fit import MEDIAN, RAW, SNAPPED, SPIKE, fit_trail
from nexgen.shared.geoengine.store import scale_of

logger = logging.getLogger(__name__)

FACILITY = ("micro", "site", "campus")

# Rows kept per artifact. The whole trail is kept for the per-fix tables --
# they are the audit trail and truncating them would defeat the point -- but
# the exhaustive candidate and distance tables are capped, since past a few
# thousand rows they are read by machine, not by eye.
MAX_DETAIL = 20_000


def _iso(v):
    return v.isoformat(sep=" ") if isinstance(v, datetime) else v


def _r(v, n=3):
    """Round for transport; None and NaN stay themselves, so 'unknown' never
    arrives at the page as 0."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) or math.isinf(f) else round(f, n)


class Artifact(dict):
    """One step, as the page renders it and the downloads serve it."""

    def __init__(self, key: str, title: str, *, what: str, why: str = "", how: str = "",
                 stats: dict | None = None, columns: list | None = None,
                 rows: list | None = None, example: dict | None = None,
                 extra: dict | None = None, note: str = ""):
        super().__init__(
            key=key, title=title, what=what, why=why, how=how, note=note,
            stats=stats or {}, columns=columns or [], rows=rows or [],
            example=example, extra=extra or {},
        )


class Tracer:
    """Runs one trail through the pipeline and records every step."""

    def __init__(self, pings: list[dict], index: FenceIndex,
                 detector: DetectorConfig, fit: FitConfig, osrm=None,
                 chunk: int = DEFAULT_CHUNK):
        self.pings = pings
        self.index = index
        self.det = detector
        self.fit_cfg = fit
        self.osrm = osrm
        self.chunk = chunk
        self.artifacts: list[Artifact] = []
        self.timings: dict[str, float] = {}

    # -- plumbing -----------------------------------------------------------

    def _add(self, art: Artifact) -> Artifact:
        self.artifacts.append(art)
        return art

    def _timed(self, name: str, fn):
        t0 = time.perf_counter()
        out = fn()
        self.timings[name] = round(time.perf_counter() - t0, 4)
        return out

    # -- the run ------------------------------------------------------------

    def run(self) -> dict:
        rows = [{"id": p["seq"], "dt_message": p["ts"], "d_lat": p["lat"],
                 "d_long": p["lon"], "i_speed": p["speed"]} for p in self.pings]

        trail = self._timed("clean", lambda: clean(
            rows, self.det.max_plausible_kmph, self.det.max_gap_seconds))
        self._step_admit(rows, trail)
        self._step_teleport(rows, trail)

        fit = self._timed("fit", lambda: fit_trail(trail, self.fit_cfg, self.det, self.osrm))
        self._step_standstill(fit)
        self._step_median(fit)
        self._step_spikes(fit)
        self._step_snap(fit)
        self._step_stops(fit)
        self._step_gaps(fit)
        self._step_trail(fit)

        self._step_index()
        cand = self._step_candidates(fit)
        self._step_prune(fit, cand)
        self._step_distance(fit, cand)

        res = self._timed("detect", lambda: evaluate_trail(
            fit.trail, self.index, self.det, chunk=self.chunk))
        self._step_band(res)
        self._step_state(fit, res)
        self._step_visits(res)
        self._step_nesting(res)
        places = self._step_places(res)
        self._step_events(res)
        self._step_violations(res)
        self._step_kpis(fit, res, places)

        return {"trail": trail, "fit": fit, "result": res, "places": places,
                "candidates": cand, "artifacts": self.artifacts, "timings": self.timings}

    # ======================================================================
    # Stage 1 -- admissibility
    # ======================================================================

    def _step_admit(self, rows, trail):
        """Structural validity, ordering, duplicates."""
        d = trail.dropped
        n = len(rows)
        bad: list[list] = []
        for r in rows:
            lat, lon = r["d_lat"], r["d_long"]
            reason = None
            if r["dt_message"] is None or lat is None or lon is None:
                reason = "null_fix"
            elif lat == 0.0 and lon == 0.0:
                reason = "null_fix (0,0 — the receiver had no lock)"
            elif not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
                reason = "out_of_range (outside India)"
            if reason:
                bad.append([r["id"] + 1, _iso(r["dt_message"]), _r(lat, 6), _r(lon, 6), reason])

        ts = [r["dt_message"] for r in rows if r["dt_message"] is not None]
        out_of_order = sum(1 for a, b in zip(ts, ts[1:]) if b < a)

        self._add(Artifact(
            "admit", "Step 1 · Is each row admissible at all?",
            what="Three checks that need no context beyond the row itself: does it "
                 "carry a stamp and a pair of coordinates; is (0, 0) — which a "
                 "receiver with no satellite lock reports — being read as a position "
                 "off the coast of Africa; and does the point fall inside India's "
                 f"bounding box ({LAT_MIN}–{LAT_MAX}°N, {LON_MIN}–{LON_MAX}°E).",
            why="These are the only failures that can be judged without looking at "
                "the fix's neighbours, so they are settled first and the rest of the "
                "pipeline never has to defend itself against a NULL or a point in "
                "the Atlantic. The India box is deliberately tighter here than the "
                "one the geofence loader uses: a *fence* may legitimately sit "
                "offshore — the master has sanity fences in the Arabian Sea — but a "
                "*truck* reported there has a broken receiver.",
            how="lat, lon present and not (0, 0)  and  "
                f"{LAT_MIN} ≤ lat ≤ {LAT_MAX}  and  {LON_MIN} ≤ lon ≤ {LON_MAX}",
            stats={
                "rows in": n,
                "refused: no usable fix": d.get("null_fix", 0),
                "refused: outside India": d.get("out_of_range", 0),
                "arrived out of order": out_of_order,
                "duplicate timestamps dropped": d.get("duplicate", 0),
                "surviving this step": n - d.get("null_fix", 0) - d.get("out_of_range", 0)
                                         - d.get("duplicate", 0),
            },
            note=("Out-of-order arrivals are repaired, not penalised — the fix is good, "
                  "it merely arrived late — but they are counted, because a device that "
                  "replays bursts out of order is worth knowing about. Sorting is "
                  "stable, so among fixes sharing one second the first to arrive is the "
                  "one kept and the rest are counted as duplicates."),
            columns=["row", "timestamp", "lat", "lon", "why refused"],
            rows=bad[:MAX_DETAIL],
        ))

    def _step_teleport(self, rows, trail):
        """Single-outlier rejection, re-derived fix by fix with its arithmetic."""
        staged = [(r["dt_message"], r["d_lat"], r["d_long"], r["i_speed"], r["id"])
                  for r in rows
                  if r["dt_message"] is not None and r["d_lat"] is not None
                  and r["d_long"] is not None
                  and not (r["d_lat"] == 0.0 and r["d_long"] == 0.0)
                  and LAT_MIN <= r["d_lat"] <= LAT_MAX and LON_MIN <= r["d_long"] <= LON_MAX]
        staged.sort(key=lambda x: x[0])
        deduped = []
        for rec in staged:
            if deduped and rec[0] == deduped[-1][0]:
                continue
            deduped.append(rec)

        limit = self.det.max_plausible_kmph
        max_gap = self.det.max_gap_seconds
        n = len(deduped)
        detail: list[list] = []
        anchor = None
        example = None

        for i, rec in enumerate(deduped):
            ts, lat, lon = rec[0], rec[1], rec[2]
            if anchor is None:
                anchor = rec
                continue
            dt_s = (ts - anchor[0]).total_seconds()
            if dt_s <= 0 or dt_s > max_gap:
                anchor = rec
                continue
            gap_m = haversine_m(anchor[1], anchor[2], lat, lon)
            implied = gap_m / dt_s * 3.6
            if implied <= limit:
                anchor = rec
                continue

            # Suspect. The look-ahead decides.
            fwd = from_anchor = None
            if i + 1 < n:
                nxt = deduped[i + 1]
                fwd_s = (nxt[0] - ts).total_seconds()
                anc_s = (nxt[0] - anchor[0]).total_seconds()
                if fwd_s > 0 and anc_s > 0:
                    fwd = haversine_m(lat, lon, nxt[1], nxt[2]) / fwd_s * 3.6
                    from_anchor = haversine_m(anchor[1], anchor[2], nxt[1], nxt[2]) / anc_s * 3.6
            keep = (fwd is not None and from_anchor is not None
                    and fwd <= limit and from_anchor > limit)
            row = [rec[4] + 1, _iso(ts), _r(lat, 6), _r(lon, 6), _iso(anchor[0]),
                   _r(gap_m / 1000, 2), int(dt_s), _r(implied, 1), _r(fwd, 1),
                   _r(from_anchor, 1), "kept — the trail really moved" if keep else "dropped as a spike"]
            detail.append(row)
            if example is None:
                example = {
                    "fix": _iso(ts), "lat": _r(lat, 6), "lon": _r(lon, 6),
                    "anchor": _iso(anchor[0]), "anchor_lat": _r(anchor[1], 6),
                    "anchor_lon": _r(anchor[2], 6),
                    "steps": [
                        f"Distance from the last accepted fix: {gap_m/1000:,.2f} km",
                        f"Time since it: {dt_s:,.0f} s",
                        f"Implied speed: {gap_m/1000:,.2f} km ÷ {dt_s:,.0f} s × 3600 = "
                        f"{implied:,.0f} km/h, over the {limit:,.0f} km/h limit → suspect",
                        (f"Look ahead — the next fix is {fwd:,.0f} km/h from the suspect and "
                         f"{from_anchor:,.0f} km/h from the anchor"
                         if fwd is not None else "No next fix to look ahead to"),
                        ("The successor agrees with the suspect and not with the anchor: "
                         "the anchor was the stale one, so the suspect is kept and becomes "
                         "the new anchor." if keep else
                         "Nothing agrees with the suspect: it is a lone spike and is dropped. "
                         "The anchor is left where it was."),
                    ],
                }
            if keep:
                anchor = rec

        self._add(Artifact(
            "teleport", "Step 2 · Teleports: the single-outlier gate",
            what="A fix hundreds of kilometres off the trail — a bad almanac, or a "
                 "cell-tower fallback position — is removed. Each fix is measured "
                 "against the last *accepted* one, and a fix implying more than "
                 f"{limit:,.0f} km/h is suspect.",
            why="One of these dropped inside a geofence produces a phantom visit that "
                "no downstream logic can tell from a real one, so it has to die here. "
                "But the naive rule — 'implied speed too high, drop it' — fails "
                "catastrophically when the *anchor* is the outlier: every good fix "
                "after it looks like a 300 km jump, and the whole remainder of the "
                "trail is thrown away. So before rejecting, the rule looks one fix "
                "ahead. If the successor sits with the suspect and not with the "
                "anchor, the truck genuinely moved and the anchor was stale.",
            how="implied = haversine(anchor, fix) ÷ Δt × 3.6\n"
                f"suspect if implied > {limit:,.0f} km/h\n"
                "keep it anyway if  speed(fix → next) ≤ limit  and  speed(anchor → next) > limit",
            stats={
                "fixes tested": max(0, n - 1),
                "flagged as suspect": len(detail),
                "kept by the look-ahead": sum(1 for r in detail if r[-1].startswith("kept")),
                "dropped as teleports": trail.dropped.get("teleport", 0),
                "speed limit (km/h)": limit,
                "outage threshold (s)": max_gap,
            },
            note=(f"Across a hole longer than {max_gap:,.0f} s the truck could plausibly be "
                  "anywhere, so the speed test carries no information and is not applied. "
                  "The hole is recorded as uncertainty instead — which is the honest "
                  "handling, and the reason a long outage never silently 'confirms' "
                  "anything later in the pipeline."),
            columns=["row", "timestamp", "lat", "lon", "anchor at", "km from anchor",
                     "Δt (s)", "implied km/h", "km/h fix→next", "km/h anchor→next", "verdict"],
            rows=detail[:MAX_DETAIL],
            example=example,
        ))

        quality = trail.quality
        self._add(Artifact(
            "admitted", "Step 3 · What survived, and how much to trust it",
            what="The trail the rest of the pipeline is allowed to reason about: "
                 "sorted, de-duplicated, physically plausible — and an explicit "
                 "verdict on how complete it is.",
            why="A clean answer computed from a broken trail is the most dangerous "
                 "output this system can produce. A trail with an hour-long hole can "
                 "hide an entire visit, so 'never visited' from such a trail is "
                 "*unsupported*, not negative. The verdict travels with every number "
                 "derived from the trail so no report has to infer trustworthiness.",
            how="no_gps: nothing usable · sparse: under 5 fixes, or a 15 min+ hole · "
                "broken: an hour-long hole · noisy: over 10% refused · good: none of these",
            stats={
                "fixes read": trail.read,
                "fixes usable": trail.used,
                "fixes refused": trail.total_dropped,
                "refused %": _r(100.0 * trail.total_dropped / trail.read, 2) if trail.read else 0,
                "re-ordered": trail.resequenced,
                "largest hole (s)": trail.max_gap_seconds,
                "outages over the gap limit": trail.breaks,
                "verdict": quality,
            },
            columns=["reason", "fixes"],
            rows=[[k.replace("_", " "), v] for k, v in sorted(
                trail.dropped.items(), key=lambda kv: -kv[1]) if v],
        ))

    # ======================================================================
    # Stage 2 -- fit
    # ======================================================================

    def _step_standstill(self, fit):
        still = fit.still
        n = len(still)
        runs = _runs(still)
        long_runs = [(a, b) for a, b in runs if b > a]
        durations = [(fit.trail.ts[b] - fit.trail.ts[a]).total_seconds() for a, b in long_runs]
        self._add(Artifact(
            "standstill", "Step 4 · Which fixes are the truck standing still",
            what=f"A fix whose reported speed is at or below {self.fit_cfg.still_kmph:g} km/h "
                 "is a standstill. Consecutive standstill fixes form a run.",
            why="Three quarters of the fixes in this fleet's corpus are at zero speed, "
                 "and a stationary consumer receiver still reports motion — tens of "
                 "metres of wander, worse under gantries and beside sheds, which is "
                 "exactly where trucks park inside a works. Standstills therefore get "
                 "different treatment from moving fixes at every later step: they are "
                 "the ones worth averaging, they are never snapped to a road, and they "
                 "are what a stop is made of. Separating them is the precondition for "
                 "all of that.",
            how=f"still = reported speed ≤ {self.fit_cfg.still_kmph:g} km/h\n"
                "a run breaks at a moving fix, or at a hole longer than "
                f"{self.det.max_gap_seconds:,.0f} s",
            stats={
                "fixes": n,
                "standing still": int(still.sum()),
                "standstill %": _r(100.0 * still.sum() / n, 1) if n else 0,
                "moving": int((~still).sum()),
                "standstill runs": len(runs),
                "longest standstill (s)": int(max(durations)) if durations else 0,
                "no speed reported": int(sum(1 for s in fit.trail.speed if s is None)),
            },
            columns=["run", "from", "to", "fixes", "seconds"],
            rows=[[i + 1, _iso(fit.trail.ts[a]), _iso(fit.trail.ts[b]), b - a + 1,
                   int((fit.trail.ts[b] - fit.trail.ts[a]).total_seconds())]
                  for i, (a, b) in enumerate(long_runs)][:MAX_DETAIL],
        ))

    def _step_median(self, fit):
        method = np.asarray(fit.method, dtype=object)
        used = method == MEDIAN
        shifts = fit.shift_m[used] if used.any() else np.zeros(0)
        idx = np.flatnonzero(used)
        order = idx[np.argsort(-fit.shift_m[idx])] if len(idx) else []
        example = None
        if len(order):
            k = int(order[0])
            half = self.fit_cfg.window_fixes
            lo, hi = max(0, k - half), min(len(fit.raw_lat) - 1, k + half)
            example = {
                "fix": _iso(fit.trail.ts[k]),
                "raw": [_r(fit.raw_lat[k], 6), _r(fit.raw_lon[k], 6)],
                "fitted": [_r(fit.trail.lat[k], 6), _r(fit.trail.lon[k], 6)],
                "window": [[_iso(fit.trail.ts[j]), _r(fit.raw_lat[j], 6), _r(fit.raw_lon[j], 6)]
                           for j in range(lo, hi + 1)],
                "steps": [
                    f"The window is this fix and up to {half} neighbours each side "
                    f"({hi - lo + 1} fixes here), all inside the same standstill.",
                    "Take the median latitude and the median longitude of the window, "
                    "separately.",
                    f"Median: {fit.trail.lat[k]:.6f}, {fit.trail.lon[k]:.6f}",
                    f"That is {fit.shift_m[k]:,.0f} m from where the receiver put this fix, "
                    "so the receiver's scatter is what moved — not the truck.",
                ],
            }
        self._add(Artifact(
            "median", "Step 5 · Standstill scatter: a rolling median",
            what="Over a standstill, each fix is replaced by the coordinate-wise median "
                 f"of up to {self.fit_cfg.window_fixes} neighbours either side. The "
                 "timestamp is never touched — every fitted position is still stamped "
                 "with an instant a fix was really observed at.",
            why="A median removes impulses and preserves steps. Up to three consecutive "
                "bad fixes vanish, while a genuine relocation of four or more — "
                "weighbridge to parking bay — keeps both its position and its timing. "
                "That is why this is a median filter and not one centroid per stop: a "
                "centroid would erase exactly the short in-plant moves that micro-scale "
                "fences exist to catch. A window whose own spread exceeds "
                f"{self.fit_cfg.still_spread_m:g} m is not really standing still (a stuck "
                "speed sensor, a slow crawl) and is left raw.",
            how=f"fitted = ( median(lat over window), median(lon over window) )\n"
                f"window: ±{self.fit_cfg.window_fixes} fixes, same standstill run, within "
                f"{self.fit_cfg.window_seconds:,.0f} s\n"
                f"applied only when the window has ≥ 3 fixes and its spread ≤ "
                f"{self.fit_cfg.still_spread_m:g} m",
            stats={
                "fixes median-filtered": int(used.sum()),
                "left raw": int((method == RAW).sum()),
                "median move (m)": _r(float(np.median(shifts)), 1) if len(shifts) else 0,
                "90th percentile move (m)": _r(float(np.percentile(shifts, 90)), 1) if len(shifts) else 0,
                "largest move (m)": _r(float(shifts.max()), 1) if len(shifts) else 0,
            },
            columns=["fix", "timestamp", "raw lat", "raw lon", "fitted lat", "fitted lon",
                     "moved (m)"],
            rows=[[int(k) + 1, _iso(fit.trail.ts[k]), _r(fit.raw_lat[k], 6), _r(fit.raw_lon[k], 6),
                   _r(fit.trail.lat[k], 6), _r(fit.trail.lon[k], 6), _r(fit.shift_m[k], 1)]
                  for k in order[:MAX_DETAIL]],
            example=example,
        ))

    def _step_spikes(self, fit):
        method = np.asarray(fit.method, dtype=object)
        idx = np.flatnonzero(method == SPIKE)
        rows = []
        example = None
        for k in idx[:MAX_DETAIL]:
            k = int(k)
            prev = max(0, k - 1)
            nxt = min(len(fit.raw_lat) - 1, k + 1)
            base = haversine_m(fit.trail.lat[prev], fit.trail.lon[prev],
                               fit.trail.lat[nxt], fit.trail.lon[nxt])
            rows.append([k + 1, _iso(fit.trail.ts[k]), _r(fit.raw_lat[k], 6),
                         _r(fit.raw_lon[k], 6), _r(fit.trail.lat[k], 6), _r(fit.trail.lon[k], 6),
                         _r(fit.shift_m[k], 1), _r(base, 1)])
            if example is None:
                example = {
                    "fix": _iso(fit.trail.ts[k]),
                    "steps": [
                        f"The fix before and the fix after are {base:,.0f} m apart — within "
                        f"the {self.fit_cfg.spike_base_m:g} m that counts as 'the same place', "
                        "and both are standing still.",
                        f"This fix sits {fit.shift_m[k]:,.0f} m from the midpoint between "
                        f"them, past the {self.fit_cfg.spike_m:g} m threshold.",
                        "A loaded truck cannot drive that far out and park back within "
                        f"{self.fit_cfg.spike_base_m:g} m of where it started inside "
                        f"{self.fit_cfg.spike_max_dt_s:g} s. A multipath reflection does it "
                        "routinely.",
                        "So the fix is moved to the midpoint of its neighbours.",
                    ],
                }
        self._add(Artifact(
            "spikes", "Step 6 · Out-and-back spikes",
            what="A stationary truck 'jumps' 100 m to 2 km for one or two fixes and "
                 "lands back within a few metres of where it started. Those fixes are "
                 "moved to the midpoint of their neighbours.",
            why="The jump is slow enough — a median implied speed around 48 km/h — that "
                "no speed filter can reject it, and the median window does not always "
                "own it. Next to a fence line one of these confirms a false exit and a "
                "false re-entry: two events, a broken dwell time, and an alert nobody "
                "can act on. The corpus carries about 5,500 of them.",
            how=f"both neighbours standing still and within {self.fit_cfg.spike_base_m:g} m "
                f"of each other\nboth at most {self.fit_cfg.spike_max_dt_s:g} s away\n"
                f"the fix itself ≥ {self.fit_cfg.spike_m:g} m off the midpoint between them",
            stats={
                "spikes corrected": len(idx),
                "one-fix excursions": int(len(idx) - sum(
                    1 for k in idx if k - 1 in set(idx.tolist()))),
                "largest correction (m)": _r(float(fit.shift_m[idx].max()), 1) if len(idx) else 0,
            },
            columns=["fix", "timestamp", "raw lat", "raw lon", "fitted lat", "fitted lon",
                     "moved (m)", "neighbours apart (m)"],
            rows=rows,
            example=example,
        ))

    def _step_snap(self, fit):
        method = np.asarray(fit.method, dtype=object)
        snapped = int((method == SNAPPED).sum())
        snaps = [s for s in fit.snap_m if s is not None]
        on = self.osrm is not None and getattr(self.osrm, "enabled", False)
        self._add(Artifact(
            "snap", "Step 7 · Road snapping (OSRM)",
            what="Moving fixes are matched to the road network and replaced by their "
                 "position on it — but only when the correction is short "
                 f"(≤ {getattr(self.osrm, 'cfg', None) and self.osrm.cfg.max_snap_m or 30:g} m).",
            why="A hidden-Markov map matcher knows the road exists; a GPS receiver does "
                "not. On open highway this removes the lateral wander that makes "
                "trajectories measurably longer than the roads they ran on. "
                "Standstills are *never* sent: yards and weighbridges are off the "
                "network, and matching would drag a parked truck onto the nearest "
                "road and straight out of the fence it is sitting in.",
            how="OSRM /match (Newson & Krumm HMM) over runs of consecutive moving "
                "fixes; a fix the matcher cannot place, lying well off the line "
                "through neighbours it did place, is treated as a spike.",
            stats={
                "OSRM": fit.osrm_status,
                "fixes snapped": snapped,
                "median snap (m)": _r(float(np.median(snaps)), 1) if snaps else None,
                "largest snap (m)": _r(float(np.max(snaps)), 1) if snaps else None,
            },
            note=("OSRM is optional and is not configured here, so this step did nothing "
                  "and every moving fix kept its raw position. Nothing else in the "
                  "pipeline changes: the detector is given positions, not rules."
                  if not on else
                  "Snapping improves positions on the open road. It cannot create or "
                  "destroy a fence crossing on its own — the detector's thresholds are "
                  "all larger than the snap ceiling."),
            columns=["fix", "timestamp", "snapped (m)", "confidence"],
            rows=[[i + 1, _iso(fit.trail.ts[i]), _r(fit.snap_m[i], 1), _r(fit.confidence[i], 3)]
                  for i in range(fit.trail.used) if fit.snap_m[i] is not None][:MAX_DETAIL],
        ))

    def _step_stops(self, fit):
        self._add(Artifact(
            "stops", "Step 8 · Stops",
            what=f"A standstill lasting {self.fit_cfg.stop_min_seconds/60:g} minutes or "
                 "more is reported as a stop, at the median of its fixes.",
            why="A stop is the physical event a geofence visit is usually made of — "
                 "loading, unloading, a queue at a gate, a break at a dhaba. Finding "
                 "them independently of the fences is what lets the report say 'the "
                 "truck stopped here for four hours and no fence covers it', which is "
                 "either a missing geofence in the master or an unauthorised halt. "
                 "Two standstills split only by a sleeping tracker, at the same place, "
                 "are one stop: the parking did not end because the device did.",
            how=f"a standstill run ≥ {self.fit_cfg.stop_min_seconds:g} s\n"
                "position = median of the run's fitted fixes\n"
                "scatter = the 90th percentile of the raw fixes' distance from it",
            stats={
                "stops": len(fit.stops),
                "total stopped (s)": sum(s.duration_s for s in fit.stops),
                "longest (s)": max((s.duration_s for s in fit.stops), default=0),
                "median scatter (m)": _r(float(np.median([s.p90_spread_m for s in fit.stops])), 1)
                                      if fit.stops else None,
            },
            columns=["stop", "from", "to", "seconds", "fixes", "lat", "lon",
                     "GPS scatter p90 (m)", "spikes inside"],
            rows=[[s.seq, _iso(s.dt_start), _iso(s.dt_end), s.duration_s, s.pings,
                   _r(s.lat, 6), _r(s.lon, 6), _r(s.p90_spread_m, 1), s.spikes]
                  for s in fit.stops],
        ))

    def _step_gaps(self, fit):
        moving = [g for g in fit.gaps if g.kind == "moving"]
        self._add(Artifact(
            "gaps", "Step 9 · Holes in the trail",
            what=f"A hole of {self.fit_cfg.gap_min_seconds/60:g} minutes or more is "
                 "recorded, and classified by whether the truck moved across it.",
            why="A gap is not an error and is never dropped — it is *missing evidence*, "
                 "and the difference matters. A hole the truck did not move across is a "
                 "sleeping tracker at a standstill and costs almost nothing. A hole it "
                 f"moved more than {self.fit_cfg.gap_moved_m:g} m across is where visits "
                 "can hide: the truck could have entered a fence and left again entirely "
                 "inside it, and no amount of cleverness recovers that. So it is "
                 "reported as unobserved rather than assumed empty.",
            how=f"gap ≥ {self.fit_cfg.gap_min_seconds:g} s\n"
                f"moving  if the straight-line distance across it ≥ {self.fit_cfg.gap_moved_m:g} m, "
                "else stationary\n"
                "with OSRM, a moving hole gets its most probable road path, and any fence "
                "that path crosses becomes an *inferred* passage — never counted as a visit",
            stats={
                "holes": len(fit.gaps),
                "the truck moved across": len(moving),
                "longest hole (s)": max((g.gap_s for g in fit.gaps), default=0),
                "unobserved time (s)": sum(g.gap_s for g in moving),
                "unobserved distance (km)": _r(sum(g.straight_m for g in moving) / 1000, 2),
            },
            columns=["from", "to", "seconds", "kind", "straight-line (m)", "road path",
                     "road (m)", "unexplained (s)"],
            rows=[[_iso(g.dt_from), _iso(g.dt_to), g.gap_s, g.kind, _r(g.straight_m, 1),
                   g.route_status, _r(g.route.distance_m, 1) if g.route else None,
                   g.unexplained_s] for g in fit.gaps],
        ))

    def _step_trail(self, fit):
        """The per-fix audit table: raw in, fitted out, and what moved it."""
        t = fit.trail
        rows = []
        for i in range(t.used):
            rows.append([
                i + 1, _iso(t.ts[i]), _r(fit.raw_lat[i], 6), _r(fit.raw_lon[i], 6),
                t.speed[i], "still" if fit.still[i] else "move",
                _r(t.lat[i], 6), _r(t.lon[i], 6), fit.method[i], _r(fit.shift_m[i], 1),
                int(fit.stop_of[i]) if fit.stop_of[i] >= 0 else None,
            ])
        moved = fit.shift_m[fit.shift_m > 0]
        self._add(Artifact(
            "fitted", "Step 10 · The trail the fences are decided on",
            what="Every admissible fix, with the position the receiver reported, the "
                 "position the fit settled on, and which rule moved it.",
            why="Raw fixes are never overwritten. Keeping both, per fix, is what makes "
                "every visit downstream auditable: any stay in any fence can be traced "
                "to the exact positions it rests on, and re-derived when a setting "
                "changes. A pipeline that only kept its output would be asking to be "
                "believed.",
            how="method — raw: untouched · median: standstill scatter averaged out · "
                "spike: out-and-back corrected · snapped: matched to the road",
            stats={
                "fixes": t.used,
                "untouched": sum(1 for m in fit.method if m == RAW),
                "median-filtered": fit.medians,
                "spikes corrected": fit.spikes,
                "road-snapped": fit.snapped,
                "mean move (m)": _r(float(moved.mean()), 2) if len(moved) else 0,
                "path length (km)": _r(fit.distance_m / 1000, 3),
                "quality": fit.quality,
                "quality reason": fit.quality_reason or "—",
            },
            columns=["fix", "timestamp", "raw lat", "raw lon", "speed", "role",
                     "fitted lat", "fitted lon", "method", "moved (m)", "stop"],
            rows=rows,
        ))

    # ======================================================================
    # Stage 3 -- the index
    # ======================================================================

    def _step_index(self):
        idx = self.index
        s = dict(idx.stats)
        per_level = []
        for lvl in range(idx.levels):
            boxes = idx.node_boxes(lvl)
            if not len(boxes):
                continue
            area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            per_level.append([
                lvl, "leaves (one per fence)" if lvl == 0 else
                     "root" if lvl == idx.levels - 1 else f"internal level {lvl}",
                len(boxes), _r(float(np.median(area)), 8), _r(float(area.max()), 6),
            ])
        self._add(Artifact(
            "index", "Step 11 · The spatial index: a packed Hilbert R-tree",
            what=f"{s.get('fences', 0):,} fences, bulk-loaded into a balanced tree of "
                 f"{s.get('levels', 0)} levels and {s.get('nodes', 0):,} nodes, "
                 f"{s.get('node_size', 16)} children per node. Built once and reused for "
                 "every trail.",
            why="The naive form of this problem is 'for every fix, for every fence, is "
                "the fix inside?'. On the full corpus that is 5.1 million × 4,937 ≈ 25 "
                "billion polygon tests — eight hours at a microsecond each.\n\n"
                "A uniform grid is the obvious alternative and it does not work here. "
                "The smallest fence is a 40 m weighbridge square and the largest spans "
                "an industrial belt; the area ratio between them is over five orders of "
                f"magnitude (measured on this master: {s.get('bbox_area_ratio', 0):,.0f}×). "
                "Size the cells for the small fences and the large ones register into "
                "tens of thousands of cells; size them for the large ones and hundreds "
                "of small fences share a bucket and the index degenerates into a linear "
                "scan. The sites are also heavily clustered — Jamshedpur, Kalinganagar "
                "and Angul hold a large share — so a grid is mostly empty cells with a "
                "few very deep ones. An R-tree adapts its node extents to the data and "
                "cares about neither the size spread nor the clustering.\n\n"
                "The fence set is static between master imports, so there is no reason "
                "to pay for an insertable tree: it is bulk-loaded bottom-up, fully "
                "balanced, into flat numpy arrays with no Python object per node.",
            how="Guttman (SIGMOD 1984) for the R-tree; Kamel & Faloutsos (VLDB 1993) "
                "for the Hilbert packing; the flat-array formulation follows Flatbush.",
            stats={
                "fences indexed": s.get("fences", 0),
                "levels": s.get("levels", 0),
                "nodes": s.get("nodes", 0),
                "children per node": s.get("node_size", 16),
                "data extent (°)": s.get("extent_deg"),
                "median fence box (deg²)": _r(s.get("median_bbox_deg2"), 9),
                "largest ÷ smallest box": _r(s.get("bbox_area_ratio"), 0),
                "build time (s)": s.get("build_seconds"),
            },
            columns=["level", "what", "nodes", "median box area (deg²)", "largest box (deg²)"],
            rows=per_level,
        ))

        # -- the Hilbert ordering itself
        order = idx.leaf_order()
        fences = idx.fences
        curve = [[_r(float(fences[i].centroid_lon), 5), _r(float(fences[i].centroid_lat), 5)]
                 for i in order]

        # How local is the ordering, measured rather than asserted: the
        # distance between fences that end up as neighbours under each sort.
        # That distance is what a parent node's box has to span, so it is the
        # quantity the packing is actually trying to minimise.
        lat = np.array([f.centroid_lat for f in fences], dtype=np.float64)
        lon = np.array([f.centroid_lon for f in fences], dtype=np.float64)

        def neighbour_km(seq: np.ndarray) -> np.ndarray:
            a, b = seq[:-1], seq[1:]
            return haversine_m_vec(lat[a], lon[a], lat[b], lon[b]) / 1000.0

        locality = []
        if len(order) > 1:
            for name, seq in (
                ("Hilbert curve (what the index uses)", order),
                ("by longitude", np.argsort(lon, kind="stable")),
                ("by latitude", np.argsort(lat, kind="stable")),
                ("as the master lists them", np.arange(len(fences))),
            ):
                d = neighbour_km(np.asarray(seq))
                locality.append([
                    name, _r(float(np.median(d)), 2), _r(float(np.percentile(d, 90)), 1),
                    _r(float(d.mean()), 1), int((d <= 1.0).sum()),
                    _r(100.0 * float((d <= 1.0).mean()), 1),
                ])
        hilbert_med = locality[0][1] if locality else None
        lon_med = locality[1][1] if len(locality) > 1 else None
        self._add(Artifact(
            "hilbert", "Step 12 · Why the leaves are sorted along a Hilbert curve",
            what="Before packing, the fences are sorted by where their centre falls on a "
                 "Hilbert space-filling curve laid over India — a 65,536 × 65,536 "
                 "lattice, about 30 cm per cell at these latitudes.",
            why="What decides how fast a query runs is not the tree's shape but how "
                "*tight* the node boxes are, and that is decided by which fences end up "
                "as siblings. Sorting by longitude puts a fence in Gujarat next to one "
                "in Assam at the same longitude; the box around them covers the "
                "country. A Hilbert curve preserves locality far better than sorting by "
                "x, or by (x, y) tiles: points close along the curve are close in the "
                "plane, so the 16 consecutive fences packed into a node have a small "
                "combined box — and a small box is one a query can reject outright.\n\n"
                "This is also why the heavy clustering in the master helps rather than "
                "hurts. All 400-odd Jamshedpur fences are consecutive on the curve, so "
                "they collapse into a handful of nodes, and a truck nowhere near "
                "Jamshedpur dismisses every one of them by rejecting those few boxes.\n\n"
                "The table measures this rather than asserting it. For each ordering it "
                "gives the distance between fences that end up adjacent — which is the "
                "span a parent node's box has to cover, and therefore the quantity the "
                "packing is trying to minimise. "
                + (f"Under the Hilbert order the median neighbour is {hilbert_med:,.2f} km "
                   f"away; sorted by longitude it is {lon_med:,.1f} km."
                   if hilbert_med is not None and lon_med is not None else ""),
            how="16 bits per axis → interleave the quadrant bits from the coarsest down, "
                "rotating the frame at each level so the sub-curve stays connected → "
                "one integer per fence → stable sort.",
            stats={
                "curve order (bits per axis)": 16,
                "lattice": "65,536 × 65,536",
                "cell size at 22°N (cm)": 30,
                "fences ordered": len(order),
                "median neighbour on the curve (km)": hilbert_med,
                "median neighbour sorted by longitude (km)": lon_med,
                "times tighter": _r(lon_med / hilbert_med, 0)
                if hilbert_med and lon_med else None,
            },
            note="On the map the curve is one thread that enters each cluster, covers it, "
                 "and leaves. The few long strokes are not the curve jumping about: they "
                 "are it crossing empty ground where there is no fence to draw through, "
                 "and there are only as many of them as there are clusters.",
            columns=["ordering", "median gap to the next fence (km)", "90th percentile (km)",
                     "mean (km)", "neighbours within 1 km", "within 1 km (%)"],
            rows=locality,
            extra={"curve": curve},
        ))

    def _step_candidates(self, fit):
        """The chunked index query, chunk by chunk."""
        t = fit.trail
        lats = np.asarray(t.lat, dtype=np.float64)
        lons = np.asarray(t.lon, dtype=np.float64)
        window_m = max(self.det.escape_m, self.det.hysteresis_m) * 4.0 + 1000.0
        pad_deg = window_m / 110_574.0

        rows = []
        seen: dict[int, object] = {}
        total_visited = total_rejected = 0
        chunk_boxes = []
        for start in range(0, len(lats), self.chunk):
            stop = min(start + self.chunk, len(lats))
            sl_lat, sl_lon = lats[start:stop], lons[start:stop]
            box = (float(sl_lon.min()) - pad_deg, float(sl_lat.min()) - pad_deg,
                   float(sl_lon.max()) + pad_deg, float(sl_lat.max()) + pad_deg)
            ex = self.index.explain_box(*box)
            total_visited += ex["visited"]
            total_rejected += ex["rejected"]
            new = [f for f in ex["hits"] if f.fence_id not in seen]
            for f in ex["hits"]:
                seen.setdefault(f.fence_id, f)
            span_km = haversine_m(box[1], box[0], box[3], box[2]) / 1000
            rows.append([len(rows) + 1, _iso(t.ts[start]), _iso(t.ts[stop - 1]), stop - start,
                         _r(box[1], 4), _r(box[0], 4), _r(box[3], 4), _r(box[2], 4),
                         _r(span_km, 1), ex["visited"], len(ex["hits"]), len(new)])
            chunk_boxes.append([_r(box[0], 5), _r(box[1], 5), _r(box[2], 5), _r(box[3], 5)])

        # What other chunk sizes would have cost and returned. The trade-off is
        # the whole reason a number had to be chosen, so it is shown rather
        # than asserted.
        sweep = []
        for size in (64, 128, 256, 512, 1024, 4096, len(lats)):
            if size > len(lats) and sweep and sweep[-1][0] >= len(lats):
                continue
            size = min(size, len(lats))
            got: set[int] = set()
            visited = 0
            for start in range(0, len(lats), size):
                stop = min(start + size, len(lats))
                sl_lat, sl_lon = lats[start:stop], lons[start:stop]
                ex = self.index.explain_box(
                    float(sl_lon.min()) - pad_deg, float(sl_lat.min()) - pad_deg,
                    float(sl_lon.max()) + pad_deg, float(sl_lat.max()) + pad_deg)
                visited += ex["visited"]
                got.update(f.fence_id for f in ex["hits"])
            sweep.append([size, -(-len(lats) // size), visited, len(got),
                          "one box over the whole trip" if size >= len(lats)
                          else "in use" if size == self.chunk else ""])

        whole = sweep[-1]
        whole_km = haversine_m(float(lats.min()), float(lons.min()),
                               float(lats.max()), float(lons.max())) / 1000
        best = min(sweep, key=lambda s: s[3])

        self._add(Artifact(
            "candidates", "Step 13 · Asking the index, one stretch of road at a time",
            what=f"The trail is cut into chunks of {self.chunk} fixes. Each chunk's own "
                 "bounding box is padded and queried against the tree; the answers are "
                 "unioned. The result is every fence worth testing against this trip.",
            why="Two opposite mistakes are being avoided, and the chunk size is what "
                "sits between them.\n\n"
                "Querying **per fix** is correct but pays a tree descent for every GPS "
                f"point — {len(lats):,} descents here, where consecutive fixes are metres "
                "apart and see exactly the same fences.\n\n"
                "Querying **per trip** pays one descent, but asks the wrong question. "
                f"A single box round this whole trail is a {whole_km:,.0f} km rectangle; "
                f"it selects {whole[3]:,} fences, against {len(seen):,} for the per-chunk "
                "boxes. The box has to contain the route's extremes in both axes, so it "
                "also contains everything in the rectangle between them — which for a "
                "route that doglegs is most of a region the truck never entered.\n\n"
                "The table below sweeps the chunk size over this trail so the trade-off "
                "is visible rather than asserted: smaller chunks hug the road and return "
                "fewer candidates, but cost more descents. The elbow is what the default "
                "sits at.\n\n"
                "Chunking cannot lose a fence. Every fix belongs to exactly one chunk, "
                "and a fence containing that fix must intersect that chunk's box — so "
                "the answer is identical at every chunk size, and only the work changes.",
            how=f"pad = max(escape {self.det.escape_m:g} m, band {self.det.hysteresis_m:g} m) "
                f"× 4 + 1000 m = {window_m:,.0f} m ≈ {pad_deg:.4f}°\n"
                "The pad is what keeps a fence the truck merely skirted available to the "
                "escape rule later, instead of being dismissed here.",
            stats={
                "chunks": len(rows),
                "fixes per chunk": self.chunk,
                "node boxes opened": total_visited,
                "node boxes rejected": total_rejected,
                "rejected %": _r(100.0 * total_rejected / total_visited, 1) if total_visited else 0,
                "candidate fences": len(seen),
                "master size": len(self.index),
                "master examined %": _r(100.0 * len(seen) / max(1, len(self.index)), 2),
                "one box over the whole trip would select": whole[3],
                "whole-trip box diagonal (km)": _r(whole_km, 0),
                "fewest candidates any chunk size gives": best[3],
            },
            note="Every chunk size in the sweep returns the same final answer. What "
                 "changes is how much geometry the next step has to run, and how many "
                 "tree descents this one pays for it.",
            columns=["chunk", "from", "to", "fixes", "min lat", "min lon", "max lat", "max lon",
                     "box diagonal (km)", "node boxes opened", "candidates", "new"],
            rows=rows,
            extra={
                "chunk_boxes": chunk_boxes,
                "sweep": {
                    "columns": ["fixes per chunk", "chunks", "node boxes opened",
                                "candidate fences", ""],
                    "rows": sweep,
                },
            },
        ))
        return list(seen.values())

    def _step_prune(self, fit, candidates):
        """The rejection ladder: candidate -> near -> in band -> tested."""
        t = fit.trail
        lats = np.asarray(t.lat, dtype=np.float64)
        lons = np.asarray(t.lon, dtype=np.float64)
        window_m = max(self.det.escape_m, self.det.hysteresis_m) * 4.0 + 1000.0

        near_n = band_n = geometry_evals = 0
        rows = []
        for f in candidates:
            min_lon, min_lat, max_lon, max_lat = f.bbox(window_m)
            near = ((lats >= min_lat) & (lats <= max_lat)
                    & (lons >= min_lon) & (lons <= max_lon))
            hits = int(near.sum())
            stage = "rejected by the bounding-box mask"
            closest = None
            band = self.det.band_for(f)
            if hits:
                near_n += 1
                geometry_evals += hits
                dist = f.signed_distance_windowed(lats, lons, window_m)
                real = dist[dist < FAR_M]
                closest = float(real.min()) if len(real) else None
                if closest is not None and closest <= band:
                    band_n += 1
                    stage = "geometry computed, inside the band"
                else:
                    stage = "geometry computed, never within the band"
            rows.append([f.site_id, f.name, scale_of(f), _r(f.area_m2, 0), hits,
                         _r(closest, 1), _r(band, 1), stage])
        rows.sort(key=lambda r: (r[5] is None, r[5] if r[5] is not None else 0))

        self._add(Artifact(
            "prune", "Step 14 · The rejection ladder",
            what="Each candidate is put through progressively more expensive tests, and "
                 "almost all of them fall out at the cheapest one.",
            why="This is where the 25-billion-test problem actually collapses, and the "
                "order of the tests is the whole trick.\n\n"
                "**Four float comparisons** decide whether *any* fix on the trail falls "
                "in the fence's padded box — one vectorised numpy mask over the entire "
                "trail. A fence that survived the chunk query merely because it sits "
                "near the road fails this and costs nothing more.\n\n"
                "**Only the survivors get real geometry**, and then it is computed for "
                "the whole trail in one vectorised call rather than per fix: the "
                "point-in-polygon loop runs over the ring's *edges* — a median of 13 — "
                "with numpy handling every fix at once. Fixes outside the padded box are "
                "not measured at all; they are provably further away than any threshold "
                "can reach, so their exact distance cannot change a verdict.\n\n"
                "The net effect: on open road a fix costs a handful of comparisons, and "
                "the expensive geometry runs only where a truck was genuinely near a fence.",
            how=f"candidates → box mask (± {window_m:,.0f} m) → signed distance → "
                "within the fence's band? → the state machine",
            stats={
                "master": len(self.index),
                "candidates from the index": len(candidates),
                "passed the bounding-box mask": near_n,
                "came within the band": band_n,
                "fence × fix pairs a naive loop would test": len(self.index) * t.used,
                "pairs reaching the polygon test": geometry_evals,
                "work avoided %": _r(
                    100.0 * (1 - geometry_evals / max(1, len(self.index) * t.used)), 4),
                "cheap comparisons instead": len(candidates) * t.used,
            },
            columns=["site", "fence", "scale", "area (m²)", "fixes in its box",
                     "closest approach (m)", "its band (m)", "outcome"],
            rows=rows[:MAX_DETAIL],
        ))

    def _step_distance(self, fit, candidates):
        """One fix against one fence, in full arithmetic."""
        t = fit.trail
        lats = np.asarray(t.lat, dtype=np.float64)
        lons = np.asarray(t.lon, dtype=np.float64)
        window_m = max(self.det.escape_m, self.det.hysteresis_m) * 4.0 + 1000.0

        # Pick the most interesting fence: the smallest one the truck got inside.
        best = None
        for f in candidates:
            d = f.signed_distance_windowed(lats, lons, window_m)
            inside = d < 0
            if inside.any() and (best is None or f.area_m2 < best[0].area_m2):
                best = (f, d, int(np.argmin(d)))
        example = None
        if best is not None:
            f, d, k = best
            lat, lon = float(lats[k]), float(lons[k])
            x, y = f.frame.to_m(lat, lon)
            # Distance to every edge of the ring, so the minimum is visible.
            edges = []
            n = f.n_vertices
            for i in range(n):
                j = (i + 1) % n
                ax, ay = float(f.ring_x[i]), float(f.ring_y[i])
                bx, by = float(f.ring_x[j]), float(f.ring_y[j])
                abx, aby = bx - ax, by - ay
                den = abx * abx + aby * aby
                if den == 0.0:
                    tt, dx, dy = 0.0, x - ax, y - ay
                else:
                    tt = max(0.0, min(1.0, ((x - ax) * abx + (y - ay) * aby) / den))
                    dx, dy = x - (ax + tt * abx), y - (ay + tt * aby)
                edges.append([i + 1, _r(f.ring_lat[i], 6), _r(f.ring_lon[i], 6),
                              _r(f.ring_lat[j], 6), _r(f.ring_lon[j], 6),
                              _r(tt, 3), _r(math.hypot(dx, dy), 2)])
            edges_sorted = sorted(edges, key=lambda e: e[6])
            crossings = _ray_crossings(lat, lon, f.ring_lat, f.ring_lon)
            example = {
                "fence": f.name, "site_id": f.site_id, "vertices": int(n),
                "fix": _iso(t.ts[k]), "lat": _r(lat, 6), "lon": _r(lon, 6),
                "frame": [_r(f.frame.lat0, 6), _r(f.frame.lon0, 6)],
                "local_xy": [_r(x, 2), _r(y, 2)],
                "crossings": crossings,
                "nearest_edge": edges_sorted[0],
                "signed": _r(float(d[k]), 2),
                "edges": edges_sorted[:12],
                "steps": [
                    f"Project the fix into metres about the fence's own centre "
                    f"({f.frame.lat0:.5f}, {f.frame.lon0:.5f}): "
                    f"x = (lon − lon₀) · R · cos(lat₀) = {x:,.1f} m east, "
                    f"y = (lat − lat₀) · R = {y:,.1f} m north. Degrees are not a length — "
                    "one degree of longitude is 111 km at the equator and 96 km here — so "
                    "every distance is computed in this local metric frame, never in degrees.",
                    f"Inside or outside: cast a ray east from the fix and count how many "
                    f"of the ring's {n} edges it crosses. Here it crosses {crossings}; "
                    f"{'odd' if crossings % 2 else 'even'} means "
                    f"{'inside' if crossings % 2 else 'outside'}.",
                    f"How far from the boundary: the shortest distance from the fix to "
                    f"each edge, clamped to the segment. The nearest is edge "
                    f"#{edges_sorted[0][0]} at {edges_sorted[0][6]:,.2f} m.",
                    (f"Signed distance = {'−' if crossings % 2 else '+'}"
                     f"{edges_sorted[0][6]:,.2f} m"
                     + (f", minus the {f.tolerance_m:g} m tolerance this site declares, "
                        f"= {float(d[k]):,.2f} m" if f.tolerance_m else "")
                     + ". Negative is inside."),
                    "That single signed number — not a bare in/out flag — is what the "
                    "detector consumes. The magnitude is what lets a fix 3 m outside be "
                    "read as 'on the boundary, state unchanged' while one 400 m outside "
                    "is read as a departure.",
                ],
            }

        self._add(Artifact(
            "geometry", "Step 15 · From a point and a polygon to one signed number",
            what="For every fix that survived the ladder, against every fence that "
                 "survived it: is the fix inside the ring, and how far is it from the "
                 "boundary — as one signed distance in metres, negative inside.",
            why="Two coordinate spaces are used deliberately, for different jobs.\n\n"
                "**Containment is decided in degree space**, treating lon/lat as plain "
                "x/y. The fences were authored in a planar map editor and the client's "
                "own system draws their edges as straight lines between consecutive "
                "lat/lon pairs. Testing along geodesics instead would answer a question "
                "nobody asked and would disagree with the client's own screen near the "
                "boundary. It also lets MySQL's planar ST_Contains act as an independent "
                "oracle — which it could not if the engine used different semantics.\n\n"
                "**Distance is measured in local metres**, equirectangular about the "
                "fence's centre. Over a site-sized extent the distortion is far below "
                "GPS noise: under 2 m at 10 km from the reference, and these polygons "
                "are almost all under 3 km across. A full geodesic solution would cost "
                "an order of magnitude more per call and change no verdict.\n\n"
                "The ray cast uses a half-open edge convention (`>` on one end, `<=` on "
                "the other), which is what makes a ray grazing a vertex count once "
                "rather than twice or zero times. That case is not exotic here: the "
                "fences are axis-aligned rectangles and trucks drive on axis-aligned "
                "roads.",
            how="x = (lon − lon₀) · R · cos(lat₀),  y = (lat − lat₀) · R\n"
                "inside  = odd number of eastward ray crossings (Franklin, PNPOLY)\n"
                "distance = min over edges of the point-to-segment distance, t clamped to [0,1]\n"
                "signed  = −distance if inside else +distance,  then − the fence's tolerance",
            stats={
                "fences measured": sum(1 for _ in candidates),
                "vectorised over": t.used,
                "edges in the worked example": example["vertices"] if example else None,
            },
            note="The loop runs over the ring's edges, not over the fixes: for a "
                 "4-vertex fence that is four numpy passes whether there are ten fixes "
                 "or ten thousand.",
            example=example,
        ))

    # ======================================================================
    # Stage 4 -- detection
    # ======================================================================

    def _step_band(self, res):
        seen = {}
        for v in res.visits:
            seen.setdefault(v["fence"].fence_id, v["fence"])
        rows = []
        for f in sorted(seen.values(), key=lambda f: f.area_m2):
            r = f.inradius_m
            rows.append([f.site_id, f.name, scale_of(f), _r(f.area_m2, 0), _r(r, 1),
                         _r(self.det.band_for(f), 1),
                         "scaled to the fence" if r is not None
                         and self.det.band_for(f) < self.det.hysteresis_m else "full band"])
        self._add(Artifact(
            "band", "Step 16 · How wide the boundary band is, per fence",
            what=f"The hysteresis half-band is normally {self.det.hysteresis_m:g} m, but "
                 "on a small fence it is scaled down to a fraction of the largest circle "
                 "that fits inside the ring.",
            why=f"A fixed {self.det.hysteresis_m:g} m band cannot work on a fence whose "
                "centre is less than that from its own edge: *nothing inside it is ever "
                "clearly inside*, so no entry can ever be confirmed and the fence is "
                "silently invisible. Nearly 1,000 fences in this master — a fifth of it "
                "— are that small, and under a fixed band 905 of the active ones "
                "recorded no visit at all. A 58 m parking bay whose centre is 29 m from "
                f"every edge leaves 4 m of 'clearly inside' under a {self.det.hysteresis_m:g} m "
                "band; entries then depend on GPS noise happening to push a fix that deep.\n\n"
                "The inscribed radius is found with polylabel — a best-first quadtree "
                "search where each cell's potential is its centre's inside-distance plus "
                "its half-diagonal, and a cell is split only if that potential beats the "
                "best found so far. Computed once per master import, then read from the "
                "table.",
            how=f"band = min( {self.det.hysteresis_m:g} m,  "
                f"max( {self.det.min_band_m:g} m, {self.det.band_fraction:g} × inradius ),  "
                "0.8 × inradius )\n"
                "so a large works behaves exactly as it did before, and a small one "
                "becomes detectable at all",
            stats={
                "adaptive band": self.det.adaptive_band,
                "full band (m)": self.det.hysteresis_m,
                "fraction of inradius": self.det.band_fraction,
                "floor (m)": self.det.min_band_m,
                "fences visited on this trip": len(rows),
                "of those, scaled down": sum(1 for r in rows if r[6] != "full band"),
            },
            columns=["site", "fence", "scale", "area (m²)", "inradius (m)", "band used (m)",
                     "why"],
            rows=rows,
        ))

    def _step_state(self, fit, res):
        """Per-fix state-machine trace for the fence that matters most."""
        t = fit.trail
        lats = np.asarray(t.lat, dtype=np.float64)
        lons = np.asarray(t.lon, dtype=np.float64)

        # The longest facility stay: the one a reader most wants to check.
        target = None
        for v in res.visits:
            if scale_of(v["fence"]) in FACILITY and (
                    target is None or (v["dwell_seconds"] or 0) > (target["dwell_seconds"] or 0)):
                target = v
        rows: list[list] = []
        example = None
        if target is not None:
            f = target["fence"]
            band = self.det.band_for(f)
            window_m = max(self.det.escape_m, self.det.hysteresis_m) * 4.0 + 1000.0
            dist = f.signed_distance_windowed(lats, lons, window_m)
            det = st.detect(t.ts, dist, hysteresis_m=band,
                            confirm_seconds=self.det.confirm_seconds,
                            escape_m=self.det.escape_m,
                            max_gap_seconds=self.det.max_gap_seconds)
            tracker = st.FenceTracker(band, self.det.confirm_seconds, self.det.escape_m,
                                      self.det.max_gap_seconds)
            first = next((i for i in range(len(dist)) if dist[i] < window_m), 0)
            last = len(dist) - 1 - next(
                (i for i in range(len(dist)) if dist[len(dist) - 1 - i] < window_m), 0)
            for i in range(len(dist)):
                d = float(dist[i])
                before = tracker.state
                pending_before = tracker.pending
                held_before, esc_before = tracker._run_held, tracker._run_escape
                crossing = tracker.step(t.ts[i], d)
                if not (first - 3 <= i <= last + 3):
                    continue
                if d >= FAR_M:
                    continue
                verdict = ""
                if crossing:
                    verdict = f"{crossing.event.upper()} confirmed by {crossing.confirmed_by}"
                elif tracker.pending and not pending_before:
                    verdict = f"a run towards {tracker.pending} begins"
                elif pending_before and not tracker.pending:
                    verdict = "the run collapsed — it was jitter"
                rows.append([
                    i + 1, _iso(t.ts[i]), _r(d, 1),
                    "clear inside" if d <= -band else "clear outside" if d >= band else "in the band",
                    before or "—", tracker.pending or "—",
                    _r(tracker._run_held if tracker.pending else held_before, 0),
                    _r(tracker._run_escape if tracker.pending else esc_before, 1),
                    tracker.state or "—", verdict,
                ])
            enter = next((c for c in det.crossings if c.event == "enter"), None)
            if enter is not None:
                k = enter.index
                example = {
                    "fence": f.name, "site_id": f.site_id, "band": _r(band, 1),
                    "confirmed_by": enter.confirmed_by,
                    "stamped": _iso(enter.ts), "gap_s": enter.gap_seconds,
                    "steps": [
                        f"While outside, a fix arrives at {float(dist[k]):,.1f} m — past "
                        f"the inner edge of the ±{band:,.1f} m band, so it *contradicts* "
                        "the current state and opens a candidate run.",
                        ("The run held for at least "
                         f"{self.det.confirm_seconds:g} s of observed time, so the new state "
                         "is believed." if enter.confirmed_by == "dwell" else
                         f"The trail got more than {self.det.escape_m:g} m past the band — "
                         "displacement no receiver produces by scatter — so the entry is "
                         "confirmed however brief it was."),
                        f"The entry is stamped at {_iso(enter.ts)}: the *first fix of the "
                        "confirmed run*, an instant that was really observed. It is never "
                        "interpolated.",
                        (f"The true crossing lies somewhere in the {enter.gap_seconds:,} s "
                         "between that fix and the one before it. That uncertainty is "
                         "reported with the crossing rather than averaged away."
                         if enter.gap_seconds else
                         "There was no earlier fix to bound the crossing against."),
                        "Everything in the run is then back-filled to the new state: the "
                        "truck was inside for the whole run; we merely took until now to "
                        "believe it.",
                    ],
                }

        self._add(Artifact(
            "detect", "Step 17 · The crossing detector, fix by fix",
            what="A band, a hold, and an escape hatch. Each fix's signed distance is fed "
                 "to a state machine that decides when a change of state is real.",
            why="**Why a band (hysteresis).** Park a truck on a fence line — which is "
                "exactly what a truck at a weighbridge or a gate is doing, by definition "
                "— and consecutive fixes fall alternately inside and outside. Read each "
                "fix as a verdict and you get in/out/in/out: hundreds of 'visits' for "
                "one parking event, a dwell time that is pure noise, and an alert stream "
                "nobody will keep reading. So there are two boundaries, "
                f"{self.det.hysteresis_m:g} m either side of the real one. Crossing into "
                "the band changes nothing; only clearing the *far* edge is even "
                "considered. Jitter never clears the far edge, so it never flips the "
                "state. This is the Schmitt trigger, 1938, and it is the standard answer "
                "to a noisy signal crossing a threshold.\n\n"
                "**Why a hold as well.** Hysteresis alone still flips on one bad "
                "multipath fix 100 m inside a yard. So a flip must also *persist* for "
                f"{self.det.confirm_seconds:g} s before it is believed — about 1.5 "
                "sampling intervals at this feed's ~60 s cadence, so two independent "
                "fixes have to agree. Shorter runs are absorbed back into the state "
                "around them.\n\n"
                "**Why an escape hatch on top.** Dwell confirmation alone quietly loses "
                "two real cases: the truck genuinely leaves and its tracker dies two "
                "minutes later — the run never reaches the threshold, so the departure "
                "is never recorded and the truck is reported inside forever; and a fast "
                "transit of a small fence — 200 m of gate zone at 40 km/h is 18 seconds, "
                "and no dwell threshold useful against jitter is that short. So a flip "
                f"is *also* confirmed, however brief, once the trail gets "
                f"{self.det.escape_m:g} m clear of the band. A fix that far out is not "
                "receiver scatter at any grade of hardware; it is displacement. Distance "
                "substitutes for time when time is unavailable.\n\n"
                "**Why an outage can confirm nothing.** Held time only accumulates over "
                f"intervals shorter than {self.det.max_gap_seconds:g} s. A six-hour hole "
                "would otherwise satisfy any dwell threshold on its own, and the detector "
                "would 'confirm' a transition using an interval in which it observed "
                "nothing at all.",
            how=f"clear inside: d ≤ −{self.det.hysteresis_m:g} m · clear outside: "
                f"d ≥ +{self.det.hysteresis_m:g} m · between: no information\n"
                f"a contradicting fix opens a run → confirmed when held ≥ "
                f"{self.det.confirm_seconds:g} s  OR  escape ≥ {self.det.escape_m:g} m\n"
                "a fix agreeing with the current state collapses the run: it was jitter",
            stats={
                "band (m)": self.det.hysteresis_m,
                "hold (s)": self.det.confirm_seconds,
                "escape (m)": self.det.escape_m,
                "gaps over (s) confirm nothing": self.det.max_gap_seconds,
                "traced fence": target["fence"].name if target else None,
                "crossings on this trip": len(res.events),
                "confirmed by dwell": sum(1 for e in res.events if e["confirmed_by"] == "dwell"),
                "confirmed by escape": sum(1 for e in res.events if e["confirmed_by"] == "escape"),
            },
            note="The table below traces the single longest facility stay on this trip, "
                 "fix by fix, so the rule can be checked by hand. Every other fence went "
                 "through the identical machine.",
            columns=["fix", "timestamp", "signed distance (m)", "reading", "state before",
                     "run towards", "held (s)", "escape (m)", "state after", "what happened"],
            rows=rows[:MAX_DETAIL],
            example=example,
        ))

    def _step_visits(self, res):
        rows = []
        for v in res.visits:
            f = v["fence"]
            rows.append([
                f.site_id, f.name, f.site_type, f.category, scale_of(f),
                _iso(v["dt_enter"]), _iso(v["dt_exit"]), v["dwell_seconds"],
                _r(v["dwell_seconds"] / 3600, 2), v["pings"], v["max_speed"],
                _r(v["distance_m"], 1), v["enter_gap"], v["exit_gap"],
                "yes" if v["primary"] else "no", "yes" if v["entry_observed"] else "no",
                "still inside at the last fix" if v["open"] else "closed",
                v["confirmed_by"],
            ])
        self._add(Artifact(
            "visits", "Step 18 · Crossings folded into visits",
            what="Confirmed entries and exits are paired into stays, each with its dwell "
                 "time, the fixes it rests on, and the GPS uncertainty at both ends.",
            why="Two untidy cases are kept rather than dropped, because dropping them "
                "would make the count wrong in the direction that matters.\n\n"
                "A trail that *begins* with the truck already inside yields an exit with "
                "no entry. That is a real visit whose start predates the trail, so it is "
                "emitted with the entry at the first fix and flagged "
                "`entry_observed = no`, not discarded for being incomplete.\n\n"
                "A visit still open when the trail ends is emitted with `open = yes`. Its "
                "dwell is the *observed* dwell — a lower bound, not a measurement — and "
                "the flag says which of the two it is. A lower bound is far more useful "
                "than the NULL a stricter reading would produce, as long as nobody is "
                "allowed to mistake it for a measurement.",
            how="enter → exit pairs in time order; an unmatched exit opens at the first "
                "fix; an unmatched entry stays open at the last.",
            stats={
                "visits": len(res.visits),
                "distinct fences": len({v["fence"].fence_id for v in res.visits}),
                "distinct sites": len({v["fence"].site_id for v in res.visits}),
                "still open at the end": sum(1 for v in res.visits if v["open"]),
                "entry not observed": sum(1 for v in res.visits if not v["entry_observed"]),
                "total dwell (s)": sum(v["dwell_seconds"] or 0 for v in res.visits),
            },
            columns=["site", "fence", "type", "category", "scale", "entered", "left",
                     "dwell (s)", "dwell (h)", "fixes", "max km/h", "distance inside (m)",
                     "± at entry (s)", "± at exit (s)", "innermost", "entry seen", "state",
                     "confirmed by"],
            rows=rows,
        ))

    def _step_nesting(self, res):
        groups: list[dict] = []
        for v in sorted(res.visits, key=lambda v: v["dt_enter"]):
            end = v["dt_exit"] or v["dt_enter"]
            placed = False
            for g in groups:
                if v["dt_enter"] <= g["end"] and g["start"] <= end:
                    g["members"].append(v)
                    g["end"] = max(g["end"], end)
                    placed = True
                    break
            if not placed:
                groups.append({"start": v["dt_enter"], "end": end, "members": [v]})
        rows = []
        for i, g in enumerate(groups):
            if len(g["members"]) < 2:
                continue
            inner = min(g["members"], key=lambda v: (v["fence"].area_m2, v["fence"].fence_id))
            for v in sorted(g["members"], key=lambda v: v["fence"].area_m2):
                rows.append([i + 1, _iso(g["start"]), v["fence"].site_id, v["fence"].name,
                             scale_of(v["fence"]), _r(v["fence"].area_m2, 0),
                             v["dwell_seconds"],
                             "innermost — this is 'where the truck is'"
                             if v is inner else "outer zone"])
        self._add(Artifact(
            "nesting", "Step 19 · Nested fences: all of them true at once",
            what="The master nests heavily. A truck at the Jamshedpur hot strip mill is "
                 "genuinely inside the mill, 'INSIDE TSL', 'INSIDE TSL_CSD', 'TATA "
                 "STEEL, JAMSHEDPUR', 'TWS' and a city-scale polygon simultaneously — "
                 "six true containments for one physical location.",
            why="Reporting that as six visits is correct and useless. Suppressing five of "
                "them is useful and wrong. So all six are kept, and the *smallest-area* "
                "fence overlapping in time is marked innermost.\n\n"
                "Smallest-area is the right tie-break because a containing fence always "
                "has the larger area — so the innermost zone wins without needing an "
                "explicit containment hierarchy, which the master does not provide. Ties "
                "break on fence id so the choice is stable between runs rather than "
                "dependent on dictionary order.\n\n"
                "'Where is the truck' then reads the innermost; 'which zones does this "
                "touch' reads them all. Two questions, one ledger, no double counting.",
            how="visits overlapping in time are grouped; "
                "min(area, fence_id) within a group is innermost",
            stats={
                "overlapping groups": sum(1 for g in groups if len(g["members"]) > 1),
                "deepest nesting": max((len(g["members"]) for g in groups), default=0),
                "visits marked innermost": sum(1 for v in res.visits if v["primary"]),
                "visits marked outer": sum(1 for v in res.visits if not v["primary"]),
            },
            columns=["group", "from", "site", "fence", "scale", "area (m²)", "dwell (s)",
                     "role"],
            rows=rows[:MAX_DETAIL],
        ))

    def _step_places(self, res):
        from nexgen.shared.geoengine.pipeline.runner import places as _places
        pl = _places(res.visits)
        rows = [[i + 1, p["fence"].site_id, p["fence"].name, scale_of(p["fence"]),
                 _iso(p["enter"]), _iso(p["exit"]), p["dwell_s"], _r(p["dwell_s"] / 3600, 2)]
                for i, p in enumerate(pl)]
        self._add(Artifact(
            "places", "Step 20 · Places: the trip as a human would tell it",
            what="Nested facility visits overlapping in time are collapsed into one "
                 "place, named by the outermost facility fence of the cluster. Regional "
                 "catchments are left out — they are districts, not destinations.",
            why="For 'where did this trip go', a works and its gate zone and its mill are "
                "one place, not three. The outermost facility fence is the one a person "
                "would name ('Tata Steel Jamshedpur', not 'Gate 4'), while the innermost "
                "is kept alongside for anyone who needs the precise bay. Dwell is "
                "measured across the whole cluster — first entry to last exit — so time "
                "spent moving between the mill and the weighbridge inside one works "
                "counts once, at the works.",
            how="facility-scale visits (micro / site / campus) clustered by time overlap; "
                "the largest-area member names the cluster",
            stats={
                "places": len(pl),
                "time at places (s)": sum(p["dwell_s"] for p in pl),
                "first": pl[0]["fence"].name if pl else None,
                "last": pl[-1]["fence"].name if len(pl) > 1 else None,
            },
            columns=["#", "site", "place", "scale", "arrived", "left", "stay (s)", "stay (h)"],
            rows=rows,
        ))
        return pl

    def _step_events(self, res):
        self._add(Artifact(
            "events", "Step 21 · Every confirmed crossing",
            what="The entry and exit events themselves, each stamped on a fix that was "
                 "actually observed, with the GPS gap that bounds it.",
            why="The timestamp is never interpolated. The true crossing lies somewhere in "
                "the interval between the stamped fix and its neighbour, and that "
                "interval is published as `± gap`. A crossing with a 45-second gap is "
                "precise; one with a 4-hour gap is a guess wearing a timestamp, and a "
                "report that averaged the two together would be worse than one that "
                "showed neither.",
            how="enter → the first fix of the confirmed run · exit → the last fix still "
                "inside, which is the one before the run began",
            stats={"crossings": len(res.events)},
            columns=["when", "event", "site", "fence", "± gap (s)", "lat", "lon", "km/h",
                     "confirmed by"],
            rows=[[_iso(e["ts"]), e["event"], e["fence"].site_id, e["fence"].name,
                   e["gap_seconds"], _r(e["lat"], 6), _r(e["lon"], 6), e["speed"],
                   e["confirmed_by"]] for e in res.events],
        ))

    def _step_violations(self, res):
        self._add(Artifact(
            "violations", "Step 22 · Rule breaches",
            what="Entry into a restricted or high-risk site, and overspeed against a "
                 "site's own declared limit.",
            why="Overspeed is reported once per visit, at its worst fix — not once per "
                "fix. A truck 10 km/h over for twenty minutes inside a works is one "
                "event an operator can act on; 1,200 of them is a wall of noise that "
                "guarantees the alert stream gets muted, and a muted alert stream is "
                "worse than none.",
            how="category ∈ {restricted, high_risk} → one alert on entry\n"
                "speed > the site's limit → one alert per visit, at the peak, with the "
                "count of offending fixes",
            stats={"alerts": len(res.violations)},
            columns=["when", "kind", "site", "fence", "observed", "limit", "detail"],
            rows=[[_iso(v["ts"]), v["kind"], v["fence"].site_id, v["fence"].name,
                   v["observed"], v["limit"], v["detail"]] for v in res.violations],
        ))

    # ======================================================================
    # Stage 5 -- the answer
    # ======================================================================

    def _step_kpis(self, fit, res, places):
        by_site: dict[int, dict] = {}
        for v in res.visits:
            f = v["fence"]
            e = by_site.setdefault(f.site_id, {
                "name": f.name, "scale": scale_of(f), "category": f.category,
                "type": f.site_type, "visits": 0, "dwell": 0, "pings": 0,
                "first": None, "last": None, "open": False, "primary": 0,
                "max_speed": None, "area": f.area_m2,
            })
            e["visits"] += 1
            e["dwell"] += v["dwell_seconds"] or 0
            e["pings"] += v["pings"]
            e["primary"] += 1 if v["primary"] else 0
            e["open"] = e["open"] or v["open"]
            e["first"] = v["dt_enter"] if e["first"] is None else min(e["first"], v["dt_enter"])
            end = v["dt_exit"] or v["dt_enter"]
            e["last"] = end if e["last"] is None else max(e["last"], end)
            if v["max_speed"] is not None:
                e["max_speed"] = max(e["max_speed"] or 0, v["max_speed"])

        rows = sorted(by_site.items(), key=lambda kv: -kv[1]["dwell"])
        table = [[sid, e["name"], e["scale"], e["category"], e["visits"], e["dwell"],
                  _r(e["dwell"] / 3600, 2), e["pings"], _iso(e["first"]), _iso(e["last"]),
                  e["max_speed"], "yes" if e["open"] else "no"] for sid, e in rows]

        s = res.summary
        first = places[0] if places else None
        last = places[-1] if len(places) > 1 else None
        transit = None
        if first and last and first["exit"] and last["enter"] > first["exit"]:
            transit = int((last["enter"] - first["exit"]).total_seconds())
        mid = [p for p in places[1:-1]] if len(places) > 2 else []
        stops_outside = [st for st in fit.stops]

        self._add(Artifact(
            "kpis", "Step 23 · The answer",
            what="Where the truck went, how long it was in each geofence, how long it "
                 "spent loading and unloading, and how much of the trip the GPS "
                 "actually saw.",
            why="Loading and unloading are read off the *first* and *last* place of the "
                 "trip, because that is what they physically are: the stay at the origin "
                 "facility before the run, and the stay at the destination facility "
                 "after it. Everything between them is transit, and the places inside "
                 "that window are intermediate handling — a transhipment yard, a "
                 "weighbridge, a parking bay.\n\n"
                "Every one of these numbers carries the trail's quality verdict with it. "
                "A fence this trip appears not to have visited may simply have been "
                "passed while the tracker was silent, and the unobserved time below is "
                "how much room there is for that to have happened.",
            how="loading = the stay at the first place · unloading = the stay at the last "
                "place · transit = first exit → last arrival · time in a fence = the sum "
                "of that fence's confirmed stays",
            stats={
                "places visited": len(places),
                "geofences touched": len(by_site),
                "fence visits": len(res.visits),
                "loading — at": first["fence"].name if first else None,
                "loading (s)": first["dwell_s"] if first else None,
                "unloading — at": last["fence"].name if last else None,
                "unloading (s)": last["dwell_s"] if last else None,
                "transit (s)": transit,
                "intermediate places": len(mid),
                "time inside some fence (s)": s.get("inside_seconds"),
                "distance travelled (km)": _r(fit.distance_m / 1000, 2),
                "unobserved distance (km)": _r(fit.gap_distance_m / 1000, 2),
                "stops": len(stops_outside),
                "alerts": len(res.violations),
                "GPS fixes used": s.get("pings_used"),
                "GPS quality": fit.quality,
            },
            columns=["site", "geofence", "scale", "category", "visits", "time inside (s)",
                     "time inside (h)", "fixes inside", "first entered", "last left",
                     "max km/h", "open at the end"],
            rows=table,
            extra={
                "timeline": [
                    {"kind": "place", "site_id": p["fence"].site_id, "name": p["fence"].name,
                     "scale": scale_of(p["fence"]), "enter": _iso(p["enter"]),
                     "exit": _iso(p["exit"]), "dwell_s": p["dwell_s"],
                     "role": ("loading" if i == 0 and len(places) > 1 else
                              "unloading" if i == len(places) - 1 and len(places) > 1 else
                              "intermediate")}
                    for i, p in enumerate(places)
                ],
            },
        ))


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[(start, end)] of each True run in a boolean mask."""
    out: list[tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and mask[j + 1]:
            j += 1
        out.append((i, j))
        i = j + 1
    return out


def _ray_crossings(lat: float, lon: float, ring_lat, ring_lon) -> int:
    """How many edges an eastward ray from the point crosses. Odd = inside.

    The same half-open convention as `geometry.point_in_ring`; this counts
    rather than toggles, so the worked example can show the number."""
    n = len(ring_lat)
    count = 0
    j = n - 1
    for i in range(n):
        yi, yj = float(ring_lat[i]), float(ring_lat[j])
        if (yi > lat) != (yj > lat):
            xi, xj = float(ring_lon[i]), float(ring_lon[j])
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                count += 1
        j = i
    return count
