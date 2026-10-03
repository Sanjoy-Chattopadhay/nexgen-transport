"""
Model 9: Driver Fatigue / Risk Prediction
- XGBoost classifier: predicts if a driver is at risk of fatigue
- Based on: consecutive driving hours, trip frequency, time-of-day,
  speed variance, recent delay patterns
- Output: fatigue_risk (low/medium/high/critical), probability, factors
- Business value: safety compliance, accident prevention, insurance
"""

import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

logger = logging.getLogger(__name__)


FATIGUE_FEATURE_COLS = [
    "trips_last_24h",
    "trips_last_7d",
    "hours_driving_last_24h",
    "hours_driving_last_7d",
    "avg_trip_duration_recent",
    "max_trip_duration_recent",
    "avg_speed_recent",
    "speed_variance_recent",
    "night_trips_last_7d",
    "eta_delay_rate_recent",
    "avg_delay_min_recent",
    "hours_since_last_trip",
    "is_current_night",
    "consecutive_days_active",
]


def fetch_driver_activity_data(conn) -> pd.DataFrame:
    """
    Build per-driver activity features from trip history.
    For each driver, compute rolling stats that indicate fatigue risk.
    """
    with conn.cursor() as cur:
        # Get driver activity in last 30 days with detailed trip info
        cur.execute("""
            SELECT
                t.driver_id,
                d.name AS driver_name,
                t.trip_start,
                t.trip_end,
                t.trip_duration_minutes,
                t.avg_speed_kmph,
                t.eta_met,
                t.eta_delay_minutes,
                HOUR(t.trip_start) AS start_hour
            FROM trips t
            JOIN drivers d ON t.driver_id = d.id
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
              AND t.driver_id IS NOT NULL
            ORDER BY t.driver_id, t.trip_start DESC
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_fatigue_features(driver_df: pd.DataFrame) -> list:
    """
    For each driver, compute fatigue-related features based on their trip history.
    Returns list of feature dicts, one per driver.
    """
    results = []

    for driver_id, group in driver_df.groupby("driver_id"):
        group = group.sort_values("trip_start", ascending=False)

        if len(group) < 3:
            continue

        driver_name = group.iloc[0]["driver_name"]
        now = group["trip_start"].max()  # Use latest trip as "now" reference

        # Time windows
        last_24h = group[group["trip_start"] >= (now - pd.Timedelta(hours=24))]
        last_7d = group[group["trip_start"] >= (now - pd.Timedelta(days=7))]
        recent = group.head(20)  # Last 20 trips

        # Feature computation
        trips_24h = len(last_24h)
        trips_7d = len(last_7d)

        hours_24h = last_24h["trip_duration_minutes"].sum() / 60 if not last_24h.empty else 0
        hours_7d = last_7d["trip_duration_minutes"].sum() / 60 if not last_7d.empty else 0

        avg_dur_recent = recent["trip_duration_minutes"].mean()
        max_dur_recent = recent["trip_duration_minutes"].max()

        avg_speed_recent = recent["avg_speed_kmph"].mean()
        speed_var = recent["avg_speed_kmph"].std() if len(recent) > 1 else 0

        # Night trips (22:00 - 05:00)
        night_mask = (last_7d["start_hour"] >= 22) | (last_7d["start_hour"] <= 5)
        night_trips_7d = night_mask.sum()

        # ETA delay patterns
        # Rate over trips with a known outcome only — unknowns (no arrival)
        # are excluded from both sides rather than counted as on-time.
        known_eta = recent["eta_met"].dropna()
        eta_delay_rate = (known_eta == 0).mean() * 100 if len(known_eta) else 0
        # Same rule as eta_met above: a trip with no known outcome has no delay
        # to report. fillna(0) would average it in as perfectly punctual and
        # drag this feature down — unknown must drop out, not read as good news.
        avg_delay = recent["eta_delay_minutes"].dropna().mean()
        if pd.isna(avg_delay):
            avg_delay = 0.0

        # Hours since last trip
        if len(group) >= 2:
            # `x or y` does NOT work here: bool(pd.NaT) is True, so a missing
            # trip_end would slip through, make hours_since NaN, and later be
            # zeroed into "back-to-back trips, no rest". trip_end is now NULL
            # for any trip that never arrived, so this must be an isna check.
            _end = group.iloc[0]["trip_end"]
            last_trip_end = group.iloc[0]["trip_start"] if pd.isna(_end) else _end
            second_last = group.iloc[1]["trip_start"]
            hours_since = (pd.to_datetime(last_trip_end) - pd.to_datetime(second_last)).total_seconds() / 3600
            hours_since = max(0, hours_since)
        else:
            hours_since = 24

        # Is current time night?
        current_hour = pd.to_datetime(now).hour if pd.notna(now) else 12
        is_night = 1 if (current_hour >= 22 or current_hour <= 5) else 0

        # Consecutive days active
        trip_dates = pd.to_datetime(group["trip_start"]).dt.date.unique()
        trip_dates = sorted(trip_dates, reverse=True)
        consecutive = 1
        for i in range(1, len(trip_dates)):
            if (trip_dates[i - 1] - trip_dates[i]).days <= 1:
                consecutive += 1
            else:
                break

        # Fatigue label (heuristic for training)
        # A driver is "fatigued" if they show these patterns:
        fatigue_score = 0
        if hours_24h > 10:
            fatigue_score += 3  # Over 10 hours driving in 24h
        elif hours_24h > 7:
            fatigue_score += 2
        if trips_24h >= 4:
            fatigue_score += 2  # Too many trips in one day
        if night_trips_7d >= 3:
            fatigue_score += 1  # Many night trips
        if consecutive >= 7:
            fatigue_score += 2  # No rest day in a week
        elif consecutive >= 5:
            fatigue_score += 1
        if hours_since < 4 and trips_24h >= 2:
            fatigue_score += 2  # Back-to-back trips, no rest
        if eta_delay_rate > 40:
            fatigue_score += 1  # High delay rate (sign of fatigue)
        if speed_var > 15:
            fatigue_score += 1  # Erratic speed (sign of fatigue)

        is_fatigued = 1 if fatigue_score >= 4 else 0

        features = {
            "driver_id": int(driver_id),
            "driver_name": str(driver_name),
            "trips_last_24h": trips_24h,
            "trips_last_7d": trips_7d,
            "hours_driving_last_24h": round(float(hours_24h), 2),
            "hours_driving_last_7d": round(float(hours_7d), 2),
            "avg_trip_duration_recent": round(float(avg_dur_recent), 2),
            "max_trip_duration_recent": round(float(max_dur_recent), 2),
            "avg_speed_recent": round(float(avg_speed_recent), 2),
            "speed_variance_recent": round(float(speed_var), 2),
            "night_trips_last_7d": int(night_trips_7d),
            "eta_delay_rate_recent": round(float(eta_delay_rate), 2),
            "avg_delay_min_recent": round(float(avg_delay), 2),
            "hours_since_last_trip": round(float(hours_since), 2),
            "is_current_night": is_night,
            "consecutive_days_active": consecutive,
            "fatigue_score": fatigue_score,
            "is_fatigued": is_fatigued,
        }
        results.append(features)

    return results


def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Driver Fatigue Predictor")
    logger.info("=" * 50)

    driver_df = fetch_driver_activity_data(conn)
    if driver_df.empty:
        logger.error("No driver activity data")
        return {"error": "No driver activity data"}

    # Ensure datetime
    driver_df["trip_start"] = pd.to_datetime(driver_df["trip_start"])
    driver_df["trip_end"] = pd.to_datetime(driver_df["trip_end"])
    driver_df["trip_duration_minutes"] = driver_df["trip_duration_minutes"].astype(float)
    driver_df["avg_speed_kmph"] = driver_df["avg_speed_kmph"].astype(float).fillna(0)
    # Keep NULL as NaN, never 0: eta_met is NULL when the trip has no recorded
    # arrival, which means "outcome unknown", not "late". Coercing it to 0 would
    # score every administratively closed trip as a delay against its driver.
    driver_df["eta_met"] = pd.to_numeric(driver_df["eta_met"], errors="coerce")
    driver_df["eta_delay_minutes"] = driver_df["eta_delay_minutes"].astype(float).fillna(0)

    logger.info(f"Processing {driver_df['driver_id'].nunique()} drivers...")

    # Build features per driver
    feature_list = build_fatigue_features(driver_df)
    if not feature_list:
        return {"error": "Could not compute fatigue features"}

    features_df = pd.DataFrame(feature_list)
    logger.info(f"Driver fatigue features: {len(features_df)} drivers")
    logger.info(f"Fatigue label balance: {features_df['is_fatigued'].value_counts().to_dict()}")

    X = features_df[FATIGUE_FEATURE_COLS].astype(float).fillna(0)
    y = features_df["is_fatigued"].astype(int)

    # Need minimum class representation
    if y.sum() < 5 or (y == 0).sum() < 5:
        logger.warning("Not enough class balance for classification, using scoring mode only")
        # Save as scoring-only model (no classifier)
        model_path = str(models_dir / "fatigue_predictor.joblib")
        joblib.dump({
            "mode": "scoring",
            "feature_columns": FATIGUE_FEATURE_COLS,
            "driver_features": feature_list,
            "generated_at": datetime.now().isoformat(),
        }, model_path)

        metrics = {
            "mode": "scoring_only",
            "drivers_analyzed": len(features_df),
            "high_fatigue_drivers": int(y.sum()),
        }
    else:
        # Train classifier
        from sklearn.model_selection import train_test_split
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y,
        )

        model = GradientBoostingClassifier(
            n_estimators=150,
            max_depth=5,
            learning_rate=0.1,
            random_state=42,
        )
        model.fit(X_train, y_train)

        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        accuracy = accuracy_score(y_test, y_pred)
        f1 = f1_score(y_test, y_pred, zero_division=0)
        auc = roc_auc_score(y_test, y_proba) if len(y_test.unique()) > 1 else 0

        logger.info(f"Accuracy: {accuracy:.4f}, F1: {f1:.4f}, AUC: {auc:.4f}")

        model_path = str(models_dir / "fatigue_predictor.joblib")
        joblib.dump({
            "mode": "classifier",
            "model": model,
            "feature_columns": FATIGUE_FEATURE_COLS,
            "driver_features": feature_list,
            "generated_at": datetime.now().isoformat(),
        }, model_path)

        metrics = {
            "mode": "classifier",
            "accuracy": round(accuracy, 4),
            "f1_score": round(f1, 4),
            "auc_roc": round(auc, 4),
            "drivers_analyzed": len(features_df),
            "high_fatigue_drivers": int(y.sum()),
        }

    # Register in DB
    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'fatigue_predictor'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'fatigue_predictor'")
        version = cur.fetchone()["max_v"] + 1

        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('fatigue_predictor', %s, 'GradientBoosting_classifier', 'is_fatigued', %s, %s, %s, %s, 1)
        """, (
            version,
            json.dumps(metrics),
            json.dumps(FATIGUE_FEATURE_COLS),
            model_path,
            len(features_df),
        ))
    conn.commit()

    # Generate fatigue alerts for high-risk drivers
    alert_count = _generate_fatigue_alerts(conn, features_df)
    metrics["fatigue_alerts_generated"] = alert_count

    logger.info(f"Fatigue predictor saved to {model_path}")
    return metrics


