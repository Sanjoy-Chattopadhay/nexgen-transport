"""analytics: service entry point (filled in step by step; see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from fastapi import APIRouter

from nexgen.core.service import Service

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/status")
def status():
    return {"service": "analytics", "state": "skeleton"}


def build() -> Service:
    svc = Service("analytics")
    svc.include(router)
    svc.worker("kpi")
    return svc
