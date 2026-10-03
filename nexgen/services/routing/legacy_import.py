"""Import Geo-Fencing's route plans, trip routes and deviations into nx_route.

Same columns as the legacy tables (created from their own CREATE TABLE), so
each copy is INSERT IGNORE ... SELECT *. The legacy database is only read.
"""

from __future__ import annotations

from nexgen.core.config import get_config
from nexgen.core.db import connect

TABLES = ("geo_route_plan", "geo_trip_route", "geo_route_deviation")


def import_legacy() -> dict:
    geo = f"`{get_config().legacy_database('geofencing')}`"
    out = {}
    with connect("routing") as conn, conn.cursor() as cur:
        for t in TABLES:
            cur.execute(f"INSERT IGNORE INTO `{t}` SELECT * FROM {geo}.`{t}`")
            out[t] = {"rows": cur.rowcount}
        cur.execute("INSERT IGNORE INTO route_state (s_key, s_value) VALUES ('data_version', '1')")
        conn.commit()
    return out
