"""
Model 4: Route Demand Forecasting
- Predicts number of trips per route per week
- Algorithm: Exponential Smoothing + Linear Regression (ensemble)
- Input: historical trip counts per route per day/week
- Output: predicted trips for next 7 days per top routes
"""

import logging
import json
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import joblib

from sklearn.linear_model import Ridge
from sklearn.preprocessing import PolynomialFeatures

logger = logging.getLogger(__name__)


def fetch_demand_data(conn) -> pd.DataFrame:
    """Get daily trip counts per route."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                DATE(t.trip_start) AS trip_date,
                lo.name AS origin,
                ld.name AS destination,
                COUNT(*) AS trip_count
            FROM trips t
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
            GROUP BY DATE(t.trip_start), lo.name, ld.name
            HAVING COUNT(*) >= 1
            ORDER BY trip_date
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Demand Forecaster")
    logger.info("=" * 50)

    df = fetch_demand_data(conn)
    if df.empty:
        logger.error("No demand data available")
        return {"error": "No demand data"}

    df["trip_date"] = pd.to_datetime(df["trip_date"])
    df["route"] = df["origin"] + " → " + df["destination"]

    logger.info(f"Demand data: {len(df):,} route-day observations")
    logger.info(f"Date range: {df['trip_date'].min()} to {df['trip_date'].max()}")
    logger.info(f"Unique routes: {df['route'].nunique():,}")

    # Focus on top routes (enough data for forecasting)
    route_totals = df.groupby("route")["trip_count"].sum().sort_values(ascending=False)
    top_routes = route_totals.head(50).index.tolist()
    logger.info(f"Forecasting top {len(top_routes)} routes")

    forecasts = {}
    route_models = {}

    for route in top_routes:
        route_df = df[df["route"] == route].sort_values("trip_date").copy()

        if len(route_df) < 14:  # need at least 2 weeks of data
            continue

        # Create time features
        route_df["day_idx"] = (route_df["trip_date"] - route_df["trip_date"].min()).dt.days
        route_df["day_of_week"] = route_df["trip_date"].dt.dayofweek
        route_df["month"] = route_df["trip_date"].dt.month
        route_df["is_weekend"] = (route_df["day_of_week"] >= 5).astype(int)

        # Moving averages
        route_df["ma_7"] = route_df["trip_count"].rolling(7, min_periods=1).mean()
        route_df["ma_14"] = route_df["trip_count"].rolling(14, min_periods=1).mean()

        # Features for regression
        feature_cols = ["day_idx", "day_of_week", "month", "is_weekend", "ma_7", "ma_14"]
        X = route_df[feature_cols].fillna(0).values
        y = route_df["trip_count"].values

        # Simple exponential smoothing forecast
        alpha = 0.3
        smoothed = [y[0]]
        for i in range(1, len(y)):
            smoothed.append(alpha * y[i] + (1 - alpha) * smoothed[-1])

        # Ridge regression for trend
        model = Ridge(alpha=1.0)
        model.fit(X, y)
        train_pred = model.predict(X)

        # Forecast next 7 days
        last_date = route_df["trip_date"].max()
        last_day_idx = route_df["day_idx"].max()
        last_ma7 = route_df["ma_7"].iloc[-1]
        last_ma14 = route_df["ma_14"].iloc[-1]

        future_forecasts = []
        for d in range(1, 8):
            future_date = last_date + timedelta(days=d)
            future_features = np.array([[
                last_day_idx + d,
                future_date.weekday(),
                future_date.month,
                1 if future_date.weekday() >= 5 else 0,
                last_ma7,
                last_ma14,
            ]])
            regression_pred = model.predict(future_features)[0]

            # Ensemble: average of regression and exponential smoothing
            exp_pred = smoothed[-1]
            ensemble_pred = max(0, (regression_pred * 0.6 + exp_pred * 0.4))

            future_forecasts.append({
                "date": future_date.strftime("%Y-%m-%d"),
                "day_of_week": future_date.strftime("%A"),
                "predicted_trips": round(ensemble_pred, 1),
            })

        forecasts[route] = {
            "historical_avg_daily": round(route_df["trip_count"].mean(), 1),
            "recent_trend": "up" if smoothed[-1] > smoothed[-7] else "down" if len(smoothed) > 7 else "stable",
            "next_7_days": future_forecasts,
            "total_predicted_week": round(sum(f["predicted_trips"] for f in future_forecasts), 1),
        }

        route_models[route] = {
            "model": model,
            "alpha": alpha,
            "last_smoothed": smoothed[-1],
            "feature_cols": feature_cols,
        }

    # Fleet-wide daily forecast (aggregate all routes)
    fleet_daily = df.groupby("trip_date")["trip_count"].sum().reset_index()
    fleet_daily = fleet_daily.sort_values("trip_date")
    fleet_daily["day_idx"] = (fleet_daily["trip_date"] - fleet_daily["trip_date"].min()).dt.days
    fleet_daily["day_of_week"] = fleet_daily["trip_date"].dt.dayofweek
    fleet_daily["is_weekend"] = (fleet_daily["day_of_week"] >= 5).astype(int)
    fleet_daily["ma_7"] = fleet_daily["trip_count"].rolling(7, min_periods=1).mean()
    fleet_daily["ma_14"] = fleet_daily["trip_count"].rolling(14, min_periods=1).mean()

    fleet_forecast = {}
    if len(fleet_daily) >= 14:
        fleet_X = fleet_daily[["day_idx", "day_of_week", "is_weekend", "ma_7", "ma_14"]].fillna(0)
        fleet_y = fleet_daily["trip_count"].values

        fleet_model = Ridge(alpha=1.0)
        fleet_model.fit(fleet_X, fleet_y)

        # Exponential smoothing
        alpha_fleet = 0.3
        fleet_smoothed = [fleet_y[0]]
        for i in range(1, len(fleet_y)):
            fleet_smoothed.append(alpha_fleet * fleet_y[i] + (1 - alpha_fleet) * fleet_smoothed[-1])

        last_date = fleet_daily["trip_date"].max()
        last_day_idx = fleet_daily["day_idx"].max()
        last_ma7 = fleet_daily["ma_7"].iloc[-1]
        last_ma14 = fleet_daily["ma_14"].iloc[-1]

        fleet_next_7 = []
        for d in range(1, 8):
            future_date = last_date + timedelta(days=d)
            features = np.array([[
                last_day_idx + d,
                future_date.weekday(),
                1 if future_date.weekday() >= 5 else 0,
                last_ma7,
                last_ma14,
            ]])
            reg_pred = fleet_model.predict(features)[0]
            exp_pred = fleet_smoothed[-1]
            ensemble = max(0, reg_pred * 0.6 + exp_pred * 0.4)

            fleet_next_7.append({
                "date": future_date.strftime("%Y-%m-%d"),
                "day_of_week": future_date.strftime("%A"),
                "predicted_trips": round(ensemble, 1),
            })

        fleet_forecast = {
            "historical_avg_daily": round(fleet_daily["trip_count"].mean(), 1),
            "recent_avg_daily_7d": round(fleet_daily["trip_count"].tail(7).mean(), 1),
            "recent_trend": "up" if fleet_smoothed[-1] > (fleet_smoothed[-7] if len(fleet_smoothed) > 7 else fleet_smoothed[0]) else "down",
            "next_7_days": fleet_next_7,
            "total_predicted_week": round(sum(f["predicted_trips"] for f in fleet_next_7), 1),
        }

    # Save
    model_path = str(models_dir / "demand_forecaster.joblib")
    joblib.dump({
        "route_models": route_models,
        "forecasts": forecasts,
        "fleet_forecast": fleet_forecast,
        "top_routes": top_routes,
        "generated_at": datetime.now().isoformat(),
    }, model_path)

    logger.info(f"Forecasted {len(forecasts)} routes")

    # Log top routes forecast
    for route in list(forecasts.keys())[:5]:
        fc = forecasts[route]
        logger.info(f"  {route}: avg={fc['historical_avg_daily']}/day, "
                     f"next week={fc['total_predicted_week']}, trend={fc['recent_trend']}")

    return {
        "routes_forecasted": len(forecasts),
        "forecast_horizon": "7 days",
        "model_path": model_path,
        "top_5_routes": {r: forecasts[r]["total_predicted_week"] for r in list(forecasts.keys())[:5]},
    }
