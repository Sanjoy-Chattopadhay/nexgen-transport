"""
On-demand trip weather + weather-impact correlator.

Ported from nextGen-FMS route_intelligence/services/weather*.py:
  - Historical hourly weather from the free Open-Meteo archive API
    (no API key), fetched at the trip's OWN coordinates and timestamps.
  - Cached in MySQL (tta_weather_cache) on a 0.25-degree / day grid,
    so re-running a trip costs zero network calls and a 430-km corridor
    needs only a handful of fetches.
  - Correlator: for every 30-min window of the trip, classify the weather
    (clear / rain / heavy_rain / storm / fog / snow) and decide whether a
    slow window is likely explained by adverse weather.

Answers the dispatcher's question: "the truck slowed down here —
was it weather, traffic, or driver?"
"""

import json
import logging
import statistics
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = "temperature_2m,precipitation,rain,weather_code,wind_speed_10m,cloud_cover"

GRID_DEG = 0.25          # cache grid ~25 km — weather doesn't vary finer than this
SLOW_RATIO = 0.60        # window is slow if avg speed < 0.6 x trip median
SLOW_ABS_KPH = 25.0      # ... or always slow below this absolute speed
RAIN_LIGHT_MM = 0.5
RAIN_HEAVY_MM = 4.0
WIND_STORM_KMH = 50.0
ADVERSE = {"rain", "heavy_rain", "storm", "snow", "fog"}

_DDL = """
CREATE TABLE IF NOT EXISTS tta_weather_cache (
    cache_key VARCHAR(48) PRIMARY KEY,
    lat DOUBLE NOT NULL,
    lng DOUBLE NOT NULL,
    date_str CHAR(10) NOT NULL,
    payload_json MEDIUMTEXT,
    fetched_at DATETIME NOT NULL,
    INDEX idx_twc_day (date_str)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _bootstrap(conn):
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def _grid(v: float) -> float:
    return round(round(v / GRID_DEG) * GRID_DEG, 2)


def _cache_key(lat: float, lng: float, date_str: str) -> str:
    return f"{_grid(lat)}|{_grid(lng)}|{date_str}"


def _fetch_day(lat: float, lng: float, date_str: str) -> dict:
    """One Open-Meteo archive call: full hourly day at a coordinate."""
    r = requests.get(ARCHIVE_URL, params={
        "latitude": _grid(lat), "longitude": _grid(lng),
        "start_date": date_str, "end_date": date_str,
        "hourly": HOURLY_VARS, "timezone": "auto",
    }, timeout=20)
    r.raise_for_status()
    return r.json()


def _day_payload(conn, lat: float, lng: float, when: datetime) -> dict:
    """Cached full-day hourly payload for the grid cell containing (lat, lng)."""
    date_str = when.strftime("%Y-%m-%d")
    key = _cache_key(lat, lng, date_str)
    with conn.cursor() as cur:
        cur.execute("SELECT payload_json FROM tta_weather_cache WHERE cache_key=%s", (key,))
        row = cur.fetchone()
        if row and row["payload_json"]:
            try:
                return json.loads(row["payload_json"])
            except Exception:
                pass
    try:
        payload = _fetch_day(lat, lng, date_str)
    except Exception as exc:
        logger.warning("weather fetch failed %s: %s", key, exc)
        return {"error": str(exc)}
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO tta_weather_cache (cache_key, lat, lng, date_str, payload_json, fetched_at)
               VALUES (%s,%s,%s,%s,%s,NOW())
               ON DUPLICATE KEY UPDATE payload_json=VALUES(payload_json), fetched_at=NOW()""",
            (key, _grid(lat), _grid(lng), date_str, json.dumps(payload)),
        )
    conn.commit()
    return payload


def _hour_weather(payload: dict, when: datetime) -> dict:
    """Pick the hour row out of a full-day hourly payload."""
    if not payload or "error" in payload:
        return {"error": (payload or {}).get("error", "no data")}
    h = payload.get("hourly", {})
    times = h.get("time", [])
    target = when.strftime("%Y-%m-%dT%H:00")
    try:
        i = times.index(target)
    except ValueError:
        return {"error": f"hour {target} not in payload"}

    def g(var):
        vals = h.get(var, [])
        return vals[i] if i < len(vals) else None

    return {
        "temperature_c": g("temperature_2m"),
        "rain_mm": g("rain") if g("rain") is not None else g("precipitation"),
        "wind_kmh": g("wind_speed_10m"),
        "cloud_cover_pct": g("cloud_cover"),
        "weather_code": g("weather_code"),
    }


# WMO weather-code buckets — https://open-meteo.com/en/docs
def _bucket_from_code(code) -> str:
    if code is None:
        return "clear"
    code = int(code)
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77:
        return "snow"
    if 95 <= code <= 99:
        return "storm"
    return "clear"


def classify_weather(w: dict) -> str:
    if not w or "error" in w:
        return "unknown"
    rain_mm = float(w.get("rain_mm") or 0)
    wind = float(w.get("wind_kmh") or 0)
    bucket = _bucket_from_code(w.get("weather_code"))
    if bucket == "rain" and rain_mm >= RAIN_HEAVY_MM:
        bucket = "heavy_rain"
    if wind >= WIND_STORM_KMH and bucket in ("clear", "rain"):
        bucket = "storm"
    if bucket == "clear" and rain_mm >= RAIN_LIGHT_MM:
        bucket = "heavy_rain" if rain_mm >= RAIN_HEAVY_MM else "rain"
    return bucket


