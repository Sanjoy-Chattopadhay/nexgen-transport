"""
Model 8: SLA Prediction (Will this delivery meet ETA?)
- XGBoost binary classifier: predicts probability of on-time delivery
- Input: route, driver, vehicle, time of day, historical patterns
- Output: probability 0-1, prediction yes/no, risk level, contributing factors
- Business value: proactive customer communication, dispatcher alerts
"""

import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, classification_report,
)

logger = logging.getLogger(__name__)


SLA_FEATURE_COLS = [
    "hour", "day_of_week", "is_weekend", "month",
    "route_avg_duration", "route_trip_count", "route_eta_success",
    "route_avg_distance",
    "driver_avg_duration", "driver_avg_speed", "driver_eta_success",
    "driver_total_trips",
    "vehicle_avg_speed", "vehicle_total_trips", "vehicle_eta_success",
    "time_pattern_avg_duration", "time_pattern_trip_count",
    "time_pattern_eta_success",
    "trip_km",
    "driver_route_trips",       # how many times this driver has done this route
    "driver_route_eta_success", # driver's ETA success on this specific route
]


def fetch_sla_training_data(conn, limit: int = 500000) -> pd.DataFrame:
    """Fetch trip data with all features needed for SLA prediction."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.id AS trip_id,
                t.eta_met,
                t.trip_km,
                t.trip_duration_minutes,
                t.eta_delay_minutes,
                t.avg_speed_kmph,
                t.trip_start,
                t.driver_id,
                t.vehicle_id,
                lo.name AS origin_name,
                ld.name AS destination_name,
                -- Route features
                rs.avg_duration_min AS route_avg_duration,
                rs.trip_count AS route_trip_count,
                rs.eta_success_rate AS route_eta_success,
                rs.avg_distance_km AS route_avg_distance,
                -- Driver features
                ds.avg_duration_min AS driver_avg_duration,
                ds.avg_speed_kmph AS driver_avg_speed,
                ds.eta_success_rate AS driver_eta_success,
                ds.total_trips AS driver_total_trips,
                -- Vehicle features
                vs.avg_speed_kmph AS vehicle_avg_speed,
                vs.total_trips AS vehicle_total_trips,
                vs.eta_success_rate AS vehicle_eta_success
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN route_summary rs ON lo.name = rs.origin AND ld.name = rs.destination
            LEFT JOIN driver_summary ds ON t.driver_id = ds.driver_id
            LEFT JOIN vehicle_summary vs ON t.vehicle_id = vs.vehicle_id
            WHERE t.eta_met IS NOT NULL
              AND t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
            ORDER BY t.trip_start DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_driver_route_stats(conn) -> pd.DataFrame:
    """Get per-driver per-route stats for the driver_route features."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.driver_id,
                lo.name AS origin,
                ld.name AS destination,
                COUNT(*) AS driver_route_trips,
                ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2)
                    AS driver_route_eta_success
            FROM trips t
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE t.eta_met IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.eta_data_status = 'available'
            GROUP BY t.driver_id, lo.name, ld.name
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def engineer_sla_features(df: pd.DataFrame, driver_route_df: pd.DataFrame) -> pd.DataFrame:
    """Build SLA prediction features."""
    features = pd.DataFrame(index=df.index)

    # Temporal
    trip_start = pd.to_datetime(df["trip_start"])
    features["hour"] = trip_start.dt.hour
    features["day_of_week"] = trip_start.dt.dayofweek
    features["is_weekend"] = (features["day_of_week"] >= 5).astype(int)
    features["month"] = trip_start.dt.month

    # Route
    features["route_avg_duration"] = df["route_avg_duration"].astype(float).fillna(0)
    features["route_trip_count"] = df["route_trip_count"].astype(float).fillna(0)
    features["route_eta_success"] = df["route_eta_success"].astype(float).fillna(50)
    features["route_avg_distance"] = df["route_avg_distance"].astype(float).fillna(0)

    # Driver
    features["driver_avg_duration"] = df["driver_avg_duration"].astype(float).fillna(0)
    features["driver_avg_speed"] = df["driver_avg_speed"].astype(float).fillna(0)
    features["driver_eta_success"] = df["driver_eta_success"].astype(float).fillna(50)
    features["driver_total_trips"] = df["driver_total_trips"].astype(float).fillna(0)

    # Vehicle
    features["vehicle_avg_speed"] = df["vehicle_avg_speed"].astype(float).fillna(0)
    features["vehicle_total_trips"] = df["vehicle_total_trips"].astype(float).fillna(0)
    features["vehicle_eta_success"] = df["vehicle_eta_success"].astype(float).fillna(50)

    # Time patterns — use route_eta_success as proxy if no specific pattern
    features["time_pattern_avg_duration"] = features["route_avg_duration"]
    features["time_pattern_trip_count"] = features["route_trip_count"]
    features["time_pattern_eta_success"] = features["route_eta_success"]

    # Trip distance
    features["trip_km"] = df["trip_km"].astype(float).fillna(0)

    # Driver-route specific stats (key differentiator)
    if not driver_route_df.empty:
        dr_lookup = driver_route_df.set_index(["driver_id", "origin", "destination"])
        driver_route_trips = []
        driver_route_eta = []
        for _, row in df.iterrows():
            key = (row["driver_id"], row.get("origin_name", ""), row.get("destination_name", ""))
            if key in dr_lookup.index:
                dr = dr_lookup.loc[key]
                if isinstance(dr, pd.DataFrame):
                    dr = dr.iloc[0]
                driver_route_trips.append(float(dr.get("driver_route_trips", 0)))
                driver_route_eta.append(float(dr.get("driver_route_eta_success", 50)))
            else:
                driver_route_trips.append(0)
                driver_route_eta.append(50)
        features["driver_route_trips"] = driver_route_trips
        features["driver_route_eta_success"] = driver_route_eta
    else:
        features["driver_route_trips"] = 0
        features["driver_route_eta_success"] = 50

    return features.fillna(0)


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: SLA Predictor (On-Time Delivery)")
    logger.info("=" * 50)

    df = fetch_sla_training_data(conn)
    if df.empty or len(df) < 100:
        logger.error("Not enough data for SLA prediction")
        return {"error": "Not enough data (need 100+ trips with eta_met)"}

    driver_route_df = fetch_driver_route_stats(conn)

    logger.info(f"SLA data: {len(df):,} rows")
    logger.info(f"Class balance: {df['eta_met'].value_counts().to_dict()}")

    features = engineer_sla_features(df, driver_route_df)
    y = df["eta_met"].astype(int)

    # Remove outliers
    valid_mask = (features["route_avg_duration"] > 0) | (features["trip_km"] > 0)
    features = features[valid_mask]
    y = y[valid_mask]

    X = features[SLA_FEATURE_COLS].astype(float)

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y,
    )

    logger.info(f"Train: {len(X_train):,}, Test: {len(X_test):,}")

    # Train XGBoost
    try:
        import xgboost as xgb
        model = xgb.XGBClassifier(
            n_estimators=300,
            max_depth=8,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            n_jobs=-1,
        )
    except ImportError:
        from sklearn.ensemble import GradientBoostingClassifier
        model = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.1,
            random_state=42,
        )

    model.fit(X_train, y_train)

    # Evaluate
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    accuracy = accuracy_score(y_test, y_pred)
    precision = precision_score(y_test, y_pred, zero_division=0)
    recall = recall_score(y_test, y_pred, zero_division=0)
    f1 = f1_score(y_test, y_pred, zero_division=0)
    auc = roc_auc_score(y_test, y_proba)

    logger.info(f"Accuracy: {accuracy:.4f}")
    logger.info(f"Precision: {precision:.4f}")
    logger.info(f"Recall: {recall:.4f}")
    logger.info(f"F1: {f1:.4f}")
    logger.info(f"AUC-ROC: {auc:.4f}")

    # Feature importance
    if hasattr(model, "feature_importances_"):
        importance = dict(zip(SLA_FEATURE_COLS, model.feature_importances_.tolist()))
        top_features = sorted(importance.items(), key=lambda x: -x[1])[:10]
        logger.info(f"Top features: {top_features}")

    # Save model
    model_path = str(models_dir / "sla_predictor.joblib")
    joblib.dump({
        "model": model,
        "feature_columns": SLA_FEATURE_COLS,
        "threshold": 0.5,
    }, model_path)

    # Register in DB
    metrics = {
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "auc_roc": round(auc, 4),
        "training_samples": len(X_train),
        "test_samples": len(X_test),
        "class_balance": {
            "on_time": int((y == 1).sum()),
            "delayed": int((y == 0).sum()),
        },
    }

    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'sla_predictor'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'sla_predictor'")
        version = cur.fetchone()["max_v"] + 1

        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('sla_predictor', %s, 'XGBoost_classifier', 'eta_met', %s, %s, %s, %s, 1)
        """, (
            version,
            json.dumps(metrics),
            json.dumps(SLA_FEATURE_COLS),
            model_path,
            len(X_train),
        ))
    conn.commit()

    logger.info(f"SLA predictor saved to {model_path}")
    return metrics


def predict_sla(artifact: dict, conn, trip_data: dict) -> dict:
    """
    Predict whether a trip will meet its ETA.
    Input: origin, destination, driver_id, vehicle_id, trip_km, trip_start
    Output: probability, prediction, risk_level, contributing_factors
    """
    from nexgen.services.ml.app.features.feature_engineering import (
        extract_temporal_features,
        get_route_features,
        get_driver_features,
        get_vehicle_features,
    )

    model = artifact["model"]
    feature_cols = artifact["feature_columns"]

    # Build features
    temporal = extract_temporal_features(trip_data.get("trip_start"))
    route = get_route_features(conn, trip_data.get("origin", ""), trip_data.get("destination", ""))
    driver = get_driver_features(conn, trip_data.get("driver_id", 0))
    vehicle = get_vehicle_features(conn, trip_data.get("vehicle_id", 0))

    # Driver-route specific stats
    driver_route_trips = 0
    driver_route_eta = 50.0
    if trip_data.get("driver_id"):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS cnt,
                       ROUND(SUM(CASE WHEN eta_met=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(eta_met),0)*100, 2) AS eta_rate
                FROM trips t
                JOIN locations lo ON t.origin_id = lo.id
                JOIN locations ld ON t.destination_id = ld.id
                WHERE t.driver_id = %s AND lo.name = %s AND ld.name = %s
                  AND t.eta_data_status = 'available' AND t.trip_duration_minutes > 0
            """, (trip_data["driver_id"], trip_data.get("origin", ""), trip_data.get("destination", "")))
            row = cur.fetchone()
            if row and row["cnt"] > 0:
                driver_route_trips = row["cnt"]
                driver_route_eta = float(row["eta_rate"]) if row["eta_rate"] else 50.0

    # Assemble feature vector
    features = {
        "hour": temporal.get("hour", 0),
        "day_of_week": temporal.get("day_of_week", 0),
        "is_weekend": temporal.get("is_weekend", 0),
        "month": temporal.get("month", 1),
        "route_avg_duration": route.get("route_avg_duration") or 0,
        "route_trip_count": route.get("route_trip_count", 0),
        "route_eta_success": route.get("route_eta_success") or 50,
        "route_avg_distance": route.get("route_avg_distance") or 0,
        "driver_avg_duration": driver.get("driver_avg_duration") or 0,
        "driver_avg_speed": driver.get("driver_avg_speed") or 0,
        "driver_eta_success": driver.get("driver_eta_success") or 50,
        "driver_total_trips": driver.get("driver_total_trips", 0),
        "vehicle_avg_speed": vehicle.get("vehicle_avg_speed") or 0,
        "vehicle_total_trips": vehicle.get("vehicle_total_trips", 0),
        "vehicle_eta_success": vehicle.get("vehicle_eta_success") or 50,
        "time_pattern_avg_duration": route.get("route_avg_duration") or 0,
        "time_pattern_trip_count": route.get("route_trip_count", 0),
        "time_pattern_eta_success": route.get("route_eta_success") or 50,
        "trip_km": trip_data.get("trip_km") or 0,
        "driver_route_trips": driver_route_trips,
        "driver_route_eta_success": driver_route_eta,
    }

    feature_df = pd.DataFrame([features])[feature_cols].astype(float).fillna(0)

    # Predict
    probability = float(model.predict_proba(feature_df)[0][1])
    prediction = probability >= 0.5

    # Risk level
    if probability >= 0.85:
        risk_level = "low"
        risk_message = "High confidence of on-time delivery"
    elif probability >= 0.65:
        risk_level = "medium"
        risk_message = "Moderate risk of delay — monitor this trip"
    elif probability >= 0.45:
        risk_level = "high"
        risk_message = "Significant delay risk — consider alternatives"
    else:
        risk_level = "critical"
        risk_message = "Very likely to miss ETA — intervene now"

    # Contributing factors
    factors = []
    if driver_route_trips == 0:
        factors.append("Driver has no experience on this route")
    elif driver_route_eta < 50:
        factors.append(f"Driver's route ETA success is low ({driver_route_eta:.0f}%)")
    route_eta = route.get("route_eta_success") or 0
    if route_eta < 60:
        factors.append(f"This route has low overall ETA success ({route_eta:.0f}%)")
    driver_eta = driver.get("driver_eta_success") or 0
    if driver_eta < 60:
        factors.append(f"Driver overall ETA success is low ({driver_eta:.0f}%)")
    if temporal.get("hour", 12) >= 22 or temporal.get("hour", 12) <= 5:
        factors.append("Night-time trip — historically lower on-time rates")

    return {
        "on_time_probability": round(probability, 4),
        "prediction": "on_time" if prediction else "likely_delayed",
        "risk_level": risk_level,
        "risk_message": risk_message,
        "contributing_factors": factors,
        "driver_route_experience": {
            "trips_on_route": driver_route_trips,
            "route_eta_success": driver_route_eta,
        },
    }
