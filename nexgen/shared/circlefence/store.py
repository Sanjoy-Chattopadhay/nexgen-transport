"""Persistence for the geofence module: schema, fence seeding, index loading.

The module owns three pieces of state:

  * `geofences`                 -- the fence definitions
  * `tta_trips.dt_geofence_out` -- the origin-exit stamp per trip
  * `tta_trip_geofence_events`  -- the full crossing ledger

Where fence centres come from
-----------------------------
The upstream feed gives node *names* and no coordinates anywhere -- there is
no geofence geometry to import. `gps_facility_anchors` already solves that by
taking the medoid of the pings actually observed at each node, so the seeder
reads from there rather than inventing a second derivation.

Radius is sized off `i_spread_m`, the observed scatter of the pings that
produced the anchor, clamped into [MIN_RADIUS_M, MAX_RADIUS_M]. A tight,
well-observed plant gets a tight fence; a node seen twice at opposite ends of
a town does not get a 40 km circle that swallows half the route.

Anchors are not trusted blindly. A destination anchor is the medoid of the
trips' *last* pings, so on a lane whose trackers usually die inside the works
it lands in the works: the "SANAND" anchor sits at Jamshedpur coordinates,
1,417 km from Sanand. Every anchor is therefore cross-checked against the
offline gazetteer and replaced when it disagrees -- see the "Anchor sanity
checking" note below, which is the single most important correctness rule in
this module.

Hand corrections are protected on top of that: a row with `b_manual = 1` is
never overwritten by a reseed, so an operator who fixes a fence the gazetteer
cannot adjudicate keeps that fix.
"""

from __future__ import annotations

import logging

from nexgen.shared.legacy_db import get_connection
from nexgen.shared.analysis import tta_geo
from . import plants
from .index import Geofence, GeofenceIndex, haversine_m, normalise_key

logger = logging.getLogger(__name__)

MIN_RADIUS_M = 1000
MAX_RADIUS_M = 5000
DEFAULT_RADIUS_M = 3000

# Anchors built from very few trips are not trustworthy enough to fence on.
MIN_ANCHOR_TRIPS = 2

# ---------------------------------------------------------------------------
# Radius overrides
#
# A few nodes are complexes, not points, and the anchor medoid cannot know
# that. They live here rather than as a hand-run UPDATE so the reasoning
# survives a reseed and stays reviewable.
#
# JAMSHEDPUR: every zonal trip is booked with origin "JAMSHEDPUR", but the
# trucks physically load at units spread across the works belt -- CRM BARA,
# TSPDL BARA, BEEKAY STEEL, Adityapur, Gamharia. Measured against the corpus,
# the distance from the medoid to a trip's first ping has a hard shoulder:
# p50 1.8 km, p90 9.7 km, p99 14.95 km, and then nothing at all until 246 km
# (three mislabelled outliers). A 2.7 km fence covers 62% of trips and reports
# the other 38% as "never inside the origin"; 15 km covers 99.4% and stops
# cleanly before it could swallow any leg of a real route.
#
# Consequence worth stating: with this radius, dt_geofence_out means "cleared
# the Jamshedpur works belt", not "crossed the works boundary wall". That is
# the figure the detention question asks for, but tighten it here if the
# narrower reading is ever wanted.
# ---------------------------------------------------------------------------
RADIUS_OVERRIDES: dict[tuple[str, str], int] = {
    ("JAMSHEDPUR", "origin"): 15_000,
}

