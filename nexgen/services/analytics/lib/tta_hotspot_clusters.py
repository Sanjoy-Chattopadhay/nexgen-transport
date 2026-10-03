"""
Pilferage Hotspot Discovery — Phase 2: masking, clustering, scoring.

Takes the coordinate-level stop corpus built by tta_hotspots (Phase 1) and
answers a different question. Per-trip anomaly detection asks "was this stop
suspicious?", which drowns in false positives because any single stop has a
hundred innocent explanations. Aggregating by LOCATION asks "why does this
coordinate attract stops?" — and coordinates don't have excuses.

PIPELINE
--------
  1. refresh_facility_anchors — learn where plants/consignees physically are,
     since the feed gives node NAMES and no coordinates anywhere.
  2. classify_stops           — mask stops at plants, consignees, and named
     nodes. Recorded on the row, never deleted: an investigator must be able to
     see what was filtered and why.
  3. _dbscan                  — density clustering over what survives. Density
     based, so it finds arbitrary-shaped clusters without being told how many.
  4. _score                   — rank by carriers / vehicles / dwell band /
     isolation / night share.

REBUILD SEMANTICS (the opposite of Phase 1, deliberately)
---------------------------------------------------------
Everything here is a pure re-derivation from gps_stop_events, so a full rebuild
is correct and safe — unlike stop extraction, which is append-only because it
is the evidence itself. Investigator verdicts live in gps_hotspot_labels and
are keyed on COORDINATES, not cluster ids, precisely so a rebuild cannot
detach them.

WHY THE SCORE IS A SUM AND NOT A PRODUCT
----------------------------------------
The obvious formulation is vehicles x carriers x night_share x dwell. Measured
against this corpus that is actively wrong: the strongest multi-carrier sites
here run 0-21% night share, so a product would multiply them to exactly zero
and drop the best candidates off the report. Night is ONE weak signal among
several, it is not a gate. Each term is squashed to 0-1 and weighted, and the
per-term contributions are stored so a rank is always explainable.
"""

import json
import logging
import re
from math import radians, cos, floor, exp

from nexgen.services.analytics.lib.tta_hotspots import _haversine_m

logger = logging.getLogger(__name__)

# --- Masking ---------------------------------------------------------------
# Matches tta_plant_delay.IN_PLANT_KM: within 3 km of a plant/consignee anchor
# is that facility, not a roadside stop.
FACILITY_MASK_M = 3000
# Named nodes are points, not areas — a tight radius. Toll plazas and named
# halts are already in the feed's ontology and get removed here.
NODE_MASK_M = 400
# An anchor derived from too few trips, or whose trips disagree wildly about
# where the place is, is not trustworthy enough to mask with.
ANCHOR_MIN_TRIPS = 2
ANCHOR_MAX_SPREAD_M = 15000

# --- Clustering ------------------------------------------------------------
# A facility footprint plus GPS error. Large enough to join stops at opposite
# ends of a yard, small enough not to merge two neighbours.
EPS_M = 250
MIN_SAMPLES = 4

# Nearest-named-node lookup for a cluster centroid (display + isolation). Cell
# is coarse because this search ranges far; the DBSCAN grid stays at EPS_M.
NEAREST_NODE_CELL_M = 2000
NEAREST_NODE_MAX_M = 50000

# --- Scoring ---------------------------------------------------------------
# Reference points at which a term saturates (contributes 1.0).
VEHICLES_REF = 25
CARRIERS_REF = 10
ISOLATION_REF_M = 10000
DWELL_PEAK_MIN = 45.0      # centre of the 30-60 min signature
DWELL_SIGMA_MIN = 30.0
# Pseudo-count for shrinking a cluster's night share toward the fleet baseline.
# A 4-stop cluster that happens to be all-night should not outrank a 40-stop
# cluster at 0.6 — with k=5 the small cluster is pulled most of the way back.
NIGHT_PRIOR_K = 5.0

DEFAULT_WEIGHTS = {
    "carriers": 0.30,     # the discriminator: one driver's tea break is noise
    "vehicles": 0.20,
    "isolation": 0.20,
    "dwell": 0.15,
    "night": 0.15,        # deliberately low — see module docstring
}

# Support gate. Below this a cluster is stored but flagged, never silently cut.
MIN_STOPS = 4
MIN_VEHICLES = 3
MIN_CARRIERS = 2

# Distance within which an investigator label attaches to a cluster.
LABEL_MATCH_M = 300

