"""
Model 5: Route Optimizer
- Graph-based route analysis + ML-assisted travel time prediction
- Uses historical trip data to build a weighted route graph
- Finds optimal routes by minimizing expected duration (Dijkstra)
- Considers: time-of-day patterns, driver performance, day-of-week effects
- Output: recommended route, estimated time, alternatives, confidence
"""

import logging
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import heapq

import pandas as pd
import numpy as np
import joblib

from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

logger = logging.getLogger(__name__)


# ============================================
# DATA FETCHING
# ============================================

def fetch_route_graph_data(conn) -> pd.DataFrame:
    """Get all completed trips with origin/destination for building the route graph."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                lo.name AS origin,
                ld.name AS destination,
                t.trip_duration_minutes,
                t.trip_km,
                t.avg_speed_kmph,
                t.eta_met,
                t.eta_delay_minutes,
                t.trip_start,
                t.driver_id,
                t.vehicle_id,
                ds.avg_speed_kmph AS driver_avg_speed,
                ds.eta_success_rate AS driver_eta_success,
                vs.avg_speed_kmph AS vehicle_avg_speed
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            LEFT JOIN driver_summary ds ON t.driver_id = ds.driver_id
            LEFT JOIN vehicle_summary vs ON t.vehicle_id = vs.vehicle_id
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_duration_minutes < 50000
              AND t.trip_start IS NOT NULL
              AND t.eta_data_status = 'available'
            LIMIT 500000
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def fetch_multi_stop_trips(conn) -> pd.DataFrame:
    """Get trip origin/destination counts for hub analysis (lightweight query)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                lo.name AS origin,
                ld.name AS destination,
                COUNT(*) AS trip_count
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND lo.name IS NOT NULL
              AND ld.name IS NOT NULL
              AND t.eta_data_status = 'available'
            GROUP BY lo.name, ld.name
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================
# ROUTE GRAPH BUILDER
# ============================================

class RouteGraph:
    """Weighted directed graph of locations with travel time statistics."""

    def __init__(self):
        self.edges = defaultdict(list)  # origin -> [(dest, weight_stats)]
        self.locations = set()
        self.edge_stats = {}  # (origin, dest) -> stats dict

    def add_edge(self, origin: str, dest: str, stats: dict):
        self.locations.add(origin)
        self.locations.add(dest)
        self.edge_stats[(origin, dest)] = stats
        self.edges[origin].append((dest, stats["avg_duration"]))

    def shortest_path(self, start: str, end: str, max_stops: int = 5) -> dict:
        """Dijkstra's algorithm with path reconstruction."""
        if start not in self.locations or end not in self.locations:
            return {"error": f"Unknown location(s): {start}, {end}"}

        # Priority queue: (total_time, current_node, path, stops)
        pq = [(0, start, [start], 0)]
        visited = set()
        best_paths = []

        while pq and len(best_paths) < 3:  # find up to 3 paths
            total_time, current, path, stops = heapq.heappop(pq)

            if current == end:
                best_paths.append({
                    "path": path,
                    "total_duration_min": round(total_time, 2),
                    "stops": stops,
                    "segments": self._get_segments(path),
                })
                continue

            state = (current, tuple(path))
            if state in visited:
                continue
            visited.add(state)

            if stops >= max_stops:
                continue

            for neighbor, weight in self.edges.get(current, []):
                if neighbor not in path:  # avoid cycles
                    new_path = path + [neighbor]
                    heapq.heappush(pq, (total_time + weight, neighbor, new_path, stops + 1))

        if not best_paths:
            return {"error": f"No path found from {start} to {end}"}

        return {
            "origin": start,
            "destination": end,
            "recommended": best_paths[0],
            "alternatives": best_paths[1:],
            "direct_available": (start, end) in self.edge_stats,
            "direct_stats": self.edge_stats.get((start, end)),
        }

    def _get_segments(self, path: list) -> list:
        segments = []
        for i in range(len(path) - 1):
            key = (path[i], path[i + 1])
            stats = self.edge_stats.get(key, {})
            segments.append({
                "from": path[i],
                "to": path[i + 1],
                "avg_duration_min": stats.get("avg_duration", 0),
                "avg_distance_km": stats.get("avg_distance", 0),
                "trip_count": stats.get("trip_count", 0),
                "eta_success_rate": stats.get("eta_success_rate", 0),
            })
        return segments

    def get_all_locations(self) -> list:
        return sorted(self.locations)

    def get_stats(self) -> dict:
        return {
            "total_locations": len(self.locations),
            "total_edges": sum(len(v) for v in self.edges.values()),
            "avg_connections_per_location": round(
                sum(len(v) for v in self.edges.values()) / max(len(self.locations), 1), 1
            ),
        }


