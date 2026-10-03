"""
Driver insights derived from trip-wise GPS (tta_trip_gps).
-----------------------------------------------------------
The legacy `waypoints` table is empty for TTA-ingested data, so a driver's
driving pattern and safety alerts are computed directly from the GPS pings of
the trips they drove.

Join path:  drivers → trips (driver_id) → dispatch_entry_no == tta_trip_gps.i_trip_no

Window presets (anchored to the driver's most recent trip so a preset always
lands on data regardless of the server clock):
    all | 7d | 15d | 30d | 90d | last3 | last5 | last10
"""

from datetime import timedelta

# Safety thresholds (heavy-vehicle oriented).
# GPS pings on this feed average ~2.4 min apart, so "harsh" accel/brake is only
# trustworthy across genuinely adjacent fixes — hence the tight gap window; and
# a single isolated over-limit ping is ignored unless it is outright critical.
OVERSPEED_KPH = 60          # speed above this = overspeed candidate
CRITICAL_KPH = 80           # a single ping this fast is always flagged
OVERSPEED_MIN_MIN = 0.5     # else the run must be sustained at least this long
HARSH_DELTA_KPH = 15        # |Δspeed| between adjacent pings = harsh accel/brake
HARSH_MAX_GAP_S = 20        # ... only when the two fixes are truly consecutive
LONG_STOP_MIN = 120         # a stop this long is flagged
LONG_STOP_CRIT_MIN = 300    # ... and this long is a warning-level detention
NIGHT_HOURS = {22, 23, 0, 1, 2, 3, 4}

SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# Trip-set resolution for a window preset
# ---------------------------------------------------------------------------

def resolve_trip_nos(cur, driver_id: int, window: str, cnr_id: int | None = None) -> list[int]:
    """Trip numbers (i_trip_no) for the driver, filtered by the window preset.

    When cnr_id is given, only that consignor's trips are considered.
    """
    cnr_filter = " AND cnr_id = %s" if cnr_id is not None else ""
    params = [driver_id] + ([cnr_id] if cnr_id is not None else [])
    cur.execute(
        f"""
        SELECT dispatch_entry_no, trip_start
        FROM trips
        WHERE driver_id = %s AND dispatch_entry_no REGEXP '^[0-9]+$'{cnr_filter}
        ORDER BY trip_start DESC
        """,
        params,
    )
    trips = cur.fetchall()
    if not trips:
        return []

    window = (window or "all").lower()
    if window.startswith("last"):
        n = int(window[4:] or 0)
        sel = trips[:n] if n > 0 else trips
    elif window.endswith("d") and window[:-1].isdigit():
        days = int(window[:-1])
        anchor = max((t["trip_start"] for t in trips if t["trip_start"]), default=None)
        if anchor is None:
            sel = trips
        else:
            cutoff = anchor - timedelta(days=days)
            sel = [t for t in trips if t["trip_start"] and t["trip_start"] >= cutoff]
    else:
        sel = trips

    return [int(t["dispatch_entry_no"]) for t in sel]


def _in_clause(trip_nos: list[int]) -> str:
    return ",".join(str(int(n)) for n in trip_nos)


# ---------------------------------------------------------------------------
# Driving pattern
# ---------------------------------------------------------------------------

