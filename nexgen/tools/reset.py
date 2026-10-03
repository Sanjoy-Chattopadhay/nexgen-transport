"""`python -m nexgen reset`: a fresh, empty NexGen database that builds forward.

Drops every schema NexGen owns (database.yaml `schemas`), recreates them from
the migrations, and loads only the reference data the product cannot work
without -- each from its own source file, never from the legacy databases:

    tenants          config/tenants/*.yaml (the platform service mirrors them)
    geofence master  Masters/ (the eTrans site and polygon CSVs), then each
                     fence's inradius and state / district
    toll plazas      data/nh_fee_plazas.json
    plant fences     the published plant coordinates (circle geofences)
    (the first geofence run is created by the detector when trips arrive)

Everything else -- trips, GPS, visits, analytics, ML training data, events,
job history -- is gone, and comes back only from the sync lanes. With
`--start-from DATE` the date is stored in the new database: no lane fetches
anything before it, and a lane's first run pulls from it.

What it never does: touch the legacy databases (`smart_truck`,
`geofencing`), drop a database that database.yaml does not name as NexGen's,
or run while NexGen is running. The imported history can be rebuilt at any
time with `python -m nexgen import-legacy`, since the legacy databases are
untouched.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from nexgen.core.config import ROOT, get_config
from nexgen.core.db import connect, ensure_databases, server_connection
from nexgen.shared.legacy_db import use_schema

logger = logging.getLogger(__name__)

SYSTEM = {"mysql", "sys", "information_schema", "performance_schema"}


class ResetRefused(RuntimeError):
    pass


def parse_start(text: str) -> datetime | None:
    """A start date as typed: ISO (2026-10-04, 2026-10-04 06:00) or the feed's
    own formats (04-10-2026, 04/10/2026 ...)."""
    try:
        return datetime.fromisoformat(str(text).strip())
    except ValueError:
        from nexgen.shared.feed.tta import parse_dt
        return parse_dt(text)


def _check_not_running() -> None:
    from nexgen.supervisor.process import port_owner
    cfg = get_config()
    ports = [("gateway", cfg.gateway_port)] + [(n, cfg.service(n).port) for n in cfg.service_names]
    for name, port in ports:
        held = port_owner(port)
        if held is not None and "nexgen" in held[1]:
            raise ResetRefused(f"NexGen is running ({name} on port {port}, pid {held[0]}). "
                               "Stop it first with stop.bat (or: python -m nexgen shutdown).")


def nexgen_databases() -> list[str]:
    """The databases a reset may drop: NexGen's own, never a legacy or system one."""
    cfg = get_config()
    legacy = set()
    for key in ("smart_truck", "geofencing"):
        try:
            legacy.add(cfg.legacy_database(key))
        except KeyError:
            pass
    names = []
    for key in cfg.schema_keys:
        name = cfg.schema(key)
        if name in legacy or name.lower() in SYSTEM:
            raise ResetRefused(f"database.yaml names {name!r} as NexGen's {key} schema, but it is a "
                               "legacy or system database; refusing to drop it")
        names.append(name)
    return names


def run_reset(start_from: datetime | None = None, masters_dir: Path | None = None) -> dict:
    t0 = time.perf_counter()
    _check_not_running()
    names = nexgen_databases()
    report: dict = {"dropped": names}

    conn = server_connection()
    try:
        with conn.cursor() as cur:
            for name in names:
                logger.info("dropping %s", name)
                cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()

    ensure_databases()
    from nexgen.core.migrate import migrate
    report["migrations"] = sum(1 for r in migrate() if r["action"] == "applied")

    # -- reference data, each from its own source ---------------------------
    from nexgen.services.platform.service import sync_tenants
    report["tenants"] = sync_tenants()

    md = Path(masters_dir) if masters_dir else ROOT / "Masters"
    with use_schema("geofence"):
        from nexgen.shared.geoengine import store
        if md.is_dir():
            from nexgen.shared.geoengine.ingest.masters import import_masters
            master = import_masters(masters_dir=md, truncate=True)
            store.invalidate()
            report["geofence_master"] = {k: master.get(k) for k in ("sites", "fences", "rejects", "status")
                                         if k in master} or master
            report["inradius"] = store.ensure_inradius()
            report["regions"] = store.ensure_regions(recompute=True)
        else:
            report["geofence_master"] = f"skipped: no {md} folder (copy the eTrans master CSVs there)"
        from nexgen.shared.geoengine.ingest.tolls import import_nh_fee_plazas
        report["toll_plazas"] = import_nh_fee_plazas()
        from nexgen.shared.circlefence.store import seed_from_plants
        from nexgen.shared.legacy_db import get_connection
        c = get_connection()
        try:
            report["plant_fences"] = seed_from_plants(c)
        finally:
            c.close()
        # The engine refuses a run over no trips: the geofence detector creates
        # and publishes the first run when the sync lanes bring the first ones.
        report["first_geofence_run"] = "created by the geofence detector when the first trips arrive"

    if start_from is not None:
        from nexgen.services.ingestion.tms.tta_api_sync import START_FROM_KEY
        with connect("ingestion") as c, c.cursor() as cur:
            cur.execute("INSERT INTO app_settings (s_key, s_value) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE s_value = VALUES(s_value)",
                        (START_FROM_KEY, json.dumps({"from": start_from.isoformat(sep=" "),
                                                     "set_at": datetime.now().isoformat(sep=" ")})))
            c.commit()
        report["start_from"] = start_from.isoformat(sep=" ")

    report["seconds"] = round(time.perf_counter() - t0, 1)
    return report