# ============================================
# TIME-AWARE ROUTE PREDICTOR
# ============================================

def build_time_aware_predictor(df: pd.DataFrame) -> dict:
    """Train a model to predict trip duration based on route + time features."""

    # Engineer features
    df = df.copy()
    df["trip_start"] = pd.to_datetime(df["trip_start"])
    df["hour"] = df["trip_start"].dt.hour
    df["day_of_week"] = df["trip_start"].dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["month"] = df["trip_start"].dt.month

    # Route encoding: use route avg stats as features
    route_stats = df.groupby(["origin", "destination"]).agg(
        route_avg_duration=("trip_duration_minutes", "mean"),
        route_trip_count=("trip_duration_minutes", "count"),
    ).reset_index()

    df = df.merge(route_stats, on=["origin", "destination"], how="left")

    feature_cols = [
        "hour", "day_of_week", "is_weekend", "month",
        "route_avg_duration", "route_trip_count",
        "driver_avg_speed", "driver_eta_success", "vehicle_avg_speed",
    ]

    # Ensure numeric types for all feature cols
    for col in feature_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    X = df[feature_cols].astype(float)
    y = df["trip_duration_minutes"].astype(float)

    # Remove extreme outliers
    q1, q99 = y.quantile(0.01), y.quantile(0.99)
    mask = (y >= q1) & (y <= q99)
    X, y = X[mask], y[mask]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        random_state=42,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    logger.info(f"Time-aware predictor: MAE={mae:.2f}, R2={r2:.4f}")

    return {
        "model": model,
        "feature_cols": feature_cols,
        "metrics": {"mae": round(mae, 4), "r2": round(r2, 4)},
        "route_stats": route_stats.set_index(["origin", "destination"]).to_dict("index"),
    }


# ============================================
# TRAINING ENTRY POINT
# ============================================