def build_driving_pattern(conn, driver_id: int, window: str, cnr_id: int | None = None) -> dict:
    with conn.cursor() as cur:
        trip_nos = resolve_trip_nos(cur, driver_id, window, cnr_id)
        empty = {"hourly_pattern": [], "speed_distribution": [], "daily_pattern": [],
                 "stats": None, "trip_count": 0, "window": window}
        if not trip_nos:
            return empty
        ins = _in_clause(trip_nos)

        # --- Hour-of-day: distance share + speed profile ---
        cur.execute(f"""
            SELECT HOUR(dt_message) AS hour_of_day,
                   ROUND(AVG(CASE WHEN is_moving = 1 AND i_speed > 0 THEN i_speed END), 1) AS avg_speed,
                   MAX(i_speed) AS max_speed,
                   SUM(i_dist) AS dist_m,
                   SUM(is_moving) AS moving_pings,
                   COUNT(*) AS data_points
            FROM tta_trip_gps
            WHERE i_trip_no IN ({ins})
            GROUP BY HOUR(dt_message)
        """)
        by_hour = {r["hour_of_day"]: r for r in cur.fetchall()}
        total_dist = sum((r["dist_m"] or 0) for r in by_hour.values()) or 1
        hourly_pattern = []
        for h in range(24):
            r = by_hour.get(h)
            dist_m = (r["dist_m"] or 0) if r else 0
            pts = (r["data_points"] or 0) if r else 0
            moving = (r["moving_pings"] or 0) if r else 0
            hourly_pattern.append({
                "hour_of_day": h,
                "avg_speed": float(r["avg_speed"]) if r and r["avg_speed"] is not None else 0.0,
                "max_speed": int(r["max_speed"]) if r and r["max_speed"] is not None else 0,
                "dist_km": round(dist_m / 1000, 1),
                "distance_share_pct": round(dist_m / total_dist * 100, 1),
                "moving_pct": round(moving / pts * 100, 1) if pts else 0.0,
                "data_points": pts,
            })

        # --- Speed distribution buckets ---
        cur.execute(f"""
            SELECT CASE
                     WHEN i_speed = 0 THEN 'Stopped (0)'
                     WHEN i_speed BETWEEN 1 AND 20 THEN 'Slow (1-20)'
                     WHEN i_speed BETWEEN 21 AND 40 THEN 'Medium (21-40)'
                     WHEN i_speed BETWEEN 41 AND 60 THEN 'Fast (41-60)'
                     WHEN i_speed BETWEEN 61 AND 80 THEN 'Very Fast (61-80)'
                     ELSE 'Over 80'
                   END AS speed_range,
                   COUNT(*) AS count
            FROM tta_trip_gps
            WHERE i_trip_no IN ({ins})
            GROUP BY speed_range
            ORDER BY FIELD(speed_range, 'Stopped (0)', 'Slow (1-20)', 'Medium (21-40)',
                           'Fast (41-60)', 'Very Fast (61-80)', 'Over 80')
        """)
        speed_distribution = cur.fetchall()

        # --- Day-of-week ---
        cur.execute(f"""
            SELECT DAYOFWEEK(dt_message) AS day_num,
                   DAYNAME(dt_message) AS day_name,
                   ROUND(AVG(CASE WHEN is_moving = 1 AND i_speed > 0 THEN i_speed END), 1) AS avg_speed,
                   ROUND(SUM(i_dist) / 1000, 1) AS dist_km,
                   COUNT(*) AS data_points
            FROM tta_trip_gps
            WHERE i_trip_no IN ({ins})
            GROUP BY DAYOFWEEK(dt_message), DAYNAME(dt_message)
            ORDER BY day_num
        """)
        daily_pattern = cur.fetchall()

        # --- Overall stats ---
        cur.execute(f"""
            SELECT COUNT(*) AS total_points,
                   COUNT(DISTINCT DATE(dt_message)) AS total_days,
                   ROUND(AVG(CASE WHEN is_moving = 1 AND i_speed > 0 THEN i_speed END), 1) AS overall_avg_speed,
                   MAX(i_speed) AS top_speed,
                   ROUND(SUM(i_dist) / 1000, 1) AS total_distance_tracked,
                   ROUND(SUM(is_moving) / COUNT(*) * 100, 1) AS moving_pct
            FROM tta_trip_gps
            WHERE i_trip_no IN ({ins})
        """)
        stats = cur.fetchone()

    return {
        "hourly_pattern": hourly_pattern,
        "speed_distribution": speed_distribution,
        "daily_pattern": daily_pattern,
        "stats": stats,
        "trip_count": len(trip_nos),
        "window": window,
    }


# ---------------------------------------------------------------------------
# Safety alerts derived from GPS
# ---------------------------------------------------------------------------

def _mk(alert_type, severity, title, message, trip_no, at, meta=None):
    return {"alert_type": alert_type, "severity": severity, "title": title,
            "message": message, "trip_no": trip_no,
            "at": at.isoformat() if at else None, "metadata": meta or {}}