# --- Amenity demotion ------------------------------------------------------
# The feed names its own nodes, and those names give the game away: the first
# build's top 15 was led by "TOLL Plaza Ranchi Ramgarh", "DEEPSHIKHA_PETROLEUM
# (BPCL)", "BEHIND TALESHWAR LINE HOTEL", "Bokaro parking in/out". These sit
# just outside NODE_MASK_M, so geometry alone will not remove them.
#
# They are DEMOTED, not deleted — same principle as masking. A cluster 500 m
# from a dhaba is still worth being able to look at; it just should not lead
# the queue. Word-boundary matched so short tokens (HP, IOC) cannot fire inside
# unrelated words.
AMENITY_PATTERNS = [
    ("fuel", r"\b(PETROL|PETROLEUM|BPCL|HPCL|IOCL|IOC|HP|BHARAT PETRO|INDIAN OIL|"
             r"RELIANCE|ESSAR|NAYARA|FUEL|FILLING|PUMP)\b"),
    ("food_rest", r"\b(HOTEL|DHABA|DABA|RESTAURANT|LINE HOTEL|MOTEL|RESORT|CANTEEN)\b"),
    ("toll", r"\b(TOLL|TOLLPLAZA)\b"),
    ("parking", r"\b(PARKING|YARD|TRANSPORT NAGAR|TPT NAGAR|TRUCK STAND|BUS STAND)\b"),
    ("weighbridge", r"\b(WEIGH|WEIGHBRIDGE|KANTA|DHARMKANTA)\b"),
]
# Beyond this the node's name no longer describes the cluster, so the hint is
# not applied.
AMENITY_HINT_M = 2000

SETTINGS_KEY = "hotspot_scoring"


# ============================================
# HELPERS
# ============================================

