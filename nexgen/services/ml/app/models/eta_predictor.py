"""
Model 1: ETA Prediction
- Primary: XGBoost Regressor
- Enhanced: LightGBM as comparison
- Target: trip_duration_minutes
- Features: temporal + route stats + driver stats + vehicle stats + time patterns
"""

import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.preprocessing import StandardScaler
import xgboost as xgb
import lightgbm as lgb

from nexgen.services.ml.app.features.feature_engineering import (
    extract_temporal_features,
    ETA_FEATURE_COLUMNS,
)

logger = logging.getLogger(__name__)


def fetch_training_data(conn) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.trip_duration_minutes,
                t.trip_km,
                t.trip_start,
                t.eta_met,
                t.eta_delay_minutes,
                t.driver_id,
                t.vehicle_id,
                t.is_5am_default,
                lo.name AS origin_name,
                ld.name AS destination_name,
                ds.avg_duration_min AS driver_avg_duration,
                ds.avg_speed_kmph AS driver_avg_speed,
                ds.eta_success_rate AS driver_eta_success,
                ds.total_trips AS driver_total_trips,
                ds.vehicles_used AS driver_vehicles_used,
                rs.avg_duration_min AS route_avg_duration,
                rs.avg_distance_km AS route_avg_distance,
                rs.trip_count AS route_trip_count,
                rs.eta_success_rate AS route_eta_success,
                vs.avg_speed_kmph AS vehicle_avg_speed,
                vs.total_trips AS vehicle_total_trips,
                vs.eta_success_rate AS vehicle_eta_success
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN driver_summary ds ON t.driver_id = ds.driver_id
            LEFT JOIN route_summary rs ON lo.name = rs.origin AND ld.name = rs.destination
            LEFT JOIN vehicle_summary vs ON t.vehicle_id = vs.vehicle_id
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_duration_minutes < 50000
              AND t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
            LIMIT 500000
        """)
        rows = cur.fetchall()

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows)


def engineer_features(df: pd.DataFrame) -> tuple:
    """Engineer all features from raw data. Returns (X, y, feature_names)."""
    # Temporal features (vectorized)
    temporal = df["trip_start"].apply(extract_temporal_features).apply(pd.Series)
    bucket_map = {"morning": 0, "afternoon": 1, "evening": 2, "night": 3}
    temporal["time_bucket_encoded"] = temporal["time_bucket"].map(bucket_map).fillna(-1)
    temporal = temporal.drop(columns=["time_bucket"])

    # Combine with DB features
    feature_df = pd.concat([temporal, df[[
        "trip_km",
        "is_5am_default",
        "driver_avg_duration", "driver_avg_speed", "driver_eta_success",
        "driver_total_trips", "driver_vehicles_used",
        "route_avg_duration", "route_avg_distance", "route_trip_count", "route_eta_success",
        "vehicle_avg_speed", "vehicle_total_trips", "vehicle_eta_success",
    ]]], axis=1)

    # Time pattern features (use route avg as proxy for now)
    feature_df["time_pattern_avg_duration"] = feature_df["route_avg_duration"]
    feature_df["time_pattern_trip_count"] = feature_df["route_trip_count"]
    feature_df["time_pattern_eta_success"] = feature_df["route_eta_success"]

    # Ensure all columns exist
    for col in ETA_FEATURE_COLUMNS:
        if col not in feature_df.columns:
            feature_df[col] = 0

    X = feature_df[ETA_FEATURE_COLUMNS].fillna(0).astype(float)
    y = df["trip_duration_minutes"].astype(float)

    return X, y, ETA_FEATURE_COLUMNS


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: ETA Predictor")
    logger.info("=" * 50)

    df = fetch_training_data(conn)
    if df.empty:
        logger.error("No training data available")
        return {"error": "No training data"}

    logger.info(f"Training data: {len(df):,} rows")

    X, y, feature_names = engineer_features(df)

    # Remove outliers (IQR method on target)
    q1, q3 = y.quantile(0.01), y.quantile(0.99)
    mask = (y >= q1) & (y <= q3)
    X, y = X[mask], y[mask]
    logger.info(f"After outlier removal: {len(X):,} rows")

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    logger.info(f"Train: {len(X_train):,}, Test: {len(X_test):,}")

    results = {}

    # --- Model A: XGBoost ---
    logger.info("\n--- XGBoost ---")
    xgb_model = xgb.XGBRegressor(
        n_estimators=300,
        max_depth=8,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
        tree_method="hist",
    )
    xgb_model.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        verbose=False,
    )

    xgb_pred = xgb_model.predict(X_test)
    xgb_metrics = _evaluate(y_test, xgb_pred, "XGBoost")
    results["xgboost"] = xgb_metrics

    # Cross-validation score
    cv_scores = cross_val_score(
        xgb_model, X_train, y_train, cv=5, scoring="neg_mean_absolute_error", n_jobs=-1
    )
    xgb_metrics["cv_mae"] = round(-cv_scores.mean(), 4)
    xgb_metrics["cv_mae_std"] = round(cv_scores.std(), 4)
    logger.info(f"XGB CV MAE: {xgb_metrics['cv_mae']:.2f} (+/- {xgb_metrics['cv_mae_std']:.2f})")

    # Feature importance
    importance = dict(zip(feature_names, xgb_model.feature_importances_.tolist()))
    top_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]
    logger.info(f"Top features: {[(f, round(v, 4)) for f, v in top_features]}")

    # --- Model B: LightGBM ---
    logger.info("\n--- LightGBM ---")
    lgb_model = lgb.LGBMRegressor(
        n_estimators=300,
        max_depth=8,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=20,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    lgb_model.fit(X_train, y_train, eval_set=[(X_test, y_test)])

    lgb_pred = lgb_model.predict(X_test)
    lgb_metrics = _evaluate(y_test, lgb_pred, "LightGBM")
    results["lightgbm"] = lgb_metrics

    # --- Choose best model ---
    if lgb_metrics["mae"] < xgb_metrics["mae"]:
        best_model = lgb_model
        best_name = "lightgbm"
        best_metrics = lgb_metrics
        logger.info("\nBest model: LightGBM")
    else:
        best_model = xgb_model
        best_name = "xgboost"
        best_metrics = xgb_metrics
        logger.info("\nBest model: XGBoost")

    # Save
    model_path = str(models_dir / "eta_predictor.joblib")
    joblib.dump({
        "model": best_model,
        "model_type": best_name,
        "feature_columns": feature_names,
        "metrics": best_metrics,
        "feature_importance": importance,
    }, model_path)

    logger.info(f"Saved to {model_path}")

    return {
        "best_model": best_name,
        "metrics": best_metrics,
        "comparison": {
            "xgboost_mae": xgb_metrics["mae"],
            "lightgbm_mae": lgb_metrics["mae"],
        },
        "training_rows": len(df),
        "feature_count": len(feature_names),
        "model_path": model_path,
    }


def _evaluate(y_true, y_pred, name: str) -> dict:
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    r2 = r2_score(y_true, y_pred)

    errors = np.abs(y_true.values - y_pred)
    within_15 = (errors <= 15).mean() * 100
    within_30 = (errors <= 30).mean() * 100
    within_60 = (errors <= 60).mean() * 100

    # MAPE (avoid division by zero)
    nonzero = y_true != 0
    mape = np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])) * 100

    metrics = {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4),
        "mape": round(mape, 2),
        "within_15min": round(within_15, 2),
        "within_30min": round(within_30, 2),
        "within_60min": round(within_60, 2),
    }

    logger.info(f"{name}: MAE={mae:.2f}, RMSE={rmse:.2f}, R2={r2:.4f}, MAPE={mape:.1f}%")
    logger.info(f"  Within: 15m={within_15:.1f}%, 30m={within_30:.1f}%, 60m={within_60:.1f}%")

    return metrics
