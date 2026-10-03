"""A route as a polyline, and a fix measured against it.

Two numbers answer "is the truck on its route": how far it is from the route
(the cross-track distance) and how far along the route it has got (the
chainage, in kilometres from the start). Navigation apps compute exactly
these on every GPS update; the difference here is that the fixes come a
minute apart from a truck, so both are computed for a whole trail at once,
vectorised.

Distances are taken on a local equirectangular plane centred on the route --
under 1% error across a few hundred kilometres of latitude span, far below
the hundreds of metres the off-route thresholds work in -- while chainage is
accumulated with the haversine formula so kilometres along a long route
match the fit's own path lengths.
"""

from __future__ import annotations

import math

import numpy as np

R_EARTH = 6_371_008.8


def haversine_m(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(np.asarray(lon2) - np.asarray(lon1)) / 2) ** 2)
    return 2 * R_EARTH * np.arcsin(np.sqrt(np.minimum(1.0, a)))


class Polyline:
    """A route: vertices in order, with their chainage."""

    def __init__(self, lat, lon):
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        keep = np.ones(len(lat), dtype=bool)
        if len(lat) > 1:
            keep[1:] = (np.diff(lat) != 0) | (np.diff(lon) != 0)
        self.lat, self.lon = lat[keep], lon[keep]
        self.lat0 = float(np.mean(self.lat)) if len(self.lat) else 0.0
        self.kx = math.cos(math.radians(self.lat0)) * math.pi / 180 * R_EARTH
        self.ky = math.pi / 180 * R_EARTH
        self.x, self.y = self.xy(self.lat, self.lon)
        seg = haversine_m(self.lat[:-1], self.lon[:-1], self.lat[1:], self.lon[1:]) if len(self.lat) > 1 \
            else np.zeros(0)
        self.chain_m = np.concatenate([[0.0], np.cumsum(seg)])

    def __len__(self) -> int:
        return len(self.lat)

    @property
    def length_m(self) -> float:
        return float(self.chain_m[-1]) if len(self.chain_m) else 0.0

    def xy(self, lat, lon):
        return ((np.asarray(lon, dtype=np.float64) - 0.0) * self.kx,
                np.asarray(lat, dtype=np.float64) * self.ky)

    def project(self, lat, lon, chunk: int = 256) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """For each fix: distance to the route (m), chainage of its nearest
        point (m), and the index of the segment it falls on."""
        px, py = self.xy(lat, lon)
        n = len(px)
        if len(self) < 2:
            d = np.hypot(px - self.x[0], py - self.y[0]) if len(self) else np.full(n, np.inf)
            return d, np.zeros(n), np.zeros(n, dtype=np.int64)
        ax, ay = self.x[:-1], self.y[:-1]
        dx, dy = np.diff(self.x), np.diff(self.y)
        l2 = np.maximum(dx * dx + dy * dy, 1e-9)
        seg_len = self.chain_m[1:] - self.chain_m[:-1]
        dist = np.empty(n)
        chain = np.empty(n)
        which = np.empty(n, dtype=np.int64)
        for i in range(0, n, chunk):
            qx = px[i:i + chunk, None]
            qy = py[i:i + chunk, None]
            t = np.clip(((qx - ax) * dx + (qy - ay) * dy) / l2, 0.0, 1.0)
            ex = ax + t * dx - qx
            ey = ay + t * dy - qy
            d2 = ex * ex + ey * ey
            j = np.argmin(d2, axis=1)
            rows = np.arange(len(j))
            dist[i:i + chunk] = np.sqrt(d2[rows, j])
            chain[i:i + chunk] = self.chain_m[j] + t[rows, j] * seg_len[j]
            which[i:i + chunk] = j
        return dist, chain, which

    def point_at(self, chain_m: float) -> tuple[float, float]:
        """(lat, lon) at a chainage."""
        c = min(max(chain_m, 0.0), self.length_m)
        return (float(np.interp(c, self.chain_m, self.lat)), float(np.interp(c, self.chain_m, self.lon)))

    def coords(self) -> list[list[float]]:
        return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(self.lat, self.lon)]


def simplify(lat, lon, tolerance_m: float = 25.0) -> tuple[np.ndarray, np.ndarray]:
    """Douglas-Peucker on the local plane: a reference route kept to the
    vertices that shape it, so projecting a trail onto it stays cheap."""
    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    if len(lat) < 3:
        return lat, lon
    lat0 = float(np.mean(lat))
    x = lon * math.cos(math.radians(lat0)) * math.pi / 180 * R_EARTH
    y = lat * math.pi / 180 * R_EARTH
    keep = np.zeros(len(lat), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(lat) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        dx, dy = x[b] - x[a], y[b] - y[a]
        l2 = dx * dx + dy * dy
        ix = x[a + 1:b]
        iy = y[a + 1:b]
        if l2 == 0:
            d = np.hypot(ix - x[a], iy - y[a])
        else:
            t = np.clip(((ix - x[a]) * dx + (iy - y[a]) * dy) / l2, 0, 1)
            d = np.hypot(x[a] + t * dx - ix, y[a] + t * dy - iy)
        k = int(np.argmax(d))
        if d[k] > tolerance_m:
            m = a + 1 + k
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return lat[keep], lon[keep]


def resample(lat, lon, n: int) -> tuple[np.ndarray, np.ndarray]:
    """`n` points evenly spaced along a path, for comparing two paths."""
    line = Polyline(lat, lon)
    if line.length_m == 0 or len(line) < 2:
        return np.full(n, line.lat[0] if len(line) else 0.0), np.full(n, line.lon[0] if len(line) else 0.0)
    c = np.linspace(0, line.length_m, n)
    return np.interp(c, line.chain_m, line.lat), np.interp(c, line.chain_m, line.lon)


def mean_offset_m(path_a: tuple, path_b: tuple, samples: int = 80) -> float:
    """How far path A runs from path B on average: A resampled, each point's
    distance to B. Not symmetric; the medoid search uses both directions."""
    la, lo = resample(path_a[0], path_a[1], samples)
    line = Polyline(path_b[0], path_b[1])
    d, _, _ = line.project(la, lo)
    return float(np.mean(d))
