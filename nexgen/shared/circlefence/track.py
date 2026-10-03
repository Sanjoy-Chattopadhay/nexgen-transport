"""One trip, drawn on a map: the trail, the fences, and where the exit landed.

Why this exists
---------------
Every other report in this module is an aggregate, and an aggregate is exactly
the wrong thing to show a client who does not yet believe the finding. "Median
hidden tail 7.9 h" invites the answer "your data is wrong". One truck, with its
own trail painted red inside the fence for the nine hours after its gate-out
stamp, does not.

So this returns everything needed to draw a single trip: the origin and
destination circles, the ping trail with each point tagged inside/outside the
origin fence, the exit stamp, and the crossing ledger. The UI colours the trail
by that tag; the red segment *is* the hidden detention.

Downsampling
------------
A long-haul trip carries up to ~3,000 pings and the map cannot use them all.
Thinning uniformly would be wrong here: the whole point of the picture is the
dense in-fence cluster, and a uniform stride throws away exactly that. So the
in-fence pings are kept whole (they are a small fraction of the trail and carry
the finding), and only the highway leg is strided. The response says what it
did in `sampling`, because a chart that has silently dropped points should say
so.
"""

from __future__ import annotations

import logging

from nexgen.shared.legacy_db import get_connection
from .index import haversine_m
from .store import build_index

logger = logging.getLogger(__name__)

# Above this many points the map stutters and nothing is gained -- the highway
# leg is a straight line sampled every few minutes either way.
MAX_ROUTE_POINTS = 1200


def _hours(a, b) -> float | None:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 3600.0, 2)


