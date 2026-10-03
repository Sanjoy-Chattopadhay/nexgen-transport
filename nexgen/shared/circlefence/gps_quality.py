"""GPS coverage and geofence-configuration reporting.

Three questions are answered here, each mapping to one thing the client asked
for.

1. `transporter_scorecard` / `region_scorecard`
   "Overall GPS performance, transporter level, region specific highlights...
   any placed GPS data not received. GPS on time at transporter level."

   Four distinct failure modes hide behind "GPS not working", and lumping
   them together makes the report unactionable because each has a different
   owner:

     * **silent**   -- trip exists, zero pings ever arrived. Device never
                       reported. Fleet/device owner's problem.
     * **died at origin** -- pings arrived, then stopped while the truck was
                       still inside the origin fence, on a trip whose
                       destination is hundreds of km away. The truck went; the
                       tracker did not. This is the one that looks healthy in
                       a ping-count report and is not.
     * **late**     -- first ping arrived well after the trip started, so the
                       loading and gate-out window is unobserved. "GPS on
                       time" is exactly this.
     * **gappy**    -- pings throughout but with long holes in the middle.

2. `route_end_gap`
   "GPS closing between route and [arrival] against at destination level."

   How far from the declared destination did the trail actually stop? A trip
   whose last ping is 400 km short did not have its arrival observed, whatever
   the close reason says. Reported per destination so a pattern in one lane
   stands out from scattered one-offs.

3. `ungeofenced_destinations`
   "Points where GPS data being captured, customer was not geo fenced, met
   expected km running ~90% but trip closed as 'no geo fenced delivery
   locations'."

   Trips where the GPS is healthy and the truck covered essentially the full
   expected distance -- so it plainly arrived -- but the delivery point has no
   geofence upstream, so the system could not close on arrival and fell back
   to a non-geofence reason (new trip found / GRN / next upload). The output
   is a work list: which delivery locations need a geofence created, ranked by
   how many trips are affected, with centre coordinates derived from the
   pings of trips that did arrive.

Expected distance
-----------------
The feed carries no planned km, so "expected" is the **median observed
distance on the same origin->destination lane**, across trips that reached the
destination fence. A route needs `MIN_LANE_TRIPS` such trips before it gets a
baseline; below that the median is not a baseline, it is an anecdote.
"""

from __future__ import annotations

import logging
import math

from nexgen.shared.legacy_db import get_connection
from .index import haversine_m
from .store import SUPERSEDED_CLOSE_PREFIX, build_index

logger = logging.getLogger(__name__)

# Minutes after dt_trip_start by which the first ping should have arrived.
ON_TIME_LAG_MIN = 30

# A hole this long in the middle of a trail is an outage, not a cadence dip.
GAP_OUTAGE_MIN = 120

# Trips needed on a lane before its median distance counts as a baseline.
MIN_LANE_TRIPS = 5

# Fraction of the lane baseline that counts as "ran the route".
DISTANCE_OK_FRACTION = 0.9

# A last ping within this of the destination fence counts as arrived even if
# no fence crossing was recorded.
NEAR_DEST_KM = 10.0


def _rate(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 1) if den else None


# 95% two-sided.
_Z = 1.959963984540054


def _wilson(num: int, den: int) -> tuple[float, float] | tuple[None, None]:
    """95% Wilson score interval for a proportion, as percentages.

    Why this is here rather than a raw percentage
    ---------------------------------------------
    The scorecard is sorted worst-first and acted on, and a raw rate ranks a
    carrier with 5 trips above one with 95. On this corpus that is not
    hypothetical: Ritco Logistics sits at the top of the table on **20% of 5
    trips**, ahead of Hind Transport at **32.6% of 95**. Ritco's true rate could
    be anywhere from about 1% to 62%; Hind's is pinned to a few points. Ranking
    them by the same number invites someone to open a conversation with the
    wrong carrier.

    Wilson rather than the normal approximation because n is small and p is
    near the boundary exactly where it matters -- the textbook +/- 1.96*sqrt(pq/n)
    produces intervals that run below 0% on these counts.
    """
    if not den:
        return None, None
    p = num / den
    denom = 1 + _Z ** 2 / den
    centre = (p + _Z ** 2 / (2 * den)) / denom
    half = (_Z / denom) * math.sqrt(p * (1 - p) / den + _Z ** 2 / (4 * den ** 2))
    return round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1)


