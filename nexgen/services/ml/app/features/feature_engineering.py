"""
Centralized feature engineering for all ML models.
Used by both training pipelines and serving endpoints.
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict


def extract_temporal_features(trip_start) -> Dict:
    if trip_start is None or pd.isna(trip_start):
        return {
            "hour": None, "day_of_week": None, "is_weekend": None,
            "month": None, "time_bucket": None,
        }

    dt = pd.to_datetime(trip_start)
    hour = dt.hour
    dow = dt.dayofweek

    if 6 <= hour < 12:
        time_bucket = "morning"
    elif 12 <= hour < 17:
        time_bucket = "afternoon"
    elif 17 <= hour < 21:
        time_bucket = "evening"
    else:
        time_bucket = "night"

    return {
        "hour": hour,
        "day_of_week": dow,
        "is_weekend": 1 if dow >= 5 else 0,
        "month": dt.month,
        "time_bucket": time_bucket,
    }


def get_route_features(conn, origin_name: str, dest_name: str) -> Dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT avg_duration_min, avg_distance_km, avg_speed_kmph, trip_count, eta_success_rate
            FROM route_summary
            WHERE origin = %s AND destination = %s
            """,
            (origin_name, dest_name),
        )
        row = cur.fetchone()

    if row:
        return {
            "route_avg_duration": float(row["avg_duration_min"]) if row["avg_duration_min"] else None,
            "route_avg_distance": float(row["avg_distance_km"]) if row["avg_distance_km"] else None,
            "route_avg_speed": float(row["avg_speed_kmph"]) if row["avg_speed_kmph"] else None,
            "route_trip_count": row["trip_count"],
            "route_eta_success": float(row["eta_success_rate"]) if row["eta_success_rate"] else None,
        }
    return {
        "route_avg_duration": None, "route_avg_distance": None, "route_avg_speed": None,
        "route_trip_count": 0, "route_eta_success": None,
    }


def get_driver_features(conn, driver_id: int) -> Dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT avg_duration_min, avg_speed_kmph, eta_success_rate,
                   total_trips, vehicles_used, total_distance_km
            FROM driver_summary
            WHERE driver_id = %s
            """,
            (driver_id,),
        )
        row = cur.fetchone()

    if row:
        return {
            "driver_avg_duration": float(row["avg_duration_min"]) if row["avg_duration_min"] else None,
            "driver_avg_speed": float(row["avg_speed_kmph"]) if row["avg_speed_kmph"] else None,
            "driver_eta_success": float(row["eta_success_rate"]) if row["eta_success_rate"] else None,
            "driver_total_trips": row["total_trips"],
            "driver_vehicles_used": row["vehicles_used"],
        }
    return {
        "driver_avg_duration": None, "driver_avg_speed": None,
        "driver_eta_success": None, "driver_total_trips": 0, "driver_vehicles_used": 0,
    }


def get_vehicle_features(conn, vehicle_id: int) -> Dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT avg_speed_kmph, total_trips, total_distance_km, eta_success_rate
            FROM vehicle_summary
            WHERE vehicle_id = %s
            """,
            (vehicle_id,),
        )
        row = cur.fetchone()

    if row:
        return {
            "vehicle_avg_speed": float(row["avg_speed_kmph"]) if row["avg_speed_kmph"] else None,
            "vehicle_total_trips": row["total_trips"],
            "vehicle_eta_success": float(row["eta_success_rate"]) if row["eta_success_rate"] else None,
        }
    return {"vehicle_avg_speed": None, "vehicle_total_trips": 0, "vehicle_eta_success": None}


def get_time_pattern_features(conn, origin: str, dest: str, hour: int, dow: int) -> Dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT avg_duration, trip_count, eta_success_rate
            FROM route_time_patterns
            WHERE origin = %s AND destination = %s AND hour_of_day = %s AND day_of_week = %s
            """,
            (origin, dest, hour, dow),
        )
        row = cur.fetchone()

    if row:
        return {
            "time_pattern_avg_duration": float(row["avg_duration"]) if row["avg_duration"] else None,
            "time_pattern_trip_count": row["trip_count"],
            "time_pattern_eta_success": float(row["eta_success_rate"]) if row["eta_success_rate"] else None,
        }
    return {"time_pattern_avg_duration": None, "time_pattern_trip_count": 0, "time_pattern_eta_success": None}


def build_feature_vector(
    temporal: Dict,
    route: Dict,
    driver: Dict,
    vehicle: Dict,
    time_pattern: Dict,
    trip_km: Optional[float] = None,
    is_5am_default: int = 0,
) -> pd.DataFrame:
    """Combine all features into a single DataFrame row for model input."""
    features = {}
    features.update(temporal)
    features.update(route)
    features.update(driver)
    features.update(vehicle)
    features.update(time_pattern)
    if trip_km is not None:
        features["trip_km"] = trip_km
    features["is_5am_default"] = is_5am_default

    # Remove non-numeric for model (time_bucket is categorical)
    if "time_bucket" in features:
        bucket_map = {"morning": 0, "afternoon": 1, "evening": 2, "night": 3}
        features["time_bucket_encoded"] = bucket_map.get(features.pop("time_bucket"), -1)

    df = pd.DataFrame([features])

    # Fill NaN with global defaults (0 for counts, median-ish for others)
    df = df.fillna(0)

    return df


# Columns used by ETA predictor (order matters for trained model)
ETA_FEATURE_COLUMNS = [
    "hour", "day_of_week", "is_weekend", "month", "time_bucket_encoded",
    "route_avg_duration", "route_avg_distance", "route_trip_count", "route_eta_success",
    "driver_avg_duration", "driver_avg_speed", "driver_eta_success",
    "driver_total_trips", "driver_vehicles_used",
    "vehicle_avg_speed", "vehicle_total_trips", "vehicle_eta_success",
    "time_pattern_avg_duration", "time_pattern_trip_count", "time_pattern_eta_success",
    "trip_km",
    "is_5am_default",
]