def _generate_fatigue_alerts(conn, features_df: pd.DataFrame) -> int:
    """Generate alerts for drivers with high fatigue scores."""
    high_risk = features_df[features_df["fatigue_score"] >= 4].sort_values("fatigue_score", ascending=False)

    if high_risk.empty:
        return 0

    count = 0
    with conn.cursor() as cur:
        for _, row in high_risk.head(50).iterrows():
            driver_id = int(row["driver_id"])
            score = int(row["fatigue_score"])

            if score >= 7:
                severity = "high"
            elif score >= 5:
                severity = "medium"
            else:
                severity = "low"

            factors = []
            if row["hours_driving_last_24h"] > 7:
                factors.append(f"{row['hours_driving_last_24h']:.0f}h driving in last 24h")
            if row["trips_last_24h"] >= 4:
                factors.append(f"{row['trips_last_24h']} trips in 24h")
            if row["consecutive_days_active"] >= 5:
                factors.append(f"{row['consecutive_days_active']} consecutive days active")
            if row["night_trips_last_7d"] >= 3:
                factors.append(f"{row['night_trips_last_7d']} night trips this week")
            if row["hours_since_last_trip"] < 4 and row["trips_last_24h"] >= 2:
                factors.append("Back-to-back trips with insufficient rest")

            message = f"Driver {row['driver_name']}: fatigue risk score {score}/10. " + "; ".join(factors)

            try:
                cur.execute("""
                    INSERT INTO alerts (alert_type, severity, driver_id, title, message, metadata)
                    VALUES ('fatigue', %s, %s, 'Driver Fatigue Risk', %s, %s)
                """, (
                    severity, driver_id, message,
                    json.dumps({"fatigue_score": score, "factors": factors}),
                ))
                count += 1
            except Exception:
                continue

    conn.commit()
    return count


