"""
In-Plant / Pre-Dispatch Delay Analyzer
--------------------------------------
Reconstructs, purely from the GPS ping trail, *where and why* a truck
lost time in and around the origin plant before it got on the line-haul.

Why this exists (and why the declared metrics are not enough):
  The provider stamps a `dt_trip_start` (gate-out) and an `i_plant_vivo_min`
  ("vehicle-in/vehicle-out") figure. In the real data these hide the story.
  Trip 28844885 reads 37 h of "plant vivo", yet the GPS shows the truck
  cleared SLAG GATE in ~5 min and then sat *outside* the plant at
  DARCL YARD-PARDIH (a transporter yard ~10 km out) stopped for 11 h.
  The ping trail is the only source that tells you the delay was a yard
  wait, not loading — so we drive the whole analysis off GPS, not stamps.

Approach:
  1. Anchor the origin from the first few minutes of pings.
  2. Find the moment the truck leaves the origin region for good
     (last ping within `radius_km` of the anchor) — this is the true
     end of the pre-dispatch window, independent of the dispatch stamp.
  3. Consolidate the origin-window pings into station visits, rank by
     stopped time, and classify each (gate queue / weighbridge / yard
     wait / loading / plant floor).
  4. Name the bottleneck, attribute the delay reason, and quantify the
     excess against a turnaround benchmark.

The output is a plain dict, ready to be JSON-serialised by the API and
handed to the LLM insight layer (llm_insights.py).
"""

import logging
from math import radians, sin, cos, asin, sqrt

from nexgen.shared.analysis.tta_analysis import _load_bundle, _fmt, _gps_stats
from nexgen.shared.circlefence import plants

logger = logging.getLogger(__name__)

# --- Tunables ---
ORIGIN_ANCHOR_MIN = 10.0     # minutes of pings used to fix the FALLBACK anchor
# The origin region is the plant geofence: 10 km around the published plant
# coordinate. It is no longer a 25 km ring around wherever the truck happened to
# start, because that ring moved whenever a trip was closed at the wrong plant.
DEFAULT_RADIUS_KM = plants.PLANT_RADIUS_M / 1000.0
# Within this of the plant coordinate == physically on the works belt.
#
# 3 km was right when the anchor was the truck's OWN first pings -- the anchor
# sat on the loading point, so anything 3 km away really was elsewhere. Measured
# against the published plant coordinate the belt units are 0.87-6.41 km out
# (JCAPCPL 0.87, JSR WORKS 0.88, CRM BARA 3.88, TRYL-BARA 4.22, TBL-BARA 4.92,
# IBMD 5.46, ADITYAPUR 6.41), so 3 km would file the whole belt under "yards"
# and report 0% inside plant on a truck that never left the works.
#
# 7 km covers the belt and still leaves a 7-10 km band inside the fence for the
# yards and parking that sit just outside it. Note this only drives the
# distance-based in-plant/yard split: a station whose NAME says yard or gate is
# classified by that name first, whatever its distance.
IN_PLANT_KM = 7.0
STATION_MIN_DWELL = 3.0      # ignore drive-through waypoints below this (minutes)
DEFAULT_BENCHMARK_MIN = 240.0  # expected in-plant turnaround (4 h) for excess calc


def _haversine_km(lat1, lng1, lat2, lng2):
    lat1, lng1, lat2, lng2 = map(radians, (lat1, lng1, lat2, lng2))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371.0088


def _classify_station(waypoint: str | None, dist_km: float) -> tuple[str, str]:
    """Return (category, basis) from the waypoint name + distance."""
    w = (waypoint or "").upper()

    def has(*keys):
        return any(k in w for k in keys)

    if has("WEIGH", "WB", "KANTA", "DHARM"):
        return "Weighbridge", "weighbridge keyword"
    if has("PARKING", "YARD", "DARCL", "TRANSPORT NAGAR", "TPT NAGAR", "TRUCK"):
        return "Yard / parking wait", "parking/yard keyword"
    if has("GATE", "OUT POINT", "OUT PONT", "CHECK POST", "BARRIER"):
        return "Gate queue", "gate keyword"
    if has("DOCK", "BAY", "LOAD", "SHED", "WHARF", "SIDING", "WORKS", "PLANT"):
        return "Loading / plant floor", "loading keyword"
    if dist_km <= IN_PLANT_KM:
        return "Inside plant", f"within {IN_PLANT_KM:g} km of origin anchor"
    return "Standing / halt", "no station keyword"


