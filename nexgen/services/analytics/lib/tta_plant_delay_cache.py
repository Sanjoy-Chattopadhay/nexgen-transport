"""
Plant-Delay Cache
-----------------
Read/write layer for tta_plant_delay_cache — stores the GPS-reconstructed
in-plant delay bundle and its LLM insight so the endpoint can serve a saved
gpt-4.1 answer instead of paying for the LLM on every request.

Design:
  - Cache key = (trip_no, radius_km, benchmark_min).
  - s_gps_sig (ping count + last ping timestamp) is stored alongside; if new
    GPS lands for the trip the signature changes and the row is treated as a
    miss, so a re-analysis happens automatically. Trips are historical/closed,
    so in practice this is a no-op after the first computation.
  - Only a REAL LLM insight (source == "azure_openai") is persisted. A
    rule-based fallback is never stored, so once the Azure key is present the
    insight is generated once and cached — never regenerated from fallback.
  - The table is created by bootstrap.py on boot; _ensure() also lazily
    creates it on first use, so the cache works even if AUTO_MIGRATE is off.
"""

import json
import logging

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS tta_plant_delay_cache (
    i_trip_no        BIGINT       NOT NULL,
    d_radius_km      DECIMAL(6,2) NOT NULL DEFAULT 25.00,
    i_benchmark_min  INT          NOT NULL DEFAULT 240,
    s_gps_sig        VARCHAR(64)  NULL,
    j_analysis       JSON         NOT NULL,
    j_insight        JSON         NULL,
    s_insight_source VARCHAR(30)  NULL,
    s_model          VARCHAR(80)  NULL,
    dt_created       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    dt_updated       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (i_trip_no, d_radius_km, i_benchmark_min)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_ensured = False


def _ensure(conn):
    """Create the cache table once per process (idempotent, cheap after first)."""
    global _ensured
    if _ensured:
        return
    with conn.cursor() as cur:
        cur.execute(_DDL)
    conn.commit()
    _ensured = True


def _key(radius_km: float, benchmark_min: float):
    return round(float(radius_km), 2), int(round(float(benchmark_min)))


def _gps_sig(conn, trip_no: int) -> str:
    """Cheap signature of the trip's GPS: ping count + last ping timestamp."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c, MAX(dt_message) AS m FROM tta_trip_gps WHERE i_trip_no = %s",
            (trip_no,),
        )
        r = cur.fetchone() or {}
    c = r.get("c", 0)
    m = r.get("m")
    return f"{c}:{m.isoformat() if m else ''}"


def _as_obj(val):
    """JSON columns come back as text under DictCursor — parse if needed."""
    if val is None:
        return None
    return json.loads(val) if isinstance(val, (str, bytes, bytearray)) else val


def get_cached(conn, trip_no: int, radius_km: float, benchmark_min: float) -> dict | None:
    """Return {"analysis": {...}, "insight": {...}|None} if a fresh row exists,
    else None (missing or GPS changed since it was cached)."""
    _ensure(conn)
    radius, bench = _key(radius_km, benchmark_min)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT j_analysis, j_insight, s_gps_sig
               FROM tta_plant_delay_cache
               WHERE i_trip_no = %s AND d_radius_km = %s AND i_benchmark_min = %s""",
            (trip_no, radius, bench),
        )
        row = cur.fetchone()
    if not row:
        return None
    if row.get("s_gps_sig") != _gps_sig(conn, trip_no):
        return None  # stale — new GPS landed, force a re-analysis
    return {"analysis": _as_obj(row["j_analysis"]), "insight": _as_obj(row["j_insight"])}


def store(conn, trip_no: int, radius_km: float, benchmark_min: float,
          analysis: dict, insight: dict | None) -> None:
    """Upsert the analysis (always) and the insight (only when it's a real LLM
    answer). Never raises fatally — caching is best-effort."""
    try:
        _ensure(conn)
        radius, bench = _key(radius_km, benchmark_min)
        sig = _gps_sig(conn, trip_no)
        src = (insight or {}).get("source")
        keep_insight = insight if src == "azure_openai" else None
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tta_plant_delay_cache
                     (i_trip_no, d_radius_km, i_benchmark_min, s_gps_sig,
                      j_analysis, j_insight, s_insight_source, s_model)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     s_gps_sig = VALUES(s_gps_sig),
                     j_analysis = VALUES(j_analysis),
                     j_insight = VALUES(j_insight),
                     s_insight_source = VALUES(s_insight_source),
                     s_model = VALUES(s_model)""",
                (trip_no, radius, bench, sig,
                 json.dumps(analysis, default=str),
                 json.dumps(keep_insight, default=str) if keep_insight else None,
                 src if keep_insight else None,
                 (keep_insight or {}).get("model") if keep_insight else None),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — cache write must never break the response
        logger.warning("plant-delay cache store failed for trip %s: %s", trip_no, exc)
