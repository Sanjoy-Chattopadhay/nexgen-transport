"""TTA export files: upload, ingest from a server path, progress.

The same four endpoints as Smart-Truck's /api/v1/tta/{schema,upload,
ingest-path,progress}, so the Data -> Uploads screen and any script calling
them keep working. What changed underneath: the file is parsed with the same
parser and landed as one batch in the raw layer; the fleet processor
normalises it a moment later (watch it on the developer page). The answer
reports what was landed and, once processed, what was new.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from nexgen.core.config import get_config
from nexgen.core.db import connect
from nexgen.core.tenancy import current_tenant_id
from nexgen.services.ingestion.landing import land_blocks
from nexgen.shared.feed.tta import parse_tta_text
from nexgen.shared.legacy_settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tta", tags=["TTA files"])

_progress: dict = {"running": False, "phase": "idle", "percent": 0, "error": None}


def _land_file(path: Path, original_name: str) -> dict:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = parse_tta_text(raw)
    tid = current_tenant_id()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with connect("ingestion") as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO upload (i_tenant_id, s_kind, s_filename, s_stored_path, i_bytes, s_sha256, "
                        "s_status) VALUES (%s,'tta_export',%s,%s,%s,%s,'landing')",
                        (tid, original_name, str(path), path.stat().st_size, digest))
            upload_id = cur.lastrowid
        conn.commit()
        try:
            result = land_blocks(conn, blocks, source="upload.tta", trip_class="zonal",
                                 trigger="upload", note=original_name[:500], tenant_id=tid)
        except Exception as exc:
            with conn.cursor() as cur:
                cur.execute("UPDATE upload SET s_status='failed', s_error=%s WHERE i_upload_id=%s",
                            (str(exc)[:2000], upload_id))
            conn.commit()
            raise
        with conn.cursor() as cur:
            cur.execute("UPDATE upload SET s_status='landed', i_batch_id=%s WHERE i_upload_id=%s",
                        (result["batch_id"], upload_id))
        conn.commit()
    result.update({"upload_id": upload_id, "file": str(path), "blocks_found": len(blocks),
                   "processing": "queued for the fleet processor"})
    return result


@router.post("/schema")
def create_tta_schema():
    """Kept for compatibility: NexGen creates every table through its migrations."""
    return {"status": "ok", "statements_executed": 0,
            "note": "Tables are created by NexGen's migrations (python -m nexgen migrate)."}


@router.post("/upload")
async def upload_tta_file(file: UploadFile = File(...)):
    """Upload a TTA export (sectioned text or JSON) and land it for processing."""
    if not file.filename:
        raise HTTPException(400, "No file provided")
    upload_dir = Path(settings.UPLOAD_DIR) / "tta"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{os.path.basename(file.filename)}"
    dest.write_bytes(await file.read())
    _progress.update(running=True, phase="landing", percent=0, error=None)
    try:
        result = _land_file(dest, file.filename)
        _progress.update(running=False, phase="done", percent=100,
                         trips=result["trips_upserted"], gps_points=result["gps_inserted"])
        return result
    except Exception as exc:
        _progress.update(running=False, phase="error", error=str(exc))
        raise HTTPException(422, f"Ingestion failed: {exc}") from exc


class IngestPathBody(BaseModel):
    path: str


@router.post("/ingest-path")
def ingest_from_path(body: IngestPathBody):
    """Land a TTA export already present on the server (dev helper)."""
    p = Path(body.path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {p}")
    try:
        return _land_file(p, p.name)
    except Exception as exc:
        raise HTTPException(422, f"Ingestion failed: {exc}") from exc


@router.get("/progress")
def tta_progress():
    """Landing progress, plus how far the fleet processor has got with the
    latest uploaded batch."""
    out = dict(_progress)
    try:
        with connect("ingestion") as conn, conn.cursor() as cur:
            cur.execute("SELECT i_batch_id FROM upload WHERE i_batch_id IS NOT NULL "
                        "ORDER BY i_upload_id DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                fleet = get_config().schema("fleet")
                cur.execute(f"SELECT s_status, i_trips, i_fixes_new, i_fixes_dup FROM `{fleet}`.v1_processed_batch "
                            "WHERE i_batch_id=%s", (row["i_batch_id"],))
                done = cur.fetchone()
                out["latest_batch"] = row["i_batch_id"]
                out["processed"] = done or {"s_status": "waiting"}
    except Exception as exc:  # the fleet schema may not be migrated yet
        out["processed_error"] = str(exc)
    return out
