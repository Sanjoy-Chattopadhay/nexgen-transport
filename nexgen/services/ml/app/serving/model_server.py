"""
Model serving: loads active models from disk, caches in memory, serves predictions.
Handles 6 models: ETA, Anomaly, Driver Scorer, Demand Forecaster, Route Optimizer, Driver Recommender.
"""

import logging
import json
from pathlib import Path
from typing import Dict, Optional, List

import pandas as pd
import numpy as np
import joblib

from nexgen.shared.legacy_settings import settings
from nexgen.shared.legacy_db import get_conn  # noqa: F401  — re-exported for ml_service.app.main

logger = logging.getLogger(__name__)

PROJECT_ROOT = settings.PROJECT_ROOT
MODELS_DIR = Path(settings.ML_MODELS_DIR)

# In-memory model cache
_model_cache: Dict[str, object] = {}


# ============================================
# MODEL LOADING
# ============================================

def load_model(model_name: str, force_reload: bool = False):
    """Load an active, validated model. Returns the artifact, or None if barred.

    The registry is the authority on what may be served, and it did not used to
    be. This function previously fell back to ``MODELS_DIR/<name>.joblib``
    whenever the query returned nothing — so deactivating a model in the
    database did not stop it being served, it only stopped the database being
    consulted about where to find it. Quarantining a leaking model was therefore
    a no-op.

    Two conditions are now required, and neither has a disk fallback:
      * a row with is_active = 1 — which version is current
      * validation_status <> 'rejected' — whether it may be served at all
    Keeping them separate means someone re-activating a model by hand cannot
    accidentally un-reject it as a side effect.
    """
    if model_name in _model_cache and not force_reload:
        return _model_cache[model_name]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM ml_models LIKE 'validation_status'")
            has_validation = cur.fetchone() is not None

            if has_validation:
                cur.execute(
                    "SELECT model_artifact_path, validation_status, validation_reasons "
                    "FROM ml_models WHERE model_name = %s AND is_active = 1 LIMIT 1",
                    (model_name,),
                )
            else:
                cur.execute(
                    "SELECT model_artifact_path FROM ml_models "
                    "WHERE model_name = %s AND is_active = 1 LIMIT 1",
                    (model_name,),
                )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        logger.warning(
            "Refusing to serve %s: no active registry entry. It may have been "
            "quarantined by the validation gate, or never registered.",
            model_name,
        )
        return None

    if row.get("validation_status") == "rejected":
        logger.warning(
            "Refusing to serve %s: rejected by the validation gate (%s)",
            model_name, row.get("validation_reasons") or "no reason recorded",
        )
        return None

    path = row.get("model_artifact_path") or str(MODELS_DIR / f"{model_name}.joblib")

    if not Path(path).exists():
        logger.warning(f"Model file not found: {path}")
        return None

    artifact = joblib.load(path)
    _model_cache[model_name] = artifact
    logger.info(f"Loaded model: {model_name} from {path}")
    return artifact


def clear_cache(model_name: str = None):
    """Clear model cache (all or specific model)."""
    if model_name:
        _model_cache.pop(model_name, None)
    else:
        _model_cache.clear()
    logger.info(f"Cache cleared: {'all' if not model_name else model_name}")


# ============================================
# ETA PREDICTION
# ============================================

def predict_eta(features_df: pd.DataFrame) -> Optional[float]:
    """Predict trip duration in minutes."""
    artifact = load_model("eta_predictor")
    if artifact is None:
        return None

    # Handle both dict artifact (from eta_predictor.py) and raw model
    if isinstance(artifact, dict) and "model" in artifact:
        model = artifact["model"]
        feature_cols = artifact.get("feature_columns", [])
        if feature_cols:
            for col in feature_cols:
                if col not in features_df.columns:
                    features_df[col] = 0
            features_df = features_df[feature_cols]
    else:
        model = artifact

    prediction = model.predict(features_df)[0]
    return round(float(max(0, prediction)), 2)


