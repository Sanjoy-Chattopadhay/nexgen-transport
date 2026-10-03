"""
Deep Learning Model: LSTM Trip Duration Sequence Predictor
- Uses sequential trip patterns (per driver) to predict next trip duration
- Architecture: LSTM (Long Short-Term Memory) network
- Captures: temporal patterns, driver habits, route sequences
- Input: sequence of last N trips for a driver
- Output: predicted duration for next trip + confidence interval
- Framework: PyTorch (lightweight, no TF dependency)
"""

import logging
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import joblib

logger = logging.getLogger(__name__)

# Try to import PyTorch; if not available, fall back to a simple RNN-like approach with numpy
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import Dataset, DataLoader
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    logger.warning("PyTorch not installed. LSTM will use numpy-based fallback.")


# ============================================
# CONSTANTS
# ============================================

SEQUENCE_LENGTH = 10       # number of past trips in each sequence
HIDDEN_DIM = 64
NUM_LAYERS = 2
BATCH_SIZE = 128
EPOCHS = 30
LEARNING_RATE = 0.001

# Features per timestep in the sequence
SEQUENCE_FEATURES = [
    "trip_duration_minutes",
    "trip_km",
    "avg_speed_kmph",
    "eta_delay_minutes",
    "hour",
    "day_of_week",
    "is_weekend",
]


# ============================================
# DATA FETCHING
# ============================================

def fetch_sequential_data(conn) -> pd.DataFrame:
    """Fetch driver trips in chronological order for sequence building."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                t.driver_id,
                t.trip_duration_minutes,
                t.trip_km,
                t.avg_speed_kmph,
                t.eta_delay_minutes,
                t.trip_start,
                lo.name AS origin_name,
                ld.name AS destination_name
            FROM trips t
            LEFT JOIN locations lo ON t.origin_id = lo.id
            LEFT JOIN locations ld ON t.destination_id = ld.id
            WHERE t.trip_duration_minutes IS NOT NULL
              AND t.trip_duration_minutes > 0
              AND t.trip_duration_minutes < 50000
              AND t.trip_start IS NOT NULL
              AND t.driver_id IS NOT NULL
            ORDER BY t.driver_id, t.trip_start
            LIMIT 500000
        """)
        rows = cur.fetchall()
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ============================================
# SEQUENCE BUILDER
# ============================================

def build_sequences(df: pd.DataFrame, seq_length: int = SEQUENCE_LENGTH) -> tuple:
    """
    Build sequential training data: for each driver, create sliding windows
    of seq_length trips, predicting the next trip's duration.
    Returns (X_sequences, y_targets, scalers_info)
    """
    df = df.copy()
    df["trip_start"] = pd.to_datetime(df["trip_start"])
    df["hour"] = df["trip_start"].dt.hour
    df["day_of_week"] = df["trip_start"].dt.dayofweek
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)

    # Fill NaN
    for col in SEQUENCE_FEATURES:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(float)

    # Normalize features (per-feature z-score)
    scaler_params = {}
    for col in SEQUENCE_FEATURES:
        mean = df[col].mean()
        std = df[col].std() or 1
        df[col + "_norm"] = (df[col] - mean) / std
        scaler_params[col] = {"mean": float(mean), "std": float(std)}

    norm_features = [c + "_norm" for c in SEQUENCE_FEATURES]

    # Build sequences per driver
    X_list = []
    y_list = []

    driver_groups = df.groupby("driver_id")
    for driver_id, group in driver_groups:
        if len(group) < seq_length + 1:
            continue

        values = group[norm_features].values
        targets = group["trip_duration_minutes"].values

        for i in range(len(group) - seq_length):
            X_list.append(values[i:i + seq_length])
            y_list.append(targets[i + seq_length])

    if not X_list:
        return np.array([]), np.array([]), scaler_params

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)

    logger.info(f"Built {len(X):,} sequences (shape: {X.shape})")

    return X, y, scaler_params


# ============================================
# PYTORCH LSTM MODEL
# ============================================

