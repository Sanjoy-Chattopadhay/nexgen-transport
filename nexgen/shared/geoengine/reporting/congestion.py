"""Plant congestion: who was inside a plant at any moment, and when it overloaded.

What this answers
---------------------------------------------------------------------------
"From 09:38 on 5 October HSM GATE held 6 or more trucks for 86 minutes, 9 at
the worst; when it is in use it holds at most 5 for nine minutes in ten." The
physical ledger (geo_pvisit) already holds every vehicle's stay in every fence,
once however many consignments saw it. This module reads it as a population
over time instead of a list of stays:

    occupancy    how many vehicles a fence held at each moment (a step function)
    usual level  what the fence holds while it is in use (time-weighted p90)
    episodes     stretches when it held more than that, for long enough
    scenes       episodes at overlapping fences that are the same crowd, once

Nothing here is stored: it is computed from the published run on read and
cached per data version (api/v1/plants.py), so it can never disagree with the
ledger it is made from.

Plants and zones
---------------------------------------------------------------------------
The fence master has no "plant", only fences, and the works overlap themselves:
the Jamshedpur works alone is drawn as TWS-JAMSHEDPUR (7.1 km2), TATA STEEL JSR
(6.9 and 5.9 km2), INSIDE TSL (6.6 km2), TWS (6.1 km2) and TISCO HSM GATE
(3.5 km2). A *plant* is therefore an outermost facility fence -- one whose
centroid lies inside no larger facility fence that saw traffic -- which is the
same rule the trip timeline uses to name a place (pipeline/runner.places).
Every visited facility fence inside it is a *zone* of that plant.

A zone's kind comes from its name, the way Smart-Truck's in-plant delay
analyser names its stations (analytics/lib/tta_plant_delay.py), but from an
ordered rule list in config (geofence.congestion.zone_kinds) and matched on
whole words, so 'WB' cannot fire inside another word. Campus-scale fences are
sub-areas whatever their name: TISCO HSM GATE is a 3.5 km2 gate *district*,
not a gate.

Identical polygons
---------------------------------------------------------------------------
TWS-JAMSHEDPUR is stored three times (sites 7168, 7043, 4270) and four sites
share one INSIDE TSL / HSM MILL polygon. The same polygon with the same declared
tolerance produces the same visits, so those copies are one *ground*: counted
once, named by the lowest site id, with the others listed. Different polygons
of the same place are not merged here -- HSM GATE is drawn twice, 8,227 m2 and
6,839 m2, and each is counted as drawn; the scene merge below is what reports
their one crowd once.

Occupancy
---------------------------------------------------------------------------
A vehicle is inside from its entry up to, not including, its exit:
``enter <= t < exit``. An open stay (the trail ended inside) runs to its last
fix inside, which is all that is known. A stay of zero seconds (a single fix
inside) holds no time and is never counted. Every count is of *tracked*
vehicles -- a truck whose trip carries no GPS is invisible here -- and the
figures say how many vehicles were being tracked at that moment.

The usual level
---------------------------------------------------------------------------
Busy is judged against the fence itself, over the time it held at least one
vehicle. Over all time would be wrong twice: a gate empty every night would
look idle, and the run's first days -- GPS from 29 September, the Jamshedpur
trips only from 3 October -- would read as an empty plant rather than one not
yet observed. The usual level is the occupancy the fence was at or below for
`usual_percentile` (90%) of its busy time, weighted by time, not by visit.

Episodes
---------------------------------------------------------------------------
An overload is a stretch at or above the fence's threshold:

    the capacity the client set for the site (tenant `plant_capacity`), else
    max(the minimum for the zone's kind,
        usual level + max(1, ceil(usual level x margin)))

lasting at least `min_minutes`; stretches less than `merge_gap_minutes` apart
are one episode (a gate dipping below the line for two minutes did not clear).
The usual level needs `min_busy_hours` of busy time to mean anything; below
that only the kind's minimum applies and the episode says its baseline is thin.

The margin (20%) is what keeps "overloaded" meaning something on large
fences. With "usual + 1" alone, TISCO HSM GATE -- a 3.5 km2 works district
usually holding 77 tracked vehicles -- was "overloaded" at 81, four trucks over
a normal night; a gate usually holding 4 still trips at 5.

Measured on run 1 (3-5 October): HSM GATE (usual 4, threshold 5) held 5 or
more from 09:38 to 11:18 on 5 October, 8 at the worst; SLAG GATE (usual 3)
held 6 for 38 minutes from 01:26 on 4 October.

What an episode cost
---------------------------------------------------------------------------
Trucks that arrived during the episode and whose stay was fully observed
(entry seen, exit seen) are compared with every fully observed stay at the
fence: the median time inside against the usual median. Stays whose entry or
exit lies in a GPS gap longer than `uncertain_gap_s` are counted and shown:
their times are known only to within that gap.

Scenes
---------------------------------------------------------------------------
The master often draws one place more than once with different polygons:
HSM GATE three times (Home site 8,227 m2, Client site 6,839 m2, Other site),
TATA STEEL JSR twice. Their overloads are one crowd and must be reported once.
Two episodes in one plant are one scene when

    their fences are the same place -- one's centroid inside the other and
    the larger at most `same_place_area_ratio` (2.5x) the smaller --
    they overlap in time, and
    at least `scene_overlap` (half) of the smaller crowd is in the larger.

The place test is what stops a gate's queue being swallowed by the works it
sits in: on vehicles alone a works-wide crowd overlaps every zone crowded
inside it, and the first version of this folded 19 episodes across the
Jamshedpur works into one "scene". A scene is named by its smallest fence (the
innermost place that holds the crowd, as everywhere else the smallest fence is
primary) and lists the others. Episodes are never summed across fences: a
scene's peak is its primary fence's peak.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from nexgen.shared.geoengine.store import scale_of

FACILITY = ("micro", "site", "campus")
ZONE_KINDS = ("gate", "weighbridge", "parking", "loading", "road", "area")

# The shipped values of geofence.congestion in config/services.yaml, used only
# where the configuration leaves a key out (tests, an older services.yaml).
DEFAULTS: dict = {
    "min_minutes": 15,
    "merge_gap_minutes": 10,
    "usual_percentile": 90,
    "min_busy_hours": 6,
    "uncertain_gap_s": 600,
    "margin": 0.2,
    "scene_overlap": 0.5,
    "same_place_area_ratio": 2.5,
    "window_days": 7,
    "min_vehicles": {"plant": 10, "area": 8, "gate": 4, "weighbridge": 3, "parking": 6, "loading": 6, "road": 4},
    "capacity": {},
    "zone_kinds": [
        {"kind": "weighbridge", "words": ["WEIGHBRIDGE", "WEIGH BRIDGE", "WEIGHING", "WB", "W/B", "KANTA",
                                          "DHARAM KANTA", "DHARMKANTA"]},
        {"kind": "parking", "words": ["PARKING", "YARD", "TRANSPORT NAGAR", "TPT NAGAR", "HOLDING"]},
        {"kind": "area", "words": ["INSIDE", "IN SIDE"]},
        {"kind": "gate", "words": ["GATE", "CHECK POST", "CHECKPOST", "BARRIER", "OUT POINT"]},
        {"kind": "loading", "words": ["LOADING", "UNLOADING", "DOCK", "BAY", "SHED", "SIDING", "WHARF"]},
        {"kind": "road", "words": ["ROAD", "SIGNAL", "TURNING", "CROSSING", "HIGHWAY"]},
    ],
}

KIND_NOUN = {"plant": "Plant crowding", "area": "Crowding", "gate": "Gate overloading",
             "weighbridge": "Weighbridge queue", "parking": "Parking full", "loading": "Loading point crowding",
             "road": "Internal road congestion"}


def settings() -> dict:
    """geofence.congestion from services.yaml over the shipped defaults, and the
    client's known capacities (tenant setting `plant_capacity`, by site id).

    The thresholds are how the scan judges any fence and stay system-wide; a
    capacity is a fact about one client's gate, so it lives with the client's
    settings and can be changed from the Admin page.
    """
    try:
        from nexgen.core.config import get_config
        got = get_config().setting("geofence", "congestion", {}) or {}
    except Exception:                       # no configuration (a bare test)
        got = {}
    try:
        from nexgen.core import tenancy
        capacity = tenancy.setting("plant_capacity", {}) or {}
    except Exception:
        capacity = {}
    out = {**DEFAULTS, **got}
    out["min_vehicles"] = {**DEFAULTS["min_vehicles"], **(got.get("min_vehicles") or {})}
    out["capacity"] = {int(k): int(v) for k, v in capacity.items() if v}
    return out


# ---------------------------------------------------------------------------
# zones
# ---------------------------------------------------------------------------

def _rules(zone_kinds: list[dict]) -> list[tuple[str, re.Pattern]]:
    out = []
    for rule in zone_kinds:
        words = sorted((w.strip().upper() for w in rule.get("words") or [] if w.strip()), key=len, reverse=True)
        if not words:
            continue
        alt = "|".join(re.escape(w) for w in words)
        # Whole words: bounded by anything that is not a letter or digit, so
        # "YARD-JMD" and "GATE_LOCAL" match and "KANTADI" does not.
        out.append((rule["kind"], re.compile(rf"(?<![A-Z0-9])(?:{alt})(?![A-Z0-9])")))
    return out


def zone_kind(name: str | None, scale: str, zone_kinds: list[dict] | None = None) -> str:
    """What a fence inside a plant is, from its name. First rule wins."""
    if scale == "campus":
        return "area"
    upper = (name or "").upper()
    for kind, rx in _rules(zone_kinds if zone_kinds is not None else DEFAULTS["zone_kinds"]):
        if rx.search(upper):
            return kind
    return "area"


# ---------------------------------------------------------------------------
# stays and occupancy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stay:
    """One physical visit (a geo_pvisit row), as occupancy needs it."""
    id: int
    vehicle: str
    enter: datetime
    end: datetime              # the exit, or the last fix inside for an open stay
    open: bool = False
    entry_observed: bool = True
    enter_gap_s: int = 0
    exit_gap_s: int = 0
    trip_no: int | None = None
    trips: str | None = None
    transporter: str | None = None
    driver: str | None = None

    @property
    def seconds(self) -> int:
        return int((self.end - self.enter).total_seconds())

    @property
    def measured(self) -> bool:
        """Entry and exit both seen: the stay's length is known, not a bound."""
        return self.entry_observed and not self.open

    def inside(self, t: datetime) -> bool:
        return self.enter <= t < self.end

    def overlaps(self, a: datetime, b: datetime) -> bool:
        return self.enter < b and self.end > a

    def uncertain(self, gap_s: int) -> bool:
        return max(self.enter_gap_s or 0, self.exit_gap_s or 0) > gap_s