def _osrm_estimate(origin: str, destination: str) -> dict | None:
    """Estimate distance/duration via OSRM for routes with no historical data."""
    import urllib.request
    import json as _json

    def _geo(place):
        try:
            q = urllib.request.quote(f"{place} India")
            url = f"https://nominatim.openstreetmap.org/search?q={q}&format=json&limit=1"
            req = urllib.request.Request(url, headers={"User-Agent": "smart-truck-fleet/1.0"})
            resp = urllib.request.urlopen(req, timeout=5)
            data = _json.loads(resp.read())
            return (float(data[0]["lat"]), float(data[0]["lon"])) if data else None
        except Exception:
            return None

    g1, g2 = _geo(origin), _geo(destination)
    if not g1 or not g2:
        return None
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{g1[1]},{g1[0]};{g2[1]},{g2[0]}?overview=false"
        resp = urllib.request.urlopen(url, timeout=10)
        data = _json.loads(resp.read())
        if data.get("code") == "Ok" and data.get("routes"):
            r = data["routes"][0]
            dist_km = round(r["distance"] / 1000, 1)
            truck_speed = 35  # km/h
            return {"distance_km": dist_km, "duration_minutes": round(dist_km / truck_speed * 60, 1)}
    except Exception:
        pass
    return None


def predict_eta_full(conn, trip_data: dict) -> dict:
    """Full ETA prediction with feature engineering from raw trip data."""
    from nexgen.services.ml.app.features.feature_engineering import (
        extract_temporal_features,
        get_route_features,
        get_driver_features,
        get_vehicle_features,
        get_time_pattern_features,
        build_feature_vector,
        ETA_FEATURE_COLUMNS,
    )

    # Extract all features
    temporal = extract_temporal_features(trip_data.get("trip_start"))
    origin = trip_data.get("origin", "")
    destination = trip_data.get("destination", "")

    route = get_route_features(conn, origin, destination)
    driver = get_driver_features(conn, trip_data.get("driver_id", 0))
    vehicle = get_vehicle_features(conn, trip_data.get("vehicle_id", 0))

    # Convert pandas dayofweek (0=Mon..6=Sun) → MySQL DAYOFWEEK (1=Sun..7=Sat)
    pandas_dow = temporal.get("day_of_week", 0)
    mysql_dow = (pandas_dow + 2) % 7  # Mon=0→2, Tue=1→3, ..., Sun=6→1
    if mysql_dow == 0:
        mysql_dow = 7

    time_pattern = get_time_pattern_features(
        conn, origin, destination,
        temporal.get("hour", 0),
        mysql_dow,
    )

    # ── OSRM fallback for routes with no history ──
    osrm_used = False
    osrm_estimate = None
    route_avg_dur = route.get("route_avg_duration")
    route_avg_dist = route.get("route_avg_distance")
    route_eta_success = route.get("route_eta_success")
    route_trip_count = route.get("route_trip_count")

    if not route_avg_dur and not route_avg_dist:
        # No route history at all — estimate via OSRM
        logger.info("No route history for %s → %s, trying OSRM estimate", origin, destination)
        osrm_estimate = _osrm_estimate(origin, destination)
        if osrm_estimate:
            osrm_used = True
            route_avg_dist = osrm_estimate["distance_km"]
            route_avg_dur = osrm_estimate["duration_minutes"]
            route["route_avg_distance"] = route_avg_dist
            route["route_avg_duration"] = route_avg_dur
            route["route_avg_speed"] = 35.0
            route["route_eta_success"] = 70.0  # reasonable default
            route_eta_success = 70.0
            logger.info("OSRM estimate: %.1f km, %.1f min", route_avg_dist, route_avg_dur)

    # ── Fall back missing time_pattern features to route averages ──
    if time_pattern.get("time_pattern_avg_duration") is None:
        time_pattern["time_pattern_avg_duration"] = route_avg_dur
    if time_pattern.get("time_pattern_trip_count") is None or time_pattern["time_pattern_trip_count"] == 0:
        time_pattern["time_pattern_trip_count"] = route_trip_count
    if time_pattern.get("time_pattern_eta_success") is None:
        time_pattern["time_pattern_eta_success"] = route_eta_success

    # ── Fall back missing trip_km to route average distance ──
    trip_km = trip_data.get("trip_km")
    if trip_km is None or trip_km == 0:
        trip_km = route_avg_dist

    # ── Fill missing driver/vehicle features with global averages ──
    if driver.get("driver_avg_duration") is None and route_avg_dur:
        driver["driver_avg_duration"] = route_avg_dur
    if driver.get("driver_avg_speed") is None:
        driver["driver_avg_speed"] = route.get("route_avg_speed") or 20.0
    if driver.get("driver_eta_success") is None and route_eta_success:
        driver["driver_eta_success"] = route_eta_success

    if vehicle.get("vehicle_avg_speed") is None:
        vehicle["vehicle_avg_speed"] = route.get("route_avg_speed") or 20.0
    if vehicle.get("vehicle_eta_success") is None and route_eta_success:
        vehicle["vehicle_eta_success"] = route_eta_success

    # Detect 5 AM default timestamp
    ts = trip_data.get("trip_start")
    is_5am = 0
    if ts:
        from datetime import datetime as _dt
        try:
            dt_parsed = pd.to_datetime(ts) if not isinstance(ts, _dt) else ts
            if dt_parsed.hour == 5 and dt_parsed.minute == 0 and dt_parsed.second == 0:
                is_5am = 1
        except Exception:
            pass

    feature_df = build_feature_vector(
        temporal, route, driver, vehicle, time_pattern,
        trip_km=trip_km,
        is_5am_default=is_5am,
    )

    # Ensure columns match — fill remaining NaN with route-level defaults, not 0
    for col in ETA_FEATURE_COLUMNS:
        if col not in feature_df.columns:
            feature_df[col] = 0
    feature_df = feature_df[ETA_FEATURE_COLUMNS]

    # Smart fill: use route_avg_duration for duration-related NaN, else 0
    if route_avg_dur:
        for col in ["time_pattern_avg_duration", "driver_avg_duration"]:
            if col in feature_df.columns:
                feature_df[col] = feature_df[col].fillna(route_avg_dur)
    feature_df = feature_df.fillna(0).astype(float)

    predicted_duration = predict_eta(feature_df)

    result = {
        "predicted_duration_minutes": predicted_duration,
        "features_used": {k: round(float(v), 4) for k, v in feature_df.iloc[0].to_dict().items()},
        "route_avg_duration": route.get("route_avg_duration"),
        "driver_avg_duration": driver.get("driver_avg_duration"),
    }

    # If OSRM was used and model returned 0 or None, fall back to OSRM estimate
    if osrm_used:
        result["estimation_source"] = "osrm_estimate"
        result["osrm_distance_km"] = osrm_estimate["distance_km"]
        if predicted_duration is None or predicted_duration <= 0:
            result["predicted_duration_minutes"] = osrm_estimate["duration_minutes"]
            result["estimation_source"] = "osrm_fallback"

    return result


