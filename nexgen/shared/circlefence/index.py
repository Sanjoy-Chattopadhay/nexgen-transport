"""Uniform-grid spatial hash over circular geofences.

The problem this solves
-----------------------
`tta_trip_gps` holds ~3.5M pings and there are ~100+ facilities. Asking
"which fence, if any, is this ping inside?" the obvious way costs one
haversine per (ping, fence) pair -- 350M+ trig calls, which is minutes to
hours of pure Python and gets worse every time a fence is added.

The fix
-------
Chop the world into fixed-size square cells and file every fence into the
cells its circle touches. Then a ping's cell is plain integer division (no
trig at all), and the cell either has candidate fences or it does not::

    cell = (floor(lat / CELL), floor(lon / CELL))
    candidates = buckets.get(cell)      # one dict lookup
    if candidates is None: -> outside every fence, done.

On a Jamshedpur->Delhi run essentially every ping is on a highway far from
any facility, so the dict lookup rejects it outright and the haversine is
never reached. Only the handful of pings that land in a cell touching a
fence pay for a distance check.

Exactness
---------
This is a filter, not an approximation. A fence is registered into *every*
cell its bounding box overlaps, so a point inside the circle is guaranteed
to hash to a cell that lists it -- there are no false negatives. False
positives (right cell, outside the circle) are removed by the haversine
that follows. The answer is identical to the brute-force scan; there is a
test that asserts exactly that.

Cell sizing
-----------
Cell width tracks the *median* fence radius, never below `MIN_CELL_DEG` --
deliberately not the largest. The fence set here is bimodal: most facilities
are 1-3 km points, but a works complex like Jamshedpur is a 15 km fence
covering a whole industrial belt. Sizing cells off that outlier would make
every cell ~30 km wide, so the hundred small fences would pile into a handful
of buckets and the filter would stop filtering.

Sizing off the median instead keeps buckets short, and the cost is only that
the few oversized fences register into more cells each -- tens of extra dict
entries, paid once at build time. Correctness is unaffected either way, since
a fence is always registered across its full bounding box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6371008.8

# ~5.5 km of latitude. Below this the index bloats without filtering better,
# because facility fences are kilometre-scale to begin with.
MIN_CELL_DEG = 0.05

# Degrees of latitude per metre. Latitude degrees are near enough constant.
_DEG_PER_M_LAT = 1.0 / 110_574.0

# Guard for the 1/cos(lat) longitude blow-up near the poles. India sits at
# 8-37N so this never binds in practice, but an out-of-range coordinate from a
# corrupt ping must not produce an infinite bounding box.
_MIN_COS_LAT = 0.01


@dataclass(frozen=True, slots=True)
class Geofence:
    """A circular fence. `radius_m` is an inclusive boundary.

    `source` matters to anyone interpreting a distance measured against this
    fence. An 'anchor' fence is plant-precise -- it is the medoid of pings
    actually observed at the facility, so a 30 km gap to it means the truck
    really was 30 km away. A 'gazetteer' fence is a *town centroid* standing in
    for an anchor that failed its sanity check, so the delivery point may
    legitimately sit tens of km from the centre and a 30 km gap means "we do
    not know precisely where this customer is", not "the truck did not
    arrive". Reports must not conflate the two.
    """

    fence_id: int
    key: str          # upper(trim(node name)) -- join key back to trip rows
    name: str
    role: str         # origin | destination | yard
    lat: float
    lon: float
    radius_m: float
    source: str = "anchor"   # anchor | anchor_unverified | gazetteer | manual


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def normalise_key(name: str | None) -> str:
    """Node names arrive with inconsistent case and padding across the two
    feeds; fold them so one facility resolves to one fence."""
    return (name or "").strip().upper()


class GeofenceIndex:
    """Point-in-fence lookup in O(1) expected time.

    Build once, query millions of times::

        idx = GeofenceIndex(fences)
        for ping in pings:
            hits = idx.locate(ping.lat, ping.lon)   # tuple of Geofence
    """

    __slots__ = ("_buckets", "_by_id", "_by_key", "_cell_deg", "_fences", "stats")

    def __init__(self, fences: list[Geofence], cell_deg: float | None = None):
        self._fences = list(fences)
        self._by_id = {f.fence_id: f for f in self._fences}

        # key -> role -> fence, so by_key() is a lookup rather than a scan.
        self._by_key: dict[str, dict[str, Geofence]] = {}
        for f in self._fences:
            self._by_key.setdefault(f.key, {})[f.role] = f

        if cell_deg is None:
            # Median, not max -- see the "Cell sizing" note in the module
            # docstring. One oversized fence must not inflate every cell.
            radii = sorted(f.radius_m for f in self._fences)
            med_r = radii[len(radii) // 2] if radii else 0.0
            cell_deg = max(MIN_CELL_DEG, 2.0 * med_r * _DEG_PER_M_LAT)
        self._cell_deg = cell_deg

        buckets: dict[tuple[int, int], list[Geofence]] = {}
        for f in self._fences:
            for cell in self._cells_covering(f):
                buckets.setdefault(cell, []).append(f)
        # Freeze to tuples: smaller, and makes it plain the index is read-only
        # once built (callers iterate it in hot loops).
        self._buckets: dict[tuple[int, int], tuple[Geofence, ...]] = {
            c: tuple(v) for c, v in buckets.items()
        }

        self.stats = {
            "fences": len(self._fences),
            "cells": len(self._buckets),
            "cell_deg": round(self._cell_deg, 6),
            "cell_km_approx": round(self._cell_deg * 110.574, 2),
            "max_fences_per_cell": max((len(v) for v in self._buckets.values()), default=0),
        }

    # -- construction helpers ------------------------------------------------

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        c = self._cell_deg
        return (math.floor(lat / c), math.floor(lon / c))

    def _cells_covering(self, f: Geofence):
        """Every cell the fence's bounding box overlaps.

        The box is padded by the radius converted to degrees -- and the
        longitude half-width is divided by cos(lat), because a degree of
        longitude is shorter than a degree of latitude away from the equator.
        Skipping that division would under-pad the box east-west and silently
        lose points near the fence's east and west edges.
        """
        dlat = f.radius_m * _DEG_PER_M_LAT
        cos_lat = max(abs(math.cos(math.radians(f.lat))), _MIN_COS_LAT)
        dlon = dlat / cos_lat

        lat_lo, lon_lo = self._cell(f.lat - dlat, f.lon - dlon)
        lat_hi, lon_hi = self._cell(f.lat + dlat, f.lon + dlon)
        for i in range(lat_lo, lat_hi + 1):
            for j in range(lon_lo, lon_hi + 1):
                yield (i, j)

    # -- queries -------------------------------------------------------------

    def locate(self, lat: float, lon: float) -> tuple[Geofence, ...]:
        """All fences containing the point. The empty tuple is the common case
        and costs a single dict lookup."""
        candidates = self._buckets.get(self._cell(lat, lon))
        if not candidates:
            return ()
        return tuple(
            f for f in candidates
            if haversine_m(lat, lon, f.lat, f.lon) <= f.radius_m
        )

    def inside(self, fence: Geofence, lat: float, lon: float) -> bool:
        """Point-in-one-known-fence. Used by the origin-exit scan, where the
        fence of interest is fixed and the bucket lookup buys nothing."""
        return haversine_m(lat, lon, fence.lat, fence.lon) <= fence.radius_m

    def get(self, fence_id: int) -> Geofence | None:
        return self._by_id.get(fence_id)

    def by_key(self, key: str | None, role: str | None = None) -> Geofence | None:
        """Resolve a trip's node name to a fence. `key` is normalised here so
        callers can pass the raw node name straight off the trip row."""
        roles = self._by_key.get(normalise_key(key))
        if not roles:
            return None
        if role is not None:
            return roles.get(role)
        # No role asked for: prefer a destination fence, then origin, then any.
        return roles.get("destination") or roles.get("origin") or next(iter(roles.values()))

    @property
    def fences(self) -> list[Geofence]:
        return list(self._fences)

    def __len__(self) -> int:
        return len(self._fences)
