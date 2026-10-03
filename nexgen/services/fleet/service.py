"""fleet: service entry point (filled in step by step; see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from fastapi import APIRouter

from nexgen.core.service import Service

router = APIRouter(prefix="/api/v1/fleet", tags=["fleet"])


@router.get("/status")
def status():
    return {"service": "fleet", "state": "skeleton"}


def build() -> Service:
    svc = Service("fleet")
    svc.include(router)
    svc.worker("processor")
    return svc
