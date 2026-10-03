"""
Database lifecycle for scale (~2500-3000 TTA trips/day, per-vehicle GPS).

Three jobs, runnable from the API or Windows Task Scheduler:

1. BACKUP  — mysqldump of smart_truck -> backups/smart_truck_<ts>.sql.gz,
             keeps the newest BACKUP_KEEP files (rotation).
2. PURGE   — GPS pings of trips that ENDED > GPS_RETENTION_DAYS ago are
             archived to backups/archive/gps_<ts>.jsonl.gz FIRST, then
             deleted in batches (never blocks the DB). Trip rows stay
             forever (they're small); only the heavy pings rotate out.
3. STATUS  — table sizes, row counts, oldest/newest data, backup list.

Schedule daily (Windows Task Scheduler):
    schtasks /Create /SC DAILY /ST 02:00 /TN SmartTruckMaintenance ^
      /TR "python -m backend.app.services.db_maintenance daily"
"""

import gzip
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from nexgen.shared.legacy_settings import settings

logger = logging.getLogger(__name__)

PURGE_BATCH = 20_000


def _backup_dir() -> Path:
    d = Path(settings.BACKUP_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _archive_dir() -> Path:
    d = _backup_dir() / "archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ============================================
# 1. BACKUP (mysqldump + gzip + rotation)
# ============================================

def run_backup() -> dict:
    dump_exe = Path(settings.MYSQL_BIN_DIR) / "mysqldump.exe"
    if not dump_exe.exists():
        dump_exe = Path("mysqldump")  # hope it's on PATH

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = _backup_dir() / f"smart_truck_{ts}.sql.gz"

    cmd = [str(dump_exe), f"-h{settings.DB_HOST}", f"-P{settings.DB_PORT}",
           f"-u{settings.DB_USER}", f"-p{settings.DB_PASSWORD}",
           "--single-transaction", "--quick", "--routines", settings.DB_NAME]
    logger.info("Backing up %s -> %s", settings.DB_NAME, out_path.name)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with gzip.open(out_path, "wb") as gz:
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            gz.write(chunk)
    _, err = proc.communicate()
    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"mysqldump failed: {err.decode(errors='replace')[:400]}")

    # rotation
    backups = sorted(_backup_dir().glob("smart_truck_*.sql.gz"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for old in backups[settings.BACKUP_KEEP:]:
        old.unlink()
        removed.append(old.name)

    return {
        "status": "ok",
        "backup_file": out_path.name,
        "size_mb": round(out_path.stat().st_size / 1e6, 2),
        "kept": min(len(backups), settings.BACKUP_KEEP),
        "rotated_out": removed,
    }


# ============================================
# 2. PURGE (archive old GPS -> delete)
# ============================================

def run_gps_purge(conn, retention_days: int | None = None, dry_run: bool = False) -> dict:
    days = retention_days or settings.GPS_RETENTION_DAYS

    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.i_trip_no
               FROM tta_trips t
               WHERE COALESCE(t.dt_trip_end, t.dt_trip_ata, t.dt_trip_start)
                     < NOW() - INTERVAL %s DAY
                 AND EXISTS (SELECT 1 FROM tta_trip_gps g WHERE g.i_trip_no = t.i_trip_no)""",
            (days,),
        )
        trip_nos = [r["i_trip_no"] for r in cur.fetchall()]

    if not trip_nos:
        return {"status": "ok", "retention_days": days, "trips_purged": 0,
                "pings_archived": 0, "dry_run": dry_run}
    if dry_run:
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(trip_nos))
            cur.execute(f"SELECT COUNT(*) AS c FROM tta_trip_gps WHERE i_trip_no IN ({ph})", trip_nos)
            n = cur.fetchone()["c"]
        return {"status": "ok", "retention_days": days, "trips_purged": len(trip_nos),
                "pings_archived": n, "dry_run": True}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = _archive_dir() / f"gps_{ts}.jsonl.gz"
    archived = 0

    with gzip.open(archive_path, "wt", encoding="utf-8") as gz:
        for trip_no in trip_nos:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM tta_trip_gps WHERE i_trip_no = %s ORDER BY dt_message", (trip_no,))
                for row in cur.fetchall():
                    gz.write(json.dumps(row, default=str) + "\n")
                    archived += 1
            # batched delete so the table never locks long
            while True:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM tta_trip_gps WHERE i_trip_no = %s LIMIT %s",
                                (trip_no, PURGE_BATCH))
                    deleted = cur.rowcount
                conn.commit()
                if deleted < PURGE_BATCH:
                    break

    logger.info("Purged GPS for %d trips (%d pings) -> %s", len(trip_nos), archived, archive_path.name)
    return {
        "status": "ok", "retention_days": days,
        "trips_purged": len(trip_nos), "pings_archived": archived,
        "archive_file": archive_path.name, "dry_run": False,
    }


# ============================================
# 3. STATUS
# ============================================

def maintenance_status(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT table_name AS tbl, table_rows AS approx_rows,
                      ROUND((data_length + index_length) / 1048576, 1) AS size_mb
               FROM information_schema.tables
               WHERE table_schema = %s
               ORDER BY (data_length + index_length) DESC""",
            (settings.DB_NAME,),
        )
        tables = cur.fetchall()
        cur.execute("SELECT MIN(dt_message) AS oldest, MAX(dt_message) AS newest, COUNT(*) AS pings FROM tta_trip_gps")
        gps = cur.fetchone()
        cur.execute(
            """SELECT COUNT(*) AS purgeable FROM tta_trips t
               WHERE COALESCE(t.dt_trip_end, t.dt_trip_ata, t.dt_trip_start)
                     < NOW() - INTERVAL %s DAY
                 AND EXISTS (SELECT 1 FROM tta_trip_gps g WHERE g.i_trip_no = t.i_trip_no)""",
            (settings.GPS_RETENTION_DAYS,),
        )
        purgeable = cur.fetchone()["purgeable"]

    backups = sorted(_backup_dir().glob("smart_truck_*.sql.gz"),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "db_size_mb": round(sum(t["size_mb"] or 0 for t in tables), 1),
        "tables": tables,
        "gps": {**gps, "oldest": str(gps["oldest"]), "newest": str(gps["newest"])},
        "retention_days": settings.GPS_RETENTION_DAYS,
        "trips_awaiting_purge": purgeable,
        "backups": [{"file": b.name, "size_mb": round(b.stat().st_size / 1e6, 2),
                     "created": datetime.fromtimestamp(b.stat().st_mtime).isoformat()}
                    for b in backups[:10]],
        "backup_keep": settings.BACKUP_KEEP,
        "schedule_hint": "schtasks /Create /SC DAILY /ST 02:00 /TN SmartTruckMaintenance "
                         "/TR \"python -m backend.app.services.db_maintenance daily\"",
    }


# ============================================
# CLI (for Task Scheduler)
# ============================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
    from nexgen.shared.legacy_db import db_session

    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action == "daily":
        print(json.dumps(run_backup(), indent=2))
        with db_session() as conn:
            print(json.dumps(run_gps_purge(conn), indent=2, default=str))
    elif action == "backup":
        print(json.dumps(run_backup(), indent=2))
    elif action == "purge":
        with db_session() as conn:
            print(json.dumps(run_gps_purge(conn), indent=2, default=str))
    else:
        with db_session() as conn:
            print(json.dumps(maintenance_status(conn), indent=2, default=str))
