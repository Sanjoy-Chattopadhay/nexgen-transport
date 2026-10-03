"""Static packed Hilbert R-tree over fence bounding boxes.

Why an R-tree and not a uniform grid
------------------------------------
A grid gives O(1) lookup and is the obvious choice when the objects are all
about the same size. This fence set is not: the smallest ring is a 40 m
weighbridge square and the largest spans a whole industrial belt, and the
area ratio between them is over five orders of magnitude. Sizing grid cells
for the small fences makes the large ones register into tens of thousands of
cells; sizing for the large ones puts hundreds of small fences in a single
bucket and the "index" degenerates into a linear scan of that bucket. The
sites are also heavily clustered -- Jamshedpur, Kalinganagar and Angul hold a
large share of the corpus -- so a uniform grid is mostly empty cells with a
few very deep ones. An R-tree adapts its node extents to the data and does
not care about either the size spread or the clustering.

Why *packed* and *Hilbert*
--------------------------
The fence set is static between master imports, so there is no reason to pay
for an insertable tree. Bulk-loading lets the tree be built bottom-up, fully
balanced, into flat numpy arrays with no per-node Python objects.

Ordering the leaves along a Hilbert curve before packing is what makes the
node boxes tight. A Hilbert curve preserves locality far better than sorting
by x, or by (x, y) tiles: points close on the curve are close in the plane,
so consecutive leaves packed into a node have a small combined box, which is
what actually determines how many subtrees a query has to open. This is the
Hilbert-packed R-tree of Kamel & Faloutsos (1993); the flat-array formulation
is the one Flatbush uses.

References
----------
* Guttman, A. "R-Trees: A Dynamic Index Structure for Spatial Searching",
  SIGMOD 1984.
* Kamel, I. & Faloutsos, C. "Hilbert R-tree: An Improved R-tree Using
  Fractals", VLDB 1993.
* Flatbush, https://github.com/mourner/flatbush -- the flat-array packing
  and the default node size of 16 are taken from it.

What this does and does not do
------------------------------
It is a *filter over bounding boxes*, so its answers are candidates, never
verdicts: a box can contain a point the polygon does not. There are no false
negatives -- a point inside a polygon is inside that polygon's box, so the
box is reported -- and the false positives are removed by the point-in-polygon
test that follows. `tests/test_index.py` asserts the candidate set matches
brute force on the full master.
"""

from __future__ import annotations

import numpy as np

DEFAULT_NODE_SIZE = 16

# Hilbert curve order. 16 bits per axis = a 65536x65536 lattice over the data
# extent, which is ~30 cm at Indian latitudes -- far finer than the geometry.
_HILBERT_BITS = 16
_HILBERT_SIDE = 1 << _HILBERT_BITS


def hilbert_d(x: np.ndarray, y: np.ndarray, bits: int = _HILBERT_BITS) -> np.ndarray:
    """Hilbert curve distance for integer lattice coordinates.

    Vectorised form of the standard xy->d conversion: walk the quadrants from
    the coarsest bit down, accumulating the curve distance and rotating the
    frame at each level so the sub-curve stays connected to its neighbours.
    """
    x = x.astype(np.int64, copy=True)
    y = y.astype(np.int64, copy=True)
    d = np.zeros(x.shape, dtype=np.int64)
    s = np.int64(1) << (bits - 1)
    while s > 0:
        rx = ((x & s) > 0).astype(np.int64)
        ry = ((y & s) > 0).astype(np.int64)
        d += s * s * ((3 * rx) ^ ry)
        # Rotate the quadrant so the curve is continuous across it.
        swap = ry == 0
        flip = swap & (rx == 1)
        x_f = np.where(flip, s - 1 - x, x)
        y_f = np.where(flip, s - 1 - y, y)
        x_new = np.where(swap, y_f, x_f)
        y_new = np.where(swap, x_f, y_f)
        x, y = x_new, y_new
        s >>= 1
    return d


