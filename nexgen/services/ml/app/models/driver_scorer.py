"""
Model 3: Driver Risk/Performance Scoring
- Hybrid approach: rule-based weighted scoring + ML-based classification
- Components: ETA compliance, speed safety, consistency, experience, efficiency
- Output: composite score 0-100, risk_level (low/medium/high), component breakdown
"""

import logging
import json
from pathlib import Path

import pandas as pd
import numpy as np
import joblib

from sklearn.preprocessing import MinMaxScaler

logger = logging.getLogger(__name__)


def fetch_driver_data(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                ds.driver_id,
                ds.driver_name,
                ds.total_trips,
                ds.eta_met_count,
                ds.eta_success_rate,
                ds.avg_duration_min,
                ds.max_duration_min,
                ds.min_duration_min,
                ds.avg_speed_kmph,
                ds.vehicles_used,
                ds.total_distance_km,
                ds.avg_distance_km,
                ds.avg_eta_delay_min
            FROM driver_summary ds
            WHERE ds.total_trips >= 3
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_driver_trip_stats(conn) -> pd.DataFrame:
    """Get per-driver trip variability stats."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                driver_id,
                ROUND(STDDEV(trip_duration_minutes), 2) AS duration_stddev,
                ROUND(STDDEV(avg_speed_kmph), 2) AS speed_stddev,
                COUNT(CASE WHEN eta_delay_minutes > 120 THEN 1 END) AS severe_delays,
                COUNT(CASE WHEN HOUR(trip_start) >= 22 OR HOUR(trip_start) <= 5 THEN 1 END) AS night_trips,
                COUNT(*) AS total
            FROM trips
            WHERE trip_duration_minutes IS NOT NULL
              AND trip_duration_minutes > 0
              AND driver_id IS NOT NULL
              AND eta_data_status = 'available'
            GROUP BY driver_id
            HAVING COUNT(*) >= 3
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Driver Risk Scorer")
    logger.info("=" * 50)

    df = fetch_driver_data(conn)
    if df.empty:
        logger.error("No driver data available")
        return {"error": "No driver data"}

    trip_stats = fetch_driver_trip_stats(conn)
    if not trip_stats.empty:
        df = df.merge(trip_stats, on="driver_id", how="left")

    logger.info(f"Scoring {len(df):,} drivers")

    # Convert Decimal types to float
    numeric_cols = df.select_dtypes(include=["object"]).columns
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col], errors="ignore")
        except Exception:
            pass

    # ============================================
    # SCORING COMPONENTS (each normalized 0-100)
    # ============================================

    # 1. ETA Compliance Score (35% weight)
    # Bayesian-smoothed: a driver with 4/4 = 100% should NOT outrank
    # a driver with 375/500 = 75%. We shrink small-sample rates
    # toward the fleet average so experience earns trust.
    # eta_success_rate is NULL when a driver has trips but none with a known
    # outcome (the rate divides by COUNT(eta_met)). That is unknown, not 0% —
    # coercing it to 0 sinks the driver at 35% weight for missing data. Fall
    # back to the fleet prior instead, which is what the smoothing below does
    # for a driver carrying no evidence. `.mean()` already skips NULLs; the
    # explicit isna check is needed because `NaN or 55.0` returns NaN.
    fleet_mean = df["eta_success_rate"].mean()
    global_eta_avg = 55.0 if pd.isna(fleet_mean) else float(fleet_mean)
    confidence_trips = 15.0  # how many trips before we fully trust raw rate
    raw_eta = df["eta_success_rate"].astype(float).fillna(global_eta_avg)
    n_trips = df["total_trips"].astype(float)
    df["eta_score"] = (
        (n_trips * raw_eta + confidence_trips * global_eta_avg)
        / (n_trips + confidence_trips)
    ).clip(0, 100)

    # 2. Speed Safety Score (20% weight)
    # Penalize average speeds that are too high (unsafe) or too low (inefficient)
    avg_speed = df["avg_speed_kmph"].fillna(0).astype(float)
    optimal_speed = 45  # optimal avg speed for loaded trucks
    max_speed = 70      # anything above this is risky
    df["speed_score"] = np.where(
        avg_speed <= optimal_speed,
        (avg_speed / optimal_speed) * 100,
        100 - ((avg_speed - optimal_speed) / (max_speed - optimal_speed)) * 100,
    )
    df["speed_score"] = df["speed_score"].clip(0, 100)

    # 3. Consistency Score (20% weight)
    # Lower variability in duration = more consistent = better
    if "duration_stddev" in df.columns:
        max_std = df["duration_stddev"].quantile(0.95) or 1
        df["consistency_score"] = (1 - df["duration_stddev"].fillna(max_std) / max_std) * 100
    else:
        max_delay = df["avg_eta_delay_min"].fillna(0).astype(float).max() or 1
        df["consistency_score"] = (1 - df["avg_eta_delay_min"].fillna(0).astype(float) / max_delay) * 100
    df["consistency_score"] = df["consistency_score"].clip(0, 100)

    # 4. Experience Score (10% weight)
    # More trips = more experienced
    max_trips = df["total_trips"].max() or 1
    df["experience_score"] = np.log1p(df["total_trips"]) / np.log1p(max_trips) * 100

    # 5. Efficiency Score (10% weight)
    # Distance per trip compared to route averages
    dist_per_trip = df["total_distance_km"].fillna(0).astype(float) / df["total_trips"].clip(lower=1)
    global_avg_dist = dist_per_trip.median() or 1
    deviation = abs(dist_per_trip - global_avg_dist) / global_avg_dist
    df["efficiency_score"] = (1 - deviation.clip(0, 1)) * 100

    # 6. BONUS: Safety penalty for severe delays and night driving
    if "severe_delays" in df.columns:
        severe_ratio = df["severe_delays"].fillna(0) / df["total_trips"].clip(lower=1)
        df["safety_penalty"] = severe_ratio.clip(0, 0.5) * 20  # max -10 points
    else:
        df["safety_penalty"] = 0

    # ============================================
    # COMPOSITE SCORE
    # ============================================

    df["composite_score"] = (
        df["eta_score"] * 0.35           # Bayesian-smoothed ETA compliance
        + df["speed_score"] * 0.15       # Speed safety
        + df["consistency_score"] * 0.20 # Duration consistency
        + df["experience_score"] * 0.20  # Experience (boosted from 10%)
        + df["efficiency_score"] * 0.10  # Route efficiency
        - df["safety_penalty"]
    ).clip(0, 100).round(2)

    # Risk levels
    df["risk_level"] = pd.cut(
        df["composite_score"],
        bins=[-1, 35, 55, 75, 101],
        labels=["critical", "high", "medium", "low"],
    )

    # ============================================
    # SAVE SCORES TO DATABASE
    # ============================================

    # First register a model entry for driver_scorer
    with conn.cursor() as cur:
        # Deactivate old versions
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'driver_scorer'")

        # Get next version safely
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'driver_scorer'")
        max_version = cur.fetchone()["max_v"]
        version = max_version + 1

        metrics_json = json.dumps({"drivers_scored": len(df)})
        features_json = json.dumps(["eta_compliance", "speed_safety", "consistency", "experience", "efficiency"])

        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, training_data_count, is_active)
            VALUES ('driver_scorer', %s, 'weighted_scoring', 'driver_risk_score', %s, %s, %s, 1)
        """, (version, metrics_json, features_json, len(df)))
        model_id = cur.lastrowid

        if not model_id:
            cur.execute(
                "SELECT id FROM ml_models WHERE model_name = 'driver_scorer' AND version = %s",
                (version,),
            )
            model_id = cur.fetchone()["id"]

        # Insert individual scores into predictions
        for _, row in df.iterrows():
            scores = {
                "eta_score": round(float(row["eta_score"]), 2),
                "speed_score": round(float(row["speed_score"]), 2),
                "consistency_score": round(float(row["consistency_score"]), 2),
                "experience_score": round(float(row["experience_score"]), 2),
                "efficiency_score": round(float(row["efficiency_score"]), 2),
                "risk_level": str(row["risk_level"]),
                "safety_penalty": round(float(row["safety_penalty"]), 2),
            }
            cur.execute("""
                INSERT INTO predictions (model_id, driver_id, input_features, predicted_value,
                                         prediction_type, confidence_score)
                VALUES (%s, %s, %s, %s, 'driver_score', 1.0)
            """, (model_id, int(row["driver_id"]), json.dumps(scores), float(row["composite_score"])))

    conn.commit()

    # Stats
    risk_dist = df["risk_level"].value_counts().to_dict()
    avg_score = df["composite_score"].mean()

    logger.info(f"Scored {len(df):,} drivers. Avg score: {avg_score:.1f}")
    logger.info(f"Risk distribution: {risk_dist}")
    logger.info(f"Top 5 drivers: {df.nlargest(5, 'composite_score')[['driver_name', 'composite_score', 'risk_level']].to_string()}")

    # Save scoring parameters for serving
    model_path = str(models_dir / "driver_scorer.joblib")
    joblib.dump({
        "weights": {
            "eta_compliance_bayesian": 0.35,
            "speed_safety": 0.15,
            "consistency": 0.20,
            "experience": 0.20,
            "efficiency": 0.10,
        },
        "thresholds": {
            "optimal_speed": optimal_speed,
            "max_speed": max_speed,
            "max_trips": int(max_trips),
        },
    }, model_path)

    return {
        "drivers_scored": len(df),
        "avg_score": round(avg_score, 2),
        "risk_distribution": {str(k): int(v) for k, v in risk_dist.items()},
        "model_path": model_path,
    }
