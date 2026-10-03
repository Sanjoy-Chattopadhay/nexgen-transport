"""Locations API — provides location names for frontend dropdowns
and route stats preview (with OSRM fallback for unknown routes)."""

import logging
import urllib.request
import json as _json
from decimal import Decimal
from datetime import datetime, date, timedelta

from fastapi import APIRouter, Depends
from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope, base_clause, summary_clause

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])


def _serialise(val):
    """Make MySQL values JSON-friendly."""
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, timedelta):
        return val.total_seconds() / 60  # minutes
    return val


def _clean_row(row: dict) -> dict:
    return {k: _serialise(v) for k, v in row.items()}


# ── OSRM distance estimation ──

def _geocode(place: str) -> tuple | None:
    """Geocode a place name via Nominatim (OpenStreetMap). Returns (lat, lon) or None."""
    try:
        q = urllib.request.quote(f"{place} India")
        url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "smart-truck-fleet/1.0"})
        resp = urllib.request.urlopen(req, timeout=5)
        data = _json.loads(resp.read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.warning("Geocode failed for '%s': %s", place, e)
    return None


def _osrm_route(lat1: float, lon1: float, lat2: float, lon2: float) -> dict | None:
    """Get driving distance/duration from OSRM (free, no API key)."""
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false"
        resp = urllib.request.urlopen(url, timeout=10)
        data = _json.loads(resp.read())
        if data.get("code") == "Ok" and data.get("routes"):
            r = data["routes"][0]
            return {
                "distance_km": round(r["distance"] / 1000, 1),
                "duration_minutes": round(r["duration"] / 60, 1),
            }
    except Exception as e:
        logger.warning("OSRM routing failed: %s", e)
    return None


def _estimate_distance(origin: str, destination: str) -> dict | None:
    """Estimate distance between two places using Nominatim + OSRM (free, open-source)."""
    logger.info("Estimating distance via OSRM: %s → %s", origin, destination)
    geo1 = _geocode(origin)
    geo2 = _geocode(destination)
    if not geo1 or not geo2:
        return None
    route = _osrm_route(geo1[0], geo1[1], geo2[0], geo2[1])
    if not route:
        return None
    # Estimate ETA for a truck (avg 35 km/h instead of OSRM's car speed)
    truck_speed_kmph = 35
    truck_duration_min = round(route["distance_km"] / truck_speed_kmph * 60, 1)
    return {
        "distance_km": route["distance_km"],
        "osrm_car_duration_min": route["duration_minutes"],
        "estimated_truck_duration_min": truck_duration_min,
        "truck_speed_assumed_kmph": truck_speed_kmph,
        "source": "osrm_estimate",
        "origin_coords": list(geo1),
        "destination_coords": list(geo2),
    }


# ── Endpoints ──

@router.get("")
def list_locations(search: str = "", limit: int = 200, conn=Depends(get_db)):
    """Return location names for autocomplete / dropdown."""
    with conn.cursor() as cur:
        if search:
            cur.execute(
                "SELECT id, name FROM locations WHERE name LIKE %s ORDER BY name LIMIT %s",
                (f"%{search}%", limit),
            )
        else:
            cur.execute("SELECT id, name FROM locations ORDER BY name LIMIT %s", (limit,))
        rows = cur.fetchall()
    return {"locations": [{"id": r["id"], "name": r["name"]} for r in rows]}


@router.get("/route-stats")
def route_stats_preview(origin: str, destination: str,
                        scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Return quick stats for a route pair — used to preview data before prediction."""
    logger.info("Route stats: %s → %s", origin, destination)
    result: dict = {"origin": origin, "destination": destination}

    # Consignor filters: route_summary carries a cnr_id dimension (rollup when
    # unscoped); the trips fact table carries cnr_id directly.
    cnr_sum, cnr_sum_p = summary_clause(scope)
    tclause, tparams = base_clause(scope, "cnr_id", alias="t")
    tfilter = f" AND {tclause}" if tclause else ""

    try:
        with conn.cursor() as cur:
            # Route summary
            cur.execute(
                f"""SELECT trip_count, avg_duration_min, avg_distance_km, avg_speed_kmph, eta_success_rate
                   FROM route_summary WHERE origin = %s AND destination = %s AND {cnr_sum}""",
                [origin, destination] + cnr_sum_p,
            )
            rs = cur.fetchone()
            result["route_summary"] = _clean_row(rs) if rs else None

            # Recent 5 trips on this route (d.name is the column in drivers table)
            cur.execute(
                f"""SELECT t.id, t.dispatch_entry_no, d.name AS driver_name,
                          t.trip_duration_minutes, t.avg_speed_kmph, t.trip_km, t.eta_met,
                          t.trip_start
                   FROM trips t
                   JOIN drivers d ON d.id = t.driver_id
                   JOIN locations o ON o.id = t.origin_id
                   JOIN locations dest ON dest.id = t.destination_id
                   WHERE o.name = %s AND dest.name = %s AND t.trip_status = 'C'{tfilter}
                   ORDER BY t.trip_start DESC LIMIT 5""",
                [origin, destination] + tparams,
            )
            result["recent_trips"] = [_clean_row(r) for r in cur.fetchall()]

            # Count total completed trips
            cur.execute(
                f"""SELECT COUNT(*) as total
                   FROM trips t
                   JOIN locations o ON o.id = t.origin_id
                   JOIN locations dest ON dest.id = t.destination_id
                   WHERE o.name = %s AND dest.name = %s AND t.trip_status = 'C'{tfilter}""",
                [origin, destination] + tparams,
            )
            row = cur.fetchone()
            result["total_trips"] = row["total"] if row else 0

            # Best / worst duration
            cur.execute(
                f"""SELECT MIN(trip_duration_minutes) as fastest, MAX(trip_duration_minutes) as slowest,
                          STDDEV(trip_duration_minutes) as duration_stddev
                   FROM trips t
                   JOIN locations o ON o.id = t.origin_id
                   JOIN locations dest ON dest.id = t.destination_id
                   WHERE o.name = %s AND dest.name = %s AND t.trip_status = 'C'
                     AND trip_duration_minutes > 0{tfilter}""",
                [origin, destination] + tparams,
            )
            extremes = cur.fetchone()
            result["fastest_minutes"] = float(extremes["fastest"]) if extremes and extremes["fastest"] else None
            result["slowest_minutes"] = float(extremes["slowest"]) if extremes and extremes["slowest"] else None
            result["duration_stddev"] = float(extremes["duration_stddev"]) if extremes and extremes["duration_stddev"] else None

    except Exception as e:
        logger.exception("Route stats query failed for %s → %s", origin, destination)
        result["error"] = str(e)
        result["route_summary"] = None
        result["recent_trips"] = []
        result["total_trips"] = 0
        result["fastest_minutes"] = None
        result["slowest_minutes"] = None
        result["duration_stddev"] = None

    # ── If no trips found, estimate distance via OSRM ──
    if result.get("total_trips", 0) == 0:
        estimate = _estimate_distance(origin, destination)
        if estimate:
            result["distance_estimate"] = estimate
            logger.info(
                "OSRM estimate for %s → %s: %.1f km, ~%.0f min (truck)",
                origin, destination, estimate["distance_km"], estimate["estimated_truck_duration_min"],
            )

    return result
