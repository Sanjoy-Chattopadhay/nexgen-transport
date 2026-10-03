"""
Migration API endpoints.
Trigger data migration and summary refresh from the running backend service.

Workflow:
    1. POST /api/v1/migrate/schema          -> Create tables
    2. POST /api/v1/migrate/trips           -> Start trip migration (background)
    3. GET  /api/v1/migrate/progress        -> Poll migration progress (live %)
    4. POST /api/v1/migrate/waypoints       -> Migrate waypoint Excel files
    5. POST /api/v1/migrate/refresh-summaries -> Build summary tables
    6. GET  /api/v1/migrate/status          -> Check final row counts

Data paths are configurable via .env:
    DATA_DIR, TRIP_CSV_FILENAME, WAYPOINT_FILE_PATTERN
"""

from fastapi import APIRouter, Depends, BackgroundTasks
from nexgen.shared.legacy_db import get_db, get_connection
from nexgen.shared.legacy_settings import settings
from nexgen.services.analytics.lib.data_migration import (
    run_schema,
    migrate_trip_csv,
    migrate_waypoint_excel,
    refresh_all_summaries,
    get_progress,
)

router = APIRouter(prefix="/migrate", tags=["Data Migration"])


@router.post("/schema")
def create_schema(conn=Depends(get_db)):
    """Step 1: Create all database tables (idempotent - safe to call multiple times)."""
    return run_schema(conn)


@router.post("/trips")
def migrate_trips(background_tasks: BackgroundTasks):
    """
    Step 2: Migrate trip dispatch CSV into database.
    Runs in BACKGROUND - poll GET /migrate/progress to track.
    CSV path is configured via DATA_DIR + TRIP_CSV_FILENAME in .env.
    """
    progress = get_progress()
    if progress["running"]:
        return {
            "status": "already_running",
            "message": "Migration is already in progress. Check GET /migrate/progress",
            "progress": progress,
        }

    csv_path = settings.TRIP_CSV_PATH
    if not csv_path.exists():
        return {"status": "error", "message": f"CSV not found: {csv_path}"}

    def _run():
        conn = get_connection()
        try:
            migrate_trip_csv(conn, str(csv_path))
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Migration failed: {e}", exc_info=True)
        finally:
            conn.close()

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "message": "Trip migration started in background. Poll GET /api/v1/migrate/progress to track.",
        "file": str(csv_path),
    }


@router.get("/progress")
def migration_progress():
    """
    Poll this endpoint to track live migration progress.
    Returns: phase, percent, inserted, skipped, elapsed_seconds, etc.
    """
    return get_progress()


@router.post("/waypoints")
def migrate_all_waypoints(conn=Depends(get_db)):
    """Step 3: Migrate all waypoint Excel files from configured DATA_DIR."""
    waypoint_dir = settings.WAYPOINT_DIR
    if not waypoint_dir.exists():
        return {"status": "error", "message": f"Directory not found: {waypoint_dir}"}

    pattern = settings.WAYPOINT_FILE_PATTERN
    results = []
    for xls in sorted(waypoint_dir.glob(pattern)):
        r = migrate_waypoint_excel(conn, str(xls))
        results.append(r)
    return {"status": "ok", "files_processed": len(results), "results": results}


@router.post("/refresh-summaries")
def refresh_summaries(conn=Depends(get_db)):
    """Step 4: Refresh all summary tables (driver, route, vehicle, daily, time patterns)."""
    return refresh_all_summaries(conn)


@router.get("/status")
def migration_status(conn=Depends(get_db)):
    """Check current database row counts across all tables."""
    counts = {}
    with conn.cursor() as cur:
        for table in ["drivers", "vehicles", "locations", "customers", "trips",
                      "waypoints", "driver_summary", "route_summary",
                      "vehicle_summary", "daily_fleet_stats", "route_time_patterns",
                      "ml_models", "predictions", "alerts"]:
            try:
                cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                counts[table] = cur.fetchone()["cnt"]
            except Exception:
                counts[table] = -1
    return counts


@router.get("/config")
def migration_config():
    """Show current data file configuration (from .env)."""
    return {
        "data_dir": settings.DATA_DIR,
        "trip_csv_filename": settings.TRIP_CSV_FILENAME,
        "trip_csv_full_path": str(settings.TRIP_CSV_PATH),
        "trip_csv_exists": settings.TRIP_CSV_PATH.exists(),
        "waypoint_dir": str(settings.WAYPOINT_DIR),
        "waypoint_pattern": settings.WAYPOINT_FILE_PATTERN,
        "waypoint_dir_exists": settings.WAYPOINT_DIR.exists(),
    }
