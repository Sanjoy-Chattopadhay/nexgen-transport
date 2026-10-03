"""Works / plant detention, measured against the geofence rather than the stamp.

The three windows
-----------------
For a zonal trip out of Jamshedpur there are three different "how long was the
truck held" answers, and they do not agree::

    dt_booking ............ plant entry (gate in)
        |
        |  (A) DECLARED works detention  = dt_trip_start - dt_booking
        v
    dt_trip_start ......... the gate-out stamp
        |
        |  (B) HIDDEN TAIL               = dt_geofence_out - dt_trip_start
        v
    dt_geofence_out ....... last observed inside the origin fence
        |
        |  (C) TRUE TOTAL                = dt_geofence_out - dt_booking = A + B
        v
    line-haul begins

(A) is what the system reports today. (B) is what this module exists to
expose: the stamp fires when the paperwork clears, and the truck then spends
hours more inside the works belt -- queueing, weighing, sitting in a
transporter yard -- before it is genuinely on the road. Measured on this
corpus (710 zonal trips with a confirmed exit) the median (A) is 13.0 h and
the median (B) is another 7.0 h, so the declared figure accounts for roughly
two thirds of the real hold.

Data-quality cases, kept visible rather than averaged away
----------------------------------------------------------
* **Negative tail** -- the truck cleared the fence *before* its own gate-out
  stamp, i.e. the stamp was entered late. Real and worth reporting, but it is
  a stamping defect, not detention, so it is counted separately instead of
  dragging the mean down (one trip in this corpus reads -270 h).
* **Wide uncertainty** -- `i_geofence_out_gap_min` is the ping gap after the
  exit ping. A trip whose tracker went quiet for three hours around the
  crossing has an exit time good to +/- 3 h, and rows above
  `MAX_TRUSTED_GAP_MIN` are excluded from the headline figures and reported
  as their own bucket.

Medians, not means, throughout: detention is heavily right-skewed and a
handful of multi-day holds otherwise set the "typical" figure.
"""

from __future__ import annotations

import logging

from nexgen.shared.legacy_db import get_connection

logger = logging.getLogger(__name__)

# Beyond this gap the exit timestamp is an interpolation across a GPS outage,
# not an observation, and should not shape a headline number.
MAX_TRUSTED_GAP_MIN = 60

# Statuses that carry a usable dt_geofence_out.
USABLE_STATUS = ("ok",)


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(len(s) * p / 100))], 2)


def _summarise(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p10": _pct(values, 10),
        "p25": _pct(values, 25),
        "median": _pct(values, 50),
        "p75": _pct(values, 75),
        "p90": _pct(values, 90),
        "mean": round(sum(values) / len(values), 2) if values else None,
    }


def _fetch(conn, trip_class: str | None, consignor_id: int | None):
    where = ["s_geofence_out_status IN %s"]
    params: list = [USABLE_STATUS]
    if trip_class:
        where.append("s_trip_class = %s")
        params.append(trip_class)
    if consignor_id:
        where.append("i_cnr_id = %s")
        params.append(consignor_id)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT i_trip_no, s_trans_name, s_dest_node_name, i_geofence_out_gap_min,
                       TIMESTAMPDIFF(MINUTE, dt_booking, dt_trip_start)     AS declared_min,
                       TIMESTAMPDIFF(MINUTE, dt_trip_start, dt_geofence_out) AS tail_min,
                       TIMESTAMPDIFF(MINUTE, dt_booking, dt_geofence_out)    AS total_min
                  FROM tta_trips
                 WHERE {' AND '.join(where)}
                   AND dt_booking IS NOT NULL""",
            params,
        )
        return cur.fetchall()


def summary(trip_class: str | None = "zonal", consignor_id: int | None = None, conn=None) -> dict:
    """Fleet-level detention summary across the three windows."""
    own = conn is None
    conn = conn or get_connection()
    try:
        rows = _fetch(conn, trip_class, consignor_id)

        wide_gap = [r for r in rows
                    if (r["i_geofence_out_gap_min"] or 0) > MAX_TRUSTED_GAP_MIN]
        trusted = [r for r in rows
                   if (r["i_geofence_out_gap_min"] or 0) <= MAX_TRUSTED_GAP_MIN]
        negative = [r for r in trusted if (r["tail_min"] or 0) < 0]
        clean = [r for r in trusted if (r["tail_min"] or 0) >= 0]

        def hours(rs, field):
            return [r[field] / 60.0 for r in rs if r[field] is not None]

        tails = hours(clean, "tail_min")
        return {
            "trip_class": trip_class,
            "trips_with_confirmed_exit": len(rows),
            "declared_works_detention_h": _summarise(hours(clean, "declared_min")),
            "hidden_tail_h": _summarise(tails),
            "true_total_detention_h": _summarise(hours(clean, "total_min")),
            "tail_over_4h": sum(1 for t in tails if t > 4),
            "tail_over_12h": sum(1 for t in tails if t > 12),
            "excluded": {
                "wide_gps_gap": len(wide_gap),
                "negative_tail_late_stamp": len(negative),
                "note": (
                    f"wide_gps_gap = exit uncertain by more than {MAX_TRUSTED_GAP_MIN} min; "
                    "negative_tail = truck cleared the fence before its own gate-out stamp"
                ),
            },
        }
    finally:
        if own:
            conn.close()


def by_dimension(
    dimension: str = "transporter",
    trip_class: str | None = "zonal",
    min_trips: int = 5,
    conn=None,
) -> list[dict]:
    """Hidden-tail leaderboard, grouped by transporter or destination.

    `min_trips` keeps single-trip flukes off a ranking that will be used to
    have conversations with carriers.
    """
    field = {"transporter": "s_trans_name", "destination": "s_dest_node_name"}[dimension]

    own = conn is None
    conn = conn or get_connection()
    try:
        rows = _fetch(conn, trip_class, None)
        buckets: dict[str, list[dict]] = {}
        for r in rows:
            if (r["i_geofence_out_gap_min"] or 0) > MAX_TRUSTED_GAP_MIN:
                continue
            if (r["tail_min"] or 0) < 0:
                continue
            buckets.setdefault(r[field] or "(unknown)", []).append(r)

        out = []
        for name, rs in buckets.items():
            if len(rs) < min_trips:
                continue
            tails = [r["tail_min"] / 60.0 for r in rs if r["tail_min"] is not None]
            declared = [r["declared_min"] / 60.0 for r in rs if r["declared_min"] is not None]
            out.append({
                dimension: name,
                "trips": len(rs),
                "declared_median_h": _pct(declared, 50),
                "hidden_tail_median_h": _pct(tails, 50),
                "hidden_tail_p90_h": _pct(tails, 90),
                "understatement_pct": (
                    round(100 * _pct(tails, 50) / _pct(declared, 50), 1)
                    if _pct(declared, 50) else None
                ),
            })
        out.sort(key=lambda d: (d["hidden_tail_median_h"] or 0), reverse=True)
        return out
    finally:
        if own:
            conn.close()
