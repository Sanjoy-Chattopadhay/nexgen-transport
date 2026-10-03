"""Consignor directory + analytics.

Backs the multi-consignor selector, validates route-based scoping, and serves
the Consignors analytics module (overview + per-consignor drill-down).
"""
from fastapi import APIRouter, Depends, HTTPException

from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope
from nexgen.services.analytics.lib import partner_insights as svc

router = APIRouter(prefix="/consignors", tags=["Consignors"])


@router.get("")
def list_consignors(conn=Depends(get_db)):
    """All consignors with trip counts (drives the consignor switcher)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.i_cnr_id  AS id,
                   c.s_cnr_name AS name,
                   COUNT(t.id)  AS trip_count
            FROM consignors c
            LEFT JOIN trips t ON t.cnr_id = c.i_cnr_id
            GROUP BY c.i_cnr_id, c.s_cnr_name
            ORDER BY trip_count DESC, name
            """
        )
        rows = cur.fetchall()
    return {"data": rows, "total": len(rows)}


@router.get("/overview")
def consignors_overview(scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """Per-consignor KPI table for the Consignors analytics page."""
    return svc.list_consignors(conn, scope)


@router.get("/{consignor_id}/detail")
def consignor_detail(consignor_id: int, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """One consignor's full profile: KPIs, top consignees/routes/vehicles/
    drivers, delivery split, monthly trend and recent trips."""
    return svc.consignor_detail(conn, consignor_id, scope)


@router.get("/{consignor_id}")
def get_consignor(consignor_id: int, conn=Depends(get_db)):
    """Single consignor (used to validate a route-supplied consignor id)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i_cnr_id AS id, s_cnr_name AS name FROM consignors WHERE i_cnr_id = %s",
            (consignor_id,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Consignor {consignor_id} not found")
    return row
