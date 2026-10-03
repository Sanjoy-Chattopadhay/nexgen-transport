"""
Model 2: Trip Anomaly Detection (v2 - Batch-Only)
- Isolation Forest (unsupervised) — trained on all historical trips
- NO manual single-trip predict endpoint (useless for users)
- Instead: batch scan that scores recent trips and writes alerts
- User triggers: "Scan for Anomalies" → gets back alert summary
"""

import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


ANOMALY_FEATURE_COLS = [
    "trip_duration_minutes",
    "eta_delay_minutes",
    "duration_ratio",        # actual / route avg
    "delay_ratio",           # delay / route avg duration
    "hour_deviation",        # how far from midnight
    "is_night_trip",
]


def fetch_anomaly_data(conn, limit: int = 500000) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.id AS trip_id,
                t.trip_duration_minutes,
                t.eta_delay_minutes,
                t.avg_speed_kmph,
                t.trip_km,
                t.trip_start,
                t.driver_id,
                t.vehicle_id,
                lo.name AS origin_name,
                ld.name AS destination_name,
                d.name AS driver_name,
                rs.avg_duration_min AS route_avg_duration,
                rs.avg_distance_km AS route_avg_distance,
                rs.eta_success_rate AS route_eta_success
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN route_summary rs ON lo.name = rs.origin AND ld.name = rs.destination
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_recent_unscanned_trips(conn, days: int = 7, limit: int = 50000) -> pd.DataFrame:
    """Fetch recent trips that haven't been scanned for anomalies yet."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.id AS trip_id,
                t.trip_duration_minutes,
                t.eta_delay_minutes,
                t.avg_speed_kmph,
                t.trip_km,
                t.trip_start,
                t.driver_id,
                t.vehicle_id,
                lo.name AS origin_name,
                ld.name AS destination_name,
                d.name AS driver_name,
                rs.avg_duration_min AS route_avg_duration,
                rs.avg_distance_km AS route_avg_distance,
                rs.eta_success_rate AS route_eta_success
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN drivers d ON t.driver_id = d.id
            LEFT JOIN route_summary rs ON lo.name = rs.origin AND ld.name = rs.destination
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
              AND t.trip_start >= DATE_SUB(NOW(), INTERVAL %s DAY)
              AND t.id NOT IN (
                  SELECT DISTINCT a.trip_id FROM alerts a
                  WHERE a.alert_type = 'anomaly' AND a.trip_id IS NOT NULL
              )
            LIMIT %s
        """, (days, limit))
        rows = cur.fetchall()

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def engineer_anomaly_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create features specifically for anomaly detection."""
    features = pd.DataFrame(index=df.index)

    features["trip_duration_minutes"] = df["trip_duration_minutes"].astype(float)
    features["eta_delay_minutes"] = df["eta_delay_minutes"].fillna(0).astype(float)

    # Duration ratio: how much longer/shorter than route average
    route_avg = df["route_avg_duration"].astype(float).replace(0, np.nan)
    features["duration_ratio"] = (features["trip_duration_minutes"] / route_avg).fillna(1.0)

    # Delay as fraction of route average
    features["delay_ratio"] = (features["eta_delay_minutes"] / route_avg.fillna(1)).fillna(0)

    # Time-based: trips at unusual hours
    trip_hour = pd.to_datetime(df["trip_start"]).dt.hour
    features["hour_deviation"] = np.minimum(trip_hour, 24 - trip_hour)  # distance from midnight
    features["is_night_trip"] = ((trip_hour >= 22) | (trip_hour <= 5)).astype(int)

    return features.fillna(0)


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Anomaly Detector v2 (Batch-Only)")
    logger.info("=" * 50)

    df = fetch_anomaly_data(conn)
    if df.empty:
        logger.error("No data for anomaly detection")
        return {"error": "No data"}

    logger.info(f"Anomaly data: {len(df):,} rows")

    features = engineer_anomaly_features(df)

    # Standardize for Isolation Forest
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(features[ANOMALY_FEATURE_COLS])

    # --- Isolation Forest ---
    logger.info("\n--- Isolation Forest ---")
    iso_model = IsolationForest(
        n_estimators=200,
        contamination=0.05,
        max_features=1.0,
        bootstrap=True,
        random_state=42,
        n_jobs=-1,
    )
    iso_model.fit(X_scaled)

    # Score all data
    iso_scores = iso_model.decision_function(X_scaled)
    iso_pred = iso_model.predict(X_scaled)
    iso_anomaly_rate = (iso_pred == -1).mean() * 100

    # Normalize scores to 0-1 range (higher = more anomalous)
    score_min, score_max = iso_scores.min(), iso_scores.max()
    normalized_scores = 1 - (iso_scores - score_min) / (score_max - score_min + 1e-8)

    logger.info(f"Isolation Forest anomaly rate: {iso_anomaly_rate:.1f}%")
    logger.info(f"Score distribution: min={normalized_scores.min():.3f}, "
                f"median={np.median(normalized_scores):.3f}, max={normalized_scores.max():.3f}")

    # Feature contribution analysis
    anomaly_mask = iso_pred == -1
    if anomaly_mask.any():
        anomaly_features = features[ANOMALY_FEATURE_COLS][anomaly_mask]
        normal_features = features[ANOMALY_FEATURE_COLS][~anomaly_mask]

        contribution_analysis = {}
        for col in ANOMALY_FEATURE_COLS:
            anomaly_mean = anomaly_features[col].mean()
            normal_mean = normal_features[col].mean()
            normal_std = normal_features[col].std() or 1
            z_score = abs(anomaly_mean - normal_mean) / normal_std
            contribution_analysis[col] = round(z_score, 3)

        logger.info(f"Feature z-scores (anomaly vs normal): {contribution_analysis}")

    # Save
    model_path = str(models_dir / "anomaly_detector.joblib")
    joblib.dump({
        "model": iso_model,
        "scaler": scaler,
        "feature_columns": ANOMALY_FEATURE_COLS,
        "score_range": {"min": float(score_min), "max": float(score_max)},
    }, model_path)

    metrics = {
        "anomaly_rate": round(iso_anomaly_rate, 2),
        "training_samples": len(df),
        "feature_count": len(ANOMALY_FEATURE_COLS),
        "contamination": 0.05,
    }

    # Generate alerts for detected anomalies during training
    anomaly_count = _generate_anomaly_alerts(conn, df, normalized_scores, iso_pred)

    metrics["alerts_generated"] = anomaly_count
    logger.info(f"Generated {anomaly_count} anomaly alerts")

    return metrics


def _generate_anomaly_alerts(conn, df, scores, predictions) -> int:
    """Insert alerts for detected anomalous trips."""
    anomaly_indices = np.where(predictions == -1)[0]

    if len(anomaly_indices) == 0:
        return 0

    # Only insert top 100 most anomalous (to avoid flooding)
    top_anomalies = anomaly_indices[np.argsort(scores[anomaly_indices])[-100:]]

    count = 0
    with conn.cursor() as cur:
        for idx in top_anomalies:
            row = df.iloc[idx]
            trip_id = int(row["trip_id"]) if pd.notna(row.get("trip_id")) else None
            driver_id = int(row["driver_id"]) if pd.notna(row.get("driver_id")) else None
            vehicle_id = int(row["vehicle_id"]) if pd.notna(row.get("vehicle_id")) else None
            score = float(scores[idx])

            severity = "low" if score < 0.7 else ("medium" if score < 0.85 else "high")

            raw_duration = row.get("trip_duration_minutes")
            duration = float(raw_duration) if raw_duration is not None and pd.notna(raw_duration) else 0
            raw_route_avg = row.get("route_avg_duration")
            route_avg = float(raw_route_avg) if raw_route_avg is not None and pd.notna(raw_route_avg) else 0

            message = (
                f"Trip duration ({duration:.0f} min) is "
                f"{duration/route_avg:.1f}x the route average ({route_avg:.0f} min). "
                f"Anomaly score: {score:.3f}"
            ) if route_avg > 0 else f"Anomaly score: {score:.3f}"

            try:
                cur.execute(
                    """
                    INSERT INTO alerts (alert_type, severity, trip_id, driver_id, vehicle_id, title, message, metadata)
                    VALUES ('anomaly', %s, %s, %s, %s, 'Anomalous Trip Detected', %s, %s)
                    """,
                    (severity, trip_id, driver_id, vehicle_id, message,
                     f'{{"anomaly_score": {score:.4f}}}'),
                )
                count += 1
            except Exception:
                continue

    conn.commit()
    return count


def scan_recent_trips(conn, days: int = 7) -> dict:
    """
    ONE-CLICK batch scan: scores recent trips for anomalies, writes alerts.
    No manual input needed — just call this endpoint.
    Returns summary of findings with top anomalies.
    """
    from nexgen.services.ml.app.serving.model_server import load_model

    artifact = load_model("anomaly_detector")
    if artifact is None:
        return {"error": "Anomaly detector model not trained. Run training first."}

    model = artifact["model"]
    scaler = artifact["scaler"]
    score_range = artifact["score_range"]

    # Fetch recent trips not yet scanned
    df = fetch_recent_unscanned_trips(conn, days=days)
    if df.empty:
        return {
            "status": "ok",
            "message": "No new trips to scan",
            "trips_scanned": 0,
            "anomalies_found": 0,
            "alerts_created": 0,
        }

    logger.info(f"Scanning {len(df)} recent trips for anomalies...")

    # Engineer features
    features = engineer_anomaly_features(df)
    X_scaled = scaler.transform(features[ANOMALY_FEATURE_COLS])

    # Score
    raw_scores = model.decision_function(X_scaled)
    predictions = model.predict(X_scaled)

    # Normalize scores
    smin, smax = score_range["min"], score_range["max"]
    normalized = 1 - (raw_scores - smin) / (smax - smin + 1e-8)
    normalized = np.clip(normalized, 0, 1)

    anomaly_mask = predictions == -1
    anomaly_count = int(anomaly_mask.sum())

    # Generate alerts for anomalous trips
    alerts_created = 0
    anomaly_details = []

    if anomaly_count > 0:
        anomaly_indices = np.where(anomaly_mask)[0]
        sorted_indices = anomaly_indices[np.argsort(normalized[anomaly_indices])[::-1]][:200]

        with conn.cursor() as cur:
            for idx in sorted_indices:
                row = df.iloc[idx]
                score = float(normalized[idx])
                trip_id = int(row["trip_id"]) if pd.notna(row.get("trip_id")) else None
                driver_id = int(row["driver_id"]) if pd.notna(row.get("driver_id")) else None
                vehicle_id = int(row["vehicle_id"]) if pd.notna(row.get("vehicle_id")) else None

                severity = "low" if score < 0.7 else ("medium" if score < 0.85 else "high")

                duration = float(row.get("trip_duration_minutes", 0) or 0)
                route_avg = float(row.get("route_avg_duration", 0) or 0)
                origin = str(row.get("origin_name", ""))
                destination = str(row.get("destination_name", ""))
                driver_name = str(row.get("driver_name", "Unknown"))

                if route_avg > 0:
                    message = (
                        f"Driver {driver_name}: trip {origin} -> {destination} "
                        f"took {duration:.0f} min ({duration/route_avg:.1f}x route avg of {route_avg:.0f} min). "
                        f"Anomaly score: {score:.3f}"
                    )
                else:
                    message = (
                        f"Driver {driver_name}: trip {origin} -> {destination} "
                        f"took {duration:.0f} min. Anomaly score: {score:.3f}"
                    )

                try:
                    cur.execute(
                        """
                        INSERT INTO alerts (alert_type, severity, trip_id, driver_id, vehicle_id,
                                            title, message, metadata)
                        VALUES ('anomaly', %s, %s, %s, %s, 'Anomalous Trip Detected', %s, %s)
                        """,
                        (severity, trip_id, driver_id, vehicle_id, message,
                         f'{{"anomaly_score": {score:.4f}}}'),
                    )
                    alerts_created += 1
                except Exception:
                    continue

                if len(anomaly_details) < 20:
                    anomaly_details.append({
                        "trip_id": trip_id,
                        "driver_name": driver_name,
                        "route": f"{origin} -> {destination}",
                        "duration_min": round(duration, 1),
                        "route_avg_min": round(route_avg, 1),
                        "anomaly_score": round(score, 3),
                        "severity": severity,
                    })

        conn.commit()

    severity_counts = {"high": 0, "medium": 0, "low": 0}
    for d in anomaly_details:
        severity_counts[d["severity"]] = severity_counts.get(d["severity"], 0) + 1

    return {
        "status": "ok",
        "trips_scanned": len(df),
        "anomalies_found": anomaly_count,
        "alerts_created": alerts_created,
        "severity_breakdown": severity_counts,
        "scan_period_days": days,
        "top_anomalies": anomaly_details,
    }
