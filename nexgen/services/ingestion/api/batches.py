"""Batches in the raw layer: what arrived, when, and from where."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from nexgen.core.db import connect
from nexgen.services.ingestion.landing import decode

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@router.get("/status")
def status():
    with connect("ingestion") as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) n, COALESCE(SUM(i_trips),0) trips, COALESCE(SUM(i_fixes),0) fixes, "
                    "MAX(dt_landed) last_landed FROM batch")
        totals = cur.fetchone()
        cur.execute("SELECT s_source, COUNT(*) n, MAX(dt_landed) last_landed FROM batch GROUP BY s_source")
        by_source = cur.fetchall()
    return {"service": "ingestion", "batches": totals, "by_source": by_source}


@router.get("/batches")
def batches(limit: int = Query(50, ge=1, le=500)):
    with connect("ingestion") as conn, conn.cursor() as cur:
        cur.execute("SELECT i_batch_id, s_source, s_trip_class, c_gps_kind, s_trigger, dt_window_from, "
                    "dt_window_to, i_trips, i_fixes, s_status, s_note, dt_landed FROM batch "
                    "ORDER BY i_batch_id DESC LIMIT %s", (limit,))
        return cur.fetchall()


@router.get("/batches/{batch_id}/payload/{part}")
def payload(batch_id: int, part: int):
    """One received record exactly as it arrived (for auditing a figure)."""
    with connect("ingestion") as conn, conn.cursor() as cur:
        cur.execute("SELECT b_body FROM payload WHERE i_batch_id=%s AND i_part=%s", (batch_id, part))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "no such payload (it may be past the 90-day replay window)")
    return decode(row["b_body"])
