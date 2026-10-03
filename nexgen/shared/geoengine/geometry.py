"""Geometric primitives.

Two coordinate spaces are used, deliberately, for different jobs:

**Degree space** (lon, lat as plain x, y) is used for the containment test.
The fences are vertex lists authored in a planar map editor, and the source
system treats their edges as straight lines between consecutive lat/lon pairs.
Testing containment any other way -- e.g. along geodesics -- would answer a
question the client never asked and would disagree with their own screen near
the boundary. It also lets MySQL's planar `ST_Contains` act as an independent
oracle in the tests, which it cannot if the engine uses different semantics.

**Local metric space** (metres east/north of a reference point) is used for
everything with a distance in it: the hysteresis band, the escape threshold,
the outlier gate, areas and perimeters. Degrees are not a length -- one degree
of longitude is 111 km at the equator and 96 km at Jamshedpur -- so a "25 m"
buffer expressed in degrees is a different buffer at every latitude.

The projection is equirectangular about a per-polygon reference latitude:

    x = R * (lon - lon0) * cos(lat0)
    y = R * (lat - lat0)

Over a site-sized extent (a few km) its distortion is far below GPS noise:
the cos(lat) term is expanded about lat0, so the error grows with the square
of the north-south extent. At 10 km from the reference it is under 2 m, and
site polygons here are almost all under 3 km across. Using a full geodesic
solution instead would cost an order of magnitude more per call and change no
verdict this system makes.
"""

from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_M = 6_371_008.8

# Degrees of latitude per metre; latitude degrees are near enough constant.
DEG_PER_M_LAT = 1.0 / 110_574.0

