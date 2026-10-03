"""Every break a truck took, from trip start to trip end, and why.

Why this exists
---------------
The trip page used to end in a table of raw GPS points. Fifty rows of

    02:29  22.784047  86.213884  Stopped  HSM CANTEEN (JH)  0.2 km
    02:30  22.784047  86.213884  Stopped  HSM CANTEEN (JH)  0.2 km
    02:31  22.784047  86.213884  Stopped  HSM CANTEEN (JH)  0.2 km

which is the same fact restated until the reader gives up: the truck was parked.
The interesting question is not where each ping was, it is **where the hours
went** -- how many breaks, how long, what kind, and which of them are explained.

So the pings are collapsed into halt events and each one is named: loading,
unloading, night rest, lunch, dinner, tea, a weather halt, or an unexplained
stop. The last category is the point of the whole exercise -- it is the time
nobody has an account for.

How a halt gets its name
------------------------
Four passes, and the order matters because a later pass must not overwrite a
better-evidenced earlier one. A stamp beats a clock; a clock beats the weather.

1. **Trip phase** (`tta_analysis._classify_stop`). A halt inside the
   booking->dispatch window at the origin is loading; one inside the
   ATA->ATA-out window at the destination is unloading. This beats every other
   rule: a four-hour standstill at the plant is loading, whatever time of day
   it happens.

2. **Duration and clock** (also `_classify_stop`). Night rest, long halt,
   lunch, dinner, tea -- the nextGen-FMS taxonomy, reused rather than
   reimplemented so the trip page and the analysis page can never disagree.

3. **Refinement** (`_refine`, here). Two things the shared taxonomy cannot
   know. A halt between the gate-out stamp and clearing the plant geofence is
   **detention at origin** -- the window the geofence work measures, and far
   more useful than what it was being called: a 64-hour standstill at the plant
   came back as "Night rest" purely because it started at 05:45. And a night's
   rest gets an upper bound, because a night is a night.

4. **Weather**, added here. A halt is re-labelled a weather halt only when the
   hour was genuinely adverse AND the existing label carries no explanation of
   its own. That condition is the whole design of this pass:

       a lunch break in the rain is still a lunch break.

   Re-labelling it would inflate "weather" with every meal that happened to
   coincide with a monsoon hour, and quietly delete the driver-behaviour signal.
   Only the unexplained residue -- `Halt`, `Extended halt`, `Long halt` -- is
   eligible, and only between 15 minutes and 6 hours: rain explains a driver
   pulling over, not a truck standing for a day. The weather reading is attached
   to every halt regardless, so a reader can see the conditions even where the
   label did not change.

Cost
----
Weather comes from the Open-Meteo archive through `tta_weather`, which caches a
full day's hourly data per ~25 km grid cell. A trip's halts cluster into a
handful of (day, cell) pairs, so a whole trip is typically a few calls, and the
count is reported in `weather.api_calls` so it is never a mystery.
"""

from __future__ import annotations

import logging

from nexgen.shared.analysis.tta_analysis import (
    _classify_stop, _detect_stops, _fmt, _load_bundle,
)
from nexgen.shared.analysis import tta_weather

logger = logging.getLogger(__name__)

# Labels that already explain themselves. A halt carrying one of these is never
# re-labelled by the weather pass -- see the module docstring.
EXPLAINED_LABELS = {
    "Loading / At plant",
    "Unloading / Detention",
    "Night rest",
    "Lunch break",
    "Dinner break",
    "Tea / short break",
}

# Only these are eligible to become a weather halt: the residue with no story.
UNEXPLAINED_LABELS = {"Halt", "Extended halt", "Long halt"}

# Labels set by a trip stamp rather than inferred. Nothing downstream overrides
# them: a stamp beats a guess.
PHASE_LABELS = {"Loading / At plant", "Unloading / Detention", "Detention at origin"}

# A driver's meal or tea break is a real thing that happened, and it stays named
# that way even when it falls inside the origin-detention window. Re-labelling a
# 13-minute tea break "Detention at origin" adds 13 minutes to a 66-hour figure
# and deletes the only record that the driver stopped for tea.
#
# Nothing is lost by leaving them alone: the detention window is measured
# authoritatively by `tta_trips.i_geofence_tail_min`, not by summing this
# register. The register's job is to say what happened, not to re-derive a
# number that already has a column.
DRIVER_BREAK_LABELS = {"Tea / short break", "Lunch break", "Dinner break"}

# A halt shorter than this is traffic, a signal, or a gate -- not a break worth
# attributing to the weather.
WEATHER_MIN_MINUTES = 15.0