def _median(seq):
    s = sorted(seq)
    return s[len(s) // 2] if s else None


def _f(v):
    """pymysql hands back Decimal for DECIMAL columns."""
    return float(v) if v is not None else None


class _SpatialGrid:
    """Uniform lat/lng bucket index for radius queries.

    Cell width in degrees is sized off the WIDEST latitude in the set, so every
    cell is at least `cell_m` across in real metres everywhere in the set.
    Oversized cells only cost a few extra candidates; undersized ones would
    silently miss neighbours. Every candidate is confirmed with real haversine,
    so the grid only ever affects speed, not correctness.
    """

    def __init__(self, points, cell_m: float):
        self.points = points
        self.cell_m = cell_m
        max_lat = max((abs(p[0]) for p in points), default=0.0)
        self.dlat = cell_m / 111320.0
        self.dlng = cell_m / (111320.0 * max(cos(radians(max_lat)), 0.1))
        self.cells: dict = {}
        for i, (la, lo) in enumerate(points):
            self.cells.setdefault(self._key(la, lo), []).append(i)

    def _key(self, la, lo):
        return (floor(la / self.dlat), floor(lo / self.dlng))

    def near(self, la, lo, radius_m: float) -> list[int]:
        span = int(radius_m / self.cell_m) + 1
        cx, cy = self._key(la, lo)
        out = []
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                out.extend(self.cells.get((cx + dx, cy + dy), ()))
        return [i for i in out
                if _haversine_m(la, lo, self.points[i][0], self.points[i][1]) <= radius_m]

    def any_within(self, la, lo, radius_m: float) -> int | None:
        hits = self.near(la, lo, radius_m)
        if not hits:
            return None
        return min(hits, key=lambda i: _haversine_m(la, lo, self.points[i][0], self.points[i][1]))

    def nearest(self, la, lo, max_m: float) -> tuple[int, float] | None:
        """Nearest point, searched in expanding rings.

        A single near(max_m) call would span (max_m / cell_m)^2 cells — at a
        250 m cell and a 50 km ceiling that is 160k cell lookups per query.
        Expanding means the common case (a node right there) costs one small
        ring, and only genuinely isolated centroids pay for the wide sweep.
        """
        radius = self.cell_m
        while radius <= max_m:
            hits = self.near(la, lo, radius)
            if hits:
                j = min(hits, key=lambda i: _haversine_m(la, lo, self.points[i][0],
                                                         self.points[i][1]))
                return j, _haversine_m(la, lo, self.points[j][0], self.points[j][1])
            radius *= 4
        return None


# ============================================
# 1. FACILITY ANCHORS
# ============================================

def refresh_facility_anchors(conn) -> dict:
    """Learn plant/consignee coordinates from the ping trail.

    A trip's first ping is at its origin and its last ping is at its
    destination. Median those across every trip sharing a node name and the
    facility's position falls out — the feed itself never provides it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT i_trip_no, s_org_node_name, s_dest_node_name
               FROM tta_trips WHERE i_gps_ping_count > 0"""
        )
        trips = cur.fetchall()

        by_node: dict = {}
        for t in trips:
            for role, name in (("origin", t["s_org_node_name"]),
                               ("destination", t["s_dest_node_name"])):
                if not name:
                    continue
                order = "ASC" if role == "origin" else "DESC"
                cur.execute(
                    f"""SELECT d_lat, d_long FROM tta_trip_gps
                        WHERE i_trip_no = %s ORDER BY dt_message {order} LIMIT 1""",
                    (t["i_trip_no"],),
                )
                row = cur.fetchone()
                if row:
                    by_node.setdefault((name, role), []).append(
                        (_f(row["d_lat"]), _f(row["d_long"]))
                    )

    written = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gps_facility_anchors")
        for (name, role), pts in by_node.items():
            lat = _median([p[0] for p in pts])
            lng = _median([p[1] for p in pts])
            spread = _median([_haversine_m(lat, lng, p[0], p[1]) for p in pts]) or 0
            cur.execute(
                """INSERT INTO gps_facility_anchors
                   (s_node_name, s_role, d_lat, d_long, i_trips, i_spread_m)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (name, role, round(lat, 8), round(lng, 8), len(pts), int(spread)),
            )
            written += 1
    conn.commit()
    logger.info("Facility anchors refreshed: %d", written)
    return {"status": "ok", "anchors": written}


# ============================================
# 2. MASKING
# ============================================

def classify_stops(conn) -> dict:
    """Tag every stop with the known place it belongs to, or 'unclassified'.

    Masks against ALL facility anchors, not just the stop's own trip's endpoints
    — a truck idling at someone else's plant gate is still at a plant.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s_node_name, s_role, d_lat, d_long FROM gps_facility_anchors
               WHERE i_trips >= %s AND (i_spread_m IS NULL OR i_spread_m <= %s)""",
            (ANCHOR_MIN_TRIPS, ANCHOR_MAX_SPREAD_M),
        )
        anchors = cur.fetchall()
        cur.execute(
            "SELECT d_lat, d_long FROM tta_waypoints WHERE d_lat IS NOT NULL AND d_long IS NOT NULL"
        )
        nodes = cur.fetchall()
        cur.execute("SELECT id, d_lat, d_long FROM gps_stop_events")
        stops = cur.fetchall()

    origin_pts = [(_f(a["d_lat"]), _f(a["d_long"])) for a in anchors if a["s_role"] == "origin"]
    dest_pts = [(_f(a["d_lat"]), _f(a["d_long"])) for a in anchors if a["s_role"] == "destination"]
    node_pts = [(_f(n["d_lat"]), _f(n["d_long"])) for n in nodes]

    g_origin = _SpatialGrid(origin_pts, FACILITY_MASK_M) if origin_pts else None
    g_dest = _SpatialGrid(dest_pts, FACILITY_MASK_M) if dest_pts else None
    g_node = _SpatialGrid(node_pts, NODE_MASK_M) if node_pts else None

    buckets: dict = {}
    for s in stops:
        la, lo = _f(s["d_lat"]), _f(s["d_long"])
        if g_origin and g_origin.any_within(la, lo, FACILITY_MASK_M) is not None:
            cls = "origin"
        elif g_dest and g_dest.any_within(la, lo, FACILITY_MASK_M) is not None:
            cls = "destination"
        elif g_node and g_node.any_within(la, lo, NODE_MASK_M) is not None:
            cls = "named_node"
        else:
            cls = "unclassified"
        buckets.setdefault(cls, []).append(s["id"])

    with conn.cursor() as cur:
        for cls, ids in buckets.items():
            for i in range(0, len(ids), 1000):
                chunk = ids[i:i + 1000]
                ph = ",".join(["%s"] * len(chunk))
                cur.execute(
                    f"UPDATE gps_stop_events SET s_place_class = %s WHERE id IN ({ph})",
                    [cls] + chunk,
                )
    conn.commit()

    counts = {k: len(v) for k, v in buckets.items()}
    logger.info("Stop masking: %s", counts)
    return {"status": "ok", "classified": counts}


# ============================================
# 3. DBSCAN (haversine, grid-indexed)
# ============================================

def _dbscan(points, eps_m: float, min_samples: int) -> list[int]:
    """Standard DBSCAN. Returns a label per point; -1 is noise.

    Hand-rolled rather than sklearn: the backend has no scikit-learn and adding
    it (plus scipy) to the image for one fixed-eps call is a poor trade. The
    grid index makes this exact and linear-ish at our scale.
    """
    grid = _SpatialGrid(points, eps_m)
    n = len(points)
    labels: list = [None] * n
    cid = -1

    for i in range(n):
        if labels[i] is not None:
            continue
        neigh = grid.near(points[i][0], points[i][1], eps_m)
        if len(neigh) < min_samples:
            labels[i] = -1              # noise for now; may become a border point
            continue

        cid += 1
        labels[i] = cid
        seeds = [j for j in neigh if j != i]
        k = 0
        while k < len(seeds):
            j = seeds[k]
            k += 1
            if labels[j] == -1:
                labels[j] = cid         # border point: joins, but does not expand
                continue
            if labels[j] is not None:
                continue
            labels[j] = cid
            jn = grid.near(points[j][0], points[j][1], eps_m)
            if len(jn) >= min_samples:
                seeds.extend(jn)

    return labels


# ============================================
# 4. SCORING
# ============================================

def _load_weights(conn) -> dict:
    """Weights live in app_settings so they are tunable without a deploy —
    the first weeks of this feature are all tuning."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT s_value FROM app_settings WHERE s_key = %s", (SETTINGS_KEY,))
            row = cur.fetchone()
        if row and row["s_value"]:
            val = row["s_value"]
            data = json.loads(val) if isinstance(val, str) else val
            return {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in data.items()
                                          if k in DEFAULT_WEIGHTS}}
    except Exception:
        logger.warning("Could not read %s, using defaults", SETTINGS_KEY, exc_info=True)
    return dict(DEFAULT_WEIGHTS)