# Public alias. The same "is this rate comparable across wildly different trip
# counts?" problem shows up on the transporter league table, and it should be
# answered by the same interval rather than a second implementation that could
# drift from this one.
wilson_interval = _wilson


# ---------------------------------------------------------------------------
# 1. Coverage scorecards
# ---------------------------------------------------------------------------

# One aggregation pass over the GPS table, then index joins -- deliberately not
# three correlated subqueries per trip row, which is the obvious way to write
# this and measured 42 s on 2.4k trips against 3.5M pings. The GROUP BY at the
# end collapses the rare case of two pings sharing a trip's last timestamp,
# which would otherwise duplicate the trip row and double-count it.
_COVERAGE_SQL = """
    SELECT t.i_trip_no,
           t.s_trans_name,
           t.s_trip_class,
           t.i_gps_ping_count,
           t.s_geofence_out_status,
           t.dt_trip_start,
           m.d_uptime_pct,
           agg.first_ping,
           agg.last_ping,
           MAX(lastp.s_wpnt1_st_abbr) AS end_state
      FROM tta_trips t
      LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
      LEFT JOIN (SELECT i_trip_no,
                        MIN(dt_message) AS first_ping,
                        MAX(dt_message) AS last_ping
                   FROM tta_trip_gps
                  GROUP BY i_trip_no) agg ON agg.i_trip_no = t.i_trip_no
      LEFT JOIN tta_trip_gps lastp
             ON lastp.i_trip_no = t.i_trip_no
            AND lastp.dt_message = agg.last_ping
     WHERE (%s IS NULL OR t.s_trip_class = %s)
     GROUP BY t.i_trip_no, t.s_trans_name, t.s_trip_class, t.i_gps_ping_count,
              t.s_geofence_out_status, t.dt_trip_start, m.d_uptime_pct,
              agg.first_ping, agg.last_ping
"""


def _coverage_rows(conn, trip_class: str | None):
    with conn.cursor() as cur:
        cur.execute(_COVERAGE_SQL, (trip_class, trip_class))
        return cur.fetchall()


def _classify(row) -> str:
    """One trip -> one GPS-health verdict."""
    if not row["i_gps_ping_count"]:
        return "silent"
    if row["s_geofence_out_status"] == "gps_died_at_origin":
        return "died_at_origin"
    if row["dt_trip_start"] and row["first_ping"]:
        lag = (row["first_ping"] - row["dt_trip_start"]).total_seconds() / 60.0
        if lag > ON_TIME_LAG_MIN:
            return "late_start"
    up = row["d_uptime_pct"]
    if up is not None and float(up) < 80.0:
        return "gappy"
    return "ok"