# ---------------------------------------------------------------------------
# Anchor sanity checking
#
# `gps_facility_anchors` takes the medoid of the pings observed at a node --
# which for a destination means the medoid of the trips' LAST pings. That
# derivation is contaminated by the exact failure this module reports on: when
# a lane's trackers usually die inside the works, the "destination anchor" for
# that lane lands in the works.
#
# Measured on this corpus, 21 of 79 destination anchors that have a city
# reference are more than 25 km from it, and 18 of those sit within a
# kilometre of Jamshedpur -- FARIDABAD, HYDERABAD, SANAND, BIDADI, RANJANGAON
# and friends, all nominally 1000+ km away.
#
# Left uncorrected this is not merely imprecise, it inverts the destination
# reports: a trip whose GPS died at the origin scores as "arrived" (it is
# right next to the bogus fence) and a trip that genuinely reached Faridabad
# scores as "stopped 1086 km short".
#
# So every anchor is checked against the offline gazetteer in tta_geo. Past
# ANCHOR_SANITY_KM the anchor loses and the gazetteer coordinate is used, with
# a city-scale radius because a city centroid locates a town, not a gate.
# ---------------------------------------------------------------------------
ANCHOR_SANITY_KM = 25.0
GAZETTEER_RADIUS_M = 20_000

# Node names that are placeholders, not places. The feed uses them when the
# destination was never filled in, and an anchor built from them is the medoid
# of unrelated trips -- "UNKNOWN" resolves to 300 m from the Jamshedpur origin.
UNRESOLVABLE_KEYS = {"UNKNOWN", "N/A", "NA", "NULL", "-", "", "TBD", "TEST"}

# The gazetteer can only vouch for names it knows. A second, data-driven guard
# covers the rest: a destination anchor is only credible if at least one trip
# to that destination actually COMPLETED there.
#
# Trips closed as "NEW TRIP FOUND..." were superseded by a later record, not
# delivered -- the truck never reached the destination on that trip, so its
# pings say nothing about where the destination is. KICHHA is the worked
# example: a town in Uttarakhand ~1,300 km away, whose only two trips were both
# superseded while still in the works, giving it a fence 1.5 km from Jamshedpur.
# It passed the gazetteer check purely because the gazetteer has no KICHHA.
SUPERSEDED_CLOSE_PREFIX = "NEW TRIP FOUND"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_COLUMNS = {
    "dt_geofence_out": (
        "ALTER TABLE tta_trips ADD COLUMN dt_geofence_out DATETIME NULL "
        "COMMENT 'Sustained exit from the origin geofence, from the GPS trail'"
    ),
    "i_geofence_out_gap_min": (
        "ALTER TABLE tta_trips ADD COLUMN i_geofence_out_gap_min INT NULL "
        "COMMENT 'Ping gap after the exit ping = uncertainty on dt_geofence_out'"
    ),
    "s_geofence_out_status": (
        "ALTER TABLE tta_trips ADD COLUMN s_geofence_out_status VARCHAR(24) NULL "
        "COMMENT 'ok | intra_fence | gps_died_at_origin | never_inside | never_exited | no_gps | no_fence'"
    ),
    # The three detention windows, as GENERATED ... STORED columns rather than
    # plain ones. MySQL recomputes them on every write, so they cannot drift
    # from the timestamps they come from and there is no backfill to forget
    # after an ETL cycle. See migrations/schema_detention_windows.sql.
    "i_works_detention_min": (
        "ALTER TABLE tta_trips ADD COLUMN i_works_detention_min INT "
        "GENERATED ALWAYS AS (TIMESTAMPDIFF(MINUTE, dt_booking, dt_trip_start)) STORED "
        "COMMENT 'A: plant entry to gate-out. The detention the TMS declares.'"
    ),
    "i_geofence_tail_min": (
        "ALTER TABLE tta_trips ADD COLUMN i_geofence_tail_min INT "
        "GENERATED ALWAYS AS (TIMESTAMPDIFF(MINUTE, dt_trip_start, dt_geofence_out)) STORED "
        "COMMENT 'B: gate-out to clearing the plant geofence. The hidden tail.'"
    ),
    "i_origin_total_min": (
        "ALTER TABLE tta_trips ADD COLUMN i_origin_total_min INT "
        "GENERATED ALWAYS AS (TIMESTAMPDIFF(MINUTE, dt_booking, dt_geofence_out)) STORED "
        "COMMENT 'A+B: plant entry to clearing the geofence. True hold at origin.'"
    ),
}