# Guards the 1/cos(lat) blow-up. India is 8-37N so this never binds in
# practice, but a corrupt fix must not produce an infinite bounding box.
_MIN_COS_LAT = 0.01


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def haversine_m_vec(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Vectorised haversine. Used on whole trails at once."""
    p1 = np.radians(np.asarray(lat1, dtype=np.float64))
    p2 = np.radians(np.asarray(lat2, dtype=np.float64))
    dp = p2 - p1
    dl = np.radians(np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64))
    a = np.sin(dp / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.minimum(1.0, a)))


# ---------------------------------------------------------------------------
# Local metric projection
# ---------------------------------------------------------------------------

class LocalFrame:
    """Equirectangular projection about a reference point.

    Construct one per fence (from its centroid) and reuse it: the cos(lat0)
    term is the only trig involved and it is computed once here rather than
    per point.
    """

    __slots__ = ("lat0", "lon0", "_mx", "_my")

    def __init__(self, lat0: float, lon0: float):
        self.lat0 = lat0
        self.lon0 = lon0
        cos_lat = max(abs(math.cos(math.radians(lat0))), _MIN_COS_LAT)
        # Metres per degree, at this latitude.
        self._mx = math.radians(1.0) * EARTH_RADIUS_M * cos_lat
        self._my = math.radians(1.0) * EARTH_RADIUS_M

    def to_m(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self._mx, (lat - self.lat0) * self._my)

    def to_m_arr(self, lat, lon) -> tuple[np.ndarray, np.ndarray]:
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        return ((lon - self.lon0) * self._mx, (lat - self.lat0) * self._my)

    def m_per_deg_lon(self) -> float:
        return self._mx

    def m_per_deg_lat(self) -> float:
        return self._my


def metres_to_deg(metres: float, lat: float) -> tuple[float, float]:
    """(dlat, dlon) spanning `metres` at latitude `lat`."""
    dlat = metres * DEG_PER_M_LAT
    cos_lat = max(abs(math.cos(math.radians(lat))), _MIN_COS_LAT)
    return dlat, dlat / cos_lat


# ---------------------------------------------------------------------------
# Ring hygiene
# ---------------------------------------------------------------------------

def clean_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop consecutive duplicates and any explicit closing vertex.

    The extract is inconsistent about closure -- 10 of 4,957 sites repeat the
    first vertex as the last -- so the internal representation is fixed as
    *open*: N distinct vertices, the closing edge implied from the last back
    to the first. Every consumer can then assume one convention.
    """
    out: list[tuple[float, float]] = []
    for p in points:
        if not out or (abs(p[0] - out[-1][0]) > 1e-12 or abs(p[1] - out[-1][1]) > 1e-12):
            out.append(p)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= 1e-12 and abs(out[0][1] - out[-1][1]) <= 1e-12:
        out.pop()
    return out


def close_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Append the first vertex if the ring is not already closed (for WKT)."""
    if not points:
        return points
    if points[0] != points[-1]:
        return [*points, points[0]]
    return points


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------

def point_in_ring(lat: float, lon: float, ring_lat, ring_lon) -> bool:
    """Crossing-number point-in-polygon (Franklin's PNPOLY).

    Casts a ray east from the point and counts edge crossings; odd means
    inside. The half-open edge convention (`>` on one end, `<=` on the other)
    is what makes a vertex-grazing ray count once rather than twice or zero
    times, so the result is correct for points level with a vertex -- the case
    a naive implementation gets wrong and which occurs constantly here,
    because the fences are axis-aligned rectangles and trucks drive on
    axis-aligned roads.

    Points exactly on the boundary are not a defined case for any crossing
    -number test and are not treated as one: the detector never asks about a
    bare point, it asks for a signed distance and resolves the boundary band
    with hysteresis (see engine/state.py).

    Reference: W. Randolph Franklin, "PNPOLY -- Point Inclusion in Polygon
    Test", https://wrf.ecse.rpi.edu/Research/Short_Notes/pnpoly.html
    """
    n = len(ring_lat)
    inside = False
    j = n - 1
    for i in range(n):
        yi, yj = ring_lat[i], ring_lat[j]
        if (yi > lat) != (yj > lat):
            xi, xj = ring_lon[i], ring_lon[j]
            if lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def points_in_ring(lats: np.ndarray, lons: np.ndarray,
                   ring_lat: np.ndarray, ring_lon: np.ndarray) -> np.ndarray:
    """Vectorised PNPOLY: many points against one ring, in one pass.

    Same algorithm as `point_in_ring`, restructured so the loop runs over
    *edges* (few) instead of *points* (many), with each edge tested against
    the whole point array at once. For a 4-vertex fence this is four numpy
    passes regardless of whether there are ten points or ten thousand.
    """
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    inside = np.zeros(lats.shape, dtype=bool)

    yi = ring_lat
    yj = np.roll(ring_lat, 1)
    xi = ring_lon
    xj = np.roll(ring_lon, 1)

    for k in range(len(ring_lat)):
        y_i, y_j, x_i, x_j = yi[k], yj[k], xi[k], xj[k]
        if y_i == y_j:
            # Horizontal edge: the (yi > lat) != (yj > lat) test is false for
            # every point, so it contributes no crossings. Skip the divide.
            continue
        straddles = (y_i > lats) != (y_j > lats)
        if not straddles.any():
            continue
        x_cross = (x_j - x_i) * (lats - y_i) / (y_j - y_i) + x_i
        inside ^= straddles & (lons < x_cross)
    return inside


# ---------------------------------------------------------------------------
# Distance to boundary
# ---------------------------------------------------------------------------

def _seg_dist_sq(px, py, ax, ay, bx, by):
    """Squared distance from points P to segments A->B, vectorised over P."""
    abx = bx - ax
    aby = by - ay
    denom = abx * abx + aby * aby
    if denom == 0.0:
        dx = px - ax
        dy = py - ay
        return dx * dx + dy * dy
    t = ((px - ax) * abx + (py - ay) * aby) / denom
    t = np.clip(t, 0.0, 1.0)
    dx = px - (ax + t * abx)
    dy = py - (ay + t * aby)
    return dx * dx + dy * dy


def distance_to_ring_m(xs: np.ndarray, ys: np.ndarray,
                       ring_x: np.ndarray, ring_y: np.ndarray) -> np.ndarray:
    """Unsigned distance in metres from each point to the ring, in local frame.

    Everything is already projected, so this is plain planar point-to-segment
    distance over the closed ring.
    """
    xs = np.asarray(xs, dtype=np.float64)
    ys = np.asarray(ys, dtype=np.float64)
    best = np.full(xs.shape, np.inf, dtype=np.float64)
    n = len(ring_x)
    for i in range(n):
        j = (i + 1) % n
        d2 = _seg_dist_sq(xs, ys, ring_x[i], ring_y[i], ring_x[j], ring_y[j])
        np.minimum(best, d2, out=best)
    return np.sqrt(best)


def signed_distance_m(lats, lons, ring_lat, ring_lon,
                      ring_x, ring_y, frame: LocalFrame) -> np.ndarray:
    """Distance to the boundary in metres, negative inside, positive outside.

    This -- not a bare in/out boolean -- is what the state machine consumes.
    The magnitude is what lets a fix 3 m outside a fence be treated as
    "on the boundary, state unchanged" while one 400 m outside is treated as
    a departure, which is the whole basis of the noise handling.
    """
    xs, ys = frame.to_m_arr(lats, lons)
    d = distance_to_ring_m(xs, ys, ring_x, ring_y)
    inside = points_in_ring(lats, lons, ring_lat, ring_lon)
    return np.where(inside, -d, d)


# ---------------------------------------------------------------------------
# Inscribed radius
# ---------------------------------------------------------------------------

def inscribed_radius_m(ring_lat, ring_lon, ring_x, ring_y, frame: LocalFrame,
                       cap_m: float = 200.0, precision_m: float = 0.5) -> float:
    """How far inside the fence any point can be: the radius of the largest
    circle that fits in the ring.

    This is what a hysteresis band has to be measured against. A 25 m band
    is sensible on a 1 km works; on a 58 m parking bay, whose centre is only
    29 m from every edge, it leaves 4 m in which a truck counts as clearly
    inside, and entries are then confirmed only when GPS noise happens to push
    a fix that deep.

    Polylabel (Garcia-Castellanos & Lombardo 2007, as popularised by Mapbox):
    a best-first quadtree search over the bounding box, where each cell's
    potential is its centre's inside distance plus its half-diagonal, and a
    cell is split only if that potential beats the best found by more than
    `precision_m`. Stops early once the radius reaches `cap_m`, because beyond
    a few band-widths the exact value changes no decision.
    """
    import heapq

    xs = np.asarray(ring_x, dtype=np.float64)
    ys = np.asarray(ring_y, dtype=np.float64)
    min_x, max_x = float(xs.min()), float(xs.max())
    min_y, max_y = float(ys.min()), float(ys.max())
    width, height = max_x - min_x, max_y - min_y
    size = min(width, height)
    if size <= 0.0:
        return 0.0

    lat0, lon0 = frame.lat0, frame.lon0
    mx, my = frame.m_per_deg_lon(), frame.m_per_deg_lat()

    def inside_depth(px: np.ndarray, py: np.ndarray) -> np.ndarray:
        lats = lat0 + py / my
        lons = lon0 + px / mx
        d = distance_to_ring_m(px, py, xs, ys)
        inside = points_in_ring(lats, lons, ring_lat, ring_lon)
        return np.where(inside, d, -d)

    half = size / 2.0
    cx = np.arange(min_x + half, max_x + half, size)
    cy = np.arange(min_y + half, max_y + half, size)
    gx, gy = np.meshgrid(cx, cy)
    gx, gy = gx.ravel(), gy.ravel()
    depth = inside_depth(gx, gy)
    heap = [(-(d + half * math.sqrt(2.0)), float(x), float(y), float(d), half)
            for x, y, d in zip(gx, gy, depth)]
    heapq.heapify(heap)

    # Seed the best guess with the area centroid; it is often close.
    ccx = float(np.mean(xs))
    ccy = float(np.mean(ys))
    best = max(float(inside_depth(np.array([ccx]), np.array([ccy]))[0]), float(depth.max()))

    while heap:
        neg_potential, x, y, d, h = heapq.heappop(heap)
        if d > best:
            best = d
        if best >= cap_m:
            return cap_m
        if -neg_potential - best <= precision_m:
            continue
        h2 = h / 2.0
        kx = np.array([x - h2, x + h2, x - h2, x + h2])
        ky = np.array([y - h2, y - h2, y + h2, y + h2])
        kd = inside_depth(kx, ky)
        for i in range(4):
            heapq.heappush(heap, (-(float(kd[i]) + h2 * math.sqrt(2.0)),
                                  float(kx[i]), float(ky[i]), float(kd[i]), h2))
    return max(0.0, min(best, cap_m))


# ---------------------------------------------------------------------------
# Ring measurements
# ---------------------------------------------------------------------------

def ring_area_m2(ring_x: np.ndarray, ring_y: np.ndarray) -> float:
    """Shoelace area in the local metric frame. Always returned positive."""
    x2 = np.roll(ring_x, -1)
    y2 = np.roll(ring_y, -1)
    return abs(float(np.sum(ring_x * y2 - x2 * ring_y)) / 2.0)


def ring_perimeter_m(ring_x: np.ndarray, ring_y: np.ndarray) -> float:
    x2 = np.roll(ring_x, -1)
    y2 = np.roll(ring_y, -1)
    return float(np.sum(np.hypot(x2 - ring_x, y2 - ring_y)))


def ring_centroid(ring_lat: np.ndarray, ring_lon: np.ndarray) -> tuple[float, float]:
    """Area-weighted centroid, falling back to the vertex mean.

    The shoelace centroid is undefined for a degenerate ring (zero area, e.g.
    a collinear or 2-vertex remnant), so those fall back to the mean of the
    vertices rather than dividing by zero.
    """
    x = np.asarray(ring_lon, dtype=np.float64)
    y = np.asarray(ring_lat, dtype=np.float64)
    x2 = np.roll(x, -1)
    y2 = np.roll(y, -1)
    cross = x * y2 - x2 * y
    a = float(np.sum(cross)) / 2.0
    if abs(a) < 1e-14:
        return float(y.mean()), float(x.mean())
    cx = float(np.sum((x + x2) * cross)) / (6.0 * a)
    cy = float(np.sum((y + y2) * cross)) / (6.0 * a)
    return cy, cx


def is_ccw(ring_x: np.ndarray, ring_y: np.ndarray) -> bool:
    x2 = np.roll(ring_x, -1)
    y2 = np.roll(ring_y, -1)
    return float(np.sum(ring_x * y2 - x2 * ring_y)) > 0.0


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

def _segments_cross(p1, p2, p3, p4) -> bool:
    """Proper intersection test for two open segments."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if v > 1e-15:
            return 1
        if v < -1e-15:
            return -1
        return 0

    o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
    o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4 and o1 != 0 and o2 != 0 and o3 != 0 and o4 != 0


def find_self_intersection(ring_x: np.ndarray, ring_y: np.ndarray) -> tuple[int, int] | None:
    """First properly-crossing pair of non-adjacent edges, or None.

    O(n^2), which is fine: it runs once per fence at compile time, and the
    largest ring in the corpus is 1,826 vertices (~1.6M cheap orientation
    tests, well under a second). A sweep-line would be the answer if this were
    on the hot path; it is not.

    A crossing ring is *reported*, not repaired. Ray casting still returns a
    deterministic answer for one (the self-overlapping lobe simply counts
    twice and reads as outside), and silently rewriting a client's geometry
    would be a worse failure than flagging it.
    """
    n = len(ring_x)
    if n < 4:
        return None
    pts = [(float(ring_x[i]), float(ring_y[i])) for i in range(n)]
    for i in range(n):
        a1, a2 = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # adjacent across the closing edge
            b1, b2 = pts[j], pts[(j + 1) % n]
            if _segments_cross(a1, a2, b1, b2):
                return (i, j)
    return None


def ring_to_wkt(ring_lat, ring_lon) -> str:
    """WKT POLYGON in X=lon, Y=lat order, explicitly closed.

    MySQL requires the ring closed; our internal form is open, so the closure
    is added here at the boundary rather than carried around everywhere.
    """
    pts = list(zip(ring_lon, ring_lat))
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    body = ", ".join(f"{x:.8f} {y:.8f}" for x, y in pts)
    return f"POLYGON(({body}))"
