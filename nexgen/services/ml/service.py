"""ml: service entry point (filled in step by step; see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from fastapi import APIRouter

from nexgen.core.service import Service

router = APIRouter(prefix="/api/v1/ml", tags=["ml"])


@router.get("/status")
def status():
    return {"service": "ml", "state": "skeleton"}


def build() -> Service:
    svc = Service("ml")
    svc.include(router)
    svc.worker("trainer")
    return svc
