"""The objects the engine works with.

A `Fence` is built once, at load time, and then read millions of times. Every
derived quantity that could be computed per query -- the metric projection of
the ring, the bounding box, the tolerance-expanded bounding box -- is computed
here instead and frozen onto the object. The hot path does no trigonometry and
allocates nothing per fence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from nexgen.shared.geoengine import geometry as G

# Distance stamped on a fix that is provably nowhere near a fence. Large enough
# to clear any escape threshold, finite so arithmetic on it stays well-defined.
FAR_M = 1.0e7


@dataclass(frozen=True, slots=True)
class Ping:
    ts: datetime
    lat: float
    lon: float
    speed: int | None = None


class Fence:
    """One compiled geofence: geometry plus the rules attached to it.

    `tolerance_m` is the site's declared outward buffer. It is applied as a
    shift of the containment threshold rather than by growing the polygon:
    buffering a ring properly (offsetting every edge and resolving the
    resulting self-intersections at concave corners) is an expensive and
    fiddly operation, and the signed distance already gives the same answer --
    a point is inside the buffered fence exactly when its signed distance to
    the original is below +tolerance.
    """

    __slots__ = (
        "fence_id", "site_id", "name", "site_type", "category",
        "max_speed", "tolerance_m", "active",
        "ring_lat", "ring_lon", "ring_x", "ring_y", "frame",
        "min_lat", "max_lat", "min_lon", "max_lon",
        "centroid_lat", "centroid_lon", "area_m2", "perimeter_m",
        "self_intersecting", "notes", "n_vertices",
        # Radius of the largest circle inside the ring, in metres (None when
        # not computed). What the adaptive hysteresis band is scaled against.
        "inradius_m",
        # Plain-Python copies of the ring for the scalar path. Indexing a
        # numpy array in a Python loop boxes a np.float64 per access, which
        # costs more than the arithmetic it feeds; tuples of floats do not.
        "_rx", "_ry", "_rlat", "_rlon", "_mx", "_my",
    )

    def __init__(
        self,
        fence_id: int,
        site_id: int,
        name: str,
        ring: list[tuple[float, float]],
        site_type: str | None = None,
        category: str = "normal",
        max_speed: int | None = None,
        tolerance_m: float = 0.0,
        active: bool = True,
        self_intersecting: bool = False,
        notes: str | None = None,
        inradius_m: float | None = None,
    ):
        self.inradius_m = inradius_m
        self.fence_id = fence_id
        self.site_id = site_id
        self.name = name
        self.site_type = site_type
        self.category = category
        self.max_speed = max_speed
        self.tolerance_m = float(tolerance_m)
        self.active = active
        self.notes = notes

        lat = np.array([p[0] for p in ring], dtype=np.float64)
        lon = np.array([p[1] for p in ring], dtype=np.float64)
        self.ring_lat = lat
        self.ring_lon = lon
        self.n_vertices = len(lat)

        self.min_lat = float(lat.min())
        self.max_lat = float(lat.max())
        self.min_lon = float(lon.min())
        self.max_lon = float(lon.max())

        clat, clon = G.ring_centroid(lat, lon)
        self.centroid_lat = clat
        self.centroid_lon = clon

        self.frame = G.LocalFrame(clat, clon)
        self.ring_x, self.ring_y = self.frame.to_m_arr(lat, lon)

        self.area_m2 = G.ring_area_m2(self.ring_x, self.ring_y)
        self.perimeter_m = G.ring_perimeter_m(self.ring_x, self.ring_y)
        self.self_intersecting = self_intersecting

        self._rx = tuple(float(v) for v in self.ring_x)
        self._ry = tuple(float(v) for v in self.ring_y)
        self._rlat = tuple(float(v) for v in lat)
        self._rlon = tuple(float(v) for v in lon)
        self._mx = self.frame.m_per_deg_lon()
        self._my = self.frame.m_per_deg_lat()

    # -- geometry -----------------------------------------------------------

    def bbox(self, pad_m: float = 0.0) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat), optionally padded in metres.

        X/Y order, matching the index and the WKT convention.
        """
        if pad_m <= 0.0:
            return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)
        dlat, dlon = G.metres_to_deg(pad_m, self.centroid_lat)
        return (self.min_lon - dlon, self.min_lat - dlat,
                self.max_lon + dlon, self.max_lat + dlat)

    def contains(self, lat: float, lon: float) -> bool:
        """Scalar containment, tolerance included.

        With no tolerance the bounding box is a sound early reject and the
        answer is a plain ray cast. With a tolerance the fence extends beyond
        its own box, so the box cannot reject and the signed distance decides.
        """
        if self.tolerance_m > 0.0:
            return float(self.signed_distance(np.array([lat]), np.array([lon]))[0]) <= 0.0
        if not (self.min_lat <= lat <= self.max_lat
                and self.min_lon <= lon <= self.max_lon):
            return False
        return G.point_in_ring(lat, lon, self.ring_lat, self.ring_lon)

    def signed_distance(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """Signed distance to the *effective* boundary, in metres.

        Negative inside, positive outside, tolerance folded in. This is the
        only containment primitive the detector uses.
        """
        d = G.signed_distance_m(
            lats, lons, self.ring_lat, self.ring_lon,
            self.ring_x, self.ring_y, self.frame,
        )
        if self.tolerance_m:
            d = d - self.tolerance_m
        return d

    def signed_distance_point(self, lat: float, lon: float,
                              window_m: float = FAR_M) -> float:
        """Signed distance for ONE point, in plain Python.

        The vectorised path is the right answer for a trail and the wrong one
        for a single fix: numpy's per-call overhead dominates completely at
        length 1, and the live detector calls this once per fence per ping.
        Measured on the live detector, routing single fixes through the array
        path costs about 7 fixes/second; this costs a few thousand.

        Same arithmetic as `signed_distance`, unrolled over a ring that is
        typically 13 edges long.
        """
        pad = window_m + self.tolerance_m
        if pad < FAR_M:
            dlat, dlon = G.metres_to_deg(pad, self.centroid_lat)
            if (lat < self.min_lat - dlat or lat > self.max_lat + dlat
                    or lon < self.min_lon - dlon or lon > self.max_lon + dlon):
                return FAR_M

        rlat, rlon = self._rlat, self._rlon
        n = len(rlat)

        # Ray cast, inlined over the tuple copies.
        inside = False
        j = n - 1
        for i in range(n):
            yi, yj = rlat[i], rlat[j]
            if (yi > lat) != (yj > lat):
                xi, xj = rlon[i], rlon[j]
                if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    inside = not inside
            j = i

        px = (lon - self.frame.lon0) * self._mx
        py = (lat - self.frame.lat0) * self._my
        rx, ry = self._rx, self._ry
        best = float("inf")
        for i in range(n):
            j = i + 1 if i + 1 < n else 0
            ax, ay = rx[i], ry[i]
            bx, by = rx[j], ry[j]
            abx, aby = bx - ax, by - ay
            den = abx * abx + aby * aby
            if den == 0.0:
                dx, dy = px - ax, py - ay
            else:
                t = ((px - ax) * abx + (py - ay) * aby) / den
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                dx = px - (ax + t * abx)
                dy = py - (ay + t * aby)
            d2 = dx * dx + dy * dy
            if d2 < best:
                best = d2

        d = math.sqrt(best)
        if inside:
            d = -d
        return d - self.tolerance_m

    def signed_distance_windowed(
        self, lats: np.ndarray, lons: np.ndarray, window_m: float
    ) -> np.ndarray:
        """Signed distance, but only computed where it can matter.

        Fixes outside the bounding box padded by `window_m` are stamped
        `FAR_M` instead of being measured. That is not an approximation: they
        are provably further than `window_m` from the fence, the detector's
        thresholds are all well inside `window_m`, and so the exact value
        cannot change any verdict. It is what keeps a 19,000-fix Jamshedpur to
        Delhi trail from paying for point-to-segment distance against a
        1,800-vertex ring it never went near.
        """
        lats = np.asarray(lats, dtype=np.float64)
        lons = np.asarray(lons, dtype=np.float64)
        out = np.full(lats.shape, FAR_M, dtype=np.float64)

        pad = window_m + self.tolerance_m
        min_lon, min_lat, max_lon, max_lat = self.bbox(pad)
        near = (
            (lats >= min_lat) & (lats <= max_lat)
            & (lons >= min_lon) & (lons <= max_lon)
        )
        if not near.any():
            return out
        idx = np.flatnonzero(near)
        out[idx] = self.signed_distance(lats[idx], lons[idx])
        return out

    def compute_inradius(self, cap_m: float = 200.0) -> float:
        """Measure and remember the inscribed radius."""
        self.inradius_m = G.inscribed_radius_m(self.ring_lat, self.ring_lon,
                                               self.ring_x, self.ring_y, self.frame, cap_m=cap_m)
        return self.inradius_m

    def __repr__(self) -> str:
        return (f"<Fence {self.fence_id} site={self.site_id} {self.name!r} "
                f"v={self.n_vertices} cat={self.category}>")
