"""ingestion: service entry point (filled in step by step; see docs/ARCHITECTURE.md)."""

from __future__ import annotations

from fastapi import APIRouter

from nexgen.core.service import Service

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@router.get("/status")
def status():
    return {"service": "ingestion", "state": "skeleton"}


def build() -> Service:
    svc = Service("ingestion")
    svc.include(router)
    svc.worker("worker")
    return svc
