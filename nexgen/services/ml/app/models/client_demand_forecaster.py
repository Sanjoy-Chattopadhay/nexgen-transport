"""
Model 10: Client Demand Forecasting
- Predicts trip volume per client (company) for the next 7 days
- Trains from CSV data (s_cnr_name + dt_trip_start) since DB trips
  don't have full client mapping yet
- Also builds client profiles: top routes, seasonal patterns, growth trends
- Output: per-client weekly forecast, growth trend, seasonal pattern
"""

import logging
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import joblib

from sklearn.linear_model import Ridge

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CSV_PATH = PROJECT_ROOT / "data-analysis" / "data" / "tbl_trip_data20260128.csv"


def fetch_client_trip_data_from_csv() -> pd.DataFrame:
    """Load client trip data directly from CSV (most complete source)."""
    if not CSV_PATH.exists():
        logger.error(f"CSV not found: {CSV_PATH}")
        return pd.DataFrame()

    logger.info(f"Loading client data from CSV: {CSV_PATH}")

    df = pd.read_csv(
        CSV_PATH,
        usecols=["s_cnr_name", "dt_trip_start", "s_org_node_name", "s_dest_node_name",
                 "i_cnr_id", "dt_trip_eta", "dt_trip_ata"],
        low_memory=False,
    )

    # Parse dates
    df["trip_date"] = pd.to_datetime(df["dt_trip_start"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["trip_date", "s_cnr_name"])
    df["client"] = df["s_cnr_name"].str.strip()

    logger.info(f"Loaded {len(df):,} rows, {df['client'].nunique()} unique clients")
    return df


def fetch_client_trip_data_from_db(conn) -> pd.DataFrame:
    """Fallback: load from DB if CSV not available."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                c.cne_name AS client,
                DATE(t.trip_start) AS trip_date,
                lo.name AS origin,
                ld.name AS destination,
                COUNT(*) AS trip_count
            FROM trips t
            JOIN customers c ON t.customer_id = c.id
            JOIN locations lo ON t.origin_id = lo.id
            JOIN locations ld ON t.destination_id = ld.id
            WHERE t.trip_start IS NOT NULL
              AND t.customer_id IS NOT NULL
              AND t.eta_data_status = 'available'
            GROUP BY c.cne_name, DATE(t.trip_start), lo.name, ld.name
            ORDER BY trip_date
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_client_profiles(df: pd.DataFrame) -> dict:
    """Build per-client profiles: top routes, volume stats, patterns."""
    profiles = {}

    for client, group in df.groupby("client"):
        if len(group) < 10:
            continue

        total_trips = len(group)
        date_range = (group["trip_date"].max() - group["trip_date"].min()).days
        weeks = max(1, date_range / 7)

        # Top routes
        if "s_org_node_name" in group.columns and "s_dest_node_name" in group.columns:
            routes = group.groupby(["s_org_node_name", "s_dest_node_name"]).size()
            top_routes = routes.nlargest(5).reset_index()
            top_routes_list = [
                {"origin": str(r["s_org_node_name"]), "destination": str(r["s_dest_node_name"]),
                 "trips": int(r[0])}
                for _, r in top_routes.iterrows()
            ]
        else:
            top_routes_list = []

        # Day-of-week pattern
        dow_counts = group["trip_date"].dt.dayofweek.value_counts().sort_index()
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        dow_pattern = {dow_names[i]: int(dow_counts.get(i, 0)) for i in range(7)}

        # Monthly pattern
        monthly = group.groupby(group["trip_date"].dt.to_period("M")).size()
        monthly_pattern = {str(k): int(v) for k, v in monthly.tail(12).items()}

        profiles[client] = {
            "total_trips": total_trips,
            "avg_trips_per_week": round(total_trips / weeks, 1),
            "first_trip": group["trip_date"].min().strftime("%Y-%m-%d"),
            "last_trip": group["trip_date"].max().strftime("%Y-%m-%d"),
            "active_weeks": int(weeks),
            "top_routes": top_routes_list,
            "day_of_week_pattern": dow_pattern,
            "monthly_trend": monthly_pattern,
        }

    return profiles


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Client Demand Forecaster")
    logger.info("=" * 50)

    # Prefer CSV (has full client data), fallback to DB
    df = fetch_client_trip_data_from_csv()
    data_source = "csv"
    if df.empty:
        logger.info("CSV not available, falling back to DB")
        df = fetch_client_trip_data_from_db(conn)
        data_source = "db"

    if df.empty:
        return {"error": "No client trip data available"}

    # Ensure trip_date is datetime
    if "trip_date" not in df.columns:
        return {"error": "No trip_date column"}

    df["trip_date"] = pd.to_datetime(df["trip_date"])

    # Build daily trip counts per client
    if data_source == "csv":
        daily_client = (
            df.groupby(["client", df["trip_date"].dt.date])
            .size()
            .reset_index(name="trip_count")
        )
        daily_client.columns = ["client", "trip_date", "trip_count"]
        daily_client["trip_date"] = pd.to_datetime(daily_client["trip_date"])
    else:
        daily_client = df.copy()

    logger.info(f"Daily client data: {len(daily_client):,} rows")
    logger.info(f"Unique clients: {daily_client['client'].nunique()}")

    # Focus on clients with enough data (at least 30 days of trips)
    client_day_counts = daily_client.groupby("client")["trip_date"].nunique()
    active_clients = client_day_counts[client_day_counts >= 30].index.tolist()
    logger.info(f"Clients with 30+ active days: {len(active_clients)}")

    # Take top 100 by volume
    client_totals = daily_client.groupby("client")["trip_count"].sum().sort_values(ascending=False)
    top_clients = [c for c in client_totals.head(100).index if c in active_clients]
    logger.info(f"Forecasting {len(top_clients)} clients")

    forecasts = {}
    client_models = {}

    for client in top_clients:
        client_df = daily_client[daily_client["client"] == client].sort_values("trip_date").copy()

        if len(client_df) < 14:
            continue

        # Time features
        client_df["day_idx"] = (client_df["trip_date"] - client_df["trip_date"].min()).dt.days
        client_df["day_of_week"] = client_df["trip_date"].dt.dayofweek
        client_df["month"] = client_df["trip_date"].dt.month
        client_df["is_weekend"] = (client_df["day_of_week"] >= 5).astype(int)

        # Moving averages
        client_df["ma_7"] = client_df["trip_count"].rolling(7, min_periods=1).mean()
        client_df["ma_14"] = client_df["trip_count"].rolling(14, min_periods=1).mean()
        client_df["ma_30"] = client_df["trip_count"].rolling(30, min_periods=1).mean()

        feature_cols = ["day_idx", "day_of_week", "month", "is_weekend", "ma_7", "ma_14", "ma_30"]
        X = client_df[feature_cols].fillna(0).values
        y = client_df["trip_count"].values

        # Exponential smoothing
        alpha = 0.3
        smoothed = [y[0]]
        for i in range(1, len(y)):
            smoothed.append(alpha * y[i] + (1 - alpha) * smoothed[-1])

        # Ridge regression
        model = Ridge(alpha=1.0)
        model.fit(X, y)

        # Forecast next 7 days
        last_date = client_df["trip_date"].max()
        last_day_idx = client_df["day_idx"].max()
        last_ma7 = client_df["ma_7"].iloc[-1]
        last_ma14 = client_df["ma_14"].iloc[-1]
        last_ma30 = client_df["ma_30"].iloc[-1]

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
                last_ma30,
            ]])
            regression_pred = model.predict(future_features)[0]
            exp_pred = smoothed[-1]
            ensemble_pred = max(0, regression_pred * 0.6 + exp_pred * 0.4)

            future_forecasts.append({
                "date": future_date.strftime("%Y-%m-%d"),
                "day_of_week": future_date.strftime("%A"),
                "predicted_trips": round(float(ensemble_pred), 1),
            })

        # Growth trend
        if len(smoothed) > 30:
            recent = np.mean(smoothed[-7:])
            month_ago = np.mean(smoothed[-30:-23]) if len(smoothed) > 30 else smoothed[0]
            growth_pct = ((recent - month_ago) / max(month_ago, 1)) * 100
            trend = "growing" if growth_pct > 5 else ("declining" if growth_pct < -5 else "stable")
        else:
            growth_pct = 0
            trend = "stable"

        forecasts[client] = {
            "historical_avg_daily": round(float(client_df["trip_count"].mean()), 1),
            "recent_avg_daily_7d": round(float(last_ma7), 1),
            "trend": trend,
            "growth_pct_30d": round(float(growth_pct), 1),
            "next_7_days": future_forecasts,
            "total_predicted_week": round(sum(f["predicted_trips"] for f in future_forecasts), 1),
            "total_historical_trips": int(client_df["trip_count"].sum()),
        }

    # Build client profiles
    logger.info("Building client profiles...")
    profiles = build_client_profiles(df)

    # Save model artifact
    model_path = str(models_dir / "client_demand_forecaster.joblib")
    joblib.dump({
        "forecasts": forecasts,
        "profiles": profiles,
        "top_clients": top_clients,
        "data_source": data_source,
        "generated_at": datetime.now().isoformat(),
    }, model_path)

    # Register in DB
    metrics = {
        "clients_forecasted": len(forecasts),
        "clients_profiled": len(profiles),
        "data_source": data_source,
        "top_5_clients": {c: forecasts[c]["total_predicted_week"]
                          for c in list(forecasts.keys())[:5]},
    }

    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'client_demand_forecaster'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'client_demand_forecaster'")
        version = cur.fetchone()["max_v"] + 1

        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('client_demand_forecaster', %s, 'Ridge+ExpSmoothing', 'client_trip_count', %s, %s, %s, %s, 1)
        """, (
            version,
            json.dumps(metrics),
            json.dumps(["day_idx", "day_of_week", "month", "is_weekend", "ma_7", "ma_14", "ma_30"]),
            model_path,
            len(daily_client),
        ))
    conn.commit()

    logger.info(f"Client demand forecaster saved to {model_path}")
    for client in list(forecasts.keys())[:5]:
        fc = forecasts[client]
        logger.info(f"  {client}: avg={fc['historical_avg_daily']}/day, "
                     f"next week={fc['total_predicted_week']}, trend={fc['trend']}")

    return metrics


