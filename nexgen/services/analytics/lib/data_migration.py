"""
Data Migration Service
----------------------
Called from:
  1. API endpoint: POST /api/v1/migrate/trips    (background, poll /migrate/status)
  2. CLI: python -m backend.app.services.data_migration

Reads CSV/Excel files and inserts into MySQL in batches.
Uses chunked CSV reading to handle large files without blowing up memory.

NEW COLUMN MAPPING (trip_data.csv):
  dt_trip_start  -> trip_start
  dt_trip_eta    -> trip_eta (expected ETA)
  dt_ata_in      -> ata_in + trip_end (actual arrival = trip end)
  s_origin       -> origin (replaces s_org_node_name)
  s_destination  -> destination (replaces s_dest_node_name)
  s_driver_mob1/mob2 -> mobile1/mobile2

DATA QUALITY:
  - If dt_ata_in is NULL -> eta_data_status = 'eta_data_unavailable'
  - If i_trip_km is NULL -> filled with route average (same origin->dest)
"""

import os
import re
import logging
import threading
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np
import pymysql
from pymysql.cursors import DictCursor

from nexgen.services.analytics.lib.trip_metrics import eta_outcome, journey_metrics

logger = logging.getLogger(__name__)

from nexgen.core.config import ROOT as PROJECT_ROOT
BATCH_SIZE = 2000
CSV_CHUNK_SIZE = 50_000  # Read CSV in 50K row chunks


# ============================================
# GLOBAL PROGRESS TRACKER
# ============================================

_migration_progress = {
    "running": False,
    "phase": "idle",
    "total_rows": 0,
    "processed_rows": 0,
    "inserted": 0,
    "skipped": 0,
    "percent": 0,
    "started_at": None,
    "elapsed_seconds": 0,
    "error": None,
    "dimensions": {},
}
_progress_lock = threading.Lock()


def get_progress() -> dict:
    with _progress_lock:
        p = dict(_migration_progress)
        if p["started_at"] and p["running"]:
            p["elapsed_seconds"] = round((datetime.now() - p["started_at"]).total_seconds(), 1)
        return p


def _update_progress(**kwargs):
    with _progress_lock:
        _migration_progress.update(kwargs)
        if _migration_progress["total_rows"] > 0:
            _migration_progress["percent"] = round(
                _migration_progress["processed_rows"] / _migration_progress["total_rows"] * 100, 1
            )


# ============================================
# UTILITIES
# ============================================