# ============================================
# ANOMALY DETECTION
# ============================================

def scan_anomalies(conn, days: int = 7) -> dict:
    """Batch scan recent trips for anomalies. One-click endpoint."""
    from nexgen.services.ml.app.models.anomaly_detector import scan_recent_trips
    return scan_recent_trips(conn, days=days)


# ============================================
# DRIVER SCORING
# ============================================

def get_driver_score(conn, driver_id: int) -> Optional[dict]:
    """Get the latest driver score from predictions table."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.predicted_value AS composite_score, p.input_features, p.created_at
            FROM predictions p
            JOIN ml_models m ON p.model_id = m.id
            WHERE m.model_name = 'driver_scorer' AND m.is_active = 1
              AND p.driver_id = %s
            ORDER BY p.created_at DESC
            LIMIT 1
        """, (driver_id,))
        row = cur.fetchone()

    if not row:
        return None

    scores = json.loads(row["input_features"]) if isinstance(row["input_features"], str) else row["input_features"]

    return {
        "driver_id": driver_id,
        "composite_score": float(row["composite_score"]),
        "scores": scores,
        "scored_at": str(row["created_at"]),
    }


def get_all_driver_scores(conn, limit: int = 100) -> list:
    """Get all driver scores, sorted by composite score."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.driver_id, d.name AS driver_name,
                   p.predicted_value AS composite_score, p.input_features
            FROM predictions p
            JOIN ml_models m ON p.model_id = m.id
            LEFT JOIN drivers d ON p.driver_id = d.id
            WHERE m.model_name = 'driver_scorer' AND m.is_active = 1
              AND p.prediction_type = 'driver_score'
            ORDER BY p.predicted_value DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()

    results = []
    for row in rows:
        scores = json.loads(row["input_features"]) if isinstance(row["input_features"], str) else row["input_features"]
        results.append({
            "driver_id": row["driver_id"],
            "driver_name": row["driver_name"],
            "composite_score": float(row["composite_score"]),
            "risk_level": scores.get("risk_level", "unknown"),
            "scores": scores,
        })
    return results


# ============================================
# DEMAND FORECASTING
# ============================================

def get_demand_forecast(route: str = None) -> dict:
    """Get demand forecasts (all routes or specific route)."""
    artifact = load_model("demand_forecaster")
    if artifact is None:
        return unavailable("demand_forecaster")

    forecasts = artifact.get("forecasts", {})
    generated_at = artifact.get("generated_at", "unknown")

    if route:
        if route in forecasts:
            return {"route": route, "forecast": forecasts[route], "generated_at": generated_at}
        return {"error": f"No forecast for route: {route}"}

    return {
        "routes_count": len(forecasts),
        "generated_at": generated_at,
        "forecasts": forecasts,
    }


# ============================================
# ROUTE OPTIMIZATION
# ============================================

def find_optimal_route(origin: str, destination: str,
                       trip_km: float = None, hour: int = None,
                       day_of_week: int = None) -> dict:
    """Find optimal route between locations."""
    artifact = load_model("route_optimizer")
    if artifact is None:
        return unavailable("route_optimizer")

    from nexgen.services.ml.app.models.route_optimizer import find_optimal_route as _find
    return _find(artifact, origin, destination, trip_km, hour, day_of_week)


def get_hub_locations() -> dict:
    """Get hub analysis from route optimizer."""
    artifact = load_model("route_optimizer")
    if artifact is None:
        return unavailable("route_optimizer")

    from nexgen.services.ml.app.models.route_optimizer import get_hub_locations as _get_hubs
    return _get_hubs(artifact)


# ============================================
# DRIVER RECOMMENDER
# ============================================

def recommend_drivers(origin: str, destination: str, top_n: int = 10) -> dict:
    """Recommend best drivers for a given route."""
    artifact = load_model("driver_recommender")
    if artifact is None:
        return unavailable("driver_recommender")

    from nexgen.services.ml.app.models.driver_recommender import recommend_drivers as _recommend
    return _recommend(artifact, origin, destination, top_n)


# ============================================
# TRIP FORECASTING
# ============================================

def get_trip_forecast(route: str = None) -> dict:
    """Get trip forecasts (fleet-wide or specific route)."""
    artifact = load_model("demand_forecaster")
    if artifact is None:
        return unavailable("demand_forecaster")

    forecasts = artifact.get("forecasts", {})
    fleet_forecast = artifact.get("fleet_forecast", {})
    generated_at = artifact.get("generated_at", "unknown")

    if route:
        if route in forecasts:
            return {"route": route, "forecast": forecasts[route], "generated_at": generated_at}
        return {"error": f"No forecast for route: {route}"}

    # Fleet-wide summary
    return {
        "fleet_forecast": fleet_forecast,
        "routes_count": len(forecasts),
        "generated_at": generated_at,
        "top_routes": {r: forecasts[r] for r in list(forecasts.keys())[:10]},
    }


# ============================================
# MODEL INFO & LISTING
# ============================================

def model_status(model_name: str) -> dict:
    """Why a model is or is not servable, in a form an API can return.

    Filtering the registry to `is_active = 1` made quarantined models invisible,
    so the only thing a caller saw was a 404 and an instruction to retrain —
    which does not help, because a leaking model retrains into another
    rejection. The reason has to travel with the refusal.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM ml_models LIKE 'validation_status'")
            has_validation = cur.fetchone() is not None

            cols = ("id, model_name, version, model_type, target_variable, metrics, "
                    "training_data_count, is_active, trained_at"
                    + (", validation_status, validation_reasons, validated_at"
                       if has_validation else ""))
            cur.execute(
                f"SELECT {cols} FROM ml_models WHERE model_name = %s "
                "ORDER BY version DESC LIMIT 1", (model_name,))
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {"model_name": model_name, "servable": False,
                "state": "never_trained",
                "explanation": f"No model named '{model_name}' has ever been "
                               "registered. Train it first."}

    status = row.get("validation_status")
    reasons = row.get("validation_reasons")
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except (TypeError, ValueError):
            reasons = [reasons]

    if status == "rejected":
        return {
            "model_name": model_name, "version": row["version"],
            "servable": False, "state": "quarantined",
            "rejected_because": reasons or [],
            "trained_at": str(row.get("trained_at")),
            "explanation": (
                "This model was trained successfully but did not pass validation, "
                "so it is deliberately not served. Retraining it on the same data "
                "will produce the same rejection — the reasons above describe a "
                "data problem, not a training problem."
            ),
            "next_step": "See REMEDIATION_REPORT.md, or run scripts/audit_models.py",
        }

    if not row.get("is_active"):
        return {"model_name": model_name, "version": row["version"],
                "servable": False, "state": "inactive",
                "explanation": "A newer version superseded this one, or it was "
                               "deactivated by hand."}

    return {"model_name": model_name, "version": row["version"],
            "servable": True, "state": "active"}