def trip_track(trip_no: int, conn=None) -> dict:
    """Trail, fences and stamps for one trip, ready to draw."""
    own = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT i_trip_no, s_org_node_name, s_dest_node_name,
                          s_trans_name, s_asset_id, s_close_reason, s_trip_class,
                          dt_booking, dt_trip_start, dt_trip_eta, dt_trip_ata,
                          dt_geofence_out, i_geofence_out_gap_min,
                          s_geofence_out_status, i_gps_ping_count
                     FROM tta_trips WHERE i_trip_no = %s""",
                (trip_no,),
            )
            trip = cur.fetchone()
            if not trip:
                raise LookupError(f"trip {trip_no} not found")

            cur.execute(
                """SELECT dt_message, d_lat, d_long, i_speed
                     FROM tta_trip_gps
                    WHERE i_trip_no = %s
                    ORDER BY dt_message""",
                (trip_no,),
            )
            pings = cur.fetchall()

            cur.execute(
                """SELECT s_fence_key, s_role, s_event, dt_event, i_gap_min,
                          d_lat, d_long
                     FROM tta_trip_geofence_events
                    WHERE i_trip_no = %s
                    ORDER BY dt_event""",
                (trip_no,),
            )
            events = cur.fetchall()

        idx = build_index(conn)
        origin = (idx.by_key(trip["s_org_node_name"], role="origin")
                  or idx.by_key(trip["s_org_node_name"]))
        dest = idx.by_key(trip["s_dest_node_name"], role="destination") \
            or idx.by_key(trip["s_dest_node_name"])

        def _fence(f):
            if f is None:
                return None
            return {
                "key": f.key, "name": f.name, "role": f.role,
                "lat": f.lat, "lon": f.lon, "radius_m": f.radius_m,
                "source": getattr(f, "source", None),
            }

        # -- tag every ping, then thin only the outside-fence leg -------------
        tagged = []
        for p in pings:
            lat, lon = float(p["d_lat"]), float(p["d_long"])
            inside = (origin is not None
                      and haversine_m(lat, lon, origin.lat, origin.lon) <= origin.radius_m)
            tagged.append({
                "t": p["dt_message"],
                "lat": lat,
                "lon": lon,
                "speed": p["i_speed"],
                "in_origin": inside,
            })

        inside_pts = [p for p in tagged if p["in_origin"]]
        outside_pts = [p for p in tagged if not p["in_origin"]]
        budget = max(MAX_ROUTE_POINTS - len(inside_pts), 100)
        stride = max(1, (len(outside_pts) // budget) + 1) if outside_pts else 1
        kept_outside = outside_pts[::stride]
        if outside_pts and outside_pts[-1] not in kept_outside:
            kept_outside.append(outside_pts[-1])

        route = sorted(inside_pts + kept_outside, key=lambda p: p["t"])

        declared = _hours(trip["dt_booking"], trip["dt_trip_start"])
        hidden = _hours(trip["dt_trip_start"], trip["dt_geofence_out"])
        total = _hours(trip["dt_booking"], trip["dt_geofence_out"])

        return {
            "trip": {
                "trip_no": trip["i_trip_no"],
                "origin": trip["s_org_node_name"],
                "destination": trip["s_dest_node_name"],
                "transporter": trip["s_trans_name"],
                "asset_id": trip["s_asset_id"],
                "close_reason": trip["s_close_reason"],
                "trip_class": trip["s_trip_class"],
                "dt_booking": trip["dt_booking"],
                "dt_trip_start": trip["dt_trip_start"],
                "dt_trip_eta": trip["dt_trip_eta"],
                "dt_trip_ata": trip["dt_trip_ata"],
                "dt_geofence_out": trip["dt_geofence_out"],
                "geofence_out_gap_min": trip["i_geofence_out_gap_min"],
                "geofence_out_status": trip["s_geofence_out_status"],
                "ping_count": trip["i_gps_ping_count"],
            },
            "detention": {
                "declared_h": declared,
                "hidden_tail_h": hidden,
                "true_total_h": total,
                "understated_by_pct": (
                    None if not total or not declared or total == 0
                    else round((total - declared) / total * 100, 1)
                ),
                "note": (
                    "declared = gate-out minus plant entry, the figure the TMS "
                    "reports. hidden = the truck was still inside the origin "
                    "geofence after that stamp. true total = both."
                ),
            },
            "fences": {"origin": _fence(origin), "destination": _fence(dest)},
            "route": route,
            "events": [
                {
                    "fence_key": e["s_fence_key"], "role": e["s_role"],
                    "event": e["s_event"], "t": e["dt_event"],
                    "gap_min": e["i_gap_min"],
                    "lat": float(e["d_lat"]), "lon": float(e["d_long"]),
                }
                for e in events
            ],
            "sampling": {
                "pings_total": len(tagged),
                "points_returned": len(route),
                "in_origin_kept_whole": len(inside_pts),
                "route_stride": stride,
                "note": (
                    "Pings inside the origin fence are never thinned -- they carry "
                    "the detention finding. Only the highway leg is strided."
                ),
            },
        }
    finally:
        if own:
            conn.close()


def demo_trips(limit: int = 12, conn=None) -> list[dict]:
    """Trips worth putting in front of a client, one per failure mode.

    A demo picked at random lands on an `intra_fence` trip and shows nothing.
    This picks deliberately: the longest confirmed hidden tails (the headline
    finding), then trips whose tracker died inside the works (the GPS-ownership
    finding), so the screen opens on something that makes the point.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT i_trip_no, s_org_node_name, s_dest_node_name,
                          s_trans_name, s_geofence_out_status, i_gps_ping_count,
                          ROUND(TIMESTAMPDIFF(MINUTE, dt_trip_start,
                                              dt_geofence_out) / 60.0, 1) AS hidden_tail_h,
                          ROUND(TIMESTAMPDIFF(MINUTE, dt_booking,
                                              dt_trip_start) / 60.0, 1) AS declared_h
                     FROM tta_trips
                    WHERE s_geofence_out_status = 'ok'
                      AND dt_booking IS NOT NULL
                      AND dt_geofence_out > dt_trip_start
                      AND i_gps_ping_count BETWEEN 200 AND 4000
                    ORDER BY hidden_tail_h DESC
                    LIMIT %s""",
                (limit,),
            )
            worst = cur.fetchall()

            cur.execute(
                """SELECT i_trip_no, s_org_node_name, s_dest_node_name,
                          s_trans_name, s_geofence_out_status, i_gps_ping_count,
                          NULL AS hidden_tail_h,
                          ROUND(TIMESTAMPDIFF(MINUTE, dt_booking,
                                              dt_trip_start) / 60.0, 1) AS declared_h
                     FROM tta_trips
                    WHERE s_geofence_out_status = 'gps_died_at_origin'
                      AND i_gps_ping_count BETWEEN 100 AND 4000
                    ORDER BY i_gps_ping_count DESC
                    LIMIT 4"""
            )
            died = cur.fetchall()

        def _row(r, why):
            return {
                "trip_no": r["i_trip_no"],
                "origin": r["s_org_node_name"],
                "destination": r["s_dest_node_name"],
                "transporter": r["s_trans_name"],
                "status": r["s_geofence_out_status"],
                "ping_count": r["i_gps_ping_count"],
                "hidden_tail_h": (float(r["hidden_tail_h"])
                                  if r["hidden_tail_h"] is not None else None),
                "declared_h": (float(r["declared_h"])
                               if r["declared_h"] is not None else None),
                "why": why,
            }

        return ([_row(r, "long confirmed hidden tail") for r in worst]
                + [_row(r, "tracker died inside the works") for r in died])
    finally:
        if own:
            conn.close()
