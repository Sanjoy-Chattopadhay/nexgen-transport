"""Fleet data: the fleet's master data, trips and GPS, normalised.

Owns nx_fleet. Its `processor` role is the data-preprocessing stage: it turns
each batch ingestion lands into clean trips, entities and one row per GPS fix
(processor.py), and announces what changed. Stop the processor and new
batches wait, untouched, in the raw layer until it starts again.

Every other service reads fleet data through the v1_* views this service
publishes (migrations/fleet/R__contracts.sql), including the legacy-shaped
ones that let Smart-Truck's and Geo-Fencing's analysis code run unchanged.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from nexgen.core import partitions
from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.service import Service
from nexgen.core.tenancy import current_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])


def _archive_dir():
    cfg = get_config()
    return cfg.path(cfg.setting("fleet", "gps.archive_dir", "archive"))


def _retention_days() -> int:
    return int(get_config().setting("fleet", "gps.retention_days", 180) or 180)


@router.get("/status")
def status():
    tid = current_tenant_id()
    with connect("fleet") as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n, MIN(dt_trip_start) a, MAX(dt_trip_start) b FROM trip WHERE i_tenant_id=%s",
                    (tid,))
        trips = cur.fetchone()
        cur.execute("SELECT COUNT(*) n, SUM(i_fixes) fixes, MIN(dt_first_fix) a, MAX(dt_last_fix) b "
                    "FROM vehicle WHERE i_tenant_id=%s", (tid,))
        vehicles = cur.fetchone()
        cur.execute("SELECT COUNT(*) n, MAX(i_batch_id) last_batch, MAX(dt_processed) last_at "
                    "FROM processed_batch WHERE i_tenant_id=%s", (tid,))
        batches = cur.fetchone()
    return {"service": "fleet", "trips": trips, "vehicles": vehicles, "batches": batches}


@router.get("/trips")
def trips(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
          vehicle: str | None = None, transporter: str | None = None):
    tid = current_tenant_id()
    where, params = ["i_tenant_id=%s"], [tid]
    if vehicle:
        where.append("s_asset_no=%s")
        params.append(vehicle)
    if transporter:
        where.append("s_transporter=%s")
        params.append(transporter)
    with connect("fleet") as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM v1_trip WHERE {' AND '.join(where)} ORDER BY dt_trip_start DESC "
                    f"LIMIT %s OFFSET %s", params + [limit, offset])
        return cur.fetchall()


@router.get("/trips/{trip_no}")
def trip(trip_no: int):
    with connect("fleet") as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM v1_trip WHERE i_tenant_id=%s AND i_trip_no=%s", (current_tenant_id(), trip_no))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"trip {trip_no} not found")
    return row


@router.get("/trips/{trip_no}/fixes")
def trip_fixes(trip_no: int, limit: int = Query(20000, ge=1, le=50000)):
    with connect("fleet") as conn, conn.cursor() as cur:
        cur.execute("SELECT dt_message, d_lat, d_long, i_speed, s_status, is_moving, i_dist, i_cdist, c_source, i_seq "
                    "FROM v1_tta_trip_gps_cdist WHERE i_tenant_id=%s AND i_trip_no=%s ORDER BY dt_message, i_seq LIMIT %s",
                    (current_tenant_id(), trip_no, limit))
        return cur.fetchall()


@router.get("/vehicles")
def vehicles(limit: int = Query(500, ge=1, le=5000)):
    with connect("fleet") as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM v1_vehicle WHERE i_tenant_id=%s ORDER BY dt_last_fix DESC LIMIT %s",
                    (current_tenant_id(), limit))
        return cur.fetchall()


@router.get("/batches")
def batches(limit: int = Query(50, ge=1, le=500)):
    with connect("fleet") as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM processed_batch ORDER BY i_batch_id DESC LIMIT %s", (limit,))
        return cur.fetchall()


@router.post("/batches/{batch_id}/reprocess")
def reprocess(batch_id: int):
    """Normalise a landed batch again (safe: every write is an upsert/dedupe)."""
    from nexgen.services.fleet.processor import process_batch
    return process_batch(batch_id)


@router.get("/retention/status")
def retention_status():
    with connect("fleet") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(dt_fix) oldest, MAX(dt_fix) newest FROM gps_fix")
            span = cur.fetchone()
            cur.execute("SELECT SUM(table_rows) n FROM information_schema.partitions "
                        "WHERE table_schema=DATABASE() AND table_name='gps_fix'")
            approx = cur.fetchone()
        due = partitions.retire(conn, "gps_fix", _retention_days(), "dt_fix", None, "fleet", dry_run=True)
    return {"gps": {"oldest": str(span["oldest"]), "newest": str(span["newest"]),
                    "pings": int(approx["n"] or 0)},
            "retention_days": _retention_days(), "months_awaiting_purge": len(due["retired"]),
            "rows_awaiting_purge": due["rows"]}


@router.post("/retention/run")
def retention_run(dry_run: bool = True, retention_days: int | None = None):
    """Archive and drop GPS months past retention (dry_run by default, as before)."""
    with connect("fleet") as conn:
        return partitions.retire(conn, "gps_fix", int(retention_days or _retention_days()), "dt_fix",
                                 _archive_dir(), "fleet", dry_run=dry_run)


def _partition_upkeep() -> dict:
    with connect("fleet") as conn:
        return {"gps_fix": partitions.ensure_future(conn, "gps_fix", 3, "fleet")}


def _retention() -> dict:
    with connect("fleet") as conn:
        return partitions.retire(conn, "gps_fix", _retention_days(), "dt_fix", _archive_dir(), "fleet")


def build() -> Service:
    from nexgen.services.fleet.processor import handle

    svc = Service("fleet")
    svc.include(router)
    proc = svc.worker("processor")
    proc.consumer("processor", ["ingest.batch.landed"], handle, batch=20)
    proc.jobs.add("partition-upkeep", _partition_upkeep, cron="30 0 * * *",
                  description="keep the next three months' GPS partitions ready")
    proc.jobs.add("gps-retention", _retention, cron="45 1 * * *",
                  description="archive and drop GPS months past retention")
    return svc