class Occupancy:
    """How many vehicles a set of stays holds over time: a step function.

    `times[i]` is when the count changed to `counts[i]`; it holds until
    `times[i+1]`. Every stay ends, so the last count is 0.
    """

    def __init__(self, stays: list[Stay]):
        delta: dict[datetime, int] = defaultdict(int)
        for s in stays:
            if s.end > s.enter:
                delta[s.enter] += 1
                delta[s.end] -= 1
        self.times: list[datetime] = []
        self.counts: list[int] = []
        n = 0
        for t in sorted(delta):
            if delta[t] == 0:
                continue
            n += delta[t]
            self.times.append(t)
            self.counts.append(n)

    @property
    def first(self) -> datetime | None:
        return self.times[0] if self.times else None

    @property
    def last(self) -> datetime | None:
        return self.times[-1] if self.times else None

    def at(self, t: datetime) -> int:
        i = bisect_right(self.times, t) - 1
        return self.counts[i] if i >= 0 else 0

    def segments(self, a: datetime | None = None, b: datetime | None = None):
        """(start, end, count) pieces covering [a, b), clipped to it."""
        if not self.times:
            return
        a = a or self.times[0]
        b = b or self.times[-1]
        if b <= a:
            return
        level = self.at(a)
        t = a
        i = bisect_right(self.times, a)         # the first change after `a`
        while t < b:
            nxt = self.times[i] if i < len(self.times) else b
            nxt = min(nxt, b)
            if nxt > t:
                yield t, nxt, level
            if i >= len(self.times) or self.times[i] >= b:
                break
            t = self.times[i]
            level = self.counts[i]
            i += 1

    def level_seconds(self, a: datetime | None = None, b: datetime | None = None) -> dict[int, float]:
        """Seconds spent at each count within [a, b)."""
        out: dict[int, float] = defaultdict(float)
        for s, e, n in self.segments(a, b):
            out[n] += (e - s).total_seconds()
        return dict(out)

    def peak(self, a: datetime | None = None, b: datetime | None = None) -> tuple[int, datetime | None]:
        """The highest count in [a, b) and the first moment it was reached."""
        best, when = 0, None
        for s, _e, n in self.segments(a, b):
            if n > best:
                best, when = n, s
        return best, when

    def runs(self, threshold: int, a: datetime | None = None, b: datetime | None = None):
        """Maximal stretches at or above `threshold`: (start, end, peak, peak_at)."""
        out: list[list] = []
        for s, e, n in self.segments(a, b):
            if n >= threshold:
                if out and out[-1][1] == s:
                    r = out[-1]
                    r[1] = e
                    if n > r[2]:
                        r[2], r[3] = n, s
                else:
                    out.append([s, e, n, s])
        return [tuple(r) for r in out]


