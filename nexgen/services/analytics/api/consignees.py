"""Consignee (receiver) analytics — trips.customer_id -> customers.cne_name.

Replaces the old ML-forecaster-backed "Clients" module with analytics derived
directly from real trip data. Honours the active consignor scope.
"""
from fastapi import APIRouter, Depends

from nexgen.shared.legacy_db import get_db
from nexgen.shared.common.consignor import ConsignorScope, consignor_scope
from nexgen.services.analytics.lib import partner_insights as svc

router = APIRouter(prefix="/consignees", tags=["Consignees"])


@router.get("")
def list_consignees(search: str = "", scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """All consignees with headline KPIs (trips, distance, OTD%, speed)."""
    return svc.list_consignees(conn, scope, search)


@router.get("/{name:path}")
def consignee_detail(name: str, scope: ConsignorScope = Depends(consignor_scope), conn=Depends(get_db)):
    """One consignee's full profile: KPIs, top routes/vehicles/drivers,
    delivery split, monthly trend and recent trips."""
    return svc.consignee_detail(conn, name, scope)