class FenceIndex:
    """Bounding-box index over a fence collection.

    Build once::

        idx = FenceIndex(fences)

    then either ask for the fences whose box covers a point::

        for fence in idx.query_point(lat, lon): ...

    or -- much faster on a trajectory -- ask once for everything near a whole
    segment of trail::

        for fence in idx.query_box(min_lon, min_lat, max_lon, max_lat): ...

    The second form is what the evaluator uses. Querying per fix would mean
    one tree descent per GPS point; querying per chunk of trail means one
    descent per few hundred points, and the containment test that follows is
    then run vectorised over the whole chunk.
    """

    __slots__ = ("_fences", "_boxes", "_indices", "_level_bounds",
                 "_node_size", "_n", "_by_site", "_by_id", "stats")

    def __init__(self, fences: list, node_size: int = DEFAULT_NODE_SIZE,
                 pad_m: float = 0.0):
        self._fences = list(fences)
        self._node_size = max(2, node_size)
        self._n = len(self._fences)
        self._by_site = {f.site_id: f for f in self._fences}
        self._by_id = {f.fence_id: f for f in self._fences}

        if self._n == 0:
            self._boxes = np.zeros((0, 4), dtype=np.float64)
            self._indices = np.zeros(0, dtype=np.int64)
            self._level_bounds = [0]
            self.stats = {"fences": 0, "nodes": 0, "levels": 0}
            return

        item_boxes = np.array(
            [f.bbox(pad_m) for f in self._fences], dtype=np.float64
        )

        # --- level sizing ---------------------------------------------------
        n = self._n
        level_sizes = [n]
        while level_sizes[-1] > 1:
            level_sizes.append(-(-level_sizes[-1] // self._node_size))
        total = sum(level_sizes)

        boxes = np.zeros((total, 4), dtype=np.float64)
        indices = np.zeros(total, dtype=np.int64)

        # --- Hilbert-sort the leaves ---------------------------------------
        minx, miny = item_boxes[:, 0].min(), item_boxes[:, 1].min()
        maxx, maxy = item_boxes[:, 2].max(), item_boxes[:, 3].max()
        width = max(maxx - minx, 1e-12)
        height = max(maxy - miny, 1e-12)
        cx = (item_boxes[:, 0] + item_boxes[:, 2]) * 0.5
        cy = (item_boxes[:, 1] + item_boxes[:, 3]) * 0.5
        hx = np.clip(((cx - minx) / width * (_HILBERT_SIDE - 1)), 0, _HILBERT_SIDE - 1).astype(np.int64)
        hy = np.clip(((cy - miny) / height * (_HILBERT_SIDE - 1)), 0, _HILBERT_SIDE - 1).astype(np.int64)
        order = np.argsort(hilbert_d(hx, hy), kind="stable")

        boxes[:n] = item_boxes[order]
        indices[:n] = order

        # --- pack parents bottom-up -----------------------------------------
        level_bounds = [n]
        read_at = 0
        write_at = n
        for lvl in range(1, len(level_sizes)):
            count = level_sizes[lvl]
            for k in range(count):
                lo = read_at + k * self._node_size
                hi = min(lo + self._node_size, read_at + level_sizes[lvl - 1])
                child = boxes[lo:hi]
                boxes[write_at + k] = (
                    child[:, 0].min(), child[:, 1].min(),
                    child[:, 2].max(), child[:, 3].max(),
                )
                # A node stores the offset of its first child, which is how a
                # descent finds its children without a pointer per node.
                indices[write_at + k] = lo
            read_at = write_at
            write_at += count
            # The END of the level. A descent needs it to know where a node's
            # children stop, since the last node of a level is usually not full.
            level_bounds.append(write_at)

        self._boxes = boxes
        self._indices = indices
        self._level_bounds = level_bounds

        leaf_area = (item_boxes[:, 2] - item_boxes[:, 0]) * (item_boxes[:, 3] - item_boxes[:, 1])
        self.stats = {
            "fences": n,
            "nodes": total,
            "levels": len(level_sizes),
            "node_size": self._node_size,
            "extent_deg": [round(float(width), 4), round(float(height), 4)],
            "median_bbox_deg2": float(np.median(leaf_area)),
            "max_bbox_deg2": float(leaf_area.max()),
            "bbox_area_ratio": float(leaf_area.max() / max(leaf_area.min(), 1e-18)),
        }

    # -- queries -------------------------------------------------------------

    def query_box(self, min_lon: float, min_lat: float,
                  max_lon: float, max_lat: float) -> list:
        """Every fence whose bounding box intersects the query box."""
        if self._n == 0:
            return []
        boxes = self._boxes
        indices = self._indices
        node_size = self._node_size
        level_bounds = self._level_bounds
        leaf_end = level_bounds[0]

        # Start at the root: the single node in the last level.
        stack: list[tuple[int, int]] = [(len(boxes) - 1, len(level_bounds) - 1)]
        hits: list[int] = []

        while stack:
            node, level = stack.pop()
            if level == 0:
                # A leaf slot: `node` indexes an item box directly.
                b = boxes[node]
                if not (max_lon < b[0] or max_lat < b[1] or min_lon > b[2] or min_lat > b[3]):
                    hits.append(int(indices[node]))
                continue

            start = int(indices[node])
            end = min(start + node_size, level_bounds[level - 1])
            for child in range(start, end):
                b = boxes[child]
                if max_lon < b[0] or max_lat < b[1] or min_lon > b[2] or min_lat > b[3]:
                    continue
                if level - 1 == 0:
                    if child < leaf_end:
                        hits.append(int(indices[child]))
                else:
                    stack.append((child, level - 1))

        return [self._fences[i] for i in hits]

    def query_point(self, lat: float, lon: float) -> list:
        """Every fence whose bounding box covers the point."""
        return self.query_box(lon, lat, lon, lat)

    def candidates_for_trail(
        self, lats: np.ndarray, lons: np.ndarray,
        chunk: int = 512, pad_deg: float = 0.0,
    ) -> list:
        """Fences worth testing against a whole trajectory.

        The trail is cut into chunks and each chunk's own bounding box is
        queried, rather than querying one box around the entire trip. On a
        Jamshedpur to Delhi run the whole-trip box is a 1,300 km rectangle
        covering half of northern India and selects most of the master; the
        per-chunk boxes track the road and select the handful of sites the
        truck actually passed. Same answer, a small fraction of the
        candidates.

        Chunking cannot lose a fence: every fix belongs to exactly one chunk,
        and a fence containing that fix intersects that chunk's box.
        """
        n = len(lats)
        if n == 0 or self._n == 0:
            return []
        seen: dict[int, object] = {}
        for start in range(0, n, chunk):
            stop = min(start + chunk, n)
            sl_lat = lats[start:stop]
            sl_lon = lons[start:stop]
            found = self.query_box(
                float(sl_lon.min()) - pad_deg, float(sl_lat.min()) - pad_deg,
                float(sl_lon.max()) + pad_deg, float(sl_lat.max()) + pad_deg,
            )
            for f in found:
                if f.fence_id not in seen:
                    seen[f.fence_id] = f
        return list(seen.values())

    def explain_box(self, min_lon: float, min_lat: float,
                    max_lon: float, max_lat: float) -> dict:
        """`query_box`, plus a count of the work it did.

        Same descent, same answer; it additionally counts how many node boxes
        were opened and how many were rejected at each level. That is the
        number that shows the tree is doing its job -- a query touching 30 of
        4,900 leaves has skipped 99.4% of the master on four float comparisons
        per skipped subtree -- and it is not worth carrying in the hot path,
        so it lives in its own method rather than as a flag on the fast one.
        """
        if self._n == 0:
            return {"hits": [], "visited": 0, "rejected": 0, "per_level": {}}
        boxes, indices = self._boxes, self._indices
        level_bounds = self._level_bounds
        stack: list[tuple[int, int]] = [(len(boxes) - 1, len(level_bounds) - 1)]
        hits: list[int] = []
        visited = rejected = 0
        per_level: dict[int, list[int]] = {}

        while stack:
            node, level = stack.pop()
            if level == 0:
                # A single-fence master: the root *is* the leaf.
                visited += 1
                b = boxes[node]
                if max_lon < b[0] or max_lat < b[1] or min_lon > b[2] or min_lat > b[3]:
                    rejected += 1
                else:
                    hits.append(int(indices[node]))
                continue
            start = int(indices[node])
            end = min(start + self._node_size, level_bounds[level - 1])
            for child in range(start, end):
                visited += 1
                seen = per_level.setdefault(level - 1, [0, 0])
                seen[0] += 1
                b = boxes[child]
                if max_lon < b[0] or max_lat < b[1] or min_lon > b[2] or min_lat > b[3]:
                    rejected += 1
                    seen[1] += 1
                    continue
                if level - 1 == 0:
                    if child < level_bounds[0]:
                        hits.append(int(indices[child]))
                else:
                    stack.append((child, level - 1))

        return {
            "hits": [self._fences[i] for i in hits],
            "visited": visited,
            "rejected": rejected,
            "per_level": {lvl: {"tested": v[0], "rejected": v[1]} for lvl, v in per_level.items()},
        }

    # -- introspection, for showing the tree ---------------------------------

    def node_boxes(self, level: int) -> np.ndarray:
        """Every node box at one level, as (min_lon, min_lat, max_lon, max_lat).

        Level 0 is the leaves (one box per fence, in Hilbert order); the last
        level is the single root. Drawn on a map these are what make the
        packing visible: tight, compact parent boxes mean the Hilbert sort put
        neighbouring fences in the same node, which is exactly what decides
        how many subtrees a query has to open.
        """
        if self._n == 0 or not (0 <= level < len(self._level_bounds)):
            return np.zeros((0, 4), dtype=np.float64)
        start = 0 if level == 0 else self._level_bounds[level - 1]
        return self._boxes[start:self._level_bounds[level]]

    def leaf_order(self) -> np.ndarray:
        """Fence positions in Hilbert order -- the curve's path through the
        master. `_indices[:n]` maps each leaf slot back to its fence."""
        return self._indices[:self._n].copy()

    @property
    def levels(self) -> int:
        return len(self._level_bounds)

    # -- accessors -----------------------------------------------------------

    def by_site(self, site_id: int):
        return self._by_site.get(site_id)

    def by_id(self, fence_id: int):
        return self._by_id.get(fence_id)

    @property
    def fences(self) -> list:
        return list(self._fences)

    def __len__(self) -> int:
        return self._n
