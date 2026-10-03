"""Build the India base map the control centre draws.

Why there is no basemap tile layer
----------------------------------
Every general-purpose tile provider renders the whole world, and depicts
India's northern borders the way the UN does rather than the way India does.
For a Tata Steel control room that is not cosmetic, and it cannot be fixed by
restricting the viewport, because the depiction is baked into the tile images.

So this map has no tiles. It is India and nothing else: polygons drawn as SVG
on an empty ground. Neighbouring countries are not rendered incorrectly
because they are not rendered at all. It also means the control centre has no
tile-server dependency and works with no internet connection.

The source
----------
District boundaries from `udit-001/india-maps-data`, the per-state files under
`geojson/states/`. That set carries 165,000 vertices across 737 districts;
the same repository's single combined `india.geojson` carries 25,000 for the
whole country and renders Odisha's coastline as a handful of straight lines.

It uses the official Indian depiction: Jammu and Kashmir and Ladakh are
present as Indian territory and the northern extent reaches 37.08 N, where an
international rendering stops around 35.5 N. `verify_extent()` checks this on
every build and refuses to produce a map from a file that has been swapped for
an international one.

Why districts are the base layer, and states are not dissolved
--------------------------------------------------------------
The obvious move is to dissolve districts into state outlines. It was tried
and rejected on evidence.

Dissolving by cancelling shared edges is exact *if* adjacent polygons share
identical vertices. This source is only partly like that. Odisha and Jharkhand
dissolve to exactly one ring each; Jammu and Kashmir produces 72 rings from 22
districts and West Bengal 46 from 23, and those are not float-noise slivers --
they survive at every snap tolerance from 1 m to 33 m, and most are over
1 km². Dissolving the whole country produces 241 fragments whose largest is
384,000 km² against India's 3.29 million, i.e. it breaks along state lines
too. The districts in those states were simply digitised separately and do not
tile.

Rather than ship a map with invented gaps in Kashmir, the state layer is the
*collection* of each state's district rings, not a merged outline. Filling
that gives exact state colour blocks; a hairline stroke on the district layer
over the top gives the internal detail. The boundary between two states is
where the fill colour changes, and it is exact because it is the same geometry
the districts came from -- there is nothing to get wrong.

That also happens to be the more useful map: a control centre wants district
context, not a flat silhouette.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"

# An international-depiction file stops well short of the northern bound, so
# this is a usable tripwire against a silent source swap.
OFFICIAL_MIN_NORTH = 36.5
INDIA_BBOX = (68.0, 6.5, 97.5, 37.5)

# Output precision, in decimal places. 5 dp is ~1.1 m -- far finer than the
# simplification tolerance, and a fraction of the bytes the source's 15 dp
# costs. The browser downloads this on every load.
PRECISION = 5


def _round(pt) -> list[float]:
    return [round(float(pt[0]), PRECISION), round(float(pt[1]), PRECISION)]


def _rings(geom) -> list:
    """Every ring of a Polygon or MultiPolygon, as coordinate lists."""
    if geom["type"] == "Polygon":
        return list(geom["coordinates"])
    if geom["type"] == "MultiPolygon":
        return [r for poly in geom["coordinates"] for r in poly]
    return []


# ---------------------------------------------------------------------------
# Simplify
# ---------------------------------------------------------------------------

def rdp(points, epsilon: float):
    """Ramer-Douglas-Peucker, iterative so a 5,000-point ring cannot blow the
    Python stack.

    Used only on the *base map*, never on geofences. Losing a few metres of a
    coastline is invisible at screen scale; losing them on a fence would move
    a detection boundary, which is why fences are always drawn from their
    exact stored vertices.

    Reference: Douglas & Peucker, Cartographica 10(2), 1973.
    """
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best_d, best_i = -1.0, -1
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm == 0.0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > best_d:
                best_d, best_i = d, i
        if best_d > epsilon:
            keep[best_i] = True
            stack.append((lo, best_i))
            stack.append((best_i, hi))

    return [p for p, k in zip(points, keep) if k]


def _ring_area_deg2(ring) -> float:
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

def load_source(source: Path) -> list:
    """Read a directory of per-state files, or one combined file.

    Features without a `district` are dropped. The combined `india.geojson`
    mixes 34 state-level outlines in among the districts, and keeping them
    would double-count every boundary.
    """
    source = Path(source)
    files = sorted(source.glob("*.geojson")) if source.is_dir() else [source]
    if not files:
        raise FileNotFoundError(f"no .geojson under {source}")

    features: list = []
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for f in raw.get("features", []):
            if f.get("properties", {}).get("district"):
                features.append(f)
    if not features:
        raise ValueError(f"no district features found under {source}")
    return features


def verify_extent(features) -> dict:
    """Confirm this is the official Indian depiction, not an international one."""
    xs, ys, states = [], [], set()
    for f in features:
        states.add(f["properties"].get("st_nm"))
        for ring in _rings(f["geometry"]):
            for x, y in ring:
                xs.append(x)
                ys.append(y)
    info = {
        "features": len(features),
        "states": len(states),
        "lon": [round(min(xs), 4), round(max(xs), 4)],
        "lat": [round(min(ys), 4), round(max(ys), 4)],
        "has_jammu_kashmir": any(s and "Jammu" in s for s in states),
        "has_ladakh": any(s and "Ladakh" in s for s in states),
    }
    info["official_depiction"] = bool(
        max(ys) >= OFFICIAL_MIN_NORTH
        and info["has_jammu_kashmir"] and info["has_ladakh"]
    )
    return info


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build(source: Path, out_dir: Path | None = None,
          state_simplify: float = 0.0015,
          district_simplify: float = 0.006) -> dict:
    """Write the two map layers.

    `state_simplify` 0.0015 deg is ~165 m; `district_simplify` 0.006 is ~660 m.
    Both are sub-pixel on a 1,400 px map of a country 3,000 km across, and the
    districts are only ever a hairline overlay so they can take the coarser
    tolerance.
    """
    out_dir = Path(out_dir or DATA_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    features = load_source(source)
    check = verify_extent(features)
    if not check["official_depiction"]:
        raise ValueError(
            "source is not the official Indian depiction "
            f"(north {check['lat'][1]}, J&K={check['has_jammu_kashmir']}, "
            f"Ladakh={check['has_ladakh']}) -- refusing to build a map that "
            "misrepresents the border"
        )
    logger.info("source verified: %s", check)

    by_state: dict[str, list] = defaultdict(list)
    for f in features:
        by_state[f["properties"].get("st_nm") or "Unknown"].append(f)

    state_features = []
    for name in sorted(by_state):
        feats = by_state[name]
        polys, biggest, biggest_area = [], None, -1.0
        for f in feats:
            for ring in _rings(f["geometry"]):
                pts = rdp([tuple(p) for p in ring], state_simplify)
                if len(pts) < 4:
                    continue
                area = _ring_area_deg2(pts)
                if area > biggest_area:
                    biggest_area, biggest = area, pts
                polys.append([[_round(p) for p in pts]])
        if not polys:
            continue

        # Label anchor: centroid of the state's largest ring. The centroid of
        # the whole state is wrong for anything with far-flung islands -- it
        # puts the Andaman and Nicobar label in open sea.
        cx = sum(p[0] for p in biggest) / len(biggest)
        cy = sum(p[1] for p in biggest) / len(biggest)

        state_features.append({
            "type": "Feature",
            "properties": {
                "state": name,
                "st_code": feats[0]["properties"].get("st_code"),
                "districts": len(feats),
                "label": [round(cx, 4), round(cy, 4)],
            },
            # One polygon per ring: GeoJSON nests a MultiPolygon as polygons of
            # rings of positions. A flat list of rings is one level short, and
            # a standard reader (Leaflet) rejects the whole layer.
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        })

    district_features = []
    for f in features:
        polys = []
        for ring in _rings(f["geometry"]):
            pts = rdp([tuple(p) for p in ring], district_simplify)
            if len(pts) >= 4:
                polys.append([[_round(p) for p in pts]])
        if not polys:
            continue
        district_features.append({
            "type": "Feature",
            "properties": {"district": f["properties"].get("district"),
                           "state": f["properties"].get("st_nm")},
            "geometry": {"type": "MultiPolygon", "coordinates": polys},
        })

    states_path = out_dir / "india_states.json"
    districts_path = out_dir / "india_districts.json"
    _write(states_path, state_features, check)
    _write(districts_path, district_features, check)

    return {
        "source_check": check,
        "states": len(state_features),
        "districts": len(district_features),
        "state_points": sum(len(ring) for f in state_features
                            for poly in f["geometry"]["coordinates"] for ring in poly),
        "district_points": sum(len(ring) for f in district_features
                               for poly in f["geometry"]["coordinates"] for ring in poly),
        "states_kb": round(states_path.stat().st_size / 1024),
        "districts_kb": round(districts_path.stat().st_size / 1024),
    }


def check_geometry(features: list) -> None:
    """Refuse a layer a standard GeoJSON reader would reject.

    Both layers were once written with every MultiPolygon one level of nesting
    short. Any JSON parser accepted the files; Leaflet threw on the first
    feature, so every map in the application drew no India at all.
    """
    for f in features:
        g = f["geometry"]
        if g["type"] != "MultiPolygon":
            raise ValueError(f"{f['properties']}: expected a MultiPolygon, got {g['type']}")
        for poly in g["coordinates"]:
            for ring in poly:
                if len(ring) < 4 or not all(
                        isinstance(p, list) and len(p) == 2
                        and all(isinstance(v, (int, float)) for v in p) for p in ring):
                    raise ValueError(f"{f['properties']}: a ring is not a list of [lon, lat] positions")


def _write(path: Path, features: list, check: dict) -> None:
    check_geometry(features)
    path.write_text(
        json.dumps({
            "type": "FeatureCollection",
            "bbox": list(INDIA_BBOX),
            "note": ("Official Indian depiction. Rendered standalone with no "
                     "basemap tiles, so no neighbouring country borders are "
                     "drawn at all."),
            "source_extent": {"lon": check["lon"], "lat": check["lat"]},
            "features": features,
        }, separators=(",", ":")),
        encoding="utf-8",
    )