def unavailable(model_name: str) -> dict:
    """The error body returned when a prediction endpoint cannot serve."""
    return {"error": f"{model_name} is not available", **model_status(model_name)}


def get_model_info(model_name: str) -> Optional[dict]:
    """Get model metadata from database."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, model_name, version, model_type, target_variable,
                       metrics, feature_columns, training_data_count,
                       is_active, trained_at
                FROM ml_models
                WHERE model_name = %s AND is_active = 1
                LIMIT 1
            """, (model_name,))
            row = cur.fetchone()
    finally:
        conn.close()

    if row:
        row["trained_at"] = str(row["trained_at"]) if row["trained_at"] else None
        if isinstance(row.get("metrics"), str):
            row["metrics"] = json.loads(row["metrics"])
        if isinstance(row.get("feature_columns"), str):
            row["feature_columns"] = json.loads(row["feature_columns"])
    return row


def list_all_models() -> list:
    """List all models, each carrying why it is or is not served.

    "0 active" on its own reads as a broken deployment. It is not — it is six
    models that were checked and failed. The list has to say which, and why.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW COLUMNS FROM ml_models LIKE 'validation_status'")
            has_validation = cur.fetchone() is not None

            extra = (", validation_status, validation_reasons, validated_at"
                     if has_validation else "")
            cur.execute(f"""
                SELECT id, model_name, version, model_type, target_variable,
                       metrics, training_data_count, is_active, trained_at{extra}
                FROM ml_models
                ORDER BY model_name, version DESC
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    for r in rows:
        r["trained_at"] = str(r["trained_at"]) if r["trained_at"] else None
        if isinstance(r.get("metrics"), str):
            r["metrics"] = json.loads(r["metrics"])
        if isinstance(r.get("validation_reasons"), str):
            try:
                r["validation_reasons"] = json.loads(r["validation_reasons"])
            except (TypeError, ValueError):
                pass
        r["servable"] = bool(r.get("is_active")) and \
            r.get("validation_status") != "rejected"
    return rows