_TABLES = {
    "geofences": """
        CREATE TABLE IF NOT EXISTS geofences (
            i_fence_id      INT AUTO_INCREMENT PRIMARY KEY,
            s_key           VARCHAR(255) NOT NULL,
            s_name          VARCHAR(255) NOT NULL,
            s_role          VARCHAR(12)  NOT NULL,
            d_lat           DECIMAL(10,8) NOT NULL,
            d_long          DECIMAL(11,8) NOT NULL,
            i_radius_m      INT NOT NULL DEFAULT 3000,
            i_trips_seen    INT DEFAULT 0,
            i_spread_m      INT NULL,
            b_manual        TINYINT(1) NOT NULL DEFAULT 0,
            b_active        TINYINT(1) NOT NULL DEFAULT 1,
            s_source        VARCHAR(20) NOT NULL DEFAULT 'anchor',
            dt_created      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dt_modified     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_geofence_key_role (s_key, s_role),
            KEY idx_geofence_active (b_active, s_role)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    "tta_trip_geofence_events": """
        CREATE TABLE IF NOT EXISTS tta_trip_geofence_events (
            id              BIGINT AUTO_INCREMENT PRIMARY KEY,
            i_trip_no       BIGINT NOT NULL,
            i_fence_id      INT NOT NULL,
            s_fence_key     VARCHAR(255) NOT NULL,
            s_role          VARCHAR(12) NOT NULL,
            s_event         VARCHAR(8) NOT NULL,
            dt_event        DATETIME NOT NULL,
            i_gap_min       INT NULL,
            d_lat           DECIMAL(10,8) NOT NULL,
            d_long          DECIMAL(11,8) NOT NULL,
            KEY idx_gfe_trip (i_trip_no),
            KEY idx_gfe_fence (i_fence_id, s_event),
            KEY idx_gfe_time (dt_event)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
}


def ensure_schema(conn=None) -> dict:
    """Create the geofence tables and columns if absent. Idempotent.

    `ADD COLUMN IF NOT EXISTS` is MariaDB-only, so existence is checked against
    information_schema first rather than swallowing the duplicate-column error
    -- which would also swallow a genuine failure.
    """
    # NexGen: every table is created by the migrations (python -m nexgen migrate);
    # tta_trips and the other feed tables are read-only views in this schema.
    return {"status": "ok", "note": "tables are created by NexGen migrations"}
    own = conn is None
    conn = conn or get_connection()
    created = {"tables": [], "columns": []}
    try:
        with conn.cursor() as cur:
            for name, ddl in _TABLES.items():
                cur.execute(ddl)
                created["tables"].append(name)

            cur.execute(
                """SELECT COLUMN_NAME FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tta_trips'"""
            )
            have = {r["COLUMN_NAME"] for r in cur.fetchall()}
            for col, ddl in _COLUMNS.items():
                if col not in have:
                    cur.execute(ddl)
                    created["columns"].append(col)

            cur.execute(
                """SELECT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tta_trips'
                     AND INDEX_NAME = 'idx_trips_geofence_out'"""
            )
            if not cur.fetchone():
                cur.execute(
                    "ALTER TABLE tta_trips ADD KEY idx_trips_geofence_out (dt_geofence_out)"
                )
        conn.commit()
        logger.info("geofence schema ensured: %s", created)
        return created
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _sanity_check(key: str, role: str, lat: float, lon: float) -> tuple[float, float, str, float | None]:
    """Validate an anchor against the offline gazetteer.

    Returns (lat, lon, source, error_km). `source` is 'anchor' when the anchor
    is corroborated or simply has no reference to check against, and
    'gazetteer' when the anchor was rejected and replaced.
    """
    ref_lat, ref_lon = tta_geo.resolve(key, None)
    if ref_lat is None:
        # No reference for this name. Nothing is known to be wrong, so keep the
        # anchor -- but say it was unverified rather than implying it passed.
        return lat, lon, "anchor_unverified", None

    err_km = haversine_m(lat, lon, ref_lat, ref_lon) / 1000.0
    if err_km <= ANCHOR_SANITY_KM:
        # Corroborated. Keep the anchor: it is plant-precise, the gazetteer is
        # only town-precise.
        return lat, lon, "anchor", err_km

    logger.warning(
        "geofence: anchor for %s (%s) is %.0f km from its gazetteer position "
        "-- using the gazetteer instead", key, role, err_km,
    )
    return ref_lat, ref_lon, "gazetteer", err_km


def _radius_for(key: str, role: str, spread_m: int | None) -> int:
    """Fence radius: an explicit override if one exists, else sized off the
    observed ping spread at that node."""
    override = RADIUS_OVERRIDES.get((key, role))
    if override is not None:
        return override
    if not spread_m:
        return DEFAULT_RADIUS_M
    # The spread is the width of the observed cluster; give it a little air so
    # a normally-parked truck is comfortably inside rather than on the edge.
    return max(MIN_RADIUS_M, min(MAX_RADIUS_M, int(spread_m * 1.5)))


def seed_from_anchors(conn=None) -> dict:
    """Populate `geofences` from `gps_facility_anchors`.

    Existing rows are refreshed unless flagged `b_manual`. Returns counts so a
    caller can report what actually changed.
    """
    own = conn is None
    conn = conn or get_connection()
    inserted = updated = skipped_manual = skipped_thin = corrected = 0
    skipped_placeholder = skipped_unsupported = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s_node_name, s_role, d_lat, d_long, i_trips, i_spread_m
                   FROM gps_facility_anchors"""
            )
            anchors = cur.fetchall()

            cur.execute("SELECT s_key, s_role, b_manual FROM geofences")
            existing = {(r["s_key"], r["s_role"]): r["b_manual"] for r in cur.fetchall()}

            # Destinations with at least one trip that actually completed there.
            # See SUPERSEDED_CLOSE_PREFIX above for why a superseded trip's
            # pings cannot locate a destination.
            cur.execute(
                """SELECT UPPER(TRIM(s_dest_node_name)) AS k
                     FROM tta_trips
                    GROUP BY k
                   HAVING SUM(s_close_reason NOT LIKE %s OR s_close_reason IS NULL) > 0""",
                (SUPERSEDED_CLOSE_PREFIX + "%",),
            )
            completed_dests = {r["k"] for r in cur.fetchall()}

            for a in anchors:
                key = normalise_key(a["s_node_name"])
                role = a["s_role"]
                if not key or key in UNRESOLVABLE_KEYS:
                    skipped_placeholder += 1
                    continue
                if (a["i_trips"] or 0) < MIN_ANCHOR_TRIPS:
                    skipped_thin += 1
                    continue
                if existing.get((key, role)):
                    # Hand-corrected or client-supplied: never overwrite.
                    skipped_manual += 1
                    continue
                if role == "destination" and key not in completed_dests:
                    skipped_unsupported += 1
                    continue

                lat, lon, source, err_km = _sanity_check(
                    key, role, float(a["d_lat"]), float(a["d_long"])
                )
                if source == "gazetteer":
                    corrected += 1
                    # The anchor's spread describes a cluster we just rejected,
                    # so it cannot size this fence. A town centroid gets a
                    # town-sized radius.
                    radius = GAZETTEER_RADIUS_M
                else:
                    radius = _radius_for(key, role, a["i_spread_m"])

                if (key, role) in existing:
                    cur.execute(
                        """UPDATE geofences
                              SET s_name=%s, d_lat=%s, d_long=%s, i_radius_m=%s,
                                  i_trips_seen=%s, i_spread_m=%s, s_source=%s
                            WHERE s_key=%s AND s_role=%s""",
                        (a["s_node_name"], lat, lon, radius,
                         a["i_trips"], a["i_spread_m"], source, key, role),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO geofences
                             (s_key, s_name, s_role, d_lat, d_long, i_radius_m,
                              i_trips_seen, i_spread_m, s_source)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (key, a["s_node_name"], role, lat, lon,
                         radius, a["i_trips"], a["i_spread_m"], source),
                    )
                    inserted += 1
        conn.commit()
        result = {
            "inserted": inserted,
            "updated": updated,
            "skipped_manual": skipped_manual,
            "skipped_thin_support": skipped_thin,
            "corrected_from_gazetteer": corrected,
            "skipped_placeholder_name": skipped_placeholder,
            "skipped_no_completed_trip": skipped_unsupported,
        }
        logger.info("geofence seed from anchors: %s", result)
        return result
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_fences(conn=None, active_only: bool = True) -> list[Geofence]:
    own = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT i_fence_id, s_key, s_name, s_role, d_lat, d_long,
                          i_radius_m, s_source
                     FROM geofences
                    WHERE (%s = 0 OR b_active = 1)""",
                (1 if active_only else 0,),
            )
            return [
                Geofence(
                    fence_id=r["i_fence_id"],
                    key=r["s_key"],
                    name=r["s_name"],
                    role=r["s_role"],
                    lat=float(r["d_lat"]),
                    lon=float(r["d_long"]),
                    radius_m=float(r["i_radius_m"]),
                    source=r["s_source"],
                )
                for r in cur.fetchall()
            ]
    finally:
        if own:
            conn.close()


