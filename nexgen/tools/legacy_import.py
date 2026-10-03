"""`python -m nexgen import-legacy`: bring both legacy databases in.

Each service imports its own domain, in dependency order, through its own
importer (writes only to its own schema; reads the legacy databases, which
are never written). Parts:

    ingestion   lane settings (switched off), watermarks, run history, gaps
    fleet       trips, entities, GPS (one row per physical fix), windows
    verify      every trip's GPS copy rebuilt from gps_fix vs its legacy copy

Later steps of the build add geofence, routing, analytics and ml parts.
Re-running is safe: every step tops up.
"""

from __future__ import annotations

import logging
import time

from nexgen.shared.legacy_db import use_schema

logger = logging.getLogger(__name__)

PARTS = ["ingestion", "fleet", "geofence", "routing", "analytics", "ml", "verify"]


def run_import(parts: list[str] | None = None, limit_trips: int | None = None) -> dict:
    wanted = parts or PARTS
    report: dict = {}
    for part in PARTS:
        if part not in wanted:
            continue
        t0 = time.perf_counter()
        logger.info("importing %s", part)
        if part == "ingestion":
            from nexgen.services.ingestion.legacy_import import import_smart_truck
            report[part] = import_smart_truck()
        elif part == "fleet":
            from nexgen.services.fleet.legacy_import import import_smart_truck
            report[part] = import_smart_truck(limit_trips=limit_trips)
        elif part == "verify":
            from nexgen.services.fleet.legacy_import import verify_trip_copies
            report[part] = verify_trip_copies(sample=limit_trips)
        else:
            module = _optional(f"nexgen.services.{part}.legacy_import")
            if module is None:
                report[part] = {"skipped": "not built yet"}
                continue
            with use_schema(part):
                report[part] = module.import_legacy()
        report[part + "_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def _optional(name: str):
    import importlib
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None