def registry_status() -> dict:
    """One call that answers "why is nothing active"."""
    models = list_all_models()
    servable = [m for m in models if m["servable"]]
    rejected = [m for m in models if m.get("validation_status") == "rejected"]

    return {
        "total_registered": len(models),
        "servable": len(servable),
        "rejected": len(rejected),
        "healthy": bool(servable),
        "explanation": (
            f"{len(servable)} of {len(models)} registered models can be served."
            if servable else
            f"No model is currently served. All {len(rejected)} were trained "
            "successfully but failed validation — this is the gate working, not "
            "a deployment failure. Each model below carries its reasons."
        ),
        "models": [
            {
                "model_name": m["model_name"], "version": m["version"],
                "servable": m["servable"],
                "state": ("active" if m["servable"]
                          else "quarantined" if m.get("validation_status") == "rejected"
                          else "inactive"),
                "reasons": m.get("validation_reasons") or [],
                "trained_at": m["trained_at"],
                "training_rows": m.get("training_data_count"),
            }
            for m in models
        ],
        "next_step": (
            None if servable else
            "The rejections describe a data problem, not a training problem. "
            "See REMEDIATION_REPORT.md; retraining on the same data reproduces them."
        ),
    }


# ============================================
# SLA PREDICTION
# ============================================