def get_client_forecast(artifact: dict, client: str = None) -> dict:
    """Get demand forecast for a specific client or all clients."""
    forecasts = artifact.get("forecasts", {})
    generated_at = artifact.get("generated_at", "unknown")

    if client:
        # Try exact match first, then case-insensitive
        if client in forecasts:
            return {"client": client, "forecast": forecasts[client], "generated_at": generated_at}

        # Case-insensitive search
        for k, v in forecasts.items():
            if k.lower() == client.lower():
                return {"client": k, "forecast": v, "generated_at": generated_at}

        # Partial match
        matches = {k: v for k, v in forecasts.items() if client.lower() in k.lower()}
        if matches:
            if len(matches) == 1:
                k = list(matches.keys())[0]
                return {"client": k, "forecast": matches[k], "generated_at": generated_at}
            return {
                "error": f"Multiple clients match '{client}'",
                "matches": list(matches.keys())[:10],
            }

        return {"error": f"No forecast for client: {client}"}

    # Return summary of all clients
    client_summary = []
    for c, fc in sorted(forecasts.items(), key=lambda x: -x[1]["total_predicted_week"]):
        client_summary.append({
            "client": c,
            "avg_daily": fc["historical_avg_daily"],
            "predicted_week": fc["total_predicted_week"],
            "trend": fc["trend"],
            "growth_pct": fc["growth_pct_30d"],
        })

    return {
        "clients_count": len(forecasts),
        "generated_at": generated_at,
        "clients": client_summary,
    }