def weighted_percentile(level_seconds: dict[int, float], q: float) -> int | None:
    """The smallest level at or below which `q`% of the time was spent."""
    total = sum(level_seconds.values())
    if total <= 0:
        return None
    acc = 0.0
    for level in sorted(level_seconds):
        acc += level_seconds[level]
        if acc / total * 100 >= q - 1e-9:
            return level
    return max(level_seconds)


@dataclass
class Baseline:
    """What a fence holds while it is in use."""
    usual: int | None           # time-weighted percentile over busy time
    busy_s: float               # time with at least one vehicle inside
    levels: dict[int, float]    # seconds at each count >= 1
    thin: bool                  # too little busy time for `usual` to mean anything
    stay_p50_s: float | None    # median fully observed stay, seconds
    stays_measured: int


def baseline(occ: Occupancy, stays: list[Stay], cfg: dict) -> Baseline:
    levels = {n: s for n, s in occ.level_seconds().items() if n > 0}
    busy = sum(levels.values())
    measured = [s.seconds for s in stays if s.measured and s.seconds > 0]
    return Baseline(usual=weighted_percentile(levels, float(cfg["usual_percentile"])), busy_s=busy, levels=levels,
                    thin=busy < float(cfg["min_busy_hours"]) * 3600,
                    stay_p50_s=float(median(measured)) if measured else None, stays_measured=len(measured))