# ...and one longer than this is not the weather either. Rain explains a driver
# pulling over for an hour; it does not explain a truck standing for a day. On
# the first trip this was run against, an unbounded rule produced a 22-hour
# "weather halt" that was really plant detention that happened to be rainy.
WEATHER_MAX_MINUTES = 6 * 60.0

# A night's rest is a night. The base taxonomy calls anything >= 6 h arriving
# 20:00-05:59 a night rest with no upper bound, which labelled a 64-hour
# standstill "Night rest" -- 55% of that trip's halt time under a name that
# says "normal". Past this it is an extended standstill and should look like
# one.
NIGHT_REST_MAX_MINUTES = 14 * 60.0

# Display order for the rollup, so the register reads as a story rather than
# whatever order the dict happened to be built in.
CATEGORY_ORDER = [
    "Loading / At plant",
    "Detention at origin",
    "Unloading / Detention",
    "Extended standstill",
    "Night rest",
    "Weather halt",
    "Long halt",
    "Extended halt",
    "Lunch break",
    "Dinner break",
    "Tea / short break",
    "Halt",
]


def _weather_label(bucket: str) -> str:
    return {
        "heavy_rain": "Heavy rain",
        "rain": "Rain",
        "storm": "Storm",
        "snow": "Snow",
        "fog": "Fog",
    }.get(bucket, bucket.replace("_", " ").title())


def _order_key(reason: str) -> int:
    try:
        return CATEGORY_ORDER.index(reason)
    except ValueError:
        return len(CATEGORY_ORDER)


def _refine(ev, windows, reason: str, rule: str) -> tuple[str, str]:
    """Bounds and phase rules the shared taxonomy does not carry.

    Two things are added here rather than in `tta_analysis._classify_stop`,
    because that function is also used by the trip-analysis page and changing it
    would move numbers there without warning.

    **Detention at origin.** The base rules know the booking->dispatch window
    (loading) and the ATA->ATA-out window (unloading), but not the window this
    project added: dispatch -> clearing the plant geofence. A halt in there is
    the hidden detention the geofence work measures, and naming it that is far
    more useful than "Night rest" -- which is what a 64-hour standstill at the
    plant was being called, purely because it began at 05:45.

    A driver's meal or tea break inside that window keeps its own name (see
    DRIVER_BREAK_LABELS). The detention total does not depend on this register --
    `tta_trips.i_geofence_tail_min` measures it directly -- so there is nothing
    to gain by overwriting the one record that the driver stopped for tea.

    **An upper bound on a night's rest.** A night is a night. Past
    NIGHT_REST_MAX_MINUTES the halt is an extended standstill and the register
    should say so instead of implying the driver slept for three days.
    """
    t, mins = ev["start"], ev["minutes"]

    if reason not in PHASE_LABELS and reason not in DRIVER_BREAK_LABELS:
        dept, fence_out = windows.get("dept"), windows.get("geofence_out")
        if dept and fence_out and dept <= t < fence_out:
            return ("Detention at origin",
                    "after the gate-out stamp, still inside the plant geofence")

    if reason in ("Night rest", "Long halt") and mins > NIGHT_REST_MAX_MINUTES:
        return ("Extended standstill",
                f"{mins / 60:.0f} h standing -- too long to be a night's rest")

    return reason, rule