def _squash(value: float, ref: float) -> float:
    """Diminishing returns, saturating at `ref`. The 40th truck at a site adds
    less than the 4th did."""
    from math import log1p
    if value <= 0:
        return 0.0
    return min(1.0, log1p(value) / log1p(ref))


def _amenity_hint(node_name: str | None, dist_m: int | None) -> str | None:
    """Classify a cluster by the name of the node it sits next to."""
    if not node_name or dist_m is None or dist_m > AMENITY_HINT_M:
        return None
    upper = node_name.upper()
    for hint, pattern in AMENITY_PATTERNS:
        if re.search(pattern, upper):
            return hint
    return None


def _score(cluster: dict, baseline_night: float, weights: dict) -> tuple[float, dict]:
    night_adj = (
        (NIGHT_PRIOR_K * baseline_night + cluster["stops"] * cluster["night_share"])
        / (NIGHT_PRIOR_K + cluster["stops"])
    )
    dwell = cluster["median_dwell"] or 0.0

    # Isolation is the TRUE distance from the centroid to the nearest named
    # node — not the median of members' i_wpnt_mt, which is the distance to the
    # node *behind* each vehicle and can read as 17 km for a cluster sitting
    # 540 m from a station. Falls back to the member median only if no node
    # registry was available.
    isolation_m = cluster.get("nearest_node_m")
    if isolation_m is None:
        isolation_m = cluster["median_isolation_m"] or 0

    terms = {
        "carriers": _squash(cluster["carriers"], CARRIERS_REF),
        "vehicles": _squash(cluster["vehicles"], VEHICLES_REF),
        "isolation": min(1.0, isolation_m / ISOLATION_REF_M),
        "dwell": exp(-(((dwell - DWELL_PEAK_MIN) / DWELL_SIGMA_MIN) ** 2) / 2),
        # Relative to the fleet: 2x the baseline saturates. An absolute share
        # would penalise every cluster in a fleet that simply runs by day.
        "night": min(1.0, night_adj / max(2 * baseline_night, 1e-6)),
    }
    score = 100.0 * sum(weights[k] * terms[k] for k in weights)
    components = {k: round(weights[k] * terms[k] * 100, 2) for k in weights}
    components["night_share_adj"] = round(night_adj, 4)
    return round(score, 2), components


# ============================================
# 5. ORCHESTRATION
# ============================================