def threshold(kind: str, base: Baseline, cfg: dict, site_id: int | None = None) -> tuple[int, str]:
    """The count at which a fence is overloaded, and where that number came from."""
    cap = cfg["capacity"].get(int(site_id)) if site_id is not None else None
    if cap:
        return int(cap), "capacity"
    floor = int(cfg["min_vehicles"].get(kind, cfg["min_vehicles"]["area"]))
    if base.thin or base.usual is None:
        return floor, "minimum (baseline thin)"
    over = base.usual + max(1, math.ceil(base.usual * float(cfg["margin"]) - 1e-9))
    if over >= floor:
        return over, "above usual"
    return floor, "minimum"


# ---------------------------------------------------------------------------
# episodes
# ---------------------------------------------------------------------------

@dataclass
class Episode:
    ground_id: int                  # the canonical fence id of the ground it happened in
    start: datetime
    end: datetime
    peak: int
    peak_at: datetime
    threshold: int
    threshold_basis: str
    usual: int | None
    vehicles: frozenset[str]        # every vehicle inside at some point during it
    arrivals: int                   # entries observed during it
    wait_p50_s: float | None        # median fully observed stay of those arrivals, seconds
    waits_measured: int
    usual_stay_p50_s: float | None
    uncertain: int                  # stays overlapping it whose entry or exit lies in a long GPS gap
    open_stays: int                 # stays overlapping it whose trail ended inside

    @property
    def minutes(self) -> int:
        return int(round((self.end - self.start).total_seconds() / 60))


