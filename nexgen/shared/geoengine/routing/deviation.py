"""When a truck left its route, for how long, and what it cost in kilometres.

How navigation apps do it
---------------------------------------------------------------------------
Google Maps, Rapido, Uber and Ola all run the same loop on every GPS update:
snap the position to the road (map matching), measure how far it is from the
planned route and how far along it the vehicle has got, and when the vehicle
has been *off* the route for long enough -- a distance threshold held for a
few seconds, so one bad fix does not trigger it -- declare it off-route and
ask the router for a new route from where it is now to the destination. That
new route becomes the plan; the app says "rerouting". Ride-hailing apps also
record the deviation, because the fare and the safety team both care: an
unexplained detour is a longer bill or a rider at risk.

What changes for a truck
---------------------------------------------------------------------------
* Fixes arrive every ~60 s, not every second, and a stationary receiver
  scatters -- so the thresholds are hundreds of metres, not tens, and "held
  for long enough" is two fixes and a few minutes. This is the geofence
  engine's own rule (hysteresis band, dwell confirmation, escape distance)
  applied to a corridor instead of a polygon.
* Trucks leave the road on purpose: fuel, food, rest, a weighbridge, a
  parking yard overnight. A deviation that returns to where it left, with a
  long stop in it, is an *off-route stop*, not a detour; its cost is time,
  not distance.
* The origin and destination are large facilities with several gates, so
  the first and last stretch of a trip (the terminal zones) is not judged.

Episodes and their kinds
---------------------------------------------------------------------------
    detour          left, rejoined further along; extra km = driven − skipped
    shortcut        the same, but shorter than the stretch of plan it skipped
    off_route_stop  left and came back near the same point with a long stop
    excursion       left and came back near the same point without one
    backtrack       rejoined the route behind where it left
    alternate_route never came back before arriving: another road to the end
    reroute         (with a router) off-route long enough that a new route was
                    planned from here, as a navigation app would; the
                    penalty is what the new plan adds to the old one
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from nexgen.shared.geoengine.routing.geometry import Polyline


@dataclass(frozen=True)
class DeviationConfig:
    off_m: float = 300.0         # further than this from the route is "off" ...
    on_m: float = 150.0          # ... and nearer than this is back "on" (hysteresis)
    confirm_s: float = 180.0     # off for this long (and two fixes) before it counts
    escape_m: float = 2000.0     # this far off counts at once
    terminal_m: float = 1500.0   # the first and last stretch of transit is not judged
    near_m: float = 1000.0       # rejoining within this of the leaving point is "the same point"
    long_stop_s: float = 900.0   # a stop this long makes an off-route stop
    gap_s: float = 1800.0        # a silence longer than this is flagged on the episode


@dataclass
class Episode:
    kind: str
    i_leave: int                 # last fix on the route before leaving
    i_back: int | None           # first fix back on it; None if it never came back
    t_leave: float
    t_back: float | None
    chain_leave_m: float
    chain_back_m: float | None
    actual_m: float              # driven while off
    planned_m: float             # the stretch of plan this replaced
    extra_m: float
    max_offset_m: float
    stop_s: float = 0.0
    silent: bool = False
    reroute: dict | None = None
    plan_index: int = 0

    @property
    def duration_s(self) -> float:
        return (self.t_back if self.t_back is not None else self.t_leave) - self.t_leave


@dataclass
class Result:
    episodes: list[Episode] = field(default_factory=list)
    plans: list[Polyline] = field(default_factory=list)
    offset_m: np.ndarray | None = None       # per fix, against the plan in force
    on_route: np.ndarray | None = None       # per fix: judged on the route
    judged: np.ndarray | None = None         # per fix: outside the terminal zones

    @property
    def reroutes(self) -> list[Episode]:
        return [e for e in self.episodes if e.reroute]

    @property
    def offroute_m(self) -> float:
        return float(sum(e.actual_m for e in self.episodes))

    @property
    def offroute_s(self) -> float:
        return float(sum(e.duration_s for e in self.episodes))

    @property
    def adherence_pct(self) -> float | None:
        if self.judged is None or not self.judged.any():
            return None
        return round(100.0 * float(self.on_route[self.judged].mean()), 1)


# (lat, lon, dest_lat, dest_lon) -> (lat array, lon array, distance_m, duration_s) or None
Router = Callable[[float, float, float, float], "tuple | None"]


def detect(t, lat, lon, odo_m, plan: Polyline, cfg: DeviationConfig = DeviationConfig(),
           stops: list[tuple[float, float]] = (), router: Router | None = None,
           destination: tuple[float, float] | None = None) -> Result:
    """Run the off-route state machine over a trip's transit fixes.

    `t` epoch seconds, `lat`/`lon` fitted positions, `odo_m` the trip's
    odometer at each fix (metres), `plan` the route it should follow,
    `stops` (start, end) epoch pairs of its standstills. With a `router`,
    a confirmed deviation asks it for a new route from the truck to
    `destination` and continues against that, as a navigation app would.
    """
    t = np.asarray(t, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    odo = np.asarray(odo_m, dtype=np.float64)
    n = len(t)
    res = Result(plans=[plan])
    if n == 0 or len(plan) < 2:
        res.offset_m = np.zeros(n)
        res.on_route = np.ones(n, dtype=bool)
        res.judged = np.zeros(n, dtype=bool)
        return res
    dest = destination or (float(plan.lat[-1]), float(plan.lon[-1]))
    total = float(odo[-1] - odo[0])
    judged = ((odo - odo[0]) >= cfg.terminal_m) & ((odo[-1] - odo) >= cfg.terminal_m)
    if total < 3 * cfg.terminal_m:
        judged[:] = False                      # a trip this short is all terminal zone

    dist, chain, _ = plan.project(lat, lon)
    offset = dist.copy()
    on = np.ones(n, dtype=bool)
    stop_arr = np.array(stops, dtype=np.float64).reshape(-1, 2) if len(stops) else np.zeros((0, 2))

    def stop_seconds(a: float, b: float) -> float:
        if not len(stop_arr):
            return 0.0
        lo = np.maximum(stop_arr[:, 0], a)
        hi = np.minimum(stop_arr[:, 1], b)
        return float(np.clip(hi - lo, 0, None).sum())

    state_off = False
    last_on = 0
    cand: int | None = None
    back: int | None = None
    ep_max = 0.0
    plan_i = 0
    i = 0
    while i < n:
        d = dist[i] if judged[i] else 0.0
        if not state_off:
            if d <= cfg.off_m:
                last_on, cand = i, None
            else:
                if cand is None:
                    cand = i
                held = t[i] - t[cand]
                if d >= cfg.escape_m or (held >= cfg.confirm_s and i > cand):
                    state_off, back, ep_max = True, None, float(dist[cand:i + 1].max())
                    on[cand:i + 1] = False
                    new = router(float(lat[i]), float(lon[i]), dest[0], dest[1]) if router else None
                    if new is not None:
                        # Rerouted: the old plan from where the truck left, against
                        # what it has driven since plus the new plan from here.
                        nlat, nlon, new_m, new_s = new
                        old_left = res.plans[-1].length_m - float(chain[last_on])
                        driven = float(odo[i] - odo[last_on])
                        penalty = driven + float(new_m) - old_left
                        res.episodes.append(Episode(
                            "reroute", last_on, i, float(t[last_on]), float(t[i]), float(chain[last_on]), None,
                            driven, old_left, penalty, ep_max,
                            stop_seconds(float(t[last_on]), float(t[i])),
                            bool(np.any(np.diff(t[last_on:i + 1]) > cfg.gap_s)),
                            {"at_lat": float(lat[i]), "at_lon": float(lon[i]), "old_remaining_m": old_left,
                             "new_route_m": float(new_m), "new_route_s": float(new_s), "driven_off_m": driven},
                            plan_i))
                        line = Polyline(nlat, nlon)
                        res.plans.append(line)
                        plan_i += 1
                        d2, c2, _ = line.project(lat[i:], lon[i:])
                        dist[i:], chain[i:] = d2, c2
                        offset[i:] = d2
                        state_off, last_on, cand = False, i, None
                        on[i] = True
        else:
            ep_max = max(ep_max, float(dist[i]))
            if d <= cfg.on_m:
                if back is None:
                    back = i
                if i > back or not judged[i]:
                    res.episodes.append(_close(cfg, t, odo, chain, last_on, back, ep_max, stop_seconds, plan_i))
                    on[back:i + 1] = True
                    state_off, last_on, cand, back = False, i, None, None
            else:
                back = None
                on[i] = False
        i += 1
    if state_off:
        res.episodes.append(_close(cfg, t, odo, chain, last_on, None, ep_max, stop_seconds, plan_i,
                                   plan_len=res.plans[-1].length_m))
    res.offset_m = offset
    res.on_route = on
    res.judged = judged
    return res


def _close(cfg: DeviationConfig, t, odo, chain, leave: int, back: int | None, max_off: float,
           stop_seconds, plan_i: int, plan_len: float | None = None) -> Episode:
    t_leave = float(t[leave])
    if back is None:
        # Never came back: from where it left to where the trail ends, against
        # the rest of the plan.
        end = len(t) - 1
        actual = float(odo[end] - odo[leave])
        planned = max(0.0, (plan_len or 0.0) - float(chain[leave]))
        return Episode("alternate_route", leave, None, t_leave, float(t[end]), float(chain[leave]), None,
                       actual, planned, actual - planned, max_off, stop_seconds(t_leave, float(t[end])),
                       bool(np.any(np.diff(t[leave:end + 1]) > cfg.gap_s)), None, plan_i)
    t_back = float(t[back])
    actual = float(odo[back] - odo[leave])
    progress = float(chain[back] - chain[leave])
    stopped = stop_seconds(t_leave, t_back)
    if progress < -cfg.near_m:
        kind = "backtrack"
    elif progress <= cfg.near_m:
        kind = "off_route_stop" if stopped >= cfg.long_stop_s else "excursion"
    elif float(odo[-1] - odo[back]) < cfg.terminal_m:
        kind = "alternate_route"          # back only at the destination: another road all the way
    elif actual - progress < -max(500.0, 0.03 * progress):
        kind = "shortcut"                 # rejoined further along, having driven less than the plan
    else:
        kind = "detour"
    planned = max(0.0, progress)
    return Episode(kind, leave, back, t_leave, t_back, float(chain[leave]), float(chain[back]),
                   actual, planned, actual - planned, max_off, stopped,
                   bool(np.any(np.diff(t[leave:back + 1]) > cfg.gap_s)), None, plan_i)