def clean_str(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s in ("", "nan", "None", "NaN", "NaT", "NA", "na"):
        return None
    return s


def clean_mobile(val):
    s = clean_str(val)
    if s is None:
        return None
    s = s.replace(".0", "")
    s = "".join(c for c in s if c.isdigit() or c == "+")
    return None if s in ("0", "") else s


def parse_datetime(val):
    if pd.isna(val):
        return None
    if isinstance(val, str) and val.strip().upper() in ("NAT", "NAN", "NONE", ""):
        return None
    try:
        dt = pd.to_datetime(val, dayfirst=True, errors="coerce")
        return dt.to_pydatetime() if not pd.isna(dt) else None
    except Exception:
        return None


def safe_float(val):
    if pd.isna(val):
        return None
    try:
        v = float(val)
        return None if (np.isinf(v) or np.isnan(v)) else v
    except (ValueError, TypeError):
        return None


def safe_int(val):
    if pd.isna(val):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ============================================
# SCHEMA SETUP SERVICE
# ============================================

def run_schema(conn):
    """Execute schema.sql to create all tables. Idempotent (IF NOT EXISTS)."""
    # NexGen: every table is created by the migrations (python -m nexgen migrate);
    # tta_trips and the other feed tables are read-only views in this schema.
    return {"status": "ok", "note": "tables are created by NexGen migrations"}
    schema_path = PROJECT_ROOT / "migrations" / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    with conn.cursor() as cur:
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                try:
                    cur.execute(statement)
                except pymysql.err.OperationalError as e:
                    if e.args[0] in (1061, 1068):  # duplicate index/key
                        continue
                    raise
    conn.commit()
    logger.info("Schema created/verified")
    return {"status": "ok", "message": "Schema created successfully"}


# ============================================
# TRIP DISPATCH MIGRATION SERVICE (CHUNKED)
# ============================================

def count_csv_rows(csv_path: Path) -> int:
    """Fast line count without loading the whole file."""
    count = 0
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        for _ in f:
            count += 1
    return count - 1  # subtract header


def migrate_trip_csv(conn, csv_path: str) -> dict:
    """
    Migrate a trip dispatch CSV into MySQL using chunked reading.
    Safe for large files - never loads entire CSV into memory.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return {"status": "error", "message": f"File not found: {csv_path}"}

    start_time = datetime.now()
    _update_progress(
        running=True, phase="counting_rows", total_rows=0,
        processed_rows=0, inserted=0, skipped=0, percent=0,
        started_at=start_time, error=None, dimensions={},
    )

    logger.info(f"Counting rows in {csv_path.name}...")
    total_rows = count_csv_rows(csv_path)
    _update_progress(total_rows=total_rows, phase="extracting_dimensions")
    logger.info(f"Total rows: {total_rows:,}")

    # --- PASS 1: Extract all unique dimensions (chunked) ---
    logger.info("Pass 1: Extracting unique dimensions...")

    all_drivers = set()       # (name, mobile1, mobile2)
    all_vehicles = set()      # (asset_id, asset_type)
    all_locations = set()     # location name
    # Route km tracker for filling missing trip_km
    route_km_data = {}        # (origin, dest) -> list of km values

    encoding = _detect_encoding(csv_path)
    chunks_read = 0

    for chunk in pd.read_csv(csv_path, encoding=encoding, low_memory=False, chunksize=CSV_CHUNK_SIZE):
        chunks_read += 1

        # Drivers - use s_driver_name, s_driver_mob1, s_driver_mob2
        if "s_driver_name" in chunk.columns:
            for _, r in chunk[chunk["s_driver_name"].notna()].iterrows():
                name = clean_str(r["s_driver_name"])
                if name:
                    mob1 = clean_mobile(r.get("s_driver_mob1"))
                    mob2 = clean_mobile(r.get("s_driver_mob2"))
                    all_drivers.add((name, mob1, mob2))

        # Vehicles
        if "s_asset_id" in chunk.columns:
            for _, r in chunk[chunk["s_asset_id"].notna()].iterrows():
                aid = clean_str(r["s_asset_id"])
                atype = clean_str(r.get("s_asset_type"))
                if aid:
                    all_vehicles.add((aid, atype))

        # Locations - use s_origin / s_destination
        for col in ["s_origin", "s_destination"]:
            if col in chunk.columns:
                locs = chunk[col].dropna().astype(str).str.strip().unique()
                for loc in locs:
                    if loc and loc not in ("nan", "None", "NA", ""):
                        all_locations.add(loc)

        # Collect trip_km per route for filling missing values
        if "s_origin" in chunk.columns and "s_destination" in chunk.columns and "i_trip_km" in chunk.columns:
            valid_km = chunk[chunk["i_trip_km"].notna() & (chunk["i_trip_km"] > 0)]
            for _, r in valid_km.iterrows():
                origin = clean_str(r.get("s_origin"))
                dest = clean_str(r.get("s_destination"))
                km = safe_float(r.get("i_trip_km"))
                if origin and dest and km and km > 0:
                    key = (origin, dest)
                    if key not in route_km_data:
                        route_km_data[key] = []
                    route_km_data[key].append(km)

        _update_progress(
            processed_rows=min(chunks_read * CSV_CHUNK_SIZE, total_rows),
            phase="extracting_dimensions",
        )

        if chunks_read % 20 == 0:
            logger.info(f"  Dimension scan: {chunks_read * CSV_CHUNK_SIZE:,} / {total_rows:,}")

    # Compute route average km for filling missing values
    route_avg_km = {}
    for key, values in route_km_data.items():
        route_avg_km[key] = round(sum(values) / len(values), 2)
    logger.info(f"Route km averages computed for {len(route_avg_km):,} routes")

    logger.info(f"Unique dimensions: {len(all_drivers):,} drivers, "
                f"{len(all_vehicles):,} vehicles, {len(all_locations):,} locations")

    # --- INSERT DIMENSIONS ---
    _update_progress(phase="inserting_dimensions", processed_rows=0)

    driver_map = _bulk_insert_drivers(conn, all_drivers)
    vehicle_map = _bulk_insert_vehicles(conn, all_vehicles)
    location_map = _bulk_insert_locations(conn, all_locations)

    _update_progress(dimensions={
        "drivers": len(driver_map),
        "vehicles": len(vehicle_map),
        "locations": len(location_map),
    })

    logger.info(f"Dimensions inserted. Maps: drivers={len(driver_map):,}, "
                f"vehicles={len(vehicle_map):,}, locations={len(location_map):,}")

    # --- PASS 2: Insert trips (chunked) ---
    _update_progress(phase="inserting_trips", processed_rows=0, inserted=0, skipped=0)
    logger.info("Pass 2: Inserting trips...")

    total_inserted = 0
    total_skipped = 0
    chunk_num = 0

    for chunk in pd.read_csv(csv_path, encoding=encoding, low_memory=False, chunksize=CSV_CHUNK_SIZE):
        chunk_num += 1

        # Dedup within chunk by s_dispatch_entry_no
        dedup_col = "s_dispatch_entry_no" if "s_dispatch_entry_no" in chunk.columns else None
        if dedup_col:
            chunk = chunk.drop_duplicates(subset=[dedup_col], keep="first")

        ins, skip = _insert_trip_chunk(conn, chunk, driver_map, vehicle_map, location_map, route_avg_km)
        total_inserted += ins
        total_skipped += skip

        processed = min(chunk_num * CSV_CHUNK_SIZE, total_rows)
        _update_progress(processed_rows=processed, inserted=total_inserted, skipped=total_skipped)

        if chunk_num % 5 == 0:
            pct = processed / total_rows * 100
            elapsed = (datetime.now() - start_time).total_seconds()
            rate = total_inserted / elapsed if elapsed > 0 else 0
            remaining = (total_rows - processed) / (rate * 60) if rate > 0 else 0
            logger.info(
                f"  Progress: {pct:.1f}% | Inserted: {total_inserted:,} | "
                f"Skipped: {total_skipped:,} | Rate: {rate:.0f}/s | "
                f"ETA: {remaining:.1f} min"
            )

    elapsed = (datetime.now() - start_time).total_seconds()

    result = {
        "status": "ok",
        "file": str(csv_path),
        "total_rows": total_rows,
        "drivers": len(driver_map),
        "vehicles": len(vehicle_map),
        "locations": len(location_map),
        "trips_inserted": total_inserted,
        "trips_skipped": total_skipped,
        "elapsed_seconds": round(elapsed, 1),
        "rows_per_second": round(total_inserted / elapsed, 1) if elapsed > 0 else 0,
        "route_km_averages": len(route_avg_km),
    }

    _update_progress(running=False, phase="completed", percent=100, elapsed_seconds=round(elapsed, 1))
    logger.info(f"Migration complete: {result}")
    return result


def _detect_encoding(csv_path: Path) -> str:
    """Try to detect CSV encoding."""
    for enc in ["utf-8", "latin1", "cp1252"]:
        try:
            with open(csv_path, "r", encoding=enc) as f:
                f.readline()
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


# ============================================
# DIMENSION BULK INSERTS
# ============================================

def _bulk_insert_drivers(conn, driver_set: set) -> dict:
    """Bulk insert unique drivers. Returns {(name, mobile1): id} mapping."""
    rows = [(name, mob1, mob2) for name, mob1, mob2 in driver_set if name]
    if rows:
        with conn.cursor() as cur:
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                cur.executemany(
                    "INSERT IGNORE INTO drivers (name, mobile1, mobile2) VALUES (%s, %s, %s)",
                    batch,
                )
        conn.commit()

    mapping = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, mobile1 FROM drivers")
        for row in cur.fetchall():
            mapping[(row["name"], row["mobile1"])] = row["id"]

    logger.info(f"  Drivers: {len(mapping):,}")
    return mapping


def _bulk_insert_vehicles(conn, vehicle_set: set) -> dict:
    """Bulk insert unique vehicles with asset_type."""
    rows = [(aid, atype) for aid, atype in vehicle_set if aid]
    if rows:
        with conn.cursor() as cur:
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                cur.executemany(
                    "INSERT IGNORE INTO vehicles (asset_id, asset_type) VALUES (%s, %s)",
                    batch,
                )
        conn.commit()

    mapping = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, asset_id FROM vehicles")
        for row in cur.fetchall():
            mapping[row["asset_id"]] = row["id"]

    logger.info(f"  Vehicles: {len(mapping):,}")
    return mapping


def _bulk_insert_locations(conn, location_set: set) -> dict:
    """Bulk insert unique locations."""
    rows = [(loc,) for loc in sorted(location_set) if loc]
    if rows:
        with conn.cursor() as cur:
            for i in range(0, len(rows), BATCH_SIZE):
                batch = rows[i:i + BATCH_SIZE]
                cur.executemany("INSERT IGNORE INTO locations (name) VALUES (%s)", batch)
        conn.commit()

    mapping = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM locations")
        for row in cur.fetchall():
            mapping[row["name"]] = row["id"]

    logger.info(f"  Locations: {len(mapping):,}")
    return mapping


# ============================================
# TRIP INSERT (per chunk) - NEW COLUMN MAPPING
# ============================================

def _insert_trip_chunk(conn, chunk_df, driver_map, vehicle_map, location_map, route_avg_km) -> tuple:
    """Insert a chunk of trips. Returns (inserted, skipped).

    KEY: dt_ata_in is used as trip_end (actual arrival).
         If dt_ata_in is NULL -> eta_data_status = 'eta_data_unavailable'.
         Missing i_trip_km is filled from route averages.
    """
    insert_sql = """
        INSERT IGNORE INTO trips (
            dispatch_entry_no, driver_id, vehicle_id, origin_id, destination_id,
            trip_start, trip_end, trip_eta, ata_in, ata_out, trip_closed_at,
            dt_created, dt_updated,
            trip_km, total_dist, cover_dist,
            trip_duration_minutes, eta_met, eta_delay_minutes, avg_speed_kmph,
            eta_data_status,
            trip_status, is_active, trip_close_remark, material_desc,
            invoice_no, invoice_date, ref_no, ref_date,
            entry_type, own_market_type, running_sts, trip_seq, delay_by,
            device_id, device_type, entity_id, cnr_id,
            created_by, updated_by, track_link, data_string
        ) VALUES (
            %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,
            %s,%s,%s,%s, %s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s,
            %s,%s,%s,%s, %s,%s,%s,%s
        )
    """

    inserted = 0
    skipped = 0
    rows = []

    for _, r in chunk_df.iterrows():
        try:
            dispatch_no = clean_str(r.get("s_dispatch_entry_no"))
            if not dispatch_no:
                skipped += 1
                continue

            # Driver lookup
            driver_name = clean_str(r.get("s_driver_name"))
            driver_mobile = clean_mobile(r.get("s_driver_mob1"))
            driver_id = driver_map.get((driver_name, driver_mobile)) if driver_name else None

            # Vehicle lookup
            vehicle_id = vehicle_map.get(clean_str(r.get("s_asset_id")))

            # Location lookup - s_origin / s_destination
            origin_str = clean_str(r.get("s_origin"))
            dest_str = clean_str(r.get("s_destination"))
            origin_id = location_map.get(origin_str) if origin_str else None
            dest_id = location_map.get(dest_str) if dest_str else None

            # Parse timestamps
            trip_start = parse_datetime(r.get("dt_trip_start"))
            trip_eta = parse_datetime(r.get("dt_trip_eta"))
            ata_in = parse_datetime(r.get("dt_ata_in"))
            ata_out = parse_datetime(r.get("dt_ata_out"))
            dt_created = parse_datetime(r.get("dt_created"))
            dt_updated = parse_datetime(r.get("dt_updated"))

            # trip_end IS the actual arrival (see this module's column mapping).
            # It used to fall back to dt_trip_end, the administrative closing
            # stamp, which made every journey metric below describe a trip that
            # never ran. The closure is not discarded — it is stored whole in
            # its own column, with its reason in trip_close_remark.
            trip_end = ata_in
            trip_closed_at = parse_datetime(r.get("dt_trip_end"))

            # eta_data_status flag
            eta_data_status = "available" if ata_in is not None else "eta_data_unavailable"

            # Trip km - fill missing with route average
            trip_km = safe_float(r.get("i_trip_km"))
            if (trip_km is None or trip_km <= 0) and origin_str and dest_str:
                trip_km = route_avg_km.get((origin_str, dest_str))

            # Computed fields — all derived from the actual arrival only, so a
            # trip that never arrived carries no invented journey. See
            # trip_metrics for why these are NULL rather than 0.
            duration, avg_speed = journey_metrics(trip_start, ata_in, trip_km)
            eta_met, eta_delay = eta_outcome(ata_in, trip_eta)

            rows.append((
                dispatch_no, driver_id, vehicle_id, origin_id, dest_id,
                trip_start, trip_end, trip_eta, ata_in, ata_out, trip_closed_at,
                dt_created, dt_updated,
                trip_km,
                safe_float(r.get("i_total_dist")),
                safe_float(r.get("i_cover_dist")),
                duration, eta_met, eta_delay, avg_speed,
                eta_data_status,
                clean_str(r.get("c_trip_status")),
                clean_str(r.get("c_is_active")),
                clean_str(r.get("s_trip_close_remark")),
                clean_str(r.get("s_material_desc")),
                clean_str(r.get("s_invoice_no")),
                clean_str(r.get("s_invoice_date")),
                clean_str(r.get("s_ref_no")),
                clean_str(r.get("s_ref_date")),
                clean_str(r.get("s_entry_type")),
                clean_str(r.get("s_own_market_type")),
                clean_str(r.get("s_running_sts")),
                safe_int(r.get("i_trip_seq")),
                safe_float(r.get("i_delay_by")),
                clean_str(r.get("s_device_id")),
                clean_str(r.get("s_device_type")),
                safe_int(r.get("i_entity_id")),
                safe_int(r.get("i_cnr_id")),
                clean_str(r.get("s_created_by")),
                clean_str(r.get("s_updated_by")),
                clean_str(r.get("s_track_link")),
                clean_str(r.get("s_data_string")),
            ))
        except Exception:
            skipped += 1
            continue

        # Flush batch
        if len(rows) >= BATCH_SIZE:
            try:
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, rows)
                conn.commit()
                inserted += len(rows)
            except Exception as e:
                logger.error(f"Batch insert error: {e}")
                conn.rollback()
                skipped += len(rows)
            rows = []

    # Final flush
    if rows:
        try:
            with conn.cursor() as cur:
                cur.executemany(insert_sql, rows)
            conn.commit()
            inserted += len(rows)
        except Exception as e:
            logger.error(f"Final batch error: {e}")
            conn.rollback()
            skipped += len(rows)

    return inserted, skipped


# ============================================
# WAYPOINT MIGRATION SERVICE
# ============================================

def migrate_waypoint_excel(conn, excel_path: str) -> dict:
    """Migrate a single waypoint Excel file into MySQL."""
    fp = Path(excel_path)
    if not fp.exists():
        return {"status": "error", "message": f"File not found: {fp}"}

    match = re.search(r"Waypoint_(.+)\.(xls|xlsx)$", fp.name)
    if not match:
        return {"status": "error", "message": f"Cannot extract vehicle ID from: {fp.name}"}

    asset_id = match.group(1)

    with conn.cursor() as cur:
        cur.execute("INSERT IGNORE INTO vehicles (asset_id) VALUES (%s)", (asset_id,))
        conn.commit()
        cur.execute("SELECT id FROM vehicles WHERE asset_id = %s", (asset_id,))
        row = cur.fetchone()

    if not row:
        return {"status": "error", "message": f"Could not resolve vehicle: {asset_id}"}
    vehicle_id = row["id"]

    df = pd.read_excel(fp)
    logger.info(f"Loaded {len(df):,} waypoints from {fp.name}")

    col_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if "date" in cl and "time" in cl:
            col_map["datetime"] = col
        elif cl in ("distance", "dist"):
            col_map["distance"] = col
        elif cl == "status":
            col_map["status"] = col
        elif cl in ("latitude", "lat"):
            col_map["latitude"] = col
        elif cl in ("longitude", "lon", "lng"):
            col_map["longitude"] = col
        elif cl == "location":
            col_map["location"] = col

    if "datetime" not in col_map:
        return {"status": "error", "message": f"No DateTime column found. Columns: {list(df.columns)}"}

    insert_sql = """
        INSERT INTO waypoints (vehicle_id, latitude, longitude, speed_kmph,
                               status, location_text, distance_from_prev, recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """

    inserted = 0
    for batch_start in range(0, len(df), BATCH_SIZE):
        batch = df.iloc[batch_start:batch_start + BATCH_SIZE]
        rows = []
        for _, r in batch.iterrows():
            recorded_at = parse_datetime(r.get(col_map["datetime"]))
            if not recorded_at:
                continue

            status_raw = clean_str(r.get(col_map.get("status")))
            speed = None
            if status_raw:
                speed_match = re.search(r"(\d+\.?\d*)", status_raw)
                if speed_match and "moving" in status_raw.lower():
                    speed = float(speed_match.group(1))
                elif "stop" in status_raw.lower():
                    speed = 0.0

            rows.append((
                vehicle_id,
                safe_float(r.get(col_map.get("latitude"))) if "latitude" in col_map else None,
                safe_float(r.get(col_map.get("longitude"))) if "longitude" in col_map else None,
                speed,
                status_raw,
                clean_str(r.get(col_map.get("location"))) if "location" in col_map else None,
                safe_float(r.get(col_map.get("distance"))) if "distance" in col_map else None,
                recorded_at,
            ))

        if rows:
            with conn.cursor() as cur:
                cur.executemany(insert_sql, rows)
            conn.commit()
            inserted += len(rows)

    return {"status": "ok", "vehicle": asset_id, "waypoints_inserted": inserted}


# ============================================
# SUMMARY REFRESH SERVICE
# ============================================

def refresh_all_summaries(conn) -> dict:
    """Refresh all summary tables from trips data.
    IMPORTANT: Only uses trips with eta_data_status = 'available' for analytics.

    Each summary table is written twice: a cnr_id = 0 "all consignors" rollup
    (backward-compatible, what unscoped requests read) and one row per consignor
    (cnr_id = trips.cnr_id) so consignor-scoped requests filter cheaply.
    """
    results = {}

    # Each entry: (table, insert_cols, select_exprs, from_join, base_where, group_dims)
    # {cnr} in select_exprs is the consignor expression; group dims get cnr appended
    # on the per-consignor pass.
    BASE_WHERE = ("t.eta_data_status = 'available' "
                  "AND t.trip_duration_minutes IS NOT NULL AND t.trip_duration_minutes > 0")
    SUMMARIES = [
        (
            "driver_summary",
            "driver_id, driver_name, driver_mobile, total_trips, eta_met_count, "
            "eta_known_count, eta_success_rate, avg_duration_min, max_duration_min, min_duration_min, "
            "avg_speed_kmph, vehicles_used, total_distance_km, avg_distance_km, avg_eta_delay_min, cnr_id",
            "d.id, d.name, d.mobile1, COUNT(*), "
            "SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END), "
            # Denominator of the rate below: trips with a KNOWN outcome.
            "COUNT(t.eta_met), "
            "ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2), "
            "ROUND(AVG(t.trip_duration_minutes), 2), ROUND(MAX(t.trip_duration_minutes), 2), "
            "ROUND(MIN(t.trip_duration_minutes), 2), ROUND(AVG(t.avg_speed_kmph), 2), "
            "COUNT(DISTINCT t.vehicle_id), ROUND(SUM(t.trip_km), 2), "
            "ROUND(AVG(t.trip_km), 2), ROUND(AVG(t.eta_delay_minutes), 2)",
            "FROM trips t JOIN drivers d ON t.driver_id = d.id",
            BASE_WHERE,
            "d.id, d.name, d.mobile1",
        ),
        (
            "route_summary",
            "origin, destination, route_name, trip_count, "
            "avg_duration_min, avg_speed_kmph, eta_success_rate, avg_distance_km, cnr_id",
            "lo.name, ld.name, CONCAT(lo.name, ' -> ', ld.name), COUNT(*), "
            "ROUND(AVG(t.trip_duration_minutes), 2), ROUND(AVG(t.avg_speed_kmph), 2), "
            "ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2), "
            "ROUND(AVG(t.trip_km), 2)",
            "FROM trips t JOIN locations lo ON t.origin_id = lo.id JOIN locations ld ON t.destination_id = ld.id",
            BASE_WHERE,
            "lo.name, ld.name",
        ),
        (
            "vehicle_summary",
            "vehicle_id, asset_id, asset_type, total_trips, drivers_used, "
            "avg_speed_kmph, total_distance_km, avg_distance_km, eta_success_rate, cnr_id",
            "v.id, v.asset_id, v.asset_type, COUNT(*), COUNT(DISTINCT t.driver_id), "
            "ROUND(AVG(t.avg_speed_kmph), 2), ROUND(SUM(t.trip_km), 2), ROUND(AVG(t.trip_km), 2), "
            "ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2)",
            "FROM trips t JOIN vehicles v ON t.vehicle_id = v.id",
            BASE_WHERE,
            "v.id, v.asset_id, v.asset_type",
        ),
        (
            "daily_fleet_stats",
            "stat_date, total_trips, total_distance_km, avg_speed, "
            "eta_success_rate, active_drivers, active_vehicles, cnr_id",
            "DATE(t.trip_start), COUNT(*), ROUND(SUM(t.trip_km), 2), ROUND(AVG(t.avg_speed_kmph), 2), "
            "ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2), "
            "COUNT(DISTINCT t.driver_id), COUNT(DISTINCT t.vehicle_id)",
            "FROM trips t",
            "t.trip_start IS NOT NULL AND t.eta_data_status = 'available' "
            "AND t.trip_duration_minutes IS NOT NULL",
            "DATE(t.trip_start)",
        ),
    ]

    for table, cols, exprs, from_join, base_where, dims in SUMMARIES:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}")
            # Rollup row (all consignors)
            cur.execute(
                f"INSERT INTO {table} ({cols}) "
                f"SELECT {exprs}, 0 {from_join} WHERE {base_where} GROUP BY {dims}"
            )
            rolled = cur.rowcount
            # Per-consignor rows
            cur.execute(
                f"INSERT INTO {table} ({cols}) "
                f"SELECT {exprs}, t.cnr_id {from_join} "
                f"WHERE {base_where} AND t.cnr_id IS NOT NULL GROUP BY {dims}, t.cnr_id"
            )
            results[table] = rolled + cur.rowcount
        conn.commit()

    # Route time patterns
    with conn.cursor() as cur:
        cur.execute("DELETE FROM route_time_patterns")
        cur.execute("""
            INSERT INTO route_time_patterns (origin, destination, hour_of_day, day_of_week,
                                             avg_duration, trip_count, eta_success_rate)
            SELECT lo.name, ld.name, HOUR(t.trip_start), DAYOFWEEK(t.trip_start),
                   ROUND(AVG(t.trip_duration_minutes), 2), COUNT(*),
                   ROUND(SUM(CASE WHEN t.eta_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(t.eta_met), 0) * 100, 2)
            FROM trips t JOIN locations lo ON t.origin_id = lo.id JOIN locations ld ON t.destination_id = ld.id
            WHERE t.trip_start IS NOT NULL
              AND t.trip_duration_minutes IS NOT NULL
              AND t.eta_data_status = 'available'
            GROUP BY lo.name, ld.name, HOUR(t.trip_start), DAYOFWEEK(t.trip_start) HAVING COUNT(*) >= 2
        """)
        results["route_time_patterns"] = cur.rowcount
    conn.commit()

    logger.info(f"Summaries refreshed: {results}")
    return results


# ============================================
# INCREMENTAL SUMMARY REFRESH (scoped to touched entities)
# ============================================

_INCR_BASE_WHERE = ("t.eta_data_status = 'available' "
                    "AND t.trip_duration_minutes IS NOT NULL AND t.trip_duration_minutes > 0")


def refresh_summaries_incremental(conn, affected: dict) -> dict:
    """Rebuild ONLY the summary rows for entities an ingest actually touched,
    recomputing each fully from ALL its trips in the DB — so nothing is lost.

    For every touched entity we DELETE its summary rows then re-INSERT both the
    cnr_id=0 rollup row and the per-consignor rows, exactly like
    refresh_all_summaries but filtered. Because each entity is recomputed from
    scratch, previously-ingested trips are always included; untouched entities
    keep their existing (correct) rows.

    `affected` keys (values are iterables; falsy entries ignored):
        driver_ids, vehicle_ids, customer_ids,
        routes  -> (origin_id, destination_id) pairs,
        dates   -> date/datetime of trip_start
    """
    driver_ids = sorted({i for i in affected.get("driver_ids", ()) if i})
    vehicle_ids = sorted({i for i in affected.get("vehicle_ids", ()) if i})
    routes = sorted({(o, d) for (o, d) in affected.get("routes", ()) if o and d})
    dates = sorted({(x.date() if hasattr(x, "date") else x)
                    for x in affected.get("dates", ()) if x})
    bw = _INCR_BASE_WHERE
    results = {}

    # ---- driver_summary (PK: driver_id, cnr_id) ----
    if driver_ids:
        ph = ",".join(["%s"] * len(driver_ids))
        cols = ("driver_id, driver_name, driver_mobile, total_trips, eta_met_count, "
                "eta_known_count, eta_success_rate, avg_duration_min, max_duration_min, min_duration_min, "
                "avg_speed_kmph, vehicles_used, total_distance_km, avg_distance_km, "
                "avg_eta_delay_min, cnr_id")
        exprs = ("d.id, d.name, d.mobile1, COUNT(*), "
                 "SUM(CASE WHEN t.eta_met=1 THEN 1 ELSE 0 END), "
                 "COUNT(t.eta_met), "
                 "ROUND(SUM(CASE WHEN t.eta_met=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(t.eta_met),0)*100,2), "
                 "ROUND(AVG(t.trip_duration_minutes),2), ROUND(MAX(t.trip_duration_minutes),2), "
                 "ROUND(MIN(t.trip_duration_minutes),2), ROUND(AVG(t.avg_speed_kmph),2), "
                 "COUNT(DISTINCT t.vehicle_id), ROUND(SUM(t.trip_km),2), "
                 "ROUND(AVG(t.trip_km),2), ROUND(AVG(t.eta_delay_minutes),2)")
        join = "FROM trips t JOIN drivers d ON t.driver_id=d.id"
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM driver_summary WHERE driver_id IN ({ph})", driver_ids)
            cur.execute(f"INSERT INTO driver_summary ({cols}) SELECT {exprs}, 0 {join} "
                        f"WHERE {bw} AND d.id IN ({ph}) GROUP BY d.id,d.name,d.mobile1", driver_ids)
            n = cur.rowcount
            cur.execute(f"INSERT INTO driver_summary ({cols}) SELECT {exprs}, t.cnr_id {join} "
                        f"WHERE {bw} AND t.cnr_id IS NOT NULL AND d.id IN ({ph}) "
                        f"GROUP BY d.id,d.name,d.mobile1,t.cnr_id", driver_ids)
            results["driver_summary"] = n + cur.rowcount
        conn.commit()

    # ---- vehicle_summary (PK: vehicle_id, cnr_id) ----
    if vehicle_ids:
        ph = ",".join(["%s"] * len(vehicle_ids))
        cols = ("vehicle_id, asset_id, asset_type, total_trips, drivers_used, "
                "avg_speed_kmph, total_distance_km, avg_distance_km, eta_success_rate, cnr_id")
        exprs = ("v.id, v.asset_id, v.asset_type, COUNT(*), COUNT(DISTINCT t.driver_id), "
                 "ROUND(AVG(t.avg_speed_kmph),2), ROUND(SUM(t.trip_km),2), ROUND(AVG(t.trip_km),2), "
                 "ROUND(SUM(CASE WHEN t.eta_met=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(t.eta_met),0)*100,2)")
        join = "FROM trips t JOIN vehicles v ON t.vehicle_id=v.id"
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM vehicle_summary WHERE vehicle_id IN ({ph})", vehicle_ids)
            cur.execute(f"INSERT INTO vehicle_summary ({cols}) SELECT {exprs}, 0 {join} "
                        f"WHERE {bw} AND v.id IN ({ph}) GROUP BY v.id,v.asset_id,v.asset_type", vehicle_ids)
            n = cur.rowcount
            cur.execute(f"INSERT INTO vehicle_summary ({cols}) SELECT {exprs}, t.cnr_id {join} "
                        f"WHERE {bw} AND t.cnr_id IS NOT NULL AND v.id IN ({ph}) "
                        f"GROUP BY v.id,v.asset_id,v.asset_type,t.cnr_id", vehicle_ids)
            results["vehicle_summary"] = n + cur.rowcount
        conn.commit()

    # ---- route_summary + route_time_patterns (keyed by location names) ----
    if routes:
        pair_ph = ",".join(["(%s,%s)"] * len(routes))
        flat = [x for pair in routes for x in pair]
        join = ("FROM trips t JOIN locations lo ON t.origin_id=lo.id "
                "JOIN locations ld ON t.destination_id=ld.id")
        rs_cols = ("origin, destination, route_name, trip_count, avg_duration_min, "
                   "avg_speed_kmph, eta_success_rate, avg_distance_km, cnr_id")
        rs_exprs = ("lo.name, ld.name, CONCAT(lo.name,' -> ',ld.name), COUNT(*), "
                    "ROUND(AVG(t.trip_duration_minutes),2), ROUND(AVG(t.avg_speed_kmph),2), "
                    "ROUND(SUM(CASE WHEN t.eta_met=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(t.eta_met),0)*100,2), "
                    "ROUND(AVG(t.trip_km),2)")
        with conn.cursor() as cur:
            cur.execute(f"DELETE rs FROM route_summary rs "
                        f"JOIN locations lo ON rs.origin=lo.name JOIN locations ld ON rs.destination=ld.name "
                        f"WHERE (lo.id, ld.id) IN ({pair_ph})", flat)
            cur.execute(f"INSERT INTO route_summary ({rs_cols}) SELECT {rs_exprs}, 0 {join} "
                        f"WHERE {bw} AND (t.origin_id,t.destination_id) IN ({pair_ph}) "
                        f"GROUP BY lo.name, ld.name", flat)
            n = cur.rowcount
            cur.execute(f"INSERT INTO route_summary ({rs_cols}) SELECT {rs_exprs}, t.cnr_id {join} "
                        f"WHERE {bw} AND t.cnr_id IS NOT NULL AND (t.origin_id,t.destination_id) IN ({pair_ph}) "
                        f"GROUP BY lo.name, ld.name, t.cnr_id", flat)
            results["route_summary"] = n + cur.rowcount

            # route_time_patterns has no cnr_id dimension
            cur.execute(f"DELETE rtp FROM route_time_patterns rtp "
                        f"JOIN locations lo ON rtp.origin=lo.name JOIN locations ld ON rtp.destination=ld.name "
                        f"WHERE (lo.id, ld.id) IN ({pair_ph})", flat)
            cur.execute(
                f"INSERT INTO route_time_patterns "
                f"(origin, destination, hour_of_day, day_of_week, avg_duration, trip_count, eta_success_rate) "
                f"SELECT lo.name, ld.name, HOUR(t.trip_start), DAYOFWEEK(t.trip_start), "
                f"ROUND(AVG(t.trip_duration_minutes),2), COUNT(*), "
                f"ROUND(SUM(CASE WHEN t.eta_met=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(t.eta_met),0)*100,2) "
                f"{join} WHERE t.trip_start IS NOT NULL AND t.trip_duration_minutes IS NOT NULL "
                f"AND t.eta_data_status='available' AND (t.origin_id,t.destination_id) IN ({pair_ph}) "
                f"GROUP BY lo.name, ld.name, HOUR(t.trip_start), DAYOFWEEK(t.trip_start) HAVING COUNT(*)>=2",
                flat)
            results["route_time_patterns"] = cur.rowcount
        conn.commit()

    # ---- daily_fleet_stats (PK: stat_date, cnr_id) ----
    if dates:
        ph = ",".join(["%s"] * len(dates))
        cols = ("stat_date, total_trips, total_distance_km, avg_speed, eta_success_rate, "
                "active_drivers, active_vehicles, cnr_id")
        exprs = ("DATE(t.trip_start), COUNT(*), ROUND(SUM(t.trip_km),2), ROUND(AVG(t.avg_speed_kmph),2), "
                 "ROUND(SUM(CASE WHEN t.eta_met=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(t.eta_met),0)*100,2), "
                 "COUNT(DISTINCT t.driver_id), COUNT(DISTINCT t.vehicle_id)")
        where = ("t.trip_start IS NOT NULL AND t.eta_data_status='available' "
                 "AND t.trip_duration_minutes IS NOT NULL")
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM daily_fleet_stats WHERE stat_date IN ({ph})", dates)
            cur.execute(f"INSERT INTO daily_fleet_stats ({cols}) SELECT {exprs}, 0 FROM trips t "
                        f"WHERE {where} AND DATE(t.trip_start) IN ({ph}) GROUP BY DATE(t.trip_start)", dates)
            n = cur.rowcount
            cur.execute(f"INSERT INTO daily_fleet_stats ({cols}) SELECT {exprs}, t.cnr_id FROM trips t "
                        f"WHERE {where} AND t.cnr_id IS NOT NULL AND DATE(t.trip_start) IN ({ph}) "
                        f"GROUP BY DATE(t.trip_start), t.cnr_id", dates)
            results["daily_fleet_stats"] = n + cur.rowcount
        conn.commit()

    logger.info("Incremental summaries refreshed (%d drivers, %d vehicles, %d routes, %d dates): %s",
                len(driver_ids), len(vehicle_ids), len(routes), len(dates), results)
    return results


# ============================================
# CLI ENTRY POINT
# ============================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    sys.path.insert(0, str(PROJECT_ROOT))
    from nexgen.shared.legacy_db import get_connection, init_database
    from nexgen.shared.legacy_settings import settings

    print("=" * 60)
    print("Smart-Truck Data Migration CLI")
    print("=" * 60)

    init_database()
    conn = get_connection()

    try:
        # Step 1: Schema
        print("\n[1/4] Creating schema...")
        run_schema(conn)

        # Step 2: Trip CSV (from env-configured path)
        trip_csv = settings.TRIP_CSV_PATH
        print(f"\n[2/4] Migrating trips from: {trip_csv}")
        result = migrate_trip_csv(conn, str(trip_csv))
        print(f"  Result: inserted={result.get('trips_inserted'):,}, "
              f"skipped={result.get('trips_skipped'):,}, "
              f"time={result.get('elapsed_seconds')}s")

        # Step 3: Waypoints (from env-configured path)
        waypoint_dir = settings.WAYPOINT_DIR
        pattern = settings.WAYPOINT_FILE_PATTERN
        print(f"\n[3/4] Migrating waypoints from: {waypoint_dir}")
        for xls in sorted(waypoint_dir.glob(pattern)):
            wp_result = migrate_waypoint_excel(conn, str(xls))
            print(f"  {xls.name}: {wp_result}")

        # Step 4: Summaries
        print("\n[4/4] Refreshing summary tables...")
        summary_result = refresh_all_summaries(conn)
        print(f"  Result: {summary_result}")

        # Final counts
        print("\nFinal database counts:")
        with conn.cursor() as cur:
            for table in ["drivers", "vehicles", "locations", "trips",
                          "waypoints", "driver_summary", "route_summary"]:
                cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                print(f"  {table:25s}: {cur.fetchone()['cnt']:>10,}")

        print("\n" + "=" * 60)
        print("MIGRATION COMPLETE!")
        print("=" * 60)

    finally:
        conn.close()