def episodes(ground_id: int, occ: Occupancy, stays: list[Stay], base: Baseline, thr: tuple[int, str], cfg: dict,
             a: datetime | None = None, b: datetime | None = None) -> list[Episode]:
    """Overloads of one fence: runs at or above its threshold, close ones merged,
    short ones dropped."""
    level, basis = thr
    gap = timedelta(minutes=float(cfg["merge_gap_minutes"]))
    shortest = timedelta(minutes=float(cfg["min_minutes"]))
    merged: list[list] = []
    for s, e, pk, pk_at in occ.runs(level, a, b):
        if merged and s - merged[-1][1] < gap:
            m = merged[-1]
            m[1] = e
            if pk > m[2]:
                m[2], m[3] = pk, pk_at
        else:
            merged.append([s, e, pk, pk_at])
    out = []
    for s, e, pk, pk_at in merged:
        if e - s < shortest:
            continue
        present = [x for x in stays if x.overlaps(s, e) and x.end > x.enter]
        arrived = [x for x in stays if x.entry_observed and s <= x.enter < e]
        waits = [x.seconds for x in arrived if x.measured and x.seconds > 0]
        out.append(Episode(
            ground_id=ground_id, start=s, end=e, peak=pk, peak_at=pk_at, threshold=level, threshold_basis=basis,
            usual=base.usual, vehicles=frozenset(x.vehicle for x in present), arrivals=len(arrived),
            wait_p50_s=float(median(waits)) if waits else None, waits_measured=len(waits),
            usual_stay_p50_s=base.stay_p50_s,
            uncertain=sum(1 for x in present if x.uncertain(int(cfg["uncertain_gap_s"]))),
            open_stays=sum(1 for x in present if x.open)))
    return out


# ---------------------------------------------------------------------------
# scenes
# ---------------------------------------------------------------------------

@dataclass
class Scene:
    plant_id: int                   # the plant's canonical fence id
    primary: Episode                # at the smallest fence in the scene
    members: list[Episode] = field(default_factory=list)   # every episode in it, primary included

    @property
    def start(self) -> datetime:
        return min(e.start for e in self.members)

    @property
    def end(self) -> datetime:
        return max(e.end for e in self.members)


def shared(a: frozenset, b: frozenset) -> float:
    """The share of the smaller crowd that is also in the larger one."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def same_place(g: "Ground", h: "Ground", ratio: float) -> bool:
    """Two polygons of one place: similar size, one's centroid inside the other."""
    small, big = (g, h) if g.area <= h.area else (h, g)
    if small.area <= 0 or big.area / small.area > ratio:
        return False
    return (big.fence.contains(small.fence.centroid_lat, small.fence.centroid_lon)
            or small.fence.contains(big.fence.centroid_lat, big.fence.centroid_lon))