def get_client_profile(artifact: dict, client: str) -> dict:
    """Get detailed profile for a client."""
    profiles = artifact.get("profiles", {})
    forecasts = artifact.get("forecasts", {})

    # Case-insensitive + partial match
    matched_key = None
    for k in profiles.keys():
        if k.lower() == client.lower() or client.lower() in k.lower():
            matched_key = k
            break

    if not matched_key:
        matches = [k for k in profiles.keys() if client.lower() in k.lower()]
        if matches:
            if len(matches) == 1:
                matched_key = matches[0]
            else:
                return {"error": f"Multiple clients match '{client}'", "matches": matches[:10]}
        else:
            return {"error": f"No profile for client: {client}"}

    profile = profiles[matched_key]
    forecast = forecasts.get(matched_key, {})

    return {
        "client": matched_key,
        "profile": profile,
        "forecast": forecast if forecast else None,
    }


def list_clients(artifact: dict) -> dict:
    """List all known clients with basic stats."""
    profiles = artifact.get("profiles", {})
    forecasts = artifact.get("forecasts", {})

    clients = []
    for name, profile in sorted(profiles.items(), key=lambda x: -x[1]["total_trips"]):
        fc = forecasts.get(name, {})
        clients.append({
            "client": name,
            "total_trips": profile["total_trips"],
            "avg_trips_per_week": profile["avg_trips_per_week"],
            "active_weeks": profile["active_weeks"],
            "has_forecast": name in forecasts,
            "trend": fc.get("trend", "unknown"),
        })

    return {
        "total_clients": len(clients),
        "clients_with_forecast": len(forecasts),
        "clients": clients,
    }
