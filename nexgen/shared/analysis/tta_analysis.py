"""
TTA Trip GPS Analysis Engine
----------------------------
Computes the full per-trip analysis bundle from tta_trips +
tta_trip_metrics + tta_trip_gps.

Ported from the proven nextGen-FMS / RouteAnalyzer analytics
(gps.py, analyzers.py — see nextGen-FMS/docs/ANALYSIS_CATALOG.md):
  - KPI time accounting (gap attribution, capped at 15 min)
  - Stop detection (run-length blocks) + halt taxonomy classification
  - Speed zones / distribution / over-speed segments
  - Driving behaviour (hourly rhythm, day x hour heatmap, harsh events,
    night share, 0-100 driving score)
  - Waypoint consolidation (visit timeline) + state crossings
  - Time-window aggregation (30-min) + cumulative progress
  - Journey cost model (fuel + driver, idle waste)

Business framing — the trip lifecycle is split into the four windows
management cares about, and the GPS between each pair is analysed:
  1. dt_booking   -> dt_trip_start   (Pre-dispatch / at plant, loading)
  2. dt_trip_start-> dt_trip_ata     (Transit / line-haul)
  3. dt_trip_ata  -> dt_ata_out      (At destination — unloading / detention)
  4. dt_ata_out   -> dt_trip_end     (Release -> trip closure)  
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# --- Tunables (same defaults as nextGen-FMS) ---
OVERSPEED_KPH = 60
GAP_CAP_MIN = 15.0          # ignore device-offline gaps above this
STOP_MIN_MINUTES = 5.0      # minimum standstill to count as a stop event
HARSH_DELTA_KPH = 25        # |speed jump| between consecutive pings
HARSH_MAX_GAP_MIN = 5.0     # only score harsh events across small gaps

# Cost model params are UI-editable and stored in app_settings —
# see backend/app/services/tta_config.py (get_cost_config).


# ============================================
# LOADERS
# ============================================

def _load_bundle(conn, trip_no: int):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM tta_trips WHERE i_trip_no = %s", (trip_no,))
        trip = cur.fetchone()
        if not trip:
            return None, None, []
        cur.execute("SELECT * FROM tta_trip_metrics WHERE i_trip_no = %s", (trip_no,))
        metrics = cur.fetchone() or {}
        metrics.pop("raw_json", None)
        cur.execute(
            """SELECT dt_message, d_lat, d_long, i_speed, i_status_speed_kmph,
                      is_moving, s_wpnt1, s_wpnt1_st_abbr, i_wpnt1_mt,
                      i_dist, i_cdist, s_status
               FROM tta_trip_gps_cdist WHERE i_trip_no = %s ORDER BY dt_message""",
            (trip_no,),
        )
        rows = cur.fetchall()

    pings = []
    prev_ts = None
    for r in rows:
        ts = r["dt_message"]
        spd = r["i_status_speed_kmph"] if r["i_status_speed_kmph"] is not None else (r["i_speed"] or 0)
        gap_min = 0.0
        if prev_ts is not None:
            gap_min = min((ts - prev_ts).total_seconds() / 60.0, GAP_CAP_MIN)
        pings.append({
            "ts": ts,
            "lat": float(r["d_lat"]),
            "lng": float(r["d_long"]),
            "spd": int(spd),
            "moving": bool(r["is_moving"]),
            "wpnt": r["s_wpnt1"],
            "state": r["s_wpnt1_st_abbr"],
            "cdist": int(r["i_cdist"] or 0),
            "gap_min": gap_min,
        })
        prev_ts = ts
    return trip, metrics, pings


# ============================================
# HELPERS
# ============================================

def _minutes(a: datetime | None, b: datetime | None):
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 60.0, 1)


def _fmt(dt: datetime | None):
    return dt.isoformat() if dt else None


def _slice(pings, start, end):
    """Pings within [start, end) — open-ended when a bound is missing."""
    return [p for p in pings
            if (start is None or p["ts"] >= start) and (end is None or p["ts"] < end)]


def _gps_stats(sub):
    """Core movement stats for a set of pings (gap-attribution accounting)."""
    if not sub:
        return {"pings": 0, "distance_km": 0.0, "moving_min": 0.0, "stopped_min": 0.0,
                "utilization_pct": 0.0, "avg_moving_speed": 0.0, "max_speed": 0,
                "has_gps": False}
    moving_min = sum(p["gap_min"] for p in sub if p["moving"])
    stopped_min = sum(p["gap_min"] for p in sub if not p["moving"])
    total = moving_min + stopped_min
    mv_speeds = [p["spd"] for p in sub if p["moving"] and p["spd"] > 0]
    dist = (max(p["cdist"] for p in sub) - min(p["cdist"] for p in sub)) / 1000.0
    return {
        "pings": len(sub),
        "distance_km": round(dist, 2),
        "moving_min": round(moving_min, 1),
        "stopped_min": round(stopped_min, 1),
        "utilization_pct": round(100 * moving_min / total, 1) if total > 0 else 0.0,
        "avg_moving_speed": round(sum(mv_speeds) / len(mv_speeds), 1) if mv_speeds else 0.0,
        "max_speed": max((p["spd"] for p in sub), default=0),
        "has_gps": True,
    }


def _detect_stops(pings, min_minutes=STOP_MIN_MINUTES):
    """Run-length blocks of stopped pings -> stop events."""
    stops, block = [], []
    for p in pings:
        if not p["moving"]:
            block.append(p)
        else:
            if block:
                stops.append(block)
            block = []
    if block:
        stops.append(block)

    events = []
    for blk in stops:
        start, end = blk[0]["ts"], blk[-1]["ts"]
        mins = (end - start).total_seconds() / 60.0
        if mins < min_minutes:
            continue
        lats = sorted(p["lat"] for p in blk)
        lngs = sorted(p["lng"] for p in blk)
        wpnts = [p["wpnt"] for p in blk if p["wpnt"]]
        states = [p["state"] for p in blk if p["state"]]
        events.append({
            "start": start, "end": end, "minutes": round(mins, 0),
            "lat": round(lats[len(lats) // 2], 6),
            "lng": round(lngs[len(lngs) // 2], 6),
            "near": max(set(wpnts), key=wpnts.count) if wpnts else None,
            "state": max(set(states), key=states.count) if states else None,
        })
    return events


def _classify_stop(ev, windows):
    """Business-aware halt taxonomy: phase windows first, then the
    nextGen-FMS taxonomy rules (duration + arrival hour)."""
    t = ev["start"]
    mins = ev["minutes"]
    ata, ata_out = windows.get("ata"), windows.get("ata_out")
    dept, booking = windows.get("dept"), windows.get("booking")

    if ata and t >= ata and (ata_out is None or t < ata_out):
        return "Unloading / Detention", "inside ATA → ATA-out window at destination"
    if dept and t < dept and (booking is None or t >= booking):
        return "Loading / At plant", "inside booking → dispatch window at origin"

    hour = t.hour
    if mins >= 360 and (hour >= 20 or hour <= 5):
        return "Night rest", "≥ 6 h, arriving 20:00–05:59"
    if mins >= 360:
        return "Long halt", "≥ 6 h, daytime arrival"
    if mins > 100:
        return "Extended halt", "> 1 h 40 m"
    if 25 <= mins <= 100 and 11 <= hour <= 15:
        return "Lunch break", "25–100 min, 11:00–15:00"
    if 25 <= mins <= 100 and 19 <= hour <= 23:
        return "Dinner break", "25–100 min, 19:00–23:00"
    if 8 <= mins <= 25:
        return "Tea / short break", "8–25 min"
    return "Halt", "short standstill"


# ============================================
# SECTION BUILDERS
# ============================================

def _build_phases(trip, metrics, pings):
    booking = trip.get("dt_booking")
    dept = trip.get("dt_trip_start")
    ata = trip.get("dt_trip_ata")
    ata_out = metrics.get("dt_ata_out")
    closing = trip.get("dt_trip_end")

    defs = [
        ("loading", "Pre-Dispatch / Loading", "At plant: booking to gate-out", booking, dept),
        ("transit", "Transit", "Line-haul: dispatch to arrival at destination", dept, ata),
        ("unloading", "Unloading / Detention", "At destination: ATA to ATA-out", ata, ata_out),
        ("closure", "Release → Closure", "ATA-out to trip closing", ata_out, closing),
    ]
    phases = []
    for key, label, desc, start, end in defs:
        duration = _minutes(start, end)
        anomaly = None
        if duration is not None and duration < 0:
            anomaly = "closed before this window ended (provider closed trip at arrival)"
            duration = 0.0
        sub = _slice(pings, start, end) if (start or end) else []
        phases.append({
            "key": key, "label": label, "description": desc,
            "from_ts": _fmt(start), "to_ts": _fmt(end),
            "duration_min": duration,
            "anomaly": anomaly,
            "gps": _gps_stats(sub),
        })
    total_known = sum(p["duration_min"] or 0 for p in phases)
    for p in phases:
        p["share_pct"] = round(100 * (p["duration_min"] or 0) / total_known, 1) if total_known > 0 else 0.0
    return phases


def _build_stops_section(pings, windows):
    events = _detect_stops(pings)
    cats: dict[str, dict] = {}
    total_min = 0.0
    for ev in events:
        reason, rule = _classify_stop(ev, windows)
        ev["reason"], ev["rule"] = reason, rule
        c = cats.setdefault(reason, {"reason": reason, "rule": rule, "count": 0,
                                     "total_min": 0.0, "longest_min": 0.0})
        c["count"] += 1
        c["total_min"] += ev["minutes"]
        c["longest_min"] = max(c["longest_min"], ev["minutes"])
        total_min += ev["minutes"]

    categories = sorted(cats.values(), key=lambda c: -c["total_min"])
    for c in categories:
        c["share_pct"] = round(100 * c["total_min"] / total_min, 1) if total_min else 0.0
        c["avg_min"] = round(c["total_min"] / c["count"], 0)

    longest = max(events, key=lambda e: e["minutes"], default=None)
    out_events = [{**e, "start": _fmt(e["start"]), "end": _fmt(e["end"])} for e in events]
    return {
        "kpis": {
            "total_stops": len(events),
            "total_stop_hours": round(total_min / 60, 1),
            "longest_stop_min": longest["minutes"] if longest else 0,
            "longest_stop_where": longest["near"] if longest else None,
            "distinct_places": len({e["near"] for e in events if e["near"]}),
        },
        "categories": categories,
        "events": out_events,
    }


def _build_speed_section(pings):
    mv = [p for p in pings if p["moving"] and p["spd"] > 0]
    speeds = [p["spd"] for p in mv]
    if not speeds:
        return {"zones": {}, "histogram": [], "overspeed_segments": [], "kpis": {}}

    n = len(speeds)
    mean = sum(speeds) / n
    std = (sum((s - mean) ** 2 for s in speeds) / n) ** 0.5

    # 10-kph histogram over moving pings
    max_band = max(80, ((max(speeds) // 10) + 1) * 10)
    histogram = []
    for lo in range(0, int(max_band), 10):
        cnt = sum(1 for s in speeds if lo <= s < lo + 10)
        histogram.append({"band": f"{lo}-{lo + 10}", "count": cnt})

    # contiguous over-speed blocks
    segs, block = [], []
    for p in pings:
        if p["spd"] > OVERSPEED_KPH:
            block.append(p)
        else:
            if block:
                segs.append(block)
            block = []
    if block:
        segs.append(block)
    overspeed_segments = [{
        "start": _fmt(b[0]["ts"]), "end": _fmt(b[-1]["ts"]),
        "minutes": round((b[-1]["ts"] - b[0]["ts"]).total_seconds() / 60, 1),
        "peak_kph": max(p["spd"] for p in b),
        "near": b[0]["wpnt"],
    } for b in segs]
    overspeed_segments.sort(key=lambda s: -s["peak_kph"])

    over_pings = sum(1 for p in pings if p["spd"] > OVERSPEED_KPH)
    return {
        "kpis": {
            "avg_moving_speed": round(mean, 1),
            "max_speed": max(speeds),
            "speed_std_dev": round(std, 1),
            "consistency": "High" if std < 15 else "Moderate" if std < 25 else "Low",
            "overspeed_threshold": OVERSPEED_KPH,
            "overspeed_pings": over_pings,
            "overspeed_pct": round(100 * over_pings / max(len(pings), 1), 1),
        },
        "zones": {
            "slow_pct": round(100 * sum(1 for s in speeds if s < 20) / n, 1),
            "moderate_pct": round(100 * sum(1 for s in speeds if 20 <= s < 40) / n, 1),
            "normal_pct": round(100 * sum(1 for s in speeds if 40 <= s < 60) / n, 1),
            "high_pct": round(100 * sum(1 for s in speeds if s >= 60) / n, 1),
        },
        "histogram": histogram,
        "overspeed_segments": overspeed_segments[:20],
    }


def _build_driving_section(pings):
    if not pings:
        return {}
    by_hour = [{"hour": h, "pings": 0, "moving": 0, "spd_sum": 0.0} for h in range(24)]
    heat: dict[tuple, dict] = {}
    for p in pings:
        h = p["ts"].hour
        by_hour[h]["pings"] += 1
        if p["moving"]:
            by_hour[h]["moving"] += 1
            by_hour[h]["spd_sum"] += p["spd"]
        dow = (p["ts"].weekday() + 1) % 7 + 1  # 1 = Sunday ... 7 = Saturday
        cell = heat.setdefault((dow, h), {"day_of_week": dow, "hour_of_day": h,
                                          "activity": 0, "moving_min": 0.0})
        cell["activity"] += 1
        if p["moving"]:
            cell["moving_min"] += p["gap_min"]

    hourly = [{
        "hour": f"{b['hour']:02d}",
        "avg_speed": round(b["spd_sum"] / b["moving"], 1) if b["moving"] else 0.0,
        "moving_pct": round(100 * b["moving"] / b["pings"], 0) if b["pings"] else 0.0,
        "pings": b["pings"],
    } for b in by_hour]

    # harsh events (speed delta across small gaps)
    harsh_accel = harsh_brake = 0
    harsh_events = []
    for i in range(1, len(pings)):
        a, b = pings[i - 1], pings[i]
        if b["gap_min"] > HARSH_MAX_GAP_MIN:
            continue
        d = b["spd"] - a["spd"]
        if d > HARSH_DELTA_KPH:
            harsh_accel += 1
            harsh_events.append({"ts": _fmt(b["ts"]), "type": "accel", "delta": d, "near": b["wpnt"]})
        elif d < -HARSH_DELTA_KPH:
            harsh_brake += 1
            harsh_events.append({"ts": _fmt(b["ts"]), "type": "brake", "delta": d, "near": b["wpnt"]})

    moving_pings = sum(1 for p in pings if p["moving"])
    night_moving = sum(1 for p in pings if p["moving"] and (p["ts"].hour >= 22 or p["ts"].hour <= 4))
    night_pct = round(100 * night_moving / max(moving_pings, 1), 1)
    over_pct = round(100 * sum(1 for p in pings if p["spd"] > OVERSPEED_KPH) / max(len(pings), 1), 1)
    harsh_per_100 = round(100 * (harsh_accel + harsh_brake) / max(len(pings), 1), 1)
    score = max(0.0, min(100.0, 100 - over_pct * 1.5 - harsh_per_100 * 4 - night_pct * 0.2))

    # longest continuous drive (fatigue proxy): moving run, allowing < 5 min pauses
    longest_min, cur_start, last_move_ts = 0.0, None, None
    best = None
    for p in pings:
        if p["moving"]:
            if cur_start is None or (last_move_ts and (p["ts"] - last_move_ts).total_seconds() / 60 > 5):
                cur_start = p["ts"]
            last_move_ts = p["ts"]
            run = (last_move_ts - cur_start).total_seconds() / 60
            if run > longest_min:
                longest_min = run
                best = (cur_start, last_move_ts)

    # per-day movement
    days: dict[str, dict] = {}
    for p in pings:
        d = p["ts"].date().isoformat()
        day = days.setdefault(d, {"date": d, "moving_min": 0.0, "stopped_min": 0.0,
                                  "min_cdist": p["cdist"], "max_cdist": p["cdist"]})
        day["moving_min" if p["moving"] else "stopped_min"] += p["gap_min"]
        day["min_cdist"] = min(day["min_cdist"], p["cdist"])
        day["max_cdist"] = max(day["max_cdist"], p["cdist"])
    daily = [{
        "date": d["date"],
        "distance_km": round((d["max_cdist"] - d["min_cdist"]) / 1000, 1),
        "drive_hours": round(d["moving_min"] / 60, 1),
        "idle_hours": round(d["stopped_min"] / 60, 1),
    } for d in sorted(days.values(), key=lambda x: x["date"])]

    peak = max(hourly, key=lambda x: x["moving_pct"])
    return {
        "score": round(score),
        "kpis": {
            "harsh_accel": harsh_accel,
            "harsh_brake": harsh_brake,
            "harsh_per_100_pings": harsh_per_100,
            "night_driving_pct": night_pct,
            "overspeed_pct": over_pct,
            "peak_hour": peak["hour"],
            "longest_continuous_drive_min": round(longest_min, 0),
            "longest_drive_window": [_fmt(best[0]), _fmt(best[1])] if best else None,
        },
        "by_hour": hourly,
        "heatmap": list(heat.values()),
        "harsh_events": harsh_events[:25],
        "daily": daily,
    }


def _build_waypoint_section(pings):
    """Consolidate consecutive pings sharing wpnt1 into visit rows."""
    visits = []
    cur = None
    for p in pings:
        w = p["wpnt"] or "—"
        if cur is None or cur["waypoint"] != w:
            if cur:
                visits.append(cur)
            cur = {"waypoint": w, "state": p["state"], "arrive": p["ts"], "depart": p["ts"],
                   "pings": 0, "min_cdist": p["cdist"], "max_cdist": p["cdist"],
                   "moving_min": 0.0, "stopped_min": 0.0, "spd_sum": 0.0, "spd_n": 0}
        cur["depart"] = p["ts"]
        cur["pings"] += 1
        cur["min_cdist"] = min(cur["min_cdist"], p["cdist"])
        cur["max_cdist"] = max(cur["max_cdist"], p["cdist"])
        cur["moving_min" if p["moving"] else "stopped_min"] += p["gap_min"]
        if p["moving"] and p["spd"] > 0:
            cur["spd_sum"] += p["spd"]
            cur["spd_n"] += 1
    if cur:
        visits.append(cur)

    out = []
    for v in visits:
        dwell = (v["depart"] - v["arrive"]).total_seconds() / 60
        out.append({
            "waypoint": v["waypoint"], "state": v["state"],
            "arrive": _fmt(v["arrive"]), "depart": _fmt(v["depart"]),
            "dwell_min": round(dwell, 0),
            "km_covered": round((v["max_cdist"] - v["min_cdist"]) / 1000, 1),
            "cum_km": round(v["max_cdist"] / 1000, 1),
            "moving_min": round(v["moving_min"], 0),
            "stopped_min": round(v["stopped_min"], 0),
            "avg_speed": round(v["spd_sum"] / v["spd_n"], 1) if v["spd_n"] else 0.0,
            "pings": v["pings"],
        })

    # dwell leaders (where the time actually went)
    dwell_by_wp: dict[str, dict] = {}
    for v in out:
        d = dwell_by_wp.setdefault(v["waypoint"], {"waypoint": v["waypoint"], "state": v["state"],
                                                   "visits": 0, "total_min": 0.0, "stopped_min": 0.0})
        d["visits"] += 1
        d["total_min"] += v["dwell_min"]
        d["stopped_min"] += v["stopped_min"]
    top_dwell = sorted(dwell_by_wp.values(), key=lambda x: -x["stopped_min"])[:10]

    # state-border crossings
    crossings = []
    prev_state = None
    for p in pings:
        if p["state"] and prev_state and p["state"] != prev_state:
            crossings.append({"ts": _fmt(p["ts"]), "from_state": prev_state,
                              "to_state": p["state"], "near": p["wpnt"],
                              "cum_km": round(p["cdist"] / 1000, 1)})
        if p["state"]:
            prev_state = p["state"]

    states = []
    for p in pings:
        if p["state"] and p["state"] not in states:
            states.append(p["state"])
    return {
        "kpis": {
            "waypoints_touched": len({v["waypoint"] for v in out if v["waypoint"] != "—"}),
            "visits": len(out),
            "states_crossed": states,
            "border_crossings": len(crossings),
        },
        "visits": out,
        "top_dwell": top_dwell,
        "state_crossings": crossings,
    }


def _build_progress_section(pings, max_points=400):
    if not pings:
        return {"series": [], "windows": []}
    step = max(len(pings) // max_points, 1)
    series = [{
        "t": _fmt(p["ts"]),
        "km": round(p["cdist"] / 1000, 1),
        "spd": p["spd"],
    } for i, p in enumerate(pings) if i % step == 0 or i == len(pings) - 1]

    # 30-min windows
    wins: dict[datetime, dict] = {}
    for p in pings:
        w0 = p["ts"].replace(minute=(p["ts"].minute // 30) * 30, second=0, microsecond=0)
        w = wins.setdefault(w0, {"start": w0, "moving_min": 0.0, "stopped_min": 0.0,
                                 "min_cdist": p["cdist"], "max_cdist": p["cdist"],
                                 "spd_sum": 0.0, "spd_n": 0, "max_spd": 0})
        w["moving_min" if p["moving"] else "stopped_min"] += p["gap_min"]
        w["min_cdist"] = min(w["min_cdist"], p["cdist"])
        w["max_cdist"] = max(w["max_cdist"], p["cdist"])
        w["max_spd"] = max(w["max_spd"], p["spd"])
        if p["moving"] and p["spd"] > 0:
            w["spd_sum"] += p["spd"]
            w["spd_n"] += 1

    windows = [{
        "window": w["start"].strftime("%d %b %H:%M"),
        "t": _fmt(w["start"]),
        "km": round((w["max_cdist"] - w["min_cdist"]) / 1000, 2),
        "avg_speed": round(w["spd_sum"] / w["spd_n"], 1) if w["spd_n"] else 0.0,
        "max_speed": w["max_spd"],
        "moving_min": round(w["moving_min"], 0),
        "stopped_min": round(w["stopped_min"], 0),
    } for w in sorted(wins.values(), key=lambda x: x["start"])]
    return {"series": series, "windows": windows}


def _build_cost_section(stats, cost: dict):
    """Fuel + driver cost estimate (RouteAnalyzer journey cost model)."""
    dist = stats["distance_km"]
    moving_h = stats["moving_min"] / 60
    stopped_h = stats["stopped_min"] / 60
    total_h = moving_h + stopped_h
    moving_fuel = dist / cost["fuel_efficiency_kmpl"] if cost["fuel_efficiency_kmpl"] else 0
    idle_fuel = stopped_h * cost["idle_fuel_consumption_lph"]
    fuel_cost = (moving_fuel + idle_fuel) * cost["fuel_price_per_liter"]
    driver_cost = total_h * cost["driver_wage_per_hour"]
    total = fuel_cost + driver_cost
    return {
        "params": cost,
        "fuel_liters": round(moving_fuel + idle_fuel, 1),
        "moving_fuel_liters": round(moving_fuel, 1),
        "idle_fuel_liters": round(idle_fuel, 1),
        "fuel_cost_inr": round(fuel_cost, 0),
        "driver_cost_inr": round(driver_cost, 0),
        "total_cost_inr": round(total, 0),
        "idle_waste_inr": round(idle_fuel * cost["fuel_price_per_liter"], 0),
        "cost_per_km": round(total / dist, 1) if dist > 0 else 0,
    }


# ============================================
# MAIN ENTRY
# ============================================

def build_trip_analysis(conn, trip_no: int) -> dict | None:
    from nexgen.shared.analysis.tta_config import get_cost_config
    trip, metrics, pings = _load_bundle(conn, trip_no)
    if trip is None:
        return None
    cost_params = get_cost_config(conn)

    windows = {
        "booking": trip.get("dt_booking"),
        "dept": trip.get("dt_trip_start"),
        "ata": trip.get("dt_trip_ata"),
        "ata_out": metrics.get("dt_ata_out"),
        "closing": trip.get("dt_trip_end"),
    }

    overall = _gps_stats(pings)
    stops = _build_stops_section(pings, windows)
    speed = _build_speed_section(pings)
    driving = _build_driving_section(pings)
    phases = _build_phases(trip, metrics, pings)

    dm = metrics.get("d_distance_travelled_km")
    delivery_delta = metrics.get("i_delivery_delta_min")
    overview = {
        "trip_no": trip_no,
        "vehicle": trip.get("s_asset_id"),
        "driver": trip.get("s_driver_name"),
        "route": f"{trip.get('s_org_node_name')} → {trip.get('s_dest_node_name')}",
        "consignor": trip.get("s_cnr_name"),
        "consignee": trip.get("s_cne_name"),
        "transporter": trip.get("s_trans_name"),
        "status": trip.get("c_trip_status"),
        "delivery_status": metrics.get("s_delivery_status"),
        "delivery_delta_min": delivery_delta,
        "kpis": {
            **overall,
            "distance_declared_km": float(dm) if dm is not None else None,
            "gps_vs_declared_km": round(overall["distance_km"] - float(dm), 1) if dm is not None else None,
            "transit_time_min": metrics.get("i_transit_time_min"),
            "detention_min": metrics.get("i_detention_min"),
            "speed_violations_declared": metrics.get("i_speed_violation"),
            "driving_score": driving.get("score"),
            "total_stops": stops["kpis"]["total_stops"],
            "first_ping": _fmt(pings[0]["ts"]) if pings else None,
            "last_ping": _fmt(pings[-1]["ts"]) if pings else None,
        },
    }

    return {
        "overview": overview,
        "phases": phases,
        "stops": stops,
        "speed": speed,
        "driving": driving,
        "waypoints": _build_waypoint_section(pings),
        "progress": _build_progress_section(pings),
        "cost": _build_cost_section(overall, cost_params),
        "generated_at": datetime.now().isoformat(),
    }