def _build_scope(conn, cnr_id: int, stops: list, node_grid, baseline_night: float,
                 weights: dict) -> int:
    """Cluster + score one consignor scope. Returns clusters written."""
    if len(stops) < MIN_SAMPLES:
        return 0

    points = [(_f(s["d_lat"]), _f(s["d_long"])) for s in stops]
    labels = _dbscan(points, EPS_M, MIN_SAMPLES)

    groups: dict = {}
    for idx, lab in enumerate(labels):
        if lab >= 0:
            groups.setdefault(lab, []).append(idx)

    written = 0
    with conn.cursor() as cur:
        for _, idxs in groups.items():
            members = [stops[i] for i in idxs]
            lat = _median([points[i][0] for i in idxs])
            lng = _median([points[i][1] for i in idxs])
            radius = max(_haversine_m(lat, lng, points[i][0], points[i][1]) for i in idxs)

            vehicles = {m["s_asset_id"] for m in members if m["s_asset_id"]}
            carriers = {m["s_trans_name"] for m in members if m["s_trans_name"]}
            trips = {m["i_trip_no"] for m in members}
            dwells = [_f(m["d_duration_min"]) for m in members]
            nights = sum(1 for m in members if m["i_hour"] < 5 or m["i_hour"] >= 22)
            isolations = [m["i_wpnt_mt"] for m in members if m["i_wpnt_mt"] is not None]

            # True nearest named node from the centroid — more meaningful than
            # the feed's "nearest node behind" that individual stops carry.
            near_name, near_m = None, None
            if node_grid is not None:
                hit = node_grid.nearest(lat, lng, NEAREST_NODE_MAX_M)
                if hit is not None:
                    j, dist = hit
                    near_name, near_m = node_grid.meta[j], int(dist)

            agg = {
                "stops": len(members),
                "vehicles": len(vehicles),
                "carriers": len(carriers),
                "median_dwell": _median(dwells),
                "night_share": nights / len(members),
                "median_isolation_m": _median(isolations),
                "nearest_node_m": near_m,
            }
            score, components = _score(agg, baseline_night, weights)
            amenity = _amenity_hint(near_name, near_m)
            low_support = int(
                agg["stops"] < MIN_STOPS
                or agg["vehicles"] < MIN_VEHICLES
                or agg["carriers"] < MIN_CARRIERS
            )

            cur.execute(
                """INSERT INTO gps_stop_clusters
                   (cnr_id, s_place_key, d_lat, d_long, i_radius_m,
                    i_stops, i_trips, i_vehicles, i_carriers,
                    d_median_dwell_min, d_night_share, d_night_share_adj,
                    i_median_isolation_m, s_nearest_node, i_nearest_node_m,
                    d_score, s_components, b_low_support, s_amenity_hint,
                    dt_first_seen, dt_last_seen)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (cnr_id, f"{lat:.3f},{lng:.3f}", round(lat, 8), round(lng, 8), int(radius),
                 agg["stops"], len(trips), agg["vehicles"], agg["carriers"],
                 agg["median_dwell"], round(agg["night_share"], 4),
                 components["night_share_adj"], agg["median_isolation_m"],
                 near_name, near_m, score, json.dumps(components), low_support, amenity,
                 min(m["dt_start"] for m in members),
                 max(m["dt_start"] for m in members)),
            )
            cluster_id = cur.lastrowid
            for m in members:
                cur.execute(
                    "INSERT IGNORE INTO gps_stop_cluster_members (cluster_id, stop_event_id) "
                    "VALUES (%s, %s)",
                    (cluster_id, m["id"]),
                )
            written += 1
    conn.commit()
    return written


def refresh_clusters(conn, run_masking: bool = True) -> dict:
    """Full Phase 2 rebuild: anchors -> masking -> clustering -> scoring.

    Writes a cnr_id = 0 rollup plus one slice per consignor, following the
    gps_*_agg convention so a scoped read is a plain WHERE with no re-aggregation.
    """
    steps = {}
    if run_masking:
        steps["anchors"] = refresh_facility_anchors(conn)
        steps["masking"] = classify_stops(conn)

    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, i_trip_no, cnr_id, s_asset_id, s_trans_name,
                      dt_start, d_duration_min, i_hour, d_lat, d_long, i_wpnt_mt
               FROM gps_stop_events WHERE s_place_class = 'unclassified'"""
        )
        candidates = cur.fetchall()

        cur.execute(
            """SELECT AVG(i_hour < 5 OR i_hour >= 22) AS b FROM gps_stop_events
               WHERE s_place_class = 'unclassified'"""
        )
        baseline_night = _f(cur.fetchone()["b"]) or 0.0

        cur.execute(
            "SELECT s_wpnt, d_lat, d_long FROM tta_waypoints "
            "WHERE d_lat IS NOT NULL AND d_long IS NOT NULL"
        )
        nodes = cur.fetchall()

        # Rebuilt wholesale: pure re-derivation, no evidentiary value. Labels
        # live in their own coordinate-keyed table and are untouched by this.
        cur.execute("DELETE FROM gps_stop_clusters")
    conn.commit()

    node_grid = None
    if nodes:
        node_grid = _SpatialGrid([(_f(n["d_lat"]), _f(n["d_long"])) for n in nodes],
                                 NEAREST_NODE_CELL_M)
        node_grid.meta = [n["s_wpnt"] for n in nodes]

    weights = _load_weights(conn)
    scopes = {0: candidates}
    for s in candidates:
        if s["cnr_id"]:
            scopes.setdefault(s["cnr_id"], []).append(s)

    total = 0
    per_scope = {}
    for cnr_id, stops in scopes.items():
        n = _build_scope(conn, cnr_id, stops, node_grid, baseline_night, weights)
        per_scope[cnr_id] = n
        total += n

    logger.info("Hotspot clusters rebuilt: %d across %d scopes (baseline night %.3f)",
                total, len(scopes), baseline_night)
    return {
        "status": "ok",
        "candidates": len(candidates),
        "baseline_night_share": round(baseline_night, 4),
        "clusters": total,
        "clusters_by_scope": per_scope,
        "weights": weights,
        **steps,
    }