def _score_group(rows: list[dict]) -> dict:
    verdicts = [_classify(r) for r in rows]
    n = len(rows)
    counts = {v: verdicts.count(v) for v in
              ("ok", "silent", "died_at_origin", "late_start", "gappy")}
    lags = [
        (r["first_ping"] - r["dt_trip_start"]).total_seconds() / 60.0
        for r in rows if r["dt_trip_start"] and r["first_ping"]
    ]
    on_time = sum(1 for l in lags if l <= ON_TIME_LAG_MIN)
    ok_low, ok_high = _wilson(counts["ok"], n)
    return {
        "trips": n,
        "gps_ok_pct": _rate(counts["ok"], n),
        # The interval, and the lower bound the table should actually rank on.
        # See _wilson: a 5-trip carrier and a 95-trip carrier are not comparable
        # on the point estimate alone.
        "gps_ok_ci_low": ok_low,
        "gps_ok_ci_high": ok_high,
        "gps_ok_ci_width": (None if ok_low is None else round(ok_high - ok_low, 1)),
        "silent_trips": counts["silent"],
        "silent_pct": _rate(counts["silent"], n),
        "died_at_origin": counts["died_at_origin"],
        "died_at_origin_pct": _rate(counts["died_at_origin"], n),
        "late_start": counts["late_start"],
        "gappy": counts["gappy"],
        "gps_on_time_pct": _rate(on_time, len(lags)),
        # None on an empty group, like every other figure here. `_scorecard`
        # never passes an empty one (it filters on min_trips first), but
        # `fleet_summary` scores whatever the query returned — which on a
        # freshly installed database is nothing, and indexing [0] into an empty
        # list 500'd the GPS summary before a single trip had been loaded.
        "median_ping_count": (
            sorted(r["i_gps_ping_count"] or 0 for r in rows)[n // 2] if n else None),
    }


def _scorecard(dimension_field: str, label: str, trip_class: str | None,
               min_trips: int, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or get_connection()
    try:
        rows = _coverage_rows(conn, trip_class)
        groups: dict[str, list[dict]] = {}
        for r in rows:
            groups.setdefault(r[dimension_field] or "(unknown)", []).append(r)
        out = [
            {label: name, **_score_group(rs)}
            for name, rs in groups.items() if len(rs) >= min_trips
        ]
        # Worst first: this is a report you act on, not one you browse.
        out.sort(key=lambda d: (d["gps_ok_pct"] if d["gps_ok_pct"] is not None else 100))
        return out
    finally:
        if own:
            conn.close()


def transporter_scorecard(trip_class: str | None = "zonal", min_trips: int = 5, conn=None):
    return _scorecard("s_trans_name", "transporter", trip_class, min_trips, conn)


def region_scorecard(trip_class: str | None = "zonal", min_trips: int = 5, conn=None):
    """Region = the state the trail ended in, from the waypoint state code."""
    return _scorecard("end_state", "region", trip_class, min_trips, conn)


def fleet_summary(trip_class: str | None = "zonal", conn=None) -> dict:
    own = conn is None
    conn = conn or get_connection()
    try:
        return _score_group(_coverage_rows(conn, trip_class))
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 2. Where the trail actually ends
# ---------------------------------------------------------------------------

def route_end_gap(trip_class: str | None = "zonal", min_trips: int = 5, conn=None) -> list[dict]:
    """Per destination: how far short of it did the GPS trail stop?"""
    own = conn is None
    conn = conn or get_connection()
    try:
        idx = build_index(conn)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.i_trip_no, t.s_dest_node_name, t.s_close_reason,
                          g.d_lat, g.d_long
                     FROM tta_trips t
                     JOIN tta_trip_gps g ON g.i_trip_no = t.i_trip_no
                     JOIN (SELECT i_trip_no, MAX(dt_message) m
                             FROM tta_trip_gps GROUP BY i_trip_no) last
                       ON last.i_trip_no = g.i_trip_no AND last.m = g.dt_message
                    WHERE (%s IS NULL OR t.s_trip_class = %s)""",
                (trip_class, trip_class),
            )
            rows = cur.fetchall()

        groups: dict[str, list[float]] = {}
        sources: dict[str, str] = {}
        no_fence: dict[str, int] = {}
        for r in rows:
            dest = r["s_dest_node_name"] or "(unknown)"
            fence = idx.by_key(dest)
            if fence is None:
                no_fence[dest] = no_fence.get(dest, 0) + 1
                continue
            sources[dest] = fence.source
            km = haversine_m(float(r["d_lat"]), float(r["d_long"]),
                             fence.lat, fence.lon) / 1000.0
            groups.setdefault(dest, []).append(km)

        out = []
        for dest, kms in groups.items():
            if len(kms) < min_trips:
                continue
            s = sorted(kms)
            src = sources.get(dest, "anchor")
            arrived_n = sum(1 for k in kms if k <= NEAR_DEST_KM)
            a_low, a_high = _wilson(arrived_n, len(kms))
            out.append({
                "destination": dest,
                "trips": len(kms),
                "median_end_gap_km": round(s[len(s) // 2], 2),
                "p90_end_gap_km": round(s[min(len(s) - 1, int(len(s) * 0.9))], 2),
                "min_end_gap_km": round(s[0], 2),
                "arrived_trips": arrived_n,
                "arrived_pct": _rate(arrived_n, len(kms)),
                # Same reasoning as the transporter scorecard: a lane with six
                # trips and one with sixty are not comparable on a bare rate.
                "arrived_ci_low": a_low,
                "arrived_ci_high": a_high,
                "arrived_ci_width": (None if a_low is None else round(a_high - a_low, 1)),
                "stopped_over_50km_short": sum(1 for k in kms if k > 50),
                "no_destination_fence_trips": no_fence.get(dest, 0),
                "fence_source": src,
                # A gazetteer fence is a town centroid, not the customer's gate.
                # Tens of km against one means the delivery point is not
                # precisely known -- it is NOT evidence the truck fell short.
                # Only a hundreds-of-km gap is unambiguous on such a fence.
                "gap_is_conclusive": src != "gazetteer" or s[len(s) // 2] > 100,
            })
        out.sort(key=lambda d: d["median_end_gap_km"], reverse=True)
        return out
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 3. Delivery locations that need a geofence
# ---------------------------------------------------------------------------

def ungeofenced_destinations(trip_class: str | None = "zonal", conn=None) -> dict:
    """Destinations with no upstream geofence where trucks demonstrably arrive.

    A destination qualifies when, on that lane:
      * no upstream geofence id is recorded (`i_geo_id` null/0), and
      * trips ran at least `DISTANCE_OK_FRACTION` of the lane's median
        distance -- so the truck covered the route, and
      * the trip closed for a reason other than a geofence hit.

    The centre offered for each is the median of the last pings of the trips
    that got within `NEAR_DEST_KM`, which is the best available estimate of
    where the delivery point physically is.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        idx = build_index(conn)
        with conn.cursor() as cur:
            # Aggregate the ping table once, then join -- the same shape as
            # _COVERAGE_SQL and for the same reason. Written the obvious way,
            # with tta_trip_gps LEFT JOINed to trips and the "is this the last
            # ping" test in the WHERE clause, all 3.5M ping rows are joined
            # before anything is filtered and this measured 37s, past the
            # frontend's 30s timeout. The rewrite runs in about a second.
            cur.execute(
                """SELECT t.i_trip_no, t.s_dest_node_name, t.s_close_reason,
                          t.i_gps_ping_count, m.i_geo_id, m.d_distance_travelled_km,
                          MAX(lastp.d_lat)  AS d_lat,
                          MAX(lastp.d_long) AS d_long
                     FROM tta_trips t
                     LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                     LEFT JOIN (SELECT i_trip_no, MAX(dt_message) AS last_ping
                                  FROM tta_trip_gps
                                 GROUP BY i_trip_no) agg ON agg.i_trip_no = t.i_trip_no
                     LEFT JOIN tta_trip_gps lastp
                            ON lastp.i_trip_no = t.i_trip_no
                           AND lastp.dt_message = agg.last_ping
                    WHERE (%s IS NULL OR t.s_trip_class = %s)
                    GROUP BY t.i_trip_no, t.s_dest_node_name, t.s_close_reason,
                             t.i_gps_ping_count, m.i_geo_id, m.d_distance_travelled_km""",
                (trip_class, trip_class),
            )
            rows = cur.fetchall()

        # Lane baselines: median observed distance per destination.
        by_dest: dict[str, list[dict]] = {}
        for r in rows:
            by_dest.setdefault(r["s_dest_node_name"] or "(unknown)", []).append(r)

        findings = []
        for dest, rs in by_dest.items():
            dists = sorted(float(r["d_distance_travelled_km"]) for r in rs
                           if r["d_distance_travelled_km"])
            if len(dists) < MIN_LANE_TRIPS:
                continue
            baseline = dists[len(dists) // 2]
            threshold = baseline * DISTANCE_OK_FRACTION

            geofenced = sum(1 for r in rs if (r["i_geo_id"] or 0) > 0)
            if geofenced:
                continue  # a fence exists upstream; not this report's problem

            ran_route = [
                r for r in rs
                if r["d_distance_travelled_km"]
                and float(r["d_distance_travelled_km"]) >= threshold
                and (r["i_gps_ping_count"] or 0) > 0
                and not (r["s_close_reason"] or "").upper().count("GEO")
            ]
            if not ran_route:
                continue

            fence = idx.by_key(dest)
            lats = sorted(float(r["d_lat"]) for r in ran_route if r["d_lat"] is not None)
            lons = sorted(float(r["d_long"]) for r in ran_route if r["d_long"] is not None)
            centre = (
                {"lat": round(lats[len(lats) // 2], 6), "lon": round(lons[len(lons) // 2], 6)}
                if lats and lons else None
            )

            findings.append({
                "destination": dest,
                "trips_on_lane": len(rs),
                "trips_ran_full_route_no_geofence_close": len(ran_route),
                "median_lane_distance_km": round(baseline, 1),
                "close_reasons": sorted({(r["s_close_reason"] or "?") for r in ran_route}),
                "suggested_fence_centre": centre,
                "derived_fence_already_available": fence is not None,
            })

        findings.sort(key=lambda d: d["trips_ran_full_route_no_geofence_close"], reverse=True)
        return {
            "trip_class": trip_class,
            "criteria": {
                "distance_threshold": f"{int(DISTANCE_OK_FRACTION * 100)}% of the lane median",
                "min_lane_trips": MIN_LANE_TRIPS,
            },
            "destinations_needing_a_geofence": len(findings),
            "trips_affected": sum(f["trips_ran_full_route_no_geofence_close"] for f in findings),
            "findings": findings,
        }
    finally:
        if own:
            conn.close()


def dimension_detail(dimension: str, value: str, trip_class: str | None = "zonal",
                     conn=None) -> dict:
    """Every trip behind one row of a scorecard, with the inputs that judged it.

    The scorecard says a carrier is at 32.6%. This says *which* trips, and for
    each one the four raw values the verdict was computed from -- ping count,
    the lag from trip start to first ping, the provider's uptime, and the
    geofence status. A reader can recompute any cell from this, or open the trip
    and look at the trail.

    That matters more than usual here because the verdicts are not independent
    measurements: `died_at_origin` comes from the geofence backfill, `late_start`
    from a 30-minute threshold, `gappy` from an uptime column that exists on only
    about half the corpus. Showing the inputs is what stops the headline
    percentage from being taken as a single, uniform fact.
    """
    field = {"transporter": "s_trans_name", "region": "end_state"}.get(dimension)
    if field is None:
        raise ValueError("dimension must be 'transporter' or 'region'")

    own = conn is None
    conn = conn or get_connection()
    try:
        rows = [r for r in _coverage_rows(conn, trip_class)
                if (r[field] or "(unknown)") == value]
        trips = []
        for r in rows:
            lag = None
            if r["dt_trip_start"] and r["first_ping"]:
                lag = round((r["first_ping"] - r["dt_trip_start"]).total_seconds() / 60.0, 1)
            trips.append({
                "trip_no": r["i_trip_no"],
                "verdict": _classify(r),
                "ping_count": r["i_gps_ping_count"],
                "first_ping_lag_min": lag,
                "uptime_pct": (None if r["d_uptime_pct"] is None
                               else round(float(r["d_uptime_pct"]), 1)),
                "geofence_status": r["s_geofence_out_status"],
                "trip_start": r["dt_trip_start"],
                "first_ping": r["first_ping"],
                "last_ping": r["last_ping"],
                "end_state": r["end_state"],
                "transporter": r["s_trans_name"],
            })
        # Worst first, then by how much evidence there is -- the same order the
        # scorecard uses, so the drill-down reads like the row it came from.
        rank = {"silent": 0, "died_at_origin": 1, "late_start": 2, "gappy": 3, "ok": 4}
        trips.sort(key=lambda t: (rank.get(t["verdict"], 9), -(t["ping_count"] or 0)))

        return {
            "dimension": dimension,
            "value": value,
            "trip_class": trip_class,
            **_score_group(rows),
            "trips_detail": trips,
            "field_notes": {
                "ping_count": "0 pings => silent. Nothing else can be judged.",
                "first_ping_lag_min": (
                    f"minutes from trip start to the first ping; over "
                    f"{ON_TIME_LAG_MIN:g} counts as a late start, which is what "
                    f"'GPS on time' measures"
                ),
                "uptime_pct": (
                    "the provider's own figure; below 80% counts as gappy. It is "
                    "NULL on roughly half the corpus, and a NULL is never counted "
                    "as gappy -- absence of evidence is not a failure"
                ),
                "geofence_status": (
                    "from the geofence backfill; 'gps_died_at_origin' means the "
                    "trail stopped inside the origin fence on a long-haul run"
                ),
            },
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 4. The consignor's complaint, judged for a single trip
# ---------------------------------------------------------------------------

def delivery_geofence_verdict(trip_no: int, conn=None) -> dict:
    """Did this trip run the route and then close without a geofence?

    The consignor's words: *"Points where GPS data being captured. Customer was
    not geo fenced, met expected km running ~90% but trip closed as 'No geo
    fenced delivery locations'."*

    That is four separate claims about one trip, and they are checked
    separately here so a reader can see which ones actually hold:

      1. GPS was captured -- pings exist, and the tracker did not die at the
         origin. Without this the rest is unknowable rather than proven.
      2. The distance was run -- at least `DISTANCE_OK_FRACTION` of the lane's
         median actual distance. There is no planned-km field anywhere in the
         feed, so the lane median is the only baseline available, and it is
         named as such rather than dressed up as a plan.
      3. The delivery point has no upstream geofence -- `i_geo_id` is null or
         zero on this trip.
      4. The trip closed for some reason other than a geofence hit.

    All four together is the complaint, and it means the delivery location is
    missing from eTrans's geofence master: a data gap on their side, not a
    transport failure. They are returned separately so a partial match can never
    be read as the whole thing.

    On the phrase itself: no row in this corpus literally reads "No geo fenced
    delivery locations". The data shows the same fact by absence -- of 1,697
    zonal trips, **zero** carry an upstream geofence id and **zero** closed with
    a geofence reason, while local trips close `DESTINATION REACHED BY GEO` 395
    times. The zonal lane has no delivery geofences at all.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.i_trip_no, t.s_dest_node_name, t.s_org_node_name,
                          t.s_close_reason, t.s_trip_class, t.i_gps_ping_count,
                          t.s_geofence_out_status, t.s_trans_name,
                          m.i_geo_id, m.d_distance_travelled_km
                     FROM tta_trips t
                     LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                    WHERE t.i_trip_no = %s""",
                (trip_no,),
            )
            trip = cur.fetchone()
            if not trip:
                raise LookupError(f"trip {trip_no} not found")

            dest = trip["s_dest_node_name"]
            # <=> is the NULL-safe equality: a destination can be NULL, and
            # `= NULL` would silently return an empty lane rather than matching.
            cur.execute(
                """SELECT m.d_distance_travelled_km AS km
                     FROM tta_trips t
                     JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                    WHERE t.s_dest_node_name <=> %s
                      AND t.s_trip_class <=> %s
                      AND m.d_distance_travelled_km IS NOT NULL""",
                (dest, trip["s_trip_class"]),
            )
            lane = sorted(float(r["km"]) for r in cur.fetchall())

            # Does ANY trip on this lane carry an upstream geofence id? If none
            # ever does, the gap belongs to the location, not to this trip.
            cur.execute(
                """SELECT COUNT(*) AS n,
                          SUM(m.i_geo_id IS NOT NULL AND m.i_geo_id <> 0) AS fenced
                     FROM tta_trips t
                     LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                    WHERE t.s_dest_node_name <=> %s AND t.s_trip_class <=> %s""",
                (dest, trip["s_trip_class"]),
            )
            lane_fence = cur.fetchone() or {}

        idx = build_index(conn)
        our_fence = idx.by_key(dest or "", role="destination") or idx.by_key(dest or "")

        km = (None if trip["d_distance_travelled_km"] is None
              else float(trip["d_distance_travelled_km"]))
        baseline = lane[len(lane) // 2] if len(lane) >= MIN_LANE_TRIPS else None
        pct_of_lane = (None if not (km and baseline) else round(km / baseline * 100, 1))

        gps_captured = (bool(trip["i_gps_ping_count"])
                        and trip["s_geofence_out_status"] != "gps_died_at_origin")
        distance_met = bool(pct_of_lane is not None
                            and pct_of_lane >= DISTANCE_OK_FRACTION * 100)
        not_geofenced = not (trip["i_geo_id"] or 0)
        closed_without_geo = "GEO" not in (trip["s_close_reason"] or "").upper()

        if gps_captured:
            gps_evidence = f"{trip['i_gps_ping_count'] or 0} pings recorded"
        elif not trip["i_gps_ping_count"]:
            gps_evidence = "no pings at all -- nothing can be concluded from this trip"
        else:
            gps_evidence = (
                f"{trip['i_gps_ping_count']} pings, but the tracker died inside the "
                "origin fence, so this trip says nothing about the destination"
            )

        if km is None:
            dist_evidence = "no distance recorded for this trip"
        elif baseline is None:
            dist_evidence = (f"{km:,.0f} km run, but the lane has only {len(lane)} "
                             f"trips with a distance -- too few for a baseline")
        else:
            dist_evidence = (f"{km:,.0f} km run against a lane median of "
                             f"{baseline:,.0f} km ({pct_of_lane}%)")

        checks = [
            {"id": "gps_captured", "holds": gps_captured,
             "claim": "GPS data was being captured",
             "evidence": gps_evidence},
            {"id": "distance_met", "holds": distance_met,
             "claim": f"Ran at least {DISTANCE_OK_FRACTION:.0%} of the lane's usual distance",
             "evidence": dist_evidence},
            {"id": "not_geofenced", "holds": not_geofenced,
             "claim": "The delivery point has no geofence in eTrans",
             "evidence": (
                 f"no upstream geofence id on this trip; "
                 f"{int(lane_fence.get('fenced') or 0)} of "
                 f"{int(lane_fence.get('n') or 0)} trips to this destination have one"
                 if not_geofenced else
                 f"upstream geofence id {trip['i_geo_id']} is present")},
            {"id": "closed_without_geo", "holds": closed_without_geo,
             "claim": "The trip closed without a geofence hit",
             "evidence": (f'closed as "{trip["s_close_reason"]}"'
                          if trip["s_close_reason"] else "no close reason recorded")},
        ]

        matches = all(c["holds"] for c in checks)
        return {
            "trip_no": trip_no,
            "destination": dest,
            "trip_class": trip["s_trip_class"],
            "matches_complaint": matches,
            "checks": checks,
            "distance_km": km,
            "lane_median_km": (None if baseline is None else round(baseline, 1)),
            "lane_trips": len(lane),
            "pct_of_lane_median": pct_of_lane,
            "close_reason": trip["s_close_reason"],
            "upstream_geo_id": trip["i_geo_id"],
            "lane_trips_with_upstream_fence": int(lane_fence.get("fenced") or 0),
            "lane_trips_total": int(lane_fence.get("n") or 0),
            # Our own derived fence is not a substitute for theirs. It is what
            # this system measures against, and it is why the destination
            # reports work at all while eTrans still closes the trip blind.
            "our_fence": (None if our_fence is None else {
                "name": our_fence.name,
                "source": getattr(our_fence, "source", None),
                "radius_m": our_fence.radius_m,
                "lat": our_fence.lat, "lon": our_fence.lon,
            }),
            "baseline_note": (
                "There is no planned-km field in the feed. The baseline is the "
                "median distance actually run on this lane, which is why it is "
                "called the lane median and not the expected distance."
            ),
            "verdict": (
                "This trip is the consignor's complaint, exactly: the truck ran the "
                "route with working GPS, and eTrans still closed it without a "
                "geofence because the delivery point is not in their geofence master."
                if matches else
                "This trip does not match the complaint -- the failing checks below "
                "say why."
            ),
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 5. One destination, trip by trip
# ---------------------------------------------------------------------------

def destination_detail(destination: str, trip_class: str | None = "zonal",
                       conn=None) -> dict:
    """Every trip to one destination, with where its trail actually stopped.

    The lane summary says "median 33 km short, 6% arrived". This says which
    trips, how far each one ended from the fence, what it closed as, and whether
    it carried an upstream geofence id -- so the summary can be recomputed by
    hand, or contradicted.

    The `fence_source` matters more than the distance and is repeated for that
    reason. Against a `gazetteer` fence -- a town centroid picked because the
    GPS-derived position failed its sanity check -- a 30 km gap means the
    customer's gate is not precisely known, NOT that the truck fell short.
    `gap_is_conclusive` carries that distinction; never report a gap without it.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        idx = build_index(conn)
        fence = idx.by_key(destination, role="destination") or idx.by_key(destination)

        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.i_trip_no, t.s_close_reason, t.s_trans_name,
                          t.i_gps_ping_count, t.s_geofence_out_status,
                          m.i_geo_id, m.d_distance_travelled_km,
                          MAX(lastp.d_lat)  AS d_lat,
                          MAX(lastp.d_long) AS d_long,
                          MAX(agg.last_ping) AS last_ping
                     FROM tta_trips t
                     LEFT JOIN tta_trip_metrics m ON m.i_trip_no = t.i_trip_no
                     LEFT JOIN (SELECT i_trip_no, MAX(dt_message) AS last_ping
                                  FROM tta_trip_gps GROUP BY i_trip_no) agg
                            ON agg.i_trip_no = t.i_trip_no
                     LEFT JOIN tta_trip_gps lastp
                            ON lastp.i_trip_no = t.i_trip_no
                           AND lastp.dt_message = agg.last_ping
                    WHERE t.s_dest_node_name <=> %s
                      AND (%s IS NULL OR t.s_trip_class = %s)
                    GROUP BY t.i_trip_no, t.s_close_reason, t.s_trans_name,
                             t.i_gps_ping_count, t.s_geofence_out_status,
                             m.i_geo_id, m.d_distance_travelled_km""",
                (destination, trip_class, trip_class),
            )
            rows = cur.fetchall()

        dists = sorted(float(r["d_distance_travelled_km"]) for r in rows
                       if r["d_distance_travelled_km"] is not None)
        baseline = dists[len(dists) // 2] if len(dists) >= MIN_LANE_TRIPS else None

        trips, gaps = [], []
        for r in rows:
            gap_km = None
            if fence is not None and r["d_lat"] is not None:
                gap_km = round(haversine_m(float(r["d_lat"]), float(r["d_long"]),
                                           fence.lat, fence.lon) / 1000.0, 2)
                gaps.append(gap_km)
            km = (None if r["d_distance_travelled_km"] is None
                  else float(r["d_distance_travelled_km"]))
            trips.append({
                "trip_no": r["i_trip_no"],
                "transporter": r["s_trans_name"],
                "close_reason": r["s_close_reason"],
                "ping_count": r["i_gps_ping_count"],
                "geofence_status": r["s_geofence_out_status"],
                "end_gap_km": gap_km,
                "arrived": (None if gap_km is None else gap_km <= NEAR_DEST_KM),
                "distance_km": (None if km is None else round(km, 1)),
                "superseded": (
                    "closed as \"NEW TRIP FOUND...\" -- the record was replaced by a "
                    "later one, not delivered. Its last ping is wherever the truck was "
                    "when that happened, so its gap says nothing about the destination"
                ),
                "pct_of_lane_median": (None if not (km and baseline)
                                       else round(km / baseline * 100, 1)),
                "upstream_geo_id": r["i_geo_id"],
                "last_ping": r["last_ping"],
                # A trip closed "NEW TRIP FOUND..." was superseded by a later
                # record, not delivered. Its last ping is wherever the truck
                # happened to be when the record was replaced, so its gap is not
                # evidence of a shortfall -- and on this lane those are exactly
                # the trips that look worst.
                "superseded": (r["s_close_reason"] or "").upper().startswith(
                    SUPERSEDED_CLOSE_PREFIX),
            })

        # Furthest-short first: the order someone investigating actually wants.
        trips.sort(key=lambda t: (t["end_gap_km"] is None, -(t["end_gap_km"] or 0)))
        srt = sorted(gaps)
        median_gap = round(srt[len(srt) // 2], 2) if srt else None
        src = getattr(fence, "source", None)

        return {
            "destination": destination,
            "trip_class": trip_class,
            "trips": len(trips),
            "fence": (None if fence is None else {
                "name": fence.name, "source": src, "radius_m": fence.radius_m,
                "lat": fence.lat, "lon": fence.lon,
            }),
            "median_end_gap_km": median_gap,
            "arrived_trips": sum(1 for t in trips if t["arrived"]),
            "superseded_trips": sum(1 for t in trips if t["superseded"]),
            "completed_trips": sum(1 for t in trips if not t["superseded"]),
            "lane_median_km": (None if baseline is None else round(baseline, 1)),
            "trips_with_upstream_fence": sum(1 for t in trips
                                             if (t["upstream_geo_id"] or 0) > 0),
            "gap_is_conclusive": (src != "gazetteer" or (median_gap or 0) > 100),
            "trips_detail": trips,
            "field_notes": {
                "end_gap_km": (
                    "distance from the trip's LAST ping to the destination fence "
                    f"centre; within {NEAR_DEST_KM:g} km counts as arrived"
                ),
                "fence_source": (
                    f"this fence is '{src}'. A 'gazetteer' fence is a town centroid, "
                    "not the customer's gate -- against one of those a gap of tens "
                    "of km means the delivery point is not precisely known, NOT that "
                    "the truck fell short"
                    if src else "no fence exists for this destination"
                ),
                "upstream_geo_id": (
                    "eTrans's own geofence id. Null on every zonal trip in this "
                    "corpus, which is the consignor's complaint"
                ),
                "pct_of_lane_median": (
                    "distance run as a share of the median distance actually run on "
                    "this lane. There is no planned-km field anywhere in the feed"
                ),
            },
        }
    finally:
        if own:
            conn.close()
