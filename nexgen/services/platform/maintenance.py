"""Backups and the data-lifecycle screen (Smart-Truck's /tta/maintenance/*).

Backups are the platform's job because they span every schema. GPS retention
is the fleet service's job, because it owns the fixes; the legacy purge
endpoint asks fleet to do it and reports what fleet did.

    GET  /api/v1/tta/maintenance/status   sizes, GPS window, backups, retention
    POST /api/v1/tta/maintenance/backup   mysqldump every NexGen schema
    POST /api/v1/tta/maintenance/purge    archive + drop GPS past retention
                                          (dry_run=true by default, as before)
"""

from __future__ import annotations

import gzip
import logging
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from nexgen.core.config import get_config
from nexgen.core.db import server_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/tta/maintenance", tags=["maintenance"])


def _backup_dir() -> Path:
    cfg = get_config()
    d = cfg.path(cfg.setting("platform", "backup_dir", "backups"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _schemas() -> list[str]:
    cfg = get_config()
    return [cfg.schema(k) for k in cfg.schema_keys]


def run_backup() -> dict:
    """mysqldump of every NexGen schema into one gzip file, newest N kept."""
    cfg = get_config()
    bin_dir = Path(str(cfg.setting("platform", "mysql_bin_dir", "")))
    exe = bin_dir / ("mysqldump.exe" if (bin_dir / "mysqldump.exe").exists() else "mysqldump")
    if not exe.exists():
        exe = Path("mysqldump")
    srv, creds = cfg.server("primary"), cfg.credentials("default")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _backup_dir() / f"nexgen_{ts}.sql.gz"
    cmd = [str(exe), f"-h{srv.get('host', 'localhost')}", f"-P{srv.get('port', 3306)}",
           f"-u{creds.get('user', 'root')}", f"-p{creds.get('password', '')}",
           "--single-transaction", "--quick", "--routines", "--databases", *_schemas()]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with gzip.open(out_path, "wb") as gz:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            gz.write(chunk)
    _, err = proc.communicate()
    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"mysqldump failed: {err.decode(errors='replace')[:400]}")
    keep = int(cfg.setting("platform", "backup_keep", 14))
    backups = sorted(_backup_dir().glob("nexgen_*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for old in backups[keep:]:
        old.unlink()
        removed.append(old.name)
    return {"status": "ok", "backup_file": out_path.name, "size_mb": round(out_path.stat().st_size / 1e6, 2),
            "kept": min(len(backups), keep), "rotated_out": removed}


def _fleet(method: str, path: str, **kw):
    cfg = get_config()
    url = f"http://{cfg.host}:{cfg.service('fleet').port}{path}"
    try:
        r = httpx.request(method, url, timeout=600.0, **kw)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"the fleet service did not answer: {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.text[:500])
    return r.json()


@router.get("/status")
def status():
    cfg = get_config()
    conn = server_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_schema AS s, table_name AS tbl, table_rows AS approx_rows, "
                        "ROUND((data_length + index_length) / 1048576, 1) AS size_mb "
                        "FROM information_schema.tables WHERE table_schema IN %s AND table_type='BASE TABLE' "
                        "ORDER BY (data_length + index_length) DESC", (tuple(_schemas()),))
            tables = cur.fetchall()
    finally:
        conn.close()
    try:
        gps = _fleet("GET", "/api/v1/fleet/retention/status")
    except HTTPException as exc:
        gps = {"error": exc.detail}
    backups = sorted(_backup_dir().glob("nexgen_*.sql.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "db_size_mb": round(sum(float(t["size_mb"] or 0) for t in tables), 1),
        "tables": [{"tbl": f"{t['s']}.{t['tbl']}", "approx_rows": t["approx_rows"], "size_mb": t["size_mb"]}
                   for t in tables],
        "gps": gps.get("gps", gps),
        "retention_days": gps.get("retention_days"),
        "trips_awaiting_purge": gps.get("months_awaiting_purge"),
        "backups": [{"file": b.name, "size_mb": round(b.stat().st_size / 1e6, 2),
                     "created": datetime.fromtimestamp(b.stat().st_mtime).isoformat()} for b in backups[:10]],
        "backup_keep": int(cfg.setting("platform", "backup_keep", 14)),
        "schedule_hint": "Backups run daily at 01:00 inside the platform service (developer page → jobs).",
    }


@router.post("/backup")
def backup():
    try:
        return run_backup()
    except Exception as exc:
        raise HTTPException(500, f"Backup failed: {exc}") from exc


@router.post("/purge")
def purge(dry_run: bool = True, retention_days: int | None = None):
    params = {"dry_run": str(dry_run).lower()}
    if retention_days:
        params["retention_days"] = retention_days
    return _fleet("POST", "/api/v1/fleet/retention/run", params=params)
