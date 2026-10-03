"""The live detector: pings in, confirmed crossings out, one fix at a time.

Relationship to the batch engine
--------------------------------
There is no second algorithm here. Containment is the same `Fence`, the
crossing rules are the same `FenceTracker`, the outlier gate is the same
physical-plausibility test. What this module adds is the bookkeeping a
streaming caller needs and a batch caller does not: which fences to keep
watching for each vehicle, when to stop watching one, and what "where is this
truck right now" means.

`tests/test_live.py` asserts that replaying a trail through here produces the
same crossings as running it through the batch evaluator. If that ever stops
being true, one of them has a bug.

Which fences a vehicle is tracked against
-----------------------------------------
Not "the ones containing it" -- that would lose the escape rule, which needs
to watch a fence the truck has just left in order to confirm the departure.
So each vehicle tracks:

  * every fence whose padded bounding box covers its current fix, and
  * every fence it already has an open tracker for.

A tracker is retired once the vehicle is confirmed outside that fence, has no
run in progress, and has moved beyond the watch radius. Without that pruning,
a truck that drives the length of the country accumulates a tracker for every
fence it has ever passed and the per-vehicle step cost grows all day.

Memory
------
One tracker is a handful of floats. A 2,000-vehicle fleet in dense industrial
country holds maybe 20 trackers each -- some tens of thousands of small
objects, a few megabytes. The whole fleet's live state fits comfortably in
one process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from nexgen.shared.geoengine.config import DetectorConfig
from nexgen.shared.geoengine.engine.state import FenceTracker
from nexgen.shared.geoengine.geometry import haversine_m
from nexgen.shared.geoengine.index import FenceIndex
from nexgen.shared.geoengine.model import FAR_M, Fence
from nexgen.shared.geoengine.store import scale_of

logger = logging.getLogger(__name__)

# How far outside a fence a vehicle is still tracked against it.
#
# Sized so that a vehicle is *always observed outside a fence before it enters
# it*. That is what makes an entry detectable at all: a tracker whose very
# first fix is already inside has no transition to report, and the arrival
# goes unrecorded. At the observed 60 s cadence and the 150 km/h plausibility
# ceiling a vehicle covers at most 2.5 km between fixes, so a 5 km window
# guarantees at least one outside observation on any physically possible
# approach. It is also why the live detector agrees with the batch one, which
# sees the whole trail and therefore always has that observation.
#
# The cost is more open trackers per vehicle in dense country, which is a
# throughput question, not a correctness one.
WATCH_PAD_M = 5000.0

# Retire below the creation threshold, so a vehicle loitering near the window
# edge does not churn a tracker on every fix.
RETIRE_AT_M = WATCH_PAD_M * 0.8


@dataclass
class LiveEvent:
    """Something worth telling the control room about."""

    ts: datetime
    asset_id: str
    trip_no: int | None
    fence: Fence
    event: str            # enter | exit | overspeed | restricted
    severity: str         # info | warn | alert
    lat: float
    lon: float
    speed: int | None = None
    gap_seconds: int | None = None
    limit: int | None = None
    detail: str = ""
    confirmed_by: str | None = None


@dataclass
class VehicleState:
    """Everything the detector remembers about one vehicle."""

    asset_id: str
    trackers: dict[int, FenceTracker] = field(default_factory=dict)
    last_ts: datetime | None = None
    last_lat: float | None = None
    last_lon: float | None = None
    last_speed: int | None = None
    trip_no: int | None = None
    inside: set[int] = field(default_factory=set)
    # Fences already alerted on for this visit, so one restricted-zone entry
    # does not re-alert on every subsequent ping inside it.
    alerted: set[int] = field(default_factory=set)
    dropped: int = 0
    seen: int = 0


class LiveDetector:
    """Stateful, single-threaded. One instance owns the whole fleet."""

    def __init__(self, index: FenceIndex, config: DetectorConfig):
        self.index = index
        self.cfg = config
        self.vehicles: dict[str, VehicleState] = {}
        self.events_emitted = 0
        self.pings_seen = 0
        self.pings_dropped = 0

    # -- the hot path -------------------------------------------------------

    def feed(self, asset_id: str, ts: datetime, lat: float, lon: float,
             speed: int | None = None, trip_no: int | None = None):
        """Process one fix. Returns (events, position) or (None, None) if the
        fix was rejected by the outlier gate."""
        self.pings_seen += 1
        v = self.vehicles.get(asset_id)
        if v is None:
            v = self.vehicles[asset_id] = VehicleState(asset_id=asset_id)

        # -- outlier gate ---------------------------------------------------
        # Same physical-plausibility rule as the batch pre-filter, minus the
        # one-fix lookahead: a live detector has no successor to consult yet.
        # A lone spike is therefore dropped on arrival, and if the trail really
        # has jumped, the *next* fix re-anchors because the gap has grown.
        if v.last_ts is not None:
            dt = (ts - v.last_ts).total_seconds()
            if dt <= 0:
                v.dropped += 1
                self.pings_dropped += 1
                return None, None
            if dt <= self.cfg.max_gap_seconds:
                implied = haversine_m(v.last_lat, v.last_lon, lat, lon) / dt * 3.6
                if implied > self.cfg.max_plausible_kmph:
                    v.dropped += 1
                    self.pings_dropped += 1
                    return None, None

        v.seen += 1
        v.last_ts, v.last_lat, v.last_lon = ts, lat, lon
        v.last_speed, v.trip_no = speed, trip_no

        # -- which fences matter for this fix -------------------------------
        near = {f.fence_id: f for f in self.index.query_point(lat, lon)}
        for fid in list(v.trackers):
            if fid not in near:
                f = self.index.by_id(fid)
                if f is not None:
                    near[fid] = f

        events: list[LiveEvent] = []

        for fid, fence in near.items():
            # Scalar path: one fix against one fence. The vectorised form is
            # for trails and is two orders of magnitude slower at length 1.
            dist = fence.signed_distance_point(lat, lon, WATCH_PAD_M)

            tracker = v.trackers.get(fid)
            if tracker is None:
                # Open a tracker for anything inside the watch window, not
                # just for fences already containing the vehicle: the tracker
                # needs to see the vehicle outside first, or the entry is not
                # a transition and never fires.
                if dist >= FAR_M:
                    continue
                tracker = v.trackers[fid] = FenceTracker(
                    hysteresis_m=self.cfg.band_for(fence),
                    confirm_seconds=self.cfg.confirm_seconds,
                    escape_m=self.cfg.escape_m,
                    max_gap_seconds=self.cfg.max_gap_seconds,
                )

            crossing = tracker.step(ts, dist)

            # Mirror the tracker's confirmed state rather than only reacting
            # to transitions. A vehicle first seen already inside a fence has
            # no entry to report, but it is still inside, and "where is it"
            # has to say so.
            if tracker.inside:
                v.inside.add(fid)
            else:
                v.inside.discard(fid)
                v.alerted.discard(fid)

            if crossing:
                events.append(LiveEvent(
                    ts=crossing.ts, asset_id=asset_id, trip_no=trip_no,
                    fence=fence, event=crossing.event,
                    severity="info", lat=lat, lon=lon, speed=speed,
                    gap_seconds=crossing.gap_seconds,
                    confirmed_by=crossing.confirmed_by,
                    detail=f"{crossing.event} {fence.name}",
                ))

            # Retire a tracker the vehicle has finished with.
            if (not tracker.inside and not tracker.pending
                    and dist > RETIRE_AT_M):
                v.trackers.pop(fid, None)
                v.inside.discard(fid)
                v.alerted.discard(fid)

        events.extend(self._rules(v, ts, lat, lon, speed, trip_no))

        self.events_emitted += len(events)
        return events, self._position(v)

    # -- rules ---------------------------------------------------------------

    def _rules(self, v: VehicleState, ts, lat, lon, speed, trip_no) -> list[LiveEvent]:
        """Breaches, raised once per visit rather than once per fix.

        A truck 10 km/h over inside a works for twenty minutes is one thing an
        operator can act on. Twenty alerts for it is a reason to stop reading
        the alert list.
        """
        out: list[LiveEvent] = []
        for fid in v.inside:
            if fid in v.alerted:
                continue
            fence = self.index.by_id(fid)
            if fence is None:
                continue

            if fence.category in ("restricted", "high_risk"):
                v.alerted.add(fid)
                out.append(LiveEvent(
                    ts=ts, asset_id=v.asset_id, trip_no=trip_no, fence=fence,
                    event="restricted", severity="alert", lat=lat, lon=lon,
                    speed=speed,
                    detail=f"entered {fence.category.replace('_', ' ')} zone {fence.name}",
                ))
            elif fence.max_speed and speed and speed > fence.max_speed:
                v.alerted.add(fid)
                out.append(LiveEvent(
                    ts=ts, asset_id=v.asset_id, trip_no=trip_no, fence=fence,
                    event="overspeed", severity="warn", lat=lat, lon=lon,
                    speed=speed, limit=fence.max_speed,
                    detail=f"{speed} km/h against a {fence.max_speed} km/h limit "
                           f"inside {fence.name}",
                ))
        return out

    # -- readout -------------------------------------------------------------

    def _position(self, v: VehicleState) -> dict:
        """Current position, plus the innermost fence containing it.

        Innermost, because the master nests: a truck at Jamshedpur is inside
        a gate zone, a mill, a works and a city polygon at once, and "where is
        it" wants the smallest of those. The full set is still reported as a
        count so the UI can show that there are others.
        """
        primary = None
        if v.inside:
            fences = [self.index.by_id(f) for f in v.inside]
            fences = [f for f in fences if f is not None]
            if fences:
                primary = min(fences, key=lambda f: (f.area_m2, f.fence_id))
        return {
            "asset_id": v.asset_id,
            "ts": v.last_ts,
            "lat": v.last_lat,
            "lon": v.last_lon,
            "speed": v.last_speed,
            "trip_no": v.trip_no,
            "inside_count": len(v.inside),
            "fence_id": primary.fence_id if primary else None,
            "site_id": primary.site_id if primary else None,
            "site_name": primary.name if primary else None,
            "category": primary.category if primary else None,
            "scale": scale_of(primary) if primary else None,
        }

    # -- state ---------------------------------------------------------------

    def snapshot(self) -> list[dict]:
        return [self._position(v) for v in self.vehicles.values()
                if v.last_ts is not None]

    def stats(self) -> dict:
        return {
            "vehicles": len(self.vehicles),
            "trackers": sum(len(v.trackers) for v in self.vehicles.values()),
            "inside_now": sum(1 for v in self.vehicles.values() if v.inside),
            "pings_seen": self.pings_seen,
            "pings_dropped": self.pings_dropped,
            "events_emitted": self.events_emitted,
        }