def get_fleet_fatigue_status(artifact: dict) -> dict:
    """
    Get current fatigue status for all drivers.
    ONE-CLICK endpoint — returns pre-computed fatigue scores.
    """
    driver_features = artifact.get("driver_features", [])
    if not driver_features:
        return {"error": "No fatigue data available. Run training first."}

    # Categorize drivers
    critical = []
    high = []
    medium = []
    low = []

    for d in driver_features:
        score = d.get("fatigue_score", 0)
        entry = {
            "driver_id": d["driver_id"],
            "driver_name": d["driver_name"],
            "fatigue_score": score,
            "hours_driving_24h": d["hours_driving_last_24h"],
            "trips_24h": d["trips_last_24h"],
            "consecutive_days": d["consecutive_days_active"],
            "night_trips_7d": d["night_trips_last_7d"],
        }

        if score >= 7:
            entry["risk_level"] = "critical"
            critical.append(entry)
        elif score >= 5:
            entry["risk_level"] = "high"
            high.append(entry)
        elif score >= 3:
            entry["risk_level"] = "medium"
            medium.append(entry)
        else:
            entry["risk_level"] = "low"
            low.append(entry)

    # Sort each by score descending
    for lst in [critical, high, medium, low]:
        lst.sort(key=lambda x: -x["fatigue_score"])

    return {
        "total_drivers_analyzed": len(driver_features),
        "summary": {
            "critical": len(critical),
            "high": len(high),
            "medium": len(medium),
            "low": len(low),
        },
        "critical_drivers": critical[:20],
        "high_risk_drivers": high[:20],
        "generated_at": artifact.get("generated_at", "unknown"),
    }


