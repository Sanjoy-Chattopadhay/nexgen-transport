"""geofence: service entry point (filled in step by step; see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from fastapi import APIRouter

from nexgen.core.service import Service

router = APIRouter(prefix="/api/v1/geo", tags=["geofence"])


@router.get("/status")
def status():
    return {"service": "geofence", "state": "skeleton"}


def build() -> Service:
    svc = Service("geofence")
    svc.include(router)
    svc.worker("detector")
    svc.worker("live")
    return svc
