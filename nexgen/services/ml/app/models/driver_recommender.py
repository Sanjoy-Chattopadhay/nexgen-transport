"""
Model 7: Driver Recommender (v2 - Route Experience Priority)
- Given a route (origin + destination), ranks drivers based on
  their historical performance on THAT ROUTE first.
- Only falls back to similar-route drivers if not enough experienced ones.
- Never recommends random high-scoring drivers with zero route relevance.
- Output: two sections — "experienced_on_route" and "similar_route_experience"
"""

import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

logger = logging.getLogger(__name__)


def fetch_driver_route_performance(conn) -> pd.DataFrame:
    """Get per-driver per-route performance stats."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.driver_id,
                d.name AS driver_name,
                d.mobile1 AS driver_mobile,
                lo.name AS origin,
                ld.name AS destination,
                COUNT(*) AS route_trips,
                ROUND(AVG(t.trip_duration_minutes), 2) AS avg_duration_min,
                ROUND(AVG(t.avg_speed_kmph), 2) AS avg_speed_kmph,
                ROUND(AVG(t.trip_km), 2) AS avg_distance_km,
                ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2) AS eta_success_rate,
                ROUND(AVG(t.eta_delay_minutes), 2) AS avg_delay_min,
                ROUND(STDDEV(t.trip_duration_minutes), 2) AS duration_stddev
            FROM trips t
            JOIN drivers d ON t.driver_id = d.id
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.eta_data_status = 'available'
              AND t.driver_id IS NOT NULL
            GROUP BY t.driver_id, d.name, d.mobile1, lo.name, ld.name
            HAVING COUNT(*) >= 2
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_driver_overall_stats(conn) -> pd.DataFrame:
    """Get overall driver performance from driver_summary."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                driver_id,
                driver_name,
                total_trips,
                eta_success_rate,
                avg_speed_kmph,
                avg_duration_min,
                avg_distance_km,
                avg_eta_delay_min,
                vehicles_used
            FROM driver_summary
            WHERE total_trips >= 2
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Driver Recommender v2 (Route-Priority)")
    logger.info("=" * 50)

    route_perf = fetch_driver_route_performance(conn)
    overall_stats = fetch_driver_overall_stats(conn)

    if route_perf.empty:
        logger.error("No driver-route performance data available")
        return {"error": "No driver-route performance data"}

    if overall_stats.empty:
        logger.error("No driver summary data available")
        return {"error": "No driver summary data"}

    # Convert Decimal types
    for col in route_perf.select_dtypes(include=["object"]).columns:
        try:
            route_perf[col] = pd.to_numeric(route_perf[col], errors="ignore")
        except Exception:
            pass

    for col in overall_stats.select_dtypes(include=["object"]).columns:
        try:
            overall_stats[col] = pd.to_numeric(overall_stats[col], errors="ignore")
        except Exception:
            pass

    unique_routes = route_perf.groupby(["origin", "destination"]).size().reset_index(name="driver_count")
    unique_drivers = route_perf["driver_id"].nunique()

    logger.info(f"Driver-route data: {len(route_perf):,} records")
    logger.info(f"Unique routes with driver data: {len(unique_routes):,}")
    logger.info(f"Unique drivers: {unique_drivers:,}")
    logger.info(f"Overall driver stats: {len(overall_stats):,} drivers")

    # Compute global averages for normalization
    global_avg_speed = float(overall_stats["avg_speed_kmph"].mean()) or 40.0
    global_avg_eta_success = float(overall_stats["eta_success_rate"].mean()) or 50.0

    # Save model artifact
    model_path = str(models_dir / "driver_recommender.joblib")
    joblib.dump({
        "route_performance": route_perf.to_dict("records"),
        "overall_stats": overall_stats.to_dict("records"),
        "global_avg_speed": global_avg_speed,
        "global_avg_eta_success": global_avg_eta_success,
        "generated_at": datetime.now().isoformat(),
    }, model_path)

    # Register model in DB
    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'driver_recommender'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'driver_recommender'")
        version = cur.fetchone()["max_v"] + 1

        metrics_json = json.dumps({
            "unique_routes": len(unique_routes),
            "unique_drivers": int(unique_drivers),
            "driver_route_records": len(route_perf),
            "global_avg_speed": round(global_avg_speed, 2),
            "global_avg_eta_success": round(global_avg_eta_success, 2),
        })

        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('driver_recommender', %s, 'ranking_scoring', 'driver_rank', %s, %s, %s, %s, 1)
        """, (
            version,
            metrics_json,
            json.dumps(["eta_success_rate", "avg_speed_kmph", "consistency", "route_experience"]),
            model_path,
            len(route_perf),
        ))
    conn.commit()

    logger.info(f"Driver recommender v2 saved to {model_path}")

    return {
        "unique_routes": len(unique_routes),
        "unique_drivers": int(unique_drivers),
        "driver_route_records": len(route_perf),
        "model_path": model_path,
    }


def _bayesian_eta(eta_rate: float, n_trips: float, global_avg: float = 55.0, confidence_trips: float = 10.0) -> float:
    """Bayesian smoothing for ETA success rate.
    With few trips, pulls toward the global average.
    With many trips, trusts the observed rate.
    confidence_trips = how many trips before we fully trust observed rate.
    """
    return (n_trips * eta_rate + confidence_trips * global_avg) / (n_trips + confidence_trips)


def _score_route_driver(route_data: dict, driver_overall: dict = None, global_avg_eta: float = 55.0) -> dict:
    """Score a driver who HAS experience on the exact route.
    Weights: route_exp=25%, eta=30%, speed=15%, consistency=10%, overall_exp=20%
    Key fix: Bayesian smoothing on ETA so 1-trip drivers don't rank above veterans.
    """
    route_trips = float(route_data.get("route_trips", 0))
    # `.get(key, 0)` only defends against a MISSING key. eta_success_rate is
    # now NULL/NaN when no trip on this route has a known outcome, and
    # float(nan) succeeds — so NaN would propagate into total_score and the
    # driver would sort unpredictably. Treat unknown as the neutral 50.
    _eta = route_data.get("eta_success_rate")
    eta_rate = 50.0 if _eta is None or _eta != _eta else float(_eta)
    speed = float(route_data.get("avg_speed_kmph", 0))
    stddev = float(route_data.get("duration_stddev", 0) or 0)
    avg_dur = float(route_data.get("avg_duration_min", 1) or 1)

    total_trips = float(driver_overall.get("total_trips", 0)) if driver_overall else route_trips

    # 1. Route Experience (25%) — logarithmic scale, needs 20+ trips for high score
    route_exp_score = min(100, np.log1p(route_trips) / np.log1p(30) * 100)

    # 2. ETA Compliance with Bayesian smoothing (30%)
    # A driver with 1 trip @ 100% ETA → smoothed to ~60% (pulled toward global avg)
    # A driver with 50 trips @ 80% ETA → smoothed to ~79% (trusts observed)
    smoothed_eta = _bayesian_eta(eta_rate, route_trips, global_avg_eta, confidence_trips=10.0)
    eta_score = min(100, smoothed_eta)

    # 3. Speed Efficiency (15%) — optimal 35-55 km/h for trucks
    if 35 <= speed <= 55:
        speed_score = 100
    elif speed > 0:
        speed_score = max(0, 100 - abs(speed - 45) * 3)
    else:
        speed_score = 0

    # 4. Consistency on this route (10%) — penalize unknown (1 trip = no stddev info)
    if route_trips <= 1:
        consistency_score = 50  # Unknown consistency, neutral score
    elif stddev == 0:
        consistency_score = 100
    else:
        cv = stddev / max(avg_dur, 1)
        consistency_score = max(0, 100 - cv * 100)

    # 5. Overall Experience (20%) — logarithmic, experienced drivers rewarded
    exp_score = min(100, np.log1p(total_trips) / np.log1p(200) * 100)

    composite = (
        route_exp_score * 0.25 +
        eta_score * 0.30 +
        speed_score * 0.15 +
        consistency_score * 0.10 +
        exp_score * 0.20
    )

    return {
        "composite_score": round(float(composite), 2),
        "route_experience_score": round(float(route_exp_score), 2),
        "eta_compliance_score": round(float(eta_score), 2),
        "eta_raw_rate": round(float(eta_rate), 2),
        "speed_efficiency_score": round(float(speed_score), 2),
        "consistency_score": round(float(consistency_score), 2),
        "overall_experience_score": round(float(exp_score), 2),
    }


def _score_similar_route_driver(similar_records: list, driver_overall: dict, global_avg_eta: float = 55.0) -> dict:
    """Score a driver who has experience on similar routes (same origin OR destination).
    Capped lower than exact-route drivers so they never outrank experienced ones unfairly.
    """
    total_similar_trips = sum(float(r.get("route_trips", 0)) for r in similar_records)
    avg_eta = np.mean([float(r.get("eta_success_rate", 0)) for r in similar_records])
    avg_speed = np.mean([float(r.get("avg_speed_kmph", 0)) for r in similar_records])

    total_trips = float(driver_overall.get("total_trips", 0))

    # Route experience capped at 80, logarithmic
    route_exp_score = min(80, np.log1p(total_similar_trips) / np.log1p(30) * 80)
    # Bayesian smoothed ETA
    smoothed_eta = _bayesian_eta(avg_eta, total_similar_trips, global_avg_eta, confidence_trips=15.0)
    eta_score = min(100, smoothed_eta)
    if 35 <= avg_speed <= 55:
        speed_score = 100
    elif avg_speed > 0:
        speed_score = max(0, 100 - abs(avg_speed - 45) * 3)
    else:
        speed_score = 0
    consistency_score = 50  # Neutral — can't measure on a route they haven't done
    exp_score = min(100, np.log1p(total_trips) / np.log1p(200) * 100)

    composite = (
        route_exp_score * 0.20 +
        eta_score * 0.30 +
        speed_score * 0.15 +
        consistency_score * 0.10 +
        exp_score * 0.25
    )

    return {
        "composite_score": round(float(composite), 2),
        "route_experience_score": round(float(route_exp_score), 2),
        "eta_compliance_score": round(float(eta_score), 2),
        "eta_raw_rate": round(float(avg_eta), 2),
        "speed_efficiency_score": round(float(speed_score), 2),
        "consistency_score": round(float(consistency_score), 2),
        "overall_experience_score": round(float(exp_score), 2),
    }


def recommend_drivers(artifact: dict, origin: str, destination: str, top_n: int = 10) -> dict:
    """
    Recommend best drivers for a given route.
    v2: Prioritizes drivers with ACTUAL route experience.
    Falls back to similar-route (same origin OR same destination) drivers.
    Never recommends random high-scoring drivers with zero route relevance.
    """
    route_perf = pd.DataFrame(artifact["route_performance"])
    overall_stats = pd.DataFrame(artifact["overall_stats"])

    if route_perf.empty and overall_stats.empty:
        return {"error": "No driver data available"}

    # Convert Decimal types
    for col in route_perf.columns:
        route_perf[col] = pd.to_numeric(route_perf[col], errors="ignore")
    for col in overall_stats.columns:
        overall_stats[col] = pd.to_numeric(overall_stats[col], errors="ignore")

    overall_dict = {}
    if not overall_stats.empty:
        overall_dict = overall_stats.set_index("driver_id").to_dict("index")

    global_avg_eta = float(artifact.get("global_avg_eta_success", 55.0))

    # ── Section 1: Drivers with EXACT route experience ──────────────────
    route_drivers = route_perf[
        (route_perf["origin"] == origin) & (route_perf["destination"] == destination)
    ]

    experienced_drivers = []
    experienced_ids = set()

    if not route_drivers.empty:
        for _, rd in route_drivers.iterrows():
            did = int(rd["driver_id"])
            experienced_ids.add(did)
            driver_overall = overall_dict.get(did, {})
            scores = _score_route_driver(rd.to_dict(), driver_overall, global_avg_eta)

            experienced_drivers.append({
                "driver_id": did,
                "driver_name": str(rd.get("driver_name", "Unknown")),
                "category": "experienced",
                **scores,
                "has_route_experience": True,
                "route_trips": int(rd["route_trips"]),
                "avg_speed_kmph": round(float(rd.get("avg_speed_kmph", 0)), 2),
                "eta_success_rate": round(float(rd.get("eta_success_rate", 0)), 2),
                "avg_duration_min": round(float(rd.get("avg_duration_min", 0)), 2),
                "total_trips": int(driver_overall.get("total_trips", rd.get("route_trips", 0))),
            })

    experienced_drivers.sort(key=lambda x: x["composite_score"], reverse=True)

    # ── Section 2: Drivers with SIMILAR route experience ────────────────
    # Same origin or same destination — they know part of the corridor
    similar_drivers = []

    if len(experienced_drivers) < top_n:
        same_origin = route_perf[
            (route_perf["origin"] == origin) & (route_perf["destination"] != destination)
        ]
        same_dest = route_perf[
            (route_perf["destination"] == destination) & (route_perf["origin"] != origin)
        ]
        similar_route_data = pd.concat([same_origin, same_dest])

        if not similar_route_data.empty:
            for did, group in similar_route_data.groupby("driver_id"):
                did = int(did)
                if did in experienced_ids:
                    continue

                driver_overall = overall_dict.get(did, {})
                if not driver_overall:
                    continue

                records = group.to_dict("records")
                scores = _score_similar_route_driver(records, driver_overall, global_avg_eta)

                similar_routes_list = [
                    f"{r['origin']} -> {r['destination']} ({int(r['route_trips'])} trips)"
                    for r in sorted(records, key=lambda x: -float(x.get("route_trips", 0)))[:3]
                ]

                similar_drivers.append({
                    "driver_id": did,
                    "driver_name": str(driver_overall.get("driver_name", group.iloc[0].get("driver_name", "Unknown"))),
                    "category": "similar_route",
                    **scores,
                    "has_route_experience": False,
                    "similar_routes": similar_routes_list,
                    "similar_route_trips": int(group["route_trips"].sum()),
                    "route_trips": 0,
                    "avg_speed_kmph": round(float(driver_overall.get("avg_speed_kmph", 0)), 2),
                    "eta_success_rate": round(float(driver_overall.get("eta_success_rate", 0)), 2),
                    "total_trips": int(driver_overall.get("total_trips", 0)),
                })

    similar_drivers.sort(key=lambda x: x["composite_score"], reverse=True)

    # ── Build final combined ranking ────────────────────────────────────
    # Experienced first, then similar-route, up to top_n
    recommended = experienced_drivers[:top_n]
    remaining = top_n - len(recommended)
    if remaining > 0:
        recommended.extend(similar_drivers[:remaining])

    for i, d in enumerate(recommended):
        d["rank"] = i + 1

    return {
        "origin": origin,
        "destination": destination,
        "total_candidates": len(experienced_drivers) + len(similar_drivers),
        "drivers_with_exact_route_exp": len(experienced_drivers),
        "drivers_with_similar_route_exp": len(similar_drivers),
        "recommended_drivers": recommended,
        "experienced_on_route": experienced_drivers[:top_n],
        "similar_route_experience": similar_drivers[:min(5, top_n)],
    }