def predict_sla(conn, trip_data: dict) -> dict:
    """Predict whether a trip will meet its ETA."""
    artifact = load_model("sla_predictor")
    if artifact is None:
        return unavailable("sla_predictor")

    from nexgen.services.ml.app.models.sla_predictor import predict_sla as _predict
    return _predict(artifact, conn, trip_data)


# ============================================
# DRIVER FATIGUE
# ============================================

def get_fleet_fatigue(conn=None) -> dict:
    """Get fatigue status for all drivers."""
    artifact = load_model("fatigue_predictor")
    if artifact is None:
        return unavailable("fatigue_predictor")

    from nexgen.services.ml.app.models.fatigue_predictor import get_fleet_fatigue_status
    return get_fleet_fatigue_status(artifact)


def get_driver_fatigue(driver_id: int) -> dict:
    """Get fatigue assessment for a single driver."""
    artifact = load_model("fatigue_predictor")
    if artifact is None:
        return unavailable("fatigue_predictor")

    from nexgen.services.ml.app.models.fatigue_predictor import get_driver_fatigue as _get
    return _get(artifact, driver_id)


# ============================================
# CLIENT DEMAND FORECASTING
# ============================================

def get_client_forecast(client: str = None) -> dict:
    """Get demand forecast for a specific client or all clients."""
    artifact = load_model("client_demand_forecaster")
    if artifact is None:
        return {"error": "Client demand forecaster not loaded. Train via POST /ml/train/client_demand_forecaster"}

    from nexgen.services.ml.app.models.client_demand_forecaster import get_client_forecast as _get
    return _get(artifact, client)


def get_client_profile(client: str) -> dict:
    """Get detailed profile for a client."""
    artifact = load_model("client_demand_forecaster")
    if artifact is None:
        return {"error": "Client demand forecaster not loaded. Train first."}

    from nexgen.services.ml.app.models.client_demand_forecaster import get_client_profile as _get
    return _get(artifact, client)


def list_clients() -> dict:
    """List all known clients with stats."""
    artifact = load_model("client_demand_forecaster")
    if artifact is None:
        return {"error": "Client demand forecaster not loaded. Train first."}

    from nexgen.services.ml.app.models.client_demand_forecaster import list_clients as _list
    return _list(artifact)


def get_model_comparison() -> dict:
    """Compare all active models side by side."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_name, version, model_type, metrics,
                       training_data_count, trained_at
                FROM ml_models
                WHERE is_active = 1
                ORDER BY model_name
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    comparison = {}
    for r in rows:
        metrics = json.loads(r["metrics"]) if isinstance(r["metrics"], str) else (r["metrics"] or {})
        comparison[r["model_name"]] = {
            "version": r["version"],
            "model_type": r["model_type"],
            "training_data": r["training_data_count"],
            "trained_at": str(r["trained_at"]) if r["trained_at"] else None,
            "metrics": metrics,
        }

    return comparison
