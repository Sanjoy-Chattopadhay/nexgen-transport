"""
Smart-Truck ML Service API v3
Serves predictions from all trained ML/DL models.
New in v3: SLA prediction, fatigue monitoring, batch anomaly scan, training tiers.

Run with:
    uvicorn ml_service.app.main:app --host 0.0.0.0 --port 8001 --reload
"""

import time
import logging

from fastapi import FastAPI, HTTPException, Query, Request, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

from nexgen.services.ml.compat import setup_logging
from nexgen.services.ml.api_keys import authenticate

setup_logging(service_name="ml-service")
logger = logging.getLogger(__name__)

from nexgen.services.ml.app.serving.model_server import (
    predict_eta_full,
    scan_anomalies,
    get_driver_score,
    get_all_driver_scores,
    get_demand_forecast,
    find_optimal_route,
    get_hub_locations,
    recommend_drivers,
    get_trip_forecast,
    predict_sla,
    get_fleet_fatigue,
    get_driver_fatigue,
    get_client_forecast,
    get_client_profile,
    list_clients,
    get_model_info,
    list_all_models,
    get_model_comparison,
    load_model,
    clear_cache,
    get_conn,
)

app = FastAPI(
    title="Smart-Truck ML Subscription API",
    description=(
        "Machine Learning model serving for fleet analytics. "
        "Provides predictions from 10 trained ML models.\n\n"
        "**Authentication:** send your subscription key in the `X-API-Key` header. "
        "Access to each model is gated by your tier (basic / pro / enterprise). "
        "See SUBSCRIPTION_GUIDE.md and API_REFERENCE.md for details."
    ),
    version="3.1.0",
    docs_url="/docs",
    # Global auth dependency. `authenticate` lets public paths (health, docs, /ml landing)
    # through, validates the X-API-Key for everything else, and enforces tier access.
    dependencies=[Depends(authenticate)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request logging middleware ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    logger.info(">>> %s %s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("!!! %s %s  UNHANDLED EXCEPTION", request.method, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    client = getattr(request.state, "api_client", None)
    client_label = f"{client.name}/{client.tier}" if client else "-"
    logger.info(
        "<<< %s %s  %s  %.0fms  client=%s",
        request.method, request.url.path, response.status_code, elapsed_ms, client_label,
    )
    return response


logger.info("ML Service ready  routes=%d", len(app.routes))


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class ETAPredictRequest(BaseModel):
    origin: str = Field(..., description="Origin location name")
    destination: str = Field(..., description="Destination location name")
    driver_id: Optional[int] = Field(None, description="Driver ID")
    vehicle_id: Optional[int] = Field(None, description="Vehicle ID")
    trip_km: Optional[float] = Field(None, description="Trip distance in km")
    trip_start: Optional[str] = Field(None, description="Trip start datetime (ISO format)")


class SLAPredictRequest(BaseModel):
    origin: str = Field(..., description="Origin location name")
    destination: str = Field(..., description="Destination location name")
    driver_id: Optional[int] = Field(None, description="Driver ID")
    vehicle_id: Optional[int] = Field(None, description="Vehicle ID")
    trip_km: Optional[float] = Field(None, description="Trip distance in km")
    trip_start: Optional[str] = Field(None, description="Trip start datetime (ISO format)")


class RouteOptimizeRequest(BaseModel):
    origin: str
    destination: str
    trip_km: Optional[float] = None
    hour: Optional[int] = Field(None, ge=0, le=23)
    day_of_week: Optional[int] = Field(None, ge=0, le=6)


class DriverRecommendRequest(BaseModel):
    origin: str = Field(..., description="Origin location name")
    destination: str = Field(..., description="Destination location name")
    top_n: int = Field(10, ge=1, le=100, description="Number of drivers to recommend")


# ============================================
# HEALTH & INFO
# ============================================

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "smart-truck-ml", "version": "3.0.0",
            "timestamp": datetime.now().isoformat()}


@app.get("/ml")
def ml_root():
    return {
        "message": "Smart-Truck ML Subscription API v3.1",
        "authentication": "Send your subscription key in the X-API-Key header.",
        "tiers": {
            "basic":      "ETA, driver scores, demand/trip forecasts",
            "pro":        "basic + SLA, anomalies, fatigue, route optimizer, recommender, hubs",
            "enterprise": "pro + client forecaster + model training/cache management",
        },
        "models": {
            "eta_predictor": "POST /ml/predict/eta - Predict trip ETA",
            "sla_predictor": "POST /ml/predict/sla - Will delivery be on time? (probability + risk)",
            "anomaly_detector": "POST /ml/scan/anomalies - Batch scan recent trips for anomalies",
            "driver_scorer": "GET /ml/drivers/scores - Driver performance scores",
            "fatigue_predictor": "GET /ml/drivers/fatigue - Fleet fatigue status",
            "demand_forecaster": "GET /ml/forecast/demand - Route demand forecast",
            "route_optimizer": "POST /ml/optimize/route - Find optimal route",
            "driver_recommender": "POST /ml/recommend/drivers - Recommend best drivers for a route",
            "trip_forecaster": "GET /ml/forecast/trips - Forecast expected trips next week",
            "client_demand_forecaster": "GET /ml/clients/forecast - Client/company demand forecast",
        },
        "management": {
            "models": "GET /ml/models - List all models",
            "comparison": "GET /ml/models/comparison - Compare active models",
            "train": "POST /ml/train/{model_name} - Train a model",
            "train_all": "POST /ml/train-all - Train all models",
            "train_tier": "POST /ml/train-tier/{tier} - Run scheduled training tier (daily/weekly/monthly)",
        },
    }


# ============================================
# ETA PREDICTION
# ============================================

@app.post("/ml/predict/eta")
def predict_eta_endpoint(request: ETAPredictRequest):
    """Predict trip duration using ETA model with full feature engineering."""
    conn = get_conn()
    try:
        result = predict_eta_full(conn, {
            "origin": request.origin,
            "destination": request.destination,
            "driver_id": request.driver_id,
            "vehicle_id": request.vehicle_id,
            "trip_km": request.trip_km,
            "trip_start": request.trip_start or datetime.now().isoformat(),
        })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ============================================
# SLA PREDICTION (NEW)
# ============================================

@app.post("/ml/predict/sla")
def predict_sla_endpoint(request: SLAPredictRequest):
    """Predict whether a trip will meet its ETA.
    Returns: on_time_probability, prediction, risk_level, contributing_factors."""
    conn = get_conn()
    try:
        result = predict_sla(conn, {
            "origin": request.origin,
            "destination": request.destination,
            "driver_id": request.driver_id,
            "vehicle_id": request.vehicle_id,
            "trip_km": request.trip_km,
            "trip_start": request.trip_start or datetime.now().isoformat(),
        })
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ============================================
# ANOMALY DETECTION (BATCH SCAN — replaces manual predict)
# ============================================

@app.post("/ml/scan/anomalies")
def scan_anomalies_endpoint(days: int = Query(7, ge=1, le=90, description="Scan trips from last N days")):
    """One-click anomaly scan: scores recent trips, creates alerts.
    No manual input needed — just click. Returns summary of findings."""
    conn = get_conn()
    try:
        result = scan_anomalies(conn, days=days)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()


# ============================================
# DRIVER SCORING
# ============================================

@app.get("/ml/drivers/scores")
def get_driver_scores(limit: int = Query(100, ge=1, le=1000)):
    """Get all driver scores ranked by composite score."""
    conn = get_conn()
    try:
        scores = get_all_driver_scores(conn, limit)
        return {"count": len(scores), "drivers": scores}
    finally:
        conn.close()


@app.get("/ml/drivers/{driver_id}/score")
def get_single_driver_score(driver_id: int):
    """Get score for a specific driver."""
    conn = get_conn()
    try:
        score = get_driver_score(conn, driver_id)
        if not score:
            raise HTTPException(status_code=404, detail=f"No score found for driver {driver_id}")
        return score
    finally:
        conn.close()


# ============================================
# DRIVER FATIGUE (NEW)
# ============================================

@app.get("/ml/drivers/fatigue")
def get_fleet_fatigue_endpoint():
    """Get fatigue risk status for all drivers.
    Returns: summary (critical/high/medium/low counts), top at-risk drivers."""
    try:
        result = get_fleet_fatigue()
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/drivers/{driver_id}/fatigue")
def get_driver_fatigue_endpoint(driver_id: int):
    """Get fatigue assessment for a specific driver."""
    try:
        result = get_driver_fatigue(driver_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# DEMAND FORECASTING
# ============================================

@app.get("/ml/forecast/demand")
def forecast_demand(route: Optional[str] = Query(None, description="Route in 'Origin -> Destination' format")):
    """Get demand forecasts for routes."""
    try:
        return get_demand_forecast(route)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# ROUTE OPTIMIZATION
# ============================================

@app.post("/ml/optimize/route")
def optimize_route(request: RouteOptimizeRequest):
    """Find optimal route between two locations."""
    try:
        result = find_optimal_route(
            request.origin, request.destination,
            request.trip_km, request.hour, request.day_of_week,
        )
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/optimize/hubs")
def get_hubs():
    """Get hub location analysis."""
    try:
        return get_hub_locations()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# DRIVER RECOMMENDER
# ============================================

@app.post("/ml/recommend/drivers")
def recommend_drivers_endpoint(request: DriverRecommendRequest):
    """Recommend best-suited drivers for a route.
    v2: Prioritizes drivers with actual route experience.
    Returns separate sections for experienced vs similar-route drivers."""
    try:
        result = recommend_drivers(request.origin, request.destination, request.top_n)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# TRIP FORECASTING
# ============================================

@app.get("/ml/forecast/trips")
def forecast_trips(route: Optional[str] = Query(None, description="Route in 'Origin -> Destination' format")):
    """Forecast expected number of trips for next week (fleet-wide or per route)."""
    try:
        result = get_trip_forecast(route)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CLIENT DEMAND FORECASTING
# ============================================

@app.get("/ml/clients")
def list_clients_endpoint():
    """List all known clients with trip stats and forecast availability."""
    try:
        result = list_clients()
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/clients/forecast")
def get_client_forecast_endpoint(
    client: Optional[str] = Query(None, description="Client/company name (partial match supported)")
):
    """Get demand forecast for a client or all clients.
    Returns: predicted trips for next 7 days, trend, growth rate."""
    try:
        result = get_client_forecast(client)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/clients/{client_name}/profile")
def get_client_profile_endpoint(client_name: str):
    """Get detailed profile for a client: top routes, seasonal patterns, volume stats."""
    try:
        result = get_client_profile(client_name)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# MODEL MANAGEMENT
# ============================================

@app.get("/ml/models")
def list_models():
    """List all trained models with their versions, metrics and servability."""
    try:
        models = list_all_models()
        servable = sum(1 for m in models if m.get("servable"))
        return {"count": len(models), "servable": servable, "models": models}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/registry/status")
def registry_health():
    """Why models are or are not being served.

    Exists because "0 active models" on a dashboard is indistinguishable from a
    broken deployment, and in this case it is the opposite: every model was
    checked and every one failed. The distinction matters enough to have its own
    endpoint.
    """
    try:
        from nexgen.services.ml.app.serving.model_server import registry_status
        return registry_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/models/comparison")
def compare_models():
    """Compare all active models side by side."""
    try:
        return get_model_comparison()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/ml/models/{model_name}")
def get_model_details(model_name: str):
    """Get detailed info about a specific model."""
    try:
        info = get_model_info(model_name)
        if not info:
            # A bare 404 sent people to retrain a quarantined model, which
            # reproduces the rejection. Return the reason instead.
            from nexgen.services.ml.app.serving.model_server import model_status
            status = model_status(model_name)
            if status["state"] == "never_trained":
                raise HTTPException(status_code=404, detail=status["explanation"])
            return {"model_name": model_name, "active": False, **status}
        return info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# TRAINING ENDPOINTS
# ============================================

@app.post("/ml/train/{model_name}")
def train_model(model_name: str, background_tasks: BackgroundTasks):
    """Trigger training for a specific model (runs in background)."""
    from nexgen.services.ml.app.training.train_pipeline import MODEL_REGISTRY, train_single

    if model_name not in MODEL_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model: {model_name}. Available: {list(MODEL_REGISTRY.keys())}",
        )

    def _train():
        result = train_single(model_name)
        clear_cache(model_name)
        return result

    background_tasks.add_task(_train)
    return {
        "status": "started",
        "model": model_name,
        "description": MODEL_REGISTRY[model_name]["description"],
        "message": f"Training {model_name} in background. Check /ml/models/{model_name} for status.",
    }


@app.post("/ml/train/{model_name}/sync")
def train_model_sync(model_name: str):
    """Trigger training for a specific model (blocks until complete)."""
    from nexgen.services.ml.app.training.train_pipeline import MODEL_REGISTRY, train_single

    if model_name not in MODEL_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model: {model_name}. Available: {list(MODEL_REGISTRY.keys())}",
        )

    result = train_single(model_name)
    clear_cache(model_name)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return result


@app.post("/ml/train-all")
def train_all_models(background_tasks: BackgroundTasks):
    """Trigger training for ALL models (runs in background)."""
    from nexgen.services.ml.app.training.train_pipeline import train_all

    def _train():
        result = train_all()
        clear_cache()
        return result

    background_tasks.add_task(_train)
    return {
        "status": "started",
        "message": "Training all 9 models in background. Check /ml/models for progress.",
        "models": [
            "eta_predictor", "anomaly_detector", "driver_scorer",
            "demand_forecaster", "route_optimizer", "driver_recommender",
            "sla_predictor", "fatigue_predictor", "client_demand_forecaster",
        ],
    }


@app.post("/ml/train-tier/{tier}")
def train_tier_endpoint(tier: str, background_tasks: BackgroundTasks):
    """Run a scheduled training tier: daily, weekly, or monthly.
    - daily: driver scores, recommender, fatigue (fast)
    - weekly: ETA, anomaly, demand, SLA (heavier)
    - monthly: all models (full retrain)"""
    from nexgen.services.ml.app.training.train_pipeline import TRAINING_TIERS, train_tier

    if tier not in TRAINING_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tier: {tier}. Available: {list(TRAINING_TIERS.keys())}",
        )

    def _train():
        result = train_tier(tier)
        clear_cache()
        return result

    background_tasks.add_task(_train)
    tier_info = TRAINING_TIERS[tier]
    return {
        "status": "started",
        "tier": tier,
        "description": tier_info["description"],
        "models": tier_info["models"],
        "message": f"Running {tier} training tier in background.",
    }


@app.get("/ml/training/readiness")
def check_training_readiness():
    """Check if the database has enough data for model training."""
    from nexgen.services.ml.app.training.train_pipeline import check_readiness
    try:
        return check_readiness()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CACHE MANAGEMENT
# ============================================

@app.post("/ml/cache/clear")
def clear_model_cache(model_name: Optional[str] = None):
    """Clear model cache (all or specific model)."""
    clear_cache(model_name)
    return {"status": "ok", "cleared": model_name or "all"}
