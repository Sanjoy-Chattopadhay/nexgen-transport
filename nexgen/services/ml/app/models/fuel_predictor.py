"""
Model 6: Fuel Consumption Predictor
- Predicts estimated fuel consumption for a trip based on:
  distance, vehicle type, route characteristics, driver behavior, load weight
- Primary: Gradient Boosting Regressor
- Secondary: Random Forest for comparison
- Features: distance, vehicle stats, driver stats, route elevation proxy, load, weather
- Output: predicted fuel in liters, fuel efficiency (km/l), cost estimate
"""

import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


FUEL_FEATURE_COLUMNS = [
    "trip_km",
    "trip_duration_minutes",
    "avg_speed_kmph",
    "hour",
    "day_of_week",
    "is_weekend",
    "month",
    "load_weight_kg",
    "vehicle_avg_speed",
    "vehicle_total_trips",
    "driver_avg_speed",
    "driver_total_trips",
    "route_avg_distance",
    "route_avg_duration",
    "route_trip_count",
    "is_night_trip",
    "speed_variance_proxy",
]


# ============================================
# DATA FETCHING
# ============================================

def fetch_fuel_data(conn) -> pd.DataFrame:
    """
    Fetch trip data for fuel prediction.
    NOTE: If fuel_consumed column has data, we use it as target (supervised).
    If not, we generate synthetic targets based on physics-informed heuristics
    (distance * base_rate * adjustments) for demonstration purposes.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.id AS trip_id,
                t.trip_km,
                t.trip_duration_minutes,
                t.avg_speed_kmph,
                t.trip_start,
                t.fuel_consumed,
                t.load_weight_kg,
                t.eta_delay_minutes,
                v.asset_type,
                lo.name AS origin_name,
                ld.name AS destination_name,
                ds.avg_speed_kmph AS driver_avg_speed,
                ds.total_trips AS driver_total_trips,
                ds.eta_success_rate AS driver_eta_success,
                vs.avg_speed_kmph AS vehicle_avg_speed,
                vs.total_trips AS vehicle_total_trips,
                rs.avg_distance_km AS route_avg_distance,
                rs.avg_duration_min AS route_avg_duration,
                rs.trip_count AS route_trip_count
            FROM trips t
            LEFT JOIN vehicles v ON t.vehicle_id = v.id
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN driver_summary ds ON t.driver_id = ds.driver_id
            LEFT JOIN vehicle_summary vs ON t.vehicle_id = vs.vehicle_id
            LEFT JOIN route_summary rs ON lo.name = rs.origin AND ld.name = rs.destination
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_start IS NOT NULL
            LIMIT 300000
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================
# FEATURE ENGINEERING
# ============================================

def engineer_fuel_features(df: pd.DataFrame) -> tuple:
    """Engineer features for fuel prediction. Returns (X, y, feature_names)."""

    features = pd.DataFrame(index=df.index)

    # Core trip metrics (trip_km and avg_speed may be NULL in the dataset)
    features["trip_km"] = df["trip_km"].fillna(0).astype(float)
    features["trip_duration_minutes"] = df["trip_duration_minutes"].astype(float)
    features["avg_speed_kmph"] = df["avg_speed_kmph"].fillna(0).astype(float)

    # Temporal features
    trip_start = pd.to_datetime(df["trip_start"])
    features["hour"] = trip_start.dt.hour
    features["day_of_week"] = trip_start.dt.dayofweek
    features["is_weekend"] = (features["day_of_week"] >= 5).astype(int)
    features["month"] = trip_start.dt.month

    # Load weight (0 if unknown)
    features["load_weight_kg"] = df["load_weight_kg"].fillna(0).astype(float)

    # Vehicle features
    features["vehicle_avg_speed"] = df["vehicle_avg_speed"].fillna(0).astype(float)
    features["vehicle_total_trips"] = df["vehicle_total_trips"].fillna(0).astype(float)

    # Driver features
    features["driver_avg_speed"] = df["driver_avg_speed"].fillna(0).astype(float)
    features["driver_total_trips"] = df["driver_total_trips"].fillna(0).astype(float)

    # Route features
    features["route_avg_distance"] = df["route_avg_distance"].fillna(0).astype(float)
    features["route_avg_duration"] = df["route_avg_duration"].fillna(0).astype(float)
    features["route_trip_count"] = df["route_trip_count"].fillna(0).astype(float)

    # Derived features
    features["is_night_trip"] = ((features["hour"] >= 22) | (features["hour"] <= 5)).astype(int)

    # Speed variance proxy: deviation from vehicle average
    features["speed_variance_proxy"] = abs(
        features["avg_speed_kmph"] - features["vehicle_avg_speed"]
    )

    # --- TARGET VARIABLE ---
    # Check if real fuel data exists
    has_fuel_data = df["fuel_consumed"].notna().sum() > 100

    if has_fuel_data:
        logger.info("Using REAL fuel consumption data as target")
        y = df["fuel_consumed"].astype(float)
        valid_mask = y.notna() & (y > 0)
        features = features[valid_mask]
        y = y[valid_mask]
    else:
        logger.info("No real fuel data. Generating physics-informed synthetic target.")
        # Physics-informed fuel model for trucks:
        # If trip_km is available, use distance-based model.
        # If trip_km is 0/NULL, estimate distance from duration (assume avg 40 km/h for trucks).
        base_rate = 0.35  # L/km base

        distance = features["trip_km"].copy()
        # Estimate distance from duration where trip_km is unavailable
        no_km_mask = distance <= 0
        if no_km_mask.any():
            estimated_speed_kmph = 40  # reasonable average truck speed
            distance[no_km_mask] = (
                features.loc[no_km_mask, "trip_duration_minutes"] / 60 * estimated_speed_kmph
            )
        distance = distance.clip(lower=0.1)

        speed = features["avg_speed_kmph"].copy()
        # Where speed is 0 (NULL), estimate from distance/duration
        no_speed_mask = speed <= 0
        if no_speed_mask.any():
            speed[no_speed_mask] = (
                distance[no_speed_mask] / (features.loc[no_speed_mask, "trip_duration_minutes"] / 60).clip(lower=0.01)
            )
        speed = speed.clip(lower=1)

        # Speed efficiency curve (trucks most efficient at 50-60 km/h)
        speed_factor = 1 + 0.005 * (abs(speed - 55)).clip(lower=0)

        # Load factor (heavier = more fuel)
        load_factor = 1 + (features["load_weight_kg"] / 20000).clip(upper=0.3)

        # Night driving slight efficiency gain (less traffic)
        night_factor = 1 - features["is_night_trip"] * 0.03

        # Duration-based stop-start penalty (idle time)
        expected_duration = distance / speed.clip(lower=1) * 60  # minutes at constant speed
        actual_duration = features["trip_duration_minutes"]
        idle_ratio = ((actual_duration - expected_duration) / expected_duration.clip(lower=1)).clip(0, 1)
        idle_factor = 1 + idle_ratio * 0.15

        # Compute synthetic fuel consumption
        y = distance * base_rate * speed_factor * load_factor * night_factor * idle_factor

        # Add realistic noise (5-10%)
        np.random.seed(42)
        noise = np.random.normal(1.0, 0.07, len(y))
        y = (y * noise).clip(lower=0.1)

    # Ensure column order matches FUEL_FEATURE_COLUMNS
    for col in FUEL_FEATURE_COLUMNS:
        if col not in features.columns:
            features[col] = 0

    X = features[FUEL_FEATURE_COLUMNS].fillna(0).astype(float)

    return X, y.reset_index(drop=True), FUEL_FEATURE_COLUMNS


# ============================================
# TRAINING
# ============================================

def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Fuel Consumption Predictor")
    logger.info("=" * 50)

    df = fetch_fuel_data(conn)
    if df.empty:
        logger.error("No data for fuel prediction")
        return {"error": "No data"}

    logger.info(f"Fuel data: {len(df):,} trips")

    X, y, feature_names = engineer_fuel_features(df)
    logger.info(f"Features: {len(X):,} rows, {len(feature_names)} features")
    logger.info(f"Target range: {y.min():.1f} - {y.max():.1f} liters (mean: {y.mean():.1f})")

    # Remove outliers
    q01, q99 = y.quantile(0.01), y.quantile(0.99)
    mask = (y >= q01) & (y <= q99)
    X, y = X[mask].reset_index(drop=True), y[mask].reset_index(drop=True)
    logger.info(f"After outlier removal: {len(X):,} rows")

    # Split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    logger.info(f"Train: {len(X_train):,}, Test: {len(X_test):,}")

    results = {}

    # --- Model A: Gradient Boosting ---
    logger.info("\n--- Gradient Boosting ---")
    gb_model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        min_samples_leaf=10,
        random_state=42,
    )
    gb_model.fit(X_train, y_train)
    gb_pred = gb_model.predict(X_test)
    gb_metrics = _evaluate(y_test, gb_pred, "GradientBoosting")
    results["gradient_boosting"] = gb_metrics

    # Cross-validation
    cv_scores = cross_val_score(gb_model, X_train, y_train, cv=5, scoring="neg_mean_absolute_error")
    gb_metrics["cv_mae"] = round(-cv_scores.mean(), 4)
    logger.info(f"GB CV MAE: {gb_metrics['cv_mae']:.2f}")

    # --- Model B: Random Forest ---
    logger.info("\n--- Random Forest ---")
    rf_model = RandomForestRegressor(
        n_estimators=200,
        max_depth=10,
        min_samples_leaf=10,
        random_state=42,
        n_jobs=-1,
    )
    rf_model.fit(X_train, y_train)
    rf_pred = rf_model.predict(X_test)
    rf_metrics = _evaluate(y_test, rf_pred, "RandomForest")
    results["random_forest"] = rf_metrics

    # Choose best
    if rf_metrics["mae"] < gb_metrics["mae"]:
        best_model = rf_model
        best_name = "random_forest"
        best_metrics = rf_metrics
    else:
        best_model = gb_model
        best_name = "gradient_boosting"
        best_metrics = gb_metrics

    logger.info(f"\nBest model: {best_name}")

    # Feature importance
    importance = dict(zip(feature_names, best_model.feature_importances_.tolist()))
    top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info(f"Top features: {[(f, round(v, 4)) for f, v in top_features]}")

    # Compute fleet-wide fuel stats
    fleet_stats = {
        "avg_fuel_per_trip": round(float(y.mean()), 2),
        "avg_fuel_per_km": round(float(y.mean() / X["trip_km"].mean()), 4) if X["trip_km"].mean() > 0 else 0,
        "total_predicted_fuel": round(float(y.sum()), 0),
    }

    # Save
    model_path = str(models_dir / "fuel_predictor.joblib")
    joblib.dump({
        "model": best_model,
        "model_type": best_name,
        "feature_columns": feature_names,
        "metrics": best_metrics,
        "feature_importance": importance,
        "fleet_stats": fleet_stats,
        "has_real_fuel_data": df["fuel_consumed"].notna().sum() > 100,
    }, model_path)

    # Register
    _register_model(conn, model_path, best_name, best_metrics, feature_names, len(df), fleet_stats)

    logger.info(f"Fuel predictor saved to {model_path}")

    return {
        "best_model": best_name,
        "metrics": best_metrics,
        "comparison": {
            "gradient_boosting_mae": gb_metrics["mae"],
            "random_forest_mae": rf_metrics["mae"],
        },
        "fleet_stats": fleet_stats,
        "training_rows": len(df),
        "model_path": model_path,
    }


def _evaluate(y_true, y_pred, name: str) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)

    errors = np.abs(y_true.values - y_pred)
    within_5L = (errors <= 5).mean() * 100
    within_10L = (errors <= 10).mean() * 100
    within_20L = (errors <= 20).mean() * 100

    # MAPE
    nonzero = y_true != 0
    mape = np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])) * 100

    metrics = {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4),
        "mape": round(mape, 2),
        "within_5L": round(within_5L, 2),
        "within_10L": round(within_10L, 2),
        "within_20L": round(within_20L, 2),
    }

    logger.info(f"{name}: MAE={mae:.2f}L, RMSE={rmse:.2f}L, R2={r2:.4f}, MAPE={mape:.1f}%")
    logger.info(f"  Within: 5L={within_5L:.1f}%, 10L={within_10L:.1f}%, 20L={within_20L:.1f}%")

    return metrics


def _register_model(conn, model_path, model_type, metrics, feature_cols, training_count, fleet_stats):
    """Register fuel predictor in ml_models table."""
    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'fuel_predictor'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'fuel_predictor'")
        version = cur.fetchone()["max_v"] + 1
        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('fuel_predictor', %s, %s, 'fuel_consumed_liters', %s, %s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE
                metrics = VALUES(metrics),
                feature_columns = VALUES(feature_columns),
                model_artifact_path = VALUES(model_artifact_path),
                training_data_count = VALUES(training_data_count),
                is_active = 1,
                trained_at = CURRENT_TIMESTAMP
        """, (
            version,
            model_type,
            json.dumps({**metrics, "fleet_stats": fleet_stats}),
            json.dumps(feature_cols),
            model_path,
            training_count,
        ))
    conn.commit()
    logger.info(f"Registered fuel_predictor v{version}")


# ============================================
# SERVING / PREDICTION
# ============================================

def predict(artifact: dict, trip_data: dict) -> dict:
    """Predict fuel consumption for a single trip."""
    model = artifact["model"]
    feature_cols = artifact["feature_columns"]

    features = {}
    for col in feature_cols:
        features[col] = trip_data.get(col, 0)

    X = pd.DataFrame([features])[feature_cols].fillna(0).astype(float)
    predicted_fuel = float(model.predict(X)[0])
    predicted_fuel = max(0, predicted_fuel)

    trip_km = trip_data.get("trip_km", 0)
    fuel_efficiency = round(trip_km / predicted_fuel, 2) if predicted_fuel > 0 else 0

    # Diesel cost estimate (INR per liter, approximate)
    diesel_price_inr = 88.0
    cost_estimate = round(predicted_fuel * diesel_price_inr, 2)

    return {
        "predicted_fuel_liters": round(predicted_fuel, 2),
        "fuel_efficiency_km_per_l": fuel_efficiency,
        "estimated_cost_inr": cost_estimate,
        "trip_km": trip_km,
        "data_source": "real" if artifact.get("has_real_fuel_data") else "synthetic_model",
    }