def build_index(conn=None, active_only: bool = True) -> GeofenceIndex:
    """Load every active fence into a query-ready spatial index."""
    return GeofenceIndex(load_fences(conn, active_only))


def seed_from_plants(conn=None) -> dict:
    """Overwrite fences for known Tata plants with published coordinates.

    Runs *after* `seed_from_anchors` and deliberately overrules it. The anchor
    derivation cannot survive a trip being closed at a plant it was not booked
    against -- the medoid then sits wherever the trucks ended up -- and no
    amount of looking at the trip data can detect that, because the trip data is
    the thing that is wrong. A published plant coordinate is the fixed reference
    the data gets measured against.

    Client-supplied fences (`s_source='client'`) are never touched: if the
    consignor has handed over the real geometry for a node, that beats a public
    plant coordinate for it.

    Rows are written `b_manual=1`, so `seed_from_anchors` will not undo them on
    the next reseed.
    """
    own = conn is None
    conn = conn or get_connection()
    inserted = updated = skipped_client = 0
    moved: list[dict] = []
    try:
        with conn.cursor() as cur:
            for key, role, plant in plants.plant_nodes():
                cur.execute(
                    "SELECT i_fence_id, d_lat, d_long, i_radius_m, s_source "
                    "FROM geofences WHERE s_key=%s AND s_role=%s",
                    (key, role),
                )
                existing = cur.fetchone()
                if existing and existing["s_source"] == "client":
                    skipped_client += 1
                    continue

                if existing:
                    shift_km = round(
                        haversine_m(plant.lat, plant.lon,
                                    float(existing["d_lat"]), float(existing["d_long"])) / 1000.0,
                        2,
                    )
                    moved.append({
                        "key": key, "role": role, "plant": plant.name,
                        "moved_km": shift_km,
                        "from_source": existing["s_source"],
                        "radius_from_m": int(existing["i_radius_m"]),
                        "radius_to_m": plant.radius_m,
                    })
                    cur.execute(
                        """UPDATE geofences
                              SET s_name=%s, d_lat=%s, d_long=%s, i_radius_m=%s,
                                  b_manual=1, b_active=1, s_source='plant'
                            WHERE s_key=%s AND s_role=%s""",
                        (plant.name, plant.lat, plant.lon, plant.radius_m, key, role),
                    )
                    updated += 1
                else:
                    cur.execute(
                        """INSERT INTO geofences
                             (s_key, s_name, s_role, d_lat, d_long, i_radius_m,
                              b_manual, b_active, s_source)
                           VALUES (%s,%s,%s,%s,%s,%s,1,1,'plant')""",
                        (key, plant.name, role, plant.lat, plant.lon, plant.radius_m),
                    )
                    inserted += 1
        conn.commit()
        result = {
            "inserted": inserted,
            "updated": updated,
            "skipped_client_supplied": skipped_client,
            "radius_m": plants.PLANT_RADIUS_M,
            "moved": sorted(moved, key=lambda m: -m["moved_km"]),
        }
        logger.info("geofence seed from plant gazetteer: %s", result)
        return result
    finally:
        if own:
            conn.close()