def build_trip_breaks(conn, trip_no: int, with_weather: bool = True) -> dict | None:
    """The full break register for one trip. None if the trip does not exist."""
    trip, metrics, pings = _load_bundle(conn, trip_no)
    if trip is None:
        return None

    base = {
        "trip_no": trip_no,
        "route": f"{trip.get('s_org_node_name')} → {trip.get('s_dest_node_name')}",
        "vehicle": trip.get("s_asset_id"),
        "driver": trip.get("s_driver_name"),
        "transporter": trip.get("s_trans_name"),
    }

    if not pings:
        return {**base, "has_gps": False, "breaks": [], "categories": [],
                "kpis": {}, "note": "No GPS pings stored for this trip."}

    windows = {
        "booking": trip.get("dt_booking"),
        "dept": trip.get("dt_trip_start"),
        "ata": trip.get("dt_trip_ata"),
        "ata_out": metrics.get("dt_ata_out"),
        "closing": trip.get("dt_trip_end"),
        # Added by the geofence work: the moment the truck actually cleared the
        # plant fence, which is what makes "detention at origin" nameable.
        "geofence_out": trip.get("dt_geofence_out"),
    }

    events = _detect_stops(pings)
    for ev in events:
        reason, rule = _classify_stop(ev, windows)
        ev["base_reason"] = reason
        ev["reason"], ev["rule"] = _refine(ev, windows, reason, rule)
        ev["weather"] = None

    api_calls = 0
    weather_error = None
    if with_weather and events:
        try:
            tta_weather._bootstrap(conn)
            day_cache: dict[str, dict] = {}
            for ev in events:
                key = tta_weather._cache_key(
                    ev["lat"], ev["lng"], ev["start"].strftime("%Y-%m-%d"))
                if key not in day_cache:
                    day_cache[key] = tta_weather._day_payload(
                        conn, ev["lat"], ev["lng"], ev["start"])
                wx = tta_weather._hour_weather(day_cache[key], ev["start"])
                bucket = tta_weather.classify_weather(wx)
                ev["weather"] = {
                    "bucket": bucket,
                    "label": _weather_label(bucket),
                    "adverse": bucket in tta_weather.ADVERSE,
                    "rain_mm": wx.get("rain_mm"),
                    "temp_c": wx.get("temp_c"),
                    "wind_kmph": wx.get("wind_kmph"),
                }
                # The re-label, under the two conditions from the docstring.
                if (bucket in tta_weather.ADVERSE
                        and ev["reason"] in UNEXPLAINED_LABELS
                        and WEATHER_MIN_MINUTES <= ev["minutes"] <= WEATHER_MAX_MINUTES):
                    ev["reason"] = "Weather halt"
                    ev["rule"] = (
                        f"{_weather_label(bucket).lower()} during an otherwise "
                        f"unexplained {int(ev['minutes'])} min standstill"
                    )
            api_calls = len(day_cache)
        except Exception as exc:               # network, API change, bad row
            # Weather is an enrichment, not the point. Losing it must not lose
            # the break register, so the failure is reported and the base
            # taxonomy stands.
            logger.warning("weather enrichment failed for trip %s: %s", trip_no, exc)
            weather_error = str(exc)[:200]

    # ---- rollup -----------------------------------------------------------
    cats: dict[str, dict] = {}
    total_min = 0.0
    for ev in events:
        c = cats.setdefault(ev["reason"], {
            "reason": ev["reason"], "count": 0, "total_min": 0.0,
            "longest_min": 0.0, "example_rule": ev["rule"],
        })
        c["count"] += 1
        c["total_min"] += ev["minutes"]
        c["longest_min"] = max(c["longest_min"], ev["minutes"])
        total_min += ev["minutes"]

    for c in cats.values():
        c["share_pct"] = round(100 * c["total_min"] / total_min, 1) if total_min else 0.0
        c["avg_min"] = round(c["total_min"] / c["count"])
        c["total_min"] = round(c["total_min"])
        c["longest_min"] = round(c["longest_min"])
    categories = sorted(cats.values(), key=lambda c: (_order_key(c["reason"]), -c["total_min"]))

    longest = max(events, key=lambda e: e["minutes"], default=None)
    unexplained = [e for e in events if e["reason"] in UNEXPLAINED_LABELS]
    unexplained_min = sum(e["minutes"] for e in unexplained)
    weather_min = sum(e["minutes"] for e in events if e["reason"] == "Weather halt")

    breaks = [{
        "seq": i + 1,
        "start": _fmt(e["start"]),
        "end": _fmt(e["end"]),
        "minutes": round(e["minutes"]),
        "reason": e["reason"],
        "base_reason": e["base_reason"],
        "rule": e["rule"],
        "near": e["near"],
        "state": e["state"],
        "lat": e["lat"],
        "lng": e["lng"],
        "weather": e["weather"],
    } for i, e in enumerate(events)]

    return {
        **base,
        "has_gps": True,
        "kpis": {
            "total_breaks": len(events),
            "total_break_hours": round(total_min / 60, 1),
            "longest_break_min": round(longest["minutes"]) if longest else 0,
            "longest_break_where": longest["near"] if longest else None,
            "distinct_places": len({e["near"] for e in events if e["near"]}),
            "unexplained_breaks": len(unexplained),
            "unexplained_hours": round(unexplained_min / 60, 1),
            "weather_hours": round(weather_min / 60, 1),
        },
        "categories": categories,
        "breaks": breaks,
        "weather": {
            "enabled": with_weather,
            "api_calls": api_calls,
            "error": weather_error,
            "note": (
                "A halt is only re-labelled a weather halt when the hour was "
                "adverse AND the halt had no explanation of its own. A lunch "
                "break in the rain stays a lunch break."
            ),
        },
        "method": (
            "Stops are runs of non-moving pings. Each is named by trip phase "
            "first (loading / unloading), then by duration and time of day, "
            "then by weather. Times are the first and last ping of the run, "
            "never interpolated."
        ),
    }