if HAS_TORCH:
    class TripLSTM(nn.Module):
        """LSTM for trip duration sequence prediction."""

        def __init__(self, input_dim: int, hidden_dim: int = HIDDEN_DIM,
                     num_layers: int = NUM_LAYERS, dropout: float = 0.2):
            super().__init__()
            self.hidden_dim = hidden_dim
            self.num_layers = num_layers

            self.lstm = nn.LSTM(
                input_size=input_dim,
                hidden_size=hidden_dim,
                num_layers=num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0,
            )

            self.fc = nn.Sequential(
                nn.Linear(hidden_dim, 32),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(32, 1),
            )

        def forward(self, x):
            # x: (batch, seq_length, input_dim)
            lstm_out, (h_n, c_n) = self.lstm(x)
            # Use last hidden state
            last_hidden = lstm_out[:, -1, :]  # (batch, hidden_dim)
            output = self.fc(last_hidden)
            return output.squeeze(-1)

    class TripSequenceDataset(Dataset):
        def __init__(self, X, y):
            self.X = torch.FloatTensor(X)
            self.y = torch.FloatTensor(y)

        def __len__(self):
            return len(self.X)

        def __getitem__(self, idx):
            return self.X[idx], self.y[idx]


# ============================================
# NUMPY FALLBACK (if PyTorch not available)
# ============================================

class SimpleRNNFallback:
    """Simple numpy-based approach: weighted moving average with learned decay."""

    def __init__(self):
        self.decay = 0.85  # exponential decay for weighting past trips
        self.bias = 0.0
        self.scale = 1.0

    def fit(self, X, y):
        """Learn optimal decay, bias, scale from data."""
        best_mae = float("inf")
        best_params = (0.85, 0.0, 1.0)

        # Grid search over decay values
        for decay in [0.7, 0.75, 0.8, 0.85, 0.9, 0.95]:
            predictions = []
            for seq in X:
                # Weighted average of duration feature (index 0)
                durations = seq[:, 0]  # normalized durations
                weights = np.array([decay ** (len(durations) - 1 - i) for i in range(len(durations))])
                weights /= weights.sum()
                pred = np.dot(weights, durations)
                predictions.append(pred)

            predictions = np.array(predictions)

            # Linear calibration
            from numpy.linalg import lstsq
            A = np.column_stack([predictions, np.ones(len(predictions))])
            result = lstsq(A, y, rcond=None)
            scale, bias = result[0]

            calibrated = predictions * scale + bias
            mae = np.mean(np.abs(y - calibrated))

            if mae < best_mae:
                best_mae = mae
                best_params = (decay, bias, scale)

        self.decay, self.bias, self.scale = best_params
        logger.info(f"Fallback RNN: decay={self.decay}, bias={self.bias:.2f}, scale={self.scale:.2f}")

    def predict(self, X):
        predictions = []
        for seq in X:
            durations = seq[:, 0]
            weights = np.array([self.decay ** (len(durations) - 1 - i) for i in range(len(durations))])
            weights /= weights.sum()
            pred = np.dot(weights, durations) * self.scale + self.bias
            predictions.append(pred)
        return np.array(predictions)


# ============================================
# TRAINING
# ============================================

def train(conn, models_dir: Path) -> dict:
    logger.info("=" * 50)
    logger.info("TRAINING: LSTM Trip Duration Predictor")
    logger.info("=" * 50)

    df = fetch_sequential_data(conn)
    if df.empty:
        logger.error("No sequential data available")
        return {"error": "No data"}

    logger.info(f"Sequential data: {len(df):,} trips, {df['driver_id'].nunique():,} drivers")

    X, y, scaler_params = build_sequences(df)
    if len(X) == 0:
        logger.error("Not enough sequential data to build sequences")
        return {"error": "Not enough sequential data (need drivers with 10+ trips)"}

    # Clip target outliers
    q01, q99 = np.percentile(y, 1), np.percentile(y, 99)
    mask = (y >= q01) & (y <= q99)
    X, y = X[mask], y[mask]

    # Split
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    logger.info(f"Train: {len(X_train):,}, Test: {len(X_test):,}")
    logger.info(f"Sequence shape: {X_train.shape}")

    if HAS_TORCH:
        metrics, model_state = _train_pytorch(X_train, y_train, X_test, y_test)
        model_artifact = {
            "type": "pytorch_lstm",
            "state_dict": model_state,
            "input_dim": X_train.shape[2],
            "hidden_dim": HIDDEN_DIM,
            "num_layers": NUM_LAYERS,
        }
    else:
        metrics, fallback_model = _train_fallback(X_train, y_train, X_test, y_test)
        model_artifact = {
            "type": "numpy_fallback",
            "model": fallback_model,
        }

    # Save
    model_path = str(models_dir / "lstm_predictor.joblib")
    joblib.dump({
        **model_artifact,
        "scaler_params": scaler_params,
        "sequence_features": SEQUENCE_FEATURES,
        "sequence_length": SEQUENCE_LENGTH,
        "metrics": metrics,
    }, model_path)

    # Register
    _register_model(conn, model_path, model_artifact["type"], metrics, len(df))

    logger.info(f"LSTM predictor saved to {model_path}")

    return {
        "model_type": model_artifact["type"],
        "metrics": metrics,
        "sequence_length": SEQUENCE_LENGTH,
        "features_per_step": len(SEQUENCE_FEATURES),
        "training_sequences": len(X_train),
        "drivers_with_sequences": df["driver_id"].nunique(),
        "model_path": model_path,
    }


