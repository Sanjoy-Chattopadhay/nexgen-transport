"""Which state and district every geofence lies in.

Answered from the application's own official boundaries -- the layers every
map draws (data/india_states.json, data/india_districts.json) -- so a fence's
state is the state the map shows it in, with no online lookup and no border
depiction but India's.

How a fence is placed
---------------------------------------------------------------------------
Its centroid and every vertex are located.

* The fence's state is the state holding its centroid, when the centroid lies
  inside the fence. A concave fence -- an L-shaped yard -- can have its
  centroid outside itself; then the state holding most of its vertices.
* A fence whose points land in more than one state lists them all in
  `states`: a catchment drawn across a border, or a plant on one.
* A fence none of whose points lies in any state is offshore -- an anchorage,
  a jetty head, an island the simplified coastline misses. It takes the
  nearest state, and the distance to that state's boundary is recorded, so
  "offshore, 2.4 km from Odisha" is visible rather than silently rounded to
  "Odisha".

The district is found the same way, from the point that decided the state.

Containment follows the map's fill rule, nonzero winding. A state's layer is
the collection of its district rings; neighbouring districts overlap by
slivers, which even-odd would cancel into false holes, while a ring wound the
other way inside another is a genuine hole and does cancel.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nexgen.shared.geoengine.geometry import points_in_ring

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
M_PER_DEG_LAT = 111_320.0


@dataclass
class _Ring:
    lat: np.ndarray
    lon: np.ndarray
    sign: int                                   # +1 counter-clockwise, -1 clockwise
    bbox: tuple[float, float, float, float]     # min_lon, min_lat, max_lon, max_lat


class Regions:
    """Named areas -- states or districts -- with a vectorised locate."""

    def __init__(self, features: list[dict], name_key: str):
        self.names: list[str] = []
        self.rings: list[list[_Ring]] = []
        for f in features:
            rings = []
            for poly in f["geometry"]["coordinates"]:
                for ring in poly:
                    a = np.asarray(ring, dtype=np.float64)
                    lon, lat = a[:, 0], a[:, 1]
                    twice_area = float(np.dot(lon[:-1], lat[1:]) - np.dot(lon[1:], lat[:-1]))
                    rings.append(_Ring(lat, lon, 1 if twice_area > 0 else -1,
                                       (float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max()))))
            if rings:
                self.names.append(f["properties"][name_key])
                self.rings.append(rings)

    @classmethod
    def load(cls, path: Path, name_key: str) -> "Regions":
        return cls(json.loads(Path(path).read_text(encoding="utf-8"))["features"], name_key)

    def locate(self, lats, lons) -> list[str | None]:
        """The region holding each point, or None. Where regions overlap by a
        sliver, the first in the layer's order wins."""
        lats = np.asarray(lats, dtype=np.float64)
        lons = np.asarray(lons, dtype=np.float64)
        out: list[str | None] = [None] * len(lats)
        found = np.zeros(len(lats), dtype=bool)
        for name, rings in zip(self.names, self.rings):
            winding = np.zeros(len(lats), dtype=np.int32)
            for r in rings:
                cand = np.nonzero(~found & (lons >= r.bbox[0]) & (lons <= r.bbox[2])
                                  & (lats >= r.bbox[1]) & (lats <= r.bbox[3]))[0]
                if not len(cand):
                    continue
                inside = points_in_ring(lats[cand], lons[cand], r.lat, r.lon)
                winding[cand[inside]] += r.sign
            hit = np.nonzero((winding != 0) & ~found)[0]
            for i in hit:
                out[i] = name
            found[hit] = True
        return out

    def nearest(self, lat: float, lon: float) -> tuple[str | None, float]:
        """The region whose boundary is nearest, and that distance in metres."""
        k = math.cos(math.radians(lat))
        best_name, best = None, math.inf
        for name, rings in zip(self.names, self.rings):
            for r in rings:
                dx = max(r.bbox[0] - lon, 0.0, lon - r.bbox[2]) * k
                dy = max(r.bbox[1] - lat, 0.0, lat - r.bbox[3])
                if math.hypot(dx, dy) * M_PER_DEG_LAT >= best:
                    continue
                d = _distance_to_ring_m(lat, lon, r, k)
                if d < best:
                    best_name, best = name, d
        return best_name, best


def _distance_to_ring_m(lat: float, lon: float, r: _Ring, k: float) -> float:
    x = (r.lon - lon) * k * M_PER_DEG_LAT
    y = (r.lat - lat) * M_PER_DEG_LAT
    x0, y0, dx, dy = x[:-1], y[:-1], np.diff(x), np.diff(y)
    length2 = dx * dx + dy * dy
    t = np.clip(-(x0 * dx + y0 * dy) / np.where(length2 > 0, length2, 1.0), 0.0, 1.0)
    px, py = x0 + t * dx, y0 + t * dy
    return float(np.sqrt((px * px + py * py).min()))


def load_layers() -> tuple[Regions, Regions]:
    return (Regions.load(DATA_DIR / "india_states.json", "state"),
            Regions.load(DATA_DIR / "india_districts.json", "district"))


def classify(fences, states: Regions, districts: Regions) -> list[dict]:
    """State, district, every state touched, and offshore distance per fence.

    `fences` are engine Fence objects (ring_lat, ring_lon, centroid, contains).
    """
    lats, lons, owner, is_centroid = [], [], [], []
    for i, f in enumerate(fences):
        lats.append(f.centroid_lat)
        lons.append(f.centroid_lon)
        owner.append(i)
        is_centroid.append(True)
        for la, lo in zip(f.ring_lat, f.ring_lon):
            lats.append(float(la))
            lons.append(float(lo))
            owner.append(i)
            is_centroid.append(False)
    state_at = states.locate(lats, lons)

    per_fence: list[dict] = [{"centroid": None, "centroid_index": None, "vertices": []} for _ in fences]
    for j, (i, c) in enumerate(zip(owner, is_centroid)):
        if c:
            per_fence[i]["centroid"] = state_at[j]
            per_fence[i]["centroid_index"] = j
        elif state_at[j] is not None:
            per_fence[i]["vertices"].append((state_at[j], j))

    # Decide each fence's state and the point that decided it.
    decided: list[tuple[str | None, int | None]] = []
    for f, p in zip(fences, per_fence):
        if p["centroid"] is not None and f.contains(f.centroid_lat, f.centroid_lon):
            decided.append((p["centroid"], p["centroid_index"]))
        elif p["vertices"]:
            state = Counter(s for s, _ in p["vertices"]).most_common(1)[0][0]
            decided.append((state, next(j for s, j in p["vertices"] if s == state)))
        elif p["centroid"] is not None:
            decided.append((p["centroid"], p["centroid_index"]))
        else:
            decided.append((None, None))

    point_ids = [j for _, j in decided if j is not None]
    district_at = dict(zip(point_ids, districts.locate([lats[j] for j in point_ids],
                                                       [lons[j] for j in point_ids])))

    rows = []
    for f, p, (state, j) in zip(fences, per_fence, decided):
        touched = sorted({s for s, _ in p["vertices"]} | ({p["centroid"]} if p["centroid"] else set()))
        offset = None
        district = district_at.get(j) if j is not None else None
        if state is None:
            state, offset = states.nearest(f.centroid_lat, f.centroid_lon)
            district, _ = districts.nearest(f.centroid_lat, f.centroid_lon)
            offset = round(offset, 1)
        rows.append({"fence_id": f.fence_id, "state": state, "district": district,
                     "states": ",".join(touched) if len(touched) > 1 else None,
                     "offshore_m": offset})
    return rows