def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: Route Optimizer")
    logger.info("=" * 50)

    try:
        df = fetch_route_graph_data(conn)
    except Exception as e:
        logger.error(f"Failed to fetch route data: {e}")
        return {"error": f"Data fetch failed: {e}"}

    if df.empty:
        logger.error("No route data available (query returned 0 rows)")
        return {"error": "No route data"}

    logger.info(f"Route data: {len(df):,} trips")

    # Convert Decimal columns to float (MySQL returns Decimal types)
    numeric_cols = ["trip_duration_minutes", "trip_km", "avg_speed_kmph", "eta_delay_minutes",
                    "driver_avg_speed", "driver_eta_success", "vehicle_avg_speed"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "eta_met" in df.columns:
        df["eta_met"] = pd.to_numeric(df["eta_met"], errors="coerce")

    # Drop rows where origin/destination is NULL (from LEFT JOIN)
    df = df.dropna(subset=["origin", "destination"])
    logger.info(f"After dropping NULL locations: {len(df):,} trips")

    if df.empty:
        logger.error("No route data with valid locations")
        return {"error": "No route data with valid locations"}

    # 1. Build route graph
    logger.info("\n--- Building Route Graph ---")
    graph = RouteGraph()

    route_groups = df.groupby(["origin", "destination"])
    for (origin, dest), group in route_groups:
        avg_km = group["trip_km"].dropna()
        avg_speed = group["avg_speed_kmph"].dropna()
        stats = {
            "avg_duration": round(float(group["trip_duration_minutes"].mean()), 2),
            "median_duration": round(float(group["trip_duration_minutes"].median()), 2),
            "std_duration": round(float(group["trip_duration_minutes"].std()), 2),
            "avg_distance": round(float(avg_km.mean()), 2) if len(avg_km) > 0 else 0,
            "avg_speed": round(float(avg_speed.mean()), 2) if len(avg_speed) > 0 else 0,
            "trip_count": len(group),
            "eta_success_rate": round(float(group["eta_met"].mean() * 100), 2) if group["eta_met"].notna().any() else 0,
            "min_duration": round(float(group["trip_duration_minutes"].min()), 2),
            "max_duration": round(float(group["trip_duration_minutes"].max()), 2),
        }
        graph.add_edge(origin, dest, stats)

    graph_stats = graph.get_stats()
    logger.info(f"Graph: {graph_stats['total_locations']} locations, {graph_stats['total_edges']} edges")

    # 2. Train time-aware predictor
    logger.info("\n--- Training Time-Aware Predictor ---")
    predictor_result = build_time_aware_predictor(df)

    # 3. Analyze hub patterns
    logger.info("\n--- Analyzing Hub Patterns ---")
    multi_df = fetch_multi_stop_trips(conn)
    hub_analysis = {}
    if not multi_df.empty:
        # Aggregate outgoing/incoming trip counts per location
        origin_trips = multi_df.groupby("origin")["trip_count"].sum()
        dest_trips = multi_df.groupby("destination")["trip_count"].sum()
        all_locations = set(origin_trips.index) | set(dest_trips.index)

        for loc in all_locations:
            outgoing = int(origin_trips.get(loc, 0))
            incoming = int(dest_trips.get(loc, 0))
            total = outgoing + incoming
            if total >= 100:  # significant locations only
                hub_analysis[loc] = {
                    "outgoing_trips": outgoing,
                    "incoming_trips": incoming,
                    "total_trips": total,
                    "hub_score": round(min(outgoing, incoming) / max(max(outgoing, incoming), 1) * 100, 1),
                }

        # Sort by total trips
        hub_analysis = dict(
            sorted(hub_analysis.items(), key=lambda x: x[1]["total_trips"], reverse=True)[:30]
        )
        logger.info(f"Identified {len(hub_analysis)} hub locations")

    # 4. Save everything
    model_path = str(models_dir / "route_optimizer.joblib")
    joblib.dump({
        "graph": graph,
        "time_predictor": predictor_result["model"],
        "time_predictor_features": predictor_result["feature_cols"],
        "time_predictor_metrics": predictor_result["metrics"],
        "route_stats": predictor_result["route_stats"],
        "hub_analysis": hub_analysis,
        "graph_stats": graph_stats,
    }, model_path)

    logger.info(f"Route optimizer saved to {model_path}")

    # Register model
    _register_model(conn, models_dir, model_path, graph_stats, predictor_result, df)

    return {
        "graph_stats": graph_stats,
        "predictor_metrics": predictor_result["metrics"],
        "hub_locations": len(hub_analysis),
        "training_trips": len(df),
        "model_path": model_path,
    }


def _register_model(conn, models_dir, model_path, graph_stats, predictor_result, df):
    """Register route optimizer in ml_models table."""
    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'route_optimizer'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'route_optimizer'")
        version = cur.fetchone()["max_v"] + 1
        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('route_optimizer', %s, 'graph_gbrt_ensemble', 'optimal_route', %s, %s, %s, %s, 1)
            ON DUPLICATE KEY UPDATE
                metrics = VALUES(metrics),
                feature_columns = VALUES(feature_columns),
                model_artifact_path = VALUES(model_artifact_path),
                training_data_count = VALUES(training_data_count),
                is_active = 1,
                trained_at = CURRENT_TIMESTAMP
        """, (
            version,
            json.dumps({
                "graph": graph_stats,
                "predictor": predictor_result["metrics"],
            }),
            json.dumps(predictor_result["feature_cols"]),
            model_path,
            len(df),
        ))
    conn.commit()
    logger.info(f"Registered route_optimizer v{version}")


# ============================================
# SERVING / PREDICTION
# ============================================

def find_optimal_route(artifact: dict, origin: str, destination: str,
                       trip_km: float = None, hour: int = None,
                       day_of_week: int = None, driver_id: int = None) -> dict:
    """Find optimal route between two locations."""
    graph = artifact["graph"]

    # Graph-based shortest path
    result = graph.shortest_path(origin, destination)

    if "error" in result:
        return result

    # If time-aware predictor is available, enhance with time-specific estimate
    if hour is not None and artifact.get("time_predictor"):
        time_model = artifact["time_predictor"]
        feature_cols = artifact["time_predictor_features"]
        route_stats = artifact.get("route_stats", {})

        route_key = (origin, destination)
        rs = route_stats.get(route_key, {})

        features = {
            "hour": hour,
            "day_of_week": day_of_week or 0,
            "is_weekend": 1 if (day_of_week or 0) >= 5 else 0,
            "month": datetime.now().month,
            "route_avg_duration": rs.get("route_avg_duration", 0),
            "route_trip_count": rs.get("route_trip_count", 0),
            "driver_avg_speed": 0,
            "driver_eta_success": 0,
            "vehicle_avg_speed": 0,
        }

        feature_df = pd.DataFrame([features])[feature_cols].fillna(0).astype(float)
        time_prediction = float(time_model.predict(feature_df)[0])

        result["time_aware_estimate_min"] = round(max(0, time_prediction), 2)
        result["estimation_context"] = {
            "hour": hour,
            "day_of_week": day_of_week,
            "trip_km": trip_km,
        }

    return result


def get_hub_locations(artifact: dict) -> dict:
    """Return hub analysis results."""
    return artifact.get("hub_analysis", {})