def _train_pytorch(X_train, y_train, X_test, y_test) -> tuple:
    """Train PyTorch LSTM model."""
    logger.info("\n--- PyTorch LSTM Training ---")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"Device: {device}")

    input_dim = X_train.shape[2]
    model = TripLSTM(input_dim=input_dim).to(device)

    train_dataset = TripSequenceDataset(X_train, y_train)
    test_dataset = TripSequenceDataset(X_test, y_test)
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE)

    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE, weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    loss_fn = nn.SmoothL1Loss()  # Huber loss - robust to outliers

    best_test_mae = float("inf")
    best_state = None
    train_losses = []

    for epoch in range(EPOCHS):
        # Train
        model.train()
        epoch_loss = 0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            pred = model(X_batch)
            loss = loss_fn(pred, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            epoch_loss += loss.item() * len(X_batch)

        avg_train_loss = epoch_loss / len(train_dataset)
        train_losses.append(avg_train_loss)

        # Evaluate
        model.eval()
        test_preds = []
        test_targets = []
        with torch.no_grad():
            for X_batch, y_batch in test_loader:
                X_batch = X_batch.to(device)
                pred = model(X_batch)
                test_preds.extend(pred.cpu().numpy())
                test_targets.extend(y_batch.numpy())

        test_preds = np.array(test_preds)
        test_targets = np.array(test_targets)
        test_mae = np.mean(np.abs(test_targets - test_preds))

        scheduler.step(test_mae)

        if test_mae < best_test_mae:
            best_test_mae = test_mae
            best_state = model.state_dict().copy()

        if (epoch + 1) % 5 == 0:
            logger.info(f"Epoch {epoch+1}/{EPOCHS}: train_loss={avg_train_loss:.4f}, test_MAE={test_mae:.2f}")

    # Final evaluation with best model
    model.load_state_dict(best_state)
    model.eval()

    all_preds = []
    all_targets = []
    with torch.no_grad():
        for X_batch, y_batch in test_loader:
            X_batch = X_batch.to(device)
            pred = model(X_batch)
            all_preds.extend(pred.cpu().numpy())
            all_targets.extend(y_batch.numpy())

    all_preds = np.array(all_preds)
    all_targets = np.array(all_targets)

    metrics = _compute_metrics(all_targets, all_preds)
    metrics["epochs_trained"] = EPOCHS
    metrics["best_epoch_mae"] = round(float(best_test_mae), 4)

    # Move state dict to CPU for saving
    cpu_state = {k: v.cpu() for k, v in best_state.items()}

    return metrics, cpu_state


def _train_fallback(X_train, y_train, X_test, y_test) -> tuple:
    """Train numpy-based fallback model."""
    logger.info("\n--- Numpy Fallback Training ---")

    model = SimpleRNNFallback()
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    metrics = _compute_metrics(y_test, y_pred)

    return metrics, model


def _compute_metrics(y_true, y_pred) -> dict:
    """Compute regression metrics for LSTM predictions."""
    mae = float(np.mean(np.abs(y_true - y_pred)))
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))

    # R2
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    r2 = float(1 - ss_res / (ss_tot + 1e-8))

    errors = np.abs(y_true - y_pred)
    within_15 = float((errors <= 15).mean() * 100)
    within_30 = float((errors <= 30).mean() * 100)
    within_60 = float((errors <= 60).mean() * 100)

    # MAPE
    nonzero = y_true != 0
    mape = float(np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])) * 100)

    metrics = {
        "mae": round(float(mae), 4),
        "rmse": round(float(rmse), 4),
        "r2": round(float(r2), 4),
        "mape": round(float(mape), 2),
        "within_15min": round(float(within_15), 2),
        "within_30min": round(float(within_30), 2),
        "within_60min": round(float(within_60), 2),
    }

    logger.info(f"LSTM: MAE={mae:.2f}, RMSE={rmse:.2f}, R2={r2:.4f}, MAPE={mape:.1f}%")
    logger.info(f"  Within: 15m={within_15:.1f}%, 30m={within_30:.1f}%, 60m={within_60:.1f}%")

    return metrics