def build_driver_alerts(conn, driver_id: int, window: str, limit: int = 80, cnr_id: int | None = None) -> dict:
    with conn.cursor() as cur:
        trip_nos = resolve_trip_nos(cur, driver_id, window, cnr_id)
        if not trip_nos:
            return {"alerts": [], "summary": {"total": 0, "by_type": {}, "by_severity": {},
                    "safety_score": 100}, "timeline": [], "trip_count": 0, "window": window}
        ins = _in_clause(trip_nos)

        cur.execute(f"""
            SELECT i_trip_no, dt_message, i_speed, is_moving, i_dist, s_wpnt1, s_wpnt1_st_abbr
            FROM tta_trip_gps
            WHERE i_trip_no IN ({ins})
            ORDER BY i_trip_no, dt_message
        """)
        pings = cur.fetchall()

    alerts: list[dict] = []
    # group pings per trip
    trips: dict[int, list] = {}
    for p in pings:
        trips.setdefault(p["i_trip_no"], []).append(p)

    for trip_no, ps in trips.items():
        # --- Overspeed runs ---
        run = []
        def flush_over(run):
            if not run:
                return
            peak = max(x["i_speed"] for x in run)
            dur = (run[-1]["dt_message"] - run[0]["dt_message"]).total_seconds() / 60
            # Ignore momentary single-ping blips unless outright critical.
            if peak < CRITICAL_KPH and dur < OVERSPEED_MIN_MIN:
                return
            sev = "critical" if peak >= CRITICAL_KPH else "warning"
            alerts.append(_mk(
                "overspeed", sev, f"Overspeeding · peak {peak} km/h",
                f"Sustained {round(dur,1)} min above {OVERSPEED_KPH} km/h near {run[0]['s_wpnt1'] or 'route'}.",
                trip_no, run[0]["dt_message"],
                {"peak_kph": peak, "minutes": round(dur, 1), "near": run[0]["s_wpnt1"]}))
        for p in ps:
            if p["i_speed"] > OVERSPEED_KPH:
                run.append(p)
            else:
                flush_over(run); run = []
        flush_over(run)

        # --- Harsh accel / brake + long stops (single pass over adjacent pings) ---
        stop_start = None
        harsh_accel = harsh_brake = 0
        harsh_samples = []
        prev = None
        for p in ps:
            # long stop tracking
            if not p["is_moving"]:
                if stop_start is None:
                    stop_start = p
            else:
                if stop_start is not None:
                    dur = (prev["dt_message"] - stop_start["dt_message"]).total_seconds() / 60 if prev else 0
                    if dur >= LONG_STOP_MIN:
                        sev = "warning" if dur >= LONG_STOP_CRIT_MIN else "info"
                        alerts.append(_mk(
                            "long_stop", sev, f"Long stop · {round(dur/60,1)} h",
                            f"Stationary {round(dur/60,1)} h near {stop_start['s_wpnt1'] or 'route'}.",
                            trip_no, stop_start["dt_message"],
                            {"minutes": round(dur), "near": stop_start["s_wpnt1"]}))
                    stop_start = None
            # harsh events
            if prev is not None and prev["is_moving"] and p["is_moving"]:
                gap = (p["dt_message"] - prev["dt_message"]).total_seconds()
                d = p["i_speed"] - prev["i_speed"]
                if 0 < gap <= HARSH_MAX_GAP_S and abs(d) >= HARSH_DELTA_KPH:
                    if d < 0:
                        harsh_brake += 1
                    else:
                        harsh_accel += 1
                    harsh_samples.append({"at": p["dt_message"], "delta": d,
                                          "near": p["s_wpnt1"], "kind": "brake" if d < 0 else "accel"})
            prev = p
        # trailing open stop
        if stop_start is not None and prev is not None:
            dur = (prev["dt_message"] - stop_start["dt_message"]).total_seconds() / 60
            if dur >= LONG_STOP_MIN:
                sev = "warning" if dur >= LONG_STOP_CRIT_MIN else "info"
                alerts.append(_mk(
                    "long_stop", sev, f"Long stop · {round(dur/60,1)} h",
                    f"Stationary {round(dur/60,1)} h near {stop_start['s_wpnt1'] or 'route'}.",
                    trip_no, stop_start["dt_message"],
                    {"minutes": round(dur), "near": stop_start["s_wpnt1"]}))

        if harsh_brake + harsh_accel > 0:
            worst = max(harsh_samples, key=lambda x: abs(x["delta"]))
            alerts.append(_mk(
                "harsh_driving", "warning" if harsh_brake + harsh_accel >= 20 else "info",
                f"Harsh driving · {harsh_brake} brakes / {harsh_accel} accels",
                f"{harsh_brake + harsh_accel} sudden speed changes (worst {abs(worst['delta'])} km/h near {worst['near'] or 'route'}).",
                trip_no, worst["at"],
                {"harsh_brake": harsh_brake, "harsh_accel": harsh_accel}))

        # --- Night driving ---
        night_moving = [p for p in ps if p["is_moving"] and p["dt_message"].hour in NIGHT_HOURS]
        if len(night_moving) >= 20:
            alerts.append(_mk(
                "night_driving", "info", f"Night driving · {len(night_moving)} pings",
                f"Vehicle moving during 22:00–05:00 window on trip {trip_no}.",
                trip_no, night_moving[0]["dt_message"],
                {"pings": len(night_moving)}))

    # sort: severity, then most recent first
    alerts.sort(key=lambda a: (SEVERITY_RANK.get(a["severity"], 9), a["at"] or ""), reverse=False)
    alerts.sort(key=lambda a: a["at"] or "", reverse=True)
    alerts.sort(key=lambda a: SEVERITY_RANK.get(a["severity"], 9))

    by_type: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for a in alerts:
        by_type[a["alert_type"]] = by_type.get(a["alert_type"], 0) + 1
        by_sev[a["severity"]] = by_sev.get(a["severity"], 0) + 1

    # timeline (alerts per day)
    tl: dict[str, int] = {}
    for a in alerts:
        d = (a["at"] or "")[:10]
        if d:
            tl[d] = tl.get(d, 0) + 1
    timeline = [{"date": d, "count": c} for d, c in sorted(tl.items())]

    penalty = by_sev.get("critical", 0) * 8 + by_sev.get("warning", 0) * 3 + by_sev.get("info", 0) * 1
    safety_score = max(0, 100 - penalty)

    return {
        "alerts": alerts[:limit],
        "summary": {"total": len(alerts), "by_type": by_type, "by_severity": by_sev,
                    "safety_score": safety_score},
        "timeline": timeline,
        "trip_count": len(trip_nos),
        "window": window,
    }