def _note(bucket: str, w: dict, kph: float) -> str:
    rain = float(w.get("rain_mm") or 0)
    wind = float(w.get("wind_kmh") or 0)
    if bucket == "heavy_rain":
        return f"Heavy rain ({rain:.1f} mm/h) — speed at {kph:.0f} km/h."
    if bucket == "rain":
        return f"Rain ({rain:.1f} mm/h) — speed at {kph:.0f} km/h."
    if bucket == "storm":
        return f"Storm winds ({wind:.0f} km/h) — speed at {kph:.0f} km/h."
    if bucket == "fog":
        return f"Low-visibility fog — speed at {kph:.0f} km/h."
    if bucket == "snow":
        return f"Snowfall — speed at {kph:.0f} km/h."
    return "Clear weather — slowdown unlikely to be weather-driven."


# ---------------------------------------------------------------------------
# Public entry — weather impact for one trip (on demand)
# ---------------------------------------------------------------------------

def weather_impact_for_trip(conn, trip_no: int) -> dict:
    from nexgen.shared.analysis.tta_analysis import _load_bundle
    _bootstrap(conn)
    trip, metrics, pings = _load_bundle(conn, trip_no)
    if trip is None:
        raise ValueError(f"trip {trip_no} not found")
    if not pings:
        return {"trip_no": trip_no, "verdict": "no_data", "summary": {}, "windows": []}

    # 30-min windows with centre coordinate + avg moving speed
    wins: dict[datetime, dict] = {}
    for p in pings:
        w0 = p["ts"].replace(minute=(p["ts"].minute // 30) * 30, second=0, microsecond=0)
        w = wins.setdefault(w0, {"start": w0, "lats": [], "lngs": [],
                                 "spd_sum": 0.0, "spd_n": 0,
                                 "moving_min": 0.0, "stopped_min": 0.0})
        w["lats"].append(p["lat"])
        w["lngs"].append(p["lng"])
        w["moving_min" if p["moving"] else "stopped_min"] += p["gap_min"]
        if p["moving"] and p["spd"] > 0:
            w["spd_sum"] += p["spd"]
            w["spd_n"] += 1

    windows = sorted(wins.values(), key=lambda x: x["start"])
    speeds = [w["spd_sum"] / w["spd_n"] for w in windows if w["spd_n"]]
    median_kph = statistics.median(speeds) if speeds else 0.0
    slow_threshold = min(SLOW_ABS_KPH, max(5.0, median_kph * SLOW_RATIO))

    out, minutes_lost = [], 0.0
    slow_n = adverse_n = both_n = 0
    day_cache: dict[str, dict] = {}

    for w in windows:
        lat = sorted(w["lats"])[len(w["lats"]) // 2]
        lng = sorted(w["lngs"])[len(w["lngs"]) // 2]
        ts = w["start"]
        avg = round(w["spd_sum"] / w["spd_n"], 1) if w["spd_n"] else 0.0
        is_stopped_window = w["spd_n"] == 0

        ck = _cache_key(lat, lng, ts.strftime("%Y-%m-%d"))
        if ck not in day_cache:
            day_cache[ck] = _day_payload(conn, lat, lng, ts)
        wx = _hour_weather(day_cache[ck], ts)
        bucket = classify_weather(wx)

        # only moving-but-slow windows count as "slow" (parked ≠ weather-slowed)
        is_slow = (not is_stopped_window) and avg < slow_threshold
        is_adverse = bucket in ADVERSE
        caused = is_slow and is_adverse
        if is_slow:
            slow_n += 1
        if is_adverse:
            adverse_n += 1
        if caused:
            both_n += 1
            minutes_lost += w["moving_min"] + w["stopped_min"]

        out.append({
            "window": ts.strftime("%d %b %H:%M"),
            "t": ts.isoformat(),
            "lat": round(lat, 4), "lng": round(lng, 4),
            "avg_speed_kmph": avg,
            "moving_min": round(w["moving_min"], 0),
            "is_slow": is_slow,
            "weather": wx,
            "weather_bucket": bucket,
            "weather_caused": caused,
            "note": _note(bucket, wx, avg),
        })

    summary = {
        "windows_total": len(out),
        "windows_slow": slow_n,
        "windows_adverse_weather": adverse_n,
        "windows_slow_and_adverse": both_n,
        "minutes_lost_to_weather": round(minutes_lost, 0),
        "median_speed_kmph": round(median_kph, 1),
        "slow_threshold_kmph": round(slow_threshold, 1),
        "api_calls_made": len(day_cache),
    }
    pct = (both_n / len(out) * 100) if out else 0
    verdict = ("no_data" if not out
               else "weather_was_a_factor" if pct >= 15
               else "weather_present_but_minor" if adverse_n > 0
               else "weather_was_clear")

    return {"trip_no": trip_no, "verdict": verdict, "summary": summary, "windows": out}
