"""
UI-editable app configuration, persisted in MySQL (app_settings table).

First use: the journey cost model (fuel price, mileage, driver wage,
idle burn) — edited from the trip analysis page, used by the analysis
engine and the comparison endpoint.
"""

import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_COST = {
    "fuel_price_per_liter": 100.0,
    "fuel_efficiency_kmpl": 4.0,
    "driver_wage_per_hour": 150.0,
    "idle_fuel_consumption_lph": 1.5,
}

_DDL = """
CREATE TABLE IF NOT EXISTS app_settings (
    s_key VARCHAR(64) PRIMARY KEY,
    s_value JSON NOT NULL,
    dt_modified TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _bootstrap(conn):
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()


def get_setting(conn, key: str, default: dict) -> dict:
    _bootstrap(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT s_value FROM app_settings WHERE s_key = %s", (key,))
        row = cur.fetchone()
    if not row:
        return dict(default)
    try:
        stored = json.loads(row["s_value"])
        return {**default, **{k: stored[k] for k in stored if k in default}}
    except Exception:
        logger.warning("Bad app_settings payload for %s — using defaults", key)
        return dict(default)


def save_setting(conn, key: str, value: dict):
    _bootstrap(conn)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO app_settings (s_key, s_value) VALUES (%s, %s)
               ON DUPLICATE KEY UPDATE s_value = VALUES(s_value)""",
            (key, json.dumps(value)),
        )
    conn.commit()


def get_cost_config(conn) -> dict:
    return get_setting(conn, "cost_model", DEFAULT_COST)


def save_cost_config(conn, cfg: dict) -> dict:
    clean = {}
    for k, default in DEFAULT_COST.items():
        v = cfg.get(k, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a number")
        if v <= 0:
            raise ValueError(f"{k} must be > 0")
        clean[k] = v
    save_setting(conn, "cost_model", clean)
    return clean