def get_driver_fatigue(artifact: dict, driver_id: int) -> dict:
    """Get fatigue assessment for a single driver."""
    driver_features = artifact.get("driver_features", [])

    for d in driver_features:
        if d["driver_id"] == driver_id:
            score = d.get("fatigue_score", 0)
            if score >= 7:
                risk = "critical"
            elif score >= 5:
                risk = "high"
            elif score >= 3:
                risk = "medium"
            else:
                risk = "low"

            factors = []
            if d["hours_driving_last_24h"] > 7:
                factors.append(f"Driving {d['hours_driving_last_24h']:.0f}h in last 24h")
            if d["trips_last_24h"] >= 4:
                factors.append(f"{d['trips_last_24h']} trips in last 24h")
            if d["consecutive_days_active"] >= 5:
                factors.append(f"{d['consecutive_days_active']} consecutive active days")
            if d["night_trips_last_7d"] >= 3:
                factors.append(f"{d['night_trips_last_7d']} night trips this week")
            if d["hours_since_last_trip"] < 4 and d["trips_last_24h"] >= 2:
                factors.append("Insufficient rest between trips")

            return {
                "driver_id": driver_id,
                "driver_name": d["driver_name"],
                "fatigue_score": score,
                "risk_level": risk,
                "factors": factors,
                "details": {
                    "hours_driving_24h": d["hours_driving_last_24h"],
                    "hours_driving_7d": d["hours_driving_last_7d"],
                    "trips_24h": d["trips_last_24h"],
                    "trips_7d": d["trips_last_7d"],
                    "avg_speed_recent": d["avg_speed_recent"],
                    "speed_variance": d["speed_variance_recent"],
                    "night_trips_7d": d["night_trips_last_7d"],
                    "consecutive_active_days": d["consecutive_days_active"],
                    "hours_since_last_trip": d["hours_since_last_trip"],
                    "eta_delay_rate": d["eta_delay_rate_recent"],
                },
            }

    return {"error": f"No fatigue data for driver {driver_id}"}