def _origin_anchor(pings):
    """Median lat/lng over the first ORIGIN_ANCHOR_MIN of pings."""
    t0 = pings[0]["ts"]
    early = [p for p in pings
             if (p["ts"] - t0).total_seconds() / 60.0 <= ORIGIN_ANCHOR_MIN] or [pings[0]]
    lats = sorted(p["lat"] for p in early)
    lngs = sorted(p["lng"] for p in early)
    name_pool = [p["wpnt"] for p in early if p["wpnt"]]
    name = max(set(name_pool), key=name_pool.count) if name_pool else None
    return lats[len(lats) // 2], lngs[len(lngs) // 2], name


def _resolve_origin(conn, origin_name, pings):
    """Where the origin plant actually is, preferring published coordinates.

    Three sources, most trustworthy first:

      1. The plant gazetteer -- a coordinate published for that Tata plant. It
         does not move, which is the entire point: a trip closed at a plant it
         was not booked against cannot drag it anywhere.
      2. The `geofences` row for that node, which may have been supplied by the
         consignor or corrected by hand.
      3. The medoid of the trip's own first few minutes of pings -- the old
         behaviour, kept only as a last resort for nodes nobody has coordinates
         for. It is marked as such in the output so a reader can discount it.

    Returns (lat, lng, name, radius_km, source).
    """
    plant = plants.resolve_plant(origin_name, role="origin")
    if plant is not None:
        return (plant.lat, plant.lon, plant.name,
                plant.radius_m / 1000.0, "plant_gazetteer")

    if conn is not None and origin_name:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s_name, d_lat, d_long, i_radius_m, s_source
                         FROM geofences
                        WHERE s_key = UPPER(TRIM(%s)) AND s_role = 'origin'
                          AND b_active = 1""",
                    (origin_name,),
                )
                row = cur.fetchone()
            if row:
                return (float(row["d_lat"]), float(row["d_long"]), row["s_name"],
                        int(row["i_radius_m"]) / 1000.0, f"geofence:{row['s_source']}")
        except Exception:
            logger.exception("origin fence lookup failed for %r", origin_name)

    lat, lng, name = _origin_anchor(pings)
    return lat, lng, name, DEFAULT_RADIUS_KM, "gps_anchor_fallback"


def _minutes(a, b):
    """Whole minutes from a to b, or None if either end is missing."""
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 60.0)


def _journey_phases(trip, fence_name):
    """The origin journey split into the windows the business defined.

        dt_booking ───────► dt_trip_start ───────► dt_geofence_out
         plant entry          gate-out stamp       cleared the plant fence

    (A) Plant / Works detention -- what the TMS declares.
    (B) Origin geofence         -- the stretch after the gate-out stamp during
                                   which the truck was still inside the fence.
    (A+B) True hold at origin.

    "Transporter Park IN - Transporter Park OUT" is deliberately absent: the
    business excluded it as unusable, so it is neither summed nor shown. It is
    named in `excluded` so a reader can see it was a decision, not an oversight.

    Every value is NULL-safe. A trip whose tracker died has no dt_geofence_out,
    and (B) must read "not measurable" rather than zero -- a zero would quietly
    pull every average down and understate exactly the thing being measured.
    """
    booking = trip.get("dt_booking")
    start = trip.get("dt_trip_start")
    fence_out = trip.get("dt_geofence_out")

    works = _minutes(booking, start)
    tail = _minutes(start, fence_out)
    total = _minutes(booking, fence_out)

    understated = None
    if total and total > 0 and works is not None:
        understated = round((total - works) / total * 100, 1)

    return {
        "works_detention": {
            "label": "Plant / Works detention",
            "definition": "Plant entry (dt_booking) to gate-out (dt_trip_start)",
            "from_field": "dt_booking", "to_field": "dt_trip_start",
            "from_ts": _fmt(booking), "to_ts": _fmt(start),
            "minutes": works,
            "note": "The figure the TMS declares.",
        },
        "geofence_exit": {
            "label": f"Origin geofence ({fence_name})",
            "definition": "Gate-out (dt_trip_start) to geofence out (dt_geofence_out)",
            "from_field": "dt_trip_start", "to_field": "dt_geofence_out",
            "from_ts": _fmt(start), "to_ts": _fmt(fence_out),
            "minutes": tail,
            "status": trip.get("s_geofence_out_status"),
            "uncertainty_min": trip.get("i_geofence_out_gap_min"),
            "note": (
                "Still inside the plant geofence after the gate-out stamp."
                if tail is not None else
                "No confirmed geofence exit for this trip, so this window "
                "cannot be measured. It is not zero."
            ),
        },
        "origin_total": {
            "label": "True hold at origin",
            "definition": "Plant entry (dt_booking) to geofence out (dt_geofence_out)",
            "from_field": "dt_booking", "to_field": "dt_geofence_out",
            "from_ts": _fmt(booking), "to_ts": _fmt(fence_out),
            "minutes": total,
            "understated_by_pct": understated,
            "note": "Works detention plus the time still inside the fence.",
        },
        "excluded": {
            "transporter_park": (
                "Transporter Park IN - Transporter Park OUT is excluded by the "
                "business as unusable. It is not counted in any figure above."
            ),
        },
    }


def _departure_index(pings, anchor_lat, anchor_lng, radius_km):
    """Index of the last ping still within `radius_km` of the anchor.

    Once a truck departs on the line-haul it does not return to the origin
    metro, so the last in-region ping marks the true end of the origin
    window — regardless of what the dispatch stamp says. Returns
    (index, left_origin). If the truck never leaves (e.g. local trip whose
    destination is the origin), the window is the whole trip.
    """
    last_in = 0
    ever_left = False
    for i, p in enumerate(pings):
        d = _haversine_km(anchor_lat, anchor_lng, p["lat"], p["lng"])
        p["_dist_km"] = d
        if d <= radius_km:
            last_in = i
        else:
            ever_left = True
    return last_in, ever_left


def _build_stations(origin_pings):
    """Consolidate consecutive same-waypoint pings into visit rows."""
    visits, cur = [], None
    for p in origin_pings:
        w = p["wpnt"] or "—"
        if cur is None or cur["waypoint"] != w:
            if cur:
                visits.append(cur)
            cur = {"waypoint": w, "arrive": p["ts"], "depart": p["ts"], "pings": 0,
                   "moving_min": 0.0, "stopped_min": 0.0, "dist_sum": 0.0}
        cur["depart"] = p["ts"]
        cur["pings"] += 1
        cur["dist_sum"] += p.get("_dist_km", 0.0)
        cur["moving_min" if p["moving"] else "stopped_min"] += p["gap_min"]
    if cur:
        visits.append(cur)

    stations = []
    for v in visits:
        dwell = (v["depart"] - v["arrive"]).total_seconds() / 60.0
        dist_km = round(v["dist_sum"] / v["pings"], 2) if v["pings"] else 0.0
        category, basis = _classify_station(v["waypoint"], dist_km)
        stations.append({
            "waypoint": v["waypoint"],
            "category": category,
            "category_basis": basis,
            "dist_from_origin_km": dist_km,
            "arrive": _fmt(v["arrive"]),
            "depart": _fmt(v["depart"]),
            "dwell_min": round(dwell, 0),
            "stopped_min": round(v["stopped_min"], 0),
            "moving_min": round(v["moving_min"], 0),
            "pings": v["pings"],
        })
    return stations


def build_plant_delay(conn, trip_no: int,
                      radius_km: float | None = None,
                      benchmark_min: float = DEFAULT_BENCHMARK_MIN) -> dict | None:
    """Full in-plant / pre-dispatch delay bundle for one trip. None if the
    trip does not exist; a `has_gps=False` bundle if it has no pings."""
    trip, metrics, pings = _load_bundle(conn, trip_no)
    if trip is None:
        return None

    route = f"{trip.get('s_org_node_name')} → {trip.get('s_dest_node_name')}"
    declared = {
        "plant_vivo_label": metrics.get("s_plant_vivo"),
        "plant_vivo_min": metrics.get("i_plant_vivo_min"),
        "detention_min": metrics.get("i_detention_min"),
        "dt_trip_start": _fmt(trip.get("dt_trip_start")),
        "dt_booking": _fmt(trip.get("dt_booking")),
        "dt_geofence_out": _fmt(trip.get("dt_geofence_out")),
        # Shown for comparison only. The business excluded the provider's
        # park-in/park-out window as unusable, so it never enters a figure.
        "note": "plant_vivo is the provider's own number, reported for contrast.",
    }
    base = {
        "trip_no": trip_no,
        "route": route,
        "origin": trip.get("s_org_node_name"),
        "vehicle": trip.get("s_asset_id"),
        "driver": trip.get("s_driver_name"),
        "transporter": trip.get("s_trans_name"),
        "declared": declared,
    }

    fence_plant = plants.resolve_plant(trip.get("s_org_node_name"), role="origin")
    fence_label = fence_plant.name if fence_plant else (trip.get("s_org_node_name") or "origin")

    if not pings:
        # Stamps survive a dead tracker, so the declared window is still
        # reportable even with no trail to reconstruct.
        return {**base, "has_gps": False,
                "phases": _journey_phases(trip, fence_label),
                "note": "No GPS pings stored for this trip."}

    anchor_lat, anchor_lng, anchor_name, fence_radius_km, anchor_source = _resolve_origin(
        conn, trip.get("s_org_node_name"), pings)
    radius_km = radius_km if radius_km is not None else fence_radius_km
    dep_idx, left_origin = _departure_index(pings, anchor_lat, anchor_lng, radius_km)
    origin_pings = pings[:dep_idx + 1]

    stats = _gps_stats(origin_pings)
    stations = _build_stations(origin_pings)

    window_from = origin_pings[0]["ts"]
    window_to = origin_pings[-1]["ts"]
    window_min = round((window_to - window_from).total_seconds() / 60.0, 0)

    # Ping-covered movement accounting (gap-attributed, capped at 15 min/ping).
    moving_min = round(stats["moving_min"], 0)
    stopped_min = round(stats["stopped_min"], 0)
    # Wall-clock the device did NOT report through — during a long park the
    # tracker goes quiet, so this silent time is almost entirely standstill.
    silent_min = round(max(window_min - moving_min - stopped_min, 0), 0)
    # Best estimate of time NOT spent driving = the real "lost" time at origin.
    idle_min = round(window_min - moving_min, 0)

    # Split idle time by geography: physically in the plant vs. origin yards.
    # Use wall-clock dwell (time present), which survives device-silent gaps.
    in_plant_dwell = sum(s["dwell_min"] for s in stations
                         if s["dist_from_origin_km"] <= IN_PLANT_KM)
    yard_dwell = sum(s["dwell_min"] for s in stations
                     if s["dist_from_origin_km"] > IN_PLANT_KM)

    # Rank the stations that actually cost time — by wall-clock dwell, since
    # a long park with sparse pings has small stopped_min but large dwell.
    significant = [s for s in stations
                   if s["waypoint"] != "—" and s["dwell_min"] >= STATION_MIN_DWELL]
    significant.sort(key=lambda s: -s["dwell_min"])

    bottleneck = significant[0] if significant else None
    excess = round(idle_min - benchmark_min, 0)

    # Attribute the delay reason.
    if bottleneck:
        share = round(100 * bottleneck["dwell_min"] / idle_min, 0) if idle_min else 0
        delay_reason = {
            "primary": bottleneck["category"],
            "location": bottleneck["waypoint"],
            "dist_from_origin_km": bottleneck["dist_from_origin_km"],
            "dwell_min": bottleneck["dwell_min"],
            "confirmed_stopped_min": bottleneck["stopped_min"],
            "share_of_idle_pct": share,
            "evidence": (
                f"Truck was present {bottleneck['dwell_min']:.0f} min "
                f"({share:.0f}% of all non-driving time at origin) at "
                f"'{bottleneck['waypoint']}', {bottleneck['dist_from_origin_km']:.1f} km "
                f"from the plant anchor — classified '{bottleneck['category']}'. "
                f"Of that, {bottleneck['stopped_min']:.0f} min is GPS-confirmed "
                f"stationary (the rest is device-silent, presumed parked)."
            ),
            "contributors": [
                {"waypoint": s["waypoint"], "category": s["category"],
                 "dwell_min": s["dwell_min"], "stopped_min": s["stopped_min"],
                 "dist_from_origin_km": s["dist_from_origin_km"]}
                for s in significant[:5]
            ],
        }
    else:
        delay_reason = {
            "primary": "No significant standstill",
            "evidence": "No station accumulated notable stopped time in the origin window.",
            "contributors": [],
        }

    return {
        **base,
        "has_gps": True,
        "params": {
            "radius_km": radius_km,
            "radius_source": anchor_source,
            "in_plant_km": IN_PLANT_KM,
            "benchmark_min": benchmark_min,
            "station_min_dwell_min": STATION_MIN_DWELL,
        },
        "phases": _journey_phases(trip, fence_label),
        "origin_anchor": {
            "lat": round(anchor_lat, 6), "lng": round(anchor_lng, 6),
            "name": anchor_name,
            "source": anchor_source,
            "radius_km": radius_km,
            "source_note": {
                "plant_gazetteer": "Published plant coordinate; does not move with the data.",
                "gps_anchor_fallback": "Derived from this trip's own first pings -- no published coordinate for this node.",
            }.get(anchor_source, "From the geofence table."),
        },
        "window": {
            "from_ts": _fmt(window_from),
            "to_ts": _fmt(window_to),
            "total_min": window_min,
            "left_origin_region": left_origin,
            "method": (
                "GPS: first ping → last ping within "
                f"{radius_km:g} km of the origin anchor"
            ),
        },
        "kpis": {
            "origin_window_min": window_min,
            "idle_min": idle_min,
            "moving_min": moving_min,
            "stopped_min": stopped_min,
            "device_silent_min": silent_min,
            "utilization_pct": stats["utilization_pct"],
            "in_plant_dwell_min": round(in_plant_dwell, 0),
            "yard_dwell_min": round(yard_dwell, 0),
            "stations_visited": len(stations),
            "benchmark_min": benchmark_min,
            "excess_over_benchmark_min": excess,
            "pings": stats["pings"],
        },
        "delay_reason": delay_reason,
        "stations": significant,
        "all_stations": stations,
    }