def _register_model(conn, model_path, model_type, metrics, training_count):
    """Register LSTM model in ml_models table."""
    with conn.cursor() as cur:
        cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = 'lstm_predictor'")
        cur.execute("SELECT COALESCE(MAX(version), 0) AS max_v FROM ml_models WHERE model_name = 'lstm_predictor'")
        version = cur.fetchone()["max_v"] + 1
        cur.execute("""
            INSERT INTO ml_models (model_name, version, model_type, target_variable, metrics,
                                   feature_columns, model_artifact_path, training_data_count, is_active)
            VALUES ('lstm_predictor', %s, %s, 'trip_duration_minutes', %s, %s, %s, %s, 1)
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
            json.dumps(metrics, default=float),
            json.dumps(SEQUENCE_FEATURES),
            model_path,
            training_count,
        ))
    conn.commit()
    logger.info(f"Registered lstm_predictor v{version}")


# ============================================
# SERVING / PREDICTION
# ============================================

def predict(artifact: dict, trip_sequence: list) -> dict:
    """
    Predict next trip duration given a sequence of recent trips.
    trip_sequence: list of dicts, each with SEQUENCE_FEATURES keys.
    """
    scaler_params = artifact["scaler_params"]
    seq_length = artifact["sequence_length"]
    features = artifact["sequence_features"]

    # Pad or trim sequence
    if len(trip_sequence) < seq_length:
        # Pad with zeros at the beginning
        padding = [dict.fromkeys(features, 0)] * (seq_length - len(trip_sequence))
        trip_sequence = padding + trip_sequence
    elif len(trip_sequence) > seq_length:
        trip_sequence = trip_sequence[-seq_length:]

    # Normalize
    normalized = []
    for trip in trip_sequence:
        step = []
        for feat in features:
            val = float(trip.get(feat, 0))
            params = scaler_params.get(feat, {"mean": 0, "std": 1})
            norm_val = (val - params["mean"]) / params["std"]
            step.append(norm_val)
        normalized.append(step)

    X = np.array([normalized], dtype=np.float32)

    if artifact["type"] == "pytorch_lstm" and HAS_TORCH:
        model = TripLSTM(
            input_dim=artifact["input_dim"],
            hidden_dim=artifact["hidden_dim"],
            num_layers=artifact["num_layers"],
        )
        model.load_state_dict(artifact["state_dict"])
        model.eval()

        with torch.no_grad():
            prediction = model(torch.FloatTensor(X)).item()
    else:
        # Fallback model
        fallback = artifact.get("model")
        if fallback:
            prediction = float(fallback.predict(X)[0])
        else:
            # Last resort: weighted average
            durations = [t.get("trip_duration_minutes", 0) for t in trip_sequence]
            prediction = float(np.mean(durations[-3:]))

    prediction = max(0, prediction)

    # Confidence interval (simple heuristic based on sequence variability)
    recent_durations = [t.get("trip_duration_minutes", prediction) for t in trip_sequence[-5:]]
    std_dev = float(np.std(recent_durations)) if len(recent_durations) > 1 else prediction * 0.15

    return {
        "predicted_duration_min": round(prediction, 2),
        "confidence_interval": {
            "lower": round(max(0, prediction - 1.96 * std_dev), 2),
            "upper": round(prediction + 1.96 * std_dev, 2),
        },
        "sequence_length_used": min(len(trip_sequence), seq_length),
        "model_type": artifact["type"],
    }
