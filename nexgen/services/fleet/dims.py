"""Entity and dictionary lookups for the fleet processor.

Each entity table is keyed on the exact values the source sent (see
migrations/fleet/0001_core.sql), so resolving a value to its id is a lookup
on that key, inserting the row the first time it is seen. Lookups are cached
in the process: there are a few thousand distinct values against millions of
fixes, so after the first batch nearly every lookup is a dict hit and no
AUTO_INCREMENT values are burned on duplicate inserts.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_cache: dict[tuple, int] = {}


def _get(key: tuple) -> int | None:
    with _lock:
        return _cache.get(key)


def _put(key: tuple, value: int) -> int:
    with _lock:
        _cache[key] = value
    return value


def clear() -> None:
    with _lock:
        _cache.clear()


def _lookup_insert(cur, cache_key: tuple, select_sql: str, params: tuple, insert_sql: str) -> int:
    hit = _get(cache_key)
    if hit is not None:
        return hit
    cur.execute(select_sql, params)
    row = cur.fetchone()
    if row:
        return _put(cache_key, int(next(iter(row.values()))))
    cur.execute(insert_sql, params)
    if cur.lastrowid:
        return _put(cache_key, int(cur.lastrowid))
    cur.execute(select_sql, params)  # lost a race with another writer
    return _put(cache_key, int(next(iter(cur.fetchone().values()))))


def vehicle(cur, tenant: int, asset_no: str | None) -> int | None:
    if not asset_no:
        return None
    return _lookup_insert(cur, ("veh", tenant, asset_no),
                          "SELECT i_vehicle_id FROM vehicle WHERE i_tenant_id=%s AND s_asset_no=%s",
                          (tenant, asset_no),
                          "INSERT IGNORE INTO vehicle (i_tenant_id, s_asset_no) VALUES (%s,%s)")


def device(cur, tenant: int, device_no: str | None) -> int | None:
    if not device_no:
        return None
    return _lookup_insert(cur, ("dev", tenant, device_no),
                          "SELECT i_device_id FROM device WHERE i_tenant_id=%s AND s_device_no=%s",
                          (tenant, device_no),
                          "INSERT IGNORE INTO device (i_tenant_id, s_device_no) VALUES (%s,%s)")


def transporter(cur, tenant: int, code, name) -> int | None:
    if code in (None, "") and name in (None, ""):
        return None
    c, n = str(code or ""), str(name or "")
    return _lookup_insert(cur, ("trn", tenant, c, n),
                          "SELECT i_transporter_id FROM transporter WHERE i_tenant_id=%s AND s_code=%s AND s_name=%s",
                          (tenant, c, n),
                          "INSERT IGNORE INTO transporter (i_tenant_id, s_code, s_name) VALUES (%s,%s,%s)")


def consignee(cur, tenant: int, name) -> int | None:
    if name in (None, ""):
        return None
    return _lookup_insert(cur, ("cne", tenant, name),
                          "SELECT i_consignee_id FROM consignee WHERE i_tenant_id=%s AND s_name=%s",
                          (tenant, name),
                          "INSERT IGNORE INTO consignee (i_tenant_id, s_name) VALUES (%s,%s)")


def location(cur, tenant: int, node_no, name) -> int | None:
    if node_no in (None, 0, "") and name in (None, ""):
        return None
    nn, nm = int(node_no or 0), str(name or "")
    return _lookup_insert(cur, ("loc", tenant, nn, nm),
                          "SELECT i_location_id FROM location WHERE i_tenant_id=%s AND i_node_no=%s AND s_name=%s",
                          (tenant, nn, nm),
                          "INSERT IGNORE INTO location (i_tenant_id, i_node_no, s_name) VALUES (%s,%s,%s)")


def driver(cur, tenant: int, name, mobile) -> int | None:
    if name in (None, "") and mobile in (None, ""):
        return None
    n, m = str(name or ""), str(mobile or "")
    return _lookup_insert(cur, ("drv", tenant, n, m),
                          "SELECT i_driver_id FROM driver WHERE i_tenant_id=%s AND s_name=%s AND s_mobile=%s",
                          (tenant, n, m),
                          "INSERT IGNORE INTO driver (i_tenant_id, s_name, s_mobile) VALUES (%s,%s,%s)")


def waypoint(cur, name, state) -> int | None:
    if name in (None, ""):
        return None
    s = str(state or "")
    return _lookup_insert(cur, ("wpt", name, s),
                          "SELECT i_waypoint_id FROM ref_waypoint WHERE s_name=%s AND s_state_abbr=%s",
                          (name, s),
                          "INSERT IGNORE INTO ref_waypoint (s_name, s_state_abbr) VALUES (%s,%s)")


def status(cur, text, moving, speed) -> int | None:
    if text in (None, ""):
        return None
    hit = _get(("sts", text))
    if hit is not None:
        return hit
    cur.execute("SELECT i_status_id FROM ref_gps_status WHERE s_status=%s", (text,))
    row = cur.fetchone()
    if row:
        return _put(("sts", text), int(row["i_status_id"]))
    cur.execute("INSERT IGNORE INTO ref_gps_status (s_status, b_moving, i_speed_kmph) VALUES (%s,%s,%s)",
                (text, int(bool(moving)), speed))
    cur.execute("SELECT i_status_id FROM ref_gps_status WHERE s_status=%s", (text,))
    return _put(("sts", text), int(cur.fetchone()["i_status_id"]))


def entity(cur, tenant: int, entity_id, name) -> None:
    if entity_id is None:
        return
    if _get(("ent", tenant, entity_id)) is not None:
        return
    cur.execute("INSERT INTO gps_entity (i_tenant_id, i_entity_id, s_entity_name) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE s_entity_name=COALESCE(VALUES(s_entity_name), s_entity_name)",
                (tenant, entity_id, name))
    _put(("ent", tenant, entity_id), 1)


def consignor(cur, tenant: int, cnr_id: int, name) -> None:
    """Upsert the consignor with the latest name seen (Smart-Truck's rule)."""
    cur.execute("INSERT INTO consignor (i_tenant_id, i_cnr_id, s_cnr_name) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE s_cnr_name=VALUES(s_cnr_name)",
                (tenant, cnr_id, name or f"CNR-{cnr_id}"))


def consignor_by_name(cur, tenant: int, name) -> int | None:
    """The local feed sends only the consignor's name: resolve it against the
    consignors already known from the id-bearing feed (lookup only, never
    invented -- a made-up id would corrupt the dimension)."""
    if not name:
        return None
    key = ("cnrn", tenant, name.strip().lower())
    hit = _get(key)
    if hit is not None:
        return hit
    cur.execute("SELECT i_cnr_id FROM consignor WHERE i_tenant_id=%s AND LOWER(TRIM(s_cnr_name))=%s LIMIT 1",
                (tenant, name.strip().lower()))
    row = cur.fetchone()
    # Only hits are cached: a name not known yet may arrive with its id in the
    # next zonal batch.
    return _put(key, int(row["i_cnr_id"])) if row else None