def scenes(plant_id: int, eps: list[Episode], grounds: dict[int, "Ground"], cfg: dict) -> list[Scene]:
    """One plant's episodes folded into scenes: the same place drawn twice,
    overlapping in time, mostly the same vehicles. Transitive."""
    n = len(eps)
    parent = list(range(n))
    overlap, ratio = float(cfg["scene_overlap"]), float(cfg["same_place_area_ratio"])

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(n), key=lambda i: eps[i].start)
    for x, i in enumerate(order):
        for j in order[x + 1:]:
            if eps[j].start >= eps[i].end:
                break
            gi, gj = grounds.get(eps[i].ground_id), grounds.get(eps[j].ground_id)
            if (gi is not None and gj is not None and gi is not gj and same_place(gi, gj, ratio)
                    and shared(eps[i].vehicles, eps[j].vehicles) >= overlap):
                parent[find(i)] = find(j)
    groups: dict[int, list[Episode]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(eps[i])
    out = []
    for members in groups.values():
        members.sort(key=lambda e: (grounds[e.ground_id].area if e.ground_id in grounds else 0.0, e.start))
        out.append(Scene(plant_id=plant_id, primary=members[0], members=members))
    out.sort(key=lambda s: s.start)
    return out


def severity(ep: Episode) -> str:
    """high: the fence held half again its usual level for an hour or more, or
    the trucks that came through it waited twice the usual time."""
    usual = max(ep.usual or 0, 1)
    long_and_full = ep.peak >= 1.5 * usual and ep.minutes >= 60
    slow = (ep.wait_p50_s is not None and ep.usual_stay_p50_s and ep.waits_measured >= 3
            and ep.wait_p50_s >= 2 * ep.usual_stay_p50_s)
    return "high" if (long_and_full or slow) else "moderate"


def score(ep: Episode) -> float:
    """Ranks scenes: vehicles over the line, for how long, and the wait it cost."""
    over = max(1, ep.peak - ep.threshold + 1)
    wait = 1.0
    if ep.wait_p50_s and ep.usual_stay_p50_s:
        wait = max(1.0, ep.wait_p50_s / ep.usual_stay_p50_s)
    return round(over * (ep.minutes / 60.0) * wait, 3)


def fmt_when(t: datetime) -> str:
    """'Sun 5 Oct 09:38' -- no %-d, which Windows' strftime lacks."""
    return f"{t:%a} {t.day} {t:%b %H:%M}"


def fmt_minutes(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m = int(math.floor(seconds / 60 + 0.5))      # half up, as the page rounds: Python's round() is half-even
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60:02d} min"


def _clock(t: datetime, ref: datetime) -> str:
    """A time, with its day only where it differs from `ref`'s."""
    return f"{t:%H:%M}" if t.date() == ref.date() else fmt_when(t)


def headline(kind: str, zone: str, plant: str, ep: Episode) -> str:
    """The scene in plain sentences, every number from the episode."""
    where = zone if zone == plant else f"{zone} ({plant})"
    text = f"{KIND_NOUN.get(kind, 'Crowding')} at {where}: {ep.peak} vehicles at once at {fmt_when(ep.peak_at)}"
    if ep.usual is not None:
        text += f"; it usually holds no more than {ep.usual} (nine minutes in ten while in use)"
    text += (f". {ep.threshold} or more from {_clock(ep.start, ep.peak_at)} to {_clock(ep.end, ep.start)} "
             f"({fmt_minutes((ep.end - ep.start).total_seconds())})")
    if ep.wait_p50_s is not None and ep.usual_stay_p50_s and ep.waits_measured == 1:
        text += (f"; the one truck that arrived in that time stayed {fmt_minutes(ep.wait_p50_s)} against "
                 f"{fmt_minutes(ep.usual_stay_p50_s)} usually")
    elif ep.wait_p50_s is not None and ep.usual_stay_p50_s and ep.waits_measured:
        text += (f"; the {ep.waits_measured} that arrived in that time stayed a median "
                 f"{fmt_minutes(ep.wait_p50_s)} against {fmt_minutes(ep.usual_stay_p50_s)} usually")
    return text + "."


# ---------------------------------------------------------------------------
# grounds and plants
# ---------------------------------------------------------------------------

def ground_key(f) -> tuple:
    """Fences drawn on exactly this polygon with this tolerance detect the same visits."""
    return (round(f.area_m2), round(f.centroid_lat, 6), round(f.centroid_lon, 6), f.n_vertices,
            round(float(f.tolerance_m or 0), 1))


@dataclass
class Ground:
    """One polygon on the ground: a fence and every identical copy of it."""
    fence: object               # the copy with the lowest site id
    copies: list                # every copy, the canonical one first
    kind: str = "area"          # 'plant' for a plant's own polygon, else its zone kind
    scale: str = "site"

    @property
    def id(self) -> int:
        return self.fence.fence_id

    @property
    def area(self) -> float:
        return self.fence.area_m2


@dataclass
class Plant:
    ground: Ground
    zones: list[Ground] = field(default_factory=list)

    @property
    def id(self) -> int:
        return self.ground.id


def grounds_of(fences: list) -> list[Ground]:
    """Identical polygons folded together; facility scales only."""
    groups: dict[tuple, list] = defaultdict(list)
    for f in fences:
        sc = scale_of(f)
        if sc in FACILITY:
            groups[ground_key(f)].append(f)
    out = []
    for copies in groups.values():
        copies.sort(key=lambda f: f.site_id)
        out.append(Ground(fence=copies[0], copies=copies, scale=scale_of(copies[0])))
    return out


def layout(grounds: list[Ground], index, zone_kinds: list[dict]) -> list[Plant]:
    """Plants (outermost grounds) and the zones inside each.

    `grounds` are the visited facility grounds; `index` answers which fences
    contain a point (the engine's own index, so nesting is decided by the same
    ray cast the detector used). A ground belongs to the largest visited
    ground containing its centroid; one inside none is a plant.
    """
    by_fence: dict[int, Ground] = {}
    for g in grounds:
        for f in g.copies:
            by_fence[f.fence_id] = g
    owner: dict[int, Ground] = {}
    for g in grounds:
        best = None
        for f in index.query_point(g.fence.centroid_lat, g.fence.centroid_lon):
            h = by_fence.get(f.fence_id)
            if h is None or h is g or h.area <= g.area:
                continue
            if not f.contains(g.fence.centroid_lat, g.fence.centroid_lon):
                continue
            if best is None or h.area > best.area or (h.area == best.area and h.id < best.id):
                best = h
        if best is not None:
            owner[g.id] = best
    # A container may itself sit inside a bigger one: climb to the top.
    def top(g: Ground) -> Ground:
        seen = set()
        while g.id in owner and g.id not in seen:
            seen.add(g.id)
            g = owner[g.id]
        return g
    plants: dict[int, Plant] = {}
    for g in grounds:
        if g.id not in owner:
            g.kind = "plant"
            plants.setdefault(g.id, Plant(ground=g))
    for g in grounds:
        if g.id in owner:
            p = plants[top(g).id]
            g.kind = zone_kind(g.fence.name, g.scale, zone_kinds)
            p.zones.append(g)
    order = {k: i for i, k in enumerate(ZONE_KINDS)}
    for p in plants.values():
        p.zones.sort(key=lambda z: (order.get(z.kind, 99), z.area))
    return list(plants.values())


# ---------------------------------------------------------------------------
# who is where at a moment
# ---------------------------------------------------------------------------

def innermost(vehicle: str, t: datetime, zone_stays: dict[int, dict[str, list[Stay]]],
              zones: list[Ground]) -> tuple[Ground | None, Stay | None]:
    """The smallest zone a vehicle was inside at `t`, with that stay."""
    best, best_stay = None, None
    for z in zones:
        for s in zone_stays.get(z.id, {}).get(vehicle, ()):
            if s.inside(t) and (best is None or z.area < best.area):
                best, best_stay = z, s
                break
    return best, best_stay


def resolution_minutes(span: timedelta) -> int:
    """Bucket size that keeps a timeline under ~600 points."""
    hours = span.total_seconds() / 3600
    for minutes in (5, 10, 15, 30, 60, 120, 360):
        if hours * 60 / minutes <= 600:
            return minutes
    return 1440