# ============================================
# 6. READS
# ============================================

def _scope_id(cnr_id):
    return 0 if cnr_id is None else cnr_id


def list_hotspots(conn, limit: int = 50, include_low_support: bool = False,
                  include_amenities: bool = False, cnr_id: int | None = None) -> dict:
    """Ranked investigation queue for one scope.

    Low-support and amenity-adjacent clusters are hidden by default but always
    retrievable — filtered, never deleted, so an investigator can audit what the
    ranking chose to set aside.
    """
    where = "WHERE cnr_id = %s"
    params: list = [_scope_id(cnr_id)]
    if not include_low_support:
        where += " AND b_low_support = 0"
    if not include_amenities:
        where += " AND s_amenity_hint IS NULL"

    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT id, s_place_key, d_lat, d_long, i_radius_m,
                       i_stops, i_trips, i_vehicles, i_carriers,
                       d_median_dwell_min, d_night_share, d_night_share_adj,
                       i_median_isolation_m, s_nearest_node, i_nearest_node_m,
                       d_score, s_components, b_low_support, s_amenity_hint,
                       dt_first_seen, dt_last_seen
                FROM gps_stop_clusters {where}
                ORDER BY d_score DESC LIMIT %s""",
            params + [min(max(limit, 1), 500)],
        )
        items = cur.fetchall()

        cur.execute(
            """SELECT COUNT(*) AS clusters, SUM(b_low_support) AS low_support,
                      SUM(s_amenity_hint IS NOT NULL) AS amenity_adjacent,
                      SUM(i_stops) AS stops, MAX(d_score) AS top_score
               FROM gps_stop_clusters WHERE cnr_id = %s""",
            [_scope_id(cnr_id)],
        )
        kpis = cur.fetchone()

        cur.execute("SELECT id, d_lat, d_long, s_status, s_note FROM gps_hotspot_labels "
                    "ORDER BY dt_created DESC")
        labels = cur.fetchall()

    for it in items:
        if it.get("s_components"):
            try:
                it["s_components"] = json.loads(it["s_components"])
            except Exception:
                pass
        it["label"] = _match_label(it, labels)

    return {"items": items, "kpis": kpis}


def _match_label(cluster, labels):
    """Most recent verdict recorded within LABEL_MATCH_M of this centroid."""
    la, lo = _f(cluster["d_lat"]), _f(cluster["d_long"])
    for lb in labels:                      # already newest-first
        if _haversine_m(la, lo, _f(lb["d_lat"]), _f(lb["d_long"])) <= LABEL_MATCH_M:
            return {"status": lb["s_status"], "note": lb["s_note"]}
    return None


def _is_night(hour) -> bool:
    """Same 22:00–05:00 boundary the corpus stats and night_share use."""
    h = int(hour or 0)
    return h < 5 or h >= 22


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2, 1)


def rollup_members(members: list[dict], key: str) -> list[dict]:
    """Group the evidence rows by vehicle or carrier.

    Computed in PYTHON from the very rows the evidence table lists, not by a
    second GROUP BY against the database. A count in a KPI and the rows behind
    it have to be the same fact — re-deriving the summary from a separate query
    is how the two drift apart and why nobody trusts either.

    `key` is 's_asset_id' (vehicle) or 's_trans_name' (carrier).
    """
    groups: dict[str, dict] = {}
    for m in members:
        gid = m.get(key) or "(unknown)"
        durations_key = "_durations"
        g = groups.get(gid)
        if g is None:
            g = groups[gid] = {
                key: gid, "stops": 0, "night_stops": 0,
                "total_dwell_min": 0.0, "longest_dwell_min": 0.0,
                "first_seen": None, "last_seen": None,
                "vehicles": set(), "carriers": set(), "trips": set(),
                durations_key: [],
            }
        dur = float(m.get("d_duration_min") or 0)
        g["stops"] += 1
        g["total_dwell_min"] += dur
        g["longest_dwell_min"] = max(g["longest_dwell_min"], dur)
        g[durations_key].append(dur)
        if _is_night(m.get("i_hour")):
            g["night_stops"] += 1
        if m.get("s_asset_id"):
            g["vehicles"].add(m["s_asset_id"])
        if m.get("s_trans_name"):
            g["carriers"].add(m["s_trans_name"])
        if m.get("i_trip_no") is not None:
            g["trips"].add(m["i_trip_no"])
        start = m.get("dt_start")
        if start is not None:
            if g["first_seen"] is None or start < g["first_seen"]:
                g["first_seen"] = start
            if g["last_seen"] is None or start > g["last_seen"]:
                g["last_seen"] = start

    out = []
    for g in groups.values():
        durations = g.pop("_durations")
        g["median_dwell_min"] = _median(durations)
        g["total_dwell_min"] = round(g["total_dwell_min"], 1)
        g["longest_dwell_min"] = round(g["longest_dwell_min"], 1)
        g["night_share"] = round(g["night_stops"] / g["stops"], 4) if g["stops"] else 0.0
        g["vehicles"] = len(g["vehicles"])
        g["carriers"] = len(g["carriers"])
        g["trips"] = len(g["trips"])
        out.append(g)
    # Most time standing still first — that is the thing worth investigating,
    # not whoever happens to have the most short stops.
    out.sort(key=lambda r: (-r["total_dwell_min"], -r["stops"]))
    return out


def stop_records(conn, cnr_id: int | None = None, clustered: bool = True,
                 limit: int = 500) -> list[dict]:
    """The individual stops behind a corpus-level count.

    `clustered=True`  -> only stops that landed in a cluster (the "Stops
                         clustered" KPI), carrying their cluster's place and score.
    `clustered=False` -> every extracted stop event (the "Stop corpus" KPI),
                         including ones masked as a plant, consignee or amenity —
                         `s_place_class` says which, so what the pipeline set
                         aside stays auditable.
    """
    limit = min(max(int(limit), 1), 2000)

    if clustered:
        # Scope on the CLUSTER, exactly as list_hotspots does. Clusters exist
        # once per consignor AND once as the cnr_id=0 rollup, so every stop is
        # a member of two of them — joining the members table without choosing
        # a scope returns each stop twice, and a drill-down showing double its
        # own KPI is worse than none.
        params: list = [_scope_id(cnr_id)]
        clause = "WHERE c.cnr_id = %s"
    else:
        # The corpus is raw events, which carry the real consignor: unscoped
        # means everything, matching stop_extraction_status.
        params = []
        clause = ""
        if cnr_id:
            clause = "WHERE e.cnr_id = %s"
            params.append(cnr_id)

    if clustered:
        sql = f"""SELECT e.id, e.i_trip_no, e.s_asset_id, e.s_trans_name,
                         e.dt_start, e.dt_end, e.d_duration_min, e.i_hour,
                         e.d_lat, e.d_long, e.s_wpnt, e.i_wpnt_mt,
                         e.s_place_class,
                         c.id AS cluster_id, c.d_score, c.s_nearest_node,
                         t.s_org_node_name, t.s_dest_node_name, t.s_driver_name
                  FROM gps_stop_cluster_members m
                  JOIN gps_stop_events e ON e.id = m.stop_event_id
                  JOIN gps_stop_clusters c ON c.id = m.cluster_id
                  LEFT JOIN tta_trips t ON t.i_trip_no = e.i_trip_no
                  {clause}
                  ORDER BY e.dt_start DESC LIMIT %s"""
    else:
        sql = f"""SELECT e.id, e.i_trip_no, e.s_asset_id, e.s_trans_name,
                         e.dt_start, e.dt_end, e.d_duration_min, e.i_hour,
                         e.d_lat, e.d_long, e.s_wpnt, e.i_wpnt_mt,
                         e.s_place_class,
                         NULL AS cluster_id, NULL AS d_score,
                         NULL AS s_nearest_node,
                         t.s_org_node_name, t.s_dest_node_name, t.s_driver_name
                  FROM gps_stop_events e
                  LEFT JOIN tta_trips t ON t.i_trip_no = e.i_trip_no
                  {clause}
                  ORDER BY e.dt_start DESC LIMIT %s"""

    with conn.cursor() as cur:
        cur.execute(sql, params + [limit])
        rows = cur.fetchall()

    for r in rows:
        r["route"] = f"{r.get('s_org_node_name') or '?'} -> {r.get('s_dest_node_name') or '?'}"
        r["night"] = _is_night(r.get("i_hour"))
        if r.get("d_duration_min") is not None:
            r["d_duration_min"] = float(r["d_duration_min"])
        if r.get("d_score") is not None:
            r["d_score"] = float(r["d_score"])
    return rows


def hotspot_detail(conn, cluster_id: int) -> dict | None:
    """One cluster with its member stops and the trips behind them —
    the evidence pack."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM gps_stop_clusters WHERE id = %s", (cluster_id,))
        cluster = cur.fetchone()
        if not cluster:
            return None
        if cluster.get("s_components"):
            try:
                cluster["s_components"] = json.loads(cluster["s_components"])
            except Exception:
                pass

        cur.execute(
            """SELECT e.id, e.i_trip_no, e.s_asset_id, e.s_trans_name,
                      e.dt_start, e.dt_end, e.d_duration_min, e.i_hour,
                      e.d_lat, e.d_long, e.i_wpnt_mt, e.s_wpnt,
                      t.s_org_node_name, t.s_dest_node_name, t.s_driver_name
               FROM gps_stop_cluster_members m
               JOIN gps_stop_events e ON e.id = m.stop_event_id
               LEFT JOIN tta_trips t ON t.i_trip_no = e.i_trip_no
               WHERE m.cluster_id = %s
               ORDER BY e.dt_start DESC""",
            (cluster_id,),
        )
        members = cur.fetchall()

        cur.execute(
            """SELECT e.i_hour AS h, COUNT(*) AS n
               FROM gps_stop_cluster_members m
               JOIN gps_stop_events e ON e.id = m.stop_event_id
               WHERE m.cluster_id = %s GROUP BY e.i_hour""",
            (cluster_id,),
        )
        hours = [0] * 24
        for r in cur.fetchall():
            hours[int(r["h"])] = r["n"]

        cur.execute(
            """SELECT e.s_trans_name AS carrier, COUNT(*) AS stops,
                      COUNT(DISTINCT e.s_asset_id) AS vehicles
               FROM gps_stop_cluster_members m
               JOIN gps_stop_events e ON e.id = m.stop_event_id
               WHERE m.cluster_id = %s
               GROUP BY e.s_trans_name ORDER BY stops DESC""",
            (cluster_id,),
        )
        carrier_mix = cur.fetchall()

    for m in members:
        m["route"] = f"{m.get('s_org_node_name') or '?'} -> {m.get('s_dest_node_name') or '?'}"

    # Both rollups come from `members`, so "16 vehicles" and the vehicle list
    # under it are one fact rendered twice — they cannot disagree.
    return {"cluster": cluster, "members": members,
            "hour_profile": hours, "carrier_mix": carrier_mix,
            "vehicle_mix": rollup_members(members, "s_asset_id"),
            "carrier_rollup": rollup_members(members, "s_trans_name")}


def add_label(conn, lat: float, lng: float, status: str, note: str = "",
              labelled_by: str = "") -> dict:
    """Record an investigator verdict against a PLACE. Append-only: a changed
    verdict is a new row, so the decision history survives."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO gps_hotspot_labels (d_lat, d_long, s_status, s_note, s_labelled_by)
               VALUES (%s,%s,%s,%s,%s)""",
            (round(float(lat), 8), round(float(lng), 8), status, note[:1000], labelled_by[:100]),
        )
        new_id = cur.lastrowid
    conn.commit()
    return {"status": "ok", "id": new_id}
